import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useConfig, useSaveConfig } from '../api/config.ts'
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
  const track = useTrackJob()
  // Instructions are saved when the box loses focus, so navigating away without starting a run keeps them.
  const saveConfig = useSaveConfig()
  const lastSaved = useRef<string | null>(null)   // so typing the original text back is still saved

  const status = useQuery({
    queryKey: ['ingest', 'parse', reprocess, limit],
    queryFn: () => api.ingest.parse(reprocess, limit),
    placeholderData: (previous) => previous,
  })
  // The saved choices come from the config, where every page saves them (Review and the Experiment
  // bench edit the instructions too), not from this page's own status, which may be older.
  const config = useConfig().data
  const savedExtractor = config && status.data?.extractors.includes(config.extractor_model) ? config.extractor_model : undefined
  const chosen = extractor ?? savedExtractor ?? status.data?.extractor ?? ''
  const customInstruction = instruction ?? config?.parse_custom_instruction ?? status.data?.custom_instruction ?? ''
  // A hosted model (OpenAI) runs beside OCR; one on this machine's GPU has to wait for it.
  const gate = useJobGate('parse', { gpu: status.data?.local_extractors.includes(chosen) ?? false })
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
                <select id="parse-model" value={chosen} onChange={(e) => {
                  setExtractor(e.target.value)
                  saveConfig.mutate({ extractor_model: e.target.value })   // remembered as soon as it's picked
                }}>
                  {s.extractors.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>
              <ReprocessField value={reprocess} onChange={setReprocess} newLabel="Unparsed documents" />
              <LimitField value={limit} onChange={setLimit} />
            </div>
            <div className="field parse-instructions">
              <label htmlFor="parse-instruction">
                Custom instructions (added to the prompt){saveConfig.isPending ? ' — saving…' : ''}
              </label>
              <textarea id="parse-instruction" rows={3} value={customInstruction}
                placeholder="e.g. Prefer the Japanese store name over the romanized one"
                onChange={(e) => setInstruction(e.target.value)}
                onBlur={() => {
                  const saved = lastSaved.current ?? config?.parse_custom_instruction ?? s.custom_instruction
                  if (instruction !== null && instruction !== saved) {
                    lastSaved.current = instruction
                    saveConfig.mutate({ parse_custom_instruction: instruction })
                  }
                }} />
            </div>
            <div className="tiles">
              <Tile label="Documents" value={s.total} />
              <Tile label="Parsed" value={s.processed} />
              <Tile label="Tossed (skipped)" value={s.tossed} />
              <Tile label="To process" value={s.to_process} />
            </div>
            {s.waiting > 0 && <p className="ingest-note">
              {s.waiting} more document{s.waiting === 1 ? ' is' : 's are'} in a batch another job is using; the
              next run picks {s.waiting === 1 ? 'it' : 'them'} up.
            </p>}
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
