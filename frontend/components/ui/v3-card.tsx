import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * V3Card: cartao base do redesign v3, separado do Card do shadcn (nao estende
 * nem substitui). Fundacao apenas, ainda sem uso em nenhuma tela.
 */
function V3Card({
  className,
  active = false,
  ...props
}: React.ComponentProps<"div"> & { active?: boolean }) {
  return (
    <div
      data-slot="v3-card"
      data-active={active}
      className={cn(
        "rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2 transition-colors",
        active && "border-2 border-v3-accent v3-glow-active",
        className
      )}
      {...props}
    />
  )
}

export { V3Card }
