"use client";

import type React from "react";

interface ShinyButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
}

// Botão CTA "shiny" (borda em gradiente cônico animado + shimmer). Os estilos vivem
// em app/brand.css na classe .shiny-cta (não usamos styled-jsx p/ não depender dele).
export function ShinyButton({ children, onClick, type = "button", className = "" }: ShinyButtonProps) {
  return (
    <button type={type} className={`shiny-cta ${className}`} onClick={onClick}>
      <span>{children}</span>
    </button>
  );
}

export default ShinyButton;
