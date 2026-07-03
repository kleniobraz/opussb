"""caption_sampler.py — detecção PURA de estados de legenda (o "onde e quando").

Princípio de arquitetura do revisor: DETECÇÃO separada de LEITURA. Este módulo é a
detecção: roda local na CPU, de graça, sobre a varredura densa do vídeo (thumbnails
em cinza), e decide QUAIS frames merecem leitura. A leitura (Sonnet, cara) fica no
caption_reviewer, que orquestra ffmpeg + este módulo + a chamada de visão.

Como o reframe_core: matemática pura, sem `config`, sem I/O de rede, tudo por
argumento. Entradas são arrays numpy (thumbnails 2D uint8); saídas são decisões
(banda, estados selecionados, orçamento). Isso mantém o módulo testável rápido e
compartilhável.

Conceitos:
- BANDA: a faixa horizontal do quadro onde a legenda vive. Detectada por "burstiness"
  temporal (texto queimado muda em degraus: parado quase sempre, salto quando a
  palavra troca; movimento de vídeo muda continuamente) combinada com densidade de
  bordas (assinatura de glifo). Sem banda confiável -> modo degradado (frame cheio).
- BLOCO: um estado de frase da legenda. Mudanças na banda são classificadas em
  ACUMULA (a frase ganhou palavra; máscara de tinta anterior preservada e tinta
  cresceu) ou RESETA (frase limpou/trocou, karaokê, ou corte de cena). Ao RESETAR,
  emitimos o ÂNCORA do bloco (o último estado assentado, que contém a frase inteira)
  — nunca o primeiro frame, que pega palavra em meio-de-animação.
- ORÇAMENTO: teto em US$ (não em frames). Prioridade de corte: P3 estados de 1
  palavra (karaokê) -> P2 quadros de cena (preserva primeiro/último) -> P1 frases
  multi-palavra (últimas a cair). Todo corte é devolvido pro chamador LOGAR.
"""
from __future__ import annotations

import heapq

import numpy as np

# ─── Varredura: perfis por linha e diffs de quadro ────────────────────────────


def scan_profile(thumbs):
    """Consome um iterável de thumbnails 2D uint8 (mesma forma) e devolve:
    - row_diffs [T-1, H] float32: diff médio absoluto por LINHA entre frames vizinhos (0..1)
    - d_full   [T-1]    float32: diff médio do quadro inteiro (0..1) — detector de cena
    - edge_profile [H]  float32: densidade média de bordas horizontais por linha (0..1)
    - n_frames  int
    Streaming: nunca materializa a pilha inteira (RAM ~O(1 frame))."""
    prev = None
    row_diffs: list[np.ndarray] = []
    d_full: list[float] = []
    edge_acc = None
    n = 0
    for arr in thumbs:
        a = np.asarray(arr, dtype=np.int16)
        e = (np.abs(np.diff(a, axis=1)).mean(axis=1) / 255.0)
        edge_acc = e if edge_acc is None else edge_acc + e
        if prev is not None:
            d = np.abs(a - prev) / 255.0
            row_diffs.append(d.mean(axis=1).astype(np.float32))
            d_full.append(float(d.mean()))
        prev = a
        n += 1
    if n < 2:
        return (np.zeros((0, prev.shape[0] if prev is not None else 0), dtype=np.float32),
                np.zeros(0, dtype=np.float32),
                np.zeros(prev.shape[0], dtype=np.float32) if prev is not None else np.zeros(0, np.float32),
                n)
    return (np.stack(row_diffs),
            np.asarray(d_full, dtype=np.float32),
            (edge_acc / n).astype(np.float32),
            n)


# ─── Banda dinâmica ───────────────────────────────────────────────────────────


def detect_band(row_diffs: np.ndarray, edge_profile: np.ndarray, *,
                d_full: np.ndarray | None = None, scene_threshold: float = 0.09,
                score_floor: float = 4.0, min_change: float = 0.02,
                min_h_frac: float = 0.05, max_h_frac: float = 0.30,
                pad_frac: float = 0.025):
    """Acha a faixa de linhas onde a legenda vive, ou None (-> modo degradado).

    Score por linha = burstiness (p99/mediana dos diffs temporais: texto muda em
    degrau, movimento muda contínuo) x peso de bordas. p99 em vez de p95 porque
    legenda lenta (frase a cada 3s) muda em <5% dos frames e o p95 a perderia.

    Cortes de cena são EXCLUÍDOS da estatística (via d_full): um único degrau global
    dá p99/mediana gigante a toda linha estática e o pico deixaria de ser a legenda.
    Devolve (y0_frac, y1_frac, peak_score) em frações da altura."""
    if d_full is not None and len(d_full) == row_diffs.shape[0]:
        row_diffs = row_diffs[np.asarray(d_full) <= scene_threshold]
    if row_diffs.shape[0] < 8:
        return None  # varredura curta demais pra estatística confiável
    H = row_diffs.shape[1]
    p99 = np.percentile(row_diffs, 99, axis=0)
    med = np.percentile(row_diffs, 50, axis=0)
    bursty = p99 / (med + 0.004)  # eps absorve ruído de sensor/JPEG em linhas paradas
    edge_n = edge_profile / (float(edge_profile.max()) + 1e-6)
    score = np.where(p99 >= min_change, bursty * (0.5 + edge_n), 0.0)

    win = max(3, int(H * min_h_frac))
    smooth = np.convolve(score, np.ones(win) / win, mode="same")
    peak = int(np.argmax(smooth))
    peak_score = float(smooth[peak])
    if peak_score < score_floor:
        return None

    # Expansão SIMÉTRICA e gulosa a partir do pico: cresce sempre pro lado com maior
    # score sustentado (uma expansão só pra cima roubaria a 2ª linha da legenda).
    keep = smooth >= 0.35 * peak_score
    y0 = y1 = peak
    max_h = max(win, int(H * max_h_frac))
    while (y1 - y0 + 1) < max_h:
        up_ok = y0 - 1 >= 0 and keep[y0 - 1]
        dn_ok = y1 + 1 < H and keep[y1 + 1]
        if not up_ok and not dn_ok:
            break
        if up_ok and (not dn_ok or smooth[y0 - 1] >= smooth[y1 + 1]):
            y0 -= 1
        else:
            y1 += 1
    pad = int(H * pad_frac)
    y0 = max(0, y0 - pad)
    y1 = min(H - 1, y1 + pad)
    if (y1 - y0 + 1) < win:  # garante altura mínima útil pro crop
        need = win - (y1 - y0 + 1)
        y0 = max(0, y0 - need // 2)
        y1 = min(H - 1, y0 + win - 1)
    return (y0 / H, (y1 + 1) / H, peak_score)


# ─── Tinta (máscara de texto) e classificação acumula/reseta ─────────────────


def ink_mask(band: np.ndarray, ink_delta: int = 40) -> np.ndarray:
    """Pixels "de texto": longe da mediana da banda (texto contrasta com o fundo)."""
    a = np.asarray(band, dtype=np.int16)
    return np.abs(a - int(np.median(a))) > ink_delta


def _best_shift(mp: np.ndarray, mc: np.ndarray, max_shift: int) -> int:
    """Deslocamento horizontal que melhor alinha as duas máscaras (correlação das
    projeções em X). Legenda CENTRALIZADA re-centra o texto a cada palavra nova; sem
    esse alinhamento todo acúmulo pareceria troca."""
    pp = mp.sum(axis=0).astype(np.float32)
    pc = mc.sum(axis=0).astype(np.float32)
    if pp.sum() == 0 or pc.sum() == 0:
        return 0
    corr = np.correlate(pc, pp, mode="full")
    center = len(pp) - 1
    lo = max(0, center - max_shift)
    hi = min(len(corr), center + max_shift + 1)
    return int(np.argmax(corr[lo:hi])) + lo - center


def classify_change(prev_band: np.ndarray, cur_band: np.ndarray, *,
                    preserve_floor: float = 0.85, ink_delta: int = 40,
                    min_ink: int = 8) -> str:
    """ACUMULA se a tinta anterior se preserva (a menos de re-centralização) e a total
    não encolheu (frase ganhando palavra); RESETA se a frase limpou/trocou. Na dúvida,
    RESETA: fechar bloco a mais só custa frames (o orçamento segura); fechar a menos
    perderia palavra. Banda anterior vazia conta como acumular sobre o vazio.

    Pisos calibrados em medição sintética: acúmulo real mede preserved 0.96-1.00 e
    added_inside 0.00-0.04; troca de palavra (karaokê) mede 0.71-0.75 e 0.24-0.35.
    Transições com pouquíssima tinta (1-2 palavras curtas) caem numa zona ambígua e
    vão pro RESETA de propósito."""
    mp = ink_mask(prev_band, ink_delta)
    mc = ink_mask(cur_band, ink_delta)
    ap, ac = int(mp.sum()), int(mc.sum())
    if ap < min_ink:
        return "acumula" if ac >= min_ink else "reseta"
    if ac < min_ink:
        return "reseta"
    shift = _best_shift(mp, mc, max_shift=mp.shape[1] // 3)
    mc_al = np.roll(mc, -shift, axis=1)
    preserved = (mp & mc_al).sum() / ap
    # Acúmulo de verdade anexa a palavra nova FORA da caixa da tinta antiga (à
    # direita/abaixo); dentro da caixa, quase nada muda. Troca de palavra (mesmo por
    # uma maior que "engole" a antiga e mantém preserved alto) INVADE a caixa antiga
    # com tinta nova -> reseta.
    rows = np.flatnonzero(mp.any(axis=1))
    cols = np.flatnonzero(mp.any(axis=0))
    sub_p = mp[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    sub_c = mc_al[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    added_inside = int(sub_c.sum()) - int((sub_p & sub_c).sum())
    if preserved >= preserve_floor and added_inside <= 0.15 * ap and ac >= ap * 0.90:
        return "acumula"
    return "reseta"


def word_groups(mask: np.ndarray, gap_frac: float = 0.02) -> int:
    """Nº aproximado de grupos de palavras: runs de colunas com tinta separados por
    vãos. Aproximação (linhas múltiplas borram os vãos), usada só pra PRIORIDADE."""
    cols = mask.any(axis=0)
    if not cols.any():
        return 0
    gap = max(2, int(len(cols) * gap_frac))
    groups, run_gap, inside = 0, gap, False
    for c in cols:
        if c:
            if not inside and run_gap >= gap:
                groups += 1
            inside, run_gap = True, 0
        else:
            inside = False
            run_gap += 1
    return groups


def x_coverage(mask: np.ndarray) -> float:
    """Fração da largura ocupada por tinta (frase larga vs palavra solta)."""
    cols = mask.any(axis=0)
    return float(cols.mean())


# ─── Segmentação em blocos e seleção de estados ───────────────────────────────


def segment_states(band_stack: np.ndarray, d_full: np.ndarray, *,
                   band_threshold: float = 0.008, scene_threshold: float = 0.09,
                   preserve_floor: float = 0.85, ink_delta: int = 40,
                   min_ink_frac: float = 0.002, min_scene_gap: int = 4) -> list[dict]:
    """Percorre a banda no tempo e devolve os estados a LER, em ordem:
    {"index": i, "kind": "caption"|"scene", "words": "multi"|"single"|None}.

    A comparação é sempre contra o ÂNCORA do bloco (último estado assentado), não
    contra o frame vizinho: mudança gradual (fade) acumula até cruzar o threshold em
    vez de escorregar por baixo dele. Ao fechar um bloco emitimos o âncora, que é o
    estado com a frase completa (palavras novas viram eventos ACUMULA que movem o
    âncora pra frente)."""
    T = int(band_stack.shape[0])
    if T == 0:
        return []
    area = int(band_stack.shape[1]) * int(band_stack.shape[2])
    min_ink = max(8, int(area * min_ink_frac))
    states: list[dict] = []
    anchor = 0
    accum = 0
    last_scene = -10**9
    last_caption = -1

    def close_block(last_idx: int):
        nonlocal last_caption
        # Bloco que acumulou emite o ÂNCORA (último estado assentado da frase; frames
        # depois dele podem ser fade-out). Bloco sem acúmulo (karaokê) emite o último
        # frame ESTÁVEL antes do evento: o âncora seria o 1º frame do estado, que em
        # animação de pop pega a palavra em meio-de-animação.
        emit = anchor if accum >= 1 else max(anchor, last_idx)
        if emit == last_caption:
            return
        m = ink_mask(band_stack[emit], ink_delta)
        if int(m.sum()) >= min_ink:
            # "multi" (frase completa, protegida no corte de orçamento) decide pelo
            # CONTEÚDO do estado final, não só pela história do bloco: legenda
            # centralizada re-centra a cada palavra e pode fatiar o acúmulo em blocos.
            multi = accum >= 1 or word_groups(m) >= 3 or x_coverage(m) >= 0.55
            states.append({"index": emit, "kind": "caption",
                           "words": "multi" if multi else "single"})
            last_caption = emit

    anchor_arr = band_stack[0].astype(np.int16)
    for k in range(T - 1):
        cur = k + 1
        if float(d_full[k]) > scene_threshold:
            close_block(cur - 1)
            if cur - last_scene >= min_scene_gap:
                states.append({"index": cur, "kind": "scene", "words": None})
                last_scene = cur
            anchor = cur
            anchor_arr = band_stack[cur].astype(np.int16)
            accum = 0
            continue
        d_anchor = float(np.abs(band_stack[cur].astype(np.int16) - anchor_arr).mean()) / 255.0
        if d_anchor > band_threshold:
            had_ink = int(ink_mask(band_stack[anchor], ink_delta).sum()) >= min_ink
            cls = classify_change(band_stack[anchor], band_stack[cur],
                                  preserve_floor=preserve_floor,
                                  ink_delta=ink_delta, min_ink=min_ink)
            if cls == "acumula":
                if had_ink:
                    accum += 1  # primeira palavra sobre banda vazia não conta como acúmulo
                anchor = cur
                anchor_arr = band_stack[cur].astype(np.int16)
            else:
                close_block(cur - 1)
                anchor = cur
                anchor_arr = band_stack[cur].astype(np.int16)
                accum = 0
    close_block(T - 1)
    states.sort(key=lambda s: (s["index"], 0 if s["kind"] == "caption" else 1))
    return states


def degrade_states(d_full: np.ndarray, *, scene_threshold: float = 0.09,
                   event_factor: float = 1 / 3, reinforce_every: int = 16,
                   min_gap: int = 4) -> list[dict]:
    """Modo degradado (banda não detectada): eventos de mudança do quadro inteiro com
    threshold mais sensível + reforço uniforme no tempo. Tudo frame cheio ('scene');
    o orçamento controla o custo."""
    idxs: set[int] = set()
    last = -10**9
    thr = scene_threshold * event_factor
    for k, d in enumerate(np.asarray(d_full, dtype=np.float32)):
        cur = k + 1
        if float(d) > thr and cur - last >= min_gap:
            idxs.add(cur)
            last = cur
    for i in range(0, len(d_full) + 1, max(1, reinforce_every)):
        idxs.add(i)
    return [{"index": i, "kind": "scene", "words": None} for i in sorted(idxs)]


# ─── Custo e orçamento ────────────────────────────────────────────────────────


def image_tokens(w: int, h: int) -> int:
    """Tokens de imagem da Messages API: ~pixels/750."""
    return int(w * h) // 750 + 1


def estimate_cost(image_dims: list[tuple[int, int]], *, overhead_in: int,
                  marker_tokens: int, read_out: int, review_in: int,
                  review_out: int, price_in: float, price_out: float) -> float:
    """Estimativa em US$ da revisão inteira (chamada de leitura + reserva da revisão),
    na MESMA tabela de preço do custo logado, pra teto e relatório falarem a mesma
    língua."""
    in_tok = sum(image_tokens(w, h) for w, h in image_dims)
    in_tok += overhead_in + marker_tokens * len(image_dims) + review_in
    out_tok = read_out + review_out
    return (in_tok * price_in + out_tok * price_out) / 1_000_000.0


def apply_budget(states: list[dict], img_costs: list[float], *, base_cost: float,
                 max_cost: float, max_images: int) -> tuple[list[int], list[int]]:
    """Corta estados até caber no teto de custo E no teto de imagens da API.

    Prioridade de corte (do primeiro ao último a cair):
      P3: legendas de 1 palavra (karaokê) — subamostradas uniformemente no tempo;
      P2: quadros de cena — preserva o primeiro e o último;
      P1: frases multi-palavra — só caem se nada mais sobrar, e uniformemente.
    Determinístico e O(n log n) (heap com invalidação preguiçosa: vídeo patológico
    gera milhares de estados e o corte não pode segurar o GIL por segundos).
    Devolve (índices mantidos, índices descartados), ambos em ordem, pro chamador
    logar cada descarte."""
    max_images = max(1, int(max_images))  # misconfig de env não pode dividir por zero
    kept = set(range(len(states)))

    def over() -> bool:
        return (base_cost + sum(img_costs[i] for i in kept) > max_cost
                or len(kept) > max_images)

    def thin(cls_pred, protect_ends: bool, min_keep: int):
        if not over():
            return
        cls = sorted((i for i in kept if cls_pred(states[i])),
                     key=lambda i: states[i]["index"])
        if not cls:
            return
        protected = set(cls if len(cls) <= 2 else [cls[0], cls[-1]]) if protect_ends else set()
        alive = set(cls)
        prev: dict[int, int] = {}
        nxt: dict[int, int] = {}
        for a, b in zip(cls, cls[1:]):
            nxt[a] = b
            prev[b] = a

        def gap(j: int) -> int:
            g = 10**9
            if j in prev:
                g = min(g, states[j]["index"] - states[prev[j]]["index"])
            if j in nxt:
                g = min(g, states[nxt[j]]["index"] - states[j]["index"])
            return g

        heap = [(gap(j), j) for j in cls if j not in protected]
        heapq.heapify(heap)
        while over() and heap and (len(alive) - len(protected)) > min_keep:
            g, j = heapq.heappop(heap)
            if j not in alive or gap(j) != g:
                continue  # entrada obsoleta (vizinho removido mudou o vão)
            alive.discard(j)
            kept.discard(j)
            p, x = prev.pop(j, None), nxt.pop(j, None)
            if p is not None and x is not None:
                nxt[p], prev[x] = x, p
            elif p is not None:
                nxt.pop(p, None)
            elif x is not None:
                prev.pop(x, None)
            for nb in (p, x):
                if nb is not None and nb in alive and nb not in protected:
                    heapq.heappush(heap, (gap(nb), nb))

    thin(lambda s: s["kind"] == "caption" and s["words"] == "single",
         protect_ends=False, min_keep=0)
    thin(lambda s: s["kind"] == "scene", protect_ends=True, min_keep=2)
    thin(lambda s: s["kind"] == "caption" and s["words"] == "multi",
         protect_ends=False, min_keep=1)
    # Trava final: o teto de imagens é limite duro da API. Só engata quando os PISOS
    # dos thins (cena>=2, multi>=1) somam mais que max_images. Reconstrói DO ZERO por
    # prioridade (frases completas primeiro) a partir de TODOS os estados: os thins
    # podem ter derrubado multi pra respeitar piso de cena, e aqui a prioridade manda.
    if len(kept) > max_images:
        preds = (lambda s: s["kind"] == "caption" and s["words"] == "multi",
                 lambda s: s["kind"] == "scene",
                 lambda s: s["kind"] == "caption" and s["words"] == "single")
        new_kept: set[int] = set()
        for pred in preds:
            slots = max_images - len(new_kept)
            if slots <= 0:
                break
            group = sorted((i for i in range(len(states)) if pred(states[i])),
                           key=lambda i: states[i]["index"])
            if len(group) <= slots:
                new_kept.update(group)
            else:
                step = len(group) / slots
                new_kept.update(group[int(i * step)] for i in range(slots))
        # reforça o teto de CUSTO no conjunto reconstruído (canto raro; determinístico)
        for pred in reversed(preds):
            grp = sorted((i for i in new_kept if pred(states[i])),
                         key=lambda i: states[i]["index"])
            while grp and len(new_kept) > 1 and \
                    base_cost + sum(img_costs[i] for i in new_kept) > max_cost:
                new_kept.discard(grp.pop(len(grp) // 2))
        kept = new_kept
    kept_sorted = sorted(kept, key=lambda i: states[i]["index"])
    dropped = sorted(set(range(len(states))) - kept, key=lambda i: states[i]["index"])
    return kept_sorted, dropped
