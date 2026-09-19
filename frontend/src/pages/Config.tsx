import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { AppConfig, ConfigOptions } from '../api/types.ts'
import { Card, ErrorState, Loading } from '../components/ui.tsx'
import './config.css'


export default function Config() {
  const config = useQuery({ queryKey: ['config'], queryFn: api.config })
  const options = useQuery({ queryKey: ['config', 'options'], queryFn: api.configOptions })
  if (config.isPending || options.isPending) return <Loading what="settings" />
  if (config.error) return <ErrorState error={config.error} />
  if (options.error) return <ErrorState error={options.error} />
  return <ConfigForm saved={config.data} options={options.data} />
}

function ConfigForm({ saved, options }: { saved: AppConfig; options: ConfigOptions }) {
  const queryClient = useQueryClient()
  // Only what was typed here. The form shows it over the saved config, so a setting another page saves
  // meanwhile (a model picked on the OCR page) shows as it is now, not as an edit made here.
  const [edits, setEdits] = useState<Partial<AppConfig>>({})
  const draft: AppConfig = { ...saved, ...edits }
  const set = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => setEdits({ ...edits, [key]: value })

  // Only the fields actually edited here are sent, so a running job's own config writes survive.
  const patch = Object.fromEntries(
    (Object.keys(edits) as (keyof AppConfig)[]).filter((k) => edits[k] !== saved[k]).map((k) => [k, edits[k]]),
  ) as Partial<AppConfig>
  const changed = Object.keys(patch)

  const save = useMutation({
    mutationFn: () => api.patchConfig(patch),
    onSuccess: (fresh) => {
      setEdits({})
      queryClient.setQueryData(['config'], fresh)
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })  // the ingest pages show these choices too
    },
  })

  return (
    <div className="config-page">
      <h1>Config</h1>
      <p className="page-sub">Stored in <code>config.json</code> next to the app. Saved changes apply immediately.</p>

      <Card title="Paths">
        <div className="config-grid">
          <PathField label="Input image path" hint="where the scanner drops new images"
            value={draft.input_image_path} onChange={(v) => set('input_image_path', v)} />
          <PathField label="Batch output path" hint="the archive: batches, mid-ingest files, YYYY/MM folders"
            value={draft.batch_output_path} onChange={(v) => set('batch_output_path', v)} />
        </div>
        <label className="config-check">
          <input type="checkbox" checked={draft.extract_structured}
            onChange={(e) => set('extract_structured', e.target.checked)} />
          Ask grounding OCR models for field boxes (a second pass per page; Review's overlay needs them)
        </label>
      </Card>

      <Card title="Models">
        <div className="config-grid">
          <Choice label="OCR model" value={draft.ocr_model} options={options.ocr_models}
            onChange={(v) => set('ocr_model', v)} hint="also set by the OCR page when you start a run" />
          <Choice label="Extractor model" value={draft.extractor_model} options={options.extractors}
            onChange={(v) => set('extractor_model', v)} hint="also set by the Parse page when you start a run" />
          <Choice label="Workshop OCR model" value={draft.workshop_ocr_model} options={options.ocr_models}
            onChange={(v) => set('workshop_ocr_model', v)} hint="Used by the Marked Workshop's Read it again." />
          <Choice label="Workshop extractor" value={draft.workshop_extractor_model} options={options.extractors}
            onChange={(v) => set('workshop_extractor_model', v)} hint="Used by the Marked Workshop's Read it again." />
        </div>
        <div className="field">
          <label htmlFor="cfg-instruction">Parse custom instructions</label>
          <textarea id="cfg-instruction" rows={4} value={draft.parse_custom_instruction}
            onChange={(e) => set('parse_custom_instruction', e.target.value)}
            placeholder="Added to every extraction prompt, e.g. Prefer the Japanese store name" />
        </div>
      </Card>

      <Card title="Indexing and dashboard">
        <div className="config-grid">
          <Choice label="Indexing scheme" value={draft.indexing_scheme} options={options.indexing_schemes}
            onChange={(v) => set('indexing_scheme', v)} hint="how scanner filenames become batches" />
          <Choice label="Dashboard rank by" value={draft.dashboard_rank_by} options={options.dashboard_rank_by}
            onChange={(v) => set('dashboard_rank_by', v)} hint="starting order of the top-merchants chart" />
        </div>
      </Card>

      <Card title="Normalization" hint="Where Normalize starts">
        <div className="config-grid">
          <Choice label="Engine" value={draft.normalize_engine} options={options.normalize_engines.map((e) => e.id)}
            labels={Object.fromEntries(options.normalize_engines.map((e) => [e.id, e.label]))}
            onChange={(v) => set('normalize_engine', v)} />
          <Slider label="Embedding distance threshold" value={draft.normalize_embedding_threshold}
            min={options.embedding_threshold_step} max={options.embedding_threshold_step * 100}
            step={options.embedding_threshold_step} format={(v) => v.toFixed(4)}
            onChange={(v) => set('normalize_embedding_threshold', v)} />
          <Slider label="String similarity" value={draft.normalize_string_similarity} min={50} max={100} step={1}
            format={(v) => `${v}%`} onChange={(v) => set('normalize_string_similarity', v)} />
        </div>
      </Card>

      <Card title="Brand prefix suggestions" hint="Where Brand registry's suggestions start">
        <label className="config-check">
          <input type="checkbox" checked={draft.prefix_suggestion_boundary_only}
            onChange={(e) => set('prefix_suggestion_boundary_only', e.target.checked)} />
          Suggest prefixes only at word boundaries
        </label>
        <div className="config-grid">
          <Count label="Prefix min length" value={draft.prefix_suggestion_min_length} min={1} max={24}
            onChange={(v) => set('prefix_suggestion_min_length', v)} />
          <Count label="Prefix max length" value={draft.prefix_suggestion_max_length} min={4} max={80}
            onChange={(v) => set('prefix_suggestion_max_length', v)} />
          <Count label="Prefix min count" value={draft.prefix_suggestion_min_count} min={1} max={50}
            onChange={(v) => set('prefix_suggestion_min_count', v)} />
        </div>
      </Card>

      <Card title="Remembered state" hint="written by the pages themselves">
        <div className="config-grid">
          <div className="field"><label>Calendar period</label><input type="text" value={draft.calendar_period} disabled /></div>
          <div className="field"><label>Calendar date</label><input type="text" value={draft.calendar_date || '(not set)'} disabled /></div>
        </div>
      </Card>

      {save.error && <div className="error-banner" role="alert">{save.error.message}</div>}
      <div className="config-save">
        <button className="primary" disabled={!changed.length || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save changes'}
        </button>
        <button disabled={!changed.length || save.isPending} onClick={() => setEdits({})}>Discard</button>
        <span className="config-hint">
          {changed.length ? `${changed.length} unsaved change${changed.length === 1 ? '' : 's'}` : 'Saved.'}
        </span>
      </div>
    </div>
  )
}

/** A folder path, checked on the server (the browser can't see the filesystem). */
function PathField({ label, hint, value, onChange }:
  { label: string; hint: string; value: string; onChange: (value: string) => void }) {
  const check = useQuery({
    queryKey: ['config', 'path-check', value],
    queryFn: () => api.pathCheck(value),
    enabled: value.trim() !== '',
    staleTime: 10_000,
  })
  const status = !value.trim() ? 'not set'
    : check.isPending ? 'checking…'
      : check.data?.is_dir ? 'folder found'
        : check.data?.exists ? 'not a folder' : 'no such folder'
  const bad = Boolean(value.trim()) && check.data !== undefined && !check.data.is_dir
  return (
    <div className="field config-path">
      <label>{label}</label>
      <input type="text" value={value} spellCheck={false} onChange={(e) => onChange(e.target.value)} />
      <span className={`config-hint${bad ? ' neg' : ''}`}>{status} · {hint}</span>
    </div>
  )
}

function Choice({ label, value, options, labels, hint, onChange }: {
  label: string
  value: string
  options: string[]
  labels?: Record<string, string>
  hint?: string
  onChange: (value: string) => void
}) {
  // A model saved in config.json can be missing now (no CUDA, Ollama down): keep showing it as the choice.
  const all = value && !options.includes(value) ? [value, ...options] : options
  return (
    <div className="field">
      <label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {!value && <option value="">(none)</option>}
        {all.map((name) => <option key={name} value={name}>{labels?.[name] ?? name}</option>)}
      </select>
      {hint && <span className="config-hint">{hint}</span>}
    </div>
  )
}

function Slider({ label, value, min, max, step, format, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (value: number) => string
  onChange: (value: number) => void
}) {
  return (
    <div className="field">
      <label>{label} <strong>{format(value)}</strong></label>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(globalThis.Number(e.target.value))} />
    </div>
  )
}

function Count({ label, value, min, max, onChange }:
  { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  const clamp = (raw: string) => Math.min(max, Math.max(min, Math.floor(globalThis.Number(raw) || min)))
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" className="limit-input" min={min} max={max} step={1} value={value}
        onChange={(e) => onChange(clamp(e.target.value))} />
    </div>
  )
}
