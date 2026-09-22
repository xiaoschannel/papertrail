import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useDebounced } from '../components/useDebounced.ts'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl, workshopScanUrl } from '../api/client.ts'
import type {
  ContextScan, Enhancement, Job, MarkedDocument, ReviewDocument, ReviewPage, TopPoints, Trim,
} from '../api/types.ts'
import { DocumentCard } from '../components/DocumentCard.tsx'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Markdown } from '../components/Markdown.tsx'
import {
  INPUT_SOURCES, ReviewForm, initialForm, parseCost, type FormState,
} from '../components/review/ReviewForm.tsx'
import { ScanOverlay, useBoxesHidden, type Turn } from '../components/review/ScanOverlay.tsx'
import { CompareKeys, DEFAULT_ENHANCEMENT, TreatmentControls, isTreated, useHoldOriginal } from '../components/ScanTreatment.tsx'
import { ScanViewer } from '../components/scans.tsx'
import { TrimEditor } from '../components/TrimEditor.tsx'
import { turnedTrim } from '../components/TrimmedImage.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import { keyLabel, useShortcuts } from '../components/useShortcuts.ts'
import { afterArchiveEdit } from '../api/invalidate.ts'
import { useSaveConfig, useShortcutKeys } from '../api/config.ts'
import { money } from '../format.ts'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import '../components/review/review.css'
import './curate.css'

/** Colors per verdict, as review_logic's VERDICT_COLORS. */
const VERDICT_COLORS: Record<string, string> = { accepted: '#28a745', marked: '#ffc107', tossed: '#6c757d' }

/** The part of a page a reread reads when it is turned from `top` (mirror of workshop.reading_trim): its
 *  trim, or all of it under a quarter turn, where the trim would lie across the page. */
const readingTrim = (trim: Trim | null, top: TopPoints | '') => (top === 'left' || top === 'right' ? null : trim)

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
  const reread = workshop.data?.reread ?? null

  // Each document starts from its own stored values and an untreated scan, never the last one's edits;
  // one with a reread waiting shows its pages turned the way that reread read them.
  const documentKey = document?.key
  const rereadTurn = reread?.top_points
  useEffect(() => setEnhancement({ ...DEFAULT_ENHANCEMENT, top_points: rereadTurn ?? '' }), [documentKey, rereadTurn])
  useEffect(() => setForm(document ? startForm(document) : null), [document])
  const discard = useMutation({
    mutationFn: (documentKey: string) => api.workshop.discardReread(documentKey),
    onSuccess: (body) => queryClient.setQueryData(['curate', 'workshop', key], body),
  })
  // Review's keys for moving (Config's Shortcuts); Accept and Toss have theirs in the Decision card.
  const keys = useShortcutKeys()
  // While the next document loads there is no answer, but moving must go on (a held or quick second
  // press): from the last list, counting from where we are headed rather than what is on screen. A key
  // that has left the list (decided elsewhere) is not where we are: the server showed another instead.
  const lastDocuments = useRef<MarkedDocument[]>([])
  if (workshop.data) lastDocuments.current = workshop.data.documents
  const documents = workshop.data?.documents ?? lastDocuments.current
  const here = key !== null && documents.some((d) => d.key === key) ? key : document?.key
  const position = documents.findIndex((d) => d.key === here)
  const move = (step: number) => {
    const next = documents[position + step]
    if (next) setKey(next.key)
  }
  useShortcuts(keys ? { [keys.prev]: () => move(-1), [keys.next]: () => move(1) } : {},
    Boolean(keys && documents.length), keys ? [keys.prev, keys.next] : [])

  if (workshop.isPending) return <Loading what="marked documents" />
  if (workshop.error) return <ErrorState error={workshop.error} />
  const { ocr_models: ocrModels, extractors } = workshop.data

  return (
    <div className="curate-page workshop-page review-page">
      <h1>Marked Workshop</h1>
      <p className="page-sub">Documents you marked during review: make the scan readable, then decide.</p>

      {documents.length === 0 || !document || !form ? <Empty>Nothing is marked. Review is where documents get marked.</Empty> : (
        <>
          <div className="review-nav">
            <button disabled={position <= 0} onClick={() => move(-1)}>
              ← Prev{keys && <> <kbd>{keyLabel(keys.prev)}</kbd></>}
            </button>
            <button disabled={position < 0 || position >= documents.length - 1} onClick={() => move(1)}>
              Next →{keys && <> <kbd>{keyLabel(keys.next)}</kbd></>}
            </button>
            <span><strong>{position + 1} / {documents.length}</strong> — {document.key}</span>
            {documents[position]?.comment && <span className="config-hint">“{documents[position].comment}”</span>}
          </div>

          {reread && (
            <div className="reread-note" role="status">
              <span>
                Showing a reread with <strong>{reread.ocr_model}</strong> and <strong>{reread.extractor}</strong>.
                Nothing is saved yet: Accept keeps it{reread.top_points ? ' and turns the pages as it read them' : ''};
                Toss or Discard drops it.
              </span>
              <button disabled={discard.isPending} onClick={() => discard.mutate(document.key)}>Discard the reread</button>
              {discard.error && <span className="neg">{discard.error.message}</span>}
            </div>
          )}

          <div className="review-layout">
            <Card title="OCR text" className="review-col">
              {document.ocr_text
                ? <Markdown className="textdump review-ocr" source={document.ocr_text} />
                : <p className="ingest-note">No OCR text yet. Read it again to get some.</p>}
            </Card>

            <Scan document={document} enhancement={enhancement} onChange={setEnhancement}
              ocrModels={ocrModels} extractors={extractors}
              ocrModel={workshop.data.ocr_model} extractor={workshop.data.extractor}
              blockedBy={gate.blockedBy} job={gate.job} rereadTurn={rereadTurn ?? null}
              activeFields={activeFields} onHoverField={(field) => setActiveFields(field ? [field] : [])}
              onTrimmed={(body) => {
                queryClient.setQueryData(['curate', 'workshop', key], body)
                void queryClient.invalidateQueries({ queryKey: ['curate', 'workshop', 'context'] })
              }}
              onStarted={(job) => {
                track(job)
                void queryClient.invalidateQueries({ queryKey: ['curate', 'workshop'] })
              }} />

            <Decision key={document.key} document={document} form={form} onChange={setForm}
              activeFields={activeFields}
              onActivate={(input) => setActiveFields(input ? INPUT_SOURCES[input] ?? [] : [])}
              onDecided={() => {
                // Keep your place: the next document moves into this slot (the previous
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
  document, enhancement, onChange, ocrModels, extractors, ocrModel, extractor, blockedBy, job, rereadTurn,
  activeFields, onHoverField, onStarted, onTrimmed,
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
  /** How a pending reread turned the pages (its boxes are measured on them turned that way), or null. */
  rereadTurn: Turn | null
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
  onStarted: (job: Parameters<ReturnType<typeof useTrackJob>>[0]) => void
  /** A page was trimmed: the queue as the server now has it. */
  onTrimmed: (body: Awaited<ReturnType<typeof api.workshop.trim>>) => void
}) {
  const [models, setModels] = useState({ ocr: ocrModel, extractor })
  const [trimming, setTrimming] = useState<string | null>(null)       // the page whose rulers are open
  const trimmingPage = document.pages.find((page) => page.filename === trimming) ?? null
  const saveConfig = useSaveConfig()     // the Workshop's own choices, remembered as soon as they're picked
  const boxesHidden = useBoxesHidden()
  const running = job?.status === 'running'
  // Dragging a slider shouldn't ask the server for a full-size render per step.
  const settled = useDebounced(enhancement, 250)
  const hasScans = document.pages.some((page) => page.image_available)
  const treated = isTreated(enhancement)
  const comparing = useHoldOriginal(treated)
  // Stored boxes were measured on the file, so they turn with the preview; but they sit on the page's band,
  // which a quarter-turned preview doesn't cut (it reads the whole page), so on a trimmed page they don't
  // fit that one. A reread's were measured on the pages turned as it read them: they line up only while
  // the preview is turned the same way.
  const quarterTurn = settled.top_points === 'left' || settled.top_points === 'right'
  const boxesFit = rereadTurn === null || (settled.top_points === rereadTurn && !(comparing && treated))
  const pages = document.pages.map((page) =>
    boxesFit && !(rereadTurn === null && quarterTurn && !(comparing && treated) && page.trim)
      ? page : { ...page, boxes: [] })

  const trim = useMutation({
    mutationFn: ({ filename, band }: { filename: string; band: Trim | null }) =>
      api.workshop.trim(document.key, filename, band),
    onSuccess: onTrimmed,
  })
  // The previews come from the server already cut to each page's band, turned the way the preview is;
  // a quarter turn reads (and so shows) the whole page.
  const bandOf = (page: ReviewPage, untreated: boolean) => {
    const top = untreated ? '' : settled.top_points
    return turnedTrim(readingTrim(page.trim, top), top)
  }
  const trimOf = (filename: string) => document.pages.find((page) => page.filename === filename)?.trim ?? null

  const reprocess = useMutation({
    mutationFn: () => api.workshop.reprocess({
      ...enhancement, key: document.key, ocr_model: models.ocr, extractor: models.extractor,
    }),
    onSuccess: onStarted,
  })

  return (
    <Card title="Scan" className="review-col"
      hint={comparing ? 'as scanned' : document.pages.length > 1 ? `${document.pages.length} pages` : 'as OCR will see it'}>
      {/* Every page, treated the same way — a reprocess re-reads all of them, so you should be able to
          see all of them before deciding. */}
      {document.pages.length === 0 ? <Empty>No scan on disk.</Empty> : (
        <ScanOverlay pages={pages} activeFields={activeFields} onHoverField={onHoverField}
          imageUrl={(filename) => workshopScanUrl(filename, settled, trimOf(filename))}
          originalUrl={(filename) => workshopScanUrl(filename, DEFAULT_ENHANCEMENT, trimOf(filename))}
          showOriginal={comparing && treated} turn={rereadTurn === null ? settled.top_points : ''}
          bandOf={bandOf} hideBoxes={boxesHidden} missing="is not in marked/" />
      )}
      {trimmingPage?.filename && (
        <ScanViewer label={document.key} filename={trimmingPage.filename} version={0}
          onClose={() => setTrimming(null)} scan={
          <TrimEditor src={mediaUrl(`marked/${trimmingPage.filename}`)!} alt={trimmingPage.filename}
            value={trimmingPage.trim} saving={trim.isPending} error={trim.error?.message ?? null}
            onSave={(band) => trim.mutate({ filename: trimmingPage.filename!, band })}
            note={quarterTurn
              ? <>The page is shown as it is stored. While the preview turns it a quarter, a reread reads all of
                it: a trim runs along the page as stored, and would lie across it turned. Accepting it turned a
                quarter files it whole.</>
              : <>The page is shown as it is stored. The trim is kept as soon as it is saved: the preview and the
                next reread read only the band between the rulers, and every view shows only that part. The
                scan itself is never changed.</>} />
        } />
      )}

      <CompareKeys treated={treated} />
      {/* In the order a reread applies them: the trim cuts the page as stored, then it is turned and treated. */}
      <div className="field">
        <label>Trim</label>
        <div className="workshop-trims">
          {/* numbered as the document's pages are, even when one has no scan to trim */}
          {document.pages.map((page, i) => page.image_available && page.filename && (
            <span key={page.file_key} className="workshop-trim">
              <button onClick={() => {
                trim.reset()
                setTrimming(page.filename)
              }} title="Cut off what OCR shouldn't read: a coupon, a survey, a header">
                ✂ Trim{document.pages.length > 1 ? ` page ${i + 1}` : ''}
              </button>
              <span className="config-hint">
                {page.trim ? `${Math.round((1 - (page.trim.bottom - page.trim.top)) * 100)}% removed` : 'nothing removed'}
              </span>
            </span>
          ))}
        </div>
      </div>
      <TreatmentControls value={enhancement} onChange={(patch) => onChange({ ...enhancement, ...patch })} />

      <div className="curate-grid">
        <div className="field">
          <label htmlFor="ws-ocr">OCR model</label>
          <select id="ws-ocr" value={models.ocr} onChange={(e) => {
            setModels({ ...models, ocr: e.target.value })
            saveConfig.mutate({ workshop_ocr_model: e.target.value })
          }}>
            {ocrModels.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ws-extractor">Extractor</label>
          <select id="ws-extractor" value={models.extractor}
            onChange={(e) => {
              setModels({ ...models, extractor: e.target.value })
              saveConfig.mutate({ workshop_extractor_model: e.target.value })
            }}>
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
        Reading again shows what the models make of the treated pages, and the form starts from it. Nothing is
        saved until you accept: then its text and extraction are kept and the pages are turned as it read them,
        so its boxes line up. The treatment only helps OCR read and isn't kept. A trim is kept as soon as you
        save it, and OCR reads only the band.
      </p>
      {job && <JobPanel job={job} />}
    </Card>
  )
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
  })
  const accept = () => {
    if (!decide.isPending) decide.mutate('accepted', { onSuccess: onDecided })
  }
  const toss = () => {
    if (!decide.isPending) decide.mutate('tossed', { onSuccess: onDecided })
  }
  const keys = useShortcutKeys()
  useShortcuts(keys ? { [keys.accept]: accept, [keys.toss]: toss } : {}, Boolean(keys))

  return (
    <Card title="Decision" className="review-col decision"
      hint={document.decision?.comment ? 'marked in review' : undefined}>
      <ReviewForm doc={document} form={form} onChange={(patch) => onChange({ ...form, ...patch })}
        hints={hints.data} activeFields={activeFields} onActivate={onActivate} />
      {decide.error && <div className="error-banner" role="alert">{decide.error.message}</div>}
      <div className="review-actions">
        <button className="primary" disabled={decide.isPending} onClick={accept}>
          Accept into the archive{keys && <> <kbd>{keyLabel(keys.accept)}</kbd></>}
        </button>
        <button className="danger-outline" disabled={decide.isPending} onClick={toss}>
          Toss{keys && <> <kbd>{keyLabel(keys.toss)}</kbd></>}
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
 * scan at a time.
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
    trim: scan.trim,
    name: <>{scan.current && '► '}{scan.name || scan.filename}</>,
    caption: <>{verdict}{detail ? ` · ${detail}` : ''}</>,
    className: scan.current ? 'context-card--current' : '',
    title: scan.filename,
  }
  return scan.receipt
    ? <DocumentCard as={Link} to={`/receipt?file=${encodeURIComponent(scan.receipt)}`} {...props} />
    : <DocumentCard {...props} />
}
