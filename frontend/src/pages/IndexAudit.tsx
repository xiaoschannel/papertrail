import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import './dev.css'

/** What batches.json says: its totals, filenames indexed more than once, each batch, and the scan folder against it. */
export default function IndexAudit() {
  const audit = useQuery({ queryKey: ['dev', 'index-audit'], queryFn: api.dev.indexAudit })

  if (audit.isPending) return <Loading what="the index audit" />
  if (audit.error) return <ErrorState error={audit.error} />
  const a = audit.data

  return (
    <div className="dev-page">
      <h1>Index Audit</h1>
      <p className="page-sub">What File Index has recorded in batches.json, and how it compares with the scan folder.</p>

      {a.index_file === null ? (
        <Empty>No batches.json yet: <Link className="rowlink" to="/file-index">File Index</Link> writes it when the
          first batch is added.</Empty>
      ) : (
        <>
          <Card title="batches.json">
            <div className="tiles">
              <Tile label="File size" value={`${a.index_file.size.toLocaleString()} bytes`} />
              <Tile label="Last modified" value={a.index_file.modified} />
            </div>
          </Card>

          <Card title="Totals">
            <div className="tiles">
              <Tile label="Batches" value={a.total_batches} />
              <Tile label="Archived" value={a.archived} />
              <Tile label="Not archived" value={a.non_archived} />
              <Tile label="File entries" value={a.total_entries} />
            </div>
            <div className="tiles">
              <Tile label="Unique filenames" value={a.unique_filenames} />
              <Tile label="Filenames in 2+ batches" value={a.duplicates.length} />
              <Tile label="Lost to dedup" value={a.lost_to_dedup} />
            </div>
            <p className="ingest-note">Unique filenames is the “Indexed” count File Index shows.</p>
            {a.duplicates.length > 0 && (
              <>
                <p className="ingest-note ingest-warning">
                  {a.duplicates.length} filename{a.duplicates.length === 1 ? ' appears' : 's appear'} in more than one
                  batch. File Index counts each filename once, so {a.lost_to_dedup} entr{a.lost_to_dedup === 1 ? 'y is' : 'ies are'} hidden
                  from its “Indexed” count.
                </p>
                <details className="ingest-details">
                  <summary>Filenames in more than one batch</summary>
                  <ul className="file-list">
                    {a.duplicates.map((d) => (
                      <li key={d.filename}><code>{d.filename}</code> — {d.count}× in batches {d.batch_ids.join(', ')}</li>
                    ))}
                  </ul>
                </details>
              </>
            )}
          </Card>

          <Card title="Scan folder">
            {a.input === null ? (
              <Empty>No scan folder to compare with. <Link className="rowlink" to="/config">Set it in Config →</Link></Empty>
            ) : (
              <>
                <div className="tiles">
                  <Tile label="Scans in the folder" value={a.input.on_disk} />
                  <Tile label="Indexed, not in the folder" value={a.input.indexed_not_on_disk} />
                  <Tile label="In the folder, not indexed" value={a.input.on_disk_not_indexed.length} />
                </div>
                <p className="ingest-note">
                  Indexed scans leave the folder when they are archived, so the middle count is expected to grow.
                </p>
                {a.input.on_disk_not_indexed.length > 0 && (
                  <details className="ingest-details">
                    <summary>In the folder, not indexed</summary>
                    <ul className="file-list">
                      {a.input.on_disk_not_indexed.map((name) => <li key={name}><code>{name}</code></li>)}
                    </ul>
                  </details>
                )}
              </>
            )}
          </Card>

          {/* The long table goes last and isn't capped: below it there's nothing for it to push. */}
          <Card title="Per batch" hint={`${a.batches.length} batch${a.batches.length === 1 ? '' : 'es'}`}
            className="card--uncapped">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="num">Batch</th><th className="num">Files</th><th className="num">Running total</th>
                    <th>Archived</th><th>Start</th><th>End</th>
                  </tr>
                </thead>
                <tbody>
                  {a.batches.map((batch) => (
                    <tr key={batch.batch_id}>
                      <td className="num">{batch.batch_id}</td>
                      <td className="num">{batch.files}</td>
                      <td className="num">{batch.running_total}</td>
                      <td>{batch.archived ? 'Yes' : 'No'}</td>
                      <td>{batch.start}</td>
                      <td>{batch.end}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
