"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Menu, LogOut } from "lucide-react";
import { Sheet, SheetContent, SheetTrigger, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, initialsFromEmail } from "@/lib/format";
import { navItemsFor, isActiveNavItem } from "@/components/nav-items";
import { cn } from "@/lib/utils";

// Barra superior + drawer do menu no mobile (abaixo de lg). Mesmos itens da Sidebar
// desktop, sempre com icone + label (aqui nao existe versao colapsada).
export function MobileSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  const items = navItemsFor(!!user?.is_admin);
  const name = firstNameFromEmail(user?.email);
  const initials = initialsFromEmail(user?.email);

  async function doLogout() {
    setOpen(false);
    await logout();
    router.push("/login");
  }

  return (
    <header className="flex items-center justify-between border-b border-v3-border bg-v3-surface-1 px-4 py-3 lg:hidden">
      <Link href="/" onClick={() => setOpen(false)} className="flex items-center gap-2.5 no-underline">
        <BrandMark />
        <span className="text-[0.95rem] font-bold tracking-tight text-v3-text">SB Clips</span>
      </Link>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Abrir menu">
            <Menu />
          </Button>
        </SheetTrigger>
        {/* Sem override de bg/borda: o SheetContent ja vem de vidro (v3-glass) e um
            fundo solido aqui esconderia o efeito. */}
        <SheetContent
          side="right"
          className="flex w-[min(82vw,320px)] flex-col gap-1 p-[18px] sm:max-w-none"
        >
          <SheetHeader className="flex-row items-center gap-3 border-b border-v3-border p-0 pb-4">
            <span
              className="grid size-9 shrink-0 place-items-center rounded-full text-[0.8rem] font-bold text-[var(--on-gold)]"
              style={{ background: "var(--grad)" }}
            >
              {initials}
            </span>
            <div className="min-w-0">
              <SheetTitle className="text-[0.95rem] font-semibold text-v3-text">{name}</SheetTitle>
              <p className="mt-0.5 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-v3-accent">
                SB Clips
              </p>
            </div>
          </SheetHeader>

          <nav className="mt-2 flex flex-1 flex-col gap-1 overflow-y-auto">
            {items.map((item) => {
              const active = isActiveNavItem(pathname, item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className={cn(
                    "flex items-center gap-3 rounded-[var(--radius-sm)] px-3.5 py-3 text-[0.95rem] font-medium transition-colors",
                    active
                      ? "bg-v3-accent/12 text-v3-accent"
                      : "text-v3-text-soft hover:bg-v3-ghost hover:text-v3-text"
                  )}
                >
                  <Icon className="size-5 shrink-0" strokeWidth={2} />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <button
            onClick={doLogout}
            className="mt-auto flex w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] border border-v3-border-hover px-4 py-2.5 text-sm font-medium text-v3-text-soft transition-colors hover:border-v3-accent hover:text-v3-accent"
          >
            <LogOut className="size-4" />
            Sair
          </button>
        </SheetContent>
      </Sheet>
    </header>
  );
}

export default MobileSidebar;
