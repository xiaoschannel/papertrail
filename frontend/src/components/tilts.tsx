import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import { setAsideChanged } from './setAside.ts'
import { keyLabel, useDialogKeys } from './useShortcuts.ts'
import type { ScanPage } from './turns.tsx'

/*
 * Scans fed in slightly crooked, as the Fix Rotation page points them out: a badge on each tile, and, in
 * the full-size viewer, a turn slider over level guides that previews the scan straightened before
 * anything is saved. Any scan can be straightened there, detected or not.
 */

/**
 * Suggestions set aside with "Leave as is" while the app is open, by scan and version: straightening or
 * rotating the scan makes a new version, which is judged afresh.
 */
const leftAsIs = new Set<string>()
const scanVersion = (page: ScanPage) => `${page.filename}@${page.image_version}`

/** Whether this version of the scan's suggestion was set aside (the sidebar's count leaves it out). */
export const isTiltLeftAsIs = (page: { filename: string; image_version: number }) =>
  leftAsIs.has(`${page.filename}@${page.image_version}`)

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
    setAsideChanged()
  }
  return { query, tilts, setAside }
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
 * Every row is always laid out, so nothing moves from one scan to the next. ``leaveKey`` says whether
 * the Leave as is key is this one's (it is the turn's while a turn is suggested).
 */
export function StraightenControls({ page, suggested, refusal, straightening, onStraightened, onLeave, leaveKey }: {
  page: ScanPage
  suggested: number | undefined
  /** Why the scan can't be straightened (a crop, a sliced sheet, a job using the batch), or null. */
  refusal: string | null
  straightening: ReturnType<typeof useStraightening>
  onStraightened: () => void
  onLeave: () => void
  leaveKey: boolean
}) {
  const queryClient = useQueryClient()
  const keys = useShortcutKeys()
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
  const canSave = degrees !== 0 && refusal === null && !save.isPending
  useDialogKeys(keys ? {
    [keys.toggle_guides]: () => setGuides(!guides),
    [keys.straighten]: canSave ? () => save.mutate() : undefined,
    ...(leaveKey && suggested !== undefined && !save.isPending ? { [keys.leave_as_is]: onLeave } : {}),
  } : {})
  const kbd = (key: string | undefined) => key && <kbd>{keyLabel(key)}</kbd>
  return (
    <div className="scan-viewer__suggest">
      <span className={`scan-viewer__line${suggested !== undefined ? ' scan-viewer__suggestion' : ''}`}>
        {suggested !== undefined ? `Looks tilted: turn ${describeTurn(suggested)} to level it.` : 'Looks level.'}
      </span>
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
        {kbd(keys?.toggle_guides)}
      </label>
      <span className="scan-viewer__actions">
        <button className={suggested === undefined ? 'scan-viewer__unused' : undefined}
          disabled={suggested === undefined || save.isPending} onClick={onLeave}>
          Leave as is{leaveKey && <> {kbd(keys?.leave_as_is)}</>}
        </button>
        <button className="primary" disabled={!canSave}
          title={refusal ?? 'Save the scan turned, over the original, keeping its corners'}
          onClick={() => save.mutate()}>
          Straighten {kbd(keys?.straighten)}
        </button>
      </span>
      {refusal && <span className="ingest-note ingest-warning">{refusal}</span>}
      {save.error && <span className="error-banner" role="alert">{save.error.message}</span>}
    </div>
  )
}
