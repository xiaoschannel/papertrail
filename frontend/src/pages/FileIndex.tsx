import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client.ts'
import { useSaveConfig } from '../api/config.ts'
import type { IndexStatus } from '../api/types.ts'
import { useEverythingHolder } from '../components/jobs.tsx'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import './ingest.css'

export default function FileIndex() {
  return (
    <div className="ingest-page">
      <h1>File Index</h1>
      <p className="page-sub">
        Add newly scanned files as batches. Then <Link to="/straighten">straighten</Link> the scans that were
        fed in turned or crooked, <Link to="/slice">slice</Link> sheets of small receipts into one page each,
        and <Link to="/group">group</Link> pages that belong to one document.
      </p>
      <Batches />
    </div>
  )
}

/* --- batches ------------------------------------------------------------------------------------ */
function Batches() {
  const queryClient = useQueryClient()
  const [scheme, setScheme] = useState<string | undefined>(undefined)
  const saveConfig = useSaveConfig()
  // A new batch belongs to no job yet, so only a job holding everything (Archive) can stop you adding it.
  const archiving = useEverythingHolder()
  const status = useQuery({
    queryKey: ['ingest', 'index', scheme],
    queryFn: () => api.ingest.index(scheme),
    placeholderData: (previous) => previous,
  })
  const [added, setAdded] = useState(0)
  const confirm = useMutation({
    mutationFn: (s: IndexStatus) => api.ingest.confirmIndex({ scheme: s.scheme, token: s.token }),
    onSuccess: (_result, sent) => {
      setAdded(sent.proposal.length)
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })
    },
  })

  if (status.isPending) return <Loading what="scan folder" />
  if (status.error) return <ErrorState error={status.error} />
  const s = status.data
  if (s.blocker) return <Card title="Batches"><Empty>{s.blocker}</Empty></Card>

  return (
    <Card title="Batches" hint={`${s.existing_batches} batch(es) indexed`}>
      <div className="controls">
        <div className="field">
          <label htmlFor="index-scheme">Scanner naming scheme</label>
          <select id="index-scheme" value={s.scheme} onChange={(e) => {
            setScheme(e.target.value)
            saveConfig.mutate({ indexing_scheme: e.target.value })   // remembered as soon as it's picked
          }}>
            {s.schemes.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <span className="config-hint">
            Only files that aren't indexed yet are passed to the scheme; indexed files are never reassigned.
          </span>
        </div>
      </div>
      <div className="tiles">
        <Tile label="Images in folder" value={s.image_count} />
        <Tile label="Indexed" value={s.indexed_count} />
        <Tile label="New" value={s.unindexed_count} />
      </div>

      {s.error && (
        <div className="error-banner" role="alert">
          {s.error}
          {s.offending.length > 0 && (
            <details className="ingest-details">
              <summary>{s.offending.length} file(s) older than the last batch</summary>
              <ul className="file-list">{s.offending.map((f) => <li key={f}>{f}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {s.warnings.map((w) => <p key={w} className="ingest-note ingest-warning">{w}</p>)}
      {s.skipped.length > 0 && (
        <details className="ingest-details">
          <summary>{s.skipped.length} file(s) skipped (name doesn't match the scheme)</summary>
          <ul className="file-list">{s.skipped.map((f) => <li key={f}>{f}</li>)}</ul>
        </details>
      )}

      {s.proposal.length > 0 && (
        <>
          <h3 className="ingest-subhead">New batches</h3>
          {s.proposal.map((b) => (
            <details key={b.batch_id} className="ingest-details">
              <summary>Batch {b.batch_id} — {b.file_count} files — {b.start_datetime} to {b.end_datetime}</summary>
              <ul className="file-list">
                {b.files.map((f) => <li key={f.serial}><code>{String(f.serial).padStart(3, '0')}</code> {f.filename}</li>)}
              </ul>
            </details>
          ))}
          {confirm.error && <div className="error-banner" role="alert">{confirm.error.message}</div>}
          <div className="start-bar">
            <button className="primary" disabled={archiving !== null || confirm.isPending} onClick={() => confirm.mutate(s)}>
              Add {s.proposal.length} batch{s.proposal.length === 1 ? '' : 'es'}
            </button>
            {archiving && <span className="ingest-note">Waiting for {archiving.title} to finish.</span>}
          </div>
        </>
      )}
      {added > 0 && s.proposal.length === 0 && (
        <p className="ingest-note ok">
          Added {added} batch(es). <Link to="/slice">Slice</Link> any sheets of small receipts,
          then <Link to="/group">group</Link> their pages and run OCR.
        </p>
      )}
      {!s.error && s.proposal.length === 0 && added === 0 && <p className="ingest-note">No new files to index.</p>}
    </Card>
  )
}

