import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl } from '../api/client.ts'
import type { PageOrderArchive as PageOrderArchiveState, PageOrderDocument } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { DocumentCard } from '../components/DocumentCard.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './dev.css'
import './ingest.css'

const fileName = (rel: string) => rel.split('/').pop() ?? rel
const plural = (n: number, what: string) => `${n} ${what}${n === 1 ? '' : 's'}`

/** A page as shown: which scan, its place, where it is and where it goes. */
type Shown = { serial: number; page: number; rel_path: string; target: string }

/** A document's pages in ``order`` (its scans' serials): each takes the name slot of its place when it is
 * filed, and keeps its own name in marked/ or tossed/. */
function inOrder(document: PageOrderDocument, order: number[]): Shown[] {
  const bySerial = new Map(document.pages.map((p) => [p.serial, p]))
  return order.map((serial, i) => {
    const page = bySerial.get(serial)!
    return { serial, page: i + 1, rel_path: page.rel_path, target: document.slots[i] ?? page.rel_path }
  })
}

/**
 * One-off migration: archived multi-page documents filed in scan order before sidecars kept page order,
 * put in the order they were grouped in (or one chosen here, where the group was saved wrong), with a
 * backup that Undo puts back, Redo re-applies from and Finalize deletes. Temporary: kept as a git tag.
 */
export default function PageOrderArchive() {
  const queryClient = useQueryClient()
  // bumped after every change, so a page now under another page's name isn't shown from the browser's cache
  const [stamp, setStamp] = useState(0)
  // page orders chosen here, by document key, until the document is put in order
  const [chosen, setChosen] = useState<Record<string, number[]>>({})
  const [finalizing, setFinalizing] = useState(false)
  const [finalized, setFinalized] = useState<number | null>(null)
  const [redone, setRedone] = useState<number | null>(null)
  const state = useQuery({ queryKey: ['dev', 'page-order-archive'], queryFn: api.dev.pageOrderArchive })
  const done = (result: PageOrderArchiveState) => {
    queryClient.setQueryData(['dev', 'page-order-archive'], result)
    void queryClient.invalidateQueries({ queryKey: ['viz'] })
    const left = new Set(result.to_do.map((d) => d.key))
    setChosen((current) => Object.fromEntries(Object.entries(current).filter(([key]) => left.has(key))))
    setStamp((n) => n + 1)
  }
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['dev', 'page-order-archive'] })
    setStamp((n) => n + 1)
  }
  const apply = useMutation({ mutationFn: api.dev.putInPageOrder, onSuccess: done, onError: refresh })
  const applyAll = useMutation({ mutationFn: api.dev.putAllInPageOrder, onSuccess: done, onError: refresh })
  const undo = useMutation({ mutationFn: api.dev.undoPageOrder, onSuccess: done, onError: refresh })
  const redo = useMutation({
    mutationFn: api.dev.redoAllPageOrder,
    onSuccess: (result) => {
      setRedone(result.redone)
      done(result.state)
    },
    onError: refresh,
  })
  const finalize = useMutation({
    mutationFn: api.dev.finalizePageOrder,
    onSuccess: (result) => {
      setFinalizing(false)
      setFinalized(result.removed)
      refresh()
    },
    onError: () => setFinalizing(false),
  })

  if (state.isPending) return <Loading what="the archive's multi-page documents" />
  if (state.error) return <ErrorState error={state.error} />
  const s = state.data
  const busy = apply.isPending || applyAll.isPending || undo.isPending || redo.isPending || finalize.isPending
  const error = (apply.error ?? applyAll.error ?? undo.error ?? redo.error ?? finalize.error)?.message
  const src = (rel: string) => `${mediaUrl(rel)}?v=${stamp}`
  const grouped = (d: PageOrderDocument) => d.pages.map((p) => p.serial)
  const orderOf = (d: PageOrderDocument) => chosen[d.key] ?? grouped(d)
  const swap = (d: PageOrderDocument, i: number) => {
    const order = [...orderOf(d)]
    ;[order[i], order[i + 1]] = [order[i + 1]!, order[i]!]
    setChosen((current) => {
      const rest = Object.fromEntries(Object.entries(current).filter(([key]) => key !== d.key))
      return order.join() === grouped(d).join() ? rest : { ...rest, [d.key]: order }
    })
  }
  const unchoose = (key: string) =>
    setChosen((current) => Object.fromEntries(Object.entries(current).filter(([k]) => k !== key)))
  const renaming = s.to_do.filter((d) => inOrder(d, orderOf(d)).some((p) => p.target !== p.rel_path)).length

  return (
    <div className="dev-page">
      <h1>Page Order Archive</h1>
      <p className="page-sub">
        A one-off pass over multi-page documents filed before the archive kept page order: they were filed in scan
        order, even when their pages were swapped on <Link className="rowlink" to="/group">Group</Link>. Each is
        shown in the order it was grouped in; where that is wrong, ⇄ swaps two pages first. Putting it in order
        hands its names round (“…”, “… (2)”) so page 1 has the plain one, and writes every page’s page number;
        pages in marked and tossed keep their names. A backup is kept, so Undo puts a document back exactly —
        until you Finalize, which deletes the backups. Redo puts everything done so far back from its backup and
        does it again, for when the migration itself is fixed while it runs.
      </p>
      {error && <div className="error-banner" role="alert">{error}</div>}

      <Card title="Not in page order" className="card--full"
        hint={s.to_do.length ? `${plural(s.to_do.length, 'document')}; ${renaming} change names` : undefined}>
        {s.problems.length > 0 && (
          <div className="ingest-note ingest-warning">
            Can’t be put in order here:
            <ul>{s.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        {s.to_do.length === 0 ? (
          <Empty>Every archived multi-page document is in its page order.</Empty>
        ) : (
          <>
            <div className="start-bar">
              <button className="primary" disabled={busy} onClick={() => applyAll.mutate(chosen)}>
                {applyAll.isPending ? 'Putting them in order…' : `Put all ${s.to_do.length} in order`}
              </button>
              {Object.keys(chosen).length > 0 && (
                <span className="ingest-note">{plural(Object.keys(chosen).length, 'document')} in an order chosen here</span>
              )}
            </div>
            {s.to_do.map((d) => {
              const pages = inOrder(d, orderOf(d))
              const renames = pages.some((p) => p.target !== p.rel_path)
              return (
                <DocumentPages key={d.key} documentKey={d.key} pages={pages} src={(p) => src(p.rel_path)}
                  onSwap={busy ? undefined : (i) => swap(d, i)}
                  note={`${chosen[d.key] ? 'in the order chosen here' : 'in the grouped order'}${renames ? '' : '; only the page numbers are written'}`}>
                  {chosen[d.key] && (
                    <button disabled={busy} onClick={() => unchoose(d.key)}>Back to the grouped order</button>
                  )}
                  <button disabled={busy} onClick={() => apply.mutate({ key: d.key, order: chosen[d.key] ?? null })}>
                    Put in order
                  </button>
                </DocumentPages>
              )
            })}
          </>
        )}
      </Card>

      <Card title="Put in order"
        hint={s.done.length ? `${plural(s.done.length, 'document')}, backed up` : undefined}>
        {s.done.length === 0
          ? <Empty>{finalized !== null ? `Finalized: ${finalized} backup(s) deleted.` : 'None put in order yet.'}</Empty>
          : (
            <>
              {s.done.map((d) => (
                <DocumentPages key={d.key} documentKey={d.key} pages={d.pages} src={(p) => src(p.target)}
                  note="as it is now" before={d.before.map(src)}>
                  <button disabled={busy} onClick={() => undo.mutate(d.key)}>Undo</button>
                </DocumentPages>
              ))}
              <div className="start-bar">
                <button disabled={busy} onClick={() => redo.mutate()}>
                  {redo.isPending ? 'Redoing…' : 'Redo all from the backups'}
                </button>
                {redone !== null && <span className="ingest-note">Redid {plural(redone, 'document')}.</span>}
              </div>
              <div className="start-bar">
                <button className="danger" disabled={busy} onClick={() => setFinalizing(true)}>
                  Finalize: delete the backups
                </button>
                <span className="ingest-note">
                  Once you’ve checked them (Receipt Detail shows a document’s pages in order). Undo is gone after.
                </span>
              </div>
            </>
          )}
      </Card>

      {finalizing && (
        <ConfirmDialog title="Delete the backups?" confirmLabel="Finalize" danger busy={finalize.isPending}
          onConfirm={() => finalize.mutate()} onCancel={() => setFinalizing(false)}>
          The {plural(s.done.length, 'document')} put in order stay as they are, and can’t be put back any more:
          their backups in <code>.page-order-backup</code> are deleted.
        </ConfirmDialog>
      )}
    </div>
  )
}

/** A document's pages in page order, each named as it is filed (or will be), with ⇄ between two pages to
 * swap them when ``onSwap`` is given; with ``before``, the backup of how it was filed beside it, to compare. */
function DocumentPages({ documentKey, pages, src, note, onSwap, before, children }: {
  documentKey: string
  pages: Shown[]
  src: (page: Shown) => string
  note: string
  onSwap?: ((index: number) => void) | undefined
  before?: string[]
  children: ReactNode
}) {
  return (
    <section className="page-order-document">
      <div className="start-bar">
        <strong>{documentKey}</strong>
        <span className="ingest-note">{note}</span>
        {children}
      </div>
      <div className="page-order-pages">
        {pages.map((p, i) => (
          <div key={p.serial} className="page-order-page">
            <DocumentCard src={src(p)} name={`Page ${p.page}`} title={p.target}
              caption={`${fileName(p.target)} · scan ${p.serial}${p.rel_path === p.target ? '' : ` (was ${fileName(p.rel_path)})`}`} />
            {onSwap && i < pages.length - 1 && (
              <button className="page-order-swap" title="Swap these two pages" onClick={() => onSwap(i)}>⇄</button>
            )}
          </div>
        ))}
      </div>
      {before && before.length > 0 && (
        <>
          <p className="ingest-note">Before (the backup), as the archive showed it:</p>
          <div className="receipt-gallery">
            {before.map((url, i) => <DocumentCard key={url} src={url} name={`Shown as page ${i + 1}`} />)}
          </div>
        </>
      )}
    </section>
  )
}
