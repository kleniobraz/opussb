"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

function isTyping(el: EventTarget | null): boolean {
  if (!el || !(el instanceof HTMLElement)) return false;
  const tag = el.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

// Atalhos globais (portados do app.js): g+h/p/f/c navega, n novo video, ? ajuda.
export function useShortcuts() {
  const router = useRouter();
  const lastG = useRef(0);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target)) return;
      // Ignora quando um dialog (Radix) esta aberto.
      if (document.querySelector('[role="dialog"][data-state="open"]')) {
        if (e.key !== "?") return;
      }
      if (e.key === "?") {
        e.preventDefault();
        toast.info("Atalhos: n novo vídeo · g depois h/p/f/c navega · Esc fecha");
        return;
      }
      const now = Date.now();
      if (e.key === "g") {
        lastG.current = now;
        return;
      }
      if (now - lastG.current < 800) {
        if (e.key === "h") return router.push("/");
        if (e.key === "p") return router.push("/projetos");
        if (e.key === "f") return router.push("/favoritos");
        if (e.key === "c") return router.push("/config");
      }
      if (e.key === "n") {
        router.push("/gerar");
        setTimeout(() => document.getElementById("video-file")?.focus(), 60);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [router]);
}
