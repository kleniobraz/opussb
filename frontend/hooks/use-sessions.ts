import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

export function useSessions() {
  return useQuery({ queryKey: ["sessions"], queryFn: api.getSessions });
}

export function useRevokeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}

export function useRevokeAllSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.revokeAllSessions(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}
