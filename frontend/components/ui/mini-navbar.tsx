"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Scissors, LogOut, ChevronDown } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, initialsFromEmail } from "@/lib/format";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface NavItem {
  label: string;
  href: string;
}

// Itens que ficam agrupados no dropdown "Workspace" da barra desktop.
const WORKSPACE_ITEMS: NavItem[] = [
  { label: "Revisor IA", href: "/revisor" },
  { label: "Podcasts", href: "/podcast" },
  { label: "Favoritos", href: "/favoritos" },
];

function navItemsFor(isAdmin: boolean): NavItem[] {
  const base: NavItem[] = [
    { label: "Home", href: "/" },
    { label: "Projetos", href: "/projetos" },
    { label: "Podcasts", href: "/podcast" },
    { label: "Revisor IA", href: "/revisor" },
    { label: "Favoritos", href: "/favoritos" },
    { label: "Configurações", href: "/config" },
  ];
  return isAdmin ? [...base, { label: "Painel", href: "/painel" }] : base;
}

// Itens da barra desktop antes e depois do dropdown "Workspace".
const DESKTOP_ITEMS_BEFORE_WORKSPACE: NavItem[] = [
  { label: "Home", href: "/" },
  { label: "Projetos", href: "/projetos" },
];

function desktopItemsAfterWorkspaceFor(isAdmin: boolean): NavItem[] {
  const base: NavItem[] = [{ label: "Configurações", href: "/config" }];
  return isAdmin ? [...base, { label: "Painel", href: "/painel" }] : base;
}

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

// Link com hover de slide vertical (cinza -> branco). Ativo fica dourado e estático.
function AnimatedNavLink({
  href,
  active,
  children,
  onClick,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
  onClick?: () => void;
}) {
  if (active) {
    return (
      <Link
        href={href}
        onClick={onClick}
        className="relative inline-flex h-5 items-center text-sm font-semibold text-[var(--gold)]"
      >
        {children}
      </Link>
    );
  }
  return (
    <Link
      href={href}
      onClick={onClick}
      className="group relative block h-5 overflow-hidden text-sm"
    >
      <span className="flex flex-col transition-transform duration-300 ease-out group-hover:-translate-y-1/2">
        <span className="flex h-5 items-center whitespace-nowrap text-[var(--ink-soft)]">{children}</span>
        <span className="flex h-5 items-center whitespace-nowrap text-[var(--foreground)]">{children}</span>
      </span>
    </Link>
  );
}

export function MiniNavbar() {
  const router = useRouter();
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const isAdmin = !!user?.is_admin;
  const items = navItemsFor(isAdmin);
  const desktopItemsAfterWorkspace = desktopItemsAfterWorkspaceFor(isAdmin);
  const workspaceActive = WORKSPACE_ITEMS.some((it) => isActive(pathname, it.href));
  const name = firstNameFromEmail(user?.email);
  const initials = initialsFromEmail(user?.email);

  const [isOpen, setIsOpen] = useState(false);
  const [shapeClass, setShapeClass] = useState("rounded-full");
  const shapeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (shapeTimeout.current) clearTimeout(shapeTimeout.current);
    if (isOpen) setShapeClass("rounded-[26px]");
    else shapeTimeout.current = setTimeout(() => setShapeClass("rounded-full"), 300);
    return () => {
      if (shapeTimeout.current) clearTimeout(shapeTimeout.current);
    };
  }, [isOpen]);

  async function doLogout() {
    setIsOpen(false);
    await logout();
    router.push("/login");
  }

  const avatar = (
    <span
      className="grid size-7 place-items-center rounded-full text-[0.7rem] font-bold text-[var(--on-gold)]"
      style={{ background: "var(--grad)" }}
    >
      {initials}
    </span>
  );

  const logoutBtn = (full = false) => (
    <button
      onClick={doLogout}
      className={`inline-flex items-center justify-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-4 py-1.5 text-sm text-[var(--ink-soft)] transition-colors hover:border-[rgba(245,197,24,0.5)] hover:text-[var(--gold)] ${full ? "w-full" : ""}`}
    >
      <LogOut className="size-4" />
      Sair
    </button>
  );

  return (
    <header
      className={`fixed left-1/2 top-5 z-50 flex w-[calc(100%-2rem)] -translate-x-1/2 flex-col items-center border border-white/10 px-5 py-2.5 backdrop-blur-md transition-[border-radius] duration-200 sm:w-auto ${shapeClass}`}
      style={{ background: "rgba(17,17,19,0.72)" }}
    >
      <div className="flex w-full items-center justify-between gap-x-6 sm:gap-x-9">
        <Link
          href="/"
          onClick={() => setIsOpen(false)}
          className="flex items-center gap-2.5 no-underline"
        >
          <span
            className="grid size-8 place-items-center rounded-lg text-[var(--on-gold)]"
            style={{ background: "var(--grad)" }}
          >
            <Scissors size={17} strokeWidth={2.1} />
          </span>
          <span className="text-[0.95rem] font-bold tracking-tight text-[var(--foreground)]">SB Clips</span>
        </Link>

        <nav className="hidden items-center gap-6 sm:flex">
          {DESKTOP_ITEMS_BEFORE_WORKSPACE.map((it) => (
            <AnimatedNavLink key={it.href} href={it.href} active={isActive(pathname, it.href)}>
              {it.label}
            </AnimatedNavLink>
          ))}

          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label="Menu Workspace"
              className={`group relative inline-flex h-5 items-center gap-1 text-sm outline-none ${
                workspaceActive ? "font-semibold text-[var(--gold)]" : "text-[var(--ink-soft)]"
              }`}
            >
              Workspace
              <ChevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="center">
              {WORKSPACE_ITEMS.map((it) => (
                <DropdownMenuItem key={it.href} asChild>
                  <Link
                    href={it.href}
                    className={isActive(pathname, it.href) ? "font-semibold text-[var(--gold)]" : undefined}
                  >
                    {it.label}
                  </Link>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {desktopItemsAfterWorkspace.map((it) => (
            <AnimatedNavLink key={it.href} href={it.href} active={isActive(pathname, it.href)}>
              {it.label}
            </AnimatedNavLink>
          ))}
        </nav>

        <div className="hidden items-center gap-3 sm:flex">
          <span className="hidden items-center gap-2 lg:flex">
            {avatar}
            <span className="text-sm text-[var(--ink-soft)]">{name}</span>
          </span>
          {logoutBtn()}
        </div>

        <button
          className="flex size-8 items-center justify-center text-[var(--ink-soft)] outline-none sm:hidden"
          onClick={() => setIsOpen(!isOpen)}
          aria-label={isOpen ? "Fechar menu" : "Abrir menu"}
        >
          {isOpen ? (
            <svg className="size-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className="size-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          )}
        </button>
      </div>

      <div
        className={`flex w-full flex-col items-center overflow-hidden transition-all duration-300 ease-in-out sm:hidden ${
          isOpen ? "max-h-[440px] pt-4 opacity-100" : "pointer-events-none max-h-0 pt-0 opacity-0"
        }`}
      >
        <nav className="flex w-full flex-col items-center gap-3.5 text-base">
          {items.map((it) => (
            <Link
              key={it.href}
              href={it.href}
              onClick={() => setIsOpen(false)}
              className="w-full text-center font-medium transition-colors"
              style={{ color: isActive(pathname, it.href) ? "var(--gold)" : "var(--ink-soft)" }}
            >
              {it.label}
            </Link>
          ))}
        </nav>
        <div className="mt-4 flex w-full flex-col items-center gap-3 border-t border-white/10 pt-4">
          <span className="flex items-center gap-2">
            {avatar}
            <span className="text-sm text-[var(--ink-soft)]">{name}</span>
          </span>
          {logoutBtn(true)}
        </div>
      </div>
    </header>
  );
}

export default MiniNavbar;
