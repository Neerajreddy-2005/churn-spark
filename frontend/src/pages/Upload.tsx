import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Pill } from '../ui'
import { UploadDrop } from '../UploadDrop'
import { fmtDate, fmtMonth, label } from '../format'
import type { UploadRow } from '../types'

export default function Upload() {
  const uploads = useLoad(
    async () =>
      must(await supabase.from('uploads').select('*').order('created_at', { ascending: false }).limit(100)) as UploadRow[],
    [],
  )
  const { reload } = uploads
  const active = uploads.data?.some((u) => u.status === 'queued' || u.status === 'processing') ?? false

  useEffect(() => {
    if (!active) return
    const t = setInterval(reload, 10000)
    return () => clearInterval(t)
  }, [active, reload])

  return (
    <div className="page rise">
      <PageHead title="Upload" meta="Processing runs on the pipeline worker and can take 2-3 minutes." />
      <section className="panel">
        <div className="panel-head"><h2>Upload this month's customers</h2></div>
        <UploadDrop onUploaded={reload} />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>Your organisation's uploads</h2>
          <span className="sub">Queued: waiting for the worker. Processing: being scored. Needs review: a file in another layout; confirm its columns under Datasets. Done: open the run. Rejected: the file was not acceptable, see the reason. Failed: system error, upload again.</span>
        </div>
        <Status loading={uploads.loading && !uploads.data} error={uploads.error}>
          {!uploads.data?.length ? (
            <p className="empty-line">No uploads yet. Upload a CSV above and it appears here.</p>
          ) : (
            <Table>
              <thead>
                <tr><th>Created</th><th>File</th><th>Month</th><th>Status</th><th>Reason</th><th>Run</th></tr>
              </thead>
              <tbody>
                {uploads.data.map((u) => (
                  <tr key={u.id}>
                    <td>{fmtDate(u.created_at)}</td>
                    <td>{u.original_name}</td>
                    <td>{fmtMonth(u.snapshot_month)}</td>
                    <td><Pill tone={u.status} dot={u.status === 'processing'}>{label(u.status)}</Pill></td>
                    <td>{u.status === 'needs_review'
                      ? <Link to={`/datasets/${u.id}`}>Confirm the columns</Link>
                      : u.status_reason ?? ''}</td>
                    <td>{u.run_id ? <Link to={`/runs/${encodeURIComponent(u.run_id)}`}>{u.run_id}</Link> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Status>
      </section>
    </div>
  )
}
