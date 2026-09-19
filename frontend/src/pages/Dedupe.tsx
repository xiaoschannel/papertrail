import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl } from '../api/client.ts'
import type { DedupeCluster, DedupeMember, Verdict } from '../api/types.ts'
import { afterArchiveEdit } from '../api/invalidate.ts'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { money, num } from '../format.ts'
import './curate.css'

/**
 * The same purchase scanned twice: documents with an identical cost minutes apart.
 *
 * Deciding means comparing two scans, so the scans are sized from the height available (a receipt is
 * about 1:3, so height binds) and clusters tile across whatever width there is — a wider window shows
 * more clusters, not bigger buttons to travel to. Each action sits under the scan it acts on.
 */
const DEDUPE = ['curate', 'dedupe'] as const
const DECIDE = ['curate', 'dedupe', 'decide'] as const
const clusterId = (cluster: DedupeCluster) => cluster.members.map((m) => m.filename).join('|')

export default function Dedupe() {
  const queryClient = useQueryClient()
  const [undoable, setUndoable] = useState<{ paths: string[]; verdict: Verdict; name: string } | null>(null)
  const clusters = useQuery({ queryKey: DEDUPE, queryFn: api.curate.dedupe })
  const [working, setWorking] = useState<string | null>(null)   // the cluster being acted on, if any

  /**
   * Clusters already decided in this session. Deciding is faster than the server can answer — it
   * rebuilds the whole archive frame per edit — so a refetch that started before your last click
   * would otherwise arrive later and put those clusters back on screen.
   */
  const decided = useRef(new Set<string>())
  const settled = clusters.data
  useEffect(() => {
    // Once the server stops reporting a cluster, there is nothing left to hide.
    const present = new Set((settled?.clusters ?? []).map(clusterId))
    decided.current = new Set([...decided.current].filter((id) => present.has(id)))
  }, [settled])

  /** Refetch only when the last decision has landed, so answers can't overtake each other. */
  const refreshWhenIdle = () => {
    if (queryClient.isMutating({ mutationKey: DECIDE }) <= 1) afterArchiveEdit(queryClient, DEDUPE)
  }

  const decide = async (id: string) => {
    setWorking(id)
    decided.current.add(id)
    await queryClient.cancelQueries({ queryKey: DEDUPE })   // an in-flight answer is already stale
  }

  const toss = useMutation({
    mutationKey: DECIDE,
    mutationFn: (member: DedupeMember) => api.curate.tossDuplicate(member.path),
    onSuccess: (result) => {
      setUndoable({ paths: result.tossed, verdict: result.previous_verdict, name: result.name })
      setWorking(null)
    },
    onSettled: refreshWhenIdle,
  })
  const restore = useMutation({
    mutationFn: () => api.curate.restoreTossed(undoable?.paths ?? [], undoable?.verdict ?? 'accepted'),
    onSuccess: () => {
      setUndoable(null)
      decided.current = new Set()      // it is a candidate again, so stop hiding it
      afterArchiveEdit(queryClient, DEDUPE)
    },
  })
  const keep = useMutation({
    mutationKey: DECIDE,
    mutationFn: (documents: string[]) => api.curate.keepBoth(documents),
    onSuccess: () => setWorking(null),
    onSettled: refreshWhenIdle,
  })

  if (clusters.isPending) return <Loading what="duplicates" />
  if (clusters.error) return <ErrorState error={clusters.error} />
  const { archived, tossed } = clusters.data
  const groups = clusters.data.clusters.filter((cluster) => !decided.current.has(clusterId(cluster)))
  // An API process older than this page (it is long-running; the page hot-reloads) has no `kept`.
  // Degrade to "none recorded" rather than blanking the page on a field that isn't there yet.
  const kept = clusters.data.kept ?? []

  return (
    <div className="curate-page dedupe-page">
      <h1>Dedupe</h1>
      <p className="page-sub">Documents that look like one purchase scanned twice — same amount, minutes apart.</p>

      <div className="tiles">
        <Tile label="Archived" value={num(archived)} />
        <Tile label="Already tossed" value={num(tossed)} />
        <Tile label="To decide" value={num(groups.length)} />
        <Tile label="Kept as different" value={num(kept.length)} />
      </div>

      {(toss.error || keep.error || restore.error) && (
        <div className="error-banner" role="alert">{(toss.error ?? keep.error ?? restore.error)?.message}</div>
      )}

      {groups.length === 0 ? <Empty>No likely duplicates in the archive.</Empty> : (
        <div className="dupe-clusters">
          {groups.map((cluster) => (
            <ClusterPanel key={clusterId(cluster)} cluster={cluster}
              busy={working === clusterId(cluster) && (toss.isPending || keep.isPending)}
              onToss={async (member) => {
                await decide(clusterId(cluster))
                toss.mutate(member)
              }}
              onKeep={async () => {
                await decide(clusterId(cluster))
                keep.mutate(cluster.members.map((m) => m.filename))
              }} />
          ))}
        </div>
      )}

      {kept.length > 0 && <KeptPairs pairs={kept} onDone={() => {
        decided.current = new Set()      // considering a pair again should bring its cluster back
        afterArchiveEdit(queryClient, DEDUPE)
      }} />}

      {undoable && (
        <div className="toast" role="status">
          Tossed <strong>{undoable.name}</strong>
          <button disabled={restore.isPending} onClick={() => restore.mutate()}>Undo</button>
          <button onClick={() => setUndoable(null)}>Dismiss</button>
        </div>
      )}

    </div>
  )
}

/** One cluster: its scans side by side at full height, each with the action that applies to it. */
function ClusterPanel({ cluster, busy, onToss, onKeep }: {
  cluster: DedupeCluster
  busy: boolean
  onToss: (member: DedupeMember) => void
  onKeep: () => void
}) {
  const first = cluster.members[0]
  return (
    <section className="dupe-cluster" style={{ '--members': cluster.members.length } as CSSProperties}>
      <header>
        <strong>{cluster.date || 'undated'}</strong>
        <span className="config-hint">
          {first ? money(first.cost, first.currency) : ''} · {cluster.members.length} documents
        </span>
        <button disabled={busy} onClick={onKeep}>Not duplicates</button>
      </header>

      <div className="dupe-scans">
        {cluster.members.map((member) => (
          <figure key={member.filename}>
            {/* The whole scan, never cropped: on a receipt the total is at the bottom. */}
            <Link to={`/receipt?file=${encodeURIComponent(member.filename)}`} title="Open this document">
              <img src={mediaUrl(member.path)} alt={member.name || member.filename} loading="lazy" />
            </Link>
            <figcaption>
              <span className="dupe-name" title={member.name}>{member.name || '(untitled)'}</span>
              <span className="config-hint">
                {member.time || 'no time'}{member.pages > 1 ? ` · ${member.pages} pages` : ''}
              </span>
            </figcaption>
            <button className="danger-outline" disabled={busy} onClick={() => onToss(member)}>Toss this one</button>
          </figure>
        ))}
      </div>
    </section>
  )
}

function KeptPairs({ pairs, onDone }: { pairs: { documents: string[]; names: string[] }[]; onDone: () => void }) {
  const consider = useMutation({
    mutationFn: ([first, second]: string[]) => api.curate.considerAgain(first ?? '', second ?? ''),
    onSuccess: onDone,
  })
  return (
    <Card title="Kept as different" hint={`${pairs.length} pair(s)`}>
      <div className="name-list">
        {pairs.map((pair) => (
          <div key={pair.documents.join('|')} className="name-row">
            <span className="name-row__name">{pair.names.join(' ↔ ') || pair.documents.join(' ↔ ')}</span>
            <button disabled={consider.isPending} onClick={() => consider.mutate(pair.documents)}>
              Consider again
            </button>
          </div>
        ))}
      </div>
    </Card>
  )
}
