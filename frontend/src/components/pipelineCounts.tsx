import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client.ts'
import type { PipelineCounts } from '../api/types.ts'

/*
 * The sidebar's counts: how much waits at each ingest step, so the pipeline reads at a glance. One cheap
 * request (api/routers/ingest.py, ``/counts``) under the 'ingest' key, so whatever refreshes the ingest
 * pages refreshes these; it is asked again on each page change too.
 */

export type Stage = 'unindexed' | 'rotation' | 'ocr' | 'parse' | 'review' | 'archive'

export function usePipelineCounts() {
  const { pathname } = useLocation()
  const query = useQuery({ queryKey: ['ingest', 'counts'], queryFn: api.ingest.counts, retry: false })
  const { refetch } = query
  useEffect(() => { void refetch() }, [pathname, refetch])
  return query.data
}

/** What a stage's count is, and what it means in words. */
function describe(counts: PipelineCounts, stage: Stage): [number, string] {
  switch (stage) {
    case 'unindexed': return [counts.unindexed, 'scans not in a batch yet']
    case 'rotation': {
      // Fix Rotation's queue, as the page shows it
      const n = counts.rotation
      return [n, counts.rotation_checked ? 'scans to decide on Fix Rotation'
        : 'scans to decide on Fix Rotation (tilts only: turns are checked once it has the model)']
    }
    case 'ocr': return [counts.ocr, 'pages left to read']
    case 'parse': return [counts.parse, 'documents left to parse']
    case 'review': return [counts.review, 'documents left to review']
    case 'archive': return [counts.archive, 'documents ready to file']
  }
}

/** A stage's count, as a pill at the end of its nav link; nothing when nothing waits. */
export function StageCount({ counts, stage }: { counts: PipelineCounts | undefined; stage: Stage }) {
  if (!counts) return null
  const [n, what] = describe(counts, stage)
  if (n === 0) return null
  return <span className="nav-count" title={`${n} ${what}`}>{n > 999 ? '999+' : n}</span>
}
