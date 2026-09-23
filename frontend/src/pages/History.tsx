import { Fragment, useState, type ReactNode } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { HistoryChange, HistoryCommit } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { ago } from '../components/historyPip.tsx'
import { Card, ErrorState, Loading } from '../components/ui.tsx'
import { plural } from '../format.ts'
import '../components/history.css'
import './ingest.css'

/*
 * The Papertrail folder's history (archive_history): what waits uncommitted, the commits, and taking
 * either back. Milestones commit their own files as they happen; here everything else is committed by
 * hand. Uncommitting the last commit keeps its files as they are, uncommitted again; throwing changes away
 * (Discard) is a separate step, file by file or all at once.
 */

const PAGE = 50
/** Rows shown of a long file list (a first commit holds every scan); the rest are counted. */
const SHOWN = 500

function when(secondsAgo: number): string {
  return new Date(Date.now() - secondsAgo * 1000).toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/** Refetch everything the history shows: the sidebar's count, the changes, the commits and their files. */
function useRefresh() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['history'] })
}

function FileRows({ files, action }: { files: HistoryChange[]; action?: (file: HistoryChange) => ReactNode }) {
  return (
    <>
      <div className="table-wrap">
        <table className="fixed history-files">
          <colgroup>
            <col className="history-files__kind" /><col /><col className="history-files__lines" />
            {action && <col className="history-files__action" />}
          </colgroup>
          <tbody>
            {files.slice(0, SHOWN).map((f) => (
              <tr key={f.path}>
                <td><span className={`history-kind history-kind--${f.kind}`}>{f.kind}</span></td>
                <td title={f.path}>{f.path}</td>
                <td className="num history-lines">
                  {f.lines_added === null || f.lines_removed === null ? <span className="history-lines__binary">binary</span> : <>
                    <span className="history-lines__added">+{f.lines_added}</span>{' '}
                    <span className="history-lines__removed">−{f.lines_removed}</span>
                  </>}
                </td>
                {action && <td className="num">{action(f)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {files.length > SHOWN && <p className="ingest-note">…and {files.length - SHOWN} more.</p>}
    </>
  )
}

function Uncommitted({ starting }: { starting: boolean }) {
  const refresh = useRefresh()
  const changes = useQuery({ queryKey: ['history', 'changes'], queryFn: api.historyChanges, enabled: !starting })
  const [message, setMessage] = useState('')
  const [discarding, setDiscarding] = useState<HistoryChange[] | null>(null)
  const commit = useMutation({
    mutationFn: () => api.commitHistory(message),
    onSuccess: () => {
      setMessage('')
      void refresh()
    },
  })
  const discard = useMutation({
    mutationFn: (files: HistoryChange[]) => api.discardChanges(files.map((f) => f.path)),
    onSuccess: () => {
      setDiscarding(null)
      void refresh()
    },
  })

  const files = changes.data ?? []
  const count = starting ? null : files.length
  const back = discarding?.filter((f) => f.kind !== 'added').length ?? 0
  const gone = discarding?.filter((f) => f.kind === 'added').length ?? 0

  return (
    <Card title="Uncommitted" hint={count === null ? undefined : plural(count, 'file')}>
      {starting ? (
        <p className="ingest-note">
          The folder has no history yet. Starting it commits everything in it, the scans and the whole archive; on a
          large archive that takes a minute or two. File Index, Finalize, a job ending or Archive would start it too.
        </p>
      ) : changes.isPending ? <Loading what="changes" />
        : changes.error ? <ErrorState error={changes.error} />
          : files.length === 0 ? <p className="ingest-note">Everything is committed.</p>
            : <FileRows files={files} action={(f) => (
              <button type="button" className="history-row-action" disabled={discard.isPending}
                onClick={() => setDiscarding([f])}>Discard</button>
            )} />}
      {commit.error && <div className="error-banner" role="alert">{commit.error.message}</div>}
      <div className="start-bar">
        <input className="history__message" type="text" value={message} placeholder="Message (optional)"
          disabled={commit.isPending} onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (starting || files.length > 0)) commit.mutate() }} />
        <button type="button" className="primary" disabled={commit.isPending || (!starting && files.length === 0)}
          onClick={() => commit.mutate()}>
          {commit.isPending ? 'Committing…' : starting ? 'Start the history' : `Commit ${plural(files.length, 'file')}`}
        </button>
        <button type="button" className="danger-outline" disabled={starting || files.length === 0 || discard.isPending}
          onClick={() => setDiscarding(files)}>Discard all</button>
      </div>

      {discarding && (
        <ConfirmDialog title={discarding.length === 1 ? 'Discard this change?' : `Discard ${plural(discarding.length, 'change')}?`}
          confirmLabel="Discard" danger busy={discard.isPending}
          onConfirm={() => discard.mutate(discarding)} onCancel={() => { setDiscarding(null); discard.reset() }}>
          {discarding.length === 1 && <p className="history__path">{discarding[0]?.path}</p>}
          {back > 0 && <p>{discarding.length === 1 ? 'It goes' : `${plural(back, 'file')} go`} back to how the last
            commit has {discarding.length === 1 ? 'it' : 'them'}.</p>}
          {gone > 0 && <p><strong>{discarding.length === 1 ? 'It is new, and is deleted' : `${plural(gone, 'new file')} ${gone === 1 ? 'is' : 'are'} deleted`}:</strong> no
            commit holds {gone === 1 ? 'it' : 'them'}, so {gone === 1 ? 'it' : 'they'} can't be brought back.</p>}
          {discard.error && <div className="error-banner" role="alert">{discard.error.message}</div>}
        </ConfirmDialog>
      )}
    </Card>
  )
}

function CommitFiles({ sha }: { sha: string }) {
  const files = useQuery({ queryKey: ['history', 'commit', sha], queryFn: () => api.historyCommitFiles(sha), staleTime: Infinity })
  if (files.isPending) return <Loading what="files" />
  if (files.error) return <ErrorState error={files.error} />
  return <FileRows files={files.data} />
}

function Commits() {
  const refresh = useRefresh()
  const [limit, setLimit] = useState(PAGE)
  const [open, setOpen] = useState<string | null>(null)
  const [uncommitting, setUncommitting] = useState<HistoryCommit | null>(null)
  const log = useQuery({ queryKey: ['history', 'commits', limit], queryFn: () => api.historyCommits(limit),
    placeholderData: keepPreviousData })
  const uncommit = useMutation({
    mutationFn: (sha: string) => api.uncommit(sha),
    onSuccess: () => {
      setUncommitting(null)
      void refresh()
    },
    // refused because a milestone committed since, say: show the history as it is now
    onError: () => { void refresh() },
  })

  if (log.isPending) return <Loading what="history" />
  if (log.error) return <ErrorState error={log.error} />
  const { commits, more } = log.data

  return (
    <Card title="Commits" hint="newest first" className="card--uncapped">
      {commits.length === 0 ? <p className="ingest-note">No commits yet.</p> : (
        <div className="table-wrap history-commits">
          <table className="fixed">
            <colgroup><col /><col className="history-commits__when" /><col className="history-commits__files" /><col className="history-files__action" /></colgroup>
            <thead><tr><th>Commit</th><th>When</th><th className="num">Files</th><th /></tr></thead>
            <tbody>
              {commits.map((c, i) => (
                <Fragment key={c.sha}>
                  <tr className={`history-commit${open === c.sha ? ' history-commit--open' : ''}`}
                    onClick={() => setOpen(open === c.sha ? null : c.sha)}>
                    <td title={c.subject}><span className="history-commit__caret">{open === c.sha ? '▾' : '▸'}</span>{c.subject}
                      <code className="history-commit__sha">{c.sha}</code></td>
                    <td>{when(c.seconds_ago)} <span className="history-commit__ago">{ago(c.seconds_ago)}</span></td>
                    <td className="num">{c.files}</td>
                    <td className="num">
                      {i === 0 && !c.first && (
                        <button type="button" className="history-row-action" disabled={uncommit.isPending}
                          onClick={(e) => { e.stopPropagation(); setUncommitting(c) }}>Uncommit</button>
                      )}
                    </td>
                  </tr>
                  {open === c.sha && (
                    <tr className="history-commit__files"><td colSpan={4}><CommitFiles sha={c.sha} /></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more && (
        <div className="start-bar">
          <button type="button" disabled={log.isFetching} onClick={() => setLimit(limit + PAGE)}>
            {log.isFetching ? 'Loading…' : 'Show older'}
          </button>
        </div>
      )}

      {uncommitting && (
        <ConfirmDialog title="Uncommit the last commit?" confirmLabel="Uncommit" busy={uncommit.isPending}
          onConfirm={() => uncommit.mutate(uncommitting.sha)} onCancel={() => { setUncommitting(null); uncommit.reset() }}>
          <p>“{uncommitting.subject}” goes out of the history. {uncommitting.files === 1
            ? 'Its file stays as it is now, and waits as an uncommitted change again'
            : `Its ${uncommitting.files} files stay as they are now, and wait as uncommitted changes again`}: to commit
            differently, or to discard.</p>
          {uncommit.error && <div className="error-banner" role="alert">{uncommit.error.message}</div>}
        </ConfirmDialog>
      )}
    </Card>
  )
}

export default function History() {
  const status = useQuery({ queryKey: ['history'], queryFn: api.history, retry: false })
  if (status.isPending) return <Loading what="history" />
  if (status.error) return <ErrorState error={status.error} />
  const h = status.data
  return (
    <div className="ingest-page history-page">
      <h1>History</h1>
      <p className="page-sub">
        The Papertrail folder keeps its history in git: every scan and page, in every state it was in. File Index,
        Finalize, a job ending and Archive commit their own files as they happen; everything else waits here until
        it is committed by hand.
      </p>
      {h.problem ? <div className="error-banner" role="alert">{h.problem}</div> : (
        <>
          <Uncommitted starting={!h.repository} />
          <Commits />
        </>
      )}
    </div>
  )
}
