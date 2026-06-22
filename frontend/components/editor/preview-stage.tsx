"use client";

import { useRef, type ReactNode, type RefObject } from "react";

// Estilo aplicado ao overlay AO VIVO. Mapeado pra mesma referência da queima (1080x1920),
// pra o preview bater com o vídeo final: tamanho 70 = 70/1920 = 3.65% da altura (cqh);
// \fsp em px a 1080 de largura vira fração da largura (cqw); sombra (0..1) vira a opacidade
// do text-shadow. cqh/cqw funcionam porque .editor-video-wrap é container-type: size.
type OverlayStyle = { size: number; tracking: number; color: string; shadow: number };
const DEFAULT_OVERLAY: OverlayStyle = { size: 70, tracking: -3, color: "#FFFFFF", shadow: 0.86 };

export function PreviewStage({
  videoRef,
  src,
  captionText,
  captionY = 0.67,
  captionStyle,
  onChangeY,
  onError,
  onTogglePlay,
  barExtra,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  src: string;
  captionText: string | null;
  captionY?: number;
  captionStyle?: OverlayStyle;
  onChangeY?: (y: number) => void;
  onError?: () => void;
  onTogglePlay?: () => void;
  barExtra?: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const cs = captionStyle ?? DEFAULT_OVERLAY;
  const sh = Math.max(0, Math.min(1, cs.shadow));
  const spanStyle = {
    fontSize: `${cs.size / 19.2}cqh`,
    letterSpacing: `${cs.tracking / 10.8}cqw`,
    color: cs.color,
    textShadow: `0 0 0.6cqh rgba(0,0,0,${sh}), 0 0.22cqh 0.9cqh rgba(0,0,0,${sh})`,
  };

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
              style={spanStyle}
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
