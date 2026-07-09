"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BrandMark } from "@/components/brand-mark";
import { LoginWaveBackground } from "@/components/login-wave-background";
import { Button } from "@/components/ui/button";
import { ShinyButton } from "@/components/ui/shiny-button";
import MotionButton from "@/components/ui/motion-button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/providers/auth-provider";
import * as api from "@/lib/api";

// Logo do Zoho (simple-icons, CC0) — monocromático p/ ler bem sob o mix-blend-difference.
function ZohoLogo() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
      <path d="M8.66 6.897a1.299 1.299 0 0 0-1.205.765l-.642 1.44-.062-.385A1.291 1.291 0 0 0 5.27 7.648l-4.185.678A1.291 1.291 0 0 0 .016 9.807l.678 4.18a1.293 1.293 0 0 0 1.27 1.087c.074 0 .143-.01.216-.017l4.18-.678c.436-.07.784-.351.96-.723l2.933 1.307a1.304 1.304 0 0 0 .988.026c.321-.12.575-.365.716-.678l.28-.629.038.276a1.297 1.297 0 0 0 1.455 1.103l3.712-.501a1.29 1.29 0 0 0 1.03.514h4.236c.713 0 1.29-.58 1.291-1.291V9.545c0-.712-.58-1.291-1.291-1.291h-4.236c-.079 0-.155.008-.23.022a1.309 1.309 0 0 0-.275-.288c-.275-.21-.614-.3-.958-.253l-4.197.571c-.155.021-.3.07-.432.14L9.159 7.01a1.27 1.27 0 0 0-.499-.113zm-.025.705c.077 0 .159.013.24.052l2.971 1.324c-.128.238-.18.508-.142.782l.357 2.596h.002l-.745 1.672a.59.59 0 0 1-.777.296l-3.107-1.385-.004-.041-.41-2.526L8.1 7.95a.589.589 0 0 1 .536-.348zm-3.159.733c.125 0 .245.039.343.112.13.09.21.227.237.382l.234 1.446-.56 1.259a1.27 1.27 0 0 0-.026.987c.12.322.364.575.678.717l.295.131a.585.585 0 0 1-.428.314l-4.185.678a.59.59 0 0 1-.674-.485l-.678-4.18a.588.588 0 0 1 .485-.674l4.185-.678c.03-.004.064-.01.094-.01zm11.705.09a.59.59 0 0 1 .415.173 1.287 1.287 0 0 0-.416.947v4.237c0 .033.003.065.005.097l-3.55.482a.586.586 0 0 1-.66-.502l-.191-1.403.899-2.017a1.29 1.29 0 0 0-.333-1.5l3.754-.51c.026-.004.051-.004.077-.004zm1.3.532h4.227c.326 0 .588.266.588.588v4.237a.589.589 0 0 1-.588.588h-4.237a.564.564 0 0 1-.12-.013c.47-.246.758-.765.684-1.318zm-5.988.309.254.113c.296.133.43.48.296.777l-.432.97-.207-1.465a.58.58 0 0 1 .09-.395zm5.39.538.453 3.325a.583.583 0 0 1-.453.65zM6.496 11.545l.17 1.052a.588.588 0 0 1-.293-.776zm3.985 4.344a.588.588 0 0 0-.612.603c0 .358.244.61.601.61a.582.582 0 0 0 .607-.608c0-.35-.242-.605-.596-.605zm5.545 0a.588.588 0 0 0-.612.603c0 .358.245.61.602.61a.582.582 0 0 0 .606-.608c0-.35-.24-.605-.596-.605zm-8.537.018a.047.047 0 0 0-.048.047v.085c0 .026.021.047.048.047h.52l-.623.9a.052.052 0 0 0-.009.027v.027c0 .026.021.047.048.047h.815a.047.047 0 0 0 .047-.047v-.085a.047.047 0 0 0-.047-.047h-.55l.606-.9a.05.05 0 0 0 .008-.026v-.028a.047.047 0 0 0-.047-.047zm5.303 0a.047.047 0 0 0-.047.047v1.086c0 .026.02.047.047.047h.135a.047.047 0 0 0 .047-.047v-.454h.545v.454c0 .026.02.047.047.047h.134a.047.047 0 0 0 .047-.047v-1.086a.047.047 0 0 0-.047-.047h-.134a.047.047 0 0 0-.047.047v.453h-.545v-.453a.047.047 0 0 0-.047-.047zm-2.324.164c.25 0 .372.194.372.425 0 .219-.109.425-.358.426-.242 0-.375-.197-.375-.419 0-.235.108-.432.36-.432zm5.545 0c.25 0 .372.194.372.425 0 .219-.108.425-.358.426-.242 0-.374-.197-.374-.419 0-.235.108-.432.36-.432z" />
    </svg>
  );
}

// Mensagens pros códigos de erro que o backend devolve no redirect (?error=...).
const ERROR_MESSAGES: Record<string, string> = {
  dominio: "Esse e-mail não tem acesso. Use uma conta @seubone.com ou @grupoquatro5.com.",
  naoverificado: "Seu e-mail do Zoho não está verificado.",
  zoho_off: "O login com Zoho ainda não está configurado.",
  zoho_denied: "Login com Zoho cancelado.",
  rejeitado: "Seu acesso foi recusado. Fale com um administrador.",
  inativo: "Sua conta está desativada. Fale com um administrador.",
};

export default function LoginPage() {
  const router = useRouter();
  const { status, user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [zohoEnabled, setZohoEnabled] = useState(false);

  // Modo cadastro
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [suEmail, setSuEmail] = useState("");
  const [suPass, setSuPass] = useState("");
  const [suPass2, setSuPass2] = useState("");
  const [suError, setSuError] = useState("");
  const [suLoading, setSuLoading] = useState(false);
  const [suDone, setSuDone] = useState(false);

  // Ja logado? sai do login.
  useEffect(() => {
    if (status === "authed") {
      router.replace(user?.must_change_password ? "/senha" : "/");
    }
  }, [status, user, router]);

  // Botão Zoho só aparece se o provider estiver configurado no backend.
  useEffect(() => {
    api
      .authProviders()
      .then((p) => setZohoEnabled(!!p.zoho))
      .catch(() => setZohoEnabled(false));
  }, []);

  // Erro vindo do callback do Zoho (?error=...). Lê e limpa a URL.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const code = new URLSearchParams(window.location.search).get("error");
    if (!code) return;
    setError(ERROR_MESSAGES[code] || "Não foi possível entrar com o Zoho. Tente de novo.");
    window.history.replaceState({}, "", "/login");
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const m = await login(email, password);
      router.replace(m.must_change_password ? "/senha" : "/");
    } catch (ex) {
      setError((ex as Error)?.message || "Falha no login.");
      setLoading(false);
    }
  }

  async function onSignup(e: React.FormEvent) {
    e.preventDefault();
    setSuError("");
    if (suPass !== suPass2) return setSuError("As senhas não coincidem.");
    if (suPass.length < 8) return setSuError("A senha precisa ter ao menos 8 caracteres.");
    setSuLoading(true);
    try {
      await api.signup(suEmail, suPass);
      setSuDone(true);
    } catch (ex) {
      setSuError((ex as Error)?.message || "Falha no cadastro.");
    } finally {
      setSuLoading(false);
    }
  }

  return (
    <div className="login-page">
      <LoginWaveBackground />
      <div className="login-page-veil" aria-hidden="true" />
      <div className="login-hero-mark" aria-hidden="true">
        <BrandMark />
      </div>
      <div className="login-hero-mark-alt" aria-hidden="true">
        <BrandMark />
      </div>

      <div className="login-shell">
        <header className="login-topbar">
          <div className="brand">
            <BrandMark />
            <span className="brand-name" style={{ fontSize: "1.15rem" }}>
              SB Clips
            </span>
          </div>
          <span className="login-topbar-tag">
            <span className="demo-dot" /> Acesso interno · Grupo SB
          </span>
        </header>

        <div className="login-content">
          <div className="login-hero">
            <h1 className="login-headline">
              Vídeos longos viram <span className="grad-text">clipes que bombam.</span>
            </h1>
            <p className="login-hero-sub">
              A central de clipes do Grupo SB. Envie um vídeo longo e receba os melhores momentos
              em 9:16.
            </p>
          </div>

          <div className="login-card">
            {mode === "login" ? (
              <>
                <p className="eyebrow">Acesso interno</p>
                <h2 className="auth-title">Entrar no SB Clips</h2>
                <p className="auth-sub">Entre com sua conta do Grupo SB.</p>

                {zohoEnabled && (
                  <>
                    <ShinyButton
                      type="button"
                      className="w-full"
                      onClick={() => {
                        window.location.href = "/api/auth/zoho/login";
                      }}
                    >
                      <ZohoLogo />
                      Entrar com Zoho
                    </ShinyButton>
                    <div className="auth-divider">
                      <span>ou com e-mail e senha</span>
                    </div>
                  </>
                )}

                <form onSubmit={onSubmit}>
                  <div className="login-field">
                    <Label htmlFor="login-email">Email</Label>
                    <Input
                      id="login-email"
                      type="email"
                      autoComplete="username"
                      placeholder="voce@seubone.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                    />
                  </div>
                  <div className="login-field">
                    <Label htmlFor="login-password">Senha</Label>
                    <Input
                      id="login-password"
                      type="password"
                      autoComplete="current-password"
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                    />
                  </div>
                  <MotionButton
                    type="submit"
                    label={loading ? "Entrando..." : "Entrar"}
                    disabled={loading}
                  />
                </form>
                {error && <div className="error">{error}</div>}

                <div className="auth-foot">
                  Não tem conta?{" "}
                  <button
                    type="button"
                    className="auth-link"
                    onClick={() => {
                      setMode("signup");
                      setError("");
                    }}
                  >
                    Cadastre-se
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="eyebrow">Novo cadastro</p>
                <h2 className="auth-title">Criar conta</h2>
                <p className="auth-sub">
                  Seu cadastro passa por aprovação de um administrador antes do primeiro acesso.
                </p>

                {suDone ? (
                  <div className="auth-success">
                    <p className="auth-success-title">Cadastro recebido! ✅</p>
                    <p>
                      Sua conta foi criada e está <strong>aguardando aprovação</strong> de um
                      administrador. Você poderá entrar assim que for liberada.
                    </p>
                  </div>
                ) : (
                  <form onSubmit={onSignup}>
                    <div className="login-field">
                      <Label htmlFor="su-email">Email</Label>
                      <Input
                        id="su-email"
                        type="email"
                        autoComplete="email"
                        placeholder="seu@email.com"
                        value={suEmail}
                        onChange={(e) => setSuEmail(e.target.value)}
                        required
                      />
                    </div>
                    <div className="login-field">
                      <Label htmlFor="su-pass">Senha (mín. 8 caracteres)</Label>
                      <Input
                        id="su-pass"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        value={suPass}
                        onChange={(e) => setSuPass(e.target.value)}
                        required
                      />
                    </div>
                    <div className="login-field">
                      <Label htmlFor="su-pass2">Repita a senha</Label>
                      <Input
                        id="su-pass2"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        value={suPass2}
                        onChange={(e) => setSuPass2(e.target.value)}
                        required
                      />
                    </div>
                    <Button
                      type="submit"
                      variant="gold"
                      size="brand"
                      className="w-full"
                      disabled={suLoading}
                    >
                      {suLoading ? "Cadastrando..." : "Criar conta"}
                    </Button>
                  </form>
                )}
                {suError && <div className="error">{suError}</div>}

                <div className="auth-foot">
                  <button
                    type="button"
                    className="auth-link"
                    onClick={() => {
                      setMode("login");
                      setSuError("");
                      setSuDone(false);
                    }}
                  >
                    ← Voltar ao login
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
