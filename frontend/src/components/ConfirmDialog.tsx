import { useEffect, useRef, type ReactNode } from 'react'

/**
 * A small modal. Enter on the focused confirm button confirms; Escape (from anywhere) or the backdrop
 * cancels. Focus moves to the confirm button and returns to where it was when the dialog closes.
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
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title"
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
