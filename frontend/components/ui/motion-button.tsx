"use client";

import { FC } from "react";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface MotionButtonProps {
  label: string;
  type?: "button" | "submit";
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}

// Botão com círculo dourado que expande no hover revelando o texto sobre o dourado.
// Tematizado pra identidade SB: gradiente --grad no círculo e texto --on-gold no hover.
const MotionButton: FC<MotionButtonProps> = ({ label, type = "button", onClick, disabled, className }) => {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "group relative h-14 w-full cursor-pointer overflow-hidden rounded-full border border-[var(--border-strong)]",
        "bg-transparent p-1 outline-none transition-opacity disabled:cursor-not-allowed disabled:opacity-60",
        "focus-visible:ring-3 focus-visible:ring-ring/50",
        className,
      )}
    >
      <span
        className="m-0 block h-12 w-12 overflow-hidden rounded-full bg-foreground duration-500 group-hover:w-full group-hover:[background-image:var(--grad)]"
        aria-hidden="true"
      />
      <span className="absolute top-1/2 left-4 -translate-y-1/2 duration-500 group-hover:translate-x-[0.4rem]">
        <ArrowRight className="size-6 text-background group-hover:text-on-gold" />
      </span>
      <span className="absolute top-1/2 left-1/2 ml-4 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-center text-lg font-semibold tracking-tight text-foreground duration-500 group-hover:text-on-gold">
        {label}
      </span>
    </button>
  );
};

export default MotionButton;
