import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useSaveConfig } from '../api/config.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { JobPanel, useJobGate, useTrackJob } from '../components/jobs.tsx'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { LimitField, ReprocessField, StartBar } from '../components/ingestControls.tsx'
import './ingest.css'

export default function Parse() {
  const queryClient = useQueryClient()
  const [reprocess, setReprocess] = useState(false)
  const [limit, setLimit] = useState(0)
  const [extractor, setExtractor] = useState<string | null>(null)
  const [instruction, setInstruction] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const gate = useJobGate('parse')
  const track = useTrackJob()
  // Streamlit saved instructions as you typed; keep them when you navigate away without starting a run.
  const saveInstruction = useSaveConfig()
  const lastSaved = useRef<string | null>(null)   // so typing the original text back is still saved

  const status = useQuery({
    queryKey: ['ingest', 'parse', reprocess, limit],
    queryFn: () => api.ingest.parse(reprocess, limit),
    placeholderData: (previous) => previous,
  })
  const chosen = extractor ?? status.data?.extractor ?? ''
  const customInstruction = instruction ?? status.data?.custom_instruction ?? ''
  const start = useMutation({
    mutationFn: () => api.ingest.startParse({ extractor: chosen, reprocess, limit, custom_instruction: customInstruction }),
    onSuccess: (job) => {
      setConfirming(false)
      track(job)
      void queryClient.invalidateQueries({ queryKey: ['config'] })
    },
    onError: () => setConfirming(false),
  })

  if (status.isPending) return <Loading what="Parse status" />
  if (status.error) return <ErrorState error={status.error} />
  const s = status.data

  return (
    <div className="ingest-page">
      <h1>Parse</h1>
      <p className="page-sub">Extract merchant, date, amount and line items from each document's OCR text.</p>
      {s.blocker && s.extractors.length === 0 ? <Empty>{s.blocker}</Empty> : (
        <>
          <Card>
            {s.blocker && <p className="ingest-note ingest-blocker">{s.blocker}</p>}
            <div className="controls">
              <div className="field">
                <label htmlFor="parse-model">Model</label>
                <select id="parse-model" value={chosen} onChange={(e) => setExtractor(e.target.value)}>
                  {s.extractors.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>
              <ReprocessField value={reprocess} onChange={setReprocess} newLabel="Unparsed documents" />
              <LimitField value={limit} onChange={setLimit} />
            </div>
            <div className="field parse-instructions">
              <label htmlFor="parse-instruction">
                Custom instructions (added to the prompt){saveInstruction.isPending ? ' — saving…' : ''}
              </label>
              <textarea id="parse-instruction" rows={3} value={customInstruction}
                placeholder="e.g. Prefer the Japanese store name over the romanized one"
                onChange={(e) => setInstruction(e.target.value)}
                onBlur={() => {
                  const saved = lastSaved.current ?? s.custom_instruction
                  if (instruction !== null && instruction !== saved) {
                    lastSaved.current = instruction
                    saveInstruction.mutate({ parse_custom_instruction: instruction })
                  }
                }} />
            </div>
            <div className="tiles">
              <Tile label="Documents" value={s.total} />
              <Tile label="Parsed" value={s.processed} />
              <Tile label="Tossed (skipped)" value={s.tossed} />
              <Tile label="To process" value={s.to_process} />
            </div>
            {start.error && <div className="error-banner" role="alert">{start.error.message}</div>}
            <StartBar label={`Parse ${s.to_process} document${s.to_process === 1 ? '' : 's'}`}
              disabled={!s.to_process || !chosen} blockedBy={gate.blockedBy} running={gate.job?.status === 'running'}
              pending={start.isPending} onStart={() => (reprocess ? setConfirming(true) : start.mutate())} />
          </Card>
          {gate.job && <JobPanel job={gate.job} />}
        </>
      )}
      {confirming && (
        <ConfirmDialog title="Parse everything again?" confirmLabel="Parse again" danger busy={start.isPending}
          onConfirm={() => start.mutate()} onCancel={() => setConfirming(false)}>
          {s.to_process} document(s) will be extracted again, replacing their current results. A document whose
          extraction fails keeps its previous result. Review decisions are not changed.
        </ConfirmDialog>
      )}
    </div>
  )
}
