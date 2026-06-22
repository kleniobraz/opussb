"use client";

import { useRef, type ReactNode, type RefObject } from "react";

export function PreviewStage({
  videoRef,
  src,
  captionText,
  captionY = 0.67,
  onChangeY,
  onError,
  onTogglePlay,
  barExtra,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  src: string;
  captionText: string | null;
  captionY?: number;
  onChangeY?: (y: number) => void;
  onError?: () => void;
  onTogglePlay?: () => void;
  barExtra?: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);

  function startDrag(e: React.PointerEvent) {
    if (!onChangeY) return;
    e.stopPropagation();
    e.preventDefault();
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const move = (ev: PointerEvent) => {
      const f = (ev.clientY - rect.top) / rect.height;
      onChangeY(Math.max(0.05, Math.min(0.95, f)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div className="editor-stage">
      <div className="editor-stage-bar">
        <span className="editor-aspect-pill">9:16</span>
        {barExtra}
      </div>
      <div className="editor-video-wrap" ref={wrapRef}>
        <video ref={videoRef} src={src} playsInline onClick={onTogglePlay} onError={onError} />
        {captionText && (
          <div className="editor-caption" style={{ top: `${captionY * 100}%` }}>
            <span
              onPointerDown={startDrag}
              role={onChangeY ? "button" : undefined}
              aria-label={onChangeY ? "Arraste para posicionar a legenda" : undefined}
              title={onChangeY ? "Arraste para cima/baixo para posicionar" : undefined}
            >
              {captionText}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
