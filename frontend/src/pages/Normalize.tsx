import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useConfig } from '../api/config.ts'
import { useDebounced } from '../components/useDebounced.ts'
import type { NameGroup } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { afterArchiveEdit } from '../api/invalidate.ts'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { num } from '../format.ts'
import './curate.css'

/**
 * Merchant names that are one shop written several ways. Merging rewrites the name everywhere it is
 * stored and re-files the documents, because the file name is built from the name — so the merge is
 * previewed first, and confirmed.
 */
export default function Normalize() {
  const config = useConfig()
  const [engine, setEngine] = useState<string | null>(null)
  const [threshold, setThreshold] = useState<number | null>(null)
  const chosenEngine = engine ?? config.data?.normalize_engine ?? 'string'
  const chosenThreshold = threshold ?? (chosenEngine === 'embedding'
    ? config.data?.normalize_embedding_threshold ?? 0.05
    : config.data?.normalize_string_similarity ?? 80)

  // Clustering runs over every name (and can ask Ollama for embeddings): wait for the drag to settle.
  const settledThreshold = useDebounced(chosenThreshold, 300)
  const clusters = useQuery({
    queryKey: ['curate', 'normalize', chosenEngine, settledThreshold],
    queryFn: () => api.curate.normalize(chosenEngine, settledThreshold),
    enabled: config.isSuccess,
    placeholderData: (previous) => previous,
  })

  if (config.isPending || clusters.isPending) return <Loading what="name clusters" />
  if (clusters.error) return <ErrorState error={clusters.error} />
  const data = clusters.data
  const embedding = chosenEngine === 'embedding'

  return (
    <div className="curate-page normalize-page">
      <h1>Normalize</h1>
      <p className="page-sub">Merge merchant names that are the same shop spelled differently.</p>

      <div className="controls">
        <div className="field">
          <label>Engine</label>
          <div className="segmented">
            {data.engines.map((e) => (
              <button key={e.id} className={chosenEngine === e.id ? 'on' : ''}
                onClick={() => { setEngine(e.id); setThreshold(null) }}>{e.label}</button>
            ))}
          </div>
        </div>
        <div className="field">
          <label htmlFor="nm-threshold">
            {embedding ? 'Distance threshold' : 'String similarity'} <strong>
              {embedding ? chosenThreshold.toFixed(4) : `${Math.round(chosenThreshold)}%`}
            </strong>
          </label>
          <input id="nm-threshold" type="range"
            min={embedding ? 0.0025 : 50} max={embedding ? 0.25 : 100} step={embedding ? 0.0025 : 1}
            value={chosenThreshold} onChange={(e) => setThreshold(Number(e.target.value))} />
        </div>
      </div>

      <div className="tiles">
        <Tile label="Names in use" value={num(data.names)} />
        <Tile label="Clusters" value={num(data.groups.length)} />
        <Tile label="Ruled different" value={num(data.distinct_pairs.length)} />
      </div>

      {data.groups.length === 0
        ? <Empty>No name clusters at this setting. Loosen the threshold to see more.</Empty>
        : data.groups.map((group) => <Cluster key={JSON.stringify(group.names)} group={group} />)}

      {data.distinct_pairs.length > 0 && <DistinctPairs pairs={data.distinct_pairs} />}
    </div>
  )
}

function Cluster({ group }: { group: NameGroup }) {
  const queryClient = useQueryClient()
  const [checked, setChecked] = useState<string[]>(group.names)
  const [target, setTarget] = useState(group.canonical[0] ?? group.names[0] ?? '')
  const [confirming, setConfirming] = useState(false)
  const variants = checked.filter((name) => name !== target)
  const refresh = () => afterArchiveEdit(queryClient, ['curate', 'normalize'])

  const preview = useQuery({
    queryKey: ['curate', 'normalize', 'preview', target, variants],
    queryFn: () => api.curate.previewMerge(target, variants),
    enabled: confirming && variants.length > 0,
  })
  const merge = useMutation({
    mutationFn: () => api.curate.merge(target, variants),
    onSuccess: () => {
      setConfirming(false)
      void refresh()
    },
  })
  const distinct = useMutation({ mutationFn: () => api.curate.confirmDistinct(group.names), onSuccess: refresh })
  const count = (name: string) => group.counts.find((c) => c.name === name)?.count ?? 0

  return (
    <Card title={`${group.names.length} similar names`} hint={`${group.names.reduce((n, name) => n + count(name), 0)} document(s)`}>
      <div className="name-list">
        {group.names.map((name) => (
          <label key={name} className="name-row">
            <input type="checkbox" checked={checked.includes(name)}
              onChange={(e) => setChecked(e.target.checked ? [...checked, name] : checked.filter((n) => n !== name))} />
            <span className="name-row__name">{name}</span>
            <span className="config-hint">
              {count(name) ? `${num(count(name))} document(s)` : 'no documents'}
              {group.canonical.includes(name) ? ' · already a merge target' : ''}
            </span>
            <button className={target === name ? 'on' : ''} disabled={!checked.includes(name)}
              onClick={(e) => { e.preventDefault(); setTarget(name) }}>
              {target === name ? 'Keeping this one' : 'Keep this one'}
            </button>
          </label>
        ))}
      </div>

      {(merge.error || distinct.error) && (
        <div className="error-banner" role="alert">{(merge.error ?? distinct.error)?.message}</div>
      )}

      <div className="start-bar">
        <button className="primary" disabled={variants.length === 0 || merge.isPending}
          onClick={() => setConfirming(true)}>
          Merge {variants.length} into “{target}”
        </button>
        <button disabled={distinct.isPending} onClick={() => distinct.mutate()}>These are all different</button>
      </div>

      {confirming && (
        <ConfirmDialog title="Merge these names?" confirmLabel="Merge" danger
          busy={merge.isPending || preview.isPending}
          onConfirm={() => merge.mutate()} onCancel={() => setConfirming(false)}>
          {preview.isPending ? 'Working out what this would change…'
            : preview.error ? `Couldn't preview: ${preview.error.message}`
              : preview.data?.error ? preview.data.error
                : (
                  <>
                    <p>
                      {variants.map((name) => `“${name}”`).join(', ')} become <strong>“{target}”</strong> everywhere:
                      {' '}{num(preview.data?.documents ?? 0)} archived document(s),
                      {' '}{num(preview.data?.decisions ?? 0)} pending review(s) and
                      {' '}{num(preview.data?.smart_matches ?? 0)} smart-match entr(ies).
                    </p>
                    <p>{num(preview.data?.moves.length ?? 0)} file(s) are renamed on disk, since the name is part
                      of the filename. There is no undo.</p>
                    {preview.data?.moves.length ? (
                      <ul className="file-list">
                        {preview.data.moves.slice(0, 8).map((move) => <li key={move.source}>{move.destination}</li>)}
                        {preview.data.moves.length > 8 && <li>… and {preview.data.moves.length - 8} more</li>}
                      </ul>
                    ) : null}
                  </>
                )}
        </ConfirmDialog>
      )}
    </Card>
  )
}

function DistinctPairs({ pairs }: { pairs: string[][] }) {
  const queryClient = useQueryClient()
  const forget = useMutation({
    mutationFn: ([first, second]: string[]) => api.curate.forgetDistinct(first ?? '', second ?? ''),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['curate', 'normalize'] }),
  })
  return (
    <Card title="Ruled different" hint={`${pairs.length} pair(s)`}>
      <div className="name-list">
        {pairs.map((pair) => (
          <div key={pair.join('|')} className="name-row">
            <span className="name-row__name">{pair[0]} ↔ {pair[1]}</span>
            <button disabled={forget.isPending} onClick={() => forget.mutate(pair)}>Consider again</button>
          </div>
        ))}
      </div>
    </Card>
  )
}
