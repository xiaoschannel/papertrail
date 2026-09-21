import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, inputThumbUrl } from '../api/client.ts'
import type { SheetGrid, SlicePlan, Slicing, SlicingSheet, TopPoints } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { GridEditor } from '../components/GridEditor.tsx'
import { useBatchHolder } from '../components/jobs.tsx'
import { BatchSelect, Pager, RotateButtons, keyRange } from '../components/scans.tsx'
import { PageViewer, TurnNotice, useTurnedPages, type Viewing } from '../components/turns.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import './ingest.css'

/** The grid's rows per page, as on Group. */
const ROWS_PER_PAGE = 6

export default function Slice() {
  const [batchId, setBatchId] = useState<number | undefined>(undefined)
  const slicing = useQuery({
    queryKey: ['ingest', 'slicing', batchId],
    queryFn: () => api.ingest.slicing(batchId),
    placeholderData: (previous) => previous,
  })
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Slice</h1>
      <p className="page-sub">
        Receipts too small to scan on their own (食券 and the like) can be taped onto a sheet in a grid and
        scanned together. Cut such a sheet here into one page per receipt; the sheet itself is kept, tossed.
        Then <Link to="/group">group</Link> the batch's pages.
      </p>
      {slicing.isPending ? <Loading what="sheets" />
        : slicing.error ? <ErrorState error={slicing.error} />
          : slicing.data.blocker || slicing.data.batch_id === null
            ? <Card title="Sheets"><Empty>{slicing.data.blocker ?? 'No batches.'}</Empty></Card>
            : <Sheets key={slicing.data.batch_id} data={slicing.data} batchId={slicing.data.batch_id} onBatch={setBatchId} />}
    </div>
  )
}

/** What a slice would cost, in words, or null when it costs nothing worth confirming. */
function warnings(plan: SlicePlan): string[] {
  const lines: string[] = []
  if (plan.replaces_decision) lines.push(`${plan.key} has a review decision; it is replaced by a toss.`)
  if (plan.moved.length > 0) {
    lines.push(`Crops move to other serials: ${plan.moved.map((m) => `${m.old_key} → ${m.new_key}`).join(', ')}.`)
  }
  const lost = [
    plan.ocr && `OCR for ${plan.ocr} page(s)`,
    plan.extractions && `${plan.extractions} parse result(s)`,
    plan.decisions && `${plan.decisions} review decision(s)`,
  ].filter(Boolean)
  if (lost.length > 0) {
    const keys = plan.dropped_keys.length > 8 ? `${plan.dropped_keys.slice(0, 8).join(', ')}…` : plan.dropped_keys.join(', ')
    lines.push(`${lost.join(', ')} will be deleted (${keys}): those serials name other `
      + 'receipts now, so they need OCR, Parse and Review again.')
  }
  return lines
}

type Pending = { key: string; grid: SheetGrid | null; plan: SlicePlan }

function Sheets({ data, batchId, onBatch }: { data: Slicing; batchId: number; onBatch: (id: number) => void }) {
  const queryClient = useQueryClient()
  const holder = useBatchHolder(batchId)
  const [editing, setEditing] = useState<SlicingSheet | null>(null)
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const [confirming, setConfirming] = useState<Pending | null>(null)
  const [done, setDone] = useState('')
  const [page, setPage] = useState(0)
  const grid = useRef<HTMLDivElement>(null)
  const columns = useGridColumnCount(grid, 6)

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['ingest'] })
    for (const root of ['review-queue', 'review-doc']) await queryClient.invalidateQueries({ queryKey: [root] })
  }
  const apply = useMutation({
    mutationFn: ({ key, grid: g, plan }: Pending) => api.ingest.applySlices(key, g, plan.token),
    onSuccess: async (result, { grid: g }) => {
      setConfirming(null)
      setEditing(null)
      setDone(g ? `Cut ${result.key} into ${keyRange(result.new_keys)}.` : `Unsliced ${result.key}.`)
      await refresh()
    },
    onError: () => setConfirming(null),
  })
  const plan = useMutation({
    mutationFn: ({ key, grid: g }: { key: string; grid: SheetGrid | null }) => api.ingest.planSlices(key, g),
    onSuccess: (result, { key, grid: g }) => {
      const pending = { key, grid: g, plan: result }
      if (warnings(result).length > 0) setConfirming(pending)
      else apply.mutate(pending)
    },
  })
  const rotate = useMutation({
    mutationFn: ({ key, top }: { key: string; top: TopPoints }) => api.ingest.rotate(key, top),
    onSuccess: refresh,
  })

  const perPage = ROWS_PER_PAGE * Math.max(1, columns)
  const pageCount = Math.max(1, Math.ceil(data.sheets.length / perPage))
  const shownPage = Math.min(page, pageCount - 1)
  const start = shownPage * perPage
  const pager = <Pager page={shownPage} pageCount={pageCount} onPage={setPage} />
  const busy = plan.isPending || apply.isPending
  const editError = holder ? `Waiting for ${holder.title}: it is using batch ${batchId}.`
    : (plan.error ?? apply.error)?.message ?? null
  const sliced = data.sheets.filter((s) => s.grid)
  const sheetsByKey = useMemo(() => new Map(data.sheets.map((s) => [s.key, s])), [data])
  // A sheet is turned before it is cut: its crops come out the way it faces.
  const turned = useTurnedPages(batchId, sheetsByKey)

  return (
    <Card title="Sheets" className="card--full"
      hint="Turn a sheet upright first: its crops are cut the way it faces.">
      <div className="controls">
        <BatchSelect id="slice-batch" batches={data.batches} value={batchId} onChange={onBatch} />
      </div>
      {data.problems.length > 0 && (
        <div className="error-banner" role="alert">
          A slice was interrupted: {data.problems.join('; ')}. Open the sheet and save or unslice it to put it right.
        </div>
      )}
      {rotate.error && <div className="error-banner" role="alert">{rotate.error.message}</div>}
      <TurnNotice query={turned.query} turns={turned.turns}
        onReview={() => setViewing({ keys: data.sheets.map((s) => s.key).filter((k) => turned.turns.has(k)), at: 0, review: true })} />
      {pager}
      <div className="page-grid" ref={grid}>
        {data.sheets.slice(start, start + perPage).map((sheet) => {
          const cut = sheet.grid !== null
          const refusal = sheet.refusal ?? (!sheet.image_available ? 'The scan is not in the input folder' : null)
          return (
            <div className="page-cell" key={sheet.key}>
              <figure className={`page-tile${refusal && !cut ? ' tossed' : ''}${cut ? ' grouped' : ''}`}
                style={cut ? { ['--group-hue' as string]: 210 } : undefined}>
                <div className="page-tile__tools">
                  <RotateButtons disabled={rotate.isPending || holder !== null || !sheet.image_available || cut || sheet.sliced}
                    {...(cut || sheet.sliced ? { title: 'Unslice the sheet to rotate it' } : {})}
                    suggested={turned.turns.get(sheet.key)}
                    onRotate={(top) => rotate.mutate({ key: sheet.key, top })} />
                  <button className={cut ? '' : 'primary'} disabled={busy || refusal !== null && !cut && !sheet.sliced}
                    title={refusal ?? (cut ? 'Change how this sheet is cut' : 'Cut this sheet into one page per receipt')}
                    onClick={() => { plan.reset(); apply.reset(); setEditing(sheet) }}>
                    ✂ {cut ? 'Edit' : 'Slice'}
                  </button>
                </div>
                <div className="page-tile__scan">
                  {sheet.image_available
                    ? (
                      <button className="page-tile__zoom" onClick={() => setViewing({ keys: [sheet.key], at: 0, review: false })} title="Show this scan full size">
                        <img src={inputThumbUrl(sheet.filename, sheet.image_version)} alt={`Scan ${sheet.key}`} loading="lazy" />
                      </button>
                    )
                    : <span className="ingest-note">Scan not in the input folder</span>}
                </div>
                <figcaption>
                  <strong>{sheet.key}</strong>{' '}
                  {cut && <span className="page-badge">→ {keyRange(sheet.crops.map((c) => c.key))}</span>}{' '}
                  {sheet.filename}
                </figcaption>
              </figure>
            </div>
          )
        })}
      </div>
      {pager}
      <p className={`ingest-note${done ? ' ok' : ''}`}>
        {done || (sliced.length > 0
          ? `${sliced.length} sheet(s) sliced into ${sliced.reduce((n, s) => n + s.crops.length, 0)} page(s).`
          : 'No sheets sliced in this batch.')}
      </p>

      {viewing && <PageViewer viewing={viewing} pages={sheetsByKey} turns={turned.turns}
        locked={holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchId}.`}
        onSetAside={turned.setAside} onMove={setViewing} onClose={() => setViewing(null)} />}
      {editing && (
        <GridEditor sheetKey={editing.key} filename={editing.filename} version={editing.image_version}
          initial={editing.grid}
          busy={busy || holder !== null} error={editError} canUnslice={editing.grid !== null || editing.sliced}
          paused={confirming !== null}
          onSave={(g) => plan.mutate({ key: editing.key, grid: g })}
          onUnslice={() => plan.mutate({ key: editing.key, grid: null })}
          onCancel={() => setEditing(null)} />
      )}
      {confirming && (
        <ConfirmDialog title={confirming.grid ? `Slice ${confirming.key}?` : `Unslice ${confirming.key}?`}
          confirmLabel={confirming.grid ? 'Slice' : 'Unslice'} danger busy={apply.isPending}
          onConfirm={() => apply.mutate(confirming)} onCancel={() => setConfirming(null)}>
          <ul className="slice-warnings">{warnings(confirming.plan).map((w) => <li key={w}>{w}</li>)}</ul>
        </ConfirmDialog>
      )}
    </Card>
  )
}
