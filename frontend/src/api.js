const qs = (params) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, v)
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function get(path) {
  const r = await fetch(path)
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* not json */ }
    throw new Error(detail || `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export const api = {
  years: () => get('/api/years'),
  dateRange: () => get('/api/date-range'),
  merchants: (groupBy = 'brand') => get(`/api/merchants${qs({ group_by: groupBy })}`),
  topMerchants: ({ groupBy = 'brand', rankBy = 'total_spend', year, topN = 20 } = {}) =>
    get(`/api/analytics/top-merchants${qs({ group_by: groupBy, rank_by: rankBy, year, top_n: topN })}`),
  monthlySpend: (year) => get(`/api/analytics/monthly-spend${qs({ year })}`),
  monthlyVolume: (year) => get(`/api/analytics/monthly-volume${qs({ year })}`),
  merchant: ({ brandId, name }) => get(`/api/analytics/merchant${qs({ brand_id: brandId, name })}`),
  calendar: (start, end) => get(`/api/analytics/calendar${qs({ start, end })}`),
  timecapsule: (month, day) => get(`/api/analytics/timecapsule${qs({ month, day })}`),
  receipt: (file) => get(`/api/receipt${qs({ file })}`),
}

/** Archived media lives under YYYY/MM with spaces + unicode in the name. */
export const mediaUrl = (relPath) =>
  relPath ? `/api/media/archived/${relPath.split('/').map(encodeURIComponent).join('/')}` : null

// --- formatting -------------------------------------------------------------
const NO_DECIMALS = new Set(['JPY', 'KRW'])

export function money(value, currency) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const digits = NO_DECIMALS.has((currency || '').toUpperCase()) ? 0 : 2
  const n = Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return currency ? `${n} ${currency}` : n
}

export const num = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** "2025-01-01T00:00:00" -> "2025-01" */
export const monthLabel = (iso) => (iso ? String(iso).slice(0, 7) : '')

export const dayLabel = (iso) => (iso ? String(iso).slice(0, 10) : '')

export const truncate = (s, n = 22) =>
  !s ? '' : String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s)
