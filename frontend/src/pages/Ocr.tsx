import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { LimitField, ReprocessField, StartBar } from '../components/ingestControls.tsx'
import './ingest.css'

export default function Ocr() {
  const queryClient = useQueryClient()
  const [batchId, setBatchId] = useState<number | undefined>(undefined)
  const [reprocess, setReprocess] = useState(false)
  const [limit, setLimit] = useState(0)
  const [provider, setProvider] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const gate = useJobGate('ocr', { gpu: true })   // every OCR model runs on this machine's GPU
  const track = useTrackJob()

  const status = useQuery({
    queryKey: ['ingest', 'ocr', provider, batchId, reprocess, limit],
    queryFn: () => api.ingest.ocr({ provider: provider ?? undefined, batchId, reprocess, limit }),
    placeholderData: (previous) => previous,
  })
  const chosen = provider ?? status.data?.provider ?? ''
  const start = useMutation({
    mutationFn: () => api.ingest.startOcr({ provider: chosen, batch_id: batchId ?? null, reprocess, limit }),
    onSuccess: (job) => {
      setConfirming(false)
      track(job)
      void queryClient.invalidateQueries({ queryKey: ['config'] })
    },
    onError: () => setConfirming(false),
  })

  if (status.isPending) return <Loading what="OCR status" />
  if (status.error) return <ErrorState error={status.error} />
  const s = status.data

  return (
    <div className="ingest-page">
      <h1>OCR</h1>
      <p className="page-sub">Read the text (and, with a grounding model, field boxes) off every scanned page.</p>
      {s.blocker ? <Empty>{s.blocker}</Empty> : (
        <>
          <Card>
            <div className="controls">
              <div className="field">
                <label htmlFor="ocr-model">OCR model</label>
                <select id="ocr-model" value={chosen} onChange={(e) => setProvider(e.target.value)}>
                  {s.providers.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="ocr-batch">Pages</label>
                <select id="ocr-batch" value={batchId ?? ''}
                  onChange={(e) => setBatchId(e.target.value ? Number(e.target.value) : undefined)}>
                  <option value="">All unarchived batches</option>
                  {s.batches.map((b) => (
                    <option key={b.batch_id} value={b.batch_id}>
                      Batch {b.batch_id} — {b.file_count} files — {b.start_datetime} to {b.end_datetime}
                    </option>
                  ))}
                </select>
              </div>
              <ReprocessField value={reprocess} onChange={setReprocess} newLabel="New & failed pages" />
              <LimitField value={limit} onChange={setLimit} />
            </div>
            <div className="tiles">
              <Tile label="Pages" value={s.total} />
              <Tile label="OCR'd" value={s.processed} />
              <Tile label="Failed" value={s.failed} />
              <Tile label="Scan missing" value={s.missing_images} />
              <Tile label="To process" value={s.to_process} />
            </div>
            {s.waiting > 0 && <p className="ingest-note">
              {s.waiting} more page{s.waiting === 1 ? ' is' : 's are'} in a batch another job is using; the next
              run picks {s.waiting === 1 ? 'it' : 'them'} up.
            </p>}
            {s.batches.length === 0 && <p className="ingest-note ingest-blocker">
              No unarchived batches. Add a batch in File Index first.
            </p>}
            <p className="ingest-note">
              {s.grounding
                ? 'This model also finds field boxes (a second, structured pass per page).'
                : 'Text only: no field boxes with this model (or structured extraction is off in Config).'}
            </p>
            {start.error && <div className="error-banner" role="alert">{start.error.message}</div>}
            <StartBar label={`OCR ${s.to_process} page${s.to_process === 1 ? '' : 's'}`} disabled={!s.to_process || !chosen}
              blockedBy={gate.blockedBy} running={gate.job?.status === 'running'} pending={start.isPending}
              onStart={() => (reprocess ? setConfirming(true) : start.mutate())} />
          </Card>
          {gate.job && <JobPanel job={gate.job} />}
        </>
      )}
      {confirming && (
        <ConfirmDialog title="Redo OCR?" confirmLabel="Redo OCR" danger busy={start.isPending}
          onConfirm={() => start.mutate()} onCancel={() => setConfirming(false)}>
          {s.to_process} page(s) will be read again, replacing their current OCR results as each one finishes.
          Pages whose documents were already parsed keep their extractions until you parse again.
        </ConfirmDialog>
      )}
    </div>
  )
}
