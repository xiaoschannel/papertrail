import createClient from 'openapi-fetch'
import type { paths } from './schema'
import type { MerchantTotals } from './types.ts'

/** Typed HTTP client. Paths, query parameters and response bodies all come from
 *  `schema.d.ts`, generated from the backend's OpenAPI schema (`npm run gen:api`). */
const client = createClient<paths>()

type Result<T> = { data?: T; error?: unknown; response: Response }

/** Resolve a request to its body, or throw so React Query surfaces the error.
 *  FastAPI errors carry a `detail` string (or a validation-error list). */
async function unwrap<T>(request: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await request
  if (data !== undefined) return data
  const detail = typeof error === 'object' && error !== null && 'detail' in error ? error.detail : undefined
  throw new Error(typeof detail === 'string' && detail ? detail : `${response.status} ${response.statusText}`)
}

export type GroupBy = 'brand' | 'name'
export type RankBy = 'total_spend' | 'visit_count'

export const api = {
  years: () => unwrap(client.GET('/api/years')),
  dateRange: () => unwrap(client.GET('/api/date-range')),
  // Totals rows are brand rows or name rows depending on group_by; callers get one
  // array of either (label via totalsLabel) instead of a union of two array types.
  merchants: (groupBy: GroupBy = 'brand'): Promise<MerchantTotals[]> =>
    unwrap(client.GET('/api/merchants', { params: { query: { group_by: groupBy } } })),
  topMerchants: ({ groupBy = 'brand', rankBy = 'total_spend', year, topN = 20 }:
    { groupBy?: GroupBy; rankBy?: RankBy; year?: number | undefined; topN?: number } = {}): Promise<MerchantTotals[]> =>
    unwrap(client.GET('/api/analytics/top-merchants', {
      params: { query: { group_by: groupBy, rank_by: rankBy, top_n: topN, year: year ?? null } },
    })),
  monthlySpend: (year?: number) =>
    unwrap(client.GET('/api/analytics/monthly-spend', { params: { query: { year: year ?? null } } })),
  monthlyVolume: (year?: number) =>
    unwrap(client.GET('/api/analytics/monthly-volume', { params: { query: { year: year ?? null } } })),
  merchant: (selection: { brandId: string } | { name: string }) =>
    unwrap(client.GET('/api/analytics/merchant', {
      params: { query: 'brandId' in selection ? { brand_id: selection.brandId } : { name: selection.name } },
    })),
  calendar: (start: string, end: string) =>
    unwrap(client.GET('/api/analytics/calendar', { params: { query: { start, end } } })),
  timecapsule: (month: number, day: number) =>
    unwrap(client.GET('/api/analytics/timecapsule', { params: { query: { month, day } } })),
  receipt: (file: string) => unwrap(client.GET('/api/receipt', { params: { query: { file } } })),
}

/** Archived media lives under YYYY/MM with spaces + unicode in the name. */
export const mediaUrl = (relPath: string | null | undefined): string | undefined =>
  relPath ? `/api/media/archived/${relPath.split('/').map(encodeURIComponent).join('/')}` : undefined
