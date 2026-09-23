import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { AppConfig, ConfigOptions, Shortcuts } from '../api/types.ts'
import { TiltDemo } from '../components/TiltDemo.tsx'
import { Card, ErrorState, Loading } from '../components/ui.tsx'
import { keyLabel, keyOf } from '../components/useShortcuts.ts'
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

  // Only the fields actually edited here are sent, so a running job's own config writes survive. A section
  // (the shortcuts) is a fresh object on every edit, so it is compared by what it holds.
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
  const patch = Object.fromEntries(
    (Object.keys(edits) as (keyof AppConfig)[]).filter((k) => !same(edits[k], saved[k])).map((k) => [k, edits[k]]),
  ) as Partial<AppConfig>
  const changed = Object.keys(patch)
  const clashes = shortcutClashes(draft.shortcuts)

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

      <Card title="The Papertrail folder">
        <div className="config-grid">
          <PathField label="Folder" hint="holds scans/ and archive/, and the history of both at its root"
            value={draft.root_path} onChange={(v) => set('root_path', v)} />
        </div>
        <p className="config-note">
          Point the scanner at <code>{draft.root_path ? `${draft.root_path}\\scans` : '…\\scans'}</code>; the archive
          (batches, mid-ingest files, YYYY/MM folders) is <code>{draft.root_path ? `${draft.root_path}\\archive` : '…\\archive'}</code>.
          Both are made when the folder is saved. The folder's history is a git repository at its root.
        </p>
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

      <Card title="Fix Rotation" hint="When a scan counts as tilted">
        <Slider label="Tilt worth fixing" value={draft.tilt_share}
          min={options.tilt_share_range[0] ?? 0.005} max={options.tilt_share_range[1] ?? 0.1} step={0.001}
          format={(v) => `${(v * 100).toFixed(1)}% of the short side`} onChange={(v) => set('tilt_share', v)} />
        <p className="config-note">
          A crooked feed leaves an empty wedge along the page's long side. A scan counts as tilted once that
          wedge is this wide against the page's short side, so a long receipt counts at a smaller angle than a
          short one. Under {options.tilt_min_degrees}° no scan counts: that is too small to measure.
        </p>
        <TiltDemo share={draft.tilt_share} minDegrees={options.tilt_min_degrees} maxDegrees={options.tilt_max_degrees} />
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

      <Card title="Shortcuts" hint="click a key, then press the new one">
        {SHORTCUT_GROUPS.map(({ title, actions }) => (
          <div key={title} className="config-keys">
            <h3>{title}</h3>
            <div className="config-grid">
              {actions.map(([action, label]) => (
                <ShortcutField key={action} label={label} value={draft.shortcuts[action]}
                  clash={clashes.has(action)} named={options.shortcut_named_keys}
                  onChange={(v) => set('shortcuts', { ...draft.shortcuts, [action]: v })} />
              ))}
            </div>
          </div>
        ))}
        {clashes.size > 0 && (
          <p className="config-hint neg">
            Two actions on one page can't share a key.
          </p>
        )}
      </Card>

      <Card title="Remembered state" hint="written by the pages themselves">
        <div className="config-grid">
          <div className="field"><label>Calendar period</label><input type="text" value={draft.calendar_period} disabled /></div>
          <div className="field"><label>Calendar date</label><input type="text" value={draft.calendar_date || '(not set)'} disabled /></div>
        </div>
      </Card>

      {save.error && <div className="error-banner" role="alert">{save.error.message}</div>}
      <div className="config-save">
        <button className="primary" disabled={!changed.length || clashes.size > 0 || save.isPending}
          onClick={() => save.mutate()}>
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

/** Where each key works, as the Config page lists them. */
const SHORTCUT_GROUPS: { title: string; actions: [keyof Shortcuts, string][] }[] = [
  { title: 'Review and Workshop', actions: [
    ['accept', 'Accept'], ['mark', 'Mark (Review)'], ['toss', 'Toss'], ['prev', 'Previous document'],
    ['next', 'Next document'], ['undo', 'Undo the last decision (Review)'],
  ] },
  { title: 'Quick matches (Review and Workshop)', actions: [
    ['quick_1', 'First quick match'], ['quick_2', 'Second quick match'], ['quick_3', 'Third quick match'],
  ] },
  { title: 'Scans', actions: [
    ['hide_boxes', 'Hold to hide boxes (Review, Workshop, Experiment, Receipt Detail)'],
    ['hold_original', 'Hold to see the original (Workshop, Experiment)'],
  ] },
  { title: 'Dialogs and scan viewers', actions: [['confirm', 'Confirm'], ['cancel', 'Cancel or close']] },
  { title: "Fix Rotation's scan viewer", actions: [
    ['leave_as_is', 'Leave as is'], ['turn_upright', 'Turn upright'], ['toggle_guides', 'Level guides on or off'],
    ['straighten', 'Straighten'],
  ] },
]

/** Actions live on one page at once, so they can't share a key (`Shortcuts.GROUPS` in settings.py). */
const QUICK: (keyof Shortcuts)[] = ['quick_1', 'quick_2', 'quick_3']
const LIVE_TOGETHER: (keyof Shortcuts)[][] = [
  ['accept', 'mark', 'toss', 'prev', 'next', 'undo', 'hide_boxes', ...QUICK],
  ['accept', 'toss', 'prev', 'next', 'hold_original', 'hide_boxes', ...QUICK], ['confirm', 'cancel'],
  ['leave_as_is', 'turn_upright', 'toggle_guides', 'straighten', 'cancel'],
]

/** The actions the server would refuse (the same rules as `Shortcuts` in settings.py). */
function shortcutClashes(keys: Shortcuts): Set<keyof Shortcuts> {
  const bad = new Set<keyof Shortcuts>()
  for (const group of LIVE_TOGETHER) {
    for (const action of group) {
      const key = keys[action]
      if (group.some((other) => other !== action && keys[other] === key)) bad.add(action)
    }
  }
  return bad
}

/** One key, recorded rather than typed: focus the box and press the key (Tab still moves on). Only a
 *  character or one of the server's `named` keys is taken, so the box can't hold what couldn't be saved. */
function ShortcutField({ label, value, clash, named, onChange }: {
  label: string
  value: string
  clash: boolean
  named: string[]
  onChange: (value: string) => void
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="text" readOnly className={`config-key${clash ? ' neg' : ''}`} value={keyLabel(value)}
        aria-invalid={clash} aria-label={`${label} key`}
        onKeyDown={(e) => {
          if (e.key === 'Tab' || e.ctrlKey || e.metaKey || e.altKey) return
          e.preventDefault()
          const key = keyOf(e)
          if ((key.length === 1 && key.trim()) || named.includes(key)) onChange(key)
        }} />
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
