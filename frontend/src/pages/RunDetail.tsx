import type { CSSProperties } from 'react'
import { useParams } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Pill } from '../ui'
import { fmtDate, fmtMonth, fmtNum, label } from '../format'
import type { DqResult, PipelineRun, ReconResult } from '../types'
import { dqText, fmtSeconds, reconText } from './Runs'

interface Detail {
  run: PipelineRun | null
  dq: DqResult[]
  recon: ReconResult[]
}

const rowClass = (s: string | null) => (s === 'fail' ? 'row-fail' : s === 'warn' ? 'row-warn' : '')

const STAGES: Record<string, string> = {
  download: 'Download upload', bronze: 'Load raw (bronze)', dq: 'Data-quality rules', silver: 'Clean (silver)',
  gold: 'Aggregate (gold)', reconciliation: 'Reconciliation', score: 'Churn scoring', supabase_write: 'Save results',
}
const stageOrder = (k: string) => { const i = Object.keys(STAGES).indexOf(k); return i < 0 ? 99 : i }

export default function RunDetail() {
  const { runId = '' } = useParams()
  const d = useLoad<Detail>(async () => {
    const run = must(await supabase.from('pipeline_runs').select('*').eq('run_id', runId).maybeSingle()) as PipelineRun | null
    const dq = must(await supabase.from('dq_results').select('*').eq('run_id', runId).order('rule_id')) as DqResult[]
    const recon = must(
      await supabase.from('reconciliation_results').select('*').eq('run_id', runId).order('check_id'),
    ) as ReconResult[]
    return { run, dq, recon }
  }, [runId])

  const run = d.data?.run
  // Pipeline order, with the names people use; unknown stages keep their own name at the end.
  const stages = Object.entries(run?.stage_seconds ?? {})
    .map(([k, v]) => [k, Number(v) || 0] as const)
    .sort(([a], [b]) => stageOrder(a) - stageOrder(b))
  const maxStage = Math.max(...stages.map(([, v]) => v), 0)
  const shortId = runId.length > 12 ? runId.slice(0, 8) : runId

  return (
    <div className="page rise">
      <PageHead
        back={{ to: '/runs', label: 'Pipeline runs' }}
        title={`Run ${shortId}`}
        meta={run && (
          <>
            {fmtMonth(run.snapshot_month)}{' · '}
            {run.status ? <Pill tone={run.status} dot={run.status === 'processing'}>{label(run.status)}</Pill> : '–'}
            {' · Started '}{fmtDate(run.started_at)}{' · '}{fmtSeconds(run.stage_seconds)}
          </>
        )}
      />
      <Status loading={d.loading} error={d.error}>
        {d.data && !run && d.data.dq.length === 0 && (
          <section className="panel"><p className="empty-line">Run not found. Check the link or pick a run from the list.</p></section>
        )}
        {run && (
          <section className="panel">
            <div className="panel-head">
              <h2>Stage timings</h2>
              <span className="sub">Data quality: {dqText(run.dq_summary)} · Reconciliation: {reconText(run.recon_summary)}</span>
            </div>
            {stages.length === 0 ? (
              <p className="empty-line">No stage timings were recorded for this run.</p>
            ) : (
              <div className="bars">
                {stages.map(([k, v], i) => (
                  <div className="bar" key={k}>
                    <span className="name">{STAGES[k] ?? label(k)}</span>
                    <div className="track">
                      <div className="fill" style={{ width: `${maxStage ? (v / maxStage) * 100 : 0}%`, '--i': i } as CSSProperties} />
                    </div>
                    <span className="val">{v.toFixed(2)} s</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
        {d.data && (
          <>
            <section className="panel">
              <div className="panel-head"><h2>Data quality results</h2></div>
              {d.data.dq.length === 0 ? (
                <p className="empty-line">No data quality results for this run.</p>
              ) : (
                <Table>
                  <thead>
                    <tr><th>Rule</th><th>Description</th><th>Status</th><th className="num">Failing rows</th><th className="num">Checked rows</th><th>If it fails</th></tr>
                  </thead>
                  <tbody>
                    {d.data.dq.map((r) => (
                      <tr key={r.rule_id} className={rowClass(r.status)}>
                        <td>{r.rule_id}</td>
                        <td>{r.description}</td>
                        <td>{r.status ? <Pill tone={r.status}>{label(r.status)}</Pill> : '–'}</td>
                        <td className="num">{fmtNum(r.failing_rows)}</td>
                        <td className="num">{fmtNum(r.checked_rows)}</td>
                        <td>{r.action === 'fail' ? 'Stop the run' : r.action === 'warn' ? 'Warn only' : label(r.action)}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Reconciliation results</h2></div>
              {d.data.recon.length === 0 ? (
                <p className="empty-line">No reconciliation results for this run.</p>
              ) : (
                <Table>
                  <thead><tr><th>Check</th><th>Description</th><th>Status</th><th className="num">Difference</th></tr></thead>
                  <tbody>
                    {d.data.recon.map((r) => (
                      <tr key={r.check_id} className={rowClass(r.status)}>
                        <td>{r.check_id}</td>
                        <td>{r.description}</td>
                        <td>{r.status ? <Pill tone={r.status}>{label(r.status)}</Pill> : '–'}</td>
                        <td className="num">{r.difference ?? '–'}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </section>
          </>
        )}
      </Status>
    </div>
  )
}
