import type { ReviewSummary } from '../../api/types.ts'

const PENDING_COLOR = '#6c757d'

/** One stacked bar: documents still to review, then each verdict in its color. */
export function ReviewProgress({ summary }: { summary: ReviewSummary }) {
  const segments = [
    { key: 'pending', label: 'Review', color: PENDING_COLOR, count: summary.pending },
    ...summary.verdicts.map((v) => ({ key: v.verdict, label: v.label, color: v.color, count: v.count })),
  ].filter((s) => s.count > 0)
  const total = Math.max(summary.total, 1)
  return (
    <div className="review-progress" role="img"
      aria-label={segments.map((s) => `${s.label}: ${s.count}`).join(', ')}>
      {segments.map((s) => (
        <div key={s.key} style={{ width: `${(s.count / total) * 100}%`, background: s.color }}>
          {s.label}: {s.count}
        </div>
      ))}
    </div>
  )
}
