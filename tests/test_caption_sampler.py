"""Testes da detecção pura de estados de legenda (caption_sampler).

Bateria sintética por ARRAYS (sem ffmpeg, sem rede, rápida): frames desenhados com
PIL simulando os 3 estilos de legenda (frase acumulativa, karaokê palavra-a-palavra,
legenda fora do rodapé) + os casos de borda (sem legenda, orçamento).

Inclui o REGRESSION PERMANENTE do caso "inteligencia" (2026-07-03): frase acumulativa
cuja última palavra vive ~300ms antes da frase limpar. O bug original (dedup com
threshold de cena) perdia exatamente esse estado. Este teste nunca sai daqui.
"""
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import caption_sampler as cs

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
pytestmark = pytest.mark.skipif(not os.path.exists(FONT_PATH),
                                reason="fonte DejaVu ausente")

W, H = 180, 320  # thumbnail da varredura (proporção 9:16)
FPS = 8


def _font(size=13):
    return ImageFont.truetype(FONT_PATH, size)


def _wrap(text: str, width: int = 20) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if len(cand) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines[-2:]  # legenda real tem 1-2 linhas na tela


def _thumb(text: str, y_frac: float = 0.72, bg: int = 18) -> np.ndarray:
    im = Image.new("L", (W, H), bg)
    d = ImageDraw.Draw(im)
    f = _font()
    y = int(H * y_frac)
    for line in _wrap(text):
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, y), line, font=f, fill=255)
        y += 16
    return np.asarray(im, dtype=np.uint8)


def _stack(events: list[tuple[float, float, str]], duration: float,
           y_frac: float = 0.72) -> np.ndarray:
    """events: (t_inicio, t_fim, texto). Fora de qualquer evento a legenda fica vazia."""
    frames = []
    for i in range(int(duration * FPS)):
        t = i / FPS
        text = ""
        for t0, t1, txt in events:
            if t0 <= t < t1:
                text = txt
        frames.append(_thumb(text, y_frac))
    return np.stack(frames)


def _moving_background(duration: float) -> np.ndarray:
    """Vídeo sem legenda: gradiente em movimento contínuo (toda linha muda sempre)."""
    yy, xx = np.mgrid[0:H, 0:W]
    frames = []
    for i in range(int(duration * FPS)):
        arr = ((xx * 2 + yy + i * 7) % 200 + 20).astype(np.uint8)
        frames.append(arr)
    return np.stack(frames)


def _detect(stack: np.ndarray):
    row_diffs, d_full, edges, n = cs.scan_profile(iter(stack))
    band = cs.detect_band(row_diffs, edges, d_full=d_full)
    return row_diffs, d_full, edges, n, band


def _band_states(stack: np.ndarray, band, **kw):
    y0f, y1f, _ = band
    th = stack.shape[1]
    band_stack = stack[:, int(y0f * th):int(y1f * th), :]
    row_diffs, d_full, _, _ = cs.scan_profile(iter(stack))
    return cs.segment_states(band_stack, d_full, **kw)


def _accumulative_events(words: list[str], step: float, clear_at: float):
    """Frase acumulando uma palavra por vez a cada `step`, limpa em `clear_at`."""
    ev = []
    for i in range(len(words)):
        t0 = i * step
        t1 = (i + 1) * step if i < len(words) - 1 else clear_at
        ev.append((t0, t1, " ".join(words[:i + 1])))
    return ev


# ─── REGRESSION PERMANENTE: caso "inteligencia" ───────────────────────────────

def test_regression_inteligencia_captura_o_estado_final():
    """A última palavra da frase aparece e a frase limpa ~300ms depois. O sampler TEM
    que emitir um estado contendo a frase completa (o bug original perdia esse estado
    e 'inteligencia' sem acento nunca chegava ao modelo)."""
    words = ["estamos", "formando", "o", "grupo", "sb", "com", "forca", "e", "inteligencia"]
    clear_at = 0.4 * (len(words) - 1) + 0.3  # última palavra vive só 300ms
    stack = _stack(_accumulative_events(words, 0.4, clear_at), duration=clear_at + 1.0)

    _, _, _, _, band = _detect(stack)
    assert band is not None, "banda da legenda não detectada no rodapé"
    states = _band_states(stack, band)
    captions = [s for s in states if s["kind"] == "caption"]
    assert captions, "nenhum estado de legenda emitido"

    # janela em que a frase COMPLETA (com 'inteligencia') está na tela
    full_ini = int(0.4 * (len(words) - 1) * FPS)
    full_fim = int(clear_at * FPS)
    finals = [s for s in captions if full_ini <= s["index"] < full_fim]
    assert finals, (f"estado final da frase não capturado (janela {full_ini}-{full_fim}, "
                    f"emitidos {[s['index'] for s in captions]})")
    assert any(s["words"] == "multi" for s in finals), "frase acumulada não marcada como multi"


# ─── Estilos de legenda ───────────────────────────────────────────────────────

def test_karaoke_cobre_todas_as_palavras():
    words = ["UM", "GRANDE", "TIME", "FAZ", "MUITO", "MAIS", "QUE", "TALENTO", "SOZINHO", "JUNTOS"]
    step = 0.3
    ev = [(i * step, (i + 1) * step, w) for i, w in enumerate(words)]
    stack = _stack(ev, duration=len(words) * step + 0.5)

    _, _, _, _, band = _detect(stack)
    assert band is not None
    states = _band_states(stack, band)
    captions = [s for s in states if s["kind"] == "caption"]
    assert len(captions) >= len(words), f"esperava >= {len(words)} estados, veio {len(captions)}"
    for i, w in enumerate(words):
        ini, fim = int(i * step * FPS), int((i + 1) * step * FPS)
        assert any(ini <= s["index"] < max(fim, ini + 1) for s in captions), \
            f"palavra '{w}' (frames {ini}-{fim}) sem estado emitido"
    assert all(s["words"] == "single" for s in captions), "karaokê tem que ser blocos de 1 palavra"


def test_legenda_no_topo_e_detectada():
    words = ["cinco", "palavras", "de", "teste", "inteligencia"]
    clear_at = 0.4 * (len(words) - 1) + 0.3
    stack = _stack(_accumulative_events(words, 0.4, clear_at),
                   duration=clear_at + 1.0, y_frac=0.22)

    _, _, _, _, band = _detect(stack)
    assert band is not None, "banda no topo não detectada"
    y0f, y1f, _ = band
    assert y0f <= 0.22 <= y1f, f"banda ({y0f:.2f}-{y1f:.2f}) não cobre a legenda no topo (0.22)"
    states = _band_states(stack, band)
    full_ini = int(0.4 * (len(words) - 1) * FPS)
    full_fim = int(clear_at * FPS)
    assert any(s["kind"] == "caption" and full_ini <= s["index"] < full_fim for s in states)


def test_video_sem_legenda_nao_acha_banda():
    stack = _moving_background(duration=5.0)
    _, _, _, _, band = _detect(stack)
    assert band is None, "movimento contínuo não pode virar banda de legenda"


def test_modo_degradado_gera_cobertura_uniforme():
    stack = _moving_background(duration=5.0)
    _, d_full, _, _, _ = _detect(stack)
    states = cs.degrade_states(d_full, reinforce_every=2 * FPS, min_gap=FPS // 2)
    assert states and all(s["kind"] == "scene" for s in states)
    idxs = [s["index"] for s in states]
    assert max(np.diff(idxs)) <= 2 * FPS + 1, "reforço uniforme deixou buraco > 2s"


# ─── Classificação acumula/reseta ─────────────────────────────────────────────

def test_classify_acumula_e_reseta():
    band = lambda txt: _thumb(txt)[int(0.72 * H) - 4:int(0.72 * H) + 30]
    # acúmulo claro: frase existente ganha palavra (com re-centralização)
    assert cs.classify_change(band("estamos formando o"),
                              band("estamos formando o grupo")) == "acumula"
    assert cs.classify_change(band("estamos"), band("estamos formando")) == "acumula"
    # trocas: karaokê e limpeza de frase
    assert cs.classify_change(band("ola"), band("tchau")) == "reseta"
    assert cs.classify_change(band("UM"), band("GRANDE")) == "reseta"   # menor -> maior
    assert cs.classify_change(band("ola mundo"), band("")) == "reseta"  # frase limpou
    # primeira palavra sobre o vazio abre o bloco acumulando
    assert cs.classify_change(band(""), band("ola")) == "acumula"
    # POLÍTICA: transição ambígua (pouquíssima tinta, 1-2 palavras curtas) vai pro
    # RESETA de propósito: custa um frame a mais, nunca uma palavra a menos.
    assert cs.classify_change(band("ola"), band("ola mundo")) == "reseta"


# ─── Custo e orçamento ────────────────────────────────────────────────────────

def test_estimate_cost_bate_com_a_conta_manual():
    dims = [(1440, 500), (720, 1280)]
    got = cs.estimate_cost(dims, overhead_in=1000, marker_tokens=20, read_out=500,
                           review_in=3000, review_out=500, price_in=3.0, price_out=15.0)
    img = (1440 * 500) // 750 + 1 + (720 * 1280) // 750 + 1
    manual = ((img + 1000 + 20 * 2 + 3000) * 3.0 + (500 + 500) * 15.0) / 1e6
    assert got == pytest.approx(manual)


def _fake_states(n_single: int, n_multi: int, n_scene: int):
    states, costs = [], []
    idx = 0
    for kind, words, n, cost in (("caption", "single", n_single, 0.003),
                                 ("caption", "multi", n_multi, 0.003),
                                 ("scene", None, n_scene, 0.005)):
        for _ in range(n):
            states.append({"index": idx, "kind": kind, "words": words})
            costs.append(cost)
            idx += 3
    order = sorted(range(len(states)), key=lambda i: states[i]["index"])
    return [states[i] for i in order], [costs[i] for i in order]


def test_apply_budget_corta_por_prioridade_e_loga_descartes():
    states, costs = _fake_states(150, 8, 6)
    kept, dropped = cs.apply_budget(states, costs, base_cost=0.02,
                                    max_cost=0.20, max_images=100)
    assert len(kept) + len(dropped) == len(states)
    assert 0.02 + sum(costs[i] for i in kept) <= 0.20 + 1e-9
    # frases multi-palavra nunca caem enquanto houver P3/P2 pra cortar
    multi = [i for i, s in enumerate(states) if s["words"] == "multi"]
    assert all(i in kept for i in multi)
    # só singles caíram (o corte nem precisou chegar nas cenas)
    assert all(states[i]["words"] == "single" for i in dropped)
    assert kept == sorted(kept)
    # determinístico
    assert cs.apply_budget(states, costs, base_cost=0.02, max_cost=0.20,
                           max_images=100) == (kept, dropped)


def test_apply_budget_respeita_teto_de_imagens():
    states, costs = _fake_states(80, 5, 4)
    kept, _ = cs.apply_budget(states, costs, base_cost=0.0,
                              max_cost=99.0, max_images=20)
    assert len(kept) <= 20
    multi = [i for i, s in enumerate(states) if s["words"] == "multi"]
    assert all(i in kept for i in multi), "teto de imagens não pode derrubar frases completas"
