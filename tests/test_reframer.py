import sys
import types
import unittest.mock as _mock

# Stub out ultralytics and cv2 before reframe_core/reframer are imported so there are no
# hard dependency errors in environments that don't have them installed.
if "ultralytics" not in sys.modules:
    _ultra_mod = types.ModuleType("ultralytics")
    _ultra_mod.YOLO = _mock.MagicMock()
    sys.modules["ultralytics"] = _ultra_mod

if "cv2" not in sys.modules:
    _cv2_mod = types.ModuleType("cv2")
    # Numeric constants matching real OpenCV values
    _cv2_mod.CAP_PROP_FRAME_WIDTH = 3
    _cv2_mod.CAP_PROP_FRAME_HEIGHT = 4
    _cv2_mod.CAP_PROP_FPS = 5
    _cv2_mod.CAP_PROP_FRAME_COUNT = 7
    _cv2_mod.VideoCapture = _mock.MagicMock()
    _cv2_mod.resize = _mock.MagicMock()
    sys.modules["cv2"] = _cv2_mod

import numpy as np
import pytest
import reframe_core
import reframer


# Constantes cv2 (stub) via o módulo que reframe_core importou
_W = reframe_core.cv2.CAP_PROP_FRAME_WIDTH    # 3
_H = reframe_core.cv2.CAP_PROP_FRAME_HEIGHT   # 4
_FPS = reframe_core.cv2.CAP_PROP_FPS          # 5
_FC = reframe_core.cv2.CAP_PROP_FRAME_COUNT   # 7


# Test 1: reframe_clip full flow — encode único via pipe (sem mp4v intermediário)
def test_reframe_clip_fluxo_completo(mocker, tmp_path):
    fake_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    # Pass 1 cap: grab() (30x e depois False) + retrieve() nos amostrados.
    grab_calls = {"n": 0}

    def grab_p1():
        grab_calls["n"] += 1
        return grab_calls["n"] <= 30

    mock_cap1 = mocker.MagicMock()
    mock_cap1.isOpened.return_value = True
    mock_cap1.get.side_effect = lambda prop: {
        _W: 1920, _H: 1080, _FPS: 30.0, _FC: 30,
    }.get(prop, 0)
    mock_cap1.grab.side_effect = grab_p1
    mock_cap1.retrieve.return_value = (True, fake_frame)

    # Pass 2 cap (dentro de _pipe_encode): read() 30x e depois False.
    read_calls_p2 = {"n": 0}

    def read_p2():
        read_calls_p2["n"] += 1
        return (True, fake_frame) if read_calls_p2["n"] <= 30 else (False, None)

    mock_cap2 = mocker.MagicMock()
    mock_cap2.read.side_effect = read_p2

    call_count = {"n": 0}

    def vc_side_effect(path):
        call_count["n"] += 1
        return mock_cap1 if call_count["n"] == 1 else mock_cap2

    mock_vc = mocker.patch("reframe_core.cv2.VideoCapture", side_effect=vc_side_effect)

    # YOLO model (carregado por reframer._get_model) — sem detecções
    mock_results = mocker.MagicMock()
    mock_results[0].boxes = []
    mock_model = mocker.MagicMock(return_value=mock_results)
    mocker.patch("reframer._get_model", return_value=mock_model)

    mocker.patch("reframe_core.cv2.resize", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))

    # ffmpeg do encode único: Popen mockado + checagem de saída ok.
    mock_proc = mocker.MagicMock()
    mock_proc.wait.return_value = 0
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_popen = mocker.patch("reframe_core.subprocess.Popen", return_value=mock_proc)
    mocker.patch("reframe_core.tempfile.TemporaryFile", return_value=mocker.MagicMock())
    mocker.patch("reframe_core.os.path.exists", return_value=True)
    mocker.patch("reframe_core.os.path.getsize", return_value=2048)

    input_file = tmp_path / "clip.mp4"
    input_file.write_bytes(b"fake")

    reframer.reframe_clip(str(input_file), str(tmp_path / "out_9x16.mp4"))

    assert mock_vc.call_count == 2                  # passe 1 (detecção) + passe 2 (encode)
    assert mock_popen.called                        # encode único via pipe (não há mais VideoWriter)
    assert mock_proc.stdin.write.call_count == 30   # os 30 frames foram enviados pro ffmpeg


# Test 2: reframe_clip raises RuntimeError when video cannot be opened
def test_reframe_clip_levanta_se_nao_abre_video(mocker, tmp_path):
    mock_cap = mocker.MagicMock()
    mock_cap.isOpened.return_value = False
    mocker.patch("reframe_core.cv2.VideoCapture", return_value=mock_cap)
    mocker.patch("reframer._get_model", return_value=mocker.MagicMock())

    input_file = tmp_path / "missing.mp4"
    input_file.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="Cannot open video"):
        reframer.reframe_clip(str(input_file), str(tmp_path / "out.mp4"))


# Test 5: câmera travada — sujeito parado -> corte FIXO (não se mexe)
def test_trajetoria_travada_sujeito_parado():
    samples = [(0, 500), (15, 500), (30, 500)]
    out = reframe_core.build_locked_trajectory(samples, total_frames=45, fps=30.0,
                                               width=1920, crop_w=600, center_x=660,
                                               deadzone_frac=0.35, max_pan_frac=0.18)
    assert len(out) == 45
    assert all(isinstance(v, int) for v in out)
    # crop_x = centro_sujeito(500) - crop_w/2(300) = 200, constante (dentro da zona morta)
    assert all(v == 200 for v in out)


# Test 6: câmera travada — sujeito salta -> segue DEVAGAR (sem pulo pro alvo)
def test_trajetoria_travada_segue_devagar():
    samples = [(0, 500), (45, 1100)]   # parado em 500 e salta pra 1100 no frame 45
    out = reframe_core.build_locked_trajectory(samples, total_frames=75, fps=30.0,
                                               width=1920, crop_w=600, center_x=660,
                                               deadzone_frac=0.35, max_pan_frac=0.18)
    assert len(out) == 75
    assert out[0] == 200          # começa travado no sujeito
    assert out[-1] > 200          # já começou a acompanhar
    assert out[-1] < 500          # mas devagar: longe de ter pulado pro alvo


# Test 7: detect_people devolve todas as pessoas (conf, cx, w) ordenadas por cx (esq -> dir)
def test_detect_people_ordena_por_cx(mocker):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # direita (cx 960) mais confiante; esquerda (cx 120) menos. scale_x = 1920/640 = 3
    r = mocker.MagicMock(); r.conf = [0.9]; r.xyxy = [[300, 0, 340, 360]]   # cx 320*3 = 960
    l = mocker.MagicMock(); l.conf = [0.5]; l.xyxy = [[20, 0, 60, 360]]     # cx 40*3 = 120
    res = mocker.MagicMock(); res[0].boxes = [r, l]
    model = mocker.MagicMock(return_value=res)
    mocker.patch("reframe_core.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))
    people = reframe_core.detect_people(frame, model, width=1920, min_conf=0.3)
    assert [round(p[1]) for p in people] == [120, 960]      # ordenado esquerda -> direita
    assert people[0][0] == 0.5 and people[1][0] == 0.9      # conf preservada por pessoa


# Test 8: _merge_boxes — funde caixas duplicadas (mesma pessoa), separa pessoas distantes
def test_merge_boxes():
    W = 1920   # thr = 0.10*1920 = 192
    merged = reframe_core._merge_boxes([(0.5, 636, 200), (0.8, 646, 210)], W)   # 10px -> mesma pessoa
    assert len(merged) == 1 and merged[0][0] == 0.8 and merged[0][1] == 646     # fica a de maior conf
    merged = reframe_core._merge_boxes([(0.7, 600, 200), (0.9, 1450, 200)], W)  # 850px -> 2 pessoas
    assert len(merged) == 2


# Test 9: _classify — 1=close, 2+=plano aberto (default convidado/direita), guest ausente=hold, 0=centro
def test_classify_geometria():
    W = 1920
    assert reframe_core._classify([], W) == ("none", None)                       # vazio -> centro
    assert reframe_core._classify([(0.9, 900, 300)], W) == ("close", 900)        # 1 pessoa -> close nela
    # plano aberto: default = convidado (mais à direita)
    assert reframe_core._classify([(0.6, 600, 200), (0.9, 1450, 200)], W) == ("wide", 1450)
    # plano aberto mas só gente à esquerda (convidado não detectado) -> hold (None), não pula
    assert reframe_core._classify([(0.6, 600, 200), (0.7, 720, 200)], W) == ("wide", None)


# Test 10: _cuts_from_kinds — debounce: troca real (>=3 amostras) conta; pisca curto NÃO
def test_cuts_from_kinds_debounce():
    seq = [(0, "close"), (15, "close"), (30, "close"),
           (45, "wide"), (60, "wide"), (75, "wide"), (90, "wide"),
           (105, "close"), (120, "close"), (135, "close")]
    assert reframe_core._cuts_from_kinds(seq) == [45, 105]            # 2 trocas reais, fronteira no 1o frame
    # pisca de 2 amostras dentro do plano aberto NÃO vira corte (debounce 3)
    seq2 = [(0, "wide"), (15, "wide"), (30, "close"), (45, "close"), (60, "wide"), (75, "wide"), (90, "wide")]
    assert reframe_core._cuts_from_kinds(seq2) == []


# Test 8: trajetória SEGMENTADA por corte — re-adquire o sujeito do novo plano (não arrasta)
def test_trajetoria_reacquire_no_corte():
    # Plano A: sujeito em 500 (frame 0). Plano B (após corte no frame 45): sujeito em 1400.
    samples = [(0, 500), (45, 1400)]
    sem = reframe_core.build_locked_trajectory(samples, 90, 30.0, 1920, 600, 660, 0.35, 0.18)
    com = reframe_core.build_locked_trajectory(samples, 90, 30.0, 1920, 600, 660, 0.35, 0.18, cuts=[45])
    # sem corte: a câmera arrasta devagar de 200 (pan limitado), ainda longe do novo sujeito
    assert sem[45] < 400
    # com corte: o novo plano re-trava no sujeito imediatamente (1400 - crop_w/2 = 1100)
    assert com[45] >= 1000
    assert com[0] == 200          # plano A inalterado


# Test 9: plano sem sujeito após um corte -> corte CENTRAL daquele plano (não fica no vazio)
def test_plano_sem_sujeito_centraliza():
    # Plano A com sujeito (frame 0), corte no frame 45, plano B sem ninguém detectado.
    samples = [(0, 500), (45, None)]
    com = reframe_core.build_locked_trajectory(samples, 90, 30.0, 1920, 600, 660, 0.35, 0.18, cuts=[45])
    assert com[0] == 200                       # plano A travado no sujeito
    assert all(v == 660 for v in com[45:])     # plano B sem sujeito -> center_x (660), não 200
