import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import type { BatchSanity } from '../api/types.ts'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './dev.css'

/** Is every indexed scan where it should be, and does every archived scan have its sidecar? */
export default function SanityCheck() {
  const report = useQuery({ queryKey: ['dev', 'sanity'], queryFn: api.dev.sanity })

  if (report.isPending) return <Loading what="the sanity check" />
  if (report.error) return <ErrorState error={report.error} />
  const { indexed, batches, sidecar_mismatches: mismatches } = report.data
  const problems = batches.filter((batch) => problemsIn(batch).length > 0).length

  return (
    <div className="dev-page">
      <h1>Sanity Check</h1>
      <p className="page-sub">
        Whether every scan File Index recorded is where it should be (the archive, or the scan folder while it is
        being ingested), and whether every archived scan has its sidecar.
      </p>

      <Card title="Archive sanity" hint="scans and sidecars, folder by folder">
        {mismatches.length === 0 ? (
          <p className="dev-ok">Every archived scan has its sidecar, and every sidecar its scan.</p>
        ) : (
          <>
            <p className="neg">{mismatches.length} folder{mismatches.length === 1 ? '' : 's'} where scans and sidecars
              don't pair up.</p>
            {mismatches.map((folder) => (
              <details key={folder.folder} className="ingest-details">
                <summary>
                  <strong>{folder.folder}</strong>
                  {folder.missing_sidecar.length > 0 && ` · ${folder.missing_sidecar.length} without a sidecar`}
                  {folder.extra_sidecar.length > 0 && ` · ${folder.extra_sidecar.length} sidecar(s) without a scan`}
                </summary>
                {folder.missing_sidecar.length > 0 && <FileList title="Scans without a sidecar" names={folder.missing_sidecar} />}
                {folder.extra_sidecar.length > 0 && <FileList title="Sidecars without a scan" names={folder.extra_sidecar} />}
              </details>
            ))}
          </>
        )}
      </Card>

      {/* The batch table goes last and isn't capped: it is most of the page, and below it there's nothing to push. */}
      <Card title="Batch sanity" className="card--uncapped"
        hint={indexed ? `${batches.length} batch${batches.length === 1 ? '' : 'es'}${problems ? ` · ${problems} with missing files` : ''}` : undefined}>
        {!indexed ? (
          <Empty>No batches.json yet: <Link className="rowlink" to="/file-index">File Index</Link> writes it when
            the first batch is added.</Empty>
        ) : batches.length === 0 ? <Empty>No batches.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th className="num">Batch</th><th className="num">Files</th><th className="num">Organized</th><th>Status</th></tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.batch_id}>
                    <td className="num">{batch.batch_id}</td>
                    <td className="num">{batch.files}</td>
                    <td className="num">{batch.organized}</td>
                    <td><Status batch={batch} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

const problemsIn = (batch: BatchSanity) => (batch.archived ? batch.missing_from_archive : batch.missing_from_input ?? [])

function Status({ batch }: { batch: BatchSanity }) {
  const missing = problemsIn(batch)
  if (missing.length > 0) {
    return <span className="neg">{batch.archived ? 'Missing from the archive' : 'Missing from the scan folder'}:{' '}
      {missing.join(', ')}</span>
  }
  if (batch.archived) return <span className="dev-ok">All files archived</span>
  if (batch.missing_from_input === null) return <span className="config-hint">Pending archive (no scan folder to check)</span>
  return <span className="dev-ok">All files in the scan folder</span>
}

function FileList({ title, names }: { title: string; names: string[] }) {
  return (
    <>
      <p className="config-hint dev-list-title">{title}</p>
      <ul className="file-list">{names.map((name) => <li key={name}><code>{name}</code></li>)}</ul>
    </>
  )
}
