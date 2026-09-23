type Maybe<T> = T | null | undefined

const NO_DECIMALS = new Set(['JPY', 'KRW'])

const missing = (v: Maybe<number>): v is null | undefined => v === null || v === undefined || Number.isNaN(v)

export function money(value: Maybe<number>, currency?: Maybe<string>): string {
  if (missing(value)) return '—'
  const digits = NO_DECIMALS.has((currency || '').toUpperCase()) ? 0 : 2
  const n = value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return currency ? `${n} ${currency}` : n
}

export const num = (v: Maybe<number>, digits = 0): string =>
  missing(v) ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** "1 file", "3 files": a count and its noun, the noun made plural with an s. */
export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** What a run or a batch cost, in cents: what a total is read for. Under a cent, the cents aren't it. */
export const spend = (value: number): string => (value < 0.01 ? '<$0.01' : `$${value.toFixed(2)}`)

/** What one call cost, where a hundredth of a cent is the difference between two models. */
export const spendExactly = (value: number): string => `$${value.toFixed(value < 0.01 ? 4 : 2)}`

/** "YYYY-MM-DD" as numbers. Splits on '-' rather than slicing fixed offsets, because a date
 *  input accepts years beyond 9999. Malformed parts come back as NaN. */
export function isoDateParts(s: string): { year: number; month: number; day: number } {
  const [year = '', month = '', day = ''] = s.split('-')
  return { year: Number(year), month: Number(month), day: Number(day) }
}

/** "2025-01-01T00:00:00" -> "2025-01" */
export const monthLabel = (iso: unknown): string => (iso ? String(iso).slice(0, 7) : '')

export const dayLabel = (iso: unknown): string => (iso ? String(iso).slice(0, 10) : '')
