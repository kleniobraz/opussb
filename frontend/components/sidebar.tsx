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

// Persiste a preferencia de coluna colapsada (mesmo padrao do editor pra
// timelineH: chave prefixada, leitura protegida por try/catch).
const COLLAPSE_KEY = "opussb_sidebar_collapsed";

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      // localStorage indisponivel: mantem expandida
    }
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        // ignora
      }
      return next;
    });
  }

  async function doLogout() {
    await logout();
    router.push("/login");
  }

  const items = navItemsFor(!!user?.is_admin);
  const name = firstNameFromEmail(user?.email);
  const initials = initialsFromEmail(user?.email);

  return (
    <aside
      className={cn(
        "hidden h-full flex-col border-r border-v3-border bg-v3-surface-1 transition-[width] duration-200 lg:flex",
        collapsed ? "w-[72px]" : "w-64"
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
            "flex items-center gap-3 rounded-[var(--radius-sm)] py-2.5 text-sm font-medium transition-colors",
            collapsed ? "justify-center px-0" : "px-3",
            active
              ? "bg-v3-accent/12 text-v3-accent"
              : "text-v3-text-soft hover:bg-v3-ghost hover:text-v3-text"
          );

          if (collapsed) {
            return (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>
                  <Link href={item.href} className={linkClass}>
                    <Icon className="size-5 shrink-0" strokeWidth={2} />
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            );
          }

          return (
            <Link key={item.href} href={item.href} className={linkClass}>
              <Icon className="size-5 shrink-0" strokeWidth={2} />
              <span className="truncate">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-v3-border p-3">
        <button
          onClick={toggleCollapsed}
          className="mb-2 flex w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] py-2 text-v3-text-soft transition-colors hover:bg-v3-ghost hover:text-v3-text"
          aria-label={collapsed ? "Expandir menu" : "Recolher menu"}
        >
          {collapsed ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
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
