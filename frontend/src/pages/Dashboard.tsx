import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, type GroupBy, type RankBy } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
import { totalsLabel, type MerchantTotals } from '../api/types.ts'
import { num } from '../format.ts'
import { Card, ChartCard, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { EChart, useChartTheme } from '../components/EChart.tsx'
import { monthlyBars, rankedBars } from '../components/chartOptions.ts'

/** The two monthly charts share one timeline, so zooming either keeps them aligned. */
const TIMELINE_GROUP = 'dashboard-timeline'

/** `dashboard_rank_by` is stored the way the Config page shows it. */
const RANK_LABEL: Record<RankBy, string> = { total_spend: 'Total Spend', visit_count: 'Visit Count' }

export default function Dashboard() {
  const [year, setYear] = useState<number | undefined>(undefined)   // undefined = all years
  const [groupBy, setGroupBy] = useState<GroupBy>('brand')

  // The ranking is remembered in config.json (as in Streamlit), so the dashboard opens the way it was left.
  const config = useConfig()
  const saveConfig = useSaveConfig()
  const [chosenRank, setChosenRank] = useState<RankBy | null>(null)
  const rankBy: RankBy = chosenRank ?? (config.data?.dashboard_rank_by === 'Visit Count' ? 'visit_count' : 'total_spend')
  const pickRank = (next: RankBy) => {
    setChosenRank(next)
    saveConfig.mutate({ dashboard_rank_by: RANK_LABEL[next] })
  }

  const years = useQuery({ queryKey: ['years'], queryFn: api.years })
  const spend = useQuery({ queryKey: ['monthly-spend', year], queryFn: () => api.monthlySpend(year) })
  const volume = useQuery({ queryKey: ['monthly-volume', year], queryFn: () => api.monthlyVolume(year) })
  const top = useQuery({
    queryKey: ['top-merchants', groupBy, rankBy, year],
    queryFn: () => api.topMerchants({ groupBy, rankBy, year }),
  })

  const label = groupBy === 'brand' ? 'Brand' : 'Merchant'

  const theme = useChartTheme()
  const spendOption = useMemo(
    () => monthlyBars((spend.data ?? []).map((r) => ({ month_ts: r.month_ts, value: r.spend })), { name: 'Spend' }, theme),
    [spend.data, theme],
  )
  const volumeOption = useMemo(
    () => monthlyBars((volume.data ?? []).map((r) => ({ month_ts: r.month_ts, value: r.count })),
      { name: 'Documents', color: theme.muted }, theme),
    [volume.data, theme],
  )
  const topOption = useMemo(
    () => rankedBars((top.data ?? []).map((r) => ({ label: totalsLabel(r), value: r[rankBy] })),
      { name: rankBy === 'total_spend' ? 'Total spend' : 'Visits' }, theme),
    [top.data, rankBy, theme],
  )

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
            : <EChart option={spendOption} group={TIMELINE_GROUP} />}
        </ChartCard>

        <ChartCard title="Document Volume" hint="all document types">
          {volume.isLoading ? <Loading what="volume" />
            : volume.isError ? <ErrorState error={volume.error} />
            : !volume.data?.length ? <Empty>No dated documents.</Empty>
            : <EChart option={volumeOption} group={TIMELINE_GROUP} />}
        </ChartCard>
      </div>

      <Card title="Top Merchants" hint={`top ${top.data?.length ?? 0} by ${rankBy === 'total_spend' ? 'spend' : 'visits'}`}>
        <div className="controls">
          <div className="field">
            <label>Rank by</label>
            <div className="segmented">
              <button className={rankBy === 'total_spend' ? 'on' : ''} onClick={() => pickRank('total_spend')}>Total Spend</button>
              <button className={rankBy === 'visit_count' ? 'on' : ''} onClick={() => pickRank('visit_count')}>Visits</button>
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
                <EChart option={topOption} />
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
