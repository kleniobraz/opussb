"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronsLeft, ChevronsRight, LogOut } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, initialsFromEmail } from "@/lib/format";
import { navItemsFor, isActiveNavItem } from "@/components/nav-items";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// Persiste se o usuario fixou a sidebar expandida (pin). O padrao (sem
// preferencia salva) e colapsada; passar o mouse por cima expande de forma
// temporaria, sem alterar essa preferencia.
const PINNED_KEY = "opussb_sidebar_pinned";

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);

  useEffect(() => {
    try {
      setPinned(localStorage.getItem(PINNED_KEY) === "1");
    } catch {
      // localStorage indisponivel: mantem o padrao (colapsada)
    }
  }, []);

  function togglePinned() {
    setPinned((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(PINNED_KEY, next ? "1" : "0");
      } catch {
        // ignora
      }
      return next;
    });
  }

  // Fixada (pin) sempre expandida; senao, o hover expande por cima do estado
  // colapsado de base, sem persistir nada.
  const expanded = pinned || hovering;
  const collapsed = !expanded;

  async function doLogout() {
    await logout();
    router.push("/login");
  }

  const items = navItemsFor(!!user?.is_admin);
  const name = firstNameFromEmail(user?.email);
  const initials = initialsFromEmail(user?.email);

  return (
    <aside
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={cn(
        "hidden h-full flex-col border-r border-v3-border bg-v3-bg transition-[width] duration-150 lg:flex",
        collapsed ? "w-20" : "w-64"
      )}
    >
      <div className={cn("flex items-center gap-2.5 px-4 py-5", collapsed && "justify-center px-0")}>
        <Link href="/" className="flex min-w-0 items-center gap-2.5 no-underline">
          <BrandMark />
          {!collapsed && (
            <span className="truncate text-[0.95rem] font-bold tracking-tight text-v3-text">
              SB Clips
            </span>
          )}
        </Link>
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-2">
        {items.map((item) => {
          const active = isActiveNavItem(pathname, item.href);
          const Icon = item.icon;
          const linkClass = cn(
            "relative flex items-center gap-3 rounded-[var(--radius-sm)] text-sm font-medium transition-colors",
            collapsed ? "justify-center px-0 py-3" : "px-3 py-2.5",
            active
              ? "bg-v3-accent/14 text-v3-accent"
              : "text-v3-text-soft hover:bg-v3-ghost hover:text-v3-text"
          );
          // Barra fina de acento na lateral do item ativo, reforca o destaque
          // agora que o icone e o protagonista na coluna colapsada.
          const activeBar = active && (
            <span
              aria-hidden="true"
              className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-v3-accent"
            />
          );

          if (collapsed) {
            // O Tooltip aqui nunca aparece por mouse: o mesmo onMouseEnter que
            // abriria o balao tambem expande a sidebar inteira (linha ~62),
            // entao o item troca pro ramo expandido antes do delay do Radix
            // terminar. Fica so pra navegacao por teclado (Tab foca o link e
            // o Radix abre o tooltip por foco, sem passar pelo onMouseEnter
            // do aside).
            return (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>
                  <Link href={item.href} className={linkClass}>
                    {activeBar}
                    <Icon className="size-6 shrink-0" strokeWidth={2} />
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            );
          }

          return (
            <Link key={item.href} href={item.href} className={linkClass}>
              {activeBar}
              <Icon className="size-6 shrink-0" strokeWidth={2} />
              <span className="truncate">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-v3-border p-3">
        <button
          onClick={togglePinned}
          className="mb-2 flex w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] py-2 text-v3-text-soft transition-colors hover:bg-v3-ghost hover:text-v3-text"
          aria-label={pinned ? "Recolher menu" : "Fixar menu expandido"}
        >
          {pinned ? <ChevronsLeft className="size-4" /> : <ChevronsRight className="size-4" />}
        </button>

        <div className={cn("flex items-center gap-2.5", collapsed && "justify-center")}>
          <span
            className="grid size-8 shrink-0 place-items-center rounded-full text-[0.7rem] font-bold text-[var(--on-gold)]"
            style={{ background: "var(--grad)" }}
          >
            {initials}
          </span>
          {!collapsed && <span className="truncate text-sm text-v3-text-soft">{name}</span>}
        </div>

        {collapsed ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={doLogout}
                aria-label="Sair"
                className="mt-2 flex w-full items-center justify-center rounded-[var(--radius-sm)] py-2 text-v3-text-soft transition-colors hover:bg-v3-ghost hover:text-v3-accent"
              >
                <LogOut className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">Sair</TooltipContent>
          </Tooltip>
        ) : (
          <button
            onClick={doLogout}
            className="mt-2 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2 text-sm text-v3-text-soft transition-colors hover:bg-v3-ghost hover:text-v3-accent"
          >
            <LogOut className="size-4" />
            Sair
          </button>
        )}
      </div>
    </aside>
  );
}

export default Sidebar;
