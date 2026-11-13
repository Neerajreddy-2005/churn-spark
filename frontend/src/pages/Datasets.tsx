// Mode 2: every non-Telco file the organisation uploaded, with its state and model quality.
import { Link } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Pill } from '../ui'
import { fmtDate, fmtNum, label } from '../format'
import type { UploadStatus } from '../types'

interface Row {
  upload_id: string
  name: string
  created_at: string
  rows: number | null
  columns: number | null
  churn: boolean | null
  explore_only: boolean | null
  auc: number | null
  suspicious: boolean | null
  uploads: { status: UploadStatus; status_reason: string | null } | null
}

export default function Datasets() {
  const list = useLoad(async () => must(await supabase.from('datasets')
    .select('upload_id,name,created_at,rows:source_info->rows,columns:source_info->columns,'
      + 'churn:suggestions->is_churn_data,explore_only:result->explore_only,auc:result->metrics->auc_roc_test,'
      + 'suspicious:result->suspicious,uploads(status,status_reason)')
    .eq('kind', 'generic').order('created_at', { ascending: false }).limit(100)) as unknown as Row[], [])

  return (
    <div className="page rise">
      <PageHead title="Datasets"
        meta="Customer churn files in any other layout. A clear churn column trains a model on that file straight away; otherwise you confirm the columns here." />
      <section className="panel">
        <Status loading={list.loading && !list.data} error={list.error}>
          {!list.data?.length ? (
            <p className="empty-line">
              No datasets yet. Upload any customer CSV or Excel file on <Link to="/upload">Upload data</Link>; files that are not in the Telco layout appear here.
            </p>
          ) : (
            <Table>
              <thead>
                <tr><th>File</th><th>Uploaded</th><th className="num">Rows</th><th className="num">Columns</th><th>Status</th><th>Model</th></tr>
              </thead>
              <tbody>
                {list.data.map((d) => {
                  const status = d.uploads?.status ?? 'done'
                  return (
                    <tr key={d.upload_id}>
                      <td><Link to={`/datasets/${d.upload_id}`}>{d.name}</Link></td>
                      <td>{fmtDate(d.created_at)}</td>
                      <td className="num">{fmtNum(d.rows)}</td>
                      <td className="num">{fmtNum(d.columns)}</td>
                      <td><Pill tone={status} dot={status === 'processing'}>{label(status)}</Pill></td>
                      <td>{d.explore_only ? 'Exploration only'
                        : d.auc != null ? <>AUC {Number(d.auc).toFixed(3)}{d.suspicious ? ' · check for leakage' : ''}</>
                        : status === 'needs_review' ? (d.churn ? 'Confirm the columns' : 'No churn column found') : '–'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </Table>
          )}
        </Status>
      </section>
    </div>
  )
}
