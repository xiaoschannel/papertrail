import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, experimentScanUrl, experimentSeenUrl } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
import type { ExperimentOptions, ExperimentRun, ExperimentTreatment, FieldBox } from '../api/types.ts'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Markdown } from '../components/Markdown.tsx'
import { ScanOverlay, fieldColor, useBoxesHidden } from '../components/review/ScanOverlay.tsx'
import { CompareKeys, DEFAULT_ENHANCEMENT, Slider, TreatmentControls, isTreated, useHoldOriginal } from '../components/ScanTreatment.tsx'
import { useDebounced } from '../components/useDebounced.ts'
import { money, num, spend, spendExactly } from '../format.ts'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './curate.css'
import './ingest.css'
import './dev.css'

const DEFAULT_TREATMENT: ExperimentTreatment = {
  ...DEFAULT_ENHANCEMENT, denoise_before: false, denoise_after: false, denoise_strength: 6,
}

const treated = (t: ExperimentTreatment) => isTreated(t) || t.denoise_before || t.denoise_after

/** What the image column shows: a preview of what OCR will read, or the last reading with its boxes. */
type View = 'preview' | 'boxes' | 'fields'

/**
 * The bench: one image through OCR and Parse, kept out of the archive. For trying a model on a scan
 * before it goes anywhere near the pipeline, seeing why a scan misreads, or tuning the prompt. Three
 * columns in the order things happen — the image, OCR and what it read, Parse and what it extracted —
 * and Parse gets exactly the input and prompt the pipeline's Parse would.
 */
export default function Experiment() {
  const [params, setParams] = useSearchParams()
  const options = useQuery({ queryKey: ['dev', 'experiment'], queryFn: api.dev.experiment })
  const runId = params.get('run') ?? options.data?.latest ?? null
  const run = useQuery({
    queryKey: ['dev', 'experiment', runId],
    queryFn: () => api.dev.run(runId ?? ''),
    enabled: runId !== null,
    retry: false,
  })
  const open = (id: string) => setParams({ run: id })

  if (options.isPending) return <Loading what="the models" />
  if (options.error) return <ErrorState error={options.error} />

  return (
    <div className="experiment-page">
      <h1>Experiment</h1>
      <p className="page-sub">
        Run one image through OCR and Parse without it going anywhere near the archive: to try a model, see why a
        scan misreads, or tune the prompt. Parse is given exactly what the pipeline's Parse would give it.
      </p>

      <ImageBar run={run.data ?? null} onOpened={open} />

      {runId === null ? <Empty>Open or drop an image (PNG, JPEG or WebP) to start. The last 20 are kept.</Empty>
        : run.error ? <ErrorState error={run.error} />
        : !run.data ? <Loading what="the image" />
        : <Bench key={run.data.id} run={run.data} options={options.data} />}
    </div>
  )
}

/** Open an image: the button, or a drop anywhere on the bar. */
function ImageBar({ run, onOpened }: { run: ExperimentRun | null; onOpened: (id: string) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const upload = useMutation({ mutationFn: api.dev.upload, onSuccess: (opened) => onOpened(opened.id) })
  const take = (file: File | undefined) => {
    if (file) upload.mutate(file)
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    take(event.dataTransfer.files[0])
  }

  return (
    <div className={`experiment-drop${dragging ? ' dragging' : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)} onDrop={drop}>
      <input ref={input} type="file" accept=".png,.jpg,.jpeg,.webp" hidden
        onChange={(event) => {
          take(event.target.files?.[0])
          event.target.value = ''     // the same file again is a new run
        }} />
      <button className={run ? '' : 'primary'} disabled={upload.isPending} onClick={() => input.current?.click()}>
        {upload.isPending ? 'Opening…' : run ? 'Open another image' : 'Open an image'}
      </button>
      <span className="config-hint">or drop one here</span>
      {run && <span className="experiment-file"><strong>{run.filename}</strong> · {run.width} × {run.height}</span>}
      {upload.error && <span className="neg">{upload.error.message}</span>}
    </div>
  )
}

function Bench({ run, options }: { run: ExperimentRun; options: ExperimentOptions }) {
  const queryClient = useQueryClient()
  const track = useTrackJob()
  // The controls start from what OCR last read, so "read it again" means the same thing twice.
  const [treatment, setTreatment] = useState<ExperimentTreatment>(run.ocr?.treatment ?? DEFAULT_TREATMENT)
  const [view, setView] = useState<View>(run.parse ? 'fields' : run.ocr?.with_boxes ? 'boxes' : 'preview')
  const comparing = useHoldOriginal(view === 'preview' && treated(treatment))
  const [activeFields, setActiveFields] = useState<readonly string[]>([])
  const [ocrModel, setOcrModel] = useState(options.ocr_model)
  const [withBoxes, setWithBoxes] = useState(options.with_boxes)
  const [extractor, setExtractor] = useState(options.extractor)
  // Parse's own instructions, as saved now (Review and the Parse page edit them too).
  const savedInstruction = useConfig().data?.parse_custom_instruction ?? options.custom_instruction
  const [typed, setInstruction] = useState<string | null>(null)
  const instruction = typed ?? savedInstruction
  const saveConfig = useSaveConfig()

  const grounding = options.grounding_models.includes(ocrModel)
  const ocrGate = useJobGate('experiment-ocr', { gpu: true })
  const parseGate = useJobGate('experiment-parse', { gpu: options.local_extractors.includes(extractor) })
  const ocrRunning = ocrGate.job?.status === 'running'
  const parseRunning = parseGate.job?.status === 'running'

  // A finished run shows what it produced: the boxes OCR found, then the fields Parse cited.
  const ocrDone = ocrGate.job?.status === 'succeeded' ? ocrGate.job.id : null
  const parseDone = parseGate.job?.status === 'succeeded' ? parseGate.job.id : null
  const shownOcr = useRef(ocrDone)       // runs that finished before the page opened change nothing
  const shownParse = useRef(parseDone)
  const askedForBoxes = useRef(false)   // the reading itself arrives a moment after its job ends
  useEffect(() => {
    if (ocrDone === shownOcr.current) return
    shownOcr.current = ocrDone
    setView(askedForBoxes.current ? 'boxes' : 'preview')
  }, [ocrDone])
  useEffect(() => {
    if (parseDone === shownParse.current) return
    shownParse.current = parseDone
    setView('fields')
  }, [parseDone])

  const changeTreatment = (patch: Partial<ExperimentTreatment>) => {
    setTreatment((current) => ({ ...current, ...patch }))
    setView('preview')               // a change only shows on the preview
  }

  const started = (job: Parameters<typeof track>[0]) => {
    track(job)
    void queryClient.invalidateQueries({ queryKey: ['config'] })
  }
  const readIt = useMutation({
    mutationFn: () => {
      askedForBoxes.current = withBoxes && grounding
      return api.dev.ocr(run.id, { ...treatment, model: ocrModel, with_boxes: askedForBoxes.current })
    },
    onSuccess: started,
  })
  const parseIt = useMutation({
    mutationFn: () => api.dev.parse(run.id, { extractor, custom_instruction: instruction }),
    onSuccess: started,
  })
  const saveOnBlur = () => {
    if (typed !== null && typed !== savedInstruction) {
      saveConfig.mutate({ parse_custom_instruction: typed }, { onSuccess: () => setInstruction(null) })
    }
  }

  return (
    <div className="experiment-layout">
      <ImageColumn run={run} view={view} onView={setView} treatment={treatment} comparing={comparing}
        activeFields={activeFields} onHoverField={(field) => setActiveFields(field ? [field] : [])}>
        {view === 'preview' && <CompareKeys treated={treated(treatment)} />}
        <TreatmentControls value={treatment} onChange={changeTreatment} />
        <div className="field">
          <label>Denoise</label>
          <div className="experiment-checks">
            <label><input type="checkbox" checked={treatment.denoise_before}
              onChange={(e) => changeTreatment({ denoise_before: e.target.checked })} /> before the treatment</label>
            <label><input type="checkbox" checked={treatment.denoise_after}
              onChange={(e) => changeTreatment({ denoise_after: e.target.checked })} /> after it</label>
          </div>
        </div>
        {(treatment.denoise_before || treatment.denoise_after) && (
          <div className="curate-grid">
            <Slider label="Denoise strength" value={treatment.denoise_strength} min={3} max={15} step={1}
              onChange={(denoise_strength) => changeTreatment({ denoise_strength })} />
          </div>
        )}
      </ImageColumn>

      <Card title="OCR" hint={run.ocr ? `${run.ocr.model} · ${seconds(run.ocr.seconds)}${
        run.ocr.structured_seconds != null ? ` + boxes ${seconds(run.ocr.structured_seconds)}` : ''}` : undefined}>
        <div className="field">
          <label htmlFor="exp-ocr">Model</label>
          <select id="exp-ocr" value={ocrModel} onChange={(e) => {
            setOcrModel(e.target.value)
            saveConfig.mutate({ ocr_model: e.target.value })
          }}>
            {options.ocr_models.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
        <label className="experiment-check">
          <input type="checkbox" checked={withBoxes && grounding} disabled={!grounding}
            onChange={(e) => setWithBoxes(e.target.checked)} />
          {' '}Also read the boxes{grounding ? ' (a second pass)' : ' — this model has no boxes'}
        </label>
        {readIt.error && <div className="error-banner" role="alert">{readIt.error.message}</div>}
        <div className="start-bar">
          <button className="primary" disabled={ocrRunning || ocrGate.blockedBy !== null || readIt.isPending}
            onClick={() => readIt.mutate()}>
            {readIt.isPending ? 'Starting…' : run.ocr ? 'Read it again' : 'Run OCR'}
          </button>
          {ocrGate.blockedBy && <span className="ingest-note">Waiting for {ocrGate.blockedBy} to finish.</span>}
        </div>
        {ocrGate.job && <JobPanel job={ocrGate.job} />}
        {run.ocr && <OcrText ocr={run.ocr} />}
      </Card>

      <Card title="Parse" hint={run.parse ? `${run.parse.extractor} · ${seconds(run.parse.seconds)}` : undefined}>
        <div className="field">
          <label htmlFor="exp-extractor">Extractor</label>
          <select id="exp-extractor" value={extractor} onChange={(e) => {
            setExtractor(e.target.value)
            saveConfig.mutate({ extractor_model: e.target.value })
          }}>
            {options.extractors.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
        <div className="field parse-instructions">
          <label htmlFor="exp-instruction">
            Custom instructions (Parse's own, added to the prompt){saveConfig.isPending ? ' — saving…' : ''}
          </label>
          <textarea id="exp-instruction" rows={3} value={instruction}
            placeholder="e.g. Prefer the Japanese store name over the romanized one"
            onChange={(e) => setInstruction(e.target.value)} onBlur={saveOnBlur} />
        </div>
        {run.ocr && <PromptPreview runId={run.id} instruction={instruction} />}
        {parseIt.error && <div className="error-banner" role="alert">{parseIt.error.message}</div>}
        <div className="start-bar">
          <button className="primary"
            disabled={!run.ocr || ocrRunning || parseRunning || parseGate.blockedBy !== null || parseIt.isPending}
            onClick={() => parseIt.mutate()}>
            {parseIt.isPending ? 'Starting…' : 'Run Parse'}
          </button>
          {!run.ocr ? <span className="ingest-note">Parse reads OCR's text: run OCR first.</span>
            : parseGate.blockedBy && <span className="ingest-note">Waiting for {parseGate.blockedBy} to finish.</span>}
        </div>
        {parseGate.job && <JobPanel job={parseGate.job} />}
        {run.parse && <Extraction parse={run.parse} activeFields={activeFields}
          onHoverField={(field) => setActiveFields(field ? [field] : [])} />}
        {run.parse && <Cost parse={run.parse} prices={options.prices} />}
      </Card>
    </div>
  )
}

function ImageColumn({ run, view, onView, treatment, comparing, activeFields, onHoverField, children }: {
  run: ExperimentRun
  view: View
  onView: (view: View) => void
  treatment: ExperimentTreatment
  comparing: boolean
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
  children: ReactNode
}) {
  // Dragging a slider shouldn't ask the server for a full-size render per step.
  const settled = useDebounced(treatment, 250)
  const boxesHidden = useBoxesHidden()
  const { ocr, parse } = run
  // Until the first reading arrives (a moment after its job ends) there is only the preview to show.
  const seen = ocr ? experimentSeenUrl(run.id, String(ocr.read_at)) : experimentScanUrl(run.id, settled)
  const boxes: FieldBox[] = view === 'boxes' ? ocr?.boxes ?? [] : view === 'fields' ? parse?.field_boxes ?? [] : []
  const page = { file_key: run.id, filename: run.filename, image_available: true, boxes }
  const hint = view === 'preview' ? (comparing ? 'as uploaded' : 'what OCR will read with the settings below')
    : view === 'boxes' ? `what ${ocr?.model} read, and the ${ocr?.boxes.length ?? 0} boxes it found`
    : `the boxes ${parse?.extractor} took each field from`

  return (
    <Card title="Image" hint={hint}>
      <div className="segmented experiment-views">
        <button className={view === 'preview' ? 'on' : ''} onClick={() => onView('preview')}>Preview</button>
        <button className={view === 'boxes' ? 'on' : ''} disabled={!ocr?.with_boxes} onClick={() => onView('boxes')}>
          OCR boxes{ocr?.with_boxes ? ` (${ocr.boxes.length})` : ''}
        </button>
        <button className={view === 'fields' ? 'on' : ''} disabled={!parse || !ocr?.with_boxes}
          onClick={() => onView('fields')}>Fields</button>
      </div>
      <ScanOverlay pages={[page]} activeFields={activeFields} onHoverField={onHoverField}
        imageUrl={() => (view === 'preview' ? experimentScanUrl(run.id, settled) : seen)}
        originalUrl={view === 'preview' ? () => experimentScanUrl(run.id, DEFAULT_TREATMENT) : undefined}
        showOriginal={view === 'preview' && comparing && treated(treatment)} hideBoxes={boxesHidden} />
      {children}
    </Card>
  )
}

type Display = 'text' | 'raw' | 'boxes'

/** What OCR read: the text as markdown or raw, and the boxes pass verbatim. */
function OcrText({ ocr }: { ocr: NonNullable<ExperimentRun['ocr']> }) {
  const [display, setDisplay] = useState<Display>('text')
  const shown: Display = display === 'boxes' && ocr.structured_raw === null ? 'text' : display
  return (
    <>
      <h3 className="experiment-step">What it read</h3>
      <div className="segmented experiment-views">
        <button className={shown === 'text' ? 'on' : ''} onClick={() => setDisplay('text')}>Text</button>
        <button className={shown === 'raw' ? 'on' : ''} onClick={() => setDisplay('raw')}>Raw</button>
        <button className={shown === 'boxes' ? 'on' : ''} disabled={ocr.structured_raw === null}
          onClick={() => setDisplay('boxes')}>Boxes pass (raw)</button>
      </div>
      {shown === 'text' ? <Markdown className="textdump experiment-text" source={ocr.markdown} />
        : <pre className="textdump experiment-text">{shown === 'raw' ? ocr.markdown : ocr.structured_raw}</pre>}
    </>
  )
}

/** What Parse extracted, readably: the fields (hover one to see its boxes on the image), then the items. */
function Extraction({ parse, activeFields, onHoverField }: {
  parse: NonNullable<ExperimentRun['parse']>
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
}) {
  const e = parse.extraction
  const cited = new Set(Object.keys('field_sources' in e ? e.field_sources : {}))
  const rows: [field: string, label: string, value: string][] = e.document_type === 'receipt' ? [
    ['name', 'Name', e.name], ['date', 'Date', e.date], ['time', 'Time', e.time],
    ['cost', 'Amount', money(e.cost, e.currency)], ['phone', 'Phone', e.phone], ['address', 'Address', e.address],
    ['language', 'Language', e.language],
  ] : e.document_type === 'other' ? [
    ['title', 'Title', e.title], ['date', 'Date', e.date], ['time', 'Time', e.time], ['language', 'Language', e.language],
  ] : []
  const items = e.document_type === 'receipt' ? e.items : []
  const itemsTotal = items.reduce((sum, item) => sum + (item.total_price ?? 0), 0)
  const cost = e.document_type === 'receipt' ? e.cost : 0
  const currency = e.document_type === 'receipt' ? e.currency : ''
  const addsUp = Math.abs(itemsTotal - cost) < 0.005

  return (
    <>
      <h3 className="experiment-step">What it extracted · {TYPE_LABEL[e.document_type]}</h3>
      {e.document_type === 'corrupted'
        ? <p className="ingest-note">Parse called it corrupted: nothing it could read as a document.</p>
        : (
          <table className="experiment-fields">
            <tbody>
              {rows.map(([field, label, value]) => (
                <tr key={field} className={activeFields.includes(field) ? 'active' : ''}
                  style={cited.has(field) ? { ['--box-color' as string]: fieldColor(field) } : undefined}
                  onMouseEnter={() => onHoverField(cited.has(field) ? field : null)}
                  onMouseLeave={() => onHoverField(null)}>
                  <th className={cited.has(field) ? 'cited' : ''}>{label}</th>
                  <td>{value || <span className="config-hint">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      {items.length > 0 && (
        <>
          <table className="experiment-items">
            <thead>
              <tr><th>Item</th><th className="num">Qty</th><th className="num">Each</th><th className="num">Total</th></tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr key={`${item.name}-${i}`}>
                  <td>{item.name}</td>
                  <td className="num">{item.quantity ?? ''}</td>
                  <td className="num">{item.unit_price != null ? money(item.unit_price, currency) : ''}</td>
                  <td className="num">{item.total_price != null ? money(item.total_price, currency) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className={`config-hint${addsUp ? '' : ' neg'}`}>
            The items add up to {money(itemsTotal, currency)}
            {addsUp ? ', the amount.' : `, not the amount (${money(cost, currency)}).`}
          </p>
        </>
      )}
      <details className="ingest-details">
        <summary>As JSON</summary>
        <pre className="textdump">{JSON.stringify(e, null, 2)}</pre>
      </details>
    </>
  )
}

const TYPE_LABEL = { receipt: 'Receipt', other: 'Other document', corrupted: 'Corrupted' } as const

/**
 * What the run cost: the tokens it actually used, priced for the model that ran, then scaled to a batch
 * and across the other billed models. The other models are an estimate — the same text, but each one
 * thinks a different amount — so they answer "which model for this run", not "what will the bill be".
 */
function Cost({ parse, prices }: { parse: NonNullable<ExperimentRun['parse']>; prices: ExperimentOptions['prices'] }) {
  const tokens = parse.tokens
  if (!tokens) return null
  const billed = Object.entries(prices)
  return (
    <>
      <h3 className="experiment-step">What it cost</h3>
      <table className="experiment-fields">
        <tbody>
          <tr><th>prompt_tokens</th><td>{num(tokens.prompt)}</td></tr>
          <tr><th>cached_tokens</th><td>{num(tokens.cached)}</td></tr>
          <tr><th>completion_tokens</th><td>{num(tokens.completion)}</td></tr>
          <tr><th>reasoning_tokens</th><td>{num(tokens.thinking)}</td></tr>
        </tbody>
      </table>
      {tokens.raw && (
        <details className="ingest-details">
          <summary>Usage, as it came back</summary>
          <pre className="textdump">{JSON.stringify(tokens.raw, null, 2)}</pre>
        </details>
      )}
      {parse.cost == null
        ? <p className="ingest-note">{parse.extractor} runs on this machine: it costs GPU time, not money.</p>
        : (
          <p className="experiment-cost">
            <strong>{spendExactly(parse.cost)}</strong> for this document — {spend(parse.cost * 100)} per 100,{' '}
            {spend(parse.cost * 1000)} per 1,000.
          </p>
        )}
      {billed.length > 1 && (
        <table className="experiment-items">
          <thead>
            <tr><th>Same tokens on</th><th className="num">Per document</th><th className="num">Per 1,000</th></tr>
          </thead>
          <tbody>
            {billed.map(([name, price]) => {
              const each = (tokens.prompt - tokens.cached) * price.input / 1e6
                + tokens.cached * price.cached_input / 1e6 + tokens.completion * price.output / 1e6
              return (
                <tr key={name} className={name === parse.extractor ? 'active' : ''}>
                  <td>{name.replace(/^OpenAI - /, '')}{name === parse.extractor ? ' (this run)' : ''}</td>
                  <td className="num">{spendExactly(each)}</td>
                  <td className="num">{spend(each * 1000)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {billed.length > 1 && (
        <p className="config-hint">
          The other models are priced on this run's tokens; each one thinks a different amount, so run it to be sure.
        </p>
      )}
    </>
  )
}



/** The prompt Parse would send now, as the instructions are typed. */
function PromptPreview({ runId, instruction }: { runId: string; instruction: string }) {
  const settled = useDebounced(instruction, 400)
  const prompt = useQuery({
    queryKey: ['dev', 'experiment', runId, 'prompt', settled],
    queryFn: () => api.dev.prompt(runId, settled),
    placeholderData: (previous) => previous,
  })
  return (
    <details className="ingest-details">
      <summary>Extraction prompt{prompt.data && !prompt.data.has_boxes ? ' (no boxes to cite)' : ''}</summary>
      {prompt.error ? <ErrorState error={prompt.error} />
        : <pre className="textdump">{prompt.data?.prompt ?? 'Building…'}</pre>}
    </details>
  )
}

const seconds = (value: number) => `${value.toFixed(1)} s`
