import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { VizRecord } from '../api/types.ts'
import { isoDateParts } from '../format.ts'
import { ReceiptGallery } from '../components/DocumentCard.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'

const todayIso = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function TimeCapsule() {
  const [date, setDate] = useState(todayIso)
  const { month, day } = isoDateParts(date)

  const q = useQuery({
    queryKey: ['timecapsule', month, day],
    queryFn: () => api.timecapsule(month, day),
    enabled: Boolean(month && day),
  })

  const byYear = useMemo(() => {
    const groups = new Map<number, VizRecord[]>()
    for (const r of q.data ?? []) {
      if (r.year === null) continue   // the endpoint returns dated documents only
      const group = groups.get(r.year)
      if (group) group.push(r)
      else groups.set(r.year, [r])
    }
    return [...groups.entries()].sort((a, b) => b[0] - a[0])
  }, [q.data])

  // local date (new Date('YYYY-MM-DD') would parse as UTC and can show the previous day);
  // leap year 2000 so Feb 29 labels correctly
  const label = new Date(2000, month - 1, day).toLocaleDateString(undefined, { month: 'long', day: 'numeric' })

  return (
    <>
      <h1>Time Capsule</h1>
      <p className="page-sub">The same day, across every year in the archive.</p>

      <div className="controls">
        <div className="field">
          <label>On this day…</label>
          <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        </div>
      </div>

      {q.isLoading ? <Loading what="documents" />
        : q.isError ? <ErrorState error={q.error} />
        : !q.data?.length ? <Empty>No documents on {label} in any year.</Empty>
        : (
          <div className="stack">
            <p className="page-sub" style={{ margin: 0 }}>
              <strong>{q.data.length}</strong> document(s) on <strong>{label}</strong> across{' '}
              <strong>{byYear.length}</strong> year(s).
            </p>
            {byYear.map(([year, recs]) => (
              <Card key={year} title={String(year)} hint={`${recs.length} document(s)`}>
                <ReceiptGallery receipts={recs} showDate={false} />
              </Card>
            ))}
          </div>
        )}
    </>
  )
}
