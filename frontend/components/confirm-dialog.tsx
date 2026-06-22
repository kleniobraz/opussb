"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Button } from "./ui/button";

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  message,
  okLabel = "Confirmar",
  cancelLabel = "Cancelar",
  danger = false,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  message?: string;
  okLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[420px] border-[var(--border)] bg-[var(--surface)]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {message && <p className="modal-text">{message}</p>}
        <div className="modal-actions">
          <Button variant="brandGhost" onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? "destructive" : "gold"}
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            {okLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
