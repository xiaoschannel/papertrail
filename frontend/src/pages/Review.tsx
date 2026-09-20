import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
import type { DecisionIn, Draft, ReviewQueue, Verdict } from '../api/types.ts'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { Markdown } from '../components/Markdown.tsx'
import { ReviewProgress } from '../components/review/ReviewProgress.tsx'
import {
  INPUT_SOURCES, ReviewForm, costIsInvalid, initialForm, parseCost, type FormState,
} from '../components/review/ReviewForm.tsx'
import { ScanOverlay } from '../components/review/ScanOverlay.tsx'
import { useShortcuts } from '../components/review/useShortcuts.ts'
import '../components/review/review.css'

const RECENT_KEPT = 10
const QUICK_APPLY_KEYS = 3

const inputImageUrl = (filename: string) => `/api/media/input/${encodeURIComponent(filename)}`

const toDraft = (form: FormState): Draft => ({
  document_type: form.document_type,
  name: form.name,
  date: form.date,
  time: form.time,
  cost: parseCost(form.cost),
  currency: form.currency,
})

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
}

type Cursor = { key: string | null; index: number }
/** A decision you can still take back: what was sent (Undo sends it again, so the server can tell whether
 *  it is still the one on file) and the form as you left it, to bring back. */
type Recent = { made: DecisionIn; label: string; form: FormState }

export default function Review() {
  const queryClient = useQueryClient()
  const queue = useQuery({ queryKey: ['review-queue'], queryFn: api.review.queue })
  const items = queue.data?.items ?? []

  // Where we are: always a document's key, so the document on screen stays on screen when the queue
  // changes around it (a Parse finishing elsewhere adds documents, which sort in by name). The slot is
  // only the fallback for a key that has left the queue without us (decided in another tab).
  const [cursor, setCursor] = useState<Cursor>({ key: null, index: 0 })
  const found = cursor.key === null ? -1 : items.findIndex((item) => item.key === cursor.key)
  const position = found >= 0 ? found : Math.max(0, Math.min(cursor.index, items.length - 1))
  const key = items[position]?.key ?? null
  useEffect(() => {
    if (key !== null && cursor.key !== key) setCursor({ key, index: position })
  }, [key, position, cursor.key])
  // The document on screen right now, readable after an await.
  const currentKey = useRef(key)
  useEffect(() => {
    currentKey.current = key
  })

  const doc = useQuery({
    queryKey: ['review-doc', key],
    queryFn: () => api.review.document(key ?? ''),
    enabled: key !== null,
  })
  const nextKey = items[position + 1]?.key
  useEffect(() => {
    if (nextKey) void queryClient.prefetchQuery({ queryKey: ['review-doc', nextKey], queryFn: () => api.review.document(nextKey) })
  }, [nextKey, queryClient])

  // Unsaved form values per document, so moving back and forth keeps what you typed.
  const [drafts, setDrafts] = useState<Record<string, FormState>>({})
  const currentDoc = doc.data && doc.data.key === key ? doc.data : null
  const form = currentDoc ? (drafts[currentDoc.key] ?? initialForm(currentDoc)) : null
  const patchForm = (patch: Partial<FormState>) => {
    if (!currentDoc) return
    const docKey = currentDoc.key
    const base = initialForm(currentDoc)
    setDrafts((all) => ({ ...all, [docKey]: { ...(all[docKey] ?? base), ...patch } }))
  }

  const draft = form ? toDraft(form) : null
  const debounced = useDebounced(key && draft ? { key, draft } : null, 250)
  const hintsFor = debounced && debounced.key === key ? debounced : null
  const hints = useQuery({
    queryKey: ['review-hints', hintsFor?.key, hintsFor?.draft],
    queryFn: () => (hintsFor ? api.review.hints(hintsFor.key, hintsFor.draft) : Promise.reject(new Error('no document'))),
    enabled: hintsFor !== null,
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === key ? previous : undefined),
  })

  const [activeFields, setActiveFields] = useState<readonly string[]>([])
  // Newest first, one per document. Kept in memory: a reload starts a fresh list, like any editor's undo.
  const [recent, setRecent] = useState<Recent[]>([])
  /** The document the "accept a new name?" question was asked about; it's only answered for that one. */
  const [confirmAccept, setConfirmAccept] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    setConfirmAccept(null)     // a question about another document isn't this one's
  }, [key])

  const decide = useMutation({ mutationFn: api.review.decide })
  const undo = useMutation({ mutationFn: api.review.undo })
  const clearAll = useMutation({ mutationFn: api.review.clearAll })
  // Set synchronously, so a second keypress before the next render can't decide the same document
  // twice (e.g. A then T in quick succession).
  const deciding = useRef(false)

  /** After any change to decisions: smart matches, name status and prefill depend on them. */
  const refreshAfterDecisionChange = async () => {
    // Archive's counts and plan follow the decisions; the rest of the app re-reads when next opened.
    void queryClient.invalidateQueries({ queryKey: ['ingest', 'archive'], refetchType: 'none' })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['review-queue'] }),
      queryClient.invalidateQueries({ queryKey: ['review-doc'] }),
      queryClient.invalidateQueries({ queryKey: ['review-hints'] }),
    ])
  }

  const save = (verdict: Verdict) => {
    if (!currentDoc || !form || !draft) return
    deciding.current = true
    const slot = position
    const decidedKey = currentDoc.key
    // The next document takes its place (the previous one when it was the last).
    const following = items[slot + 1]?.key ?? items[slot - 1]?.key ?? null
    const decidedForm = form
    const made: DecisionIn = { key: decidedKey, verdict, draft, comment: form.comment }
    decide.mutate(made, {
      onSuccess: (summary) => {
        // Drop it from the queue right away (the refetch confirms). Stay in the same slot, unless
        // you already moved to another document while this was saving.
        queryClient.setQueryData<ReviewQueue>(['review-queue'], (old) =>
          old && { ...old, summary, items: old.items.filter((item) => item.key !== decidedKey) })
        setCursor((prev) => (prev.key === decidedKey || prev.key === null ? { key: following, index: slot } : prev))
        setDrafts((all) => {
          const rest = { ...all }
          delete rest[decidedKey]
          return rest
        })
        setRecent((all) => [{ made, label: decidedForm.name, form: decidedForm },
          ...all.filter((entry) => entry.made.key !== decidedKey)].slice(0, RECENT_KEPT))
        setConfirmAccept(null)
        setError(null)
        void refreshAfterDecisionChange()
      },
      onError: (e) => {
        setConfirmAccept(null)
        setError(e.message)
      },
      onSettled: () => {
        deciding.current = false
      },
    })
  }

  const submit = async (verdict: Verdict) => {
    if (!currentDoc || !form || !draft || deciding.current) return
    if (form.document_type === 'receipt' && costIsInvalid(form.cost)) {
      setError('Cost must be a number.')
      return
    }
    if (verdict === 'accepted') {
      // Check these exact values first (the debounced hints may lag the last keystroke): report a
      // blocker before anything else, then ask before accepting a name no earlier review confirmed.
      const checkedKey = currentDoc.key
      const fresh = hints.data && !hints.isFetching && !hints.isPlaceholderData && hintsFor?.key === checkedKey
        && JSON.stringify(hintsFor.draft) === JSON.stringify(draft)
      deciding.current = true
      let check
      try {
        check = fresh && hints.data ? hints.data : await api.review.hints(checkedKey, draft)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        return
      } finally {
        deciding.current = false
      }
      if (currentKey.current !== checkedKey) return // moved on while checking; don't act on the new document
      if (check.accept_error) {
        setError(check.accept_error)
        return
      }
      if (check.name_status === 'unseen') {
        setConfirmAccept(checkedKey)
        return
      }
    }
    save(verdict)
  }

  /** Move relative to the current document. Computed from the latest cursor, so key repeat or
   *  several presses before a re-render each advance one step. */
  const move = (delta: number) => {
    setCursor((prev) => {
      const at = prev.key === null ? -1 : items.findIndex((item) => item.key === prev.key)
      const from = at >= 0 ? at : Math.max(0, Math.min(prev.index, items.length - 1))
      const to = Math.max(0, Math.min(from + delta, items.length - 1))
      const item = items[to]
      return item ? { key: item.key, index: to } : prev
    })
  }

  const forget = (key: string) => setRecent((all) => all.filter((entry) => entry.made.key !== key))
  /** Take back any recent decision, in any order: decisions on different documents don't depend on each other. */
  const undoDecision = (entry: Recent) => {
    if (undo.isPending) return
    const { made: { key: undoneKey }, form: undoneForm } = entry
    undo.mutate(entry.made, {
      onSuccess: async () => {
        forget(undoneKey)
        // bring back exactly what was submitted, not the extraction's defaults
        setDrafts((all) => ({ ...all, [undoneKey]: undoneForm }))
        await refreshAfterDecisionChange()
        setCursor({ key: undoneKey, index: position })
      },
      onError: (e) => {
        // No longer the decision on file (archived, cleared, decided again or regrouped): it can't come back.
        if (e instanceof ApiError && (e.status === 404 || e.status === 409)) forget(undoneKey)
        setError(e.message)
      },
    })
  }
  const undoLast = () => {
    const [newest] = recent
    if (newest) undoDecision(newest)
  }

  const quickNames = currentDoc?.smart_matches.filter((m) => m.quick_apply).slice(0, QUICK_APPLY_KEYS) ?? []
  useShortcuts({
    a: () => void submit('accepted'),
    m: () => void submit('marked'),
    t: () => void submit('tossed'),
    ArrowLeft: () => move(-1),
    ArrowRight: () => move(1),
    z: undoLast,
    ...Object.fromEntries(quickNames.map((m, i) => [String(i + 1), () => patchForm({ name: m.name })])),
  })

  const summary = queue.data?.summary
  const decidedCount = summary ? summary.verdicts.reduce((n, v) => n + v.count, 0) : 0
  const verdictOf = (verdict: Verdict) => summary?.verdicts.find((v) => v.verdict === verdict)
  const recentList = recent.length > 0 && (
    <section className="recent-decisions" aria-label="Recent decisions">
      <div className="config-hint">Recent decisions</div>
      <div className="name-list">
        {recent.map((entry, i) => (
          <div key={entry.made.key} className="name-row">
            <span className="verdict-dot" style={{ background: verdictOf(entry.made.verdict)?.color }} />
            <span className="name-row__name">
              {verdictOf(entry.made.verdict)?.label ?? entry.made.verdict}{' '}
              {entry.label && <strong>{entry.label}</strong>} <span className="config-hint">{entry.made.key}</span>
            </span>
            <button disabled={undo.isPending} onClick={() => undoDecision(entry)}>
              Undo{i === 0 && <> <kbd>Z</kbd></>}
            </button>
          </div>
        ))}
      </div>
    </section>
  )

  return (
    <div className="review-page">
      <div className="review-head">
        <div>
          <h1>Review</h1>
          <p className="page-sub">
            Check each extracted document, then accept, mark or toss it.{' '}
            <span className="shortcut-legend">
              <kbd>A</kbd> accept <kbd>M</kbd> mark <kbd>T</kbd> toss <kbd>←</kbd><kbd>→</kbd> move{' '}
              <kbd>1</kbd>–<kbd>3</kbd> quick match <kbd>Z</kbd> undo <kbd>Esc</kbd> leave a field
            </span>
          </p>
        </div>
        {decidedCount > 0 && (
          <button className="danger-outline" onClick={() => setConfirmClear(true)}>Clear all reviews</button>
        )}
      </div>

      {error && <div className="error-banner" role="alert">{error}</div>}

      {queue.isLoading ? <Loading what="review queue" />
        : queue.isError ? <ErrorState error={queue.error} />
        : queue.data?.blocker ? <Empty>{queue.data.blocker}</Empty>
        : summary && (
          <>
            <ReviewProgress summary={summary} />

            {items.length === 0 ? <><Empty>All items reviewed!</Empty>{recentList}</> : (
              <>
                <div className="review-nav">
                  <button disabled={position === 0} onClick={() => move(-1)}>← Prev</button>
                  <button disabled={position >= items.length - 1} onClick={() => move(1)}>Next →</button>
                  <span><strong>{position + 1} / {items.length}</strong> — {key}</span>
                </div>

                {doc.isError ? <ErrorState error={doc.error} />
                  : !currentDoc || !form ? <Loading what="document" />
                  : (
                    <div className="review-layout">
                      <Card title="OCR text" className="review-col">
                        {currentDoc.ocr_text
                          ? <Markdown className="textdump review-ocr" source={currentDoc.ocr_text} />
                          : <p className="ingest-note">No OCR text.</p>}
                      </Card>

                      <Card title="Scan" hint={currentDoc.pages.length > 1 ? `${currentDoc.pages.length} pages` : undefined}
                        className="review-col">
                        <ScanOverlay pages={currentDoc.pages} imageUrl={inputImageUrl} activeFields={activeFields}
                          onHoverField={(field) => setActiveFields(field ? [field] : [])} />
                      </Card>

                      <Card title="Decision" className="review-col decision">
                        <ReviewForm key={currentDoc.key} doc={currentDoc} form={form} onChange={patchForm}
                          hints={hintsFor ? hints.data : undefined}
                          activeFields={activeFields}
                          onActivate={(input) => setActiveFields(input ? INPUT_SOURCES[input] ?? [] : [])} />
                        <div className="review-actions">
                          <button className="primary" disabled={decide.isPending} onClick={() => void submit('accepted')}>
                            Accept <kbd>A</kbd>
                          </button>
                          <button className="warn" disabled={decide.isPending} onClick={() => void submit('marked')}>
                            Mark <kbd>M</kbd>
                          </button>
                          <button disabled={decide.isPending} onClick={() => void submit('tossed')}>
                            Toss <kbd>T</kbd>
                          </button>
                        </div>
                        <CustomInstructions />
                        {recentList}
                      </Card>
                    </div>
                  )}
              </>
            )}
          </>
        )}

      {confirmAccept !== null && confirmAccept === key && form && (
        <ConfirmDialog title="Accept a new name?" confirmLabel="Accept" busy={decide.isPending}
          onConfirm={() => (currentKey.current === confirmAccept ? save('accepted') : setConfirmAccept(null))}
          onCancel={() => setConfirmAccept(null)}>
          <strong>{form.name}</strong> was not seen in previous reviews. Accept it anyway?
        </ConfirmDialog>
      )}

      {confirmClear && (
        <ConfirmDialog title="Clear all reviews?" confirmLabel="Clear all" danger busy={clearAll.isPending}
          onCancel={() => setConfirmClear(false)}
          onConfirm={() => clearAll.mutate(undefined, {
            onSuccess: async () => {
              setConfirmClear(false)
              setRecent([])
              setError(null)
              await refreshAfterDecisionChange()
              setCursor({ key: null, index: 0 })
            },
            onError: (e) => {
              setConfirmClear(false)
              setError(e.message)
            },
          })}>
          This removes all {decidedCount} review decisions, including documents tossed from File Index.
          It cannot be undone.
        </ConfirmDialog>
      )}
    </div>
  )
}

/** Parse's custom instructions, editable here too; saved when the box loses focus. Only this one
 *  setting is sent, so settings changed on other pages meanwhile are left alone.
 *
 *  Folded away, because they are read far less often than they are scrolled past. The summary says only
 *  when the box is empty, which is the state worth noticing from outside it. */
function CustomInstructions() {
  const config = useConfig()
  const [text, setText] = useState<string | null>(null)
  const saveConfig = useSaveConfig()
  const save = {
    ...saveConfig,
    mutate: (value: string) => saveConfig.mutate({ parse_custom_instruction: value }, { onSuccess: () => setText(null) }),
  }
  if (!config.data) return null
  const saved = config.data.parse_custom_instruction
  const value = text ?? saved
  // Nothing to say when there are instructions: folded away is where they live. Empty is the surprise.
  const state = save.isPending ? ' — saving…' : save.isError ? ' — not saved' : value.trim() ? '' : ' — empty'
  return (
    <details className="custom-instructions">
      <summary>Custom instructions for Parse{state}</summary>
      <textarea id="rv-instructions" rows={12} value={value} aria-label="Custom instructions for Parse"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          if (text !== null && text !== saved) save.mutate(text)
        }} />
    </details>
  )
}
