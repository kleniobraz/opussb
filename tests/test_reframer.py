import sys
import types
import unittest.mock as _mock

# Stub out ultralytics and cv2 before reframer is imported so there are no
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
    _cv2_mod.VideoWriter = _mock.MagicMock()
    _cv2_mod.VideoWriter_fourcc = _mock.MagicMock(return_value=0)
    _cv2_mod.resize = _mock.MagicMock()
    sys.modules["cv2"] = _cv2_mod

import numpy as np
import pytest
import reframer


# Grab the stubbed cv2 constants via the module reframer imported
_W = reframer.cv2.CAP_PROP_FRAME_WIDTH    # 3
_H = reframer.cv2.CAP_PROP_FRAME_HEIGHT   # 4
_FPS = reframer.cv2.CAP_PROP_FPS          # 5
_FC = reframer.cv2.CAP_PROP_FRAME_COUNT   # 7


# Test 1: reframe_clip full flow — encode único via pipe (sem mp4v intermediário)
def test_reframe_clip_fluxo_completo(mocker, tmp_path):
    fake_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    # Pass 1 cap: agora usa grab() (30x e depois False) + retrieve() nos amostrados.
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

    # Pass 2 cap (dentro de _pipe_reframe_encode): read() 30x e depois False.
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

    mock_vc = mocker.patch("reframer.cv2.VideoCapture", side_effect=vc_side_effect)

    # YOLO model — no detections
    mock_results = mocker.MagicMock()
    mock_results[0].boxes = []
    mock_model = mocker.MagicMock(return_value=mock_results)
    mocker.patch("reframer._get_model", return_value=mock_model)

    # resize — return a plausible ndarray
    mocker.patch("reframer.cv2.resize", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))

    # ffmpeg do encode único: Popen mockado + a checagem de saída ok.
    mock_proc = mocker.MagicMock()
    mock_proc.wait.return_value = 0
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_popen = mocker.patch("reframer.subprocess.Popen", return_value=mock_proc)
    mocker.patch("reframer.tempfile.TemporaryFile", return_value=mocker.MagicMock())
    mocker.patch("reframer.os.path.exists", return_value=True)
    mocker.patch("reframer.os.path.getsize", return_value=2048)

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
    mocker.patch("reframer.cv2.VideoCapture", return_value=mock_cap)

    input_file = tmp_path / "missing.mp4"
    input_file.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="Cannot open video"):
        reframer.reframe_clip(str(input_file), str(tmp_path / "out.mp4"))


# Test 3: _detect_subject_cx devolve None quando não há pessoa (-> trajetória SEGURA a posição)
def test_detect_subject_sem_pessoa_retorna_none(mocker):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    mock_results = mocker.MagicMock()
    mock_results[0].boxes = []
    mock_model = mocker.MagicMock(return_value=mock_results)

    mocker.patch("reframer.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))

    result = reframer._detect_subject_cx(frame, mock_model, width=1920, prev_cx=None, min_conf=0.3)

    assert result is None


# Test 4: _detect_subject_cx devolve o CENTRO do sujeito em coords da resolução original
def test_detect_subject_centra_na_pessoa(mocker):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    mock_box = mocker.MagicMock()
    mock_box.conf = [0.9]
    mock_box.xyxy = [[200, 100, 400, 900]]  # x1=200, x2=400 no espaço de 640px

    mock_results = mocker.MagicMock()
    mock_results[0].boxes = [mock_box]
    mock_model = mocker.MagicMock(return_value=mock_results)

    mocker.patch("reframer.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))

    # scale_x = 1920/640 = 3.0; centro em 640px = (200+400)/2 = 300; real = 300*3.0 = 900
    result = reframer._detect_subject_cx(frame, mock_model, width=1920, prev_cx=None, min_conf=0.3)

    assert result == 900


# Test 4b: continuidade — com sujeito anterior, segue o box mais PRÓXIMO, não o mais confiante
def test_detect_subject_continuidade_segue_o_mais_proximo(mocker):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # A: muito confiante e longe (centro real 120); B: menos confiante e perto (centro real 960)
    a = mocker.MagicMock(); a.conf = [0.95]; a.xyxy = [[20, 0, 60, 360]]
    b = mocker.MagicMock(); b.conf = [0.50]; b.xyxy = [[300, 0, 340, 360]]
    mock_results = mocker.MagicMock()
    mock_results[0].boxes = [a, b]
    mock_model = mocker.MagicMock(return_value=mock_results)
    mocker.patch("reframer.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))

    # sujeito anterior perto de 960 -> escolhe B (perto), não A (confiante)
    result = reframer._detect_subject_cx(frame, mock_model, width=1920, prev_cx=950.0, min_conf=0.3)

    assert result == 960


# Test 5: câmera travada — sujeito parado -> corte FIXO (não se mexe)
def test_trajetoria_travada_sujeito_parado():
    samples = [(0, 500), (15, 500), (30, 500)]
    out = reframer._build_locked_trajectory(samples, total_frames=45, fps=30.0,
                                            width=1920, crop_w=600, center_x=660)
    assert len(out) == 45
    assert all(isinstance(v, int) for v in out)
    # crop_x = centro_sujeito(500) - crop_w/2(300) = 200, constante (dentro da zona morta)
    assert all(v == 200 for v in out)


# Test 6: câmera travada — sujeito salta -> segue DEVAGAR (sem pulo pro alvo)
def test_trajetoria_travada_segue_devagar():
    samples = [(0, 500), (45, 1100)]   # parado em 500 e salta pra 1100 no frame 45
    out = reframer._build_locked_trajectory(samples, total_frames=75, fps=30.0,
                                            width=1920, crop_w=600, center_x=660)
    assert len(out) == 75
    assert out[0] == 200          # começa travado no sujeito
    assert out[-1] > 200          # já começou a acompanhar
    assert out[-1] < 500          # mas devagar: longe de ter pulado pro alvo (1100-105=995 -> crop_x 695)
