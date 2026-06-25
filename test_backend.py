#!/usr/bin/env python3
"""Testes unitários do backend do OpusSB (puros, sem rede e sem tocar dados reais).

Rode com:  ./venv/bin/python test_backend.py

Usa um DB_PATH temporário (definido ANTES de importar app/db), então o import do app
cria o schema num banco descartável, nunca no opussb.db de produção (regra 10). Cobre:
  - a montagem do comando de queima (regressão do bug de áudio sumido);
  - validação de segmentos de legenda;
  - parsing tolerante do clips_json;
  - extração de legenda do SRT + render do .ass (round-trip com edição).
"""
import os
import sys
import tempfile

# DB descartável ANTES de importar qualquer módulo que toque o banco.
_TMP_DB = tempfile.NamedTemporaryFile(prefix="opussb_test_", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DB_PATH"] = _TMP_DB.name

import app          # noqa: E402
import captions     # noqa: E402
import processor    # noqa: E402

_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        _failures.append(msg)


# ─── 1. Comando de queima: o áudio NUNCA pode sumir (regressão do bug) ─────────
def test_burn_cmd():
    print("[burn cmd]")

    # Só legenda (caso do bug): tem que mapear o áudio original (0:a?) e copiá-lo.
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", None, None, "out.mp4")
    check("0:a?" in cmd, "legenda-só mapeia o áudio original (0:a?)")
    i = cmd.index("-c:a")
    check(cmd[i + 1] == "copy", "legenda-só copia o áudio (sem recodificar)")
    check("[v]" in cmd, "legenda-só mapeia o vídeo filtrado [v]")
    check("+faststart" in cmd, "legenda-só usa +faststart")
    check("-threads" in cmd, "queima limita threads do encoder (-threads; não monopoliza a CPU)")

    # Só música: mixa fala+música e recodifica em aac.
    cmd = app._build_burn_cmd("base.mp4", None, "m.mp3", 0.2, "out.mp4")
    check("[a]" in cmd, "música-só mapeia o áudio mixado [a]")
    i = cmd.index("-c:a")
    check(cmd[i + 1] == "aac", "música-só recodifica em aac")
    check("-shortest" in cmd, "música-só usa -shortest")

    # Legenda + música: vídeo filtrado e áudio mixado.
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", "m.mp3", 0.2, "out.mp4")
    check("[v]" in cmd and "[a]" in cmd, "legenda+música mapeia [v] e [a]")

    # Sem legenda e sem música: vídeo direto, áudio original preservado.
    cmd = app._build_burn_cmd("base.mp4", None, None, None, "out.mp4")
    check("0:v" in cmd and "0:a?" in cmd, "sem nada mapeia 0:v e preserva 0:a?")

    # ── Com trim (handles): output seek -ss/-t DEPOIS do -i, sem regredir o áudio ──
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", None, None, "out.mp4", 10.0, 25.0)
    check("0:a?" in cmd and cmd[cmd.index("-c:a") + 1] == "copy",
          "trim+legenda preserva e copia o áudio original (0:a?)")
    check(cmd.index("-ss") > cmd.index("base.mp4"), "trim: -ss DEPOIS do -i (output seek)")
    check(cmd[cmd.index("-ss") + 1] == "10.000", "trim: -ss = in_point")
    check(cmd[cmd.index("-t") + 1] == "15.000", "trim: -t = out - in")
    check("make_zero" in cmd, "trim usa -avoid_negative_ts make_zero")

    # Só música + trim: um único -t (a música em loop é limitada pelo amix duration=first).
    cmd = app._build_burn_cmd("base.mp4", None, "m.mp3", 0.2, "out.mp4", 10.0, 25.0)
    check(cmd.count("-t") == 1, "trim+música: um único -t (a música não recebe -t)")
    check("duration=first" in " ".join(cmd), "música cobre só o corte (amix duration=first)")
    check("-shortest" in cmd, "trim+música mantém -shortest")

    # Sem nada + trim (export sem legenda): corte cru, áudio preservado, sem -shortest.
    cmd = app._build_burn_cmd("base.mp4", None, None, None, "out.mp4", 10.0, 25.0)
    check("0:v" in cmd and "0:a?" in cmd, "trim sem nada: 0:v + 0:a? (export sem legenda)")
    check("-ss" in cmd and "-shortest" not in cmd, "trim sem música: tem -ss e não tem -shortest")

    # Trim inválido (out <= in) ou ausente NÃO corta: cai no comportamento antigo.
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", None, None, "out.mp4", 25.0, 10.0)
    check("-ss" not in cmd and "-t" not in cmd, "trim inválido (out<=in) não corta")
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", None, None, "out.mp4")
    check("-ss" not in cmd and "-t" not in cmd, "sem in/out (default) não corta")


# ─── 2. Validação de segmentos de legenda ─────────────────────────────────────
def test_validate_segments():
    print("[validate segments]")
    ok = app._validate_segments([{"start": 0, "end": 1.5, "text": " oi "}])
    check(ok == [{"start": 0.0, "end": 1.5, "text": "oi"}], "segmento válido normaliza e faz trim")

    for bad, why in [
        ("nao-lista", "não-lista"),
        ([{"start": 1, "end": 0.5, "text": "x"}], "end <= start"),
        ([{"start": float("nan"), "end": 1, "text": "x"}], "start NaN"),
        ([{"start": 1.0001, "end": 1.0002, "text": "x"}], "intervalo some no arredondamento"),
        ([{"start": 0, "end": 1}], "text ausente é ok"),  # text default "" -> válido
    ]:
        try:
            app._validate_segments(bad)
            raised = False
        except ValueError:
            raised = True
        if why == "text ausente é ok":
            check(not raised, "segmento sem text não quebra (vira string vazia)")
        else:
            check(raised, f"rejeita: {why}")


# ─── 3. Parsing tolerante do clips_json ───────────────────────────────────────
def test_parse_clips_json():
    print("[parse clips_json]")
    check(processor._parse_clips_json(None) == [], "None -> []")
    check(processor._parse_clips_json([{"start": 1}]) == [{"start": 1}], "lista passa direto")
    check(processor._parse_clips_json('[{"start":1,"end":2}]') == [{"start": 1, "end": 2}], "json puro")
    fenced = '```json\n[{"start":1,"end":2}]\n```'
    check(processor._parse_clips_json(fenced) == [{"start": 1, "end": 2}], "json com cerca ```json")
    check(processor._parse_clips_json("lixo {[") == [], "lixo -> []")
    check(processor._parse_clips_json('{"nao":"lista"}') == [], "objeto (não-lista) -> []")


# ─── 4. SRT -> segmentos editáveis -> .ass (round-trip com edição) ─────────────
def test_captions_roundtrip():
    print("[captions srt -> ass]")
    srt = ("1\n00:00:05,740 --> 00:00:09,680\nQual o maior erro em marketing hoje?\n\n"
           "2\n00:00:10,020 --> 00:00:15,760\nDepositar confiança numa estratégia só.\n")
    segs = captions.segments_for_clip(srt, 5.74, 16.0)
    check(len(segs) > 0, "extrai segmentos do clipe")
    check(all(s["start"] >= 0 for s in segs), "tempos relativos ao início do clipe (>= 0)")

    ass = captions.build_ass_from_segments(segs)
    check(ass and "Inter Medium" in ass and "Dialogue:" in ass, "render do .ass com fonte SB")

    segs[0]["text"] = "Texto Corrigido"
    ass2 = captions.build_ass_from_segments(segs)
    check("Texto Corrigido" in ass2, "edição do texto reflete no .ass")

    # Defesa: segmento com tempo inválido é pulado no render.
    bad = [{"start": 2.0, "end": 1.0, "text": "invertido"}, {"start": 0.0, "end": 1.0, "text": "bom"}]
    ass3 = captions.build_ass_from_segments(bad)
    check(ass3 is not None and "invertido" not in ass3 and "bom" in ass3, "render pula segmento inválido")
    check(captions.build_ass_from_segments([]) is None, "lista vazia -> None")


# ─── 4b. Legenda ancorada na PALAVRA (word_timestamps) anti-"adiantada" ───────
def test_words_for_clip():
    print("[captions: legenda ancorada na palavra (anti-adiantada)]")
    # Uma frase SRT longa [0,8] com uma PAUSA real no meio (depois 2.0 -> a 5.0).
    srt = "1\n00:00:00,000 --> 00:00:08,000\nPrimeira parte e depois a segunda parte da frase\n"
    words = [
        {"start": 0.0, "end": 0.8, "word": "Primeira"},
        {"start": 0.8, "end": 1.2, "word": "parte"},
        {"start": 1.2, "end": 1.4, "word": "e"},
        {"start": 1.4, "end": 2.0, "word": "depois"},
        {"start": 5.0, "end": 5.2, "word": "a"},        # 3s de pausa antes
        {"start": 5.2, "end": 5.8, "word": "segunda"},
        {"start": 5.8, "end": 6.2, "word": "parte"},
        {"start": 6.2, "end": 6.4, "word": "da"},
        {"start": 6.4, "end": 8.0, "word": "frase"},
    ]
    new = captions.words_for_clip(words, srt, 0.0, 8.0)
    old = captions.segments_for_clip(srt, 0.0, 8.0)
    check(len(new) > 0, "gera eventos a partir das palavras")
    check(abs(new[0]["start"] - 0.0) < 0.001, "1o evento comeca na 1a palavra real")
    check(all(len(e["text"]) <= captions.MAX_LINE_CHARS for e in new), "linhas respeitam MAX_LINE_CHARS")
    covers = lambda evs, t: any(e["start"] <= t < e["end"] for e in evs)
    check(not covers(new, 3.5), "NOVO: sem legenda durante a pausa (t=3.5s)")
    check(covers(old, 3.5), "ANTIGO: mostrava texto durante a pausa (o bug 'adiantada')")
    check(any(abs(e["start"] - 5.0) < 0.2 for e in new), "NOVO: legenda volta junto com a fala (~5s)")

    # Quebra por fronteira de frase + pausa entre frases (tempos relativos a janela).
    srt2 = ("1\n00:00:10,000 --> 00:00:12,000\nBom dia pessoal\n\n"
            "2\n00:00:14,000 --> 00:00:16,000\nTudo certo\n")
    words2 = [
        {"start": 10.0, "end": 10.4, "word": "Bom"},
        {"start": 10.4, "end": 10.8, "word": "dia"},
        {"start": 10.8, "end": 12.0, "word": "pessoal"},
        {"start": 14.0, "end": 14.5, "word": "Tudo"},
        {"start": 14.5, "end": 16.0, "word": "certo"},
    ]
    ev = captions.words_for_clip(words2, srt2, 10.0, 16.0)
    check(len(ev) == 2, "duas frases viram dois eventos")
    check(ev[0]["text"] == "Bom dia pessoal" and abs(ev[0]["start"]) < 0.001, "frase 1 ancorada, relativa a janela")
    check(ev[1]["text"] == "Tudo certo" and abs(ev[1]["start"] - 4.0) < 0.001, "frase 2 comeca em 4.0 (14.0 - 10.0)")

    # clip_words_window: palavras CRUAS relativas a janela, filtradas (pro editor word-level).
    cw = captions.clip_words_window(words2, 10.0, 16.0)
    check(len(cw) == 5, "clip_words_window traz todas as palavras da janela")
    check(cw[0]["word"] == "Bom" and abs(cw[0]["start"]) < 0.001, "palavra crua relativa a janela")
    check(all(("word" in w and "start" in w and "end" in w) for w in cw), "shape {start,end,word}")
    cw2 = captions.clip_words_window(words2, 14.0, 16.0)
    check(all(w["word"] in ("Tudo", "certo") for w in cw2), "filtra palavras fora da janela")

    # Modo dual: sem palavras -> [] (chamador cai no segments_for_clip, sem regressao).
    check(captions.words_for_clip([], srt, 0.0, 8.0) == [], "sem palavras -> [] (modo dual)")
    check(captions.words_for_clip(None, srt, 0.0, 8.0) == [], "words None -> [] (modo dual)")


# ─── 4a. Estilo customizado da fonte (size/tracking/color/shadow) ─────────────
def test_caption_style():
    print("[captions: estilo customizado da fonte]")
    segs = [{"start": 0.0, "end": 2.0, "text": "Olá mundo"}]
    # Padrão SB (sem style): Fontsize 70, branco, \fsp -3, glow alpha 0x24.
    base = captions.build_ass_from_segments(segs)
    check("Style: SB,Inter Medium,70," in base, "padrão usa Fontsize 70")
    check("&H00FFFFFF" in base, "padrão usa branco")
    check("\\fsp-3" in base, "padrão usa tracking -3")
    check("&H24000000" in base, "padrão usa glow alpha 0x24")

    # Override: size 90, tracking 5, vermelho (#FF0000 -> &H000000FF), sombra 0 (alpha 0xFF).
    style = {"size": 90, "tracking": 5, "color": "#FF0000", "shadow": 0.0}
    ass = captions.build_ass_from_segments(segs, None, style)
    check("Style: SB,Inter Medium,90," in ass, "size override -> Fontsize 90")
    check("&H000000FF" in ass, "cor #FF0000 -> &H000000FF (BGR)")
    check("\\fsp5" in ass, "tracking override -> \\fsp5")
    check("&HFF000000" in ass, "sombra 0 -> glow alpha 0xFF (transparente)")

    # Sombra cheia (1.0) -> alpha 0x00 (opaco).
    ass2 = captions.build_ass_from_segments(segs, None, {"shadow": 1.0})
    check("&H00000000" in ass2, "sombra 1.0 -> glow alpha 0x00 (opaco)")

    # _hex_to_ass: válido (BGR) e inválido.
    check(captions._hex_to_ass("#00FF00") == "&H0000FF00", "hex verde -> &H0000FF00")
    check(captions._hex_to_ass("xyz") is None and captions._hex_to_ass("#12345") is None,
          "hex inválido -> None")

    # Override fora de faixa é clampado, não quebra.
    huge = captions.build_ass_from_segments(segs, None, {"size": 9999, "shadow": 5})
    check("Style: SB,Inter Medium,160," in huge, "size absurdo clampa em 160")
    check("&H00000000" in huge, "shadow > 1 clampa em opaco")
    # caption_y + style juntos continuam compondo (não se anulam).
    both = captions.build_ass_from_segments(segs, 0.5, {"size": 80})
    check("\\pos(" in both and "Inter Medium,80," in both, "caption_y e style coexistem")


# ─── 4c. YouTube: validação de URL + tradução de erro do yt-dlp ───────────────
def test_youtube_helpers():
    print("[youtube: url + erro]")
    ok = ["https://www.youtube.com/watch?v=abc123", "https://youtu.be/abc123",
          "https://youtube.com/live/xyz", "https://m.youtube.com/watch?v=q",
          "http://music.youtube.com/watch?v=q"]
    bad = ["https://vimeo.com/123", "http://evil.com/x", "not a url", "",
           "https://youtube.com.evil.com/x", "ftp://youtube.com/x"]
    check(all(app._is_youtube_url(u) for u in ok), "aceita URLs do YouTube (incl. subdomínios)")
    check(not any(app._is_youtube_url(u) for u in bad), "rejeita não-YouTube e host forjado")

    import processor
    check("anti-robô" in processor._yt_error_hint("ERROR: Sign in to confirm you're not a bot").lower()
          or "anti" in processor._yt_error_hint("Sign in to confirm you're not a bot"),
          "erro de bot vira dica de cookies")
    check("indisponível" in processor._yt_error_hint("ERROR: Private video").lower(),
          "private video vira indisponível")
    check(processor._yt_error_hint("algo aleatório") == "Não consegui baixar este vídeo do YouTube.",
          "erro desconhecido tem mensagem padrão")


# ─── 4b. Auto-snap do corte: não terminar/começar no meio da fala ─────────────
def test_snap_clip_bounds():
    print("[snap clip bounds]")
    srt = ("1\n00:00:05,000 --> 00:00:10,000\nPrimeira fala completa aqui.\n\n"
           "2\n00:00:11,000 --> 00:00:14,000\nSegunda fala curtinha.\n")
    # Corte termina no MEIO da 1a fala (9s): estende até o fim dela (10s).
    _s, e = captions.snap_clip_bounds(srt, 5.0, 9.0)
    check(abs(e - 10.0) < 1e-6, "fim no meio da fala estende até o fim do trecho")
    # Faltando mais que o teto pra terminar a fala: mantém o corte como veio.
    _s, e2 = captions.snap_clip_bounds(srt, 5.0, 6.0, max_tail=1.0)
    check(abs(e2 - 6.0) < 1e-6, "fala longa demais além do teto não estende")
    # Entrada no MEIO da 2a fala (12s): puxa o início pro começo dela (11s).
    s3, _e = captions.snap_clip_bounds(srt, 12.0, 13.5, max_head=2.0)
    check(abs(s3 - 11.0) < 1e-6, "início no meio da fala puxa pro começo do trecho")
    # Corte em silêncio (gap 10-11s): não mexe no fim.
    _s, e4 = captions.snap_clip_bounds(srt, 5.0, 10.5)
    check(abs(e4 - 10.5) < 1e-6, "corte em silêncio não estende")
    # Sem transcript: devolve os limites originais.
    s5, e5 = captions.snap_clip_bounds("", 5.0, 9.0)
    check(abs(s5 - 5.0) < 1e-6 and abs(e5 - 9.0) < 1e-6, "sem transcript mantém os limites")
    # Bounds inválidos (end <= start): devolve igual, sem quebrar.
    s6, e6 = captions.snap_clip_bounds(srt, 9.0, 9.0)
    check(s6 == 9.0 and e6 == 9.0, "intervalo inválido não quebra")


# ─── 5. Thumbnail: seleção do melhor clipe + URL no contrato ──────────────────
def test_thumbnail():
    print("[thumbnail]")
    # _best_clip: pega o de maior score; score ausente/inválido conta como -inf.
    check(processor._best_clip([]) is None, "lista vazia -> None")
    produced = [
        {"path": "a.mp4", "meta": {"score": 3}},
        {"path": "b.mp4", "meta": {"score": 9}},
        {"path": "c.mp4", "meta": {"score": 5}},
    ]
    check(processor._best_clip(produced)["path"] == "b.mp4", "escolhe o de maior score")
    mixed = [{"path": "a.mp4", "meta": {}}, {"path": "b.mp4", "meta": {"score": 1}}]
    check(processor._best_clip(mixed)["path"] == "b.mp4", "score ausente perde pro válido")

    # _thumb_url: rota relativa quando há key, None quando não há.
    check(app._thumb_url({"job_id": "J1", "thumbnail_key": "jobs/J1/thumb.jpg"}) == "/api/projects/J1/thumb",
          "com key -> rota /thumb")
    check(app._thumb_url({"job_id": "J1", "thumbnail_key": None}) is None, "sem key -> None")

    # _project_from_job: popula thumbnail_url só quando há key (degradação graciosa).
    base_job = {"job_id": "J1", "filename": "v.mp4", "status": "done",
                "result_urls": '[{"key":"k","score":9}]', "created_at": "t", "captions": 1}
    with_key = dict(base_job, thumbnail_key="jobs/J1/thumb.jpg")
    no_key = dict(base_job, thumbnail_key=None)
    check(app._project_from_job(with_key)["thumbnail_url"] == "/api/projects/J1/thumb",
          "projeto com frame expõe a rota")
    check(app._project_from_job(no_key)["thumbnail_url"] is None,
          "projeto sem frame mantém None (fallback de onda)")

    # _clip_objects: cada clipe tem o PROPRIO poster (rota /thumb por clipe), 1 frame do clipe.
    clips = app._clip_objects("J1", '[{"key":"k1"},{"key":"k2"}]', "/api/projects/J1/thumb")
    check(clips[0]["thumbnail_url"] == "/api/clips/J1:0/thumb"
          and clips[1]["thumbnail_url"] == "/api/clips/J1:1/thumb",
          "cada clipe tem o proprio poster (/api/clips/<id>/thumb)")
    clips2 = app._clip_objects("J1", '[{"key":"k1"}]')
    check(clips2[0]["thumbnail_url"] == "/api/clips/J1:0/thumb",
          "poster do clipe independe da thumb do projeto")
    # _thumb_key_for: deriva a chave do poster da base (novo e antigo).
    check(app._thumb_key_for("jobs/J1/clips/clip_01_base.mp4") == "jobs/J1/clips/clip_01_thumb.jpg",
          "thumb_key derivado da base nova (_base.mp4 -> _thumb.jpg)")
    check(app._thumb_key_for("jobs/J1/clips/clip_01_9x16.mp4") == "jobs/J1/clips/clip_01_9x16_thumb.jpg",
          "thumb_key derivado de clipe antigo (.mp4 -> _thumb.jpg)")


# ─── 6. Sessões server-side ───────────────────────────────────────────────────
def test_sessions_helpers():
    print("[sessions: helpers puros]")
    UAS = {
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36": ("Chrome", "Windows"),
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36 Edg/120.0": ("Edge", "Windows"),
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/17.0 Safari/605.1.15": ("Safari", "macOS"),
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1": ("Safari", "iOS"),
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0": ("Firefox", "Linux"),
        "Mozilla/5.0 (Linux; Android 13) SamsungBrowser/23.0 Chrome/115.0 Mobile Safari/537.36": ("Samsung Internet", "Android"),
        "Mozilla/5.0 (Windows NT 10.0) Chrome/115.0 Safari/537.36 OPR/101.0": ("Opera", "Windows"),
        "Mozilla/5.0 (X11; CrOS x86_64) Chrome/120.0 Safari/537.36": ("Chrome", "ChromeOS"),
    }
    for ua, exp in UAS.items():
        check(app._parse_ua(ua) == exp, f"UA -> {exp}")
    check(app._parse_ua(None) == ("Navegador", None), "UA None -> ('Navegador', None)")
    check(app._parse_ua("") == ("Navegador", None), "UA vazio -> ('Navegador', None)")
    check(app._parse_ua("curl/8.0") == ("Navegador", None), "UA curl -> fallback")

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    check(app._humanize_pt(now.isoformat()) == "agora", "humanize agora")
    check(app._humanize_pt((now - timedelta(minutes=5)).isoformat()) == "há 5 min", "humanize há 5 min")
    check(app._humanize_pt((now - timedelta(hours=3)).isoformat()) == "há 3 h", "humanize há 3 h")
    check(app._humanize_pt((now - timedelta(days=2)).isoformat()) == "há 2 d", "humanize há 2 d")
    check("/" in (app._humanize_pt((now - timedelta(days=45)).isoformat()) or ""), "humanize >30d -> data")
    check(app._humanize_pt(None) is None and app._humanize_pt("lixo") is None, "humanize None/lixo -> None")

    # _client_ip: confia no XFF só quando o peer é loopback (Nginx); pega o ÚLTIMO hop.
    with app.app.test_request_context(headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"},
                                      environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        check(app._client_ip() == "203.0.113.7", "XFF: pega o último hop, ignora o forjado")
    with app.app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        check(app._client_ip() is None, "loopback sem XFF -> None")
    with app.app.test_request_context(headers={"X-Forwarded-For": "9.9.9.9"},
                                      environ_base={"REMOTE_ADDR": "8.8.8.8"}):
        check(app._client_ip() == "8.8.8.8", "peer não-loopback: ignora XFF forjado, usa o peer")
    with app.app.test_request_context(environ_base={"REMOTE_ADDR": "172.17.0.5"}):
        check(app._client_ip() is None, "IP da bridge -> None")


def test_sessions_ownership():
    print("[sessions: ownership no banco]")
    import db
    db.create_session("sidA", "owner@test", "1.2.3.4", "ua")
    db.create_session("sidB", "outro@test", "5.6.7.8", "ua")
    db.delete_session("sidA", "outro@test")        # dono errado: não apaga
    check(db.get_session("sidA") is not None, "não apaga sessão de outro dono")
    db.delete_session("sidA", "owner@test")        # dono certo: apaga
    check(db.get_session("sidA") is None, "apaga a própria sessão")
    db.delete_user_sessions("outro@test")          # cascade
    check(db.get_session("sidB") is None, "cascade de delete_user_sessions")


def test_sessions_flow():
    print("[sessions: fluxo HTTP via test client]")
    import db
    from werkzeug.security import generate_password_hash
    db.upsert_user("flow@test", generate_password_hash("pw12345678"), must_change=False)
    UA = "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"

    c1 = app.app.test_client()
    c2 = app.app.test_client()
    r = c1.post("/api/login", json={"email": "flow@test", "password": "pw12345678"}, headers={"User-Agent": UA})
    check(r.status_code == 200, "login navegador 1 ok")
    r = c2.post("/api/login", json={"email": "flow@test", "password": "pw12345678"}, headers={"User-Agent": UA})
    check(r.status_code == 200, "login navegador 2 ok")

    js = c1.get("/api/sessions").get_json()
    check(len(js["sessions"]) == 2, "lista mostra 2 sessões")
    cur = [s for s in js["sessions"] if s["current"]]
    check(len(cur) == 1, "exatamente 1 marcada current")
    check(cur[0]["browser"] == "Chrome" and cur[0]["os"] == "Windows", "browser/os parseados do UA")
    cur_id = cur[0]["id"]

    # encerrar a PRÓPRIA sessão atual: no-op, continua logado
    r = c1.delete("/api/sessions/" + cur_id)
    check(r.status_code == 200 and r.get_json().get("ok") is True, "delete da atual responde ok")
    check(len(c1.get("/api/sessions").get_json()["sessions"]) == 2, "delete da atual foi no-op")

    # revoke_all do navegador 1: mata a sessão do navegador 2
    r = c1.post("/api/sessions/revoke_all")
    check(r.status_code == 200 and r.get_json().get("ok") is True, "revoke_all responde ok")
    check(len(c1.get("/api/sessions").get_json()["sessions"]) == 1, "sobra só a sessão atual")
    check(c2.get("/api/me").status_code == 401, "navegador 2 cai em 401 no request seguinte (revogado)")

    # logout remove a linha; /api/me vira 401
    check(c1.post("/api/logout").status_code == 200, "logout ok")
    check(c1.get("/api/me").status_code == 401, "após logout, /api/me 401")

    # rota interna /api/callback NÃO exige sessão (gate por IP da bridge), não vira 401
    r = app.app.test_client().post("/api/callback", json={"job_id": "naoexiste"},
                                   environ_base={"REMOTE_ADDR": "172.17.0.5"})
    check(r.status_code == 400, "/api/callback aberto da bridge sem sessão (400 por job inexistente, não 401/403)")

    db.delete_user_sessions("flow@test")
    with db._conn() as cc:
        cc.execute("DELETE FROM users WHERE email = ?", ("flow@test",))


def test_sessions_extra():
    print("[sessions: reuso/reap/método errado/troca de senha]")
    import db
    from datetime import datetime, timezone, timedelta
    from werkzeug.security import generate_password_hash

    # (a) re-login no MESMO navegador reusa o sid: não cria linha órfã.
    db.upsert_user("reuse@test", generate_password_hash("pw12345678"), must_change=False)
    c = app.app.test_client()
    UA = "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"
    c.post("/api/login", json={"email": "reuse@test", "password": "pw12345678"}, headers={"User-Agent": UA})
    c.post("/api/login", json={"email": "reuse@test", "password": "pw12345678"}, headers={"User-Agent": UA})
    check(len(c.get("/api/sessions").get_json()["sessions"]) == 1, "re-login reusa sid (sem linha órfã)")

    # (b) reap_sessions remove sessão inativa além da janela.
    db.create_session("reapsid", "reap@test", None, "ua")
    with db._conn() as cc:
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        cc.execute("UPDATE sessions SET last_active = ? WHERE id = ?", (old, "reapsid"))
    db.reap_sessions(7)
    check(db.get_session("reapsid") is None, "reap remove sessão inativa > janela")

    # (c) DELETE /api/sessions/revoke_all (método errado) cai no <sid> e é no-op 200.
    r = c.delete("/api/sessions/revoke_all")
    check(r.status_code == 200 and r.get_json().get("ok") is True, "DELETE revoke_all (método errado) é no-op 200")
    check(len(c.get("/api/sessions").get_json()["sessions"]) == 1, "continua logado após o no-op")

    # (d) trocar a senha encerra as OUTRAS sessões (mantém a atual).
    db.upsert_user("pwd@test", generate_password_hash("oldpw12345"), must_change=False)
    a = app.app.test_client(); b = app.app.test_client()
    a.post("/api/login", json={"email": "pwd@test", "password": "oldpw12345"}, headers={"User-Agent": UA})
    b.post("/api/login", json={"email": "pwd@test", "password": "oldpw12345"}, headers={"User-Agent": UA})
    r = a.post("/api/change_password", json={"current_password": "oldpw12345", "new_password": "newpw12345"})
    check(r.status_code == 200, "troca de senha ok")
    check(a.get("/api/me").status_code == 200, "quem trocou continua logado")
    check(b.get("/api/me").status_code == 401, "outra sessão é revogada após a troca")

    for em in ("reuse@test", "reap@test", "pwd@test"):
        db.delete_user_sessions(em)
    with db._conn() as cc:
        cc.execute("DELETE FROM users WHERE email IN (?, ?)", ("reuse@test", "pwd@test"))


def test_clip_window():
    print("[clip window: db + validação de in/out na rota]")
    import db
    import json as _json
    from werkzeug.security import generate_password_hash

    # round-trip no banco
    db.set_clip_captions("JW:0", _json.dumps([{"start": 0.0, "end": 1.0, "text": "x"}]))
    db.set_clip_window("JW:0", 12.0, 30.0, 10.0, 25.0)
    w = db.get_clip_window("JW:0")
    check(w is not None and abs(w["window_dur"] - 30.0) < 1e-6 and abs(w["in_point"] - 10.0) < 1e-6,
          "set/get_clip_window round-trip")
    db.set_clip_inout("JW:0", 8.0, 20.0)
    w = db.get_clip_window("JW:0")
    check(abs(w["in_point"] - 8.0) < 1e-6 and abs(w["out_point"] - 20.0) < 1e-6,
          "set_clip_inout atualiza in/out")
    check(abs(w["window_dur"] - 30.0) < 1e-6, "set_clip_inout não mexe na janela")
    check(db.get_clip_window("NOPE:0") is None, "clipe sem janela -> None")

    # validação da rota PUT /captions com in/out (clipe novo, editável)
    db.upsert_user("win@test", generate_password_hash("pw12345678"), must_change=False)
    db.create_job("JW", "v.mp4", 10, 60, "/x", "win@test")
    db.update_job("JW", status="done",
                  result_urls=_json.dumps([{"key": "jobs/JW/clips/clip_01_base.mp4"},
                                           {"key": "jobs/JW/clips/clip_02_base.mp4"}]))
    c = app.app.test_client()
    c.post("/api/login", json={"email": "win@test", "password": "pw12345678"})
    segs = [{"start": 0, "end": 1, "text": "a"}]

    r = c.put("/api/clips/JW:0/captions", json={"segments": segs, "in": 9.0, "out": 22.0})
    check(r.status_code == 200, "PUT in/out válido -> 200")
    w = db.get_clip_window("JW:0")
    check(abs(w["in_point"] - 9.0) < 1e-6 and abs(w["out_point"] - 22.0) < 1e-6, "PUT grava in/out")

    r = c.put("/api/clips/JW:0/captions", json={"segments": segs, "in": 20.0, "out": 20.3})
    check(r.status_code == 400, "corte curto demais (< mínimo) -> 400")

    r = c.put("/api/clips/JW:0/captions", json={"segments": segs, "in": 5.0, "out": 999.0})
    check(r.status_code == 200 and abs(db.get_clip_window("JW:0")["out_point"] - 30.0) < 1e-6,
          "out além da janela é clampado ao window_dur")

    db.set_clip_captions("JW:1", _json.dumps(segs))   # editável, mas SEM janela persistida
    r = c.put("/api/clips/JW:1/captions", json={"segments": segs, "in": 1.0, "out": 5.0})
    check(r.status_code == 409, "clipe sem handles: PUT in/out -> 409")

    # bounds no GET /captions
    js = c.get("/api/clips/JW:0/captions").get_json()
    check(js.get("bounds") and abs(js["bounds"]["window_dur"] - 30.0) < 1e-6,
          "GET /captions expõe bounds do clipe com janela")
    js1 = c.get("/api/clips/JW:1/captions").get_json()
    check(js1.get("bounds") is None, "GET /captions: bounds null pra clipe sem janela")

    db.delete_job("JW")   # cascade apaga clip_captions/favorites/music de JW:*
    with db._conn() as cc:
        cc.execute("DELETE FROM users WHERE email = ?", ("win@test",))


def test_burn_semaphore():
    """O semáforo global de queima nunca pode conceder mais que BURN_CONCURRENCY slots:
    é o que impede N ffmpeg em paralelo (a estampida que derrubava a VPS)."""
    print("[burn semaphore]")
    import config
    n = max(1, config.BURN_CONCURRENCY)
    got = [app._burn_sem.acquire(timeout=0.1) for _ in range(n)]
    check(all(got), f"adquire os {n} slot(s) de queima")
    extra = app._burn_sem.acquire(timeout=0.1)            # o slot a mais NÃO pode vir
    check(not extra, "bloqueia a queima além do teto (timeout -> serve a base)")
    if extra:                                             # defensivo: não corrompe o semáforo
        app._burn_sem.release()
    for _ in range(n):
        app._burn_sem.release()
    reacquired = app._burn_sem.acquire(timeout=0.1)       # release devolve o slot
    check(reacquired, "release devolve o slot pra próxima queima")
    if reacquired:
        app._burn_sem.release()


def test_presigned_put():
    """O presigned PUT (usado pelo worker GPU pra subir a base) é uma URL assinada localmente
    (sem rede) contendo a key. Garante que o helper existe e assina."""
    print("[presigned put]")
    import s3_client
    url = s3_client.get_presigned_put_url("jobs/TESTxx/clips/clip_01_base.mp4", "video/mp4", 60)
    check(isinstance(url, str) and url.startswith("http"), "presigned put devolve uma URL")
    check("clip_01_base.mp4" in url, "URL contém a key do objeto")


def test_runpod_client():
    """submit_reframe monta o payload certo (/run, executionTimeout em ms) e poll interpreta os
    estados (COMPLETED devolve output; FAILED e output com 'error' levantam). requests mockado."""
    print("[runpod client]")
    import runpod_client
    import config as _cfg

    class FakeResp:
        def __init__(self, data, status=200):
            self._d, self.status = data, status

        def raise_for_status(self):
            if self.status >= 400:
                raise RuntimeError(f"http {self.status}")

        def json(self):
            return self._d

    calls = {}

    class FakePost:
        RequestException = Exception

        def post(self, url, json=None, headers=None, timeout=None):
            calls["url"], calls["payload"] = url, json
            return FakeResp({"id": "job-1", "status": "IN_QUEUE"})

    def _get_returning(resp):
        class G(FakePost):
            def get(self, url, headers=None, timeout=None):
                return resp
        return G()

    orig_req = runpod_client.requests
    orig_ep = _cfg.RUNPOD_REFRAME_ENDPOINT_ID
    _cfg.RUNPOD_REFRAME_ENDPOINT_ID = "epTEST"
    try:
        runpod_client.requests = FakePost()
        jid = runpod_client.submit_reframe("http://get", "http://put", "video/mp4",
                                           {"deadzone_frac": 0.35}, 1800)
        check(jid == "job-1", "submit devolve o job_id")
        check("/epTEST/run" in calls["url"], "submit bate em /run do endpoint certo")
        check(calls["payload"]["input"]["input_url"] == "http://get", "input_url vai no payload")
        check(calls["payload"]["policy"]["executionTimeout"] == 1800000, "executionTimeout em ms")

        runpod_client.requests = _get_returning(FakeResp({"status": "COMPLETED", "output": {"duration": 12.5}}))
        out = runpod_client.poll("job-1", timeout=5)
        check(out.get("duration") == 12.5, "poll COMPLETED devolve o output")

        runpod_client.requests = _get_returning(FakeResp({"status": "FAILED", "output": "boom"}))
        try:
            runpod_client.poll("job-1", timeout=5)
            check(False, "poll FAILED deveria levantar")
        except RuntimeError:
            check(True, "poll FAILED levanta")

        runpod_client.requests = _get_returning(FakeResp({"status": "COMPLETED", "output": {"error": "x"}}))
        try:
            runpod_client.poll("job-1", timeout=5)
            check(False, "poll COMPLETED com 'error' deveria levantar")
        except RuntimeError:
            check(True, "poll COMPLETED com 'error' levanta (vira fallback CPU)")
    finally:
        runpod_client.requests = orig_req
        _cfg.RUNPOD_REFRAME_ENDPOINT_ID = orig_ep


def test_download_all():
    """download_all lista os clipes do projeto (id/filename/url) pro front baixar um a um;
    _safe_filename/_clip_download_name geram nomes amigáveis; o presigned com download_name
    embute o Content-Disposition de anexo."""
    print("[download all: helpers + rota]")
    import db
    import json as _json
    import s3_client
    from werkzeug.security import generate_password_hash

    # _safe_filename: tira acento/símbolo, colapsa espaço, limita tamanho, cai no fallback
    check(app._safe_filename("Início: O Vídeo!! ") == "Inicio O Video", "_safe_filename tira acento/símbolo")
    check(app._safe_filename("   ") == "clip", "_safe_filename vazio -> fallback")
    check(len(app._safe_filename("x" * 200)) <= 80, "_safe_filename limita o tamanho")

    # presigned com/sem download_name (assina local, sem rede)
    u = s3_client.get_presigned_url("jobs/T/clips/clip_01_cap.mp4", 60, download_name="01 Teste.mp4")
    check("response-content-disposition" in u.lower(), "presigned com download_name força attachment")
    u2 = s3_client.get_presigned_url("jobs/T/clips/clip_01_cap.mp4", 60)
    check("response-content-disposition" not in u2.lower(), "presigned sem download_name não força disposition")

    # a rota (offline: só lê o job e monta URLs, não toca o S3)
    db.upsert_user("dla@test", generate_password_hash("pw12345678"), must_change=False)
    db.create_job("JDLA", "v.mp4", 10, 60, "/x", "dla@test")
    db.update_job("JDLA", status="done",
                  result_urls=_json.dumps([{"key": "jobs/JDLA/clips/clip_01_base.mp4", "title": "Abertura"},
                                           {"key": "jobs/JDLA/clips/clip_02_base.mp4"}]))
    c = app.app.test_client()
    c.post("/api/login", json={"email": "dla@test", "password": "pw12345678"})
    r = c.get("/api/projects/JDLA/download_all")
    check(r.status_code == 200, "download_all -> 200")
    d = r.get_json()
    check(d.get("count") == 2 and len(d.get("clips", [])) == 2, "lista os 2 clipes")
    check(d["clips"][0]["url"].endswith("?dl=1") and "JDLA:0" in d["clips"][0]["url"],
          "url aponta pra /video?dl=1 do clipe")
    check(d["clips"][0]["filename"] == "01 Abertura.mp4", "filename usa o título quando há")
    check(d["clips"][1]["filename"] == "clip_02.mp4", "clipe sem título cai no fallback clip_NN")
    check(c.get("/api/projects/NOPE/download_all").status_code == 404, "projeto inexistente -> 404")

    db.delete_job("JDLA")
    with db._conn() as cc:
        cc.execute("DELETE FROM users WHERE email = ?", ("dla@test",))


def test_end_screen():
    """Tela final: derivação de chave, resolução por projeto+clipe (herança/override), opções, a
    invalidação dos finais _es, e as rotas (biblioteca, default do projeto, override do clipe)."""
    print("[end screen: resolução + invalidação + rotas]")
    import db
    import json as _json
    import s3_client
    import config
    from werkzeug.security import generate_password_hash

    db.ensure_end_screen("clubcast", "Clubcast", "assets/end_screens/clubcast.mp4", 4.586667)

    # _endscreen_key: content -> final
    check(app._endscreen_key("jobs/J/clips/clip_01_cap.mp4") == "jobs/J/clips/clip_01_cap_es.mp4",
          "_endscreen_key: cap -> cap_es")
    check(app._endscreen_key("jobs/J/clips/clip_01_base.mp4") == "jobs/J/clips/clip_01_base_es.mp4",
          "_endscreen_key: base -> base_es")
    check(app._endscreen_key("jobs/J/clips/clip_01_9x16.mp4") == "jobs/J/clips/clip_01_9x16_es.mp4",
          "_endscreen_key: clipe antigo -> _es")
    check(app._endscreen_key("jobs/J/clips/clip_01_cap_es.mp4") == "jobs/J/clips/clip_01_cap_es.mp4",
          "_endscreen_key: idempotente")

    check("clubcast" in [o["id"] for o in app._end_screen_options()], "biblioteca inclui clubcast")

    # _resolve_end_screen_id: herança (projeto) + override (clipe)
    db.set_clip_captions("JES:0", _json.dumps([{"start": 0, "end": 1, "text": "x"}]))
    job_none, job_club = {"end_screen": None}, {"end_screen": "clubcast"}
    check(app._resolve_end_screen_id(job_none, "JES:0") is None, "sem projeto/sem override -> None")
    check(app._resolve_end_screen_id(job_club, "JES:0") == "clubcast", "herda o default do projeto")
    db.set_clip_end_screen("JES:0", "none")
    check(app._resolve_end_screen_id(job_club, "JES:0") is None, "override 'none' remove mesmo com projeto")
    db.set_clip_end_screen("JES:0", "clubcast")
    check(app._resolve_end_screen_id(job_none, "JES:0") == "clubcast", "override força mesmo sem projeto")
    db.set_clip_end_screen("JES:0", "xxx")
    check(app._resolve_end_screen_id(job_none, "JES:0") is None, "id desconhecido -> None")
    db.set_clip_end_screen("JES:0", None)

    # invalidação (monkeypatch do delete_object/delete_prefix: sem rede, captura as chaves)
    deleted = []
    _orig_del = s3_client.delete_object
    _orig_delp = s3_client.delete_prefix
    s3_client.delete_object = lambda k: deleted.append(k)
    s3_client.delete_prefix = lambda p: deleted.append(p) or 0
    try:
        app._invalidate_cap("jobs/J/clips/clip_01_base.mp4")
        check(set(deleted) == {"jobs/J/clips/clip_01_cap.mp4", "jobs/J/clips/clip_01_trim.mp4",
                               "jobs/J/clips/clip_01_cap_es.mp4", "jobs/J/clips/clip_01_trim_es.mp4",
                               "jobs/J/clips/clip_01_base_es.mp4"},
              "_invalidate_cap apaga content (_cap/_trim) + finais _es")
        deleted.clear()
        app._invalidate_endscreen("jobs/J/clips/clip_01_base.mp4")
        check(set(deleted) == {"jobs/J/clips/clip_01_cap_es.mp4", "jobs/J/clips/clip_01_trim_es.mp4",
                               "jobs/J/clips/clip_01_base_es.mp4"},
              "_invalidate_endscreen (novo) apaga só os finais _es")
        deleted.clear()
        app._invalidate_endscreen("jobs/J/clips/clip_01_9x16.mp4")
        check(deleted == ["jobs/J/clips/clip_01_9x16_es.mp4"],
              "_invalidate_endscreen (antigo) apaga só o _9x16_es")

        # rotas
        db.upsert_user("es@test", generate_password_hash("pw12345678"), must_change=False)
        db.create_job("JES", "v.mp4", 10, 60, "/x", "es@test")
        db.update_job("JES", status="done",
                      result_urls=_json.dumps([{"key": "jobs/JES/clips/clip_01_base.mp4"},
                                               {"key": "jobs/JES/clips/clip_02_base.mp4"}]))
        db.set_clip_captions("JES:0", _json.dumps([{"start": 0, "end": 1, "text": "a"}]))
        c = app.app.test_client()
        c.post("/api/login", json={"email": "es@test", "password": "pw12345678"})

        r = c.get("/api/end_screens")
        check(r.status_code == 200 and any(o["id"] == "clubcast" for o in r.get_json()["end_screens"]),
              "GET /api/end_screens lista clubcast")

        deleted.clear()
        r = c.put("/api/projects/JES/end_screen", json={"end_screen": "clubcast"})
        check(r.status_code == 200 and db.get_job("JES").get("end_screen") == "clubcast",
              "PUT projeto end_screen grava o default")
        check(any(k.endswith("_es.mp4") for k in deleted), "PUT projeto invalida os finais _es dos clipes")
        check(c.put("/api/projects/JES/end_screen", json={"end_screen": "naoexiste"}).status_code == 400,
              "PUT projeto id inválido -> 400")
        check(c.put("/api/projects/JES/end_screen", json={"end_screen": "none"}).status_code == 400,
              "PUT projeto não aceita 'none' (só null ou id)")
        check(c.put("/api/projects/NOPE/end_screen", json={"end_screen": "clubcast"}).status_code == 404,
              "PUT projeto inexistente -> 404")

        js = c.get("/api/clips/JES:0/captions").get_json()
        check(js.get("project_end_screen") == "clubcast", "GET /captions expõe default do projeto")
        check(js.get("end_screen_effective") == "clubcast", "GET /captions: efetivo herda do projeto")
        check(js.get("end_screen") is None, "GET /captions: override do clipe ainda null")
        check(any(o["id"] == "clubcast" for o in js.get("end_screens", [])), "GET /captions traz a biblioteca")

        r = c.put("/api/clips/JES:0/end_screen", json={"end_screen": "none"})
        check(r.status_code == 200 and r.get_json().get("end_screen_effective") is None,
              "PUT clipe 'none' remove (efetivo None)")
        check(db.get_clip_end_screen("JES:0") == "none", "override 'none' persistido")
        r = c.put("/api/clips/JES:0/end_screen", json={"end_screen": None})
        check(r.status_code == 200 and r.get_json().get("end_screen_effective") == "clubcast",
              "PUT clipe null volta a herdar (efetivo clubcast)")
        check(c.put("/api/clips/JES:0/end_screen", json={"end_screen": "xxx"}).status_code == 400,
              "PUT clipe id inválido -> 400")

        # duração (encurtar a tela final por clipe): grava, clampa ao asset, rejeita inválida, limpa
        r = c.put("/api/clips/JES:0/end_screen", json={"dur": 3.0})
        check(r.status_code == 200 and abs((db.get_clip_end_screen_dur("JES:0") or 0) - 3.0) < 1e-6,
              "PUT clipe dur=3 grava a duração")
        c.put("/api/clips/JES:0/end_screen", json={"dur": 999})
        check(abs((db.get_clip_end_screen_dur("JES:0") or 0) - 4.586667) < 0.05,
              "dur acima do asset é clampada à duração da tela (clubcast ~4,59s)")
        check(c.put("/api/clips/JES:0/end_screen", json={"dur": -1}).status_code == 400, "dur <=0 -> 400")
        c.put("/api/clips/JES:0/end_screen", json={"dur": None})
        check(db.get_clip_end_screen_dur("JES:0") is None, "dur null volta a usar a tela inteira")
        check(c.get("/api/clips/JES:0/captions").get_json().get("end_screen_dur") is None,
              "GET /captions expõe end_screen_dur")
        opt = next((o for o in app._end_screen_options() if o["id"] == "clubcast"), {})
        check(abs((opt.get("duration") or 0) - 4.586667) < 0.05, "biblioteca traz a duração do asset")

        # biblioteca: add + DELETE (rota)
        db.add_end_screen("testes_es_x", "Teste", "assets/end_screens/testes_es_x.mp4", 5.0)
        check(db.get_end_screen("testes_es_x") is not None, "add_end_screen grava na biblioteca")
        r = c.delete("/api/end_screens/testes_es_x")
        check(r.status_code == 200 and db.get_end_screen("testes_es_x") is None,
              "DELETE remove da biblioteca")

        db.update_job("JES", result_urls=_json.dumps([{"key": "jobs/JES/clips/clip_01_9x16.mp4"}]))
        check(c.put("/api/clips/JES:0/end_screen", json={"end_screen": "clubcast"}).status_code == 409,
              "PUT clipe antigo (não editável) -> 409")
    finally:
        s3_client.delete_object = _orig_del
        s3_client.delete_prefix = _orig_delp

    db.delete_job("JES")
    with db._conn() as cc:
        cc.execute("DELETE FROM users WHERE email = ?", ("es@test",))
        cc.execute("DELETE FROM end_screens WHERE id = ?", ("testes_es_x",))


def main():
    for t in (test_burn_cmd, test_burn_semaphore, test_presigned_put, test_runpod_client,
              test_validate_segments, test_parse_clips_json,
              test_captions_roundtrip, test_words_for_clip, test_caption_style, test_youtube_helpers,
              test_snap_clip_bounds, test_thumbnail, test_clip_window, test_download_all, test_end_screen,
              test_sessions_helpers, test_sessions_ownership, test_sessions_flow,
              test_sessions_extra):
        t()
    print()
    if _failures:
        print(f"FALHOU: {len(_failures)} verificação(ões)")
        sys.exit(1)
    print("TODOS OS TESTES PASSARAM")


if __name__ == "__main__":
    try:
        main()
    finally:
        os.unlink(_TMP_DB.name)
        for ext in ("-wal", "-shm"):
            p = _TMP_DB.name + ext
            if os.path.exists(p):
                os.unlink(p)
