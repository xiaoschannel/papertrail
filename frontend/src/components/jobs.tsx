import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, jobEventsUrl } from '../api/client.ts'
import type { Job } from '../api/types.ts'
import './jobs.css'

/**
 * Background jobs (OCR, Parse, Archive). The server runs one at a time; `JobWatcher` (mounted once in
 * the app shell) follows the running job over Server-Sent Events and writes each update into the query
 * cache, so every page and the sidebar read the same live job without opening their own streams.
 */
export const CURRENT_JOB = ['job', 'current'] as const

export function useCurrentJob(): Job | null | undefined {
  return useQuery({
    queryKey: CURRENT_JOB,
    queryFn: api.jobs.current,
    // While a job runs, the watcher's event stream keeps this current. Otherwise poll gently, so a job
    // started in another tab still shows up here (and its pages lock) without waiting for a refused request.
    refetchInterval: (query) => (query.state.data?.status === 'running' ? false : 10_000),
    refetchOnWindowFocus: true,
    staleTime: 0,
  }).data
}

/** Put a job the page just started into the cache, so the watcher starts following it. */
export function useTrackJob() {
  const queryClient = useQueryClient()
  return (job: Job) => queryClient.setQueryData(CURRENT_JOB, job)
}

export function JobWatcher() {
  const queryClient = useQueryClient()
  const job = useCurrentJob()
  const runningId = job?.status === 'running' ? job.id : null

  useEffect(() => {
    if (!runningId) return undefined
    const source = new EventSource(jobEventsUrl(runningId))
    source.onmessage = (event: MessageEvent<string>) => {
      const next = JSON.parse(event.data) as Job
      queryClient.setQueryData(CURRENT_JOB, next)
      if (next.status !== 'running') {
        source.close()
        // OCR/Parse/Archive changed what every ingest page (and, after Archive, the visualize pages) shows.
        void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'job' })
      }
    }
    source.onerror = () => {
      // EventSource retries dropped connections by itself; once it gives up (e.g. the server restarted
      // and forgot the job), ask the server what is running now.
      if (source.readyState === EventSource.CLOSED) void queryClient.invalidateQueries({ queryKey: CURRENT_JOB })
    }
    return () => source.close()
  }, [runningId, queryClient])

  return null
}

/** Sidebar line for the running job, so it stays visible from any page. */
export function JobIndicator() {
  const job = useCurrentJob()
  if (job?.status !== 'running') return null
  return (
    <div className="job-indicator" title={job.message}>
      <span className="job-indicator__dot" />
      {job.title} · {job.done}/{job.total || '…'}
    </div>
  )
}

/** Whether a page of `kind` may start its job, and the job it should show. */
export function useJobGate(kind: string) {
  const job = useCurrentJob()
  const busy = job?.status === 'running'
  return {
    busy,
    /** The running or last job of this page's kind (to show its progress/result). */
    job: job && job.kind === kind ? job : null,
    blockedBy: busy && job.kind !== kind ? job.title : null,
  }
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
      </div>
      {job.message && <p className={`job-message${job.status === 'failed' ? ' neg' : ''}`}>{job.message}</p>}
      {cancel.error && <div className="error-banner" role="alert">{cancel.error.message}</div>}
      <div className="job-actions">
        {running && (
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
