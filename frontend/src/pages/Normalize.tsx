import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
import { useDebounced } from '../components/useDebounced.ts'
import type { AppConfig, NameGroup } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { afterArchiveEdit } from '../api/invalidate.ts'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { num } from '../format.ts'
import './curate.css'

/** A threshold dragged on one engine: the two measure in different units (embedding distance, percent
 * similarity), so a value is only ever used, or saved, with the engine it was dragged on. */
type Drag = { engine: string; value: number }

const thresholdSetting = (drag: Drag): Partial<AppConfig> => drag.engine === 'embedding'
  ? { normalize_embedding_threshold: drag.value } : { normalize_string_similarity: drag.value }

/**
 * Merchant names that are one shop written several ways. Merging rewrites the name everywhere it is
 * stored and re-files the documents, because the file name is built from the name — so the merge is
 * previewed first, and confirmed.
 */
export default function Normalize() {
  const config = useConfig()
  const { mutate: saveConfig } = useSaveConfig()
  const [engine, setEngine] = useState<string | null>(null)
  const [merged, setMerged] = useState<string | null>(null)
  const [dragged, setDragged] = useState<Drag | null>(null)
  const chosenEngine = engine ?? config.data?.normalize_engine ?? 'string'
  const thresholdOf = (drag: Drag | null) => drag?.engine === chosenEngine ? drag.value
    : chosenEngine === 'embedding' ? config.data?.normalize_embedding_threshold ?? 0.05
      : config.data?.normalize_string_similarity ?? 80
  const chosenThreshold = thresholdOf(dragged)

  // Clustering runs over every name (and can ask Ollama for embeddings): wait for the drag to settle.
  const settled = useDebounced(dragged, 300)
  const settledThreshold = thresholdOf(settled)

  // The page opens where you left it: the engine is saved when picked, a threshold once its drag settles
  // (one save per pause, not per step). A drag not settled yet is saved with an engine switch, or on leaving.
  useEffect(() => { if (settled) saveConfig(thresholdSetting(settled)) }, [settled, saveConfig])
  const unsettled = useRef<Drag | null>(null)
  useEffect(() => { unsettled.current = dragged === settled ? null : dragged }, [dragged, settled])
  useEffect(() => () => { if (unsettled.current) saveConfig(thresholdSetting(unsettled.current)) }, [saveConfig])
  const pickEngine = (id: string) => {
    saveConfig({ ...(unsettled.current ? thresholdSetting(unsettled.current) : {}), normalize_engine: id })
    unsettled.current = null
    setEngine(id)
    setDragged(null)
  }
  const clusters = useQuery({
    queryKey: ['curate', 'normalize', chosenEngine, settledThreshold],
    queryFn: () => api.curate.normalize(chosenEngine, settledThreshold),
    enabled: config.isSuccess,
    placeholderData: (previous) => previous,
  })
  // Clustering on Embedding embeds the names that had no vector yet, which the sidebar's count now takes in
  const queryClient = useQueryClient()
  const embedded = chosenEngine === 'embedding' && clusters.isSuccess ? clusters.dataUpdatedAt : 0
  useEffect(() => {
    if (embedded) void queryClient.invalidateQueries({ queryKey: ['curate', 'counts'] })
  }, [embedded, queryClient])

  if (config.isPending || clusters.isPending) return <Loading what="name clusters" />
  if (clusters.error) return <ErrorState error={clusters.error} />
  const data = clusters.data
  const embedding = chosenEngine === 'embedding'
  const slider = data.engines.find((e) => e.id === chosenEngine)

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
                onClick={() => pickEngine(e.id)}>{e.label}</button>
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
            min={slider?.threshold_min} max={slider?.threshold_max} step={slider?.threshold_step}
            value={chosenThreshold}
            onChange={(e) => setDragged({ engine: chosenEngine, value: Number(e.target.value) })} />
        </div>
      </div>

      {merged && <p className="ingest-note ok" role="status">{merged}</p>}

      <div className="tiles">
        <Tile label="Names in use" value={num(data.names)} />
        <Tile label="Clusters" value={num(data.groups.length)} />
        <Tile label="Ruled different" value={num(data.distinct_pairs.length)} />
      </div>

      {data.groups.length === 0
        ? <Empty>No name clusters at this setting. Loosen the threshold to see more.</Empty>
        : data.groups.map((group) => <Cluster key={JSON.stringify(group.names)} group={group} onMerged={setMerged} />)}

      {data.distinct_pairs.length > 0 && <DistinctPairs pairs={data.distinct_pairs} />}
    </div>
  )
}

function Cluster({ group, onMerged }: { group: NameGroup; onMerged: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [checked, setChecked] = useState<string[]>(group.names)
  const [target, setTarget] = useState(group.canonical[0] ?? group.names[0] ?? '')
  /** Check or uncheck names. The name kept is always a checked one: unchecking it passes to the next. */
  const check = (names: string[], on: boolean) => {
    const next = on ? group.names.filter((n) => checked.includes(n) || names.includes(n))
      : checked.filter((n) => !names.includes(n))
    setChecked(next)
    if (!next.includes(target)) setTarget(next[0] ?? '')
  }
  const allChecked = checked.length === group.names.length
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
    onSuccess: (result) => {
      setConfirming(false)
      onMerged(`Merged ${variants.length} name(s) into “${target}”${result.moves.length
        ? `; ${num(result.moves.length)} file(s) renamed on disk.` : '.'}`)
      void refresh()
    },
  })
  const distinct = useMutation({ mutationFn: () => api.curate.confirmDistinct(group.names), onSuccess: refresh })
  const count = (name: string) => group.counts.find((c) => c.name === name)?.count ?? 0

  return (
    <Card title={`${group.names.length} similar names`} hint={`${group.names.reduce((n, name) => n + count(name), 0)} document(s)`}>
      <label className="toggle-all">
        <input type="checkbox" checked={allChecked}
          ref={(box) => { if (box) box.indeterminate = checked.length > 0 && !allChecked }}
          onChange={(e) => check(group.names, e.target.checked)} /> All of them
      </label>
      <div className="name-list">
        {group.names.map((name) => (
          <label key={name} className="name-row">
            <input type="checkbox" checked={checked.includes(name)}
              onChange={(e) => check([name], e.target.checked)} />
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
        <button className="primary" disabled={variants.length === 0 || !checked.includes(target) || merge.isPending}
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
    onSuccess: () => afterArchiveEdit(queryClient, ['curate', 'normalize']),   // the sidebar counts the pair again
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
