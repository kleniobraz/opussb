"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { AUTH_EVENT } from "@/lib/api";
import type { Me } from "@/lib/types";

type AuthStatus = "loading" | "authed" | "guest";

interface AuthValue {
  user: Me | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // undefined = ainda carregando; null = deslogado; Me = logado.
  const [user, setUser] = useState<Me | null | undefined>(undefined);
  const queryClient = useQueryClient();

  const refresh = useCallback(async () => {
    try {
      setUser(await api.me());
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onExpired = () => {
      setUser(null);
      queryClient.clear();
    };
    window.addEventListener(AUTH_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EVENT, onExpired);
  }, [refresh, queryClient]);

  const login = useCallback(async (email: string, password: string) => {
    const m = await api.login(email, password);
    setUser(m);
    return m;
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
    queryClient.clear();
    // Limpa o flag do preloader: o próximo login volta a exibir a animação de entrada.
    if (typeof window !== "undefined") sessionStorage.removeItem("opussb_preloaded");
  }, [queryClient]);

  const changePassword = useCallback(async (current: string, next: string) => {
    await api.changePassword(current, next);
    // Trocar a senha encerra as outras sessoes; recarrega o "me" (limpa must_change).
    try {
      setUser(await api.me());
    } catch {
      setUser(null);
    }
  }, []);

  const status: AuthStatus = user === undefined ? "loading" : user ? "authed" : "guest";

  return (
    <AuthContext.Provider
      value={{ user: user ?? null, status, login, logout, changePassword, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve ser usado dentro de <AuthProvider>");
  return ctx;
}
