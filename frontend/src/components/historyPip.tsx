import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../api/client.ts'
import './history.css'

/*
 * The folder's history, at the foot of the sidebar: how many files changed since the last commit, and
 * when that was. Commits happen at milestones (File Index, Finalize, a job ending, Archive) with only
 * what each produced; everything else waits until it is committed on the History page, which the pill
 * opens. Asked again on every page change and every half minute, and after anything the page does.
 */

const REFRESH_MS = 30_000

export function ago(seconds: number): string {
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

export function HistoryPip() {
  const { pathname } = useLocation()
  const status = useQuery({ queryKey: ['history'], queryFn: api.history, retry: false, refetchInterval: REFRESH_MS })
  const { refetch } = status
  useEffect(() => { void refetch() }, [pathname, refetch])

  const h = status.data
  if (!h) return null   // no folder set yet (Config says so), or the API is away
  // Before the first commit there is nothing to count against: the page starts the history.
  const starting = !h.repository && !h.problem
  const label = h.problem ? 'History unavailable' : starting ? 'No history yet: start it'
    : h.changed === 0 ? 'All committed' : `${h.changed} uncommitted`
  const detail = h.problem ?? (h.last ? `Last commit ${ago(h.last.seconds_ago)}: ${h.last.subject}` : 'No commits yet')

  return (
    <div className={`history${h.changed > 0 || starting ? ' history--waiting' : ''}`} title={detail}>
      <NavLink to="/history" className={({ isActive }) => `history__button${isActive ? ' history__button--active' : ''}`}>
        <span className="history__dot" />
        {label}
      </NavLink>
      <small className="history__last">{h.last ? `last commit ${ago(h.last.seconds_ago)}` : detail}</small>
    </div>
  )
}
