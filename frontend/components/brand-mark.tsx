import { Scissors } from "lucide-react";
import { cn } from "@/lib/utils";

// Marca OpusSB: tesoura ("Corte Viral") sobre o quadrado com o gradiente da casa.
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cn("brand-mark", className)} aria-hidden="true">
      <Scissors strokeWidth={2.1} />
    </span>
  );
}
