import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * V3Progress: barra de progresso do redesign v3. Trilho neutro (borda v3-border) e
 * preenchimento solido no acento, sem o brilho "cometa" da versao antiga (.upload-bar).
 */
function V3Progress({
  value,
  className,
  barClassName,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & { value: number; barClassName?: string }) {
  const pct = Math.min(100, Math.max(0, value))
  return (
    <div
      data-slot="v3-progress"
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn(
        "h-[6px] w-full overflow-hidden rounded-full border border-v3-border bg-v3-surface-1",
        className
      )}
      {...props}
    >
      <div
        className={cn(
          "h-full min-w-[6px] rounded-full bg-v3-accent transition-[width] duration-300 ease-out",
          barClassName
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

export { V3Progress }
