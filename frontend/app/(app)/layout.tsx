"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
import { MiniNavbar } from "@/components/ui/mini-navbar";
import { HashRedirect } from "@/components/hash-redirect";
import Preloader from "@/components/ui/preloader";
import { useShortcuts } from "@/hooks/use-shortcuts";

// Flag por sessão: a animação de entrada toca uma vez por login (o logout limpa o flag
// no auth-provider, então cada novo login replaya). Refresh no meio da sessão não repete.
const PRELOAD_KEY = "opussb_preloaded";

function FullscreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <span className="clip-prep-spin" aria-label="Carregando" />
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { status, user } = useAuth();
  const [showPreloader, setShowPreloader] = useState(false);
  useShortcuts();

  useEffect(() => {
    if (status === "guest") router.replace("/login");
    else if (status === "authed" && user?.must_change_password) router.replace("/senha");
  }, [status, user, router]);

  // Ao entrar no app autenticado, dispara o preloader se ainda não tocou nesta sessão.
  useEffect(() => {
    if (status === "authed" && !user?.must_change_password) {
      if (typeof window !== "undefined" && !sessionStorage.getItem(PRELOAD_KEY)) {
        setShowPreloader(true);
      }
    }
  }, [status, user]);

  const finishPreloader = useCallback(() => {
    if (typeof window !== "undefined") sessionStorage.setItem(PRELOAD_KEY, "1");
    setShowPreloader(false);
  }, []);

  // Enquanto carrega/redireciona, segura a UI (evita flash de conteudo protegido).
  if (status !== "authed" || user?.must_change_password) {
    return <FullscreenLoader />;
  }

  return (
    <div className="app-shell flex min-h-screen flex-col">
      {showPreloader && <Preloader onComplete={finishPreloader} />}
      <HashRedirect />
      <MiniNavbar />
      <main className="flex-1 pt-20">{children}</main>
      <footer className="app-footer">
        Cortado com <span aria-hidden="true">💛</span> por{" "}
        <a
          href="https://www.instagram.com/kleniobraz/"
          target="_blank"
          rel="noopener noreferrer"
          className="app-footer-link"
        >
          Klenio Braz
        </a>
      </footer>
    </div>
  );
}
