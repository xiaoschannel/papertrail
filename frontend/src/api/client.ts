import createClient from 'openapi-fetch'
import type { paths } from './schema'
import type {
  AppConfig, BrandIn, ConfirmIndexIn, DecisionIn, Draft, ExperimentOcrIn, ExperimentParseIn, ExperimentTreatment,
  MerchantTotals, ReceiptEditIn, SheetGrid, StartOcrIn, StartParseIn, TopPoints, Verdict, WorkshopDecisionIn,
  WorkshopReprocessIn,
} from './types.ts'

/** Typed HTTP client. Paths, query parameters and response bodies all come from
 *  `schema.d.ts`, generated from the backend's OpenAPI schema (`npm run gen:api`). */
const client = createClient<paths>()

type Result<T> = { data?: T; error?: unknown; response: Response }

/** A refused request: the server's reason, and its status for callers that act on the kind of refusal. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

/** Resolve a request to its body, or throw so React Query surfaces the error.
 *  FastAPI errors carry a `detail` string (or a validation-error list). */
async function unwrap<T>(request: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await request
  if (data !== undefined) return data
  const detail = typeof error === 'object' && error !== null && 'detail' in error ? error.detail : undefined
  throw new ApiError(typeof detail === 'string' && detail ? detail : `${response.status} ${response.statusText}`,
    response.status)
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
  /** What reading and extracting this document took, when it was filed after Papertrail kept that. */
  receiptRuns: (file: string) => unwrap(client.GET('/api/receipt/runs', { params: { query: { file } } })),
  receiptPages: (file: string) => unwrap(client.GET('/api/receipt/pages', { params: { query: { file } } })),
  /** Every archived document (original filename, name, date, type), for Receipt Detail's picker. */
  documents: () => unwrap(client.GET('/api/documents')),
  /** Correct an archived document: the server re-files its pages and rewrites their sidecars. */
  editReceipt: (body: ReceiptEditIn) => unwrap(client.PATCH('/api/receipt', { body })),

  config: () => unwrap(client.GET('/api/config')),
  configOptions: () => unwrap(client.GET('/api/config/options')),
  pathCheck: (path: string) => unwrap(client.GET('/api/config/path-check', { params: { query: { path } } })),
  /** Change only the given settings (other pages may have changed the rest). */
  patchConfig: (patch: Partial<AppConfig>) => unwrap(client.PATCH('/api/config', { body: patch })),

  review: {
    queue: () => unwrap(client.GET('/api/review/queue')),
    document: (key: string) => unwrap(client.GET('/api/review/document', { params: { query: { key } } })),
    hints: (key: string, draft: Draft) => unwrap(client.POST('/api/review/hints', { body: { key, draft } })),
    decide: (decision: DecisionIn) => unwrap(client.POST('/api/review/decisions', { body: decision })),
    /** Take back `decision`, exactly as it was made; refused (409/404) if it's no longer the one on file. */
    undo: (decision: DecisionIn) => unwrap(client.POST('/api/review/undo', { body: decision })),
    clearAll: () => unwrap(client.DELETE('/api/review/decisions')),
  },

  ingest: {
    index: (scheme?: string) =>
      unwrap(client.GET('/api/ingest/index', { params: { query: { scheme: scheme ?? null } } })),
    confirmIndex: (body: ConfirmIndexIn) => unwrap(client.POST('/api/ingest/index', { body })),
    grouping: (batchId?: number) =>
      unwrap(client.GET('/api/ingest/grouping', { params: { query: { batch_id: batchId ?? null } } })),
    saveGrouping: (batchId: number, groups: string[][]) =>
      unwrap(client.PUT('/api/ingest/grouping', { body: { batch_id: batchId, groups } })),
    toss: (key: string) => unwrap(client.POST('/api/ingest/pages/toss', { body: { key } })),
    recover: (key: string) => unwrap(client.POST('/api/ingest/pages/recover', { body: { key } })),
    rotate: (key: string, topPoints: TopPoints) =>
      unwrap(client.POST('/api/ingest/pages/rotate', { body: { key, top_points: topPoints } })),
    slicing: (batchId?: number) =>
      unwrap(client.GET('/api/ingest/slicing', { params: { query: { batch_id: batchId ?? null } } })),
    /** What cutting `key` by `grid` (null: unslicing it) would do, to confirm before `applySlices`. */
    planSlices: (key: string, grid: SheetGrid | null) =>
      unwrap(client.POST('/api/ingest/slices/plan', { body: { key, grid } })),
    applySlices: (key: string, grid: SheetGrid | null, token: string) =>
      unwrap(client.PUT('/api/ingest/slices', { body: { key, grid, token } })),
    ocr: (query: OcrQuery) => unwrap(client.GET('/api/ingest/ocr', {
      params: {
        query: { provider: query.provider ?? null, batch_id: query.batchId ?? null, reprocess: query.reprocess, limit: query.limit },
      },
    })),
    startOcr: (body: StartOcrIn) => unwrap(client.POST('/api/ingest/ocr', { body })),
    parse: (reprocess: boolean, limit: number) =>
      unwrap(client.GET('/api/ingest/parse', { params: { query: { reprocess, limit } } })),
    startParse: (body: StartParseIn) => unwrap(client.POST('/api/ingest/parse', { body })),
    archive: () => unwrap(client.GET('/api/ingest/archive')),
    startArchive: () => unwrap(client.POST('/api/ingest/archive')),
  },

  brands: {
    list: () => unwrap(client.GET('/api/brands')),
    create: (body: BrandIn) => unwrap(client.POST('/api/brands', { body })),
    update: (brandId: string, body: BrandIn) =>
      unwrap(client.PATCH('/api/brands/{brand_id}', { params: { path: { brand_id: brandId } }, body })),
    addPrefix: (brandId: string, prefix: string) =>
      unwrap(client.POST('/api/brands/{brand_id}/prefixes', { params: { path: { brand_id: brandId } }, body: { prefix } })),
    remove: (brandId: string) =>
      unwrap(client.DELETE('/api/brands/{brand_id}', { params: { path: { brand_id: brandId } } })),
    suggestions: (settings: SuggestionSettings) =>
      unwrap(client.GET('/api/brands/suggestions', { params: { query: {
        boundary_only: settings.boundaryOnly, max_length: settings.maxLength,
        min_length: settings.minLength, min_count: settings.minCount,
      } } })),
  },

  curate: {
    dedupe: () => unwrap(client.GET('/api/curate/dedupe')),
    tossDuplicate: (path: string) => unwrap(client.POST('/api/curate/dedupe/toss', { body: { path } })),
    keepBoth: (documents: string[]) => unwrap(client.POST('/api/curate/dedupe/keep', { body: { documents } })),
    restoreTossed: (paths: string[], verdict: Verdict) =>
      unwrap(client.POST('/api/curate/dedupe/restore', { body: { paths, verdict } })),
    considerAgain: (first: string, second: string) =>
      unwrap(client.DELETE('/api/curate/dedupe/keep', { params: { query: { first, second } } })),
    normalize: (engine?: string, threshold?: number) =>
      unwrap(client.GET('/api/curate/normalize', { params: { query: {
        engine: engine ?? null, threshold: threshold ?? null,
      } } })),
    previewMerge: (target: string, variants: string[]) =>
      unwrap(client.POST('/api/curate/normalize/preview', { body: { target, variants } })),
    merge: (target: string, variants: string[]) =>
      unwrap(client.POST('/api/curate/normalize/merge', { body: { target, variants } })),
    confirmDistinct: (names: string[]) =>
      unwrap(client.POST('/api/curate/normalize/distinct', { body: { names } })),
    forgetDistinct: (first: string, second: string) =>
      unwrap(client.DELETE('/api/curate/normalize/distinct', { params: { query: { first, second } } })),
  },

  workshop: {
    queue: (key?: string) => unwrap(client.GET('/api/curate/workshop', { params: { query: { key: key ?? null } } })),
    reprocess: (body: WorkshopReprocessIn) => unwrap(client.POST('/api/curate/workshop/reprocess', { body })),
    /** Drop a pending reread: the document goes back to what its sidecars say. */
    discardReread: (key: string) =>
      unwrap(client.DELETE('/api/curate/workshop/reread', { params: { query: { key } } })),
    decide: (body: WorkshopDecisionIn) => unwrap(client.POST('/api/curate/workshop/decide', { body })),
    hints: (key: string, draft: Draft) => unwrap(client.POST('/api/curate/workshop/hints', { body: { key, draft } })),
    /** The week around the form's date and time, and the document's batch. */
    context: (key: string, when: { date: string; time: string; document_type: string }) =>
      unwrap(client.GET('/api/curate/workshop/context', { params: { query: { key, ...when } } })),
  },

  dev: {
    sanity: () => unwrap(client.GET('/api/dev/sanity')),
    indexAudit: () => unwrap(client.GET('/api/dev/index-audit')),
    experiment: () => unwrap(client.GET('/api/dev/experiment')),
    run: (runId: string) => unwrap(client.GET('/api/dev/experiment/{run_id}', { params: { path: { run_id: runId } } })),
    /** Start a run from an image. OpenAPI types the file as a string ("binary"); the serializer sends the File. */
    upload: (file: File) => unwrap(client.POST('/api/dev/experiment', {
      body: { file: file.name },
      bodySerializer: () => {
        const form = new FormData()
        form.append('file', file)
        return form
      },
    })),
    prompt: (runId: string, customInstruction: string) => unwrap(client.POST('/api/dev/experiment/{run_id}/prompt', {
      params: { path: { run_id: runId } }, body: { custom_instruction: customInstruction },
    })),
    ocr: (runId: string, body: ExperimentOcrIn) =>
      unwrap(client.POST('/api/dev/experiment/{run_id}/ocr', { params: { path: { run_id: runId } }, body })),
    parse: (runId: string, body: ExperimentParseIn) =>
      unwrap(client.POST('/api/dev/experiment/{run_id}/parse', { params: { path: { run_id: runId } }, body })),
  },

  jobs: {
    list: () => unwrap(client.GET('/api/jobs')),
    cancel: (jobId: string) => unwrap(client.POST('/api/jobs/{job_id}/cancel', { params: { path: { job_id: jobId } } })),
  },
}

export type SuggestionSettings = {
  boundaryOnly: boolean
  maxLength: number
  minLength: number
  minCount: number
}

export type OcrQuery = { provider?: string | undefined; batchId?: number | undefined; reprocess: boolean; limit: number }

/** Server-Sent Events URL streaming a job's progress (each `data:` event is a JobOut). */
export const jobEventsUrl = (jobId: string): string => `/api/jobs/${encodeURIComponent(jobId)}/events`

const withQuery = (path: string, query: Record<string, string | number | boolean>): string =>
  `${path}?${new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)])).toString()}`

/** A marked scan as OCR would see it, with the workshop's treatment applied by the server. */
export const workshopScanUrl = (filename: string, enhancement: Record<string, string | number>): string =>
  withQuery('/api/curate/workshop/scan', { filename, ...enhancement })

/** An Experiment image with a treatment applied by the server: what OCR would read with these settings. */
export const experimentScanUrl = (runId: string, treatment: ExperimentTreatment): string =>
  withQuery(`/api/dev/experiment/${encodeURIComponent(runId)}/scan`, treatment)

/** The image the last Experiment OCR run read; `version` changes with each run. */
export const experimentSeenUrl = (runId: string, version: string): string =>
  withQuery(`/api/dev/experiment/${encodeURIComponent(runId)}/seen`, { v: version })

/** A scan in the input folder at full size; `version` (the file's mtime) busts the cache after a rotate,
 *  since the file keeps its name and the browser would otherwise show the copy it has. */
export const inputUrl = (filename: string, version: number): string =>
  `/api/media/input/${encodeURIComponent(filename)}?v=${version}`

/** A scan in the input folder, scaled down; `version` (the file's mtime) busts the cache after a rotate. */
export const inputThumbUrl = (filename: string, version: number, width = 360): string =>
  `/api/media/input-thumb/${encodeURIComponent(filename)}?width=${width}&v=${version}`

/** Archived media lives under YYYY/MM with spaces + unicode in the name. */
export const mediaUrl = (relPath: string | null | undefined): string | undefined =>
  relPath ? `/api/media/archived/${relPath.split('/').map(encodeURIComponent).join('/')}` : undefined
