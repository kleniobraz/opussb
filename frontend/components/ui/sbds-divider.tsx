import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * SbdsDivider: divisor horizontal do design system oficial (SeuBone Design System,
 * importado via MCP). Espelha o V3Divider, mas com a proporcao propria do novo sistema:
 * 4px por padrao, ou 2px com `fina` (usado, por exemplo, dentro do SbdsBox com `divider`).
 */
function SbdsDivider({
  fina = false,
  className,
  ...props
}: React.ComponentProps<"div"> & { fina?: boolean }) {
  return (
    <div
      data-slot="sbds-divider"
      role="separator"
      aria-orientation="horizontal"
      className={cn("w-full rounded-[2px] bg-sbds-amarelo", fina ? "h-[2px]" : "h-[4px]", className)}
      {...props}
    />
  )
}

export { SbdsDivider }
