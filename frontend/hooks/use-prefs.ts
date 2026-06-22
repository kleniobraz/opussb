import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import type { Preferences } from "@/lib/types";

export const DEFAULT_PREFS: Preferences = {
  defaultMin: 30,
  defaultMax: 90,
  aspect: "9:16",
  language: "pt",
  captions: true,
  autoplayPreview: false,
};

export function usePrefs() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["prefs"], queryFn: api.getPreferences });
  const prefs: Preferences = { ...DEFAULT_PREFS, ...(query.data ?? {}) };
  const save = useMutation({
    mutationFn: (patch: Partial<Preferences>) => api.putPreferences({ ...prefs, ...patch }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["prefs"] }),
  });
  return { prefs, isLoaded: query.isSuccess, save };
}
