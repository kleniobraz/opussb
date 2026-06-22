import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api";

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["project", id],
    queryFn: () => api.getProject(id),
    enabled: !!id,
  });
}
