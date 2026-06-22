import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api";

// Polling do status do job (5s) ate done/error, no estilo declarativo do TanStack Query.
export function useJobStatus(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.status(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "error" ? false : 5000;
    },
  });
}
