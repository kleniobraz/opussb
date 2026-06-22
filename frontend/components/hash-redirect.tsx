"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Compatibilidade com os links/bookmarks antigos do roteador hash (#/projetos -> /projetos).
export function HashRedirect() {
  const router = useRouter();
  useEffect(() => {
    const h = window.location.hash;
    if (!h || !h.startsWith("#/")) return;
    const seg = h.slice(2); // remove "#/"
    const path = "/" + seg;
    if (window.location.pathname !== path) router.replace(path || "/");
  }, [router]);
  return null;
}
