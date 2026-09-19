import { useEffect, useLayoutEffect, useRef } from 'react'

/** Keys are `KeyboardEvent.key` values; single characters are matched lower-cased ('a', '1'). */
export type ShortcutMap = Record<string, (() => void) | undefined>

const TYPING = 'input, textarea, select, [contenteditable="true"]'

/** Keys that act again while held (the OS's key repeat): moving, which is harmless to overshoot. */
const REPEATS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'])

/**
 * Single-key shortcuts that stay out of the way of typing: ignored while focus is in a form field,
 * with Ctrl/Alt/Meta held, or while a modal is open (`[data-modal-open]`). Escape in a field blurs
 * it, so you can type a value and go straight back to shortcuts.
 *
 * Every other key fires once per press: holding A must not accept each document as it comes up.
 */
export function useShortcuts(shortcuts: ShortcutMap, enabled = true) {
  const latest = useRef(shortcuts)
  useLayoutEffect(() => {
    latest.current = shortcuts
  })

  useEffect(() => {
    if (!enabled) return undefined
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return
      const target = event.target instanceof HTMLElement ? event.target : null
      if (target?.closest(TYPING)) {
        if (event.key === 'Escape') target.blur()
        return
      }
      if (document.querySelector('[data-modal-open]')) return
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key
      const handler = latest.current[key]
      if (handler) {
        event.preventDefault()
        if (!event.repeat || REPEATS.has(key)) handler()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled])
}
