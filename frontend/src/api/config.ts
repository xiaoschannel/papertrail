import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client.ts'
import type { AppConfig } from './types.ts'

/** The saved settings (`config.json`). One query key, so every page sees the same values. */
export const CONFIG_KEY = ['config'] as const

export function useConfig() {
  return useQuery({ queryKey: CONFIG_KEY, queryFn: api.config })
}

/** The app's shortcut keys (Config's Shortcuts). Undefined until the config has loaded: no keys till then. */
export function useShortcutKeys() {
  return useConfig().data?.shortcuts
}

/**
 * Save a few settings. Only the given fields are sent (PATCH), so a page remembering its own view
 * can't revert a model another page just chose, and the fresh config replaces the cached one. Pages whose
 * answers carry a setting (OCR's and Parse's status, the Experiment bench) are re-read too.
 */
export function useSaveConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<AppConfig>) => api.patchConfig(patch),
    onSuccess: (fresh) => {
      queryClient.setQueryData(CONFIG_KEY, fresh)
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })
      void queryClient.invalidateQueries({ queryKey: ['dev', 'experiment'], exact: true })
    },
  })
}
