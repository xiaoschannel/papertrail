import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
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
  const [period, setPeriodState] = useState<Period | null>(null)
  const [anchor, setAnchorState] = useState<string | null>(null)

  // Where the calendar was left last time (the Config page shows both values).
  const config = useConfig()
  const saveConfig = useSaveConfig()
  const range = useQuery({ queryKey: ['date-range'], queryFn: api.dateRange })

  // Fall back to the newest month that actually has documents, not an empty current month (the
  // archive may end months ago). With no dated documents at all, use the current month.
  useEffect(() => {
    if (anchor || !range.isSuccess || !config.isSuccess) return
    const newest = range.data?.max ? parseIso(range.data.max.slice(0, 10)) : new Date()
    const remembered = config.data.calendar_date ? parseIso(config.data.calendar_date) : null
    const view: Period = config.data.calendar_period === 'week' ? 'week' : 'month'
    const start = remembered && !Number.isNaN(remembered.getTime()) ? remembered : newest
    setPeriodState(view)
    setAnchorState(place(view, iso(start)))
  }, [range.isSuccess, range.data, config.isSuccess, config.data, anchor])

  const remember = (view: Period, day: string) => saveConfig.mutate({ calendar_period: view, calendar_date: day })
  // The anchor is always a period START (Monday, or the 1st) inside the archive's range: switching
  // view or picking a mid-period day snaps to it, so the date picker's value always
  // satisfies its own bounds and the view always lands on a period that can hold documents.
  const startOf = (view: Period, day: string) =>
    iso(view === 'week' ? mondayOfWeek(parseIso(day)) : firstOfMonth(parseIso(day)))
  const place = (view: Period, day: string) => {
    const first = range.data?.min ? startOf(view, range.data.min.slice(0, 10)) : undefined
    const last = range.data?.max ? startOf(view, range.data.max.slice(0, 10)) : undefined
    const start = startOf(view, day)
    if (first !== undefined && start < first) return first
    if (last !== undefined && start > last) return last
    return start
  }
  const setPeriod = (next: Period) => {
    setPeriodState(next)
    if (anchor) {
      const day = place(next, anchor)
      setAnchorState(day)
      remember(next, day)
    }
  }
  const setAnchor = (next: string) => {
    if (!period) return
    const day = place(period, next)
    setAnchorState(day)
    remember(period, day)
  }

  if (!anchor || !period) {
    return (
      <>
        <h1>Calendar</h1>
        {range.isError ? <ErrorState error={range.error} />
          : config.isError ? <ErrorState error={config.error} />
            : <Loading what="calendar" />}
      </>
    )
  }
  return (
    <CalendarBody period={period} setPeriod={setPeriod} anchor={anchor} setAnchor={setAnchor}
      min={range.data?.min ? range.data.min.slice(0, 10) : undefined}
      max={range.data?.max ? range.data.max.slice(0, 10) : undefined} />
  )
}

function CalendarBody({ period, setPeriod, anchor, setAnchor, min, max }: {
  period: Period
  setPeriod: (p: Period) => void
  anchor: string
  setAnchor: (iso: string) => void
  min?: string | undefined
  max?: string | undefined
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

  // The picker holds a period START, so its bounds have to be period starts too, or the browser
  // marks the value out of range and the first days of the earliest month can't be picked.
  const align = (day: string) => iso(period === 'week' ? mondayOfWeek(parseIso(day)) : firstOfMonth(parseIso(day)))
  const minAnchor = min !== undefined ? align(min) : undefined
  const maxAnchor = max !== undefined ? align(max) : undefined

  // Paging stops at the archive's first and last document, so you can't walk into empty years.
  const next = (dir: 1 | -1) => iso(period === 'week'
    ? addDays(start, 7 * dir)
    : new Date(start.getFullYear(), start.getMonth() + dir, 1))
  const beyond = (day: string, dir: 1 | -1) =>
    (dir < 0 && minAnchor !== undefined && day < minAnchor) || (dir > 0 && maxAnchor !== undefined && day > maxAnchor)
  const canShift = (dir: 1 | -1) => !beyond(next(dir), dir)
  const shift = (dir: 1 | -1) => {
    if (canShift(dir)) setAnchor(next(dir))
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
          <label htmlFor="calendar-date">Go to date</label>
          <input id="calendar-date" type="date" value={anchor} min={minAnchor} max={maxAnchor}
            onChange={(e) => e.target.value && setAnchor(e.target.value)} />
        </div>
        <div className="field">
          <label>&nbsp;</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button disabled={!canShift(-1)} onClick={() => shift(-1)}>← Prev</button>
            <strong style={{ minWidth: 160, textAlign: 'center' }}>{title}</strong>
            <button disabled={!canShift(1)} onClick={() => shift(1)}>Next →</button>
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
                    {/* In month view, split a day into two columns only when it has 2+
                        receipts; CSS then does it
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
