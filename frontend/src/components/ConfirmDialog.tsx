import { useEffect, useRef, type ReactNode } from 'react'

/**
 * A small modal. Enter on the focused confirm button confirms; Escape (from anywhere) or the backdrop
 * cancels. Focus moves to the confirm button, stays inside the dialog while it is open (Tab cycles its
 * buttons, so nothing behind it can be reached), and returns to where it was when the dialog closes.
 * `busy` disables confirming, so a held Enter can't send the request twice.
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
  const confirmButton = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const cancel = useRef(onCancel)
  useEffect(() => {
    cancel.current = onCancel
  })

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    confirmButton.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        cancel.current()
        return
      }
      if (event.key !== 'Tab' || !dialog.current) return
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input, select, textarea')]
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) return
      const inside = dialog.current.contains(document.activeElement)
      if (event.shiftKey && (!inside || document.activeElement === first)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
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
        onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-dialog-title">{title}</h2>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">
          <button onClick={onCancel}>Cancel <kbd>Esc</kbd></button>
          <button ref={confirmButton} className={danger ? 'danger' : 'primary'} disabled={busy} onClick={onConfirm}>
            {confirmLabel} <kbd>Enter</kbd>
          </button>
        </div>
      </div>
    </div>
  )
}
