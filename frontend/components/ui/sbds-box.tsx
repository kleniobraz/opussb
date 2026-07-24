import * as React from "react"

import { cn } from "@/lib/utils"
import { SbdsDivider } from "@/components/ui/sbds-divider"

/**
 * SbdsBox: cartao do design system oficial (SeuBone Design System, importado via MCP),
 * por enquanto so na tela de corte de inicio do podcast. Independente do Card do shadcn
 * e do V3Card (nao estende nem substitui nenhum dos dois). `claro` troca pro fundo branco/
 * texto preto sem borda nem sombra; `divider` acrescenta a barra fina amarela acima do
 * conteudo (reaproveita o SbdsDivider em vez de duplicar a receita da barra aqui).
 */
function SbdsBox({
  className,
  claro = false,
  divider = false,
  children,
  ...props
}: React.ComponentProps<"div"> & { claro?: boolean; divider?: boolean }) {
  return (
    <div
      data-slot="sbds-box"
      data-claro={claro}
      className={cn(
        "rounded-sbds-md p-6",
        claro
          ? "bg-sbds-branco text-sbds-preto"
          : "border border-sbds-border-subtle bg-sbds-surface-card text-sbds-branco shadow-sbds-card",
        className
      )}
      {...props}
    >
      {divider && <SbdsDivider fina className="mb-4" />}
      {children}
    </div>
  )
}

export { SbdsBox }
