import { useSyncExternalStore } from 'react'

/*
 * Suggestions set aside with "Leave as is" (turns.tsx, tilts.tsx) live only while the app is open. Anything
 * else that counts suggestions (the sidebar) re-renders when one is set aside through this.
 */

let revision = 0
const listeners = new Set<() => void>()

/** Say a suggestion was set aside. */
export function setAsideChanged() {
  revision += 1
  for (const listener of listeners) listener()
}

/** Re-render when a suggestion is set aside. */
export function useSetAsideRevision(): number {
  return useSyncExternalStore((listener) => {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }, () => revision)
}
