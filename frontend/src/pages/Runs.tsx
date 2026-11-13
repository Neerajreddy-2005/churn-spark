import { Link } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Pill } from '../ui'
import { fmtDate, fmtMonth, label } from '../format'
import type { PipelineRun } from '../types'

export const totalSeconds = (s: Record<string, number> | null) =>
  s ? Object.values(s).reduce((a, b) => a + (Number(b) || 0), 0) : null

export const summary = (s: Record<string, number> | null) =>
  s ? Object.entries(s).map(([k, v]) => `${k}: ${v}`).join(', ') : '-'

// "20 passed · 0 failed · 1 warning"
export const dqText = (s: Record<string, number> | null) =>
  s ? `${s.pass ?? 0} passed · ${s.fail ?? 0} failed · ${s.warn ?? 0} ${(s.warn ?? 0) === 1 ? 'warning' : 'warnings'}` : '–'

// "10 passed · 0 failed"
export const reconText = (s: Record<string, number> | null) =>
  s ? `${s.pass ?? 0} passed · ${s.fail ?? 0} failed` : '–'

export const fmtSeconds = (s: Record<string, number> | null) => {
  const t = totalSeconds(s)
  return t == null ? '–' : `${t.toFixed(1)} s`
}

type Kind = { kind: string } | { kind: string }[] | null
type RunRow = PipelineRun & { uploads: { id: string; original_name: string; datasets: Kind } | null }

// A run belongs to a Telco upload or to a dataset in another layout (Mode 2).
const isDataset = (r: RunRow) => {
  const d = r.uploads?.datasets
  return (Array.isArray(d) ? d[0]?.kind : d?.kind) === 'generic'
}

export default function Runs() {
  const runs = useLoad(
    async () =>
      must(await supabase.from('pipeline_runs').select('*,uploads(id,original_name,datasets(kind))')
        .order('started_at', { ascending: false }).limit(100)) as unknown as RunRow[],
    [],
  )
  return (
    <div className="page rise">
      <PageHead title="Pipeline runs"
        meta="Each processed upload produces one run: Telco files (20 data checks, 26 reconciliation checks) and datasets in other layouts (7 general checks, reconciliation of rows and churners). Open a run to see its checks." />
      <section className="panel">
        <Status loading={runs.loading} error={runs.error}>
          {!runs.data?.length ? (
            <p className="empty-line">No runs yet. <Link to="/upload">Upload a CSV</Link> and its run appears here.</p>
          ) : (
            <Table>
              <thead>
                <tr><th>Started</th><th>File</th><th>Type</th><th>Month</th><th>Status</th><th>Data quality</th><th>Reconciliation</th><th className="num">Duration</th><th>Run</th></tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.run_id}>
                    <td>{fmtDate(r.started_at)}</td>
                    <td className="wrap">{!r.uploads ? '–' : isDataset(r)
                      ? <Link to={`/datasets/${r.uploads.id}`}>{r.uploads.original_name}</Link>
                      : <Link to={`/?data=telco:${r.snapshot_month}`}>{r.uploads.original_name}</Link>}</td>
                    <td><Pill tone="neutral">{isDataset(r) ? 'Dataset' : 'Telco'}</Pill></td>
                    <td>{fmtMonth(r.snapshot_month)}</td>
                    <td>{r.status ? <Pill tone={r.status} dot={r.status === 'processing'}>{label(r.status)}</Pill> : '–'}</td>
                    <td>{dqText(r.dq_summary)}</td>
                    <td>{reconText(r.recon_summary)}</td>
                    <td className="num">{fmtSeconds(r.stage_seconds)}</td>
                    <td><Link to={`/runs/${encodeURIComponent(r.run_id)}`} className="short-id" title={r.run_id}>{r.run_id.slice(0, 8)}</Link></td>
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
