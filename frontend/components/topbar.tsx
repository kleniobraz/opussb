"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { BrandMark } from "./brand-mark";
import { MobileMenu } from "./mobile-menu";
import { Button } from "./ui/button";
import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, initialsFromEmail } from "@/lib/format";

export const NAV = [
  { href: "/", label: "Home", key: "home" },
  { href: "/projetos", label: "Projetos", key: "projetos" },
  { href: "/favoritos", label: "Favoritos", key: "favoritos" },
  { href: "/config", label: "Configurações", key: "config" },
] as const;

export function activeKey(pathname: string): string {
  if (pathname.startsWith("/projetos")) return "projetos";
  if (pathname.startsWith("/favoritos")) return "favoritos";
  if (pathname.startsWith("/config")) return "config";
  if (pathname.startsWith("/painel")) return "painel";
  return "home";
}

export function Topbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const active = activeKey(pathname);
  const isAdmin = !!user?.is_admin;
  const name = firstNameFromEmail(user?.email);
  const initials = initialsFromEmail(user?.email);

  async function doLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link href="/" className="topbar-brand no-underline">
          <BrandMark />
          <span className="brand-name">SB Clips</span>
          <span className="topbar-tag">CLIPES · SB</span>
        </Link>

        <nav className="topnav">
          {NAV.map((n) => (
            <Link
              key={n.key}
              href={n.href}
              className={`nav-item${active === n.key ? " is-active" : ""}`}
            >
              <span>{n.label}</span>
            </Link>
          ))}
          {isAdmin && (
            <Link
              href="/painel"
              className={`nav-item${active === "painel" ? " is-active" : ""}`}
            >
              <span>Painel</span>
            </Link>
          )}
        </nav>

        <div className="topbar-user">
          <div className="user-chip">
            <span className="user-name">{name}</span>
            <span className="user-avatar" aria-hidden="true">
              {initials}
            </span>
          </div>
          <Button variant="ghost" size="icon" onClick={doLogout} aria-label="Sair" title="Sair">
            <LogOut />
          </Button>
        </div>

        <MobileMenu
          active={active}
          name={name}
          initials={initials}
          isAdmin={isAdmin}
          onLogout={doLogout}
        />
      </div>
    </header>
  );
}
