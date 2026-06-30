"use client";

import { useRef, type ReactNode, type RefObject } from "react";

// Largura fixa da caixa 9:16 sobre um 16:9 (so pan, sem zoom): (9/16)/(16/9) = 81/256 da largura.
// Bate com o backend (reframe_core: crop_w = height*9/16, sobre width = height*16/9).
export const CROP_BOX_W_FRAC = 81 / 256;

// Palco do enquadramento: mostra a FONTE 16:9 (/src169) com uma caixa 9:16 arrastavel por cima.
// O recorte e so visual (CSS); o render final 9:16 sai do servidor no "Aplicar". A caixa segue a
// trajetoria dos keyframes enquanto o video toca (cx vem do pai, interpolado pelo tempo atual);
// arrastar cria/atualiza um keyframe no instante atual.
export function CropStage({
  videoRef,
  src,
  cx,
  boxWidthFrac = CROP_BOX_W_FRAC,
  onDragCx,
  onCommitCx,
  onError,
  onTogglePlay,
  barExtra,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  src: string;
  cx: number; // centro horizontal do recorte (fracao 0..1)
  boxWidthFrac?: number;
  onDragCx?: (cx: number) => void; // durante o arraste (so visual)
  onCommitCx?: (cx: number) => void; // ao soltar: cria/atualiza o keyframe no tempo atual
  onError?: () => void;
  onTogglePlay?: () => void;
  barExtra?: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const half = boxWidthFrac / 2;
  const clamp = (c: number) => Math.max(half, Math.min(1 - half, c));
  const center = clamp(Number.isFinite(cx) ? cx : 0.5);
  const leftPct = (center - half) * 100;
  const widthPct = boxWidthFrac * 100;
  const rightPct = Math.max(0, 100 - leftPct - widthPct);

  function startDrag(e: React.PointerEvent) {
    if (!onCommitCx) return;
    e.stopPropagation();
    e.preventDefault();
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    let last = center;
    const move = (ev: PointerEvent) => {
      last = clamp((ev.clientX - rect.left) / rect.width);
      onDragCx?.(last);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onCommitCx?.(last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div className="editor-stage">
      <div className="editor-stage-bar">
        <span className="editor-aspect-pill">16:9 → 9:16</span>
        {barExtra}
      </div>
      <div className="editor-crop-wrap" ref={wrapRef}>
        <video ref={videoRef} src={src} playsInline onClick={onTogglePlay} onError={onError} />
        {/* faixas escurecidas fora do recorte 9:16 */}
        <div className="editor-crop-mask" style={{ left: 0, width: `${leftPct}%` }} />
        <div className="editor-crop-mask" style={{ right: 0, width: `${rightPct}%` }} />
        {/* caixa 9:16 arrastavel (so horizontal) */}
        <div
          className={"editor-crop-box" + (onCommitCx ? " is-draggable" : "")}
          style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
          onPointerDown={startDrag}
          role={onCommitCx ? "button" : undefined}
          aria-label={onCommitCx ? "Arraste para enquadrar (horizontal)" : undefined}
          title={onCommitCx ? "Arraste para a esquerda/direita para enquadrar" : undefined}
        >
          <span className="editor-crop-grip" />
        </div>
      </div>
    </div>
  );
}
