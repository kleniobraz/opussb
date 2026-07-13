import type { LucideIcon } from "lucide-react";
import { Home, Scissors, Folder, Podcast, Heart, Settings, Shield } from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

// Itens base do menu, na mesma ordem usada hoje (Home -> Config). "Painel" so entra
// pra admin (ver navItemsFor); mesma logica de gate que o MiniNavbar ja tinha.
const BASE_NAV_ITEMS: NavItem[] = [
  { label: "Home", href: "/", icon: Home },
  { label: "Gerar clipes", href: "/gerar", icon: Scissors },
  { label: "Projetos", href: "/projetos", icon: Folder },
  { label: "Podcasts", href: "/podcast", icon: Podcast },
  { label: "Favoritos", href: "/favoritos", icon: Heart },
  { label: "Configurações", href: "/config", icon: Settings },
];

const ADMIN_NAV_ITEM: NavItem = { label: "Painel", href: "/painel", icon: Shield };

export function navItemsFor(isAdmin: boolean): NavItem[] {
  return isAdmin ? [...BASE_NAV_ITEMS, ADMIN_NAV_ITEM] : BASE_NAV_ITEMS;
}

export function isActiveNavItem(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}
