#!/usr/bin/env python
"""tools/eval_reframe.py — avaliação da detecção de troca de câmera do reframe.

Roda a Passada 1 (reframe_core.sample_camera: YOLO real + diff de quadro inteiro) direto num
vídeo real, sem passar por processor.py/DB/S3. Não custa dinheiro (só YOLO local + leitura de
vídeo) e não grava nada no banco.

Serve pra:
  - CALIBRAR o threshold do diff (_CUT_DIFF_THRESHOLD): reporta a distribuição do diff no vídeo
    inteiro (a maior parte é "chão" de plano contínuo) e os maiores picos (candidatos a corte real).
  - LISTAR os cortes: por tipo (_cuts_from_kinds, o comportamento ANTIGO), por diff
    (_cuts_from_diff) e a união (o comportamento NOVO) — destaca os cortes que só o diff achou
    (o buraco que motivou o fix: dois planos do MESMO kind).
  - PROVAR o antes/depois: pra cada corte que só o diff achou, compara a trajetória
    (build_locked_trajectory) com cuts=só-tipo vs cuts=união numa janela ao redor do corte.

Uso
---
    ./venv/bin/python tools/eval_reframe.py <video.mp4> [--start 0] [--end -1]
        [--min-conf 0.30] [--sample-every 15] [--threshold 0.09] [--json out.json]
        [--no-before-after]

--threshold reprocessa os cortes em cima do diff já calculado (não redecodifica o vídeo), pra
testar valores diferentes rápido depois de uma primeira rodada com --json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import reframe_core  # noqa: E402


def _load_model(weights: str):
    from ultralytics import YOLO
    return YOLO(weights)


def analyze(video_path: str, model, *, start_s: float, end_s: float,
           min_conf: float, sample_every: int) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"não abriu: {video_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if start_s > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    end_frame = int(end_s * fps) if end_s > 0 else None

    # detect_cuts=True pra sempre trazer diffs (o threshold real é recomputado depois, fora daqui,
    # via recompute_cuts, sem precisar redecodificar o vídeo pra testar outro valor).
    cam = reframe_core.sample_camera(cap, model, width, min_conf=min_conf, detect_cuts=True,
                                     sample_every=sample_every, max_frame=end_frame)
    cap.release()

    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    center_x = (width - crop_w) // 2
    return {"width": width, "height": height, "fps": fps, "total_frames": total_frames,
            "crop_w": crop_w, "center_x": center_x, **cam}


def recompute_cuts(cam: dict, *, threshold: float, sample_every: int) -> dict:
    cuts_kind = reframe_core._cuts_from_kinds(cam["kinds"])
    cuts_diff = reframe_core._cuts_from_diff(cam["diffs"], threshold=threshold, min_gap=sample_every)
    cuts = reframe_core._merge_cuts(cuts_kind, cuts_diff, sample_every)
    return {"cuts_kind": cuts_kind, "cuts_diff": cuts_diff, "cuts": cuts}


def report_calibration(cam: dict, fps: float) -> None:
    diffs = cam["diffs"]
    if not diffs:
        print("sem diffs (vídeo curto demais pra 2 amostras)")
        return
    vals = sorted(d for _, d in diffs)
    n = len(vals)
    def pct(p):
        return vals[min(n - 1, int(n * p))]
    print(f"\n=== calibração do diff (n={n} amostras) ===")
    print(f"chão (sem corte):  p50={pct(0.50):.4f}  p90={pct(0.90):.4f}  "
         f"p95={pct(0.95):.4f}  p99={pct(0.99):.4f}")
    print(f"máximo: {vals[-1]:.4f}")
    top = sorted(diffs, key=lambda x: -x[1])[:15]
    print("maiores picos (candidatos a corte real):")
    for frame, d in top:
        print(f"  frame={frame:7d}  t={frame / fps:7.2f}s  diff={d:.4f}")


def report_cuts(cam: dict, fps: float) -> None:
    print(f"\n=== cortes por TIPO (_cuts_from_kinds, comportamento ANTIGO): {len(cam['cuts_kind'])} ===")
    for f in cam["cuts_kind"]:
        print(f"  frame={f:7d}  t={f / fps:7.2f}s")
    print(f"\n=== cortes por DIFF (_cuts_from_diff): {len(cam['cuts_diff'])} ===")
    for f in cam["cuts_diff"]:
        print(f"  frame={f:7d}  t={f / fps:7.2f}s")
    novos = sorted(set(cam["cuts_diff"]) - set(cam["cuts_kind"]))
    print(f"\n=== cortes que SÓ o diff achou (o buraco que motivou o fix): {len(novos)} ===")
    for f in novos:
        print(f"  frame={f:7d}  t={f / fps:7.2f}s")
    print(f"\n=== união final (comportamento NOVO): {len(cam['cuts'])} ===")
    for f in cam["cuts"]:
        marca = " <- só o diff achou" if f in novos else ""
        print(f"  frame={f:7d}  t={f / fps:7.2f}s{marca}")


def report_before_after(cam: dict, fps: float, deadzone_frac: float, max_pan_frac: float,
                        window_s: float) -> None:
    novos = sorted(set(cam["cuts_diff"]) - set(cam["cuts_kind"]))
    if not novos:
        print("\n(nenhum corte novo pra comparar antes/depois: o diff não achou nada que o "
             "tipo já não achasse neste trecho)")
        return
    window = int(window_s * fps)
    samples, total_frames = cam["samples"], cam["total_frames"]
    width, crop_w, center_x = cam["width"], cam["crop_w"], cam["center_x"]
    for f_cut in novos:
        antes = reframe_core.build_locked_trajectory(
            samples, total_frames, fps, width, crop_w, center_x,
            deadzone_frac, max_pan_frac, cuts=cam["cuts_kind"])
        depois = reframe_core.build_locked_trajectory(
            samples, total_frames, fps, width, crop_w, center_x,
            deadzone_frac, max_pan_frac, cuts=cam["cuts"])
        lo, hi = max(0, f_cut - window), min(total_frames, f_cut + window)
        print(f"\n=== antes/depois no corte t={f_cut / fps:.2f}s (frame {f_cut}) ===")
        print(f"{'frame':>8} {'t_s':>7} {'ANTES (só tipo)':>16} {'DEPOIS (união)':>16}")
        step = max(1, (hi - lo) // 20)
        for i in range(lo, hi, step):
            print(f"{i:8d} {i / fps:7.2f} {antes[i]:16d} {depois[i]:16d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0, help="início do trecho (segundos)")
    ap.add_argument("--end", type=float, default=-1.0, help="fim do trecho (segundos), -1 = até o fim")
    ap.add_argument("--min-conf", type=float, default=0.30)
    ap.add_argument("--sample-every", type=int, default=15)
    ap.add_argument("--threshold", type=float, default=reframe_core._CUT_DIFF_THRESHOLD)
    ap.add_argument("--deadzone-frac", type=float, default=0.35)
    ap.add_argument("--max-pan-frac", type=float, default=0.18)
    ap.add_argument("--window-s", type=float, default=3.0, help="janela do antes/depois, em segundos")
    ap.add_argument("--weights", default="yolov8n.pt")
    ap.add_argument("--json", default=None, help="grava kinds/diffs/cuts brutos aqui")
    ap.add_argument("--no-before-after", action="store_true")
    args = ap.parse_args()

    model = _load_model(args.weights)
    cam = analyze(args.video, model, start_s=args.start, end_s=args.end,
                 min_conf=args.min_conf, sample_every=args.sample_every)
    cam.update(recompute_cuts(cam, threshold=args.threshold, sample_every=args.sample_every))

    report_calibration(cam, cam["fps"])
    report_cuts(cam, cam["fps"])
    if not args.no_before_after:
        report_before_after(cam, cam["fps"], args.deadzone_frac, args.max_pan_frac, args.window_s)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({k: v for k, v in cam.items() if k != "samples" or True}, fh, indent=2)
        print(f"\nbruto salvo em {args.json}")


if __name__ == "__main__":
    main()
