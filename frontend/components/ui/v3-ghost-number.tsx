import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * V3GhostNumber: numero/texto gigante em outline sutil, pensado pra ficar
 * atras de outro conteudo (o proprio consumidor controla posicionamento e
 * z-index via className). Fundacao apenas, ainda sem uso em nenhuma tela.
 */
function V3GhostNumber({
  value,
  children,
  className,
  ...props
}: React.ComponentProps<"span"> & { value?: React.ReactNode }) {
  return (
    <span
      data-slot="v3-ghost-number"
      aria-hidden="true"
      className={cn(
        "pointer-events-none block font-v3-display text-[16rem] leading-none font-black text-v3-ghost select-none",
        className
      )}
      {...props}
    >
      {value ?? children}
    </span>
  )
}

export { V3GhostNumber }
