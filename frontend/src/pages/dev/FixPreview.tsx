import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl } from '../../api/client.ts'
import type { components } from '../../api/schema'
import { Card, Empty, ErrorState, Loading, Tile } from '../../components/ui.tsx'
import { ConfirmDialog } from '../../components/ConfirmDialog.tsx'
import { DocumentCard } from '../../components/scans/DocumentCard.tsx'
import './dev.css'

type Schemas = components['schemas']
type SmartMatch = Schemas['SmartMatchOut']
type Marked = Schemas['MarkedOut']
type Records = Schemas['RecordsOut']
type Fields = Schemas['Fields'] | Schemas['DocumentFields']
type CleanUp = Schemas['CleanUp']
type Which = 'smart-match' | 'records'

const FIELDS = ['document_type', 'name', 'date', 'time', 'cost', 'currency'] as const
const LABELS: Record<(typeof FIELDS)[number], string> = {
  document_type: 'Type', name: 'Name', date: 'Date', time: 'Time', cost: 'Cost', currency: 'Currency',
}
const PAGE = 48

/** A value as the page shows it: nothing read is a dash, an empty string is said so. */
const show = (value: string | number | null | undefined): ReactNode =>
  value === null || value === undefined ? <span className="fix-none">—</span>
    : value === '' ? <span className="fix-none">(empty)</span>
    : typeof value === 'number' ? value.toLocaleString() : value

/**
 * Temporary, never in a PR: the extraction-vs-decision fixes on this archive, and the clean-up of what the old
 * code left behind. Listing reads only; each clean-up is Apply (asks first), then Finalize (commit) or Undo
 * (back to the last commit).
 */
export default function FixPreview() {
  const preview = useQuery({ queryKey: ['dev', 'fix-preview'], queryFn: api.dev.fixPreview })

  if (preview.isPending) return <Loading what="the fix preview" />
  if (preview.error) return <ErrorState error={preview.error} />
  const p = preview.data

  return (
    <div className="dev-page fix-preview">
      <h1>Fix Preview</h1>
      <p className="page-sub">
        Three fixes on this archive. The code is fixed; two of them left data behind, which a clean-up below
        rewrites. Nothing changes until you press Apply, Undo puts the files back to their last commit, and
        Finalize commits them.
      </p>
      <SmartMatchSection s={p.smart_match} />
      <MarkedSection marked={p.marked} />
      <RecordsSection r={p.records} />
    </div>
  )
}

type Filter = 'loses' | 'kept' | 'all'

function SmartMatchSection({ s }: { s: SmartMatch }) {
  const [filter, setFilter] = useState<Filter>('loses')
  const [page, setPage] = useState(0)
  const losing = s.entries.filter((e) => !e.still_approved)
  const kept = s.entries.filter((e) => e.still_approved)
  const shown = filter === 'loses' ? losing : filter === 'kept' ? kept : s.entries
  const pages = Math.max(1, Math.ceil(shown.length / PAGE))
  const at = Math.min(page, pages - 1)
  const choose = (f: Filter) => { setFilter(f); setPage(0) }

  return (
    <Card title="1 · Smart match learned names from tossed and marked documents" className="card--uncapped">
      <p className="fix-claim">
        <strong>Was.</strong> Archive wrote every document it filed into the smart-match cache as “read this →
        confirmed that”, whatever its verdict. So the name on a tossed or marked document, often just what the model
        read, counted as a confirmed name: Review marked it “previously approved” and could prefill it from an
        exact match.
      </p>
      <p className="fix-claim">
        <strong>The fix.</strong> Archive learns only from accepted documents (a marked document still teaches its
        name when the Workshop accepts it). <strong>Clean-up:</strong> remove the entries tossed and marked
        documents left.
      </p>
      <CleanUpBar which="smart-match" c={s.clean_up} what={`Remove ${s.clean_up.todo} cache entries`} />
      <div className="tiles">
        <Tile label="Entries to remove" value={s.entries.length} />
        <Tile label="…just the model’s read" value={s.entries.filter((e) => e.same_as_read).length} />
        <Tile label="…whose name stops being approved" value={losing.length} />
        <Tile label="Names no longer approved" value={new Set(losing.map((e) => e.confirmed)).size} />
      </div>
      <p className="ingest-note">
        Of {s.cache_size.toLocaleString()} cache entries. Besides these, {s.unnamed} entr{s.unnamed === 1 ? 'y' : 'ies'} from
        tossed or marked documents have no name; they never counted and go too. “Stops being approved”: no accepted
        document confirms the same name, so after the clean-up Review no longer calls it approved.
      </p>

      <div className="segmented fix-filter">
        <button className={filter === 'loses' ? 'on' : ''} onClick={() => choose('loses')}>
          Stops being approved ({losing.length})
        </button>
        <button className={filter === 'kept' ? 'on' : ''} onClick={() => choose('kept')}>
          Still approved by an accepted document ({kept.length})
        </button>
        <button className={filter === 'all' ? 'on' : ''} onClick={() => choose('all')}>All ({s.entries.length})</button>
      </div>
      {shown.length === 0 ? <Empty>None.</Empty> : (
        <>
          <div className="receipt-gallery">
            {shown.slice(at * PAGE, (at + 1) * PAGE).map((e) => (
              <DocumentCard key={e.key} src={mediaUrl(e.image)} trim={e.trim} name={e.confirmed}
                caption={<>{e.verdict} · read {e.same_as_read ? 'the same' : <>“{e.extracted || '(nothing)'}”</>}</>}
                title={e.comment ? `${e.key} · comment: ${e.comment}` : e.key}>
                {e.comment && <div className="fix-comment">{e.comment}</div>}
              </DocumentCard>
            ))}
          </div>
          {pages > 1 && (
            <div className="pager">
              <button disabled={at === 0} onClick={() => setPage(at - 1)}>← Prev</button>
              <span>Page {at + 1} of {pages}</span>
              <button disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>Next →</button>
            </div>
          )}
        </>
      )}

      <details className="ingest-details">
        <summary>Waiting in Review: tossed or marked with a name, which the old Archive would have taught the cache
          and the fixed one won’t ({s.pending.length})</summary>
        {s.pending.length === 0 ? <Empty>None.</Empty> : (
          <div className="table-wrap short">
            <table>
              <thead><tr><th>Document</th><th>Verdict</th><th>Read</th><th>Would have been confirmed</th><th>Approved otherwise</th></tr></thead>
              <tbody>
                {s.pending.map((e) => (
                  <tr key={e.key}>
                    <td><code>{e.key}</code></td><td>{e.verdict}</td><td>{show(e.extracted)}</td>
                    <td>{e.confirmed}{e.same_as_read && <span className="fix-none"> (the read)</span>}</td>
                    <td>{e.still_approved ? 'Yes' : 'No'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </Card>
  )
}

/** Two sets of fields side by side, the rows that differ marked. */
function Compare({ left, right, leftLabel, rightLabel, differs }:
  { left: Fields | null; right: Fields | null; leftLabel: string; rightLabel: string; differs: string[] }) {
  return (
    <div className="table-wrap">
      <table className="fix-compare">
        <thead><tr><th>Field</th><th>{leftLabel}</th><th>{rightLabel}</th></tr></thead>
        <tbody>
          {FIELDS.map((f) => (
            <tr key={f} className={differs.includes(f) ? 'fix-differs' : ''}>
              <td>{LABELS[f]}</td><td>{left ? show(left[f]) : show(null)}</td><td>{right ? show(right[f]) : show(null)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MarkedSection({ marked }: { marked: Marked[] }) {
  const differing = marked.filter((m) => m.differs.length > 0 && !m.reread_pending)
  return (
    <Card title="2 · The Workshop form started from the model’s read" className="card--uncapped">
      <p className="fix-claim">
        <strong>Was.</strong> Mark in Review records the whole form, but the Workshop started its form again from what
        the model read, keeping only the comment.
      </p>
      <p className="fix-claim">
        <strong>The fix.</strong> The form starts from what Review recorded (a pending reread still wins: it is a new
        read). Code only, nothing to clean up: below, each marked document’s form as it starts now beside what
        Review recorded, which should match.
      </p>
      {differing.length === 0
        ? <p className="dev-ok">All {marked.length} marked document{marked.length === 1 ? '' : 's'} start from what Review recorded.</p>
        : <p className="ingest-note ingest-warning">{differing.length} still start from something else.</p>}
      {marked.length === 0 ? <Empty>Nothing is marked.</Empty> : marked.map((m) => (
        <div className="fix-row" key={m.key}>
          <DocumentCard src={mediaUrl(m.image)} trim={m.trim} name={<code>{m.key}</code>}
            caption={m.pages.length > 1 ? `${m.pages.length} pages` : undefined} />
          <div>
            <Compare left={m.starts} right={m.recorded} leftLabel="The form starts with" rightLabel="Review recorded"
              differs={m.differs} />
            <p className="ingest-note">
              {m.reread_pending ? 'A reread is pending: the form starts from it. ' : ''}
              {m.comment ? <>Comment: {m.comment}</> : null}
            </p>
          </div>
        </div>
      ))}
    </Card>
  )
}

function RecordsSection({ r }: { r: Records }) {
  const [all, setAll] = useState(false)
  const records = r.records
  const differing = records.filter((x) => x.differs.length > 0 || x.source !== 'sidecar'
    || x.corrected_now.join() !== x.corrected_fixed.join())
  const shown = all ? records : differing
  return (
    <Card title="3 · The Workshop’s record kept placeholders as the model’s read" className="card--uncapped">
      <p className="fix-claim">
        <strong>Was.</strong> A record’s “read” was the form’s defaults: “Receipt” (or “Document”) where the model read
        no name, and a cost of 0 and an empty currency for a document read as having neither.
      </p>
      <p className="fix-claim">
        <strong>The fix.</strong> The record keeps what the model read, as it read it, and “corrected” compares only
        fields the accepted document has. <strong>Clean-up:</strong> rewrite the records already written, in the log
        and on their pages’ sidecars, from those sidecars’ extraction (which is the read).
      </p>
      <CleanUpBar which="records" c={r.clean_up} what={`Rewrite ${r.clean_up.todo} records`} />
      <p className="ingest-note">
        {records.length} record{records.length === 1 ? '' : 's'}; {differing.length} would change or can’t be checked.
      </p>
      <div className="segmented fix-filter">
        <button className={all ? '' : 'on'} onClick={() => setAll(false)}>Those ({differing.length})</button>
        <button className={all ? 'on' : ''} onClick={() => setAll(true)}>All ({records.length})</button>
      </div>
      {shown.length === 0 ? <Empty>None.</Empty> : shown.map((x) => (
        <div className="fix-row" key={`${x.key}-${x.at}`}>
          <DocumentCard src={mediaUrl(x.image)} trim={x.trim} name={<code>{x.key}</code>}
            caption={x.reread ? 'reread in the Workshop' : 'accepted without a reread'} />
          <div>
            <Compare left={x.recorded} right={x.literal ?? null} leftLabel="Recorded as read"
              rightLabel="What the model read" differs={x.differs} />
            <p className="ingest-note">
              Corrected: {x.corrected_now.join(', ') || 'nothing'}
              {x.corrected_now.join() !== x.corrected_fixed.join() && <> → {x.corrected_fixed.join(', ') || 'nothing'}</>}.{' '}
              {x.source === 'none' && 'Nothing was read from it: the record holds what Review recorded, and keeps it.'}
              {x.source === 'not found' && 'Its archived page wasn’t found, so the read can’t be checked.'}
            </p>
          </div>
        </div>
      ))}
    </Card>
  )
}

/** Where a clean-up stands, and its actions: Apply asks first; Finalize and Undo act on the files it changed. */
function CleanUpBar({ which, c, what }: { which: Which; c: CleanUp; what: string }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const act = useMutation({
    mutationFn: (action: 'apply' | 'undo' | 'finalize') =>
      action === 'apply' ? api.dev.fixApply(which) : action === 'undo' ? api.dev.fixUndo(which) : api.dev.fixFinalize(which),
    onSuccess: (data) => { queryClient.setQueryData(['dev', 'fix-preview'], data); setConfirming(false) },
  })
  const files = c.uncommitted.length === 1 ? '1 file differs from its' : `${c.uncommitted.length} files differ from their`
  return (
    <div className="fix-cleanup">
      {c.todo > 0 && !c.blocked && (
        <button className="primary" disabled={act.isPending} onClick={() => setConfirming(true)}>{what}…</button>
      )}
      {c.todo > 0 && c.blocked && <p className="ingest-note ingest-warning">{c.blocked}</p>}
      {c.todo === 0 && c.uncommitted.length > 0 && (
        <>
          <span>Applied: {files} last commit.</span>
          <button className="primary" disabled={act.isPending} onClick={() => act.mutate('finalize')}>Finalize (commit)</button>
          <button disabled={act.isPending} onClick={() => act.mutate('undo')}
            title="Puts them back as last committed: anything else written to them since goes too">Undo</button>
        </>
      )}
      {c.todo === 0 && c.uncommitted.length === 0 && <span className="dev-ok">Done: nothing left to clean up.</span>}
      {act.error && <div className="error-banner" role="alert">{act.error.message}</div>}
      {confirming && (
        <ConfirmDialog title={`${what}?`} confirmLabel="Apply" busy={act.isPending}
          onConfirm={() => act.mutate('apply')} onCancel={() => setConfirming(false)}>
          It changes {which === 'smart-match' ? 'smart_match_cache.json' : 'workshop_log.jsonl and the records’ sidecars'}.
          Until you finalize, Undo puts them back exactly as they were last committed.
        </ConfirmDialog>
      )}
    </div>
  )
}
