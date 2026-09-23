import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, inputThumbUrl } from '../api/client.ts'
import type { Batch, SheetGrid, SlicePlan, Slicing, SlicingSheet } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { CappedImage } from '../components/DocumentCard.tsx'
import { FinalizeBar, useFinalizeStatus } from '../components/finalize.tsx'
import { GridEditor } from '../components/GridEditor.tsx'
import { useBatchHolder } from '../components/jobs.tsx'
import { ScanViewer, batchHint, batchTitle, keyRange } from '../components/scans.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './ingest.css'

export default function Slice() {
  const status = useFinalizeStatus('slice')
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Slice</h1>
      <p className="page-sub">
        Receipts too small to scan on their own (食券 and the like) can be taped onto a sheet in a grid and
        scanned together. Cut such a sheet here into one page per receipt; the sheet itself is kept, tossed.
        <Link to="/fix-rotation">Fix a sheet's rotation</Link> first: its crops are cut the way it faces. Then{' '}
        <Link to="/group">group</Link> the pages. Every batch not archived yet is here; each cut is saved as it is
        made, and Finalize commits them all.
      </p>
      {status.isPending ? <Loading what="batches" />
        : status.error ? <ErrorState error={status.error} />
          : status.data.batches.length === 0
            ? <Card title="Sheets"><Empty>No unarchived batches. Add batches on File Index first.</Empty></Card>
            : <>
              {status.data.batches.map((b) => <BatchSheets key={b.batch_id} batch={b} />)}
              <FinalizeBar step="slice" status={status.data} what="the slicing" />
            </>}
    </div>
  )
}

/** One batch's sheets, in its own section. */
function BatchSheets({ batch }: { batch: Batch }) {
  const slicing = useQuery({
    queryKey: ['ingest', 'slicing', batch.batch_id],
    queryFn: () => api.ingest.slicing(batch.batch_id),
    placeholderData: (previous) => previous,
  })
  if (slicing.isPending) return <Loading what={`batch ${batch.batch_id}'s sheets`} />
  if (slicing.error) return <ErrorState error={slicing.error} />
  if (slicing.data.batch_id !== batch.batch_id) return null     // archived since the page asked
  return <Sheets data={slicing.data} batch={batch} />
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
    plan.trims && `${plan.trims} trim(s)`,
  ].filter(Boolean)
  if (lost.length > 0) {
    const keys = plan.dropped_keys.length > 8 ? `${plan.dropped_keys.slice(0, 8).join(', ')}…` : plan.dropped_keys.join(', ')
    lines.push(`${lost.join(', ')} will be deleted (${keys}): those serials name other `
      + 'receipts now, so they need OCR, Parse and Review again.')
  }
  return lines
}

type Pending = { key: string; grid: SheetGrid | null; plan: SlicePlan }

function Sheets({ data, batch }: { data: Slicing; batch: Batch }) {
  const batchId = batch.batch_id
  const queryClient = useQueryClient()
  const holder = useBatchHolder(batchId)
  const [editing, setEditing] = useState<SlicingSheet | null>(null)
  const [viewing, setViewing] = useState<SlicingSheet | null>(null)
  const [confirming, setConfirming] = useState<Pending | null>(null)
  const [done, setDone] = useState('')

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
  const busy = plan.isPending || apply.isPending
  const editError = holder ? `Waiting for ${holder.title}: it is using batch ${batchId}.`
    : (plan.error ?? apply.error)?.message ?? null
  const sliced = data.sheets.filter((s) => s.grid)

  return (
    <Card title={batchTitle(batch)} className="card--full" hint={batchHint(batch)}>
      {data.problems.length > 0 && (
        <div className="error-banner" role="alert">
          A slice was interrupted: {data.problems.join('; ')}. Open the sheet and save or unslice it to put it right.
        </div>
      )}
      <div className="page-grid">
        {data.sheets.map((sheet) => {
          const cut = sheet.grid !== null
          const refusal = sheet.refusal ?? (!sheet.image_available ? 'The scan is not in the input folder' : null)
          return (
            <div className="page-cell" key={sheet.key}>
              <figure className={`page-tile${refusal && !cut ? ' tossed' : ''}${cut ? ' grouped' : ''}`}
                style={cut ? { ['--group-hue' as string]: 210 } : undefined}>
                <div className="page-tile__tools">
                  <span className="page-tile__key"><strong>{sheet.key}</strong></span>
                  <button className={cut ? '' : 'primary'} disabled={busy || refusal !== null && !cut && !sheet.sliced}
                    title={refusal ?? (cut ? 'Change how this sheet is cut' : 'Cut this sheet into one page per receipt')}
                    onClick={() => { plan.reset(); apply.reset(); setEditing(sheet) }}>
                    ✂ {cut ? 'Edit' : 'Slice'}
                  </button>
                </div>
                <div className="page-tile__scan">
                  {sheet.image_available
                    ? (
                      <button className="page-tile__zoom" onClick={() => setViewing(sheet)} title="Show this scan full size">
                        <CappedImage src={inputThumbUrl(sheet.filename, sheet.image_version)} alt={`Scan ${sheet.key}`} />
                      </button>
                    )
                    : <span className="ingest-note">Scan not in the input folder</span>}
                </div>
                <figcaption>
                  {cut && <span className="page-badge">→ {keyRange(sheet.crops.map((c) => c.key))}</span>}{' '}
                  {sheet.filename}
                </figcaption>
              </figure>
            </div>
          )
        })}
      </div>
      <p className={`ingest-note${done ? ' ok' : ''}`}>
        {done || (sliced.length > 0
          ? `${sliced.length} sheet(s) sliced into ${sliced.reduce((n, s) => n + s.crops.length, 0)} page(s).`
          : 'No sheets sliced in this batch.')}
      </p>

      {viewing && <ScanViewer label={viewing.key} filename={viewing.filename} version={viewing.image_version}
        onClose={() => setViewing(null)} />}
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
