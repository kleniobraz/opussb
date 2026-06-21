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

    # _clip_objects: repassa a thumbnail_url do projeto pra cada clipe.
    clips = app._clip_objects("J1", '[{"key":"k1"},{"key":"k2"}]', "/api/projects/J1/thumb")
    check(all(c["thumbnail_url"] == "/api/projects/J1/thumb" for c in clips),
          "cada clipe herda a thumbnail do projeto")
    clips2 = app._clip_objects("J1", '[{"key":"k1"}]')
    check(clips2[0]["thumbnail_url"] is None, "sem thumbnail -> clipe com None")


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


def main():
    for t in (test_burn_cmd, test_validate_segments, test_parse_clips_json,
              test_captions_roundtrip, test_thumbnail,
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
