import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { TopPoints, TurnArchive as TurnArchiveState } from '../api/types.ts'
import { ARROWS, FACING } from '../components/scans.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './dev.css'
import './ingest.css'

/** The CSS turn (clockwise) that shows a scan whose top points this way upright. */
const UPRIGHT_TURN: Record<TopPoints, number> = { left: 90, right: -90, down: 180 }

/** Suggestions set aside with "Leave as is" while the app is open. */
const leftAsIs = new Set<string>()

/**
 * One-off migration: archived scans that look sideways or upside down, each turned upright only when
 * confirmed, with a backup that Undo puts back. Temporary: kept as a git tag, not shipped.
 */
export default function TurnArchive() {
  const queryClient = useQueryClient()
  // bumped after every change, so the thumbnails of a rewritten scan aren't served from the browser's cache
  const [stamp, setStamp] = useState(0)
  const [, setLeft] = useState(0)
  const state = useQuery({
    queryKey: ['dev', 'turn-archive'],
    queryFn: api.dev.turnArchive,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : false),
  })
  const done = (result: TurnArchiveState) => {
    queryClient.setQueryData(['dev', 'turn-archive'], result)
    void queryClient.invalidateQueries({ queryKey: ['viz'] })
    setStamp((n) => n + 1)
  }
  const scan = useMutation({ mutationFn: api.dev.scanTurnArchive, onSuccess: done })
  const turn = useMutation({ mutationFn: api.dev.turnArchived, onSuccess: done })
  const undo = useMutation({ mutationFn: api.dev.undoTurnArchived, onSuccess: done })

  if (state.isPending) return <Loading what="the archive check" />
  if (state.error) return <ErrorState error={state.error} />
  const s = state.data
  const busy = turn.isPending || undo.isPending
  const found = s.found.filter((f) => !leftAsIs.has(f.rel_path))
  const thumb = (rel: string) => api.dev.turnArchiveThumb(rel, stamp)
  const error = (scan.error ?? turn.error ?? undo.error)?.message

  return (
    <div className="dev-page">
      <h1>Turn Archive</h1>
      <p className="page-sub">
        A one-off pass over scans filed before Slice and Group pointed out turned pages: every scan in the archive
        (filed, marked and tossed) is checked, and each one that looks sideways or upside down is turned upright only
        when you say so. Its OCR boxes turn with it, and a backup is kept, so Undo puts it back exactly. Batches not
        archived yet are checked on <Link className="rowlink" to="/slice">Slice</Link> and{' '}
        <Link className="rowlink" to="/group">Group</Link>.
      </p>
      {error && <div className="error-banner" role="alert">{error}</div>}

      <Card title="Check the archive" hint={s.total ? `${s.done} of ${s.total} scans looked at` : undefined}>
        <div className="start-bar">
          <button className="primary" disabled={s.running || scan.isPending} onClick={() => scan.mutate()}>
            {s.running ? 'Checking…' : s.total ? 'Check again' : 'Check every archived scan'}
          </button>
          {s.running && <progress max={s.total || 1} value={s.done} />}
          {!s.running && s.total > 0 && (
            <span className="ingest-note">
              Looked at {s.total} scans{s.unreadable ? `; ${s.unreadable} couldn’t be read` : ''}.
            </span>
          )}
        </div>
        {s.error && <p className="ingest-note ingest-warning">The check stopped: {s.error}</p>}
      </Card>

      <Card title="Look turned" className="card--full"
        hint={found.length ? `${found.length} scan${found.length === 1 ? '' : 's'}, most certain first` : undefined}>
        {found.length === 0 ? (
          <Empty>{s.running ? 'Nothing yet.' : s.total ? 'No archived scan looks turned.' : 'Not checked yet.'}</Empty>
        ) : (
          <div className="turn-archive-grid">
            {found.map((f) => (
              <figure key={f.rel_path} className="turn-archive-tile">
                <div className="turn-archive-pair">
                  <a href={api.dev.archivedUrl(f.rel_path)} target="_blank" rel="noreferrer" title="Open full size">
                    <img src={thumb(f.rel_path)} alt={`${f.rel_path} as filed`} loading="lazy" />
                  </a>
                  <span className="turn-archive-upright">
                    <img src={thumb(f.rel_path)} alt={`${f.rel_path} turned upright`} loading="lazy"
                      style={{ transform: `rotate(${UPRIGHT_TURN[f.top_points]}deg)` }} />
                  </span>
                </div>
                <figcaption>
                  <span className="turn-archive-path">{f.rel_path}</span>
                  <span>Looks {FACING[f.top_points]} ({Math.round(f.confidence * 100)}%). Left: as filed; right: turned.</span>
                  <span className="turn-archive-actions">
                    <button disabled={busy} onClick={() => { leftAsIs.add(f.rel_path); setLeft((n) => n + 1) }}>
                      Leave as is
                    </button>
                    <button className="primary" disabled={busy}
                      title={`Turn it upright: its ${ARROWS.find((a) => a.top === f.top_points)?.label} arrow`}
                      onClick={() => turn.mutate({ rel_path: f.rel_path, top_points: f.top_points })}>
                      Turn upright
                    </button>
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </Card>

      <Card title="Turned" hint={s.turned.length ? `${s.turned.length} scan${s.turned.length === 1 ? '' : 's'}, backed up` : undefined}>
        {s.turned.length === 0 ? <Empty>None turned yet.</Empty> : (
          <div className="turn-archive-grid">
            {s.turned.map((rel) => (
              <figure key={rel} className="turn-archive-tile">
                <div className="turn-archive-pair">
                  <a href={api.dev.archivedUrl(rel)} target="_blank" rel="noreferrer" title="Open full size">
                    <img src={thumb(rel)} alt={`${rel} as it is now`} loading="lazy" />
                  </a>
                </div>
                <figcaption>
                  <span className="turn-archive-path">{rel}</span>
                  <span className="turn-archive-actions">
                    <button disabled={busy} onClick={() => undo.mutate({ rel_path: rel })}>Undo</button>
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
