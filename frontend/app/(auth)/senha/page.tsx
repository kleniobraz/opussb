"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/providers/auth-provider";

export default function SenhaPage() {
  const router = useRouter();
  const { status, changePassword } = useAuth();
  const [cur, setCur] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Sem sessao, volta pro login.
  useEffect(() => {
    if (status === "guest") router.replace("/login");
  }, [status, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (n1 !== n2) return setError("As senhas novas não coincidem.");
    if (n1.length < 8) return setError("A nova senha precisa ter ao menos 8 caracteres.");
    setLoading(true);
    try {
      await changePassword(cur, n1);
      router.replace("/");
    } catch (ex) {
      setError((ex as Error)?.message || "Falha ao trocar a senha.");
      setLoading(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="login-glows" aria-hidden="true">
        <div className="glow glow-a" />
        <div className="glow glow-b" />
      </div>
      <div className="auth-card">
        <div className="brand">
          <BrandMark />
          <span className="brand-text">
            <span className="brand-name">SB Clips</span>
            <span className="brand-sub">CLIPES · SEUBONÉ</span>
          </span>
        </div>
        <h1 className="auth-title" style={{ fontSize: "1.6rem" }}>
          Defina sua senha
        </h1>
        <p className="auth-sub">Primeiro acesso: crie uma senha pessoal antes de continuar.</p>
        <form onSubmit={onSubmit}>
          <div className="mb-[18px] grid gap-2">
            <Label htmlFor="cur-password">Senha atual (a padrão)</Label>
            <Input
              id="cur-password"
              type="password"
              autoComplete="current-password"
              value={cur}
              onChange={(e) => setCur(e.target.value)}
              required
            />
          </div>
          <div className="mb-[18px] grid gap-2">
            <Label htmlFor="new-password">Nova senha (mínimo 8 caracteres)</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={n1}
              onChange={(e) => setN1(e.target.value)}
              required
            />
          </div>
          <div className="mb-[18px] grid gap-2">
            <Label htmlFor="new-password2">Repita a nova senha</Label>
            <Input
              id="new-password2"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={n2}
              onChange={(e) => setN2(e.target.value)}
              required
            />
          </div>
          <Button type="submit" variant="gold" size="brand" className="w-full" disabled={loading}>
            {loading ? "Salvando..." : "Salvar nova senha"}
          </Button>
        </form>
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}
