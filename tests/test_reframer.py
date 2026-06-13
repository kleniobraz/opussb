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


# Test 1: reframe_clip full flow — no person detected, no tmp file cleanup needed
def test_reframe_clip_fluxo_completo(mocker, tmp_path):
    fake_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    # Pass 1 cap
    read_calls_p1 = {"n": 0}

    def read_p1():
        read_calls_p1["n"] += 1
        return (True, fake_frame) if read_calls_p1["n"] <= 30 else (False, None)

    mock_cap1 = mocker.MagicMock()
    mock_cap1.isOpened.return_value = True
    mock_cap1.get.side_effect = lambda prop: {
        _W: 1920, _H: 1080, _FPS: 30.0, _FC: 30,
    }.get(prop, 0)
    mock_cap1.read.side_effect = read_p1

    # Pass 2 cap
    read_calls_p2 = {"n": 0}

    def read_p2():
        read_calls_p2["n"] += 1
        return (True, fake_frame) if read_calls_p2["n"] <= 30 else (False, None)

    mock_cap2 = mocker.MagicMock()
    mock_cap2.isOpened.return_value = True
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

    # VideoWriter
    mock_writer = mocker.MagicMock()
    mock_writer.isOpened.return_value = True
    mock_vw = mocker.patch("reframer.cv2.VideoWriter", return_value=mock_writer)
    mocker.patch("reframer.cv2.VideoWriter_fourcc", return_value=0x7634706D)

    # resize — return a plausible ndarray
    mocker.patch("reframer.cv2.resize", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))

    mocker.patch("reframer._mux_audio")
    mocker.patch("reframer.os.path.exists", return_value=False)

    input_file = tmp_path / "clip.mp4"
    input_file.write_bytes(b"fake")

    reframer.reframe_clip(str(input_file), str(tmp_path / "out_9x16.mp4"))

    assert mock_vc.called
    assert mock_vw.called


# Test 2: reframe_clip raises RuntimeError when video cannot be opened
def test_reframe_clip_levanta_se_nao_abre_video(mocker, tmp_path):
    mock_cap = mocker.MagicMock()
    mock_cap.isOpened.return_value = False
    mocker.patch("reframer.cv2.VideoCapture", return_value=mock_cap)

    input_file = tmp_path / "missing.mp4"
    input_file.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="Cannot open video"):
        reframer.reframe_clip(str(input_file), str(tmp_path / "out.mp4"))


# Test 3: _detect_crop_x returns fallback when no person detected
def test_detect_crop_x_sem_pessoa_retorna_fallback(mocker):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    mock_results = mocker.MagicMock()
    mock_results[0].boxes = []
    mock_model = mocker.MagicMock(return_value=mock_results)

    mocker.patch("reframer.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))

    result = reframer._detect_crop_x(frame, mock_model, width=1920, crop_w=607, fallback_x=420)

    assert result == 420


# Test 4: _detect_crop_x centers crop on detected person
def test_detect_crop_x_com_pessoa_centra_na_pessoa(mocker):
    # Pass a full-resolution frame; reframer resizes it internally (mocked)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    mock_box = mocker.MagicMock()
    mock_box.conf = [0.9]
    mock_box.xyxy = [[200, 100, 400, 900]]  # x1=200, x2=400 in 640px space

    mock_results = mocker.MagicMock()
    mock_results[0].boxes = [mock_box]
    mock_model = mocker.MagicMock(return_value=mock_results)

    mocker.patch("reframer.cv2.resize", return_value=np.zeros((360, 640, 3), dtype=np.uint8))

    # width=1920, crop_w=607
    # scale_x = 1920/640 = 3.0
    # person_cx in 640px space: (200+400)/2 = 300
    # person_cx in real coords: 300 * 3.0 = 900
    # cx = int(900 - 607/2) = int(900 - 303.5) = 596
    # clamped to [0, 1920-607=1313] → 596
    result = reframer._detect_crop_x(frame, mock_model, width=1920, crop_w=607, fallback_x=420)

    assert result == 596


# Test 5: _interpolate_and_smooth returns list of correct length with integer values
def test_interpolate_and_smooth_retorna_lista_do_tamanho_correto():
    raw_cx = [(0, 100), (15, 200), (30, 150)]
    result = reframer._interpolate_and_smooth(raw_cx, total_frames=45, window=3)

    assert len(result) == 45
    assert all(isinstance(v, int) for v in result)
