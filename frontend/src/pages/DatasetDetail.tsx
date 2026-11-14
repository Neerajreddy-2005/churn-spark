// Mode 2 dataset: confirm the columns (review), then exploration, model card, insights and at-risk list.
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table } from '../components'
import { Explain, Pill, useToast } from '../ui'
import { Select } from '../Select'
import { fmtDate, fmtNum, fmtPct, fmtRisk, label } from '../format'
import type { Dataset, DatasetResult, DatasetScore, ProfileColumn, Suggestions, UploadStatus } from '../types'

interface Loaded { ds: Dataset; status: UploadStatus; reason: string | null; runId: string | null }

const PAGE = 25
const num = (x: number | null | undefined) =>
  x == null ? '–' : Math.abs(x) >= 1000 || Number.isInteger(x) ? Math.round(x).toLocaleString('en-US') : x.toFixed(2)

export default function DatasetDetail() {
  const { uploadId = '' } = useParams()
  const loaded = useLoad<Loaded>(async () => {
    const [ds, up] = await Promise.all([
      supabase.from('datasets').select('*').eq('upload_id', uploadId).maybeSingle(),
      supabase.from('uploads').select('status,status_reason,run_id').eq('id', uploadId).maybeSingle(),
    ])
    const d = must(ds) as Dataset | null
    const u = must(up) as { status: UploadStatus; status_reason: string | null; run_id: string | null } | null
    if (!d || !u) throw new Error('Dataset not found.')
    return { ds: d, status: u.status, reason: u.status_reason, runId: u.run_id }
  }, [uploadId])
  const { reload } = loaded
  const busy = loaded.data?.status === 'queued' || loaded.data?.status === 'processing'

  useEffect(() => {
    if (!busy) return
    const t = setInterval(reload, 8000)
    return () => clearInterval(t)
  }, [busy, reload])

  const d = loaded.data
  return (
    <div className="page rise">
      <PageHead back={{ to: '/datasets', label: 'Datasets' }} title={d?.ds.name ?? 'Dataset'}
        meta={d && <>
          <Pill tone={d.status} dot={busy}>{label(d.status)}</Pill>{' '}
          {fmtNum(d.ds.profile?.rows)} rows · {fmtNum(d.ds.profile?.columns.length)} columns
          {d.ds.source_info?.sheet ? <> · sheet “{d.ds.source_info.sheet}”</> : null} · uploaded {fmtDate(d.ds.created_at)}
          {d.runId && <> · <Link to={`/runs/${encodeURIComponent(d.runId)}`}>see its checks</Link></>}
        </>} />
      <Status loading={loaded.loading && !d} error={loaded.error}>
        {d && <Body d={d} onChange={reload} />}
      </Status>
    </div>
  )
}

function Body({ d, onChange }: { d: Loaded; onChange: () => void }) {
  const { ds, status } = d
  const profile = ds.profile
  const [editing, setEditing] = useState(false)
  if (!profile) return <p className="empty-line">This dataset has no profile yet.</p>
  const canChange = (status === 'done' || status === 'rejected') && ds.suggestions
  const name = (k?: string | null) => profile.columns.find((c) => c.key === k)?.name ?? '–'
  const r = ds.review
  return (
    <>
      {(status === 'needs_review' || (editing && canChange)) && ds.suggestions && (
        <Review ds={ds} s={ds.suggestions} onDone={() => { setEditing(false); onChange() }}
          onCancel={editing ? () => setEditing(false) : undefined} />
      )}
      {canChange && !editing && (
        <p className="note-box">
          {r?.explore_only ? 'Exploration only: no model was trained. '
            : r?.target ? <>{r.auto ? 'Columns chosen automatically: ' : 'Columns: '}predicting {name(r.target)} = {r.positive},
              ID {r.id ? name(r.id) : 'row number'}, {r.features?.length ?? 0} columns offered to the model. </> : null}
          <button type="button" className="linkish" onClick={() => setEditing(true)}>Change columns</button>
        </p>
      )}
      {(status === 'queued' || status === 'processing') && (
        <section className="panel"><p className="empty-line">
          {ds.review?.explore_only ? 'Finishing the exploration…' : 'Training the model on this file. This takes about a minute; the page updates by itself.'}
        </p></section>
      )}
      {status === 'rejected' && (
        <section className="panel">
          <div className="panel-head"><h2>Not trained</h2></div>
          <p className="error" role="alert">{d.reason}</p>
          {ds.checks && <Checks checks={ds.checks} />}
        </section>
      )}
      {status === 'done' && ds.result && !ds.result.explore_only && <Model ds={ds} r={ds.result} />}
      {status === 'done' && ds.result?.insights && <Insights r={ds.result} />}
      <Exploration columns={profile.columns} s={ds.suggestions} />
      {status === 'done' && ds.result?.metrics && <AtRisk uploadId={ds.upload_id} />}
    </>
  )
}

// ---------------------------------------------------------------- review
function Review({ ds, s, onDone, onCancel }: { ds: Dataset; s: Suggestions; onDone: () => void; onCancel?: () => void }) {
  // Start from the columns used last time when changing them, else from the suggestions.
  const prev = ds.review?.target ? ds.review : null
  const toast = useToast()
  const cols = ds.profile!.columns
  const rows = ds.profile!.rows
  const byKey = useMemo(() => Object.fromEntries(cols.map((c) => [c.key, c])), [cols])
  const binary = cols.filter((c) => c.distinct === 2 && c.blank === 0 && !c.values_hidden)
  const unique = cols.filter((c) => c.distinct === rows && c.blank === 0)
  const [target, setTarget] = useState(prev?.target ?? s.target ?? binary[0]?.key ?? '')
  const [positive, setPositive] = useState(prev?.positive ?? s.positive ?? '')
  const [id, setId] = useState(prev ? prev.id ?? '' : s.id ?? '')
  const [features, setFeatures] = useState<Set<string>>(new Set(prev?.features ?? s.features))
  const [sending, setSending] = useState(false)

  const values = byKey[target]?.top.map(([v]) => v) ?? []
  useEffect(() => {
    if (!values.includes(positive)) setPositive(target === s.target && s.positive ? s.positive : values[0] ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  // Suggested columns first, the ones left out (with their reasons) at the end.
  // ID and personal columns (values hidden) are never offered: the database refuses them as features.
  const candidates = cols.filter((c) => c.key !== target && c.key !== id && !c.values_hidden)
    .sort((a, b) => Number(Boolean(s.exclude[a.key])) - Number(Boolean(s.exclude[b.key])))
  const chosen = candidates.filter((c) => features.has(c.key))
  const toggle = (k: string) => setFeatures((f) => {
    const n = new Set(f)
    if (n.has(k)) n.delete(k)
    else n.add(k)
    return n
  })

  const send = async (review: object) => {
    setSending(true)
    const { error } = await supabase.rpc('submit_dataset_review', { p_upload: ds.upload_id, p_review: review })
    setSending(false)
    if (error) toast.show(`Couldn't start: ${error.message}`, 'error')
    else onDone()
  }
  const train = () => void send({ target, positive, id: id || null, features: chosen.map((c) => c.key) })

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Confirm the columns</h2>
        <span className="sub">Suggested from the file; change anything that is wrong.</span>
      </div>
      {!s.is_churn_data && <p className="note-box">{s.target_note}</p>}
      <div className="review-grid">
        <Select field label="Churn column (what we predict)" value={target} onChange={setTarget}
          options={binary.length ? binary.map((c) => ({ value: c.key, label: c.name })) : [{ value: '', label: 'No column with two values' }]} />
        <Select field label="Value that means the customer left" value={positive} onChange={setPositive}
          options={values.map((v) => ({ value: v, label: v }))} disabled={!values.length} />
        <Select field label="Customer ID" value={id} onChange={setId}
          options={[{ value: '', label: 'Row number' }, ...unique.map((c) => ({ value: c.key, label: c.name }))]} />
      </div>
      {target === s.target && s.target && <p className="muted small">{s.target_note} {s.id_note}</p>}
      <fieldset className="feature-pick">
        <legend>Columns the model may use <span className="muted">({chosen.length} of {candidates.length})</span></legend>
        <div className="feature-grid">
          {candidates.map((c) => {
            const ex = s.exclude[c.key]
            return (
              <label key={c.key} className={ex ? 'excluded' : undefined}>
                <input type="checkbox" checked={features.has(c.key)} onChange={() => toggle(c.key)} />
                <span><strong>{c.name}</strong>{ex && <em>{label(ex.kind)}: {ex.reason}</em>}</span>
              </label>
            )
          })}
        </div>
      </fieldset>
      <div className="actions">
        <button type="button" disabled={sending || !target || !positive || !chosen.length} onClick={train}>
          {sending ? 'Starting…' : prev ? 'Retrain with these columns' : 'Train the model'}
        </button>
        <button type="button" className="plain" disabled={sending} onClick={() => void send({ explore_only: true })}>
          Exploration only
        </button>
        {onCancel && <button type="button" className="plain" disabled={sending} onClick={onCancel}>Cancel</button>}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- model card
function Model({ ds, r }: { ds: Dataset; r: DatasetResult }) {
  const m = r.metrics!
  const top20 = m.capture.top_20
  const name = (k?: string | null) => ds.profile!.columns.find((c) => c.key === k)?.name ?? k ?? '–'
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Model trained on this file</h2>
        <span className="sub">Predicting {name(ds.review?.target)} = {ds.review?.positive}. Measured on {fmtNum(r.rows?.test)} customers held back from training.</span>
      </div>
      {r.suspicious && (
        <p className="error" role="alert">Suspiciously perfect (AUC {m.auc_roc_test.toFixed(3)}). Some column probably reveals the outcome; check the strongest columns below before trusting these scores.</p>
      )}
      <div className="kpis" style={{ ['--n' as string]: 4 }}>
        <div className="kpi"><span className="label">Holdout AUC</span>
          <span className="value"><Explain title="Holdout AUC" body={<>How well the model ranks churners above stayers, on customers it never saw: 0.5 is a coin flip, 1.0 is perfect. Training AUC {m.auc_roc_train.toFixed(3)}; a big gap would mean overfitting.</>}>{m.auc_roc_test.toFixed(3)}</Explain></span>
          <span className="note">train {m.auc_roc_train.toFixed(3)}</span></div>
        <div className="kpi"><span className="label">Churn rate</span>
          <span className="value">{fmtPct(r.base_churn_rate_pct)}</span><span className="note">{fmtNum(r.rows?.total)} customers</span></div>
        <div className="kpi"><span className="label">Riskiest 20% reach</span>
          <span className="value"><Explain title="Riskiest 20% reach" body={<>If the team contacts the 20% of held-back customers the model ranks riskiest, they reach {fmtPct(top20?.capture_rate_pct)} of those who left. A random 20% reaches 20%. The best any ranking could do here is {fmtPct(top20?.max_possible_pct)}, because {fmtPct(r.base_churn_rate_pct)} of customers left.</>}>{fmtPct(top20?.capture_rate_pct)}</Explain></span>
          <span className="note">of churners · best possible {fmtPct(top20?.max_possible_pct)}</span></div>
        <div className="kpi"><span className="label">Columns used</span>
          <span className="value">{fmtNum(ds.review?.features?.length ? ds.review.features.length - (r.leakage?.removed.length ?? 0) : null)}</span>
          <span className="note">{r.leakage?.removed.length ? `${r.leakage.removed.length} removed as leakage` : 'none removed'}</span></div>
      </div>
      {!!r.leakage?.removed.length && (
        <p className="note-box">Removed automatically because each one alone predicts the outcome almost perfectly, which means it is recorded after or because the customer left: {r.leakage.removed.map((x) => `${x.name} (AUC ${x.single_column_auc.toFixed(3)})`).join(', ')}.</p>
      )}
      {!!r.leakage?.warned.length && (
        <p className="note-box warn">Very strong on their own; make sure they are known before a customer leaves, or the scores flatter the model: {r.leakage.warned.map((x) => `${x.name} (AUC ${x.single_column_auc.toFixed(3)})`).join(', ')}.</p>
      )}
      {!!r.drivers?.length && (
        <div className="drivers">
          <h3>What moves the risk</h3>
          <ul>
            {r.drivers.slice(0, 8).map((x) => (
              <li key={x.feature}>
                <span className={x.coefficient > 0 ? 'up' : 'down'}>{x.coefficient > 0 ? '▲' : '▼'}</span>
                <strong>{x.feature}{x.value ? ` = ${x.value}` : ''}</strong>
                <span className="muted">{x.coefficient > 0 ? 'raises' : 'lowers'} risk{x.value ? '' : ' as it grows'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function Checks({ checks }: { checks: NonNullable<Dataset['checks']> }) {
  return (
    <Table>
      <thead><tr><th>Check</th><th>Description</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>{checks.map((c) => (
        <tr key={c.rule_id}><td>{c.rule_id}</td><td>{c.description}</td><td><Pill tone={c.status}>{label(c.status)}</Pill></td><td>{c.detail}</td></tr>
      ))}</tbody>
    </Table>
  )
}

// ---------------------------------------------------------------- insights
function Insights({ r }: { r: DatasetResult }) {
  const feats = (r.insights ?? []).filter((f) => f.spread_pct_points > 0).slice(0, 6)
  const total = r.rows?.total ?? 0
  if (!feats.length) return null
  return (
    <section className="panel">
      <div className="panel-head"><h2>Where churn differs most</h2><span className="sub">Churn rate by group, biggest differences first. Faded: under 1% of customers</span></div>
      <div className="insight-grid">
        {feats.map((f) => {
          const max = Math.max(...f.segments.map((s) => s.churn_rate_pct), 1)
          return (
            <div key={f.key} className="insight">
              <h3>{f.name} <span className="muted">{f.spread_pct_points.toFixed(1)} pts apart</span></h3>
              <div className="bars">
                {f.segments.slice(0, 12).map((s, i) => (
                  // groups under 1% of customers are faded: a 100% rate over 3 customers is not a finding
                  <div className={s.customers < total * 0.01 ? 'bar few' : 'bar'} key={s.segment}
                    title={`${fmtNum(s.churned)} of ${fmtNum(s.customers)} customers left${s.customers < total * 0.01 ? ' (small group)' : ''}`}>
                    <span className="name">{s.segment}</span>
                    <span className="track"><span className="fill" style={{ width: `${(s.churn_rate_pct / max) * 100}%`, ['--i' as string]: i }} /></span>
                    <span className="val">{fmtPct(s.churn_rate_pct)}</span>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- exploration
const FIRST_COLUMNS = 8

function Exploration({ columns, s }: { columns: ProfileColumn[]; s: Suggestions | null }) {
  const [all, setAll] = useState(false)
  const shown = all ? columns : columns.slice(0, FIRST_COLUMNS)
  return (
    <section className="panel">
      <div className="panel-head"><h2>Data exploration</h2><span className="sub">Every column: type, gaps and spread of values</span></div>
      <div className="explore-grid">
        {shown.map((c) => <ColumnCard key={c.key} c={c} note={s?.exclude[c.key]} />)}
      </div>
      {columns.length > FIRST_COLUMNS && (
        <div><button type="button" className="plain" onClick={() => setAll((a) => !a)}>
          {all ? 'Show fewer columns' : `Show all ${columns.length} columns`}
        </button></div>
      )}
    </section>
  )
}

function ColumnCard({ c, note }: { c: ProfileColumn; note?: { kind: string; reason: string } }) {
  const hist = c.histogram ?? []
  const hmax = Math.max(...hist.map((h) => h.count), 1)
  const tmax = Math.max(...c.top.map(([, n]) => n), 1)
  return (
    <div className="col-card">
      <div className="col-head"><strong title={c.name}>{c.name}</strong><Pill tone="neutral">{c.kind}</Pill></div>
      <div className="col-meta muted">{fmtNum(c.distinct)} distinct · {c.missing_pct ? `${c.missing_pct}% missing` : 'no gaps'}</div>
      {c.values_hidden ? (
        <p className="muted small">Values hidden: an ID or personal column.</p>
      ) : c.kind === 'number' && c.integer && c.distinct <= 10 ? (
        // a few whole-number values (0/1 flags, 1-4 products): one bar per value, in value order
        <ul className="top-values">
          {[...c.top].sort((a, b) => Number(a[0]) - Number(b[0])).map(([v, n]) => (
            <li key={v}><span className="v">{v}</span><span className="t"><span style={{ width: `${(n / tmax) * 100}%` }} /></span><span className="n">{fmtNum(n)}</span></li>
          ))}
        </ul>
      ) : c.kind === 'number' && hist.length ? (
        <>
          <div className="spark" aria-label={`Histogram of ${c.name}`}>
            {hist.map((h, i) => <span key={i} style={{ height: `${(h.count / hmax) * 100}%` }} title={`${num(h.from)} to ${num(h.to)}: ${fmtNum(h.count)}`} />)}
          </div>
          <div className="col-range muted"><span>{num(c.min)}</span><span>median {num(c.median)}</span><span>{num(c.max)}</span></div>
        </>
      ) : (
        <ul className="top-values">
          {c.top.slice(0, 5).map(([v, n]) => (
            <li key={v}><span className="v" title={v}>{v}</span><span className="t"><span style={{ width: `${(n / tmax) * 100}%` }} /></span><span className="n">{fmtNum(n)}</span></li>
          ))}
        </ul>
      )}
      {note && <p className="col-note">Not used by default · {label(note.kind)}</p>}
    </div>
  )
}

// ---------------------------------------------------------------- at-risk list
function AtRisk({ uploadId }: { uploadId: string }) {
  const [limit, setLimit] = useState(PAGE)
  const scores = useLoad(async () => {
    const res = await supabase.from('dataset_scores').select('*', { count: 'exact' }).eq('upload_id', uploadId)
      .order('risk_score', { ascending: false }).order('row_id').limit(limit)
    if (res.error) throw new Error(res.error.message)
    return { rows: res.data as DatasetScore[], total: res.count ?? 0 }
  }, [uploadId, limit])
  return (
    <section className="panel">
      <div className="panel-head"><h2>Most at risk</h2><span className="sub">Riskiest customers first, with the three biggest reasons</span></div>
      <Status loading={scores.loading && !scores.data} error={scores.error}>
        {scores.data && (
          <>
            <Table>
              <thead><tr><th>Customer</th><th className="num">Risk</th><th><span className="sr-only">Band</span></th><th>Reasons</th><th>Left?</th></tr></thead>
              <tbody>{scores.data.rows.map((x) => (
                <tr key={x.row_id}>
                  <td className="id">{x.row_id}</td>
                  <td className="num">{fmtRisk(x.risk_score)}</td>
                  <td><Pill tone={x.risk_band}>{label(x.risk_band)}</Pill></td>
                  <td className="wrap">{[x.reason_1, x.reason_2, x.reason_3].filter(Boolean).join(' · ') || '–'}</td>
                  <td>{x.churned == null ? '–' : x.churned ? 'Yes' : 'No'}</td>
                </tr>
              ))}</tbody>
            </Table>
            {scores.data.rows.length < scores.data.total && (
              <button type="button" className="plain" onClick={() => setLimit((l) => l + PAGE * 2)}>
                Show more ({fmtNum(scores.data.total - scores.data.rows.length)} left)
              </button>
            )}
          </>
        )}
      </Status>
    </section>
  )
}
