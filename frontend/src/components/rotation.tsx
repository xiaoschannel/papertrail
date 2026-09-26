import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, inputUrl } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { RotationDecision, RotationPrediction, TopPoints } from '../api/types.ts'
import { ARROWS, FACING, ScanCompare, ScanViewer } from './scans.tsx'
import { keyLabel, useDialogKeys, useHeldKey } from './useShortcuts.ts'

/*
 * Deciding on a scan's rotation, as Fix Rotation's queue and its all-scans view do: the scan beside it as
 * the fix would leave it, a turn (preset from the detectors, any of the four), a straightening slider
 * (preset from the tilt measured on the scan turned), and Fix or Leave as is. Every decision is kept on
 * the server as training data for the detectors (rotation_review.py).
 */

/** The CSS turn (clockwise) that shows a scan whose top points this way upright. */
export const UPRIGHT_TURN: Record<TopPoints, number> = { left: 90, right: -90, down: 180 }

/** A scan to decide on: the page, its scan version, and what the detectors said of it. */
export type Decidable = { key: string; filename: string; image_version: number; prediction: RotationPrediction;
  sent_back: boolean }

/** How a turn reads to a person: which way, and how far. */
export const describeTurn = (degrees: number) =>
  `${Math.abs(degrees).toFixed(1)}° ${degrees > 0 ? 'counter-clockwise' : 'clockwise'}`

/** What the detectors said, in a line. */
export function describePrediction(p: RotationPrediction, sentBack: boolean): string {
  const said = [
    p.turn && `Looks ${FACING[p.turn]}`,
    p.tilt !== null && p.tilt !== undefined && `${p.turn ? 'turned upright, it is' : 'Looks'} tilted: ${describeTurn(p.tilt)} levels it`,
  ].filter(Boolean).join('; ')
  if (sentBack) return `Sent back from Review: the detectors missed it${said ? ` (they said: ${said.toLowerCase()})` : ''}.`
  return said ? `${said}.` : 'Looks upright and level.'
}

/** Whether the level guides are drawn over the scan: on until turned off, then off for every scan after,
 *  while the app is open. Holding the guides key flips them while it is held. */
let showGuides = true

/** What a decision did, in a few words. */
export function describeDecision(d: RotationDecision): string {
  if (d.action === 'left') return 'left as is'
  if (d.action === 'sent_back') return 'sent back from Review'
  if (d.action === 'undone') return 'fix undone'
  const parts = [d.top_points && `turned upright from ${FACING[d.top_points]}`, d.degrees && `straightened ${describeTurn(d.degrees)}`]
  return parts.filter(Boolean).join(', then ')
}

/**
 * The controls and the preview for one scan. ``layout`` "inline" draws them in the page (the queue), with the
 * preview above the controls; "dialog" in the scan viewer (the all-scans view), which ``onClose`` closes.
 * The controls are laid out the same for every scan, so each key and button stays in one place.
 */
export function RotationDecider({ scan, source, locked, layout, maxHeight, header, onDecided, onClose }: {
  scan: Decidable
  source: 'queue' | 'browse'
  /** Why deciding has to wait (a job is using the batch), or null. */
  locked: string | null
  layout: 'inline' | 'dialog'
  /** The preview's height, inline. */
  maxHeight?: number | undefined
  /** Over the preview, inline: where the scan is in the queue. */
  header?: ReactNode
  onDecided: (decision: RotationDecision) => void
  onClose?: () => void
}) {
  const queryClient = useQueryClient()
  const keys = useShortcutKeys()
  const p = scan.prediction
  // What to start from: the detectors' suggestion; for a page sent back (they missed it), what they measured
  // even under their thresholds, since someone has already said it needs fixing.
  const startTop = p.turn ?? (scan.sent_back ? p.top_points ?? null : null)
  const startTilt = p.tilt ?? (scan.sent_back ? p.degrees : 0)
  const [top, setTop] = useState<TopPoints | null>(startTop)
  const [degrees, setDegrees] = useState<number>(startTilt)
  const [guides, setGuidesState] = useState(showGuides)
  const setGuides = (on: boolean) => {
    showGuides = on
    setGuidesState(on)
  }
  // where the ink is, so a straightening previewed alone crops as straightening will
  const outline = useQuery({
    queryKey: ['ingest', 'ink-outline', scan.key, scan.image_version],
    queryFn: () => api.ingest.inkOutline(scan.key),
    enabled: degrees !== 0 && top === null,
    staleTime: Infinity,
  })
  // the preview waits for the outline; should it fail, it crops to the levelled page alone rather than wait on
  const outlinePoints = outline.isError ? [] : outline.data?.points
  const decide = useMutation({
    mutationFn: (fix: boolean) => api.ingest.decideRotation({
      key: scan.key, image_version: scan.image_version, fix, top_points: fix ? top : null, degrees: fix ? degrees : 0, source,
    }),
    onSuccess: async (decision) => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
      onDecided(decision)
    },
    // refused (the scan changed since it was shown, say): show it as it is now, so the next try can take
    onError: () => void queryClient.invalidateQueries({ queryKey: ['ingest', 'rotation'] }),
  })
  const canFix = locked === null && !decide.isPending && (top !== null || degrees !== 0)
  const canLeave = locked === null && !decide.isPending
  useDialogKeys(keys ? {
    [keys.fix_scan]: canFix ? () => decide.mutate(true) : undefined,
    [keys.leave_as_is]: canLeave ? () => decide.mutate(false) : undefined,
  } : {}, true, layout === 'inline')
  const flipped = useHeldKey(keys?.toggle_guides ?? '', keys !== undefined, true)
  const shown = guides !== flipped
  const kbd = (key: string | undefined) => key && <kbd>{keyLabel(key)}</kbd>

  const controls = (
    <div className="scan-viewer__suggest rotation-controls">
      <span className={`scan-viewer__line${p.turn || p.tilt !== null || scan.sent_back ? ' scan-viewer__suggestion' : ''}`}>
        {describePrediction(p, scan.sent_back)}
      </span>
      <span className="rotation-controls__turn" role="group" aria-label="Turn">
        <span>Turn</span>
        <span className="segmented">
          {/* the suggested one is coloured, not marked, so the four stay one size */}
          {([null, ...ARROWS.map((a) => a.top)] as (TopPoints | null)[]).map((to) => (
            <button key={to ?? 'up'} onClick={() => setTop(to)}
              className={[top === to && 'on', to === startTop && 'is-suggested'].filter(Boolean).join(' ') || undefined}
              title={`Its top points ${to ?? 'up'}: ${to ? 'turn it upright' : 'no turn'}${to === startTop ? ' (suggested)' : ''}`}>
              {to ? ARROWS.find((a) => a.top === to)?.label : '↑'}
            </button>
          ))}
        </span>
      </span>
      <span className="scan-viewer__dial">
        <label htmlFor={`straighten-${layout}`}>Straighten</label>
        <input id={`straighten-${layout}`} type="range" min={-15} max={15} step={0.1} value={-degrees}
          aria-valuetext={degrees === 0 ? 'no turn' : describeTurn(degrees)}
          onChange={(e) => setDegrees(Math.round(-Number(e.target.value) * 10) / 10 || 0)} />
        <span className="scan-viewer__turn">{degrees === 0 ? 'no turn' : describeTurn(degrees)}</span>
        {/* Always laid out, so the slider keeps its width while it is dragged. */}
        <button className={degrees === startTilt ? 'scan-viewer__unused' : undefined} disabled={degrees === startTilt}
          onClick={() => setDegrees(startTilt)}>
          {startTilt === 0 ? 'Reset' : 'Back to suggested'}
        </button>
      </span>
      <label className="scan-viewer__check">
        <input type="checkbox" checked={guides} onChange={(e) => setGuides(e.target.checked)} /> Level guides
        <span className="rotation-controls__hold">(hold {kbd(keys?.toggle_guides)} to {guides ? 'hide' : 'show'})</span>
      </label>
      <span className="scan-viewer__actions">
        <button disabled={!canLeave} onClick={() => decide.mutate(false)}
          title="Keep the scan as it is; the detectors' suggestion is kept as a wrong one">
          Leave as is {kbd(keys?.leave_as_is)}
        </button>
        <button className="primary" disabled={!canFix} onClick={() => decide.mutate(true)}
          title={locked ?? 'Save the scan turned and straightened as previewed, over the original (the history keeps it)'}>
          Fix {kbd(keys?.fix_scan)}
        </button>
      </span>
      {locked && <span className="ingest-note ingest-warning scan-viewer__line">{locked}</span>}
      {decide.error && <span className="error-banner scan-viewer__line" role="alert">{decide.error.message}</span>}
    </div>
  )

  const turn = top ? UPRIGHT_TURN[top] : 0
  if (layout === 'dialog') {
    return (
      <ScanViewer label={scan.key} filename={scan.filename} version={scan.image_version} onClose={onClose ?? (() => {})}
        turn={turn} tilt={degrees} outline={outlinePoints} guides={shown} footer={controls} pair />
    )
  }
  return (
    <div className={`rotation-decider${shown ? ' scan-viewer--guides' : ''}`}>
      {header}
      {/* a fixed height, whatever the scan's shape, so the controls under it keep their place */}
      <div className="rotation-decider__stage" style={maxHeight === undefined ? undefined : { height: maxHeight + 24 }}>
        <ScanCompare src={inputUrl(scan.filename, scan.image_version)} alt={`Scan ${scan.key}`} turn={turn} tilt={degrees}
          outline={outlinePoints} maxHeight={maxHeight} pair />
      </div>
      {controls}
    </div>
  )
}

/** The latest decisions, newest first, each fix with Undo while it is the page's last and its scan unchanged. */
export function RecentRotations({ recent }: { recent: { decision: RotationDecision; undoable: boolean }[] }) {
  const queryClient = useQueryClient()
  const undo = useMutation({
    mutationFn: (key: string) => api.ingest.undoRotation(key),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
    },
  })
  if (recent.length === 0) return null
  return (
    <>
      {undo.error && <div className="error-banner" role="alert">{undo.error.message}</div>}
      <ul className="rotation-recent">
        {recent.map(({ decision: d, undoable }) => (
          <li key={`${d.key}-${d.at}`}>
            <strong>{d.key}</strong> {describeDecision(d)}
            {d.agrees === false && d.action !== 'sent_back' && <span className="page-badge">not as suggested</span>}
            {undoable && <button disabled={undo.isPending} onClick={() => undo.mutate(d.key)}>Undo</button>}
          </li>
        ))}
      </ul>
    </>
  )
}
