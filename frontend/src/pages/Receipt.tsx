import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl } from '../api/client.ts'
import type { DocumentType, VizRecord } from '../api/types.ts'
import { costIsInvalid, parseCost } from '../components/review/ReviewForm.tsx'
import { money, num } from '../format.ts'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'

export default function Receipt() {
  const [params, setParams] = useSearchParams()
  const file = params.get('file') || ''
  const [editing, setEditing] = useState(false)
  useEffect(() => setEditing(false), [file])   // ?file= changes without remounting this page

  const q = useQuery({
    queryKey: ['receipt', file],
    queryFn: () => api.receipt(file),
    enabled: Boolean(file),
  })

  const picker = <DocumentPicker file={file} onPick={(picked) => setParams({ file: picked })} />
  if (!file) {
    return (
      <div className="detail-page">
        <h1>Receipt Detail</h1>
        {picker}
        <Empty>Pick a document, or open one from the Merchant Profile, Calendar or Time Capsule.</Empty>
      </div>
    )
  }
  if (q.isError) return <div className="detail-page">{picker}<ErrorState error={q.error} /></div>
  if (!q.data) return <Loading what="document" />

  const r = q.data
  const pages = r.paths.length ? r.paths : (r.path ? [r.path] : [])
  const items = r.items
  // Only a receipt has a merchant profile; a title of some other document isn't a shop.
  const merchantLink = r.document_type !== 'receipt' ? null
    : r.brand_id ? `/merchant?brand=${encodeURIComponent(r.brand_id)}`
    : r.name ? `/merchant?name=${encodeURIComponent(r.name)}` : null

  return (
    <div className="detail-page">
      {picker}
      <div className="detail-head">
        <div>
          <h1 style={{ overflowWrap: 'anywhere' }}>{r.name || r.filename}</h1>
          <p className="page-sub">
            {r.date || 'undated'}{r.time ? ` ${r.time}` : ''} · {r.document_type}
            {pages.length > 1 ? ` · ${pages.length} pages` : ''} · <code>{r.path || r.filename}</code>
          </p>
        </div>
        {!editing && pages.length > 0 && <button onClick={() => setEditing(true)}>Edit</button>}
      </div>

      <div className="stack">
        {editing
          ? <EditForm key={r.filename} record={r} onDone={() => setEditing(false)} />
          : (
            <div className="tiles">
              <Tile label="Cost" value={r.document_type === 'receipt' ? money(r.cost, r.currency) : '—'} />
              <Tile label="Date" value={r.date || '—'} />
              <Tile label="Time" value={r.time || '—'} />
              <Tile label="Brand" value={merchantLink
                ? <Link className="rowlink" to={merchantLink}>{r.brand_label || r.brand_id || r.name}</Link>
                : (r.brand_label || r.brand_id || '—')} />
              <Tile label="Location" value={r.brand_location || '—'} />
              <Tile label="Language" value={r.language || '—'} />
            </div>
          )}

        {/* scan | details. The scan column's width sets the zoom; the scan itself
            is never cropped or scrolled. */}
        <div className="detail-layout">
          <Card title="Scan" hint={pages.length > 1 ? `${pages.length} pages` : undefined}>
            <div className="scan-full">
              {pages.length === 0 && <Empty>No image on disk.</Empty>}
              {pages.map((p, i) => (
                <img key={p} src={mediaUrl(p)} alt={`${r.name || r.filename} — page ${i + 1}`} />
              ))}
            </div>
          </Card>

          <div className="stack">
            {r.comment && (
              <Card title="Comment" hint="from Review">
                <div style={{ fontSize: 13, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{r.comment}</div>
              </Card>
            )}

            {r.address && (
              <Card title="Address">
                <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{r.address}</div>
              </Card>
            )}

            <Card title="Line Items" hint={items.length ? `${items.length} item(s)` : 'none listed'}>
              {items.length === 0 ? <Empty>No line items extracted.</Empty> : (
                <div className="table-wrap short">
                  <table>
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="num">Qty</th>
                        <th className="num">Unit</th>
                        <th className="num">Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((it, i) => (
                        <tr key={`${it.name}-${i}`}>
                          <td className="ellipsis" title={it.name}>{it.name}</td>
                          <td className="num">{it.quantity != null ? num(it.quantity, 0) : '—'}</td>
                          <td className="num">{it.unit_price != null ? num(it.unit_price) : '—'}</td>
                          <td className={`num${(it.total_price ?? 0) < 0 ? ' neg' : ''}`}>
                            {it.total_price != null ? num(it.total_price) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="Raw OCR" hint="scrolls">
              {r.ocr_markdown
                ? <pre className="textdump">{r.ocr_markdown}</pre>
                : <Empty>No OCR text stored.</Empty>}
            </Card>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Correct an archived document: saving re-files every page under the new name and rewrites its sidecar. */
function EditForm({ record, onDone }: { record: VizRecord; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState({
    document_type: record.document_type as DocumentType,
    name: record.name,
    date: record.date,
    time: record.time,
    cost: record.document_type === 'receipt' ? String(record.cost) : '0',
    currency: record.currency,
    address: record.address,
    language: record.language,
    comment: record.comment,
  })
  const set = <K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) => setDraft({ ...draft, [key]: value })
  const receipt = draft.document_type === 'receipt'

  const save = useMutation({
    mutationFn: () => api.editReceipt({
      file: record.filename,
      document_type: draft.document_type,
      name: draft.name,
      date: draft.date,
      time: draft.time,
      cost: receipt ? parseCost(draft.cost) : 0,
      currency: draft.currency,
      address: draft.address,
      language: draft.language,
      comment: draft.comment,
    }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['receipt', record.filename], updated)
      // The document moved and its values changed: every archive-derived view is stale.
      void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'receipt' })
      onDone()
    },
  })

  return (
    <Card title="Edit document" hint="saving re-files the scan and rewrites its sidecar">
      <div className="edit-grid">
        <div className="field">
          <label>Type</label>
          <div className="segmented">
            {(['receipt', 'other', 'corrupted'] as const).map((t) => (
              <button key={t} className={draft.document_type === t ? 'on' : ''}
                onClick={() => set('document_type', t)}>{t}</button>
            ))}
          </div>
        </div>
        <div className="field">
          <label htmlFor="ed-name">Name</label>
          <input id="ed-name" type="text" value={draft.name} onChange={(e) => set('name', e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ed-date">Date</label>
          <input id="ed-date" type="text" placeholder="YYYY-MM-DD" value={draft.date}
            onChange={(e) => set('date', e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ed-time">Time</label>
          <input id="ed-time" type="text" placeholder="HH:MM" value={draft.time}
            onChange={(e) => set('time', e.target.value)} />
        </div>
        {receipt && (
          <>
            <div className="field">
              <label htmlFor="ed-cost">Cost</label>
              <input id="ed-cost" type="text" inputMode="decimal" value={draft.cost}
                aria-invalid={costIsInvalid(draft.cost)}
                onChange={(e) => set('cost', e.target.value)} />
              {costIsInvalid(draft.cost) && <span className="config-hint neg">Not a number</span>}
            </div>
            <div className="field">
              <label htmlFor="ed-currency">Currency</label>
              <div className="currency">
                <input id="ed-currency" type="text" value={draft.currency}
                  onChange={(e) => set('currency', e.target.value)} />
                <button className={draft.currency.toUpperCase() === 'JPY' ? 'on' : ''}
                  onClick={() => set('currency', draft.currency.toUpperCase() === 'JPY' ? record.currency : 'JPY')}>
                  JPY
                </button>
              </div>
            </div>
            <div className="field">
              <label htmlFor="ed-address">Address</label>
              <input id="ed-address" type="text" value={draft.address} onChange={(e) => set('address', e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="ed-language">Language</label>
              <input id="ed-language" type="text" value={draft.language} onChange={(e) => set('language', e.target.value)} />
            </div>
          </>
        )}
      </div>
      <div className="field" style={{ marginTop: 12 }}>
        <label htmlFor="ed-comment">Comment</label>
        <textarea id="ed-comment" rows={3} value={draft.comment} onChange={(e) => set('comment', e.target.value)} />
      </div>
      {save.error && <div className="error-banner" role="alert">{save.error.message}</div>}
      <div className="start-bar">
        <button className="primary" disabled={save.isPending || (receipt && parseCost(draft.cost) === null)}
          onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        <button disabled={save.isPending} onClick={onDone}>Cancel</button>
      </div>
    </Card>
  )
}

/**
 * Any archived document by its original filename, name or date: type to narrow the list. Charts, the
 * calendar and merchant pages lead only to dated receipts; this reaches the rest too.
 */
function DocumentPicker({ file, onPick }: { file: string; onPick: (file: string) => void }) {
  const documents = useQuery({ queryKey: ['documents'], queryFn: api.documents })
  const [typed, setTyped] = useState<string | null>(null)
  const byFilename = useMemo(() => new Set((documents.data ?? []).map((d) => d.filename)), [documents.data])
  return (
    <div className="field document-picker">
      <label htmlFor="receipt-picker">Document{documents.data ? ` (${documents.data.length})` : ''}</label>
      <input id="receipt-picker" type="text" list="receipt-picker-list" value={typed ?? file}
        placeholder="Type a filename, name or date" autoComplete="off"
        onChange={(e) => {
          const value = e.target.value
          setTyped(value)
          if (byFilename.has(value)) {
            onPick(value)
            setTyped(null)
          }
        }} />
      <datalist id="receipt-picker-list">
        {(documents.data ?? []).map((d) => (
          <option key={d.filename} value={d.filename}>
            {[d.date || 'undated', d.name || d.document_type].join(' · ')}
          </option>
        ))}
      </datalist>
    </div>
  )
}

