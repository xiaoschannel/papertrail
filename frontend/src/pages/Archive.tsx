import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Card, ErrorState, Loading, Tile } from '../components/ui.tsx'
import './ingest.css'

export default function Archive() {
  const [confirming, setConfirming] = useState(false)
  const gate = useJobGate('archive', { everything: true })   // it moves every reviewed file
  const track = useTrackJob()
  const status = useQuery({ queryKey: ['ingest', 'archive'], queryFn: api.ingest.archive })
  const start = useMutation({
    mutationFn: api.ingest.startArchive,
    onSuccess: (job) => {
      setConfirming(false)
      track(job)
    },
    onError: () => setConfirming(false),
  })

  if (status.isPending) return <Loading what="archive plan" />
  if (status.error) return <ErrorState error={status.error} />
  const s = status.data
  const running = gate.job?.status === 'running'

  return (
    <div className="ingest-page">
      <h1>Archive</h1>
      <p className="page-sub">File every reviewed scan into the archive with its OCR, extraction and review.</p>
      <Card>
        <div className="tiles">
          <Tile label="Unarchived batches" value={s.unarchived_batches} />
          <Tile label="Ready batches" value={s.complete_batches} />
          <Tile label="Documents" value={s.documents} />
          <Tile label="Multi-page" value={s.multipage} />
          <Tile label="Files" value={s.files} />
          <Tile label="Accepted" value={s.accepted} />
          <Tile label="Marked" value={s.marked} />
          <Tile label="Tossed" value={s.tossed} />
        </div>
        {s.blocker && !running && <p className="ingest-note ingest-blocker">{s.blocker}</p>}
        {start.error && <div className="error-banner" role="alert">{start.error.message}</div>}
        <div className="start-bar">
          <button className="primary" disabled={Boolean(s.blocker) || running || gate.blockedBy !== null || start.isPending}
            onClick={() => setConfirming(true)}>
            Archive {s.files} file{s.files === 1 ? '' : 's'}
          </button>
          {gate.blockedBy && <span className="ingest-note">Waiting for {gate.blockedBy} to finish.</span>}
        </div>
      </Card>

      {gate.job && <JobPanel job={gate.job} />}

      {s.moves.length > 0 && (
        <Card title="Preview" hint={`${s.moves.length} file(s)`} className="card--uncapped">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Page</th><th>Scan</th><th>Destination</th></tr></thead>
              <tbody>
                {s.moves.map((m) => (
                  <tr key={m.key}>
                    <td>{m.key}{m.note && <> <span className="page-badge">{m.note}</span></>}</td>
                    <td>{m.filename}</td><td>{m.destination}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {confirming && (
        <ConfirmDialog title="Archive these files?" confirmLabel="Archive" busy={start.isPending}
          onConfirm={() => start.mutate()} onCancel={() => setConfirming(false)}>
          {s.files} scan(s) will be copied into the archive with their sidecars (the originals stay in the input
          folder). When every file is in place, the batches are marked archived and the working files
          (OCR results, extractions, decisions) are removed. If any file fails, nothing is finalized and you can
          run Archive again to finish.
        </ConfirmDialog>
      )}
    </div>
  )
}
