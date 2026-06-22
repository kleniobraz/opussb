"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Inter } from "next/font/google";
import { useAuth } from "@/providers/auth-provider";
import { ClipEditor } from "@/components/editor/clip-editor";

// Inter pro overlay de legenda (espelha a fonte usada na queima SB no backend).
const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-inter",
  display: "swap",
});

export default function EditarPage() {
  const params = useParams<{ clipId: string }>();
  const router = useRouter();
  const { status } = useAuth();
  const [title, setTitle] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (status === "guest") router.replace("/login");
  }, [status, router]);

  useEffect(() => {
    try {
      const t = new URLSearchParams(window.location.search).get("t");
      if (t) setTitle(t);
    } catch {
      /* ignore */
    }
  }, []);

  if (status !== "authed") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span className="clip-prep-spin" aria-label="Carregando" />
      </div>
    );
  }

  // Next 16 pode entregar o param ainda url-encoded (o id tem ":"). Decodifica de forma
  // segura (idempotente quando ja vem decodificado) pra nao duplo-encodar na chamada de API.
  let clipId = params?.clipId ?? "";
  try {
    clipId = decodeURIComponent(clipId);
  } catch {
    /* mantem como veio */
  }

  return (
    <div className={inter.variable}>
      <ClipEditor clipId={clipId} title={title} />
    </div>
  );
}
