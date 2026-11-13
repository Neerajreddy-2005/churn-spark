// Overview and At-risk views for a trained dataset of any layout (Mode 2), built from the same cards,
// bars and tables as the Telco views so both read as one product.
import { useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from './supabase'
import { must, useLoad } from './useLoad'
import { Status, Table } from './components'
import { CountUp, Explain, Pill } from './ui'
import { Select } from './Select'
import { useGo } from './nav'
import { fmtNum, fmtPct, fmtRisk, label } from './format'
import { RISK_BANDS, type DatasetResult, type DatasetScore, type RiskBand } from './types'

interface Summary {
  result: DatasetResult
  target: string
  positive: string
  bands: Record<RiskBand, number>
  churned: number
  top: DatasetScore[]
}

const customerLink = (id: string, row: string) => `/customers/${encodeURIComponent(row)}?dataset=${id}`

export function DatasetOverview({ id }: { id: string }) {
  const go = useGo()
  const s = useLoad<Summary>(async () => {
    const base = () => supabase.from('dataset_scores').select('row_id', { count: 'exact', head: true }).eq('upload_id', id)
    const [ds, top, churned, ...bands] = await Promise.all([
      supabase.from('datasets').select('result,review,profile->columns').eq('upload_id', id).single(),
      supabase.from('dataset_scores').select('*').eq('upload_id', id).order('risk_score', { ascending: false }).order('row_id').limit(6),
      base().eq('churned', true),
      ...RISK_BANDS.map((b) => base().eq('risk_band', b)),
    ])
    const d = must(ds) as unknown as { result: DatasetResult; review: { target: string; positive: string }; columns: { key: string; name: string }[] }
    const out = { high: 0, medium: 0, low: 0 } as Record<RiskBand, number>
    RISK_BANDS.forEach((b, i) => {
      if (bands[i]!.error) throw new Error(bands[i]!.error!.message)
      out[b] = bands[i]!.count ?? 0
    })
    if (churned.error) throw new Error(churned.error.message)
    return {
      result: d.result, target: d.columns.find((c) => c.key === d.review.target)?.name ?? d.review.target,
      positive: d.review.positive, bands: out, churned: churned.count ?? 0,
      top: must(top) as DatasetScore[],
    }
  }, [id])

  return (
    <Status loading={s.loading && !s.data} error={s.error}>
      {s.data && (() => {
        const { result: r, bands, top } = s.data
        const total = r.rows?.total ?? 0
        const m = r.metrics!
        const insights = (r.insights ?? []).filter((f) => f.spread_pct_points > 0)
        const src = <span className="src">Model trained on this file, measured on {fmtNum(r.rows?.test)} customers held back from training. <Link to={`/datasets/${id}`}>Data and model details</Link>.</span>
        return (
          <>
            <div className="kpis" style={{ ['--n' as string]: 4 }}>
              <div className="kpi"><span className="label">Customers</span>
                <span className="value"><Explain title="Customers" body={<>Every row of the file. {src}</>}><CountUp value={total} format={(n) => fmtNum(Math.round(n))} /></Explain></span>
                <span className="note">in this file</span></div>
              <div className="kpi"><span className="label">Churned</span>
                <span className="value"><Explain title="Churned" body={<>Rows where {s.data.target} = {s.data.positive}: {fmtNum(s.data.churned)} of {fmtNum(total)}. {src}</>}><CountUp value={s.data.churned} format={(n) => fmtNum(Math.round(n))} /></Explain></span>
                <span className="note">{fmtPct(r.base_churn_rate_pct)} of customers</span></div>
              <div className="kpi"><span className="label">High risk now</span>
                <span className="value"><Explain title="High risk" body={<>Customers the model puts in the high band. Medium {fmtNum(bands.medium)}, low {fmtNum(bands.low)}. {src}</>}><CountUp value={bands.high} format={(n) => fmtNum(Math.round(n))} /></Explain></span>
                <span className="note"><span className="nb">{fmtNum(bands.medium)} medium</span> · <span className="nb">{fmtNum(bands.low)} low</span></span></div>
              <div className="kpi"><span className="label">Model quality</span>
                <span className="value"><Explain title="Holdout AUC" body={<>How well the model ranks leavers above stayers on customers it never saw (0.5 = coin flip, 1.0 = perfect). The riskiest 20% reach {fmtPct(m.capture.top_20?.capture_rate_pct)} of churners. {src}</>}>{m.auc_roc_test.toFixed(3)}</Explain></span>
                <span className="note">AUC{r.suspicious ? ' · check for leakage' : ''}</span></div>
            </div>
            <div className="grid-2">
              {insights[0] ? <InsightPanel f={insights[0]} total={total} /> : <section className="panel"><p className="empty-line">No group stands out.</p></section>}
              <section className="panel">
                <div className="panel-head"><h2>Most at risk</h2><Link to={`/customers?data=ds:${id}`} className="sub">View all {fmtNum(bands.high)} high-risk</Link></div>
                {!top.length ? <p className="empty-line">No customers were scored.</p> : (
                  <div className="tablewrap"><table>
                    <thead><tr><th>Customer</th><th className="hide-sm">Top reason</th><th className="num">Risk</th><th><span className="sr-only">Band</span></th></tr></thead>
                    <tbody>{top.map((c) => (
                      <tr key={c.row_id} className="clickable" tabIndex={0} aria-label={`Open customer ${c.row_id}`}
                        onClick={() => go(customerLink(id, c.row_id))} onKeyDown={(e) => { if (e.key === 'Enter') go(customerLink(id, c.row_id)) }}>
                        <td className="id">{c.row_id}</td>
                        <td className="wrap muted hide-sm">{c.reason_1 ?? '–'}</td>
                        <td className="num">{fmtRisk(c.risk_score)}</td>
                        <td className="num"><Pill tone={c.risk_band}>{label(c.risk_band)}</Pill></td>
                      </tr>
                    ))}</tbody>
                  </table></div>
                )}
              </section>
            </div>
            {insights.length > 1 && (
              <div className="grid-3">{insights.slice(1, 4).map((f) => <InsightPanel key={f.key} f={f} total={total} />)}</div>
            )}
          </>
        )
      })()}
    </Status>
  )
}

function InsightPanel({ f, total }: { f: NonNullable<DatasetResult['insights']>[number]; total: number }) {
  const max = Math.max(...f.segments.map((x) => x.churn_rate_pct), 1)
  return (
    <section className="panel">
      <div className="panel-head"><h2>By {f.name}</h2><span className="sub">Churn rate</span></div>
      <div className="bars">
        {f.segments.slice(0, 10).map((x, i) => (
          <div className={x.customers < total * 0.01 ? 'bar few' : 'bar'} key={x.segment}
            title={`${fmtNum(x.churned)} of ${fmtNum(x.customers)} customers left`}>
            <span className="name">{x.segment}</span>
            <span className="track"><span className="fill" style={{ width: `${(x.churn_rate_pct / max) * 100}%`, ['--i' as string]: i }} /></span>
            <span className="val">{fmtPct(x.churn_rate_pct)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

const PAGE = 50

export function DatasetCustomers({ id }: { id: string }) {
  const go = useGo()
  const [band, setBand] = useState('high')
  const [left, setLeft] = useState('')
  const [page, setPage] = useState(0)
  const list = useLoad(async () => {
    let q = supabase.from('dataset_scores').select('*', { count: 'exact' }).eq('upload_id', id)
    if (band) q = q.eq('risk_band', band)
    // In a file whose outcome is known, many high-risk rows have already left: let the team hide them.
    if (left) q = q.eq('churned', left === 'yes')
    const res = await q.order('risk_score', { ascending: false }).order('row_id').range(page * PAGE, page * PAGE + PAGE - 1)
    if (res.error) throw new Error(res.error.message)
    return { rows: res.data as DatasetScore[], count: res.count ?? 0 }
  }, [id, band, left, page])
  const total = list.data?.count ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))
  const open = (r: DatasetScore) => go(customerLink(id, r.row_id))
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, r: DatasetScore) => {
    if (e.key === 'Enter') { e.preventDefault(); open(r); return }
    if (!['ArrowDown', 'ArrowUp'].includes(e.key)) return
    e.preventDefault()
    const sib = e.key === 'ArrowDown' ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling
    ;(sib as HTMLElement | null)?.focus()
  }
  return (
    <>
      <div className="filters">
        <Select label="Risk band" value={band} onChange={(v) => { setBand(v); setPage(0) }}
          options={[{ value: '', label: 'All' }, ...RISK_BANDS.map((b) => ({ value: b, label: label(b) }))]} />
        <Select label="Customers" value={left} onChange={(v) => { setLeft(v); setPage(0) }}
          options={[{ value: '', label: 'All' }, { value: 'no', label: 'Not left yet' }, { value: 'yes', label: 'Already left' }]} />
      </div>
      <section className="panel">
        <Status loading={list.loading && !list.data} error={list.error}>
          <div className={`results${list.loading ? ' is-loading' : ''}`} aria-busy={list.loading}>
            <p className="muted count-line" role="status">{fmtNum(total)} customers · page {page + 1} of {pages}</p>
            {!list.data?.rows.length ? <p className="empty-line">No customers in this band.</p> : (
              <Table>
                <thead><tr><th>Customer</th><th className="num">Risk</th><th>Band</th><th>Reasons</th><th>Left?</th></tr></thead>
                <tbody className="rows-in" key={`${band}-${page}`}>
                  {list.data.rows.map((r, i) => (
                    <tr key={r.row_id} className="clickable" style={{ ['--i' as string]: i }} tabIndex={0}
                      onClick={(_e: MouseEvent<HTMLTableRowElement>) => open(r)} onKeyDown={(e) => onKey(e, r)}>
                      <td className="id">{r.row_id}</td>
                      <td className="num">{fmtRisk(r.risk_score)}</td>
                      <td><Pill tone={r.risk_band}>{label(r.risk_band)}</Pill></td>
                      <td className="wrap">{[r.reason_1, r.reason_2, r.reason_3].filter(Boolean).join(' · ') || '–'}</td>
                      <td>{r.churned == null ? '–' : r.churned ? 'Yes' : 'No'}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </div>
          <div className="filters">
            <button className="plain" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="plain" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </Status>
      </section>
    </>
  )
}
