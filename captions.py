"""Geração de legenda no padrão SB a partir do transcript (SRT) do Whisper.

O transcript já chega em SRT, na mesma linha do tempo absoluta do vídeo original
(o mesmo eixo que o start/end de cada clipe vindo do Claude). Aqui a gente:
  1) parseia o SRT em segmentos {start, end, text} em segundos;
  2) `segments_for_clip` recorta por clipe, desloca os tempos para a origem do
     clipe e quebra em uma linha por evento -> isto é o DADO editável que vai pro
     banco (clip_captions) e que o usuário corrige;
  3) `build_ass_from_segments` rende o .ass (libass) a partir desse dado, pra
     queimar no ffmpeg (na queima sob demanda).

Os parâmetros do estilo (extraídos do .prtextstyle + painel Essential Graphics e
guardados na memória do projeto) ficam como constantes no topo, pra ajuste fino
num ponto só. Resolução de referência fixa em 1080x1920 (saída do reframe).
"""

import re
import math

# ─── Estilo SB (1080x1920) ───────────────────────────────────────────────────
PLAY_W, PLAY_H = 1080, 1920
FONT_NAME = "Inter Medium"      # SF Pro Display no design; Inter Medium é o substituto
FONT_SIZE = 70
TRACKING_PX = -3.0              # aperto entre letras, aplicado via \fsp inline (libass IGNORA o
                               # campo Spacing do estilo). Spec Premiere era ~-4.7; valor escolhido pelo Klenio.
FILL = "&H00FFFFFF"            # branco opaco (formato ASS: &HAABBGGRR)
# Sombra do Premiere (distância 2 + blur 155 + opacidade 86%) = halo escuro suave
# atrás do texto. No libass reproduzimos como contorno escuro borrado (glow).
GLOW_ALPHA = 0x24              # ~86% opaco -> alpha (1-0.86)*255 ≈ 36 = 0x24
GLOW_OUTLINE = 6              # espessura do halo (px), ajustável
GLOW_BLUR = 10               # suavidade do halo (\blur), ajustável
ALIGNMENT = 2                # 2 = base-centro (numpad ASS)
MARGIN_LR = 80               # margem lateral (área segura)
MARGIN_V = 580               # distância da base; maior = mais pra cima
# Layout do texto: SEMPRE uma linha por evento (não usamos duas linhas)
MAX_LINE_CHARS = 22          # ~largura segura pra fonte 70 em 1080
MAX_LINES = 1

_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(srt_text: str) -> list:
    """Converte SRT em [{start, end, text}] (segundos). Tolerante a fences ``` e
    a blocos malformados (pula o que não tiver linha de tempo)."""
    if not srt_text:
        return []
    raw = srt_text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.startswith("srt"):
            raw = raw[3:]
    segments = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # acha a linha de tempo (pode ou não ter índice numérico antes)
        time_idx = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                time_idx = i
                break
        if time_idx is None:
            continue
        stamps = _TS.findall(lines[time_idx])
        if len(stamps) < 2:
            continue
        start = _to_seconds(*stamps[0])
        end = _to_seconds(*stamps[1])
        text = " ".join(ln.strip() for ln in lines[time_idx + 1:]).strip()
        if not text or end <= start:
            continue
        segments.append({"start": start, "end": end, "text": text})
    return segments


def snap_clip_bounds(srt_text, start, end, max_tail: float = 2.5, max_head: float = 1.0):
    """Ajusta [start, end] (segundos absolutos do vídeo) pra o corte não cair no meio
    de uma fala, usando o SRT da transcrição:
      - FIM: se o ponto de corte cai DENTRO de um trecho falado, estende até o fim desse
        trecho — no máximo `max_tail` s a mais (senão a frase é longa demais e mantém o
        corte original).
      - INÍCIO: se o ponto de entrada cai no MEIO de um trecho, puxa pro começo dele — no
        máximo `max_head` s antes.
    Função pura e idempotente: sem SRT, sem fala no ponto de corte, ou bounds inválidos,
    devolve os limites de entrada (arredondados a 3 casas). Devolve (start, end) floats."""
    try:
        s = float(start)
        e = float(end)
    except (TypeError, ValueError):
        return start, end
    if not (math.isfinite(s) and math.isfinite(e)) or e <= s:
        return start, end
    segs = parse_srt(srt_text)
    if not segs:
        return round(s, 3), round(e, 3)
    for seg in segs:                       # fim: estende até terminar a fala em curso
        if seg["start"] < e < seg["end"]:
            if seg["end"] - e <= max_tail:
                e = seg["end"]
            break
    for seg in segs:                       # início: puxa pro começo da fala em curso
        if seg["start"] < s < seg["end"]:
            if s - seg["start"] <= max_head:
                s = max(0.0, seg["start"])
            break
    if e <= s:
        return round(float(start), 3), round(float(end), 3)
    return round(s, 3), round(e, 3)


def _sanitize(text: str) -> str:
    """Neutraliza o que o parser do ASS interpretaria como controle. Aplicado
    também no render porque o texto pode ter vindo editado pelo usuário."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").strip()


def _layout_segment(text: str, start: float, end: float) -> list:
    """Quebra o texto do segmento em eventos de até MAX_LINES linhas curtas e
    distribui a duração proporcional ao tamanho de cada evento (aproxima o tempo
    por palavra sem precisar de timestamp por palavra)."""
    words = _sanitize(text).split()
    if not words:
        return []
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= MAX_LINE_CHARS or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    events = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]
    weights = [sum(len(ln) for ln in ev) for ev in events]
    total = sum(weights) or 1
    out, t, dur = [], start, max(end - start, 0.01)
    for ev, w in zip(events, weights):
        seg_dur = dur * (w / total)
        out.append(("\\N".join(ev), t, t + seg_dur))
        t += seg_dur
    return out


def _fmt_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def segments_for_clip(srt_text: str, clip_start: float, clip_end: float) -> list:
    """Extrai a legenda do intervalo [clip_start, clip_end] (segundos absolutos do
    vídeo original) como DADO editável: lista de {start, end, text} com tempos
    relativos ao início do intervalo, já quebrada em uma linha por evento. É isto que
    vai pro banco (clip_captions) e que o usuário corrige.

    Com handles (margem de corte), o pipeline passa [window_start, window_end] aqui, então os
    tempos ficam relativos ao início da JANELA (cobrindo as sobras). A queima sob demanda corta o
    vídeo em [in_point, out_point] via output seek; a legenda continua na escala da janela e o
    próprio corte do ffmpeg descarta os eventos das sobras (validado com o ffmpeg da VPS)."""
    out = []
    for seg in parse_srt(srt_text):
        ov_s = max(seg["start"], clip_start)
        ov_e = min(seg["end"], clip_end)
        if ov_e - ov_s <= 0.05:
            continue
        for txt, es, ee in _layout_segment(seg["text"], ov_s, ov_e):
            out.append({
                "start": round(es - clip_start, 3),
                "end": round(ee - clip_start, 3),
                "text": txt,
            })
    return out


def _phrase_bounds(srt_text):
    """Fronteiras de frase (segmentos do SRT) como [(start, end)] em segundos absolutos.
    Usadas so pra NAO juntar palavras de frases diferentes na mesma linha de legenda."""
    return [(s["start"], s["end"]) for s in parse_srt(srt_text)]


def _phrase_index_at(bounds, t):
    """Indice da frase cujo intervalo contem t (senao -1)."""
    for i, (s, e) in enumerate(bounds):
        if s <= t < e:
            return i
    return -1


def words_for_clip(words, srt_text, clip_start, clip_end, max_gap=0.6):
    """Igual ao segments_for_clip (mesma forma: [{start,end,text}] relativo a clip_start, uma
    linha curta por evento), mas o TIMING vem do tempo por PALAVRA do Whisper
    (output.word_timestamps), nao da reparticao char-proporcional do _layout_segment.

    Conserta a legenda 'adiantada': cada evento comeca/termina na PALAVRA real falada, em vez
    de no inicio folgado da frase do Whisper. Quebra a linha quando passaria de MAX_LINE_CHARS,
    ou cruzaria uma fronteira de frase (segmento SRT), ou ha uma pausa maior que max_gap.
    words = [{start,end,word}] em segundos absolutos do video.

    Retorna [] se nao houver palavra util na janela; o chamador cai no segments_for_clip
    (modo dual: clipe sem word_timestamps continua funcionando como antes)."""
    if not words:
        return []
    bounds = _phrase_bounds(srt_text)
    win = []
    for w in words:
        try:
            ws = float(w["start"])
            we = float(w["end"])
            tok = _sanitize(str(w.get("word", ""))).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not tok or not (math.isfinite(ws) and math.isfinite(we)) or we <= ws:
            continue
        if we <= clip_start or ws >= clip_end:
            continue
        win.append((max(ws, clip_start), min(we, clip_end), tok))
    win.sort()
    if not win:
        return []
    out = []
    cur, cur_start, cur_end, cur_phrase, prev_end = [], 0.0, 0.0, None, None
    for ws, we, tok in win:
        phrase = _phrase_index_at(bounds, ws) if bounds else 0
        cand_len = len((" ".join(cur + [tok])).strip())
        gap = (ws - prev_end) if prev_end is not None else 0.0
        if cur and (cand_len > MAX_LINE_CHARS or phrase != cur_phrase or gap > max_gap):
            out.append({"start": round(cur_start - clip_start, 3),
                        "end": round(cur_end - clip_start, 3),
                        "text": " ".join(cur)})
            cur = []
        if not cur:
            cur_start, cur_phrase = ws, phrase
        cur.append(tok)
        cur_end = we
        prev_end = we
    if cur:
        out.append({"start": round(cur_start - clip_start, 3),
                    "end": round(cur_end - clip_start, 3),
                    "text": " ".join(cur)})
    return out


def clip_words_window(words, clip_start, clip_end):
    """Palavras CRUAS da janela [clip_start, clip_end] (s absolutos) como [{start,end,word}] com
    tempos relativos a clip_start. Pro editor word-level (karaoke por palavra + pilulas de pausa).
    Difere de words_for_clip (que AGRUPA em linhas de legenda); aqui e palavra a palavra."""
    out = []
    for w in words or []:
        try:
            ws = float(w["start"])
            we = float(w["end"])
            tok = str(w.get("word", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not tok or not (math.isfinite(ws) and math.isfinite(we)) or we <= ws:
            continue
        if we <= clip_start or ws >= clip_end:
            continue
        out.append({"start": round(max(ws, clip_start) - clip_start, 3),
                    "end": round(min(we, clip_end) - clip_start, 3),
                    "word": tok})
    return out


def _hex_to_ass(color):
    """'#RRGGBB' (ou 'RRGGBB') -> cor de preenchimento ASS '&H00BBGGRR'. None se inválido.
    O ASS guarda a cor como AABBGGRR (ordem invertida do RGB), alpha 00 = opaco."""
    if not isinstance(color, str):
        return None
    c = color.strip().lstrip("#")
    if len(c) != 6:
        return None
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _resolve_style(style):
    """Mescla os overrides do clipe (dict possivelmente parcial vindo do editor) com o estilo
    SB padrão. Retorna (font_size, tracking_px, fill, glow_alpha). Valor inválido ou fora de
    faixa cai no padrão SB. style None/{} = padrão SB puro.

    shadow é a INTENSIDADE da sombra (0..1) = opacidade do halo; vira o alpha do glow
    (alpha = (1-shadow)*255). O padrão SB (GLOW_ALPHA 0x24) equivale a shadow ≈ 0.86."""
    size, tracking, fill, glow_alpha = FONT_SIZE, TRACKING_PX, FILL, GLOW_ALPHA
    if isinstance(style, dict):
        try:
            v = float(style.get("size"))
            if math.isfinite(v):
                size = int(min(160, max(24, v)))
        except (TypeError, ValueError):
            pass
        try:
            v = float(style.get("tracking"))
            if math.isfinite(v):
                tracking = min(40.0, max(-20.0, v))
        except (TypeError, ValueError):
            pass
        a = _hex_to_ass(style.get("color"))
        if a:
            fill = a
        try:
            v = float(style.get("shadow"))
            if math.isfinite(v):
                glow_alpha = int(round((1.0 - min(1.0, max(0.0, v))) * 255))
        except (TypeError, ValueError):
            pass
    return size, tracking, fill, glow_alpha


def build_ass_from_segments(segments: list, caption_y=None, style=None):
    """Renderiza o .ass no estilo SB a partir de uma lista de {start, end, text}
    (tempos relativos ao clipe). Retorna a string .ass ou None se vazia. O texto
    é re-sanitizado aqui porque pode ter vindo editado pelo usuário (Parte 3).

    caption_y (fração 0..1 do centro, a partir do topo) reposiciona a legenda na
    vertical via \\an5\\pos (centralizada). None = posição padrão do estilo SB
    (alignment 2 + MARGIN_V). O usuário define isso arrastando no editor do front.

    style (dict opcional {size, tracking, color, shadow}) sobrescreve a fonte por clipe
    (painel "Estilo" do editor); chaves ausentes/invalidas usam o padrão SB. Ver _resolve_style."""
    if not segments:
        return None

    size, tracking, fill, glow_alpha = _resolve_style(style)
    glow = f"&H{glow_alpha:02X}000000"
    # Spacing do estilo fica 0 de propósito: o libass ignora esse campo. O aperto
    # entre letras é aplicado por evento via \fsp no prefixo (ver abaixo).
    style_line = (
        "Style: SB,{font},{size},{fill},{fill},{glow},{glow},"
        "0,0,0,0,100,100,0,0,1,{outline},0,{align},{ml},{mr},{mv},1"
    ).format(
        font=FONT_NAME, size=size, fill=fill, glow=glow,
        outline=GLOW_OUTLINE, align=ALIGNMENT, ml=MARGIN_LR, mr=MARGIN_LR, mv=MARGIN_V,
    )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\n"
        f"PlayResY: {PLAY_H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    # Reposicionamento vertical opcional: \an5 (centro) + \pos no centro horizontal.
    pos_tag = ""
    if caption_y is not None:
        try:
            cy = float(caption_y)
            if math.isfinite(cy):
                cy = min(0.95, max(0.05, cy))
                pos_tag = f"\\an5\\pos({PLAY_W // 2},{round(cy * PLAY_H)})"
        except (TypeError, ValueError):
            pos_tag = ""
    prefix = f"{{\\blur{GLOW_BLUR}\\fsp{tracking:g}{pos_tag}}}"
    lines = []
    for s in segments:
        # Defesa: um segmento salvo com tempo inválido (NaN/inf, end<=start) não pode
        # virar uma linha Dialogue quebrada. _validate_segments já filtra na entrada,
        # mas o render também é chamado a partir de dado persistido e do pipeline.
        try:
            st = float(s["start"])
            en = float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(st) and math.isfinite(en)) or en <= st:
            continue
        txt = _sanitize(str(s.get("text", "")))
        if not txt:
            continue
        lines.append(f"Dialogue: 0,{_fmt_time(st)},{_fmt_time(en)},SB,,0,0,0,,{prefix}{txt}")
    if not lines:
        return None
    return header + "\n".join(lines) + "\n"


def build_ass_for_clip(srt_text: str, clip_start: float, clip_end: float):
    """Atalho: extrai os segmentos do SRT e já renderiza o .ass (mantido por
    compatibilidade; o pipeline agora persiste os segmentos e rende a partir deles
    via build_ass_from_segments)."""
    return build_ass_from_segments(segments_for_clip(srt_text, clip_start, clip_end))


if __name__ == "__main__":
    sample = (
        "1\n00:00:05,740 --> 00:00:09,680\n"
        "Qual você acha que é o maior erro que as empresas cometem em marketing hoje?\n\n"
        "2\n00:00:10,020 --> 00:00:15,760\n"
        "Eu acredito que depositar muita confiança em uma única estratégia.\n"
    )
    segs = segments_for_clip(sample, 5.74, 16.0)
    print("segmentos do clipe:", len(segs))
    print("primeiro:", segs[0])
    ass = build_ass_from_segments(segs)
    assert ass and "Inter Medium" in ass and "Dialogue:" in ass
    # round-trip: editar um texto e re-renderizar
    segs[0]["text"] = "Nome Corrigido da Empresa"
    ass2 = build_ass_from_segments(segs)
    assert "Nome Corrigido da Empresa" in ass2
    print("OK: dado editável + render a partir do dado (com edição) funcionam")
