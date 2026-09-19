import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useDebounced } from '../components/useDebounced.ts'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, workshopScanUrl } from '../api/client.ts'
import type { ContextScan, Enhancement, Job, ReviewDocument } from '../api/types.ts'
import { DocumentCard } from '../components/DocumentCard.tsx'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Markdown } from '../components/Markdown.tsx'
import {
  INPUT_SOURCES, ReviewForm, initialForm, parseCost, type FormState,
} from '../components/review/ReviewForm.tsx'
import { ScanOverlay } from '../components/review/ScanOverlay.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import { afterArchiveEdit } from '../api/invalidate.ts'
import { money } from '../format.ts'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import '../components/review/review.css'
import './curate.css'

const TREATMENTS = [
  ['none', 'As scanned'], ['clahe', 'Local contrast'], ['contrast', 'Contrast + gamma'], ['whiten', 'Whiten paper'],
] as const

const ORIENTATIONS = [['', '↑ upright'], ['left', '← top left'], ['right', '→ top right'], ['down', '↓ upside down']] as const

const DEFAULT_ENHANCEMENT: Enhancement = {
  top_points: '', treatment: 'none', clip: 3, grid: 8, contrast: 2.5, gamma: 0.5, lightness: 200, chroma: 10,
}

/** Colors per verdict, as review_logic's VERDICT_COLORS. */
const VERDICT_COLORS: Record<string, string> = { accepted: '#28a745', marked: '#ffc107', tossed: '#6c757d' }

/** The comment review left ("why is this marked?") is part of the document, not a blank field. */
const startForm = (doc: ReviewDocument): FormState => ({ ...initialForm(doc), comment: doc.decision?.comment ?? '' })

/**
 * The bench for documents review marked instead of deciding: treat the scan until OCR can read it,
 * re-run the models, then accept or toss. Laid out like Review (OCR text | scan | decision), and the
 * decision form is Review's own component, so the two queues can't drift apart. Below, the week around
 * the receipt and the batch it was scanned in help work out what it is.
 */
export default function Workshop() {
  const queryClient = useQueryClient()
  const [key, setKey] = useState<string | null>(null)
  const [enhancement, setEnhancement] = useState<Enhancement>(DEFAULT_ENHANCEMENT)
  const [form, setForm] = useState<FormState | null>(null)
  const [activeFields, setActiveFields] = useState<readonly string[]>([])
  const gate = useJobGate('workshop', { gpu: true })   // reprocessing runs OCR on this machine's GPU
  const track = useTrackJob()

  const workshop = useQuery({
    queryKey: ['curate', 'workshop', key],
    queryFn: () => api.workshop.queue(key ?? undefined),
  })
  const document = workshop.data?.document ?? null

  // Each document starts from its own stored values and an untreated scan, as Streamlit kept them per file.
  const documentKey = document?.key
  useEffect(() => setEnhancement(DEFAULT_ENHANCEMENT), [documentKey])
  useEffect(() => setForm(document ? startForm(document) : null), [document])
  // A finished reread has turned the files upright, so the preview must stop turning them.
  const finished = gate.job?.status === 'succeeded' ? gate.job.id : null
  useEffect(() => {
    if (finished) setEnhancement((current) => ({ ...current, top_points: '' }))
  }, [finished])

  if (workshop.isPending) return <Loading what="marked documents" />
  if (workshop.error) return <ErrorState error={workshop.error} />
  const { documents, ocr_models: ocrModels, extractors } = workshop.data
  const position = documents.findIndex((d) => d.key === document?.key)

  const move = (step: number) => {
    const next = documents[position + step]
    if (next) setKey(next.key)
  }

  return (
    <div className="curate-page workshop-page review-page">
      <h1>Marked Workshop</h1>
      <p className="page-sub">Documents you marked during review: make the scan readable, then decide.</p>

      {documents.length === 0 || !document || !form ? <Empty>Nothing is marked. Review is where documents get marked.</Empty> : (
        <>
          <div className="review-nav">
            <button disabled={position <= 0} onClick={() => move(-1)}>← Prev</button>
            <button disabled={position < 0 || position >= documents.length - 1} onClick={() => move(1)}>Next →</button>
            <span><strong>{position + 1} / {documents.length}</strong> — {document.key}</span>
            {documents[position]?.comment && <span className="config-hint">“{documents[position].comment}”</span>}
          </div>

          <div className="review-layout">
            <Card title="OCR text" className="review-col">
              {document.ocr_text
                ? <Markdown className="textdump review-ocr" source={document.ocr_text} />
                : <p className="ingest-note">No OCR text yet. Read it again to get some.</p>}
            </Card>

            <Scan document={document} enhancement={enhancement} onChange={setEnhancement}
              ocrModels={ocrModels} extractors={extractors}
              ocrModel={workshop.data.ocr_model} extractor={workshop.data.extractor}
              blockedBy={gate.blockedBy} job={gate.job} revision={finished ?? ''}
              activeFields={activeFields} onHoverField={(field) => setActiveFields(field ? [field] : [])}
              onStarted={(job) => {
                track(job)
                void queryClient.invalidateQueries({ queryKey: ['curate', 'workshop'] })
              }} />

            <Decision key={document.key} document={document} form={form} onChange={setForm}
              activeFields={activeFields}
              onActivate={(input) => setActiveFields(input ? INPUT_SOURCES[input] ?? [] : [])}
              onDecided={() => {
                // Keep your place, as Streamlit did: the next document moves into this slot (the previous
                // one when this was the last). The server's own answer would start over from the first.
                const after = documents[position + 1] ?? documents[position - 1]
                setKey(after?.key ?? null)
                void afterArchiveEdit(queryClient, ['curate', 'workshop'])
              }} />
          </div>

          <Context document={document} form={form} />
        </>
      )}
    </div>
  )
}

function Scan({
  document, enhancement, onChange, ocrModels, extractors, ocrModel, extractor, blockedBy, job, revision,
  activeFields, onHoverField, onStarted,
}: {
  document: ReviewDocument
  enhancement: Enhancement
  onChange: (enhancement: Enhancement) => void
  ocrModels: string[]
  extractors: string[]
  ocrModel: string
  extractor: string
  blockedBy: string | null
  /** The last reread (running or finished), shown under its button: its errors can be long, so it gets
   *  the column's height rather than a full-width row under the layout. */
  job: Job | null
  /** Changes when a reread has rewritten the files, so the scans are fetched again. */
  revision: string
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
  onStarted: (job: Parameters<ReturnType<typeof useTrackJob>>[0]) => void
}) {
  const [models, setModels] = useState({ ocr: ocrModel, extractor })
  const [comparing, setComparing] = useState(false)
  const running = job?.status === 'running'
  // Dragging a slider shouldn't ask the server for a full-size render per step.
  const settled = useDebounced(enhancement, 250)
  const set = <K extends keyof Enhancement>(field: K, value: Enhancement[K]) =>
    onChange({ ...enhancement, [field]: value })
  const hasScans = document.pages.some((page) => page.image_available)
  const treated = enhancement.top_points !== '' || enhancement.treatment !== 'none'
  useHeldKey('o', setComparing, treated)

  const reprocess = useMutation({
    mutationFn: () => api.workshop.reprocess({
      ...enhancement, key: document.key, ocr_model: models.ocr, extractor: models.extractor,
    }),
    onSuccess: onStarted,
  })
  const hold = {
    onPointerDown: () => setComparing(true),
    onPointerUp: () => setComparing(false),
    onPointerLeave: () => setComparing(false),
    onPointerCancel: () => setComparing(false),
  }

  return (
    <Card title="Scan" className="review-col"
      hint={comparing ? 'as scanned' : document.pages.length > 1 ? `${document.pages.length} pages` : 'as OCR will see it'}>
      {/* Every page, treated the same way — a reprocess re-reads all of them, so you should be able to
          see all of them before deciding. */}
      {document.pages.length === 0 ? <Empty>No scan on disk.</Empty> : (
        <ScanOverlay pages={document.pages} activeFields={activeFields} onHoverField={onHoverField}
          imageUrl={(filename) => workshopScanUrl(filename, { ...settled, v: revision })}
          originalUrl={(filename) => workshopScanUrl(filename, { ...DEFAULT_ENHANCEMENT, v: revision })}
          showOriginal={comparing && treated} turn={settled.top_points} missing="is not in marked/" />
      )}

      <div className="start-bar workshop-compare">
        <button disabled={!treated} {...hold}>Hold to see the original</button>
        <span className="config-hint">{treated ? 'or hold O' : 'nothing is treated yet'}</span>
      </div>

      <div className="field">
        <label>The top of the page points</label>
        <div className="segmented">
          {ORIENTATIONS.map(([value, label]) => (
            <button key={value} className={enhancement.top_points === value ? 'on' : ''}
              onClick={() => set('top_points', value)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Treatment</label>
        <div className="segmented">
          {TREATMENTS.map(([value, label]) => (
            <button key={value} className={enhancement.treatment === value ? 'on' : ''}
              onClick={() => set('treatment', value)}>{label}</button>
          ))}
        </div>
      </div>

      {enhancement.treatment === 'clahe' && (
        <div className="curate-grid">
          <Slider label="Strength" value={enhancement.clip} min={1} max={10} step={0.5}
            onChange={(v) => set('clip', v)} />
          <Slider label="Detail size" value={enhancement.grid} min={2} max={16} step={1}
            onChange={(v) => set('grid', v)} />
        </div>
      )}
      {enhancement.treatment === 'contrast' && (
        <div className="curate-grid">
          <Slider label="Contrast" value={enhancement.contrast} min={0.5} max={3} step={0.1}
            onChange={(v) => set('contrast', v)} />
          <Slider label="Gamma" value={enhancement.gamma} min={0.2} max={3} step={0.1}
            onChange={(v) => set('gamma', v)} />
        </div>
      )}
      {enhancement.treatment === 'whiten' && (
        <div className="curate-grid">
          <Slider label="Lightness floor" value={enhancement.lightness} min={128} max={255} step={1}
            onChange={(v) => set('lightness', v)} />
          <Slider label="Colour floor" value={enhancement.chroma} min={1} max={80} step={1}
            onChange={(v) => set('chroma', v)} />
        </div>
      )}

      <div className="curate-grid">
        <div className="field">
          <label htmlFor="ws-ocr">OCR model</label>
          <select id="ws-ocr" value={models.ocr} onChange={(e) => setModels({ ...models, ocr: e.target.value })}>
            {ocrModels.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ws-extractor">Extractor</label>
          <select id="ws-extractor" value={models.extractor}
            onChange={(e) => setModels({ ...models, extractor: e.target.value })}>
            {extractors.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
      </div>

      {reprocess.error && <div className="error-banner" role="alert">{reprocess.error.message}</div>}
      <div className="start-bar">
        <button className="primary" disabled={running || blockedBy !== null || reprocess.isPending || !hasScans}
          onClick={() => reprocess.mutate()}>
          {reprocess.isPending ? 'Starting…' : 'Read it again'}
        </button>
        {blockedBy && <span className="ingest-note">Waiting for {blockedBy} to finish.</span>}
      </div>
      <p className="ingest-note">
        Reading again replaces this document's OCR text and extraction, then the form starts from it. A turn
        is kept (the file is turned upright, so the new boxes line up with it); the treatment only helps OCR read.
      </p>
      {job && <JobPanel job={job} />}
    </Card>
  )
}

/** Holding `key` (outside a text field) turns `on` while it is down. */
function useHeldKey(key: string, set: (down: boolean) => void, enabled: boolean) {
  const latest = useRef(set)
  useEffect(() => {
    latest.current = set
  })
  useEffect(() => {
    if (!enabled) return undefined
    const typing = (event: KeyboardEvent) =>
      event.target instanceof HTMLElement && event.target.closest('input, textarea, select, [contenteditable="true"]')
    const down = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== key || event.ctrlKey || event.metaKey || event.altKey || typing(event)) return
      latest.current(true)
    }
    const up = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === key) latest.current(false)
    }
    const release = () => latest.current(false)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', release)      // a key released in another window never sends keyup here
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', release)
      latest.current(false)
    }
  }, [key, enabled])
}

function Decision({ document, form, onChange, activeFields, onActivate, onDecided }: {
  document: ReviewDocument
  form: FormState
  onChange: (form: FormState) => void
  activeFields: readonly string[]
  onActivate: (input: string | null) => void
  onDecided: () => void
}) {
  // The same hint rules, name status and accept blocker Review shows, on the same endpoint's rules.
  const draft = {
    document_type: form.document_type, name: form.name, date: form.date, time: form.time,
    cost: parseCost(form.cost), currency: form.currency,
  }
  const hints = useQuery({
    queryKey: ['curate', 'workshop', 'hints', document.key, draft],
    queryFn: () => api.workshop.hints(document.key, draft),
    placeholderData: (previous) => previous,
  })

  const decide = useMutation({
    mutationFn: (verdict: 'accepted' | 'tossed') => api.workshop.decide({
      key: document.key,
      verdict,
      draft,
      comment: form.comment,
    }),
    onSuccess: () => onDecided(),
  })

  return (
    <Card title="Decision" className="review-col decision"
      hint={document.decision?.comment ? 'marked in review' : undefined}>
      <ReviewForm doc={document} form={form} onChange={(patch) => onChange({ ...form, ...patch })}
        hints={hints.data} activeFields={activeFields} onActivate={onActivate} />
      {decide.error && <div className="error-banner" role="alert">{decide.error.message}</div>}
      <div className="review-actions">
        <button className="primary" disabled={decide.isPending} onClick={() => decide.mutate('accepted')}>
          Accept into the archive
        </button>
        <button className="danger-outline" disabled={decide.isPending} onClick={() => decide.mutate('tossed')}>
          Toss
        </button>
      </div>
    </Card>
  )
}

/** The week around the form's date and time, and the batch the document was scanned in. */
function Context({ document, form }: { document: ReviewDocument; form: FormState }) {
  const when = useDebounced({ date: form.date, time: form.time, document_type: form.document_type }, 400)
  const context = useQuery({
    queryKey: ['curate', 'workshop', 'context', document.key, when],
    queryFn: () => api.workshop.context(document.key, when),
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[3] === document.key ? previous : undefined),
  })
  // A failure stays inside the cards: the rest of the page is still the place to decide.
  const data = context.data
  const failed = context.error && !data ? <ErrorState error={context.error} /> : null
  return (
    <>
      <Card title="Around this time" hint="receipts a week either side of the date in the form">
        {failed ?? (!data ? <Loading what="the week" />
          : data.week === null ? <Empty>{form.document_type !== 'receipt'
            ? 'Only a receipt has a week to compare with.'
            : form.date.trim()
              ? `${form.date} isn't a date the week can be found from (YYYY-MM-DD).`
              : 'No date yet. Once the form has one, the receipts around it show here; the batch below may help find it.'}</Empty>
          : data.week.length <= 1 ? <Empty>No other receipts that week.</Empty>
          : <Strip key={`${document.key}-week`} scans={data.week} />)}
      </Card>
      <Card title="Same batch" hint={data?.batch_id != null ? `batch ${data.batch_id}` : undefined}>
        {failed ?? (!data ? <Loading what="the batch" />
          : data.batch_id === null ? <Empty>This document has no batch recorded.</Empty>
          : data.batch.length === 0 ? <Empty>Batch {data.batch_id} isn't in the scan index.</Empty>
          : <Strip key={`${document.key}-batch`} scans={data.batch} />)}
      </Card>
    </>
  )
}

/**
 * One row of scans, as many as fit, starting with the current one near the middle; ◀ ▶ move it along one
 * scan at a time, as Streamlit's did.
 */
function Strip({ scans }: { scans: ContextScan[] }) {
  const gridRef = useRef<HTMLDivElement>(null)
  const columns = useGridColumnCount(gridRef, 5)
  const here = Math.max(0, scans.findIndex((scan) => scan.current))
  const [first, setFirst] = useState<number | null>(null)
  const clamp = (at: number) => Math.max(0, Math.min(at, scans.length - columns))
  const start = clamp(first ?? here - Math.floor(columns / 2))
  const shown = scans.slice(start, start + columns)

  return (
    <>
      <div className="review-nav">
        <button disabled={start === 0} onClick={() => setFirst(clamp(start - 1))}>◀ Prev</button>
        <button disabled={start + columns >= scans.length} onClick={() => setFirst(clamp(start + 1))}>Next ▶</button>
        <span>{start + 1}–{Math.min(start + columns, scans.length)} of {scans.length}</span>
      </div>
      <div className="receipt-gallery context-strip" ref={gridRef}>
        {shown.map((scan) => <ContextCard key={`${scan.filename}-${scan.verdict}`} scan={scan} />)}
      </div>
    </>
  )
}

function ContextCard({ scan }: { scan: ContextScan }) {
  const verdict = (
    <span style={{ color: VERDICT_COLORS[scan.verdict] ?? 'var(--muted)', fontWeight: scan.current ? 600 : undefined }}>
      {scan.verdict ? scan.verdict[0]?.toUpperCase() + scan.verdict.slice(1) : 'Not archived'}
    </span>
  )
  const detail = [scan.date && `${scan.date}${scan.time ? ` ${scan.time.slice(0, 5)}` : ''}`,
    scan.cost ? money(scan.cost, scan.currency) : ''].filter(Boolean).join(' · ')
  const props = {
    src: scan.image ?? undefined,
    name: <>{scan.current && '► '}{scan.name || scan.filename}</>,
    caption: <>{verdict}{detail ? ` · ${detail}` : ''}</>,
    className: scan.current ? 'context-card--current' : '',
    title: scan.filename,
  }
  return scan.receipt
    ? <DocumentCard as={Link} to={`/receipt?file=${encodeURIComponent(scan.receipt)}`} {...props} />
    : <DocumentCard {...props} />
}

function Slider({ label, value, min, max, step, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <div className="field">
      <label>{label} <strong>{value}</strong></label>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  )
}
