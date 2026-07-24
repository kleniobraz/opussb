import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
        // Variantes do design system oficial (SeuBone Design System, importado via MCP),
        // por enquanto so na tela de corte de inicio do podcast. A base ja e pill
        // (rounded-4xl), entao nenhuma variante abaixo precisa mexer em radius. Border em
        // colchete (outline/neutro) pelo mesmo motivo do Button: garante o dedupe contra o
        // border-transparent da base.
        sbdsSolid:
          "h-auto border-transparent bg-sbds-amarelo px-2.5 py-1 text-[12.5px] font-bold text-sbds-ink-on-amarelo uppercase tracking-[0.08em]",
        sbdsOutline:
          "h-auto border-[var(--sbds-amarelo)] bg-transparent px-2.5 py-1 text-[12.5px] font-bold text-sbds-amarelo uppercase tracking-[0.08em]",
        sbdsNeutro:
          "h-auto border-[var(--sbds-border-subtle)] bg-sbds-surface-card px-2.5 py-1 text-[12.5px] font-bold text-sbds-branco uppercase tracking-[0.08em]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
