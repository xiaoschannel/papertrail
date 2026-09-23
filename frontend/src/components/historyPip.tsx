import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client.ts'
import { ConfirmDialog } from './ConfirmDialog.tsx'
import './history.css'

/*
 * The folder's history, at the foot of the sidebar: how many files changed since the last commit, and
 * when that was. Commits happen at milestones (File Index, Slice, Group, a job ending, Archive) with only
 * what each produced; everything else waits here until the Commit button, which commits it all. Asked
 * again on every page change and every half minute, and after a commit.
 */

const REFRESH_MS = 30_000

function ago(seconds: number): string {
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

export function HistoryPip() {
  const { pathname } = useLocation()
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['history'], queryFn: api.history, retry: false, refetchInterval: REFRESH_MS })
  const { refetch } = status
  useEffect(() => { void refetch() }, [pathname, refetch])
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const commit = useMutation({
    mutationFn: () => api.commitHistory(message),
    onSuccess: (fresh) => {
      queryClient.setQueryData(['history'], fresh)
      setOpen(false)
      setMessage('')
    },
  })

  const h = status.data
  if (!h) return null   // no folder set yet (Config says so), or the API is away
  const label = h.problem ? 'History unavailable' : !h.repository ? 'No history yet'
    : h.changed === 0 ? 'All committed' : `${h.changed} uncommitted`
  const detail = h.problem ?? (h.last ? `Last commit ${ago(h.last.seconds_ago)}: ${h.last.subject}` : 'No commits yet')

  return (
    <div className={`history${h.changed > 0 ? ' history--waiting' : ''}`} title={detail}>
      <button type="button" className="history__button" disabled={h.changed === 0 || Boolean(h.problem)}
        onClick={() => setOpen(true)}>
        <span className="history__dot" />
        {label}
      </button>
      <small className="history__last">{h.last ? `last commit ${ago(h.last.seconds_ago)}` : detail}</small>

      {open && (
        <ConfirmDialog title="Commit the folder?" confirmLabel="Commit" busy={commit.isPending}
          onConfirm={() => commit.mutate()} onCancel={() => setOpen(false)}>
          <p>
            {h.changed} changed file{h.changed === 1 ? '' : 's'} (edits, decisions, new scans) go into the
            folder's history as one commit.
          </p>
          <input className="history__message" type="text" value={message} placeholder="Message (optional)"
            autoFocus onChange={(e) => setMessage(e.target.value)} />
          {commit.error && <div className="error-banner" role="alert">{commit.error.message}</div>}
        </ConfirmDialog>
      )}
    </div>
  )
}
