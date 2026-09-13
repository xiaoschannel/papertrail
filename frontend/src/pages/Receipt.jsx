import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, mediaUrl, money, num } from '../api.js'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.jsx'

export default function Receipt() {
  const [params] = useSearchParams()
  const file = params.get('file') || ''

  const q = useQuery({
    queryKey: ['receipt', file],
    queryFn: () => api.receipt(file),
    enabled: Boolean(file),
  })

  if (!file) {
    return (
      <>
        <h1>Receipt Detail</h1>
        <Empty>Open a receipt from the Merchant Profile, Calendar or Time Capsule to view it here.</Empty>
      </>
    )
  }
  if (q.isLoading) return <Loading what="document" />
  if (q.isError) return <ErrorState error={q.error} />

  const r = q.data
  const pages = r.paths?.length ? r.paths : (r.path ? [r.path] : [])
  const items = r.items || []

  return (
    <>
      <h1 style={{ overflowWrap: 'anywhere' }}>{r.name || r.filename}</h1>
      <p className="page-sub">
        {r.date || 'undated'}{r.time ? ` ${r.time}` : ''} · {r.document_type}
        {pages.length > 1 ? ` · ${pages.length} pages` : ''}
      </p>

      <div className="stack">
        <div className="tiles">
          <Tile label="Cost" value={r.document_type === 'receipt' ? money(r.cost, r.currency) : '—'} />
          <Tile label="Date" value={r.date || '—'} />
          <Tile label="Time" value={r.time || '—'} />
          <Tile label="Brand" value={r.brand_label || '—'} />
          <Tile label="Location" value={r.brand_location || '—'} />
          <Tile label="Language" value={r.language || '—'} />
        </div>

        {/* scan | details | spacer (reserved for later). The scan column's width
            sets the zoom; the scan itself is never cropped or scrolled. */}
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
                          <td className={`num${it.total_price < 0 ? ' neg' : ''}`}>
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

          <div className="spacer" aria-hidden="true" />
        </div>
      </div>
    </>
  )
}
