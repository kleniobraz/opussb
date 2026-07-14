"""Lógica PURA de reframe 9:16, compartilhada entre a VPS (reframer.py, codec libx264) e o worker
GPU (gpu_reframe/handler.py, codec h264_nvenc). Sem import de `config`: tudo entra por argumento,
pra a mesma matemática de câmera rodar IDÊNTICA nos dois lugares (evita o enquadramento divergir).

Depende só de cv2/numpy/ffmpeg (NÃO importa ultralytics: o modelo YOLO entra como argumento, cada
ambiente carrega o seu)."""
import os
import shutil
import subprocess
import tempfile
import cv2
import numpy as np

# Rede de proteção contra ffmpeg travado no encode final (clipe curto; folga larga).
_FFMPEG_TIMEOUT = 1200


def filter_escape(path: str) -> str:
    """Escapa um caminho para uso dentro de um filtergraph do ffmpeg (subtitles=...)."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def detect_people(frame: np.ndarray, model, width: int, min_conf: float):
    """Todas as pessoas (>= min_conf) do frame, como (conf, cx, box_w) em coords da resolução
    ORIGINAL, ORDENADAS por cx (esquerda -> direita). Base da detecção por GEOMETRIA: o chamador
    usa a CONTAGEM e a POSIÇÃO pra saber se é close (1), plano aberto (2+) ou vazio (0), e qual lado."""
    small = cv2.resize(frame, (640, 360))
    scale_x = width / 640
    people = []
    for box in model(small, classes=[0], verbose=False)[0].boxes:
        conf = float(box.conf[0])
        if conf < min_conf:
            continue
        x1 = float(box.xyxy[0][0]) * scale_x
        x2 = float(box.xyxy[0][2]) * scale_x
        people.append((conf, (x1 + x2) / 2.0, x2 - x1))
    people.sort(key=lambda p: p[1])
    return people


def moving_avg(arr, window: int):
    """Média móvel com bordas replicadas (não puxa pro centro). Polimento da trajetória."""
    a = np.asarray(arr, dtype=float)
    if window <= 1 or a.size == 0:
        return a
    window = min(window, a.size)
    pad = window // 2
    padded = np.pad(a, pad, mode="edge")
    sm = np.convolve(padded, np.ones(window) / window, mode="valid")
    return sm[:a.size]


# Caixas cujos centros estão dentro desta fração da largura são a MESMA pessoa (o YOLO duplica/divide
# o box) -> fundimos, senão a contagem oscila (2/3/4) e quebra a classificação de plano.
_MERGE_FRAC = 0.10
# Amostras CONSECUTIVAS que um novo tipo de quadro precisa persistir pra contar como troca de câmera
# (debounce). 3 absorve o YOLO piscando por ~0,5s (ex.: convidado some 1-2 amostras no plano aberto).
_CUT_DEBOUNCE = 3

# Troca de câmera por DIFF DE QUADRO INTEIRO (pega o que a geometria não vê: dois planos do MESMO
# kind, ex. close-host -> close-convidado, onde a contagem de pessoas não muda). Mesma receita do
# caption_sampler.py (mesmo projeto, já validado em produção pro Revisor de legendas): reduz a um
# thumbnail cinza pequeno ANTES de comparar, diff médio normalizado 0..1.
_DIFF_WIDTH = 180
# Calibrado com tools/eval_reframe.py num multicam real (Carbone/Clubcast, 322s, 60fps, 3 câmeras
# mesmo estúdio): os 21 cortes reais do vídeo mediram entre 0.187 e 0.211; o maior valor de "chão"
# (sem corte, incluindo gesticulação normal) foi 0.0445. Margem de mais de 4x sem sobreposição
# nenhuma entre as duas distribuições. 0.09 fica quase no meio dessa folga. Recalibrar (mesmo
# script) se um vídeo de conteúdo bem diferente (iluminação, distância de câmera) começar a
# escapar ou dar falso positivo.
_CUT_DIFF_THRESHOLD = 0.09
# Corte real é um PICO isolado no diff (a amostra seguinte já compara pós-corte com pós-corte), não
# um sinal que precisa persistir feito o debounce acima. min_gap só evita contar o MESMO corte 2x se
# ele vazar pra amostra adjacente numa transição mais lenta que o gap (unidade: FRAMES, não amostras;
# comparável a sample_every).
_CUT_DIFF_MIN_GAP_FRAMES = 15


def _merge_boxes(people: list, width: int) -> list:
    """Funde detecções sobrepostas (a MESMA pessoa contada 2x pelo YOLO): centros mais próximos que
    _MERGE_FRAC*largura viram uma só (fica a de maior confiança). `people` já vem ordenado por cx."""
    if not people:
        return people
    thr = _MERGE_FRAC * width
    merged = [people[0]]
    for p in people[1:]:
        if p[1] - merged[-1][1] < thr:        # mesma pessoa
            if p[0] > merged[-1][0]:          # mantém a de maior confiança
                merged[-1] = p
        else:
            merged.append(p)
    return merged


def _classify(people: list, width: int):
    """Enquadramento de UM sample por GEOMETRIA (layout fixo do podcast: host=esquerda, convidado=
    direita). `people` já DEDUPLICADO e ordenado por cx. Retorna (kind, subject_cx):
      0 pessoas -> ('none', None): a trajetória cai pro CENTRO.
      1 pessoa  -> ('close', cx): CLOSE; enquadra nela.
      2+ pessoas-> ('wide', cx_do_convidado): PLANO ABERTO; default = CONVIDADO (mais à direita). Se o
                   mais à direita NÃO está na metade direita (convidado não detectado neste frame),
                   ('wide', None) -> a trajetória SEGURA o sujeito (não pula pro host/vazio). O keyframe
                   (Fase 1) é o override pra escolher o host."""
    if not people:
        return "none", None
    if len(people) == 1:
        return "close", people[0][1]
    guest_cx = people[-1][1]                  # mais à direita = convidado
    if guest_cx < width / 2.0:                # convidado não visível neste frame -> segura
        return "wide", None
    return "wide", guest_cx


def _cuts_from_kinds(kinds: list, debounce: int = _CUT_DEBOUNCE) -> list:
    """Fronteiras de plano (frames) a partir da sequência [(frame, kind)], com DEBOUNCE: um novo tipo
    de quadro só vira troca de câmera se persistir por `debounce` amostras seguidas (absorve o YOLO
    piscando). A fronteira é o PRIMEIRO frame do novo plano. O primeiro plano não gera corte."""
    cuts = []
    stable = cand = cand_start = None
    cand_n = 0
    for f, kind in kinds:
        if stable is None:
            stable = kind
        elif kind != stable:
            if kind == cand:
                cand_n += 1
            else:
                cand, cand_n, cand_start = kind, 1, f
            if cand_n >= debounce:
                cuts.append(cand_start)
                stable, cand, cand_n = kind, None, 0
        else:
            cand, cand_n = None, 0
    return cuts


def _small_gray(frame: np.ndarray, width: int = _DIFF_WIDTH) -> np.ndarray:
    """Downscale + cinza pra diff barato (mesma receita do caption_sampler.py). Reduz ANTES de
    converter pra cinza, senão o cvtColor roda em cima da resolução original à toa."""
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, max(1, round(h * width / w))))
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def _frame_diff(prev_small: np.ndarray, cur_small: np.ndarray) -> float:
    """Diff médio normalizado 0..1 entre dois quadros já reduzidos por `_small_gray` (mesma métrica
    do caption_sampler.scan_profile: d_full = mean(abs(a - b)) / 255)."""
    return float(np.abs(cur_small - prev_small).mean()) / 255.0


def _cuts_from_diff(diffs: list, threshold: float = _CUT_DIFF_THRESHOLD,
                    min_gap: int = _CUT_DIFF_MIN_GAP_FRAMES) -> list:
    """Fronteiras de plano a partir do diff de quadro inteiro entre amostras consecutivas
    (`diffs` = [(frame, d)], frame = frame da amostra PÓS-corte, d = `_frame_diff` contra a amostra
    anterior). Sem debounce de persistência (isso é pra sinal de NÍVEL, tipo o kind do YOLO
    piscando): um corte real é um PICO isolado, a amostra seguinte já compara pós-corte com
    pós-corte. `min_gap` só evita contar o MESMO corte 2x se ele vazar pra amostra adjacente."""
    cuts = []
    last = -min_gap
    for frame, d in diffs:
        if d > threshold and frame - last > min_gap:
            cuts.append(frame)
            last = frame
    return cuts


def _merge_cuts(kind_cuts: list, diff_cuts: list, min_gap: int) -> list:
    """União dos cortes por tipo (`_cuts_from_kinds`) e por diff (`_cuts_from_diff`): qualquer um dos
    dois confirma o corte (nunca os dois precisam concordar, senão perde exatamente o caso que o diff
    foi criado pra cobrir: kind igual dos dois lados). Dedup por PROXIMIDADE (não igualdade exata): as
    duas fontes vendo o MESMO corte perto do mesmo frame vira uma fronteira só."""
    all_cuts = sorted(set(kind_cuts) | set(diff_cuts))
    merged = []
    for c in all_cuts:
        if not merged or c - merged[-1] > min_gap:
            merged.append(c)
    return merged


def _motion_bias_wide(prev_frame, frame, width: int):
    """TODO (Fase 2, opcional): no PLANO ABERTO, medir movimento (diff de pixels) na metade ESQUERDA
    vs DIREITA e favorecer o lado mais ativo (provável quem fala/gesticula). Heurística barata, sem
    áudio nem ML; gesto != fala, então é só um palpite. NÃO implementar ainda: o keyframe manual
    (Fase 1) já cobre a escolha com certeza. Mantido como ponto de extensão."""
    raise NotImplementedError("movimento esquerda/direita no plano aberto: TODO Fase 2")


def _locked_segment(samples: list, total_frames: int, fps: float,
                    width: int, crop_w: int, center_x: int,
                    deadzone_frac: float, max_pan_frac: float) -> list:
    """Câmera travada de UM plano contínuo (sem corte no meio). A partir das amostras do centro do
    sujeito (None = sem detecção -> segura o último), produz o crop_x por frame com ZONA MORTA
    (deadzone_frac: dentro dela não mexe) + PAN LIMITADO (max_pan_frac por segundo: anda devagar, sem
    salto). Plano sem nenhum sujeito -> corte central fixo."""
    if total_frames <= 0:
        return []
    valid = [(f, cx) for f, cx in samples if cx is not None]
    if not valid:
        return [center_x] * total_frames

    # Centro desejado do sujeito por frame: segura o último válido (sem pular pro centro).
    desired = np.empty(total_frames, dtype=float)
    vi, cur = 0, valid[0][1]
    for f in range(total_frames):
        while vi < len(valid) and valid[vi][0] <= f:
            cur = valid[vi][1]
            vi += 1
        desired[f] = cur

    half_dz = deadzone_frac * crop_w / 2.0
    max_step = max(1.0, max_pan_frac * crop_w / max(fps, 1.0))
    half_w = crop_w / 2.0
    lo, hi = half_w, max(half_w, width - half_w)
    cc = min(max(desired[0], lo), hi)
    out = np.empty(total_frames, dtype=float)
    for f in range(total_frames):
        s = desired[f]
        if s - cc > half_dz:
            target = s - half_dz
        elif cc - s > half_dz:
            target = s + half_dz
        else:
            target = cc
        delta = target - cc
        if delta > max_step:
            delta = max_step
        elif delta < -max_step:
            delta = -max_step
        cc = min(max(cc + delta, lo), hi)
        out[f] = cc - half_w

    out = moving_avg(out, max(3, int(round(fps / 3))))
    return [int(round(v)) for v in out]


def build_locked_trajectory(samples: list, total_frames: int, fps: float,
                            width: int, crop_w: int, center_x: int,
                            deadzone_frac: float, max_pan_frac: float,
                            cuts: list | None = None) -> list:
    """Câmera virtual TRAVADA, ciente de CORTES DE CENA. Sem `cuts`, é um plano só (comportamento de
    sempre). Com `cuts` (frames onde começa um novo plano), trata cada plano como uma mini-trajetória
    INDEPENDENTE: reseta a câmera no corte (o pan não atravessa o corte arrastando do plano A pro B) e
    um plano sem sujeito vira corte CENTRAL daquele plano, em vez de segurar a posição do plano
    anterior (que é o 'cair no vazio' do multicam)."""
    if total_frames <= 0:
        return []
    bounds = sorted({c for c in (cuts or []) if 0 < c < total_frames})
    if not bounds:
        return _locked_segment(samples, total_frames, fps, width, crop_w, center_x,
                               deadzone_frac, max_pan_frac)
    edges = [0] + bounds + [total_frames]
    out = []
    for i in range(len(edges) - 1):
        start, end = edges[i], edges[i + 1]
        seg = [(f - start, cx) for (f, cx) in samples if start <= f < end]
        out.extend(_locked_segment(seg, end - start, fps, width, crop_w, center_x,
                                   deadzone_frac, max_pan_frac))
    return out


def sample_camera(cap, model, width: int, *, min_conf: float, detect_cuts: bool = False,
                  sample_every: int = 15, max_frame: int | None = None) -> dict:
    """Passada 1 sobre um cv2.VideoCapture JÁ ABERTO (não abre nem fecha; o chamador decide o ciclo
    de vida). Amostra a cada `sample_every` frames: grab() nos pulados (sem converter BGR),
    retrieve() só nos amostrados. Detecção por GEOMETRIA (NÃO por cor: as 3 câmeras são do mesmo
    cenário): após FUNDIR caixas duplicadas (_merge_boxes), a CONTAGEM diz close (1, enquadra nela) /
    plano aberto (2+, default = convidado à direita, segurando quando ele some um frame) / vazio (0,
    cai pro centro). As trocas de câmera saem da UNIÃO de duas fontes (_merge_cuts): o histórico de
    tipos com DEBOUNCE (_cuts_from_kinds, pra não picar no ruído do YOLO) e o diff de quadro inteiro
    (_cuts_from_diff, pega troca de câmera que a contagem não vê: dois planos do MESMO kind).
    detect_cuts=False desliga as duas (cuts sempre []). `max_frame` corta a varredura cedo (a
    ferramenta de avaliação usa isso pra iterar rápido num trecho, sem decodificar o vídeo inteiro);
    None (produção) varre até o fim. `frame_idx` começa da posição ATUAL do cap, então também
    funciona se o chamador já deu seek. Extraída de reframe_to_file pra ser reaproveitada por
    tools/eval_reframe.py sem duplicar a lógica de amostragem."""
    samples = []
    kinds = []
    diffs = []
    prev_gray = None
    frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    while max_frame is None or frame_idx < max_frame:
        if not cap.grab():
            break
        if frame_idx % sample_every == 0:
            ok, frame = cap.retrieve()
            if ok:
                people = _merge_boxes(detect_people(frame, model, width, min_conf), width)
                kind, subject_cx = _classify(people, width)
                kinds.append((frame_idx, kind))
                samples.append((frame_idx, subject_cx))
                if detect_cuts:
                    gray = _small_gray(frame)
                    if prev_gray is not None:
                        diffs.append((frame_idx, _frame_diff(prev_gray, gray)))
                    prev_gray = gray
        frame_idx += 1

    if detect_cuts:
        cuts_kind = _cuts_from_kinds(kinds)
        cuts_diff = _cuts_from_diff(diffs)
        cuts = _merge_cuts(cuts_kind, cuts_diff, sample_every)
    else:
        cuts_kind, cuts_diff, cuts = [], [], []
    return {"samples": samples, "kinds": kinds, "diffs": diffs,
            "cuts_kind": cuts_kind, "cuts_diff": cuts_diff, "cuts": cuts}


def reframe_to_file(input_path: str, output_path: str, model, *,
                    deadzone_frac: float, max_pan_frac: float, min_conf: float,
                    video_codec_args: list, subtitles: str | None = None,
                    detect_cuts: bool = False) -> dict | None:
    """Reenquadra pra 9:16 (1080x1920) com câmera travada. `model` é um YOLO já carregado;
    `video_codec_args` é a lista de args de vídeo do ffmpeg (ex.: libx264 veryfast na VPS, h264_nvenc
    na GPU). Se já-9:16, padroniza/copia. Levanta em falha (o chamador decide o fallback). Devolve a
    trajetória calculada {per_frame_cx, fps, width, crop_w, cuts} (ou None se já-9:16/copiado), pro
    auto-semeio de keyframes do crop (BACKEND §33)."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Já 9:16 (ou mais vertical): sem reframe. Sem legenda, copia; com legenda, padroniza e queima.
    if width > 0 and height / width >= 16 / 9 - 0.05:
        cap.release()
        if subtitles:
            _encode_vertical_with_subs(input_path, output_path, subtitles, video_codec_args)
        else:
            shutil.copy2(input_path, output_path)
        return None   # já vertical: sem pan horizontal possível, crop por keyframe não se aplica

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    center_x = (width - crop_w) // 2

    cam = sample_camera(cap, model, width, min_conf=min_conf, detect_cuts=detect_cuts)
    cap.release()

    cuts = cam["cuts"]
    per_frame_cx = build_locked_trajectory(cam["samples"], total_frames, fps, width, crop_w,
                                           center_x, deadzone_frac, max_pan_frac, cuts=cuts)
    _pipe_encode(input_path, output_path, per_frame_cx, fps, width, crop_w, center_x,
                 video_codec_args, subtitles)
    return {"per_frame_cx": per_frame_cx, "fps": fps, "width": width, "crop_w": crop_w, "cuts": cuts}


def keyframes_from_trajectory(per_frame_cx: list, fps: float, width: int, crop_w: int,
                              cuts: list | None = None, interval_s: float = 0.75) -> dict | None:
    """Reduz a trajetória auto (per_frame_cx = BORDA ESQUERDA do recorte em px) a uma lista ESPARSA de
    keyframes {t (s), cx (CENTRO horizontal, fração 0..1 da largura)}, pra semear o modo manual do
    editor de crop (BACKEND §33). Amostra a cada interval_s + ancora nos cortes de câmera (degrau
    quase instantâneo: frame anterior + frame do corte). cx em fração = resolução-independente. Devolve
    {"mode":"auto","kf":[...]} ou None se a trajetória for vazia/inválida."""
    n = len(per_frame_cx or [])
    if n == 0 or width <= 0 or crop_w <= 0:
        return None
    fps = fps or 30.0
    half = crop_w / 2.0

    def cx_frac(f):
        f = max(0, min(f, n - 1))
        return round((per_frame_cx[f] + half) / width, 4)

    idxs = set(range(0, n, max(1, int(round(interval_s * fps)))))
    idxs.add(n - 1)
    for c in (cuts or []):
        if 0 < c < n:
            idxs.add(c - 1)   # último frame do plano anterior
            idxs.add(c)        # primeiro frame do novo plano (preserva o "degrau" do corte)
    kf = [{"t": round(f / fps, 3), "cx": cx_frac(f)} for f in sorted(idxs)]
    return {"mode": "auto", "kf": kf}


def trajectory_from_keyframes(kf_list: list, fps: float, total_frames: int,
                              width: int, crop_w: int) -> list:
    """Inverso de keyframes_from_trajectory: expande os keyframes esparsos {t (s), cx (CENTRO, fração
    0..1)} numa lista DENSA per_frame_cx (BORDA ESQUERDA do recorte em px), 1 valor por frame,
    interpolando LINEARMENTE entre keyframes e segurando as pontas. cx fora de [0, width-crop_w] é
    clampado (pan-only, sem zoom). Pura/testável. Lista vazia/invalida -> corte central fixo."""
    fps = fps or 30.0
    n = max(0, int(total_frames))
    half = crop_w / 2.0
    lo, hi = 0, max(0, width - crop_w)
    pts = []   # (frame, borda_esquerda_px), ja clampado
    for k in (kf_list or []):
        try:
            f = int(round(float(k["t"]) * fps))
            left = int(round(float(k["cx"]) * width - half))
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((f, max(lo, min(left, hi))))
    if not pts:
        return [(width - crop_w) // 2] * n
    pts.sort(key=lambda p: p[0])
    out = []
    j = 0
    for f in range(n):
        if f <= pts[0][0]:
            out.append(pts[0][1]); continue
        if f >= pts[-1][0]:
            out.append(pts[-1][1]); continue
        while j < len(pts) - 1 and pts[j + 1][0] < f:
            j += 1
        fa, va = pts[j]
        fb, vb = pts[j + 1]
        frac = 0.0 if fb == fa else (f - fa) / (fb - fa)
        out.append(int(round(va + (vb - va) * frac)))
    return out


def reframe_with_keyframes(input_path: str, output_path: str, kf_list: list,
                           video_codec_args: list, subtitles: str | None = None) -> None:
    """Re-render 9:16 (1080x1920) a partir dos keyframes do usuario (crop por keyframe, BACKEND §33),
    SEM YOLO: abre o 16:9 fonte so pra medir dims/fps/total_frames, interpola a trajetoria densa e
    recorta horizontalmente (mesmo _pipe_encode do auto). Levanta em falha (o chamador decide)."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    center_x = (width - crop_w) // 2
    per_frame_cx = trajectory_from_keyframes(kf_list, fps, total_frames, width, crop_w)
    _pipe_encode(input_path, output_path, per_frame_cx, fps, width, crop_w, center_x,
                 video_codec_args, subtitles)


def _pipe_encode(input_path: str, output_path: str, per_frame_cx: list, fps: float,
                 width: int, crop_w: int, center_x: int,
                 video_codec_args: list, subtitles: str | None = None) -> None:
    """Pass 2 em UMA passada: recorta cada frame e envia direto pro ffmpeg (rawvideo via stdin),
    que encoda (codec parametrizado) e junta o áudio original num único encode. stderr do ffmpeg vai
    pra ARQUIVO (não PIPE; PIPE não drenado travaria o ffmpeg em deadlock). BrokenPipe/timeout ->
    levanta (o chamador decide o fallback)."""
    out_w, out_h = 1080, 1920
    last_cx = per_frame_cx[-1] if per_frame_cx else center_x   # fora do alcance: segura a última
    vf = "setsar=1"
    if subtitles:
        vf += f",subtitles={filter_escape(subtitles)}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", f"{fps}", "-i", "-",
        "-i", input_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", vf,
        *video_codec_args, "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ]
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf)
    cap = cv2.VideoCapture(input_path)
    try:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            cx = per_frame_cx[frame_idx] if frame_idx < len(per_frame_cx) else last_cx
            cx = max(0, min(cx, width - crop_w))
            resized = cv2.resize(frame[:, cx:cx + crop_w], (out_w, out_h))
            proc.stdin.write(resized.tobytes())
            frame_idx += 1
        proc.stdin.close()
        try:
            proc.wait(timeout=_FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("reframe encode estourou o timeout")
    except (BrokenPipeError, OSError) as e:
        # ffmpeg morreu antes de drenar o pipe (ex.: NVENC nao inicializou). Da um tempinho pra ele
        # fechar o stderr e ANEXA o tail do log do ffmpeg, senao a causa real (args/NVENC/driver) se perde.
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        try:
            errf.seek(0)
            ff_tail = errf.read()[-800:].decode("utf-8", "replace")
        except Exception:
            ff_tail = "(stderr indisponivel)"
        raise RuntimeError(f"reframe pipe quebrou (ffmpeg morreu cedo): {e}; ffmpeg: {ff_tail}")
    finally:
        cap.release()
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
        errf.seek(0)
        tail = errf.read()[-500:].decode("utf-8", "replace")
        errf.close()
        raise RuntimeError(f"reframe encode falhou (rc={proc.returncode}): {tail}")
    errf.close()


def _encode_vertical_with_subs(input_path: str, output_path: str, subtitles: str,
                               video_codec_args: list) -> None:
    """Caso raro: clipe já é 9:16. Padroniza pra 1080x1920 e queima a legenda."""
    vf = f"scale=1080:1920,setsar=1,subtitles={filter_escape(subtitles)}"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
         *video_codec_args, "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", output_path],
        capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed: {result.stderr[-400:]}")
