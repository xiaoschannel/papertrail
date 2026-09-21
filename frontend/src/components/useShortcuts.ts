import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * Keys are `KeyboardEvent.key` values, single characters lower-cased ('a', '1', ' ' for Space) and
 * named keys as named ('Enter', 'ArrowLeft') — the same form the config's `shortcuts` are stored in.
 */
export type ShortcutMap = Record<string, (() => void) | undefined>

/** The key an event is for, in `ShortcutMap`'s form (Shift+A is still 'a'). */
export const keyOf = (event: KeyboardEvent | { key: string }) =>
  event.key.length === 1 ? event.key.toLowerCase() : event.key

const NAMES: Record<string, string> = {
  ' ': 'Space', Escape: 'Esc', ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓',
  PageUp: 'PgUp', PageDown: 'PgDn', Delete: 'Del', Insert: 'Ins',
}

/** How a key is written on screen, as on the keycap. */
export const keyLabel = (key: string) => NAMES[key] ?? (key.length === 1 ? key.toUpperCase() : key)

const TYPING = 'input, textarea, select, [contenteditable="true"]'

/**
 * Enter or Space on a focused button or link: it presses that, so it is not a shortcut even when a
 * shortcut is set to that key — what Tab reached is what the key was meant for.
 */
export const pressesFocused = (event: KeyboardEvent) =>
  (event.key === 'Enter' || event.key === ' ') &&
  event.target instanceof HTMLElement && Boolean(event.target.closest('button, a[href], summary'))

/** The same guards the shortcuts use: typing, a modifier chord or an open modal is not a shortcut. */
const isShortcutKey = (event: KeyboardEvent) =>
  !event.defaultPrevented && !event.ctrlKey && !event.metaKey && !event.altKey &&
  !(event.target instanceof HTMLElement && event.target.closest(TYPING)) && !pressesFocused(event) &&
  !document.querySelector('[data-modal-open]')

/**
 * Single-key shortcuts that stay out of the way of typing: ignored while focus is in a form field,
 * with Ctrl/Alt/Meta held, or while a modal is open (`[data-modal-open]`). Escape in a field blurs
 * it, so you can type a value and go straight back to shortcuts.
 *
 * Each key fires once per press (holding A must not accept each document as it comes up), except the
 * `repeating` ones, which act again with the OS's key repeat: moving, which is harmless to overshoot.
 */
export function useShortcuts(shortcuts: ShortcutMap, enabled = true, repeating: readonly string[] = []) {
  const latest = useRef({ shortcuts, repeating })
  useLayoutEffect(() => {
    latest.current = { shortcuts, repeating }
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
      if (document.querySelector('[data-modal-open]') || pressesFocused(event)) return
      const key = keyOf(event)
      const handler = latest.current.shortcuts[key]
      if (handler) {
        event.preventDefault()
        if (!event.repeat || latest.current.repeating.includes(key)) handler()
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
    const matches = (event: KeyboardEvent) => keyOf(event) === key
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
