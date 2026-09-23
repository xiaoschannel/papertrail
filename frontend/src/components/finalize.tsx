import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { FinalizeStatus, FinalizeStep } from '../api/types.ts'
import { ConfirmDialog } from './ConfirmDialog.tsx'

/*
 * Finalize, at the foot of Fix Rotation, Slice and Group: each edit on those pages is saved as it is made,
 * and Finalize is the step's one commit to the folder's history, for every unarchived batch the page shows.
 * The status says which batches the page shows and how many of the step's files wait to be committed.
 */

export function useFinalizeStatus(step: FinalizeStep) {
  return useQuery({ queryKey: ['ingest', 'finalize', step], queryFn: () => api.ingest.finalizeStatus(step) })
}

type Grouping = { batch_id: number; groups: string[][] }

export function FinalizeBar({ step, status, what, groups = [], confirm = null }: {
  step: FinalizeStep
  status: FinalizeStatus
  /** The step's work, in words: "the rotation fixes", "the slicing", "the grouping". */
  what: string
  /** Group: the batches whose grouping changed on the page, saved as part of Finalize. */
  groups?: Grouping[]
  /** What to confirm before Finalize, or null to go straight ahead. */
  confirm?: ReactNode
}) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [done, setDone] = useState('')
  const finalize = useMutation({
    mutationFn: () => api.ingest.finalize(step, groups),
    onSuccess: async (result) => {
      setAsking(false)
      setDone(result.committed ? `Committed ${what} (${result.committed}).` : 'Nothing had changed since the last commit.')
      queryClient.setQueryData(['ingest', 'finalize', step], result)
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      await queryClient.invalidateQueries({ queryKey: ['history'] })
      if (groups.length > 0) {
        for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
      }
    },
    onError: () => setAsking(false),
  })

  const batches = status.batches.length
  const waiting = status.changed
  const unsaved = groups.length
  const note = unsaved > 0
    ? `${unsaved} batch${unsaved === 1 ? '' : 'es'} with grouping changes, saved when you finalize.`
    : waiting === null ? 'The folder has no history yet: this starts it.'
      : waiting > 0 ? `${waiting} file${waiting === 1 ? '' : 's'} changed since the last commit, across ${batches} batch${batches === 1 ? '' : 'es'}.`
        : done || 'Everything here is committed.'
  const go = () => (confirm ? setAsking(true) : finalize.mutate())

  return (
    <div className="finalize-bar">
      {finalize.error && <div className="error-banner" role="alert">{finalize.error.message}</div>}
      <div className="start-bar">
        <button className="primary" disabled={finalize.isPending || (waiting === 0 && unsaved === 0)} onClick={go}>
          Finalize
        </button>
        <span className={`ingest-note${done && waiting === 0 && unsaved === 0 ? ' ok' : ''}`}>{note}</span>
      </div>
      {asking && (
        <ConfirmDialog title="Finalize?" confirmLabel="Finalize" danger busy={finalize.isPending}
          onConfirm={() => finalize.mutate()} onCancel={() => setAsking(false)}>
          {confirm}
        </ConfirmDialog>
      )}
    </div>
  )
}
