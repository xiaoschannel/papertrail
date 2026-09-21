import { useEffect, useRef, type ReactNode } from 'react'
import { useShortcutKeys } from '../api/config.ts'
import { keyLabel, keyOf, pressesFocused } from './useShortcuts.ts'

const TYPING = 'input, textarea, select, [contenteditable="true"]'

/**
 * A small modal. The Confirm and Cancel shortcuts (Config's Shortcuts: E and Q unless changed) or the
 * backdrop answer it. They fire once per press, so the key that opened the dialog, still held, can't also
 * confirm it; `busy` disables confirming, so a second press can't send the request twice.
 *
 * Focus moves to the dialog itself, not a button, so no key confirms unless it was chosen to: Enter
 * presses a button only once Tab has been used to reach one. Focus stays inside while it is open (Tab
 * cycles its buttons, so nothing behind it can be reached) and returns to where it was when it closes.
 */
export function ConfirmDialog({ title, children, confirmLabel = 'Confirm', danger = false, busy = false, onConfirm, onCancel }: {
  title: string
  children: ReactNode
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const keys = useShortcutKeys()
  const dialog = useRef<HTMLDivElement>(null)
  const latest = useRef({ keys, busy, onConfirm, onCancel })
  useEffect(() => {
    latest.current = { keys, busy, onConfirm, onCancel }
  })

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      const { keys: own, busy: sending } = latest.current
      const key = keyOf(event)
      const typing = event.target instanceof HTMLElement && event.target.closest(TYPING)
      // A button reached with Tab is the answer, even when Confirm or Cancel is set to Enter or Space.
      if (own && (key === own.confirm || key === own.cancel) && !typing && !pressesFocused(event) &&
          !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault()
        if (event.repeat) return
        if (key === own.cancel) latest.current.onCancel()
        else if (!sending) latest.current.onConfirm()
        return
      }
      if (event.key !== 'Tab' || !dialog.current) return
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input, select, textarea')]
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) return
      // On the dialog itself (as it opens) is not yet on a button: Tab goes to the first, Shift+Tab the last.
      const onOne = document.activeElement !== dialog.current && dialog.current.contains(document.activeElement)
      if (event.shiftKey && (!onOne || document.activeElement === first)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (!onOne || document.activeElement === last)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previousFocus?.focus()
    }
  }, [])

  return (
    <div className="modal-backdrop" data-modal-open onClick={onCancel}>
      <div ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title"
        tabIndex={-1} onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-dialog-title">{title}</h2>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">
          <button onClick={onCancel}>Cancel{keys && <> <kbd>{keyLabel(keys.cancel)}</kbd></>}</button>
          <button className={danger ? 'danger' : 'primary'} disabled={busy} onClick={onConfirm}>
            {confirmLabel}{keys && <> <kbd>{keyLabel(keys.confirm)}</kbd></>}
          </button>
        </div>
      </div>
    </div>
  )
}
