import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * V3Card: cartao base do redesign v3, separado do Card do shadcn (nao estende
 * nem substitui). Com `glass`, a superficie solida da lugar a receita .v3-glass
 * (veu alfa + backdrop-blur, ver brand-v3.css); use apenas onde ha conteudo
 * visivel atras do cartao. O default continua identico ao original.
 */
function V3Card({
  className,
  active = false,
  glass = false,
  ...props
}: React.ComponentProps<"div"> & { active?: boolean; glass?: boolean }) {
  return (
    <div
      data-slot="v3-card"
      data-active={active}
      className={cn(
        "rounded-[var(--radius-md)] transition-colors",
        glass ? "v3-glass" : "border border-v3-border bg-v3-surface-2",
        active && (glass ? "v3-glass-active" : "border-2 border-v3-accent v3-glow-active"),
        className
      )}
      {...props}
    />
  )
}

export { V3Card }
