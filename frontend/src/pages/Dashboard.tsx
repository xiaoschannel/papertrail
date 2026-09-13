import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, type GroupBy, type RankBy } from '../api/client.ts'
import { totalsLabel, type MerchantTotals } from '../api/types.ts'
import { monthLabel, num } from '../format.ts'
import {
  Card, ChartCard, Empty, ErrorState, Loading, axisProps, niceAxis, tooltipStyle, truncTick,
} from '../components/ui.tsx'

export default function Dashboard() {
  const [year, setYear] = useState<number | undefined>(undefined)   // undefined = all years
  const [rankBy, setRankBy] = useState<RankBy>('total_spend')
  const [groupBy, setGroupBy] = useState<GroupBy>('brand')

  const years = useQuery({ queryKey: ['years'], queryFn: api.years })
  const spend = useQuery({ queryKey: ['monthly-spend', year], queryFn: () => api.monthlySpend(year) })
  const volume = useQuery({ queryKey: ['monthly-volume', year], queryFn: () => api.monthlyVolume(year) })
  const top = useQuery({
    queryKey: ['top-merchants', groupBy, rankBy, year],
    queryFn: () => api.topMerchants({ groupBy, rankBy, year }),
  })

  const keyCol = groupBy === 'brand' ? 'merchant_group' : 'name'
  const label = groupBy === 'brand' ? 'Brand' : 'Merchant'

  return (
    <>
      <h1>Spending Dashboard</h1>
      <p className="page-sub">Monthly totals and the merchants you spend the most with.</p>

      <div className="controls">
        <div className="field">
          <label htmlFor="yr">View</label>
          <select id="yr" value={year ?? ''} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">All years</option>
            {(years.data || []).map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <ChartCard title="Monthly Spending" hint={year ?? 'all years'}>
          {spend.isLoading ? <Loading what="spending" />
            : spend.isError ? <ErrorState error={spend.error} />
            : !spend.data?.length ? <Empty>No dated receipts.</Empty>
            : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={spend.data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="month_ts" tickFormatter={monthLabel} minTickGap={28}
                         interval="preserveStartEnd" {...axisProps} />
                  <YAxis {...niceAxis(spend.data, 'spend')} allowDataOverflow
                         tickFormatter={(v) => num(v)} width={62} {...axisProps} />
                  <Tooltip {...tooltipStyle}
                           labelFormatter={monthLabel}
                           formatter={(v) => [num(Number(v)), 'Spend']} />
                  <Bar dataKey="spend" fill="var(--accent)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            )}
        </ChartCard>

        <ChartCard title="Document Volume" hint="all document types">
          {volume.isLoading ? <Loading what="volume" />
            : volume.isError ? <ErrorState error={volume.error} />
            : !volume.data?.length ? <Empty>No dated documents.</Empty>
            : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={volume.data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="month_ts" tickFormatter={monthLabel} minTickGap={28}
                         interval="preserveStartEnd" {...axisProps} />
                  <YAxis width={42} {...axisProps} />
                  <Tooltip {...tooltipStyle}
                           labelFormatter={monthLabel}
                           formatter={(v) => [num(Number(v)), 'Documents']} />
                  <Bar dataKey="count" fill="var(--muted)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            )}
        </ChartCard>
      </div>

      <Card title="Top Merchants" hint={`top ${top.data?.length ?? 0} by ${rankBy === 'total_spend' ? 'spend' : 'visits'}`}>
        <div className="controls">
          <div className="field">
            <label>Rank by</label>
            <div className="segmented">
              <button className={rankBy === 'total_spend' ? 'on' : ''} onClick={() => setRankBy('total_spend')}>Total Spend</button>
              <button className={rankBy === 'visit_count' ? 'on' : ''} onClick={() => setRankBy('visit_count')}>Visits</button>
            </div>
          </div>
          <div className="field">
            <label>Group by</label>
            <div className="segmented">
              <button className={groupBy === 'brand' ? 'on' : ''} onClick={() => setGroupBy('brand')}>Brand</button>
              <button className={groupBy === 'name' ? 'on' : ''} onClick={() => setGroupBy('name')}>Merchant name</button>
            </div>
          </div>
        </div>

        {top.isLoading ? <Loading what="merchants" />
          : top.isError ? <ErrorState error={top.error} />
          : !top.data?.length ? <Empty>No receipts in this view.</Empty>
          : (
            <div className="grid grid-sidebar">
              <div className="chart-box tall">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={top.data} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                    <XAxis type="number" {...niceAxis(top.data, rankBy, 4)} allowDataOverflow
                           tickFormatter={(v) => num(v)} {...axisProps} />
                    {/* interval={0} forces a label on EVERY bar — without it Recharts
                        silently drops half of them and bars become unidentifiable */}
                    <YAxis type="category" dataKey={keyCol} width={150} interval={0}
                           tickFormatter={truncTick(15)} {...axisProps} />
                    <Tooltip {...tooltipStyle}
                             formatter={(v) => [num(Number(v)), rankBy === 'total_spend' ? 'Total spend' : 'Visits']} />
                    <Bar dataKey={rankBy} fill="var(--accent)" radius={[0, 3, 3, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="table-wrap" style={{ maxHeight: 460 }}>
                <table className="fixed">
                  {/* fixed widths for the numeric columns so a long CJK merchant
                      name can never push "Avg" out of view */}
                  <colgroup>
                    <col />
                    <col style={{ width: 92 }} />
                    <col style={{ width: 62 }} />
                    <col style={{ width: 74 }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>{label}</th>
                      <th className="num">Spend</th>
                      <th className="num">Visits</th>
                      <th className="num">Avg</th>
                    </tr>
                  </thead>
                  <tbody>
                    {top.data.map((r: MerchantTotals) => {
                      const rowLabel = totalsLabel(r)
                      const target = groupBy === 'brand' && r.brand_id
                        ? `/merchant?brand=${encodeURIComponent(r.brand_id)}`
                        : `/merchant?name=${encodeURIComponent(rowLabel)}`
                      return (
                        <tr key={rowLabel}>
                          <td className="ellipsis" title={rowLabel}>
                            <Link className="rowlink" to={target}>{rowLabel}</Link>
                          </td>
                          <td className="num">{num(r.total_spend)}</td>
                          <td className="num">{num(r.visit_count)}</td>
                          <td className="num">{num(r.avg_per_visit)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
      </Card>
    </>
  )
}
