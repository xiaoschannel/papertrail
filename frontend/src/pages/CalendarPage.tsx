import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { isoDateParts } from '../format.ts'
import { ReceiptCard } from '../components/DocumentCard.tsx'
import { Empty, ErrorState, Loading } from '../components/ui.tsx'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

type Period = 'week' | 'month'

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
/** "YYYY-MM-DD" as a LOCAL date (new Date(string) would parse it as UTC). */
const parseIso = (s: string) => {
  const { year, month, day } = isoDateParts(s)
  return new Date(year, month - 1, day)
}
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
const mondayOfWeek = (d: Date) => addDays(d, -((d.getDay() + 6) % 7))
const firstOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1)
const firstOfNextMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth() + 1, 1)

export default function CalendarPage() {
  const [period, setPeriod] = useState<Period>('month')
  const [anchor, setAnchor] = useState<string | null>(null)

  // Open on the newest month that actually has documents, not an empty current
  // month (the archive may end months ago). With no dated documents at all, fall
  // back to the current month rather than waiting forever.
  const range = useQuery({ queryKey: ['date-range'], queryFn: api.dateRange })
  useEffect(() => {
    if (anchor || !range.isSuccess) return
    const newest = range.data?.max ? parseIso(range.data.max.slice(0, 10)) : new Date()
    setAnchor(iso(firstOfMonth(newest)))
  }, [range.isSuccess, range.data, anchor])

  if (!anchor) {
    return (
      <>
        <h1>Calendar</h1>
        {range.isError ? <ErrorState error={range.error} /> : <Loading what="calendar" />}
      </>
    )
  }
  return <CalendarBody period={period} setPeriod={setPeriod} anchor={anchor} setAnchor={setAnchor} />
}

function CalendarBody({ period, setPeriod, anchor, setAnchor }: {
  period: Period
  setPeriod: (p: Period) => void
  anchor: string
  setAnchor: (iso: string) => void
}) {
  const anchorDate = parseIso(anchor)
  const start = period === 'week' ? mondayOfWeek(anchorDate) : firstOfMonth(anchorDate)
  const end = period === 'week' ? addDays(start, 7) : firstOfNextMonth(start)

  const cal = useQuery({
    queryKey: ['calendar', iso(start), iso(end)],
    queryFn: () => api.calendar(iso(start), iso(end)),
  })

  const cells = useMemo(() => {
    const gridStart = period === 'week' ? start : mondayOfWeek(start)
    const out: { date: Date; inRange: boolean }[] = []
    for (let d = gridStart; d < end || out.length % 7 !== 0; d = addDays(d, 1)) {
      out.push({ date: d, inRange: d >= start && d < end })
      if (out.length > 42) break
    }
    return out
  }, [anchor, period])

  const shift = (dir: 1 | -1) => {
    setAnchor(iso(period === 'week'
      ? addDays(start, 7 * dir)
      : new Date(start.getFullYear(), start.getMonth() + dir, 1)))
  }

  const title = period === 'week'
    ? `Week of ${iso(start)}`
    : start.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

  const byDate = cal.data ?? {}

  return (
    <>
      <h1>Calendar</h1>
      <p className="page-sub">Archived documents laid out by date.</p>

      <div className="controls">
        <div className="field">
          <label>View</label>
          <div className="segmented">
            <button className={period === 'week' ? 'on' : ''} onClick={() => setPeriod('week')}>Week</button>
            <button className={period === 'month' ? 'on' : ''} onClick={() => setPeriod('month')}>Month</button>
          </div>
        </div>
        <div className="field">
          <label>Go to date</label>
          <input type="date" value={anchor} onChange={(e) => e.target.value && setAnchor(e.target.value)} />
        </div>
        <div className="field">
          <label>&nbsp;</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={() => shift(-1)}>← Prev</button>
            <strong style={{ minWidth: 160, textAlign: 'center' }}>{title}</strong>
            <button onClick={() => shift(1)}>Next →</button>
          </div>
        </div>
      </div>

      {cal.isLoading ? <Loading what="calendar" />
        : cal.isError ? <ErrorState error={cal.error} />
        : (
          <>
            <div className="cal-grid" style={{ marginBottom: 6 }}>
              {WEEKDAYS.map((w) => <div key={w} className="cal-head">{w}</div>)}
            </div>
            <div className="cal-grid">
              {cells.map(({ date, inRange }) => {
                const key = iso(date)
                const items = inRange ? (byDate[key] ?? []) : []
                return (
                  <div key={key} className={`cal-cell${inRange ? '' : ' empty'}`}>
                    {inRange && <div className="cal-daynum">{date.getDate()}</div>}
                    {/* Like the Streamlit month view (analytics.balance_into_columns): split a day
                        into two columns only when it has 2+ receipts; CSS then does it
                        only if the cell is wide enough (see .cal-items.split). */}
                    <div className={`cal-items${period === 'month' && items.length >= 2 ? ' split' : ''}`}>
                      {items.map((r) => (
                        <ReceiptCard key={r.filename} rec={r} compact showDate={false} />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
            {Object.keys(byDate).length === 0 && <Empty>No documents in this period.</Empty>}
          </>
        )}
    </>
  )
}
