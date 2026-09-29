import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { api } from '../../api/client.ts'
import type { CurateCounts, PipelineCounts } from '../../api/types.ts'

/*
 * The sidebar's counts: how much waits at each ingest step, so the pipeline reads at a glance, and on
 * each curate page. Two cheap requests (papertrail/api/routers/ingest.py and curate.py, ``/counts``): the ingest one
 * under the 'ingest' key, so whatever refreshes the ingest pages refreshes it; the curate one is refreshed
 * by every archive edit (api/invalidate.ts) and by saved settings (api/config.ts, pages/settings/Config.tsx). Both
 * are asked again on each page change too.
 */

export type Stage = 'unindexed' | 'rotation' | 'ocr' | 'parse' | 'review' | 'archive'
  | 'workshop' | 'dedupe' | 'brands' | 'normalize'

export type SidebarCounts = { ingest: PipelineCounts | undefined; curate: CurateCounts | undefined }

export function useSidebarCounts(): SidebarCounts {
  const { pathname } = useLocation()
  const ingest = useQuery({ queryKey: ['ingest', 'counts'], queryFn: api.ingest.counts, retry: false })
  const curate = useQuery({ queryKey: ['curate', 'counts'], queryFn: () => api.curate.counts(), retry: false })
  const { refetch: refetchIngest } = ingest
  const { refetch: refetchCurate } = curate
  useEffect(() => { void refetchIngest(); void refetchCurate() }, [pathname, refetchIngest, refetchCurate])
  return { ingest: ingest.data, curate: curate.data }
}

/** How the Normalize count's threshold reads: percent similarity, or cosine distance. */
function normalizeSetting(engine: string, threshold: number): string {
  return engine === 'embedding' ? `embedding distance ≤ ${threshold}` : `≥ ${Math.round(threshold)}% similar`
}

/** What a stage's count is, and what it means in words; nothing until its counts have loaded. */
function describe({ ingest, curate }: SidebarCounts, stage: Stage): [number, string] | undefined {
  switch (stage) {
    case 'unindexed': return ingest && [ingest.unindexed, 'scans not in a batch yet']
    case 'rotation': {
      if (!ingest) return undefined
      // Fix Rotation's queue, as the page shows it
      return [ingest.rotation, ingest.rotation_checked ? 'scans to decide on Fix Rotation'
        : 'scans to decide on Fix Rotation (tilts only: turns are checked once it has the model)']
    }
    case 'ocr': return ingest && [ingest.ocr, 'pages left to read']
    case 'parse': return ingest && [ingest.parse, 'documents left to parse']
    case 'review': return ingest && [ingest.review, 'documents left to review']
    case 'archive': return ingest && [ingest.archive, 'documents ready to file']
    case 'workshop': return curate && [curate.workshop, 'marked documents to rework']
    case 'dedupe': return curate && [curate.dedupe, 'likely duplicates to check']
    case 'brands': return curate && [curate.brands,
      `prefixes shared by ${curate.brands_min_names}+ unbranded names (set in Config)`]
    case 'normalize': {
      if (!curate) return undefined
      const unembedded = curate.normalize_unembedded
        ? ` · ${curate.normalize_unembedded} new name(s) not embedded yet: open Normalize on Embedding to count them`
        : ''
      return [curate.normalize, `name groups to merge or rule apart, at ${
        normalizeSetting(curate.normalize_engine, curate.normalize_threshold)} (set in Config)${unembedded}`]
    }
  }
}

/** A stage's count, as a pill at the end of its nav link; nothing when nothing waits. */
export function StageCount({ counts, stage }: { counts: SidebarCounts; stage: Stage }) {
  const described = describe(counts, stage)
  if (!described || described[0] === 0) return null
  const [n, what] = described
  return <span className="nav-count" title={`${n} ${what}`}>{n > 999 ? '999+' : n}</span>
}
