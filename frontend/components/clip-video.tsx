"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

// Video do clipe com queima de legenda SOB DEMANDA: a 1a abertura faz 302 e demora alguns
// segundos. O overlay "preparando" fica por cima ate o video poder tocar (eventos de midia).
export function ClipVideo({
  src,
  autoPlay = false,
  className,
  children,
}: {
  src: string;
  autoPlay?: boolean;
  className?: string;
  children?: React.ReactNode;
}) {
  const [ready, setReady] = useState(false);
  return (
    <div className={cn("clip-video", ready && "is-ready", className)}>
      <video
        src={src}
        controls
        playsInline
        preload="metadata"
        autoPlay={autoPlay}
        onLoadedMetadata={() => setReady(true)}
        onCanPlay={() => setReady(true)}
        onError={() => setReady(true)}
        onWaiting={() => setReady(false)}
      />
      {children}
      <div className="clip-prep" aria-hidden="true">
        <span className="clip-prep-spin" />
        <span className="clip-prep-note">preparando legenda…</span>
      </div>
    </div>
  );
}
