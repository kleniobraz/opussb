"use client";

import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { QueryProvider } from "./query-provider";
import { AuthProvider } from "./auth-provider";

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <QueryProvider>
      <AuthProvider>
        <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
        <Toaster position="bottom-right" theme="dark" richColors closeButton />
      </AuthProvider>
    </QueryProvider>
  );
}
