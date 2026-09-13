import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, dayLabel, money, monthLabel, num } from '../api.js'
import {
  Card, ChartCard, Empty, ErrorState, Loading, Tile, axisProps, niceAxis, tooltipStyle,
} from '../components/ui.jsx'
import { ReceiptGallery } from '../components/DocumentCard.jsx'

export default function Merchant() {
  const [params, setParams] = useSearchParams()
  const [mode, setMode] = useState(params.get('name') ? 'name' : 'brand')

  const groupBy = mode === 'brand' ? 'brand' : 'name'
  const list = useQuery({ queryKey: ['merchants', groupBy], queryFn: () => api.merchants(groupBy) })

  const selected = mode === 'brand' ? params.get('brand') : params.get('name')
  // default to the biggest merchant once the list arrives
  const options = list.data || []
  const fallback = mode === 'brand'
    ? options.find((o) => o.brand_id)?.brand_id
    : options[0]?.name
  const current = selected || fallback || ''

  const detail = useQuery({
    queryKey: ['merchant', mode, current],
    queryFn: () => api.merchant(mode === 'brand' ? { brandId: current } : { name: current }),
    enabled: Boolean(current),
  })

  const choose = (value) => {
    setParams(value ? { [mode === 'brand' ? 'brand' : 'name']: value } : {})
  }
  const switchMode = (m) => {
    setMode(m)
    setParams({})
  }

  const m = detail.data?.metrics
  const receipts = detail.data?.receipts || []

  return (
    <>
      <h1>Merchant Profile</h1>
      <p className="page-sub">Spend, cadence and receipts for one merchant or brand.</p>

      <div className="controls">
        <div className="field">
          <label>Match by</label>
          <div className="segmented">
            <button className={mode === 'brand' ? 'on' : ''} onClick={() => switchMode('brand')}>Brand</button>
            <button className={mode === 'name' ? 'on' : ''} onClick={() => switchMode('name')}>Exact name</button>
          </div>
        </div>
        <div className="field" style={{ flex: 1, minWidth: 260 }}>
          <label>{mode === 'brand' ? 'Brand' : 'Merchant'}</label>
          <select value={current} onChange={(e) => choose(e.target.value)} style={{ maxWidth: 420 }}>
            {options.map((o) => {
              const value = mode === 'brand' ? o.brand_id : o.name
              const label = mode === 'brand' ? o.merchant_group : o.name
              if (!value) return null
              return (
                <option key={value} value={value}>
                  {label} — {num(o.visit_count)} receipt(s)
                </option>
              )
            })}
          </select>
        </div>
      </div>

      {list.isError ? <ErrorState error={list.error} />
        : list.isLoading ? <Loading what="merchants" />
        : !current ? <Empty>No merchants found.</Empty>
        : detail.isLoading ? <Loading what="merchant" />
        : detail.isError ? <ErrorState error={detail.error} />
        : (
          <div className="stack">
            <div className="tiles">
              <Tile label="Total Spend" value={money(m?.total_spend, m?.currency)} />
              <Tile label="Visits" value={num(m?.visit_count)} />
              <Tile label="Avg Ticket" value={money(m?.avg_ticket, m?.currency)} />
              <Tile label="First Visit" value={dayLabel(m?.first_visit) || '—'} />
              <Tile label="Last Visit" value={dayLabel(m?.last_visit) || '—'} />
              <Tile label="Avg Days Between" value={m?.avg_gap != null ? num(m.avg_gap, 0) : '—'} />
            </div>

            <div className="grid grid-2">
              <ChartCard title="Spending Trend">
                {!detail.data.trend?.length ? <Empty>No dated receipts.</Empty> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={detail.data.trend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                      <XAxis dataKey="month_ts" tickFormatter={monthLabel} minTickGap={28}
                             interval="preserveStartEnd" {...axisProps} />
                      <YAxis {...niceAxis(detail.data.trend, 'spend')} allowDataOverflow
                             tickFormatter={(v) => num(v)} width={62} {...axisProps} />
                      <Tooltip {...tooltipStyle} labelFormatter={monthLabel}
                               formatter={(v) => [num(v), 'Spend']} />
                      <Bar dataKey="spend" fill="var(--accent)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>

              <ChartCard title="Visit Cadence" hint="days since previous visit">
                {!detail.data.cadence?.length ? <Empty>Needs at least two visits.</Empty> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="visit_date" tickFormatter={dayLabel} minTickGap={34}
                             interval="preserveStartEnd" {...axisProps} />
                      <YAxis dataKey="days_since_last" width={46} {...axisProps} />
                      <Tooltip {...tooltipStyle} labelFormatter={dayLabel}
                               formatter={(v) => [num(v), 'Days since last']} />
                      <Scatter data={detail.data.cadence} fill="var(--accent)" isAnimationActive={false} />
                    </ScatterChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </div>

            {detail.data.items?.length > 0 && (
              <Card title="Item Breakdown" hint={`${detail.data.items.length} distinct item(s)`}>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="num">Count</th>
                        <th className="num">Total</th>
                        <th className="num">Avg Unit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...detail.data.items]
                        .sort((a, b) => b.times_purchased - a.times_purchased)
                        .map((it) => (
                          <tr key={it.item_name}>
                            <td className="ellipsis" title={it.item_name}>{it.item_name}</td>
                            <td className="num">{num(it.times_purchased)}</td>
                            <td className={`num${it.total_spent < 0 ? ' neg' : ''}`}>{num(it.total_spent)}</td>
                            <td className={`num${it.avg_unit_price < 0 ? ' neg' : ''}`}>{num(it.avg_unit_price, 1)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <Card title="Receipts" hint={`${receipts.length} total`}>
              {/* keyed by selection so switching merchant starts at page 1 */}
              <ReceiptGallery key={`${mode}:${current}`} receipts={receipts} rowsPerPage={3} />
            </Card>
          </div>
        )}
    </>
  )
}
