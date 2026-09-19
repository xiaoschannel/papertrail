import type { QueryClient } from '@tanstack/react-query'

/**
 * What to re-read after an edit changed the archive.
 *
 * `own` is refetched now — it is what the page in front of you shows. Everything else is only marked
 * stale, so it re-reads when you next open it. Refetching the lot on every click means rebuilding the
 * archive-derived frames (seconds on a real archive) before the page can respond, which reads as the
 * whole page freezing after a one-document change.
 */
export function afterArchiveEdit(queryClient: QueryClient, own: readonly unknown[]) {
  void queryClient.invalidateQueries({ queryKey: own })
  void queryClient.invalidateQueries({
    predicate: (query) => {
      const root = query.queryKey[0]
      if (root === 'config' || root === 'job') return false          // not derived from the archive
      return !own.every((part, index) => query.queryKey[index] === part)
    },
    refetchType: 'none',
  })
}
