import { useRef } from 'react'
import type { DocumentType, HintsResponse, ReviewDocument } from '../../api/types.ts'
import { fieldColor } from './ScanOverlay.tsx'

/** The form's editable values (cost stays a string while typing). */
export type FormState = {
  document_type: DocumentType
  name: string
  date: string
  time: string
  cost: string
  currency: string
  comment: string
}

export function initialForm(doc: ReviewDocument): FormState {
  const d = doc.defaults
  return {
    document_type: d.document_type,
    name: doc.initial_name,
    date: d.date,
    time: d.time,
    cost: d.document_type === 'receipt' ? String(d.cost) : '',
    currency: d.currency,
    comment: '',
  }
}

/** A number, or `null` when empty or not a number. Commas count only as thousands separators
 *  ("1,260" -> 1260); "12,50" is rejected rather than read as 1250. */
export function parseCost(cost: string): number | null {
  const trimmed = cost.trim()
  if (!trimmed) return null
  const plain = /^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(trimmed) ? trimmed.replace(/,/g, '') : trimmed
  const value = Number(plain)
  return Number.isFinite(value) ? value : null
}

export const costIsInvalid = (cost: string) => cost.trim() !== '' && parseCost(cost) === null

/** Which OCR-cited source fields each input corresponds to (an "other" document's name is its title). */
export const INPUT_SOURCES: Record<string, readonly string[]> = {
  name: ['name', 'title'],
  date: ['date'],
  time: ['time'],
  cost: ['cost'],
}

const TYPES: { value: DocumentType; label: string }[] = [
  { value: 'receipt', label: 'Receipt' },
  { value: 'other', label: 'Other' },
  { value: 'corrupted', label: 'Corrupted' },
]

const NAME_STATUS = {
  approved: { text: 'Name previously approved', className: 'ok' },
  unseen: { text: 'Name not seen in previous reviews', className: 'warn' },
} as const

/**
 * The review form. Presentational: the page owns the values, hints and saving, so drafts survive
 * moving between documents and keyboard shortcuts can act on the current values.
 */
const pct = (score: number) => `${Math.round(score * 100)}%`

/** "name 87%, phone 100%" — the phone part only when a phone actually matched. */
const scoreText = (m: ReviewDocument['smart_matches'][number]) =>
  m.phone_score > 0 ? `name ${pct(m.name_score)}, phone ${pct(m.phone_score)}` : `name ${pct(m.name_score)}`

export function ReviewForm({ doc, form, onChange, hints, activeFields, onActivate }: {
  doc: ReviewDocument
  form: FormState
  onChange: (patch: Partial<FormState>) => void
  hints: HintsResponse | undefined
  activeFields: readonly string[]
  onActivate: (input: string | null) => void
}) {
  const beforeJpy = useRef('')   // what the currency box held before JPY was pressed
  const receipt = form.document_type === 'receipt'
  const quick = doc.smart_matches.filter((m) => m.quick_apply)
  const bestNameScore = Math.max(0, ...doc.smart_matches.map((m) => m.name_score))

  /** Props linking an input to its boxes on the scan: highlight both on hover or focus. */
  const linked = (input: string) => {
    const sources = INPUT_SOURCES[input] ?? []
    const active = sources.some((s) => activeFields.includes(s))
    return {
      className: `field${active ? ' linked' : ''}`,
      style: { ['--link-color' as string]: fieldColor(sources[0]) },
      onMouseEnter: () => onActivate(input),
      onMouseLeave: () => onActivate(null),
      onFocus: () => onActivate(input),
      onBlur: () => onActivate(null),
    }
  }

  return (
    <div className="review-form">
      {quick.length > 0 && (
        <div className="quick-apply">
          {quick.map((m, i) => (
            <button key={m.name} onClick={() => onChange({ name: m.name })} title="Use this previously confirmed name">
              {i < 3 && <kbd>{i + 1}</kbd>} {m.label}
            </button>
          ))}
        </div>
      )}

      <div className="field">
        <label htmlFor="rv-smart">
          Smart match ({doc.smart_matches.length})
          {doc.smart_matches.length ? ` — best ${pct(bestNameScore)}` : ''}
        </label>
        <select id="rv-smart" value={doc.smart_matches.some((m) => m.name === form.name) ? form.name : ''}
          onChange={(e) => e.target.value && onChange({ name: e.target.value })}
          disabled={doc.smart_matches.length === 0}>
          <option value="">{doc.smart_matches.length ? 'Pick a confirmed name…' : 'No similar names'}</option>
          {/* Every candidate is listed here with its score, the weaker ones included: the score is
              exactly what tells you whether to trust the suggestion. */}
          {doc.smart_matches.map((m) => (
            <option key={m.name} value={m.name}>{`${m.name} — ${scoreText(m)}`}</option>
          ))}
        </select>
      </div>

      <div className="field">
        <label id="rv-type-label">Type</label>
        <div className="segmented" role="group" aria-labelledby="rv-type-label">
          {TYPES.map((t) => (
            <button key={t.value} className={form.document_type === t.value ? 'on' : ''}
              aria-pressed={form.document_type === t.value}
              onClick={() => onChange(t.value === 'receipt' && form.cost.trim() === ''
                ? { document_type: t.value, cost: '0' } : { document_type: t.value })}>{t.label}</button>
          ))}
        </div>
      </div>

      <div {...linked('name')}>
        <label htmlFor="rv-name">Name</label>
        <input id="rv-name" type="text" value={form.name} onChange={(e) => onChange({ name: e.target.value })} />
        {/* A confident smart match overwrites the box, so what the OCR actually read would otherwise
            be off the screen at the moment you are deciding whether the match is right. */}
        {doc.defaults.name && doc.defaults.name !== form.name && (
          <span className="extracted-name">Extracted: {doc.defaults.name}</span>
        )}
        {hints && hints.name_status !== 'placeholder' && (
          <span className={`name-status ${NAME_STATUS[hints.name_status].className}`}>
            {NAME_STATUS[hints.name_status].text}
          </span>
        )}
      </div>

      <div className="form-row">
        <div {...linked('date')}>
          <label htmlFor="rv-date">Date</label>
          <input id="rv-date" type="text" placeholder="YYYY-MM-DD" value={form.date}
            onChange={(e) => onChange({ date: e.target.value })} />
        </div>
        <div {...linked('time')}>
          <label htmlFor="rv-time">Time</label>
          <input id="rv-time" type="text" placeholder="HH:MM" value={form.time}
            onChange={(e) => onChange({ time: e.target.value })} />
        </div>
      </div>

      {receipt && (
        <div className="form-row">
          <div {...linked('cost')}>
            <label htmlFor="rv-cost">Cost</label>
            <input id="rv-cost" type="text" inputMode="decimal" value={form.cost}
              aria-invalid={costIsInvalid(form.cost)} onChange={(e) => onChange({ cost: e.target.value })} />
            {costIsInvalid(form.cost) && <span className="name-status bad">Not a number</span>}
          </div>
          <div className="field">
            <label htmlFor="rv-currency">Currency</label>
            <div className="currency">
              <input id="rv-currency" type="text" value={form.currency}
                onChange={(e) => onChange({ currency: e.target.value.toUpperCase() })} />
              <button className={form.currency === 'JPY' ? 'on' : ''} aria-pressed={form.currency === 'JPY'}
                title={form.currency === 'JPY' ? 'Back to the previous currency' : 'Set currency to JPY'}
                onClick={() => {
                  // A toggle: pressing it again gives back what was in the box.
                  if (form.currency === 'JPY') {
                    onChange({ currency: beforeJpy.current || doc.defaults.currency || '' })
                  } else {
                    beforeJpy.current = form.currency
                    onChange({ currency: 'JPY' })
                  }
                }}>
                JPY
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="field">
        <label htmlFor="rv-comment">Comment</label>
        <textarea id="rv-comment" rows={3} value={form.comment} onChange={(e) => onChange({ comment: e.target.value })} />
      </div>

      {hints && hints.hints.length > 0 && (
        <ul className="hints">
          {hints.hints.map((h) => (
            <li key={h.message} style={h.color ? { color: h.color } : undefined} className={h.color ? '' : 'plain'}>
              {h.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
