import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/** Keys are `KeyboardEvent.key` values; single characters are matched lower-cased ('a', '1'). */
export type ShortcutMap = Record<string, (() => void) | undefined>

const TYPING = 'input, textarea, select, [contenteditable="true"]'

/** The same guards the shortcuts use: typing, a modifier chord or an open modal is not a shortcut. */
const isShortcutKey = (event: KeyboardEvent) =>
  !event.defaultPrevented && !event.ctrlKey && !event.metaKey && !event.altKey &&
  !(event.target instanceof HTMLElement && event.target.closest(TYPING)) &&
  !document.querySelector('[data-modal-open]')

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

/**
 * Whether `key` is being held right now — for a look-underneath, like lifting the boxes off a scan
 * to read the text they cover. Press and it is true, release and it is false again.
 *
 * Releasing the key somewhere this window never hears about (alt-tab, a click into the devtools)
 * would otherwise leave it stuck down, so losing the window or the tab lets go as well.
 */
export function useHeldKey(key: string, enabled = true): boolean {
  const [held, setHeld] = useState(false)

  useEffect(() => {
    if (!enabled) {
      setHeld(false)
      return undefined
    }
    const matches = (event: KeyboardEvent) =>
      (event.key.length === 1 ? event.key.toLowerCase() : event.key) === key
    const onKeyDown = (event: KeyboardEvent) => {
      if (!matches(event) || !isShortcutKey(event)) return
      event.preventDefault()
      setHeld(true)
    }
    // No guards on the way up: whatever happened in between, the key is no longer down.
    const onKeyUp = (event: KeyboardEvent) => {
      if (matches(event)) setHeld(false)
    }
    const letGo = () => setHeld(false)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', letGo)
    document.addEventListener('visibilitychange', letGo)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', letGo)
      document.removeEventListener('visibilitychange', letGo)
    }
  }, [key, enabled])

  return held
}
