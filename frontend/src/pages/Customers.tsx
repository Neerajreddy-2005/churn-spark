import { useState, type KeyboardEvent, type MouseEvent } from 'react'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Select } from '../Select'
import { Pill } from '../ui'
import { useGo } from '../nav'
import { fmtMoney, fmtMonth, fmtNum, fmtRisk, label, fmtReason } from '../format'
import { RISK_BANDS, type CustomerScore, type SegmentAggregate } from '../types'

const PAGE = 50
const ALL = { value: '', label: 'All' }

interface Page {
  rows: CustomerScore[]
  count: number
}

export default function Customers() {
  const go = useGo()
  const [month, setMonth] = useState<string | null>(null)
  const [band, setBand] = useState('high')
  const [contract, setContract] = useState('')
  const [internet, setInternet] = useState('')
  const [tenure, setTenure] = useState('')
  const [page, setPage] = useState(0)

  // Months and filter options come from segment_aggregates (distinct, DB-derived).
  const meta = useLoad(
    async () =>
      must(
        await supabase
          .from('segment_aggregates')
          .select('snapshot_month, segment_type, segment')
          .order('snapshot_month', { ascending: false }),
      ) as Pick<SegmentAggregate, 'snapshot_month' | 'segment_type' | 'segment'>[],
    [],
  )
  const months = [...new Set(meta.data?.map((r) => r.snapshot_month) ?? [])]
  const selected = month ?? months[0] ?? null
  const opts = (type: string) =>
    [...new Set(meta.data?.filter((r) => r.snapshot_month === selected && r.segment_type === type).map((r) => r.segment) ?? [])].sort()

  const list = useLoad<Page | null>(async () => {
    if (!selected) return null
    let q = supabase.from('customer_scores').select('*', { count: 'exact' }).eq('snapshot_month', selected)
    if (band) q = q.eq('risk_band', band)
    if (contract) q = q.eq('contract', contract)
    if (internet) q = q.eq('internet_service', internet)
    if (tenure) q = q.eq('tenure_band', tenure)
    const res = await q
      .order('risk_score', { ascending: false })
      .order('customer_id')
      .range(page * PAGE, page * PAGE + PAGE - 1)
    if (res.error) throw new Error(res.error.message)
    return { rows: res.data as CustomerScore[], count: res.count ?? 0 }
  }, [selected, band, contract, internet, tenure, page])

  const total = list.data?.count ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  // Tag only the row being opened so the customer ID animates into the detail page.
  const open = (tr: HTMLElement, r: CustomerScore) => {
    const id = tr.querySelector<HTMLElement>('.id')
    if (id) id.style.viewTransitionName = 'customer-id'
    go(`/customers/${encodeURIComponent(r.customer_id)}?month=${r.snapshot_month}`)
  }

  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, r: CustomerScore) => {
    const tr = e.currentTarget
    if (e.key === 'Enter') {
      e.preventDefault()
      return open(tr, r)
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    e.preventDefault()
    const rows = Array.from(tr.parentElement?.querySelectorAll<HTMLElement>('tr.clickable') ?? [])
    const i = rows.indexOf(tr)
    const target =
      e.key === 'ArrowDown' ? rows[i + 1] : e.key === 'ArrowUp' ? rows[i - 1] : e.key === 'Home' ? rows[0] : rows[rows.length - 1]
    target?.focus()
  }

  return (
    <div className="page rise">
      <PageHead title="At-risk customers" meta="Customers ranked by churn risk for the month you pick." />
      <Status loading={meta.loading} error={meta.error}>
        {!selected ? (
          <section className="panel">
            <p className="empty-line">No scored customers yet. Upload a CSV and its customers appear here.</p>
          </section>
        ) : (
          <>
            <div className="filters">
              <Select label="Month" value={selected} onChange={(v) => { setMonth(v); setPage(0) }}
                options={months.map((m) => ({ value: m, label: fmtMonth(m) }))} />
              <Select label="Risk band" value={band} onChange={(v) => { setBand(v); setPage(0) }}
                options={[ALL, ...RISK_BANDS.map((b) => ({ value: b, label: label(b) }))]} />
              <Select label="Contract" value={contract} onChange={(v) => { setContract(v); setPage(0) }}
                options={[ALL, ...opts('contract').map((o) => ({ value: o, label: label(o) }))]} />
              <Select label="Internet service" value={internet} onChange={(v) => { setInternet(v); setPage(0) }}
                options={[ALL, ...opts('internet_service').map((o) => ({ value: o, label: label(o) }))]} />
              <Select label="Tenure band" value={tenure} onChange={(v) => { setTenure(v); setPage(0) }}
                options={[ALL, ...opts('tenure_band').map((o) => ({ value: o, label: `${o} months` }))]} />
            </div>
            <section className="panel">
              <Status loading={list.loading && !list.data} error={list.error}>
                <div className={`results${list.loading ? ' is-loading' : ''}`} aria-busy={list.loading}>
                <p className="muted count-line" role="status">{fmtNum(total)} customers · page {page + 1} of {pages}</p>
                <p className="muted" style={{ fontSize: 'var(--fs-small)' }}>Use ↑ ↓ to move, Enter to open</p>
                {!list.data?.rows.length ? (
                  <p className="empty-line">No customers match these filters. Widen a filter to see more.</p>
                ) : (
                  <Table>
                    <thead>
                      <tr>
                        <th>Customer</th><th className="num">Risk</th><th>Band</th><th>Top reason</th>
                        <th>Contract</th><th className="num">Tenure (months)</th><th className="num">Monthly charges</th>
                      </tr>
                    </thead>
                    <tbody className="rows-in" key={`${total}-${page}-${list.data.rows[0]?.customer_id}-${list.data.rows.at(-1)?.customer_id}`}>
                      {list.data.rows.map((r, i) => (
                        <tr
                          key={r.customer_id}
                          className="clickable"
                          style={{ ['--i' as string]: i }}
                          tabIndex={0}
                          onClick={(e: MouseEvent<HTMLTableRowElement>) => open(e.currentTarget, r)}
                          onKeyDown={(e) => onRowKey(e, r)}
                        >
                          <td className="id">{r.customer_id}</td>
                          <td className="num">{fmtRisk(r.risk_score)}</td>
                          <td>{r.risk_band ? <Pill tone={r.risk_band}>{label(r.risk_band)}</Pill> : '–'}</td>
                          <td>{fmtReason(r.reason_1)}</td>
                          <td>{r.contract}</td>
                          <td className="num">{fmtNum(r.tenure_months)}</td>
                          <td className="num">{fmtMoney(r.monthly_charges)}</td>
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
        )}
      </Status>
    </div>
  )
}
