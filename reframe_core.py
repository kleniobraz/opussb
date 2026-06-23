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


def reframe_to_file(input_path: str, output_path: str, model, *,
                    deadzone_frac: float, max_pan_frac: float, min_conf: float,
                    video_codec_args: list, subtitles: str | None = None,
                    detect_cuts: bool = False) -> None:
    """Reenquadra pra 9:16 (1080x1920) com câmera travada. `model` é um YOLO já carregado;
    `video_codec_args` é a lista de args de vídeo do ffmpeg (ex.: libx264 veryfast na VPS, h264_nvenc
    na GPU). Se já-9:16, padroniza/copia. Levanta em falha (o chamador decide o fallback)."""
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
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    center_x = (width - crop_w) // 2

    # Pass 1: amostra a cada N frames. grab() nos pulados (sem converter BGR), retrieve() só nos
    # amostrados. Detecção por GEOMETRIA (NÃO por cor: as 3 câmeras são do mesmo cenário): após FUNDIR
    # caixas duplicadas (_merge_boxes), a CONTAGEM diz close (1, enquadra nela) / plano aberto (2+,
    # default = convidado à direita, segurando quando ele some um frame) / vazio (0, cai pro centro).
    # As trocas de câmera saem do histórico de tipos com DEBOUNCE (_cuts_from_kinds), pra não picar no
    # ruído do YOLO. detect_cuts=False desliga a segmentação (volta a um plano só).
    sample_every = 15
    samples = []
    kinds = []
    frame_idx = 0
    while True:
        if not cap.grab():
            break
        if frame_idx % sample_every == 0:
            ok, frame = cap.retrieve()
            if ok:
                people = _merge_boxes(detect_people(frame, model, width, min_conf), width)
                kind, subject_cx = _classify(people, width)
                kinds.append((frame_idx, kind))
                samples.append((frame_idx, subject_cx))
        frame_idx += 1
    cap.release()

    cuts = _cuts_from_kinds(kinds) if detect_cuts else []
    per_frame_cx = build_locked_trajectory(samples, total_frames, fps, width, crop_w, center_x,
                                           deadzone_frac, max_pan_frac, cuts=cuts)
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
        proc.kill()
        raise RuntimeError(f"reframe pipe quebrou (ffmpeg morreu cedo): {e}")
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
