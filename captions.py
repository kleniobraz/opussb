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
    """Extrai a legenda do clipe [clip_start, clip_end] (segundos absolutos do
    vídeo original) como DADO editável: lista de {start, end, text} com tempos
    relativos ao início do clipe, já quebrada em uma linha por evento. É isto que
    vai pro banco (clip_captions) e que o usuário corrige."""
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


def build_ass_from_segments(segments: list):
    """Renderiza o .ass no estilo SB a partir de uma lista de {start, end, text}
    (tempos relativos ao clipe). Retorna a string .ass ou None se vazia. O texto
    é re-sanitizado aqui porque pode ter vindo editado pelo usuário (Parte 3)."""
    if not segments:
        return None

    glow = f"&H{GLOW_ALPHA:02X}000000"
    # Spacing do estilo fica 0 de propósito: o libass ignora esse campo. O aperto
    # entre letras é aplicado por evento via \fsp no prefixo (ver abaixo).
    style = (
        "Style: SB,{font},{size},{fill},{fill},{glow},{glow},"
        "0,0,0,0,100,100,0,0,1,{outline},0,{align},{ml},{mr},{mv},1"
    ).format(
        font=FONT_NAME, size=FONT_SIZE, fill=FILL, glow=glow,
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
        f"{style}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    prefix = f"{{\\blur{GLOW_BLUR}\\fsp{TRACKING_PX:g}}}"
    body = "".join(
        f"Dialogue: 0,{_fmt_time(s['start'])},{_fmt_time(s['end'])},SB,,0,0,0,,{prefix}{_sanitize(s['text'])}\n"
        for s in segments
    )
    return header + body


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
