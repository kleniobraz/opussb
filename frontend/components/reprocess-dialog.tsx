"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import * as api from "@/lib/api";
import { usePrefs } from "@/hooks/use-prefs";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";

export function ReprocessDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { prefs } = usePrefs();
  const router = useRouter();
  const [min, setMin] = useState(prefs.defaultMin);
  const [max, setMax] = useState(prefs.defaultMax);
  const [captions, setCaptions] = useState(prefs.captions);

  // Ao abrir, parte dos padroes atuais.
  useEffect(() => {
    if (open) {
      setMin(prefs.defaultMin);
      setMax(prefs.defaultMax);
      setCaptions(prefs.captions);
    }
  }, [open, prefs.defaultMin, prefs.defaultMax, prefs.captions]);

  async function go() {
    if (min >= max) {
      toast.error("A duração mínima deve ser menor que a máxima.");
      return;
    }
    try {
      const res = await api.reprocess(projectId, {
        min_duration: min,
        max_duration: max,
        captions,
      });
      onOpenChange(false);
      if (res?.job_id) {
        toast.success("Reprocessamento iniciado");
        router.push("/");
      } else {
        toast.success("Reprocessamento solicitado");
      }
    } catch {
      onOpenChange(false);
      toast.info("Reprocessar aguarda o backend (POST /api/projects/:id/reprocess).", {
        duration: 6000,
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[420px] border-[var(--border)] bg-[var(--surface)]">
        <DialogHeader>
          <DialogTitle>Reprocessar projeto</DialogTitle>
        </DialogHeader>
        <p className="modal-text">Gerar novos clipes com outros parâmetros.</p>
        <div className="field row flex gap-3.5">
          <div className="flex-1">
            <label className="ctrl-label" htmlFor="rp-min">
              Duração mínima (s)
            </label>
            <Input
              id="rp-min"
              type="number"
              min={5}
              max={300}
              value={min}
              onChange={(e) => setMin(parseInt(e.target.value, 10) || 0)}
            />
          </div>
          <div className="flex-1">
            <label className="ctrl-label" htmlFor="rp-max">
              Duração máxima (s)
            </label>
            <Input
              id="rp-max"
              type="number"
              min={10}
              max={600}
              value={max}
              onChange={(e) => setMax(parseInt(e.target.value, 10) || 0)}
            />
          </div>
        </div>
        <label className="mt-3 flex cursor-pointer items-center gap-3 text-[0.9rem] text-ink-soft">
          <Switch checked={captions} onCheckedChange={setCaptions} />
          <span>Gerar legendas</span>
        </label>
        <div className="modal-actions">
          <Button variant="brandGhost" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button variant="gold" onClick={go}>
            Reprocessar
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
