import { useState } from 'react'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { TiltedPages } from '../api/types.ts'
import type { ScanPage } from './turns.tsx'

/*
 * Scans fed in slightly crooked, as the Slice and Group pages point them out: a notice over the grid, a
 * badge on each tile, and, in the full-size viewer, a turn slider over level guides that previews the
 * scan straightened before anything is saved. Any scan can be straightened there, detected or not.
 */

/**
 * Suggestions set aside with "Leave as is" while the app is open, by scan and version (so on Slice and
 * Group alike): straightening or rotating the scan makes a new version, which is judged afresh.
 */
const leftAsIs = new Set<string>()
const scanVersion = (page: ScanPage) => `${page.filename}@${page.image_version}`

/** The batch's pages that look tilted and haven't been set aside, by key, with the turn that levels each. */
export function useTiltedPages(batchId: number, pages: Map<string, ScanPage>) {
  const [, setLeftCount] = useState(0)   // re-render when a suggestion is left as is
  const query = useQuery({ queryKey: ['ingest', 'tilted', batchId], queryFn: () => api.ingest.tilted(batchId) })
  const tilts = new Map<string, number>()
  for (const t of query.data?.pages ?? []) {
    // only for the scan as it was measured: one straightened since drops out until it has been measured again
    const page = pages.get(t.key)
    if (page && page.image_version === t.image_version && !leftAsIs.has(scanVersion(page))) tilts.set(t.key, t.degrees)
  }
  const setAside = (page: ScanPage) => {
    leftAsIs.add(scanVersion(page))
    setLeftCount((n) => n + 1)
  }
  return { query, tilts, setAside }
}

/** Says how many scans look tilted, once the batch has been checked, with the way to go through them. */
export function TiltNotice({ query, tilts, onReview }: {
  query: UseQueryResult<TiltedPages>
  tilts: Map<string, number>
  onReview: () => void
}) {
  if (query.isPending) return <p className="ingest-note">Checking the scans for tilt…</p>
  if (query.error) return <p className="ingest-note ingest-warning">Couldn’t check the scans for tilt: {query.error.message}</p>
  if (tilts.size === 0) return null
  return (
    <div className="suggestion-notice">
      <span>{tilts.size === 1 ? '1 scan looks' : `${tilts.size} scans look`} slightly tilted.</span>
      <button onClick={onReview}>Review {tilts.size === 1 ? 'it' : 'them'}</button>
    </div>
  )
}

/** A tile's mark for a scan that looks tilted; it opens the scan to straighten it. */
export function TiltBadge({ degrees, onOpen }: { degrees: number; onOpen: () => void }) {
  return (
    <button className="page-tile__tilt" onClick={onOpen}
      title={`Looks tilted ${Math.abs(degrees).toFixed(1)}° — check and straighten it`}>
      ∠ {Math.abs(degrees).toFixed(1)}°
    </button>
  )
}

/** How a turn reads to a person: which way, and how far. */
const describeTurn = (degrees: number) =>
  `${Math.abs(degrees).toFixed(1)}° ${degrees > 0 ? 'counter-clockwise' : 'clockwise'}`

/** Whether the viewer draws level guides over the scan: on until turned off, then off for every scan
 *  opened after, while the app is open. They are the reference for judging a tilt by eye, so they don't
 *  wait for one to be detected. */
let showGuides = true

/** The turn being tried on the scan in the viewer, and whether the guides are drawn. */
export function useStraightening(suggested: number | undefined) {
  const [degrees, setDegrees] = useState(suggested ?? 0)
  const [guides, setGuidesState] = useState(showGuides)
  const setGuides = (on: boolean) => {
    showGuides = on
    setGuidesState(on)
  }
  return { degrees, setDegrees, guides, setGuides }
}

/**
 * Under the full-size scan: the turn slider (it turns only the preview), the guides checkbox, and
 * Straighten, which saves the scan turned. A detected tilt presets the slider and offers "Leave as is".
 */
export function StraightenControls({ page, suggested, refusal, straightening, onStraightened, onLeave, note = null }: {
  page: ScanPage
  suggested: number | undefined
  /** Why the scan can't be straightened (a crop, a sliced sheet, a job using the batch), or null. */
  refusal: string | null
  straightening: ReturnType<typeof useStraightening>
  onStraightened: () => void
  onLeave: () => void
  /** A word on what the preview hides meanwhile (the trim rulers), or null. */
  note?: string | null
}) {
  const queryClient = useQueryClient()
  const { degrees, setDegrees, guides, setGuides } = straightening
  const start = suggested ?? 0
  const save = useMutation({
    mutationFn: () => api.ingest.straighten(page.key, degrees),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
      onStraightened()
    },
  })
  return (
    <div className="scan-viewer__suggest">
      {suggested !== undefined && (
        <span className="scan-viewer__suggestion scan-viewer__line">
          Looks tilted: turn {describeTurn(suggested)} to level it.
        </span>
      )}
      <span className="scan-viewer__dial">
        <label htmlFor="straighten-degrees">Straighten</label>
        <input id="straighten-degrees" type="range" min={-15} max={15} step={0.1} value={-degrees}
          aria-valuetext={degrees === 0 ? 'no turn' : describeTurn(degrees)}
          onChange={(e) => setDegrees(Math.round(-Number(e.target.value) * 10) / 10 || 0)} />
        <span className="scan-viewer__turn">{degrees === 0 ? 'no turn' : describeTurn(degrees)}</span>
      </span>
      {/* Always laid out, so the slider keeps its width while it is dragged. */}
      <button className={degrees === start ? 'scan-viewer__unused' : undefined} disabled={degrees === start}
        onClick={() => setDegrees(start)}>
        {suggested === undefined ? 'Reset' : 'Back to suggested'}
      </button>
      <label className="scan-viewer__check">
        <input type="checkbox" checked={guides} onChange={(e) => setGuides(e.target.checked)} /> Level guides
      </label>
      <span className="scan-viewer__actions">
        {suggested !== undefined && <button disabled={save.isPending} onClick={onLeave}>Leave as is</button>}
        <button className="primary" disabled={degrees === 0 || refusal !== null || save.isPending}
          title={refusal ?? 'Save the scan turned, over the original, keeping its corners'}
          onClick={() => save.mutate()}>
          Straighten
        </button>
      </span>
      {note && <span className="ingest-note">{note}</span>}
      {refusal && <span className="ingest-note ingest-warning">{refusal}</span>}
      {save.error && <span className="error-banner" role="alert">{save.error.message}</span>}
    </div>
  )
}
