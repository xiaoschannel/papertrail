import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, type GroupBy } from '../../api/client.ts'
import { totalsLabel, type MerchantTotals } from '../../api/types.ts'
import { dayLabel, money, num } from '../../format.ts'
import { Card, ChartCard, Empty, ErrorState, Loading, Tile } from '../../components/ui.tsx'
import { EChart, useChartTheme } from '../../components/charts/EChart.tsx'
import { cadenceScatter, monthlyBars } from '../../components/charts/chartOptions.ts'
import { ReceiptGallery } from '../../components/scans/DocumentCard.tsx'
import { SearchSelect, type Choice } from '../../components/SearchSelect.tsx'

export default function Merchant() {
  const [params, setParams] = useSearchParams()
  const [mode, setMode] = useState<GroupBy>(params.get('name') ? 'name' : 'brand')

  const list = useQuery({ queryKey: ['merchants', mode], queryFn: () => api.merchants(mode) })

  const selected = mode === 'brand' ? params.get('brand') : params.get('name')
  const choices: Choice[] = useMemo(() => (list.data ?? []).flatMap((o: MerchantTotals) => {
    // a row's value: its brand id, or its exact merchant name
    const value = mode === 'brand' ? o.brand_id : 'name' in o ? o.name : null
    if (!value) return []
    const label = totalsLabel(o)
    return [{
      value,
      label: mode === 'brand' && label !== value ? `${label} (${value})` : label,
      detail: `${num(o.visit_count)} receipt(s)`,
    }]
  }), [list.data, mode])
  // default to the biggest merchant once the list arrives
  const current = selected || choices[0]?.value || ''

  const detail = useQuery({
    queryKey: ['merchant', mode, current],
    queryFn: () => api.merchant(mode === 'brand' ? { brandId: current } : { name: current }),
    enabled: Boolean(current),
  })

  const choose = (value: string) => {
    setParams(value ? { [mode === 'brand' ? 'brand' : 'name']: value } : {})
  }
  const switchMode = (m: GroupBy) => {
    setMode(m)
    setParams({})
  }

  const m = detail.data?.metrics
  const receipts = detail.data?.receipts || []

  const theme = useChartTheme()
  const trendOption = useMemo(
    () => monthlyBars((detail.data?.trend ?? []).map((r) => ({ month_ts: r.month_ts, value: r.spend })),
      { name: 'Spend' }, theme),
    [detail.data, theme],
  )
  const cadenceOption = useMemo(() => cadenceScatter(detail.data?.cadence ?? [], theme), [detail.data, theme])

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
          <label htmlFor="merchant-pick">{mode === 'brand' ? 'Brand' : 'Merchant'}</label>
          <SearchSelect id="merchant-pick" className="wide" choices={choices} value={current} onChange={choose}
            placeholder={mode === 'brand' ? 'Type a brand' : 'Type a merchant name'} />
        </div>
      </div>

      {list.isError ? <ErrorState error={list.error} />
        : list.isLoading ? <Loading what="merchants" />
        : !current ? (mode === 'brand'
          ? <Empty>No brands have receipts yet. <Link className="rowlink" to="/brands">Add brands in Brand registry →</Link></Empty>
          : <Empty>No merchants found.</Empty>)
        : detail.isError ? <ErrorState error={detail.error} />
        : !detail.data ? <Loading what="merchant" />
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
                {!detail.data.trend.length ? <Empty>No dated receipts.</Empty> : <EChart option={trendOption} />}
              </ChartCard>

              <ChartCard title="Visit Cadence" hint="days since previous visit">
                {!detail.data.cadence.length ? <Empty>Needs at least three visits.</Empty> : <EChart option={cadenceOption} />}
              </ChartCard>
            </div>

            {mode === 'brand' && detail.data.locations.length > 0 && (
              <Card title="Locations" hint={`${detail.data.locations.length} branch(es)`} className="card--table">
                <div className="table-wrap short">
                  <table>
                    <thead><tr><th>Location</th><th className="num">Receipts</th></tr></thead>
                    <tbody>
                      {detail.data.locations.map((row) => (
                        <tr key={row.location}>
                          <td className="ellipsis" title={row.location}>{row.location}</td>
                          <td className="num">{num(row.count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {detail.data.items.length > 0 && (
              <Card title="Item Breakdown" hint={`${detail.data.items.length} distinct item(s)`}
                className="card--table">
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
                            <td className={`num${(it.avg_unit_price ?? 0) < 0 ? ' neg' : ''}`}>{num(it.avg_unit_price, 1)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <Card title="Receipts" hint={`${receipts.length} total`}>
              {/* keyed by selection so switching merchant starts at page 1 */}
              <ReceiptGallery key={`${mode}:${current}`} receipts={receipts} rowsPerPage={3} showBranch={mode === 'brand'} />
            </Card>
          </div>
        )}
    </>
  )
}
