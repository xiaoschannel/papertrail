import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client.ts'
import type { AppConfig } from './types.ts'

/** The saved settings (`config.json`). One query key, so every page sees the same values. */
export const CONFIG_KEY = ['config'] as const

export function useConfig() {
  return useQuery({ queryKey: CONFIG_KEY, queryFn: api.config })
}

/**
 * Save a few settings. Only the given fields are sent (PATCH), so a page remembering its own view
 * can't revert a model another page just chose, and the fresh config replaces the cached one.
 */
export function useSaveConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<AppConfig>) => api.patchConfig(patch),
    onSuccess: (fresh) => queryClient.setQueryData(CONFIG_KEY, fresh),
  })
}
