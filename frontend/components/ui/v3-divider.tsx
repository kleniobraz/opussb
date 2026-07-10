import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * V3Divider: o "divisor vivo" do spec v3, linha fina na cor do acento.
 * Fundacao apenas, ainda sem uso em nenhuma tela.
 */
function V3Divider({
  orientation = "horizontal",
  className,
  ...props
}: React.ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      data-slot="v3-divider"
      data-orientation={orientation}
      role="separator"
      aria-orientation={orientation}
      className={cn(
        "bg-v3-accent",
        orientation === "horizontal" ? "h-[2px] w-full" : "h-full w-[2px]",
        className
      )}
      {...props}
    />
  )
}

export { V3Divider }
