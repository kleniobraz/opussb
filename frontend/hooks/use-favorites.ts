import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

export function useFavorites() {
  return useQuery({ queryKey: ["favorites"], queryFn: api.getFavorites });
}

export function useToggleFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => api.favoriteClip(id, on),
    onSettled: () => qc.invalidateQueries({ queryKey: ["favorites"] }),
  });
}
