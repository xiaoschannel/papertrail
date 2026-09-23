import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, jobEventsUrl } from '../api/client.ts'
import type { Job } from '../api/types.ts'
import { spend, spendExactly } from '../format.ts'
import './jobs.css'

/**
 * Background jobs (OCR, Parse, Archive, Workshop reprocess). Several can run at once — each holds the
 * batches it works on, the GPU if it loads a model, or everything (Archive) — so OCR can read one batch
 * while Parse extracts another. `JobWatcher` (mounted once in the app shell) follows every running job
 * over Server-Sent Events and writes each update into the query cache, so pages and the sidebar read the
 * same live jobs without opening their own streams.
 */
export const JOBS = ['job', 'list'] as const

/** Every running job, plus the last finished one of each kind with none running. */
export function useJobs(): Job[] {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: JOBS,
    // A job that ended between two reads of the list (seen here first, not on its stream) ends here too.
    queryFn: async () => {
      const before = queryClient.getQueryData<Job[]>(JOBS) ?? []
      const jobs = await api.jobs.list()
      if (before.some((was) => isRunning(was) && jobs.some((job) => job.id === was.id && !isRunning(job)))) {
        queueMicrotask(() => jobEnded(queryClient))
      }
      return jobs
    },
    // While jobs run, the watcher's streams keep this current. Otherwise poll gently, so a job started in
    // another tab still shows up here (and its locks apply) without waiting for a refused request.
    refetchInterval: (query) => ((query.state.data ?? []).some(isRunning) ? false : 10_000),
    refetchOnWindowFocus: true,
    staleTime: 0,
  }).data ?? []
}

const isRunning = (job: Job) => job.status === 'running'

function upsert(queryClient: QueryClient, job: Job) {
  const was = queryClient.getQueryData<Job[]>(JOBS)?.find((j) => j.id === job.id)
  queryClient.setQueryData<Job[]>(JOBS, (jobs = []) =>
    jobs.some((j) => j.id === job.id) ? jobs.map((j) => (j.id === job.id ? job : j))
      // a new run replaces the last finished one of its kind
      : [...jobs.filter((j) => j.kind !== job.kind || isRunning(j)), job])
  if (was && isRunning(was) && !isRunning(job)) jobEnded(queryClient)
}

/** A job just ended: OCR/Parse/Archive changed what the ingest pages (and, after Archive, the visualize
 *  pages) show, so everything but the job list re-reads. */
function jobEnded(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'job' })
}

/** Put a job the page just started (or cancelled) into the cache, so the watcher follows it. */
export function useTrackJob() {
  const queryClient = useQueryClient()
  return (job: Job) => upsert(queryClient, job)
}

export function JobWatcher() {
  const running = useJobs().filter(isRunning)
  return <>{running.map((job) => <JobStream key={job.id} id={job.id} />)}</>
}

/** One running job's event stream, written into the job list as it changes. */
function JobStream({ id }: { id: string }) {
  const queryClient = useQueryClient()
  useEffect(() => {
    const source = new EventSource(jobEventsUrl(id))
    source.onmessage = (event: MessageEvent<string>) => {
      const next = JSON.parse(event.data) as Job
      upsert(queryClient, next)
      if (next.status !== 'running') source.close()
    }
    source.onerror = () => {
      // EventSource retries dropped connections by itself; once it gives up (e.g. the server restarted
      // and forgot the job), ask the server what is running now.
      if (source.readyState === EventSource.CLOSED) void queryClient.invalidateQueries({ queryKey: JOBS })
    }
    return () => source.close()
  }, [id, queryClient])
  return null
}

/** Sidebar lines for the running jobs, so they stay visible from any page. */
export function JobIndicator() {
  const running = useJobs().filter(isRunning)
  return (
    <>
      {running.map((job) => (
        <div key={job.id} className="job-indicator" title={job.message}>
          <span className="job-indicator__dot" />
          {job.title} · {job.done}/{job.total || '…'}
        </div>
      ))}
    </>
  )
}

/** What a page's job would hold: the GPU if it loads a model, everything if it's Archive. */
export type JobNeeds = { gpu?: boolean; everything?: boolean }

/**
 * Whether a page of `kind` may start its job, and the job it should show.
 *
 * Batches aren't known until the server plans the run (it plans around batches other jobs hold), so
 * this only predicts the GPU and everything-collisions; the server stays the authority and says why
 * when it refuses.
 */
export function useJobGate(kind: string, needs: JobNeeds = {}) {
  const jobs = useJobs()
  const own = jobs.find((job) => job.kind === kind && isRunning(job)) ?? jobs.findLast((job) => job.kind === kind)
  const blocker = jobs.find((job) => isRunning(job) && job.kind !== kind
    && (job.everything || needs.everything || (needs.gpu && job.gpu)))
  return {
    /** The running or last job of this page's kind (to show its progress/result). */
    job: own ?? null,
    blockedBy: blocker ? blocker.title : null,
  }
}

/** The running job holding ``batchId`` (or everything), which locks edits to that batch's pages. */
export function useBatchHolder(batchId: number | null): Job | null {
  return useJobs().find((job) => isRunning(job)
    && (job.everything || (batchId !== null && job.batches.includes(batchId)))) ?? null
}

/** Which running job holds a batch (or everything), for a page showing several batches at once. */
export function useHolderOf(): (batchId: number) => Job | null {
  const running = useJobs().filter(isRunning)
  return (batchId) => running.find((job) => job.everything || job.batches.includes(batchId)) ?? null
}

/** The running job holding everything (Archive), which locks every ingest edit. */
export function useEverythingHolder(): Job | null {
  return useJobs().find((job) => isRunning(job) && job.everything) ?? null
}

const duration = (seconds: number | null | undefined): string => {
  if (seconds == null) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

const STATUS_LABEL: Record<Job['status'], string> = {
  running: 'Running', succeeded: 'Done', failed: 'Failed', cancelled: 'Cancelled',
}

/** Every number behind the spend, under the provider's own names for them. */
function TokenBreakdown({ job }: { job: Job }) {
  const per = (total: number) => (job.calls ? Math.round(total / job.calls).toLocaleString() : '—')
  const rows: [string, string, string][] = [
    ['prompt_tokens', job.prompt_tokens.toLocaleString(), per(job.prompt_tokens)],
    ['cached_tokens', job.cached_tokens.toLocaleString(), per(job.cached_tokens)],
    ['completion_tokens', job.completion_tokens.toLocaleString(), per(job.completion_tokens)],
    ['reasoning_tokens', job.thinking_tokens.toLocaleString(), per(job.thinking_tokens)],
  ]
  return (
    <details className="job-tokens">
      <summary>Tokens ({job.calls.toLocaleString()} call{job.calls === 1 ? '' : 's'})</summary>
      <table>
        <thead><tr><th /><th className="num">Total</th><th className="num">Per document</th></tr></thead>
        <tbody>
          {rows.map(([label, total, each]) => (
            <tr key={label}><th>{label}</th><td className="num">{total}</td><td className="num">{each}</td></tr>
          ))}
          <tr><th>spent</th><td className="num">{spendExactly(job.spent)}</td>
            <td className="num">{spendExactly(job.spent / (job.calls || 1))}</td></tr>
        </tbody>
      </table>
    </details>
  )
}

export function JobPanel({ job }: { job: Job }) {
  const track = useTrackJob()
  const cancel = useMutation({ mutationFn: () => api.jobs.cancel(job.id), onSuccess: track })
  const [showErrors, setShowErrors] = useState(false)
  const percent = job.total ? Math.round((job.done / job.total) * 100) : 0
  const running = job.status === 'running'

  return (
    <section className={`card job-panel job-panel--${job.status}`} aria-live="polite">
      <div className="card-head">
        <h2>{job.title}</h2>
        <span className="job-status">{STATUS_LABEL[job.status]}</span>
      </div>
      <div className="job-bar" role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}>
        <div style={{ width: `${percent}%` }} />
      </div>
      <div className="job-stats">
        <span><strong>{job.done}</strong> / {job.total || '…'}</span>
        {job.failed > 0 && <span className="neg">{job.failed} failed</span>}
        <span>{duration(job.elapsed_seconds)} elapsed</span>
        {job.seconds_per_item != null && <span>{job.seconds_per_item.toFixed(1)} s/item</span>}
        {running && job.eta_seconds != null && <span>~{duration(job.eta_seconds)} left</span>}
        {job.spent > 0 && (
          // A model that bills by the token: what the run has spent, what all of it comes to at that
          // rate, and how much of the prompt is being charged a tenth. Only Parse on a hosted model does.
          <span>
            {spend(job.spent)} spent
            {running && job.done > 0 && job.total > job.done && ` · ${spend(job.spent / job.done * job.total)} for all ${job.total}`}
            {job.prompt_tokens > 0 && ` · ${Math.round((job.cached_tokens / job.prompt_tokens) * 100)}% cached`}
          </span>
        )}
      </div>
      {job.calls > 0 && <TokenBreakdown job={job} />}
      {job.message && <p className={`job-message${job.status === 'failed' ? ' neg' : ''}`}>{job.message}</p>}
      {cancel.error && <div className="error-banner" role="alert">{cancel.error.message}</div>}
      <div className="job-actions">
        {running && job.cancellable && (
          <button className="danger-outline" disabled={job.cancel_requested || cancel.isPending} onClick={() => cancel.mutate()}>
            {job.cancel_requested ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
        {job.errors.length > 0 && (
          <button onClick={() => setShowErrors((v) => !v)}>
            {showErrors ? 'Hide' : 'Show'} errors ({job.errors.length}{job.failed > job.errors.length ? ` of ${job.failed}` : ''})
          </button>
        )}
      </div>
      {showErrors && (
        <ul className="job-errors">
          {job.errors.map((e, i) => (
            <li key={`${e.item}-${i}`}><strong>{e.item}</strong> <pre>{e.error}</pre></li>
          ))}
        </ul>
      )}
    </section>
  )
}
