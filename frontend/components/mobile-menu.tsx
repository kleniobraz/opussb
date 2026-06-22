"use client";

import { useState } from "react";
import Link from "next/link";
import { Menu } from "lucide-react";
import { Sheet, SheetContent, SheetTrigger, SheetHeader, SheetTitle } from "./ui/sheet";
import { Button } from "./ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Home", key: "home" },
  { href: "/projetos", label: "Projetos", key: "projetos" },
  { href: "/favoritos", label: "Favoritos", key: "favoritos" },
  { href: "/config", label: "Configurações", key: "config" },
];

export function MobileMenu({
  active,
  name,
  initials,
  isAdmin,
  onLogout,
}: {
  active: string;
  name: string;
  initials: string;
  isAdmin?: boolean;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const items = isAdmin ? [...NAV, { href: "/painel", label: "Painel", key: "painel" }] : NAV;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Abrir menu"
          className="min-[761px]:hidden"
        >
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="w-[min(82vw,320px)] gap-1 border-l border-[var(--border)] bg-[var(--surface)] p-[18px] sm:max-w-none"
      >
        <SheetHeader className="flex-row items-center gap-3 border-b border-[var(--border)] p-0 pb-4">
          <span className="user-avatar !h-[38px] !w-[38px] !text-[0.85rem]" aria-hidden="true">
            {initials}
          </span>
          <div className="min-w-0">
            <SheetTitle className="text-[0.95rem] font-semibold text-foreground">{name}</SheetTitle>
            <p className="mt-0.5 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-gold">
              SB Clips
            </p>
          </div>
        </SheetHeader>

        <nav className="mt-2 flex flex-col gap-1">
          {items.map((n) => (
            <Link
              key={n.key}
              href={n.href}
              onClick={() => setOpen(false)}
              className={cn(
                "flex items-center gap-3 rounded-[var(--radius-sm)] px-3.5 py-3 text-[1rem] font-semibold transition-colors",
                active === n.key
                  ? "bg-[rgba(245,197,24,0.12)] text-gold"
                  : "text-ink-soft hover:bg-[rgba(245,197,24,0.12)] hover:text-gold",
              )}
            >
              {n.label}
            </Link>
          ))}
        </nav>

        <Button
          variant="brandGhost"
          className="mt-auto w-full"
          onClick={() => {
            setOpen(false);
            onLogout();
          }}
        >
          Sair
        </Button>
      </SheetContent>
    </Sheet>
  );
}
