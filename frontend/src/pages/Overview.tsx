import { useState, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status } from '../components'
import { CountUp, Explain, Pill } from '../ui'
import { useGo } from '../nav'
import { UploadDrop } from '../UploadDrop'
import { Select } from '../Select'
import { fmtReason, fmtDay, fmtMoney, fmtMonth, fmtNum, fmtPct, fmtRisk, label } from '../format'
import { RISK_BANDS, type CustomerScore, type PipelineRun, type RiskBand, type SegmentAggregate } from '../types'

interface MonthData {
  segments: SegmentAggregate[]
  bands: Record<RiskBand, number>
  top: CustomerScore[]
  run: PipelineRun | null
}

export default function Overview() {
  const [month, setMonth] = useState<string | null>(null)

  const months = useLoad(async () => {
    const rows = must(
      await supabase.from('segment_aggregates').select('snapshot_month').eq('segment_type', 'overall').order('snapshot_month', { ascending: false }),
    ) as { snapshot_month: string }[]
    return [...new Set(rows.map((r) => r.snapshot_month))]
  }, [])

  const selected = month ?? months.data?.[0] ?? null

  const data = useLoad<MonthData | null>(async () => {
    if (!selected) return null
    const [segRes, topRes, runRes, ...bandRes] = await Promise.all([
      supabase.from('segment_aggregates').select('*').eq('snapshot_month', selected),
      supabase.from('customer_scores').select('*').eq('snapshot_month', selected)
        .order('risk_score', { ascending: false }).order('customer_id').limit(6),
      supabase.from('pipeline_runs').select('*').eq('snapshot_month', selected).eq('status', 'done')
        .order('finished_at', { ascending: false }).limit(1),
      ...RISK_BANDS.map((b) => supabase.from('customer_scores').select('risk_band', { count: 'exact', head: true })
        .eq('snapshot_month', selected).eq('risk_band', b)),
    ])
    const bands = { high: 0, medium: 0, low: 0 } as Record<RiskBand, number>
    RISK_BANDS.forEach((b, i) => {
      const r = bandRes[i]!
      if (r.error) throw new Error(r.error.message)
      bands[b] = r.count ?? 0
    })
    return {
      segments: must(segRes) as SegmentAggregate[],
      top: must(topRes) as CustomerScore[],
      run: ((must(runRes) as PipelineRun[])[0]) ?? null,
      bands,
    }
  }, [selected])

  if (!months.loading && !months.error && !months.data?.length) return <Welcome />

  const run = data.data?.run
  return (
    <div className="page rise">
      <PageHead
        title="Overview"
        meta={run ? <>From the pipeline run of {fmtDay(run.finished_at)} · <Link to={`/runs/${encodeURIComponent(run.run_id)}`}>see its checks</Link></> : selected ? fmtMonth(selected) : null}
        actions={months.data && months.data.length > 0 && (
          <Select label="Month" hideLabel value={selected ?? ''} onChange={setMonth}
            options={months.data.map((m) => ({ value: m, label: fmtMonth(m) }))} />
        )} />
      <Status loading={months.loading || (data.loading && !data.data)} error={months.error ?? data.error}>
        {data.data && <MonthView key={selected} d={data.data} />}
      </Status>
    </div>
  )
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

const source = (run: PipelineRun | null) => {
  if (!run) return <span className="src">No finished pipeline run is recorded for this month.</span>
  const dq = run.dq_summary ?? {}
  const rc = run.recon_summary ?? {}
  return (
    <span className="src">
      Run {run.run_id.slice(0, 8)} on {fmtDay(run.finished_at)}. Data quality: {dq.pass ?? 0} passed, {plural(dq.warn ?? 0, 'warning')}, {dq.fail ?? 0} failed.
      Reconciliation: {rc.pass ?? 0} passed, {rc.fail ?? 0} failed.
    </span>
  )
}

function MonthView({ d }: { d: MonthData }) {
  const go = useGo()
  const overall = d.segments.find((s) => s.segment_type === 'overall')
  const scored = d.bands.high + d.bands.medium + d.bands.low
  const lostShare = overall?.mrr && overall.mrr_lost != null ? (overall.mrr_lost / overall.mrr) * 100 : null
  // A file without a Churn column is scored, but actual churn and revenue lost are unknown.
  const labelled = overall?.churned != null
  const seg = (t: string) => d.segments.filter((s) => s.segment_type === t)

  const open = (e: MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>, c: CustomerScore) => {
    const id = e.currentTarget.querySelector<HTMLElement>('.id')
    if (id) id.style.viewTransitionName = 'customer-id'
    go(`/customers/${encodeURIComponent(c.customer_id)}?month=${c.snapshot_month}`)
  }

  return (
    <>
      {overall && (
        <div className="kpis" style={{ ['--n' as string]: 4 }}>
          <div className="kpi">
            <span className="label">Customers</span>
            <span className="value">
              <Explain title="Customers" body={<>Every row in this month's upload that passed the data-quality rules. {source(d.run)}</>}>
                <CountUp value={overall.customers ?? 0} format={(n) => fmtNum(Math.round(n))} />
              </Explain>
            </span>
            <span className="note">in {fmtMonth(overall.snapshot_month)}</span>
          </div>
          <div className="kpi">
            <span className="label">Churned</span>
            <span className="value">
              {labelled ? (
                <Explain title="Churn rate" body={<>Churned ÷ customers = {fmtNum(overall.churned)} ÷ {fmtNum(overall.customers)} = {fmtPct(overall.churn_rate_pct)}. {source(d.run)}</>}>
                  <CountUp value={overall.churned ?? 0} format={(n) => fmtNum(Math.round(n))} />
                </Explain>
              ) : (
                <Explain title="Churn not known yet" body={<>This month's file had no Churn column, so who actually left is unknown. Every customer is still scored by the model. {source(d.run)}</>}>
                  –
                </Explain>
              )}
            </span>
            <span className="note">{labelled ? <>{fmtPct(overall.churn_rate_pct)} of customers</> : 'No Churn column in this file'}</span>
          </div>
          <div className="kpi">
            <span className="label">High risk now</span>
            <span className="value">
              <Explain title="High risk" body={<>Customers the churn model puts in the high band, out of {fmtNum(scored)} scored. Medium {fmtNum(d.bands.medium)}, low {fmtNum(d.bands.low)}. {source(d.run)}</>}>
                <CountUp value={d.bands.high} format={(n) => fmtNum(Math.round(n))} />
              </Explain>
            </span>
            <span className="note"><span className="nb">{fmtNum(d.bands.medium)} medium</span> · <span className="nb">{fmtNum(d.bands.low)} low</span></span>
          </div>
          <div className="kpi">
            <span className="label">Monthly revenue lost</span>
            <span className="value">
              {labelled ? (
                <Explain title="Monthly revenue lost" body={<>Sum of monthly charges of churned customers: {fmtMoney(overall.mrr_lost)} of {fmtMoney(overall.mrr)} total ({fmtPct(lostShare)}). No currency in the source data. {source(d.run)}</>}>
                  <CountUp value={overall.mrr_lost ?? 0} format={fmtMoney} />
                </Explain>
              ) : (
                <Explain title="Revenue lost not known yet" body={<>Needs the Churn column. Total monthly revenue of the file: {fmtMoney(overall.mrr)}. {source(d.run)}</>}>
                  –
                </Explain>
              )}
            </span>
            <span className="note">{labelled ? <><span className="nb">{fmtPct(lostShare)} of</span> monthly revenue</> : 'Needs the Churn column'}</span>
          </div>
        </div>
      )}

      <div className="grid-2">
        <BarPanel title="Churn by tenure" sub="How long they had been customers" rows={seg('tenure_band')} />
        <section className="panel">
          <div className="panel-head">
            <h2>Most at risk</h2>
            <Link to="/customers" className="sub">View all {fmtNum(d.bands.high)} high-risk</Link>
          </div>
          {d.top.length === 0 ? (
            <p className="empty-line">No customers were scored for this month.</p>
          ) : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Customer</th><th className="hide-sm">Top reason</th><th className="num">Risk</th><th><span className="sr-only">Band</span></th></tr></thead>
                <tbody>
                  {d.top.map((c) => (
                    <tr key={c.customer_id} className="clickable" tabIndex={0} aria-label={`Open customer ${c.customer_id}`}
                      onClick={(e) => open(e, c)} onKeyDown={(e) => { if (e.key === 'Enter') open(e, c) }}>
                      <td className="id">{c.customer_id}</td>
                      <td className="wrap muted hide-sm">{fmtReason(c.reason_1)}</td>
                      <td className="num">{fmtRisk(c.risk_score)}</td>
                      <td className="num">{c.risk_band && <Pill tone={c.risk_band}>{label(c.risk_band)}</Pill>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <div className="grid-3">
        <BarPanel title="By contract" rows={seg('contract')} />
        <BarPanel title="By internet service" rows={seg('internet_service')} />
        <BarPanel title="By payment method" rows={seg('payment_method')} />
      </div>
    </>
  )
}

// Churn rate per segment as cobalt bars, scaled to the highest rate in the group.
// Unlabelled month (no Churn column): the bars show customer counts instead.
function BarPanel({ title, sub, rows }: { title: string; sub?: string; rows: SegmentAggregate[] }) {
  const labelled = rows.some((r) => r.churned != null)
  const value = (r: SegmentAggregate) => (labelled ? r.churn_rate_pct : r.customers) ?? 0
  const sorted = [...rows].sort((a, b) => value(b) - value(a))
  if (title === 'Churn by tenure') sorted.sort((a, b) => parseInt(a.segment) - parseInt(b.segment))
  const max = Math.max(...sorted.map(value), 1)
  return (
    <section className="panel">
      <div className="panel-head"><h2>{labelled ? title : title.replace(/^Churn by/, 'Customers by')}</h2><span className="sub">{labelled ? (sub ?? 'Churn rate') : 'Customers · churn not known yet'}</span></div>
      {sorted.length === 0 ? <p className="empty-line">No data for this month.</p> : (
        <div className="bars">
          {sorted.map((r, i) => (
            <div className="bar" key={r.segment} title={labelled ? `${fmtNum(r.churned)} of ${fmtNum(r.customers)} churned` : `${fmtNum(r.customers)} customers`}>
              <span className="name">{r.segment}</span>
              <span className="track"><span className="fill" style={{ width: `${(value(r) / max) * 100}%`, ['--i' as string]: i }} /></span>
              <span className="val">{labelled ? fmtPct(r.churn_rate_pct) : fmtNum(r.customers)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function Welcome() {
  return (
    <div className="page rise">
      <PageHead title="Welcome to Churn-Spark" meta="Your team's numbers will appear here after the first upload." />
      <section className="panel">
        <div className="welcome">
          <div style={{ display: 'grid', gap: 14 }}>
            <h2>Start with this month's customer file</h2>
            <p>Upload the CSV export of your customers. The pipeline checks it against 20 data-quality rules, reconciles the totals and scores every customer's chance of leaving.</p>
            <ol>
              <li>Upload the CSV and pick the month it covers.</li>
              <li>Processing takes about 2–3 minutes. Follow it on Pipeline runs.</li>
              <li>Come back here for the overview, then work the at-risk list.</li>
            </ol>
          </div>
          <UploadDrop />
        </div>
      </section>
    </div>
  )
}
