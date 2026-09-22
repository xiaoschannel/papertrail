import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { TopPoints } from '../api/types.ts'
import { ARROWS, FACING, ScanViewer } from './scans.tsx'
import { StraightenControls, useStraightening } from './tilts.tsx'

/*
 * Scans that look sideways or upside down, as the Straighten page points them out: the suggested arrow lit
 * on each tile, and a review that shows each page turned upright in the full-size viewer before anything
 * is saved. Turning one is the page's own rotate arrow.
 */

/** What these need of a page. */
export interface ScanPage {
  key: string
  filename: string
  /** The scan file's mtime: a suggestion is for the version it was measured on. */
  image_version: number
}

/** The CSS turn (clockwise) that shows a scan whose top points this way upright. */
const UPRIGHT_TURN: Record<TopPoints, number> = { left: 90, right: -90, down: 180 }

/**
 * Suggestions set aside with "Leave as is" while the app is open, by scan and version: rotating the scan
 * makes a new version, which is judged afresh.
 */
const leftAsIs = new Set<string>()
const scanVersion = (page: ScanPage) => `${page.filename}@${page.image_version}`

/** The batch's pages that look turned and haven't been set aside, by key, with where each one's top points. */
export function useTurnedPages(batchId: number, pages: Map<string, ScanPage>) {
  const [, setLeftCount] = useState(0)   // re-render when a suggestion is left as is
  const query = useQuery({ queryKey: ['ingest', 'turned', batchId], queryFn: () => api.ingest.turned(batchId) })
  const turns = new Map<string, TopPoints>()
  for (const t of query.data?.pages ?? []) {
    // only for the scan as it was measured: one rotated since drops out until it has been measured again
    const page = pages.get(t.key)
    if (page && page.image_version === t.image_version && !leftAsIs.has(scanVersion(page))) turns.set(t.key, t.top_points)
  }
  const setAside = (page: ScanPage) => {
    leftAsIs.add(scanVersion(page))
    setLeftCount((n) => n + 1)
  }
  return { query, turns, setAside }
}

/** "1 upside down" or "2 sideways" or "1 upside down and 2 sideways": the scans that look turned. */
export function describeTurned(turns: TopPoints[]): string {
  const down = turns.filter((t) => t === 'down').length
  const sideways = turns.length - down
  return [down > 0 && `${down} upside down`, sideways > 0 && `${sideways} sideways`].filter(Boolean).join(' and ')
}

/** Pages open in the full-size viewer: one opened on its own, or the ones that look turned or tilted, stepped
 *  through under review. */
export type Viewing = { keys: string[]; at: number; review: boolean }

/**
 * The page ``viewing`` is at, full size. One that looks turned is shown as it would be turned upright
 * (a checkbox shows it as scanned), with "Turn upright" and "Leave as is"; under it, any page can be
 * straightened, starting from its detected tilt if it looks tilted. Reviewing steps on to the next page
 * that still looks turned or tilted, and a page opened on its own stays open, showing the saved scan.
 */
export function PageViewer({ viewing, pages, turns, tilts, locked, cannotTurn, onSetAside, onSetAsideTilt, onMove,
  onClose }: {
  viewing: Viewing
  pages: Map<string, ScanPage>
  turns: Map<string, TopPoints>
  tilts: Map<string, number>
  /** Why rotating has to wait (a job is using the batch), or null. */
  locked: string | null
  /** Why a page can't be turned at all (a crop, a sliced sheet), or null. */
  cannotTurn: (key: string) => string | null
  onSetAside: (page: ScanPage) => void
  onSetAsideTilt: (page: ScanPage) => void
  onMove: (viewing: Viewing | null) => void
  onClose: () => void
}) {
  const flagged = (k: string) => turns.has(k) || tilts.has(k)
  const key = viewing.review ? viewing.keys.slice(viewing.at).find(flagged) : viewing.keys[viewing.at]
  const page = key === undefined ? undefined : pages.get(key)
  const done = key === undefined || page === undefined
  useEffect(() => { if (done) onClose() }, [done, onClose])   // reviewed past the last one
  if (key === undefined || page === undefined) return null
  const at = viewing.keys.indexOf(key)
  const next = () => onMove(viewing.review ? { ...viewing, at: at + 1 } : viewing)
  const top = turns.get(key)
  const tilt = tilts.get(key)
  const leave = (setAside: (page: ScanPage) => void, still: boolean) => () => {
    setAside(page)
    if (still) return          // left the turn as is, but it looks tilted too: that is offered next
    if (viewing.review) next()
    else onMove(null)
  }
  return (
    // Keyed by the scan's version and its tilt: a saved turn, or a tilt measured after the viewer opened,
    // starts the slider again from the scan as it is now.
    <ScanViewerWithTurn key={`${scanVersion(page)}|${tilt ?? ''}`} page={page} top={top} tilt={tilt}
      position={viewing.keys.length > 1 ? `${at + 1} of ${viewing.keys.length}` : ''} locked={locked}
      cannotTurn={cannotTurn(key)} onTurned={next} onLeave={leave(onSetAside, tilt !== undefined)} onLeaveTilt={leave(onSetAsideTilt, false)}
      onClose={onClose} />
  )
}

function ScanViewerWithTurn({ page, top, tilt, position, locked, cannotTurn, onTurned, onLeave, onLeaveTilt,
  onClose }: {
  page: ScanPage
  top: TopPoints | undefined
  tilt: number | undefined
  position: string
  locked: string | null
  cannotTurn: string | null
  onTurned: () => void
  onLeave: () => void
  onLeaveTilt: () => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [preview, setPreview] = useState(true)
  const straightening = useStraightening(tilt)
  // where the ink is, so the preview crops as straightening will (only asked for once a turn is previewed)
  const outline = useQuery({
    queryKey: ['ingest', 'ink-outline', page.key, page.image_version],
    queryFn: () => api.ingest.inkOutline(page.key),
    enabled: straightening.degrees !== 0,
    staleTime: Infinity,
  })
  const rotate = useMutation({
    mutationFn: (to: TopPoints) => api.ingest.rotate(page.key, to),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
      onTurned()
    },
  })
  const label = position ? `${page.key} — ${position}` : page.key
  const straighten = (
    <StraightenControls page={page} suggested={tilt} refusal={cannotTurn ?? locked} straightening={straightening}
      onStraightened={onTurned} onLeave={onLeaveTilt} />
  )
  const viewed = { label, filename: page.filename, version: page.image_version, onClose,
    tilt: straightening.degrees, outline: outline.data?.points, guides: straightening.guides }
  if (top === undefined) return <ScanViewer {...viewed} footer={straighten} />
  const arrow = ARROWS.find((a) => a.top === top)?.label
  return (
    <ScanViewer {...viewed} turn={preview ? UPRIGHT_TURN[top] : 0}
      footer={(<>
        <div className="scan-viewer__suggest">
          <span className="scan-viewer__suggestion">Looks {FACING[top]}.</span>
          <label className="scan-viewer__check">
            <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} /> Preview upright
          </label>
          <span className="scan-viewer__actions">
            <button disabled={rotate.isPending} onClick={onLeave}>Leave as is</button>
            <button className="primary" disabled={locked !== null || rotate.isPending}
              title={`Save the scan turned upright, over the original: its ${arrow} arrow`}
              onClick={() => rotate.mutate(top)}>
              Turn upright
            </button>
          </span>
          {locked && <span className="ingest-note ingest-warning">{locked}</span>}
          {rotate.error && <span className="error-banner" role="alert">{rotate.error.message}</span>}
        </div>
        {straighten}
      </>)} />
  )
}
