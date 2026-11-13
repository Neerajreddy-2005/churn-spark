import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { Status } from '../components'
import { Select } from '../Select'
import { Pill, useToast } from '../ui'
import { IconCheck, IconPulse, IconRisk } from '../icons'
import { reason, shortId, fmtDate, fmtMoney, fmtMonth, fmtRisk, label } from '../format'
import { ACTION_TYPES, OUTCOMES, type ActionType, type CustomerScore, type DatasetScore, type Outcome, type RetentionAction } from '../types'

interface Detail {
  history: CustomerScore[] // every month this customer was scored, newest first (Telco)
  actions: RetentionAction[]
  dataset: { score: DatasetScore; name: string; month: string } | null // a customer of another dataset
}

export default function CustomerDetail() {
  const { customerId = '' } = useParams()
  const [params] = useSearchParams()
  const month = params.get('month')
  const datasetId = params.get('dataset')
  const { session, profile } = useAuth()
  const userId = session?.user.id
  const toast = useToast()

  const d = useLoad<Detail>(async () => {
    if (datasetId) {
      const [sc, ds, a] = await Promise.all([
        supabase.from('dataset_scores').select('*').eq('upload_id', datasetId).eq('row_id', customerId).maybeSingle(),
        supabase.from('datasets').select('name,uploads(snapshot_month)').eq('upload_id', datasetId).maybeSingle(),
        supabase.from('retention_actions').select('*').eq('dataset_id', datasetId).eq('customer_id', customerId)
          .order('created_at', { ascending: false }),
      ])
      const score = must(sc) as DatasetScore | null
      const meta = must(ds) as unknown as { name: string; uploads: { snapshot_month: string } | null } | null
      return {
        history: [], actions: must(a) as RetentionAction[],
        dataset: score && meta?.uploads ? { score, name: meta.name, month: meta.uploads.snapshot_month } : null,
      }
    }
    const [h, a] = await Promise.all([
      supabase.from('customer_scores').select('*').eq('customer_id', customerId).order('snapshot_month', { ascending: false }),
      supabase.from('retention_actions').select('*').eq('customer_id', customerId).is('dataset_id', null)
        .order('created_at', { ascending: false }),
    ])
    return { history: must(h) as CustomerScore[], actions: must(a) as RetentionAction[], dataset: null }
  }, [customerId, datasetId])

  const [actionType, setActionType] = useState<ActionType>('call')
  const [note, setNote] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  const gen = d.data?.dataset ?? null
  // Telco: the score for the month asked for. Another dataset: its one score, in the same shape.
  const s: CustomerScore | null = gen
    ? { org_id: profile?.org_id ?? '', snapshot_month: gen.month, customer_id: gen.score.row_id, risk_score: gen.score.risk_score,
        risk_band: gen.score.risk_band, reason_1: gen.score.reason_1, reason_2: gen.score.reason_2, reason_3: gen.score.reason_3,
        contract: null, internet_service: null, payment_method: null, tenure_months: null, tenure_band: null,
        monthly_charges: null, churned: gen.score.churned, model_version: 'generic-lr', scored_at: null } as CustomerScore
    : d.data ? (month ? d.data.history.find((h) => h.snapshot_month === month) : d.data.history[0]) ?? null : null

  // Logging is undoable: nothing is written until the toast's 5-second window ends.
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!s || !profile || !userId) return
    if (note.length > 500) return setFormError('The note must be 500 characters or fewer.')
    setFormError(null)
    const row = {
      org_id: profile.org_id,
      customer_id: s.customer_id,
      snapshot_month: s.snapshot_month,
      dataset_id: datasetId,
      action_type: actionType,
      note: note.trim() || null,
      created_by: userId,
    }
    setNote('')
    toast.deferred({
      message: `${label(actionType)} logged for ${s.customer_id}`,
      done: 'Saved to the activity.',
      commit: async () => {
        const { error } = await supabase.from('retention_actions').insert(row)
        return error ? `Couldn't save the action: ${error.message}` : null
      },
      onCommitted: d.reload,
    })
  }

  async function setOutcome(id: string, outcome: Outcome) {
    const { data, error } = await supabase
      .from('retention_actions')
      .update({ outcome, updated_at: new Date().toISOString() })
      .eq('id', id)
      .select('id')
    if (error) toast.show(`Couldn't save the outcome: ${error.message}`, 'error')
    else if (!data?.length) toast.show('Outcome not saved. You can only change your own actions.', 'error')
    else toast.show(`Outcome set to ${label(outcome).toLowerCase()}.`)
    d.reload()
  }

  return (
    <div className="page rise">
      <Link to={datasetId ? `/customers?data=ds:${datasetId}` : '/customers'} className="back-link">&larr; At-risk customers</Link>
      <div className="page-head">
        <div>
          <h1><span style={{ viewTransitionName: 'customer-id', display: 'inline-block' }}>{customerId}</span></h1>
          {s && (
            <div className="meta">
              {gen ? <>{gen.name} · <Link to={`/datasets/${datasetId}`}>data and model details</Link></>
                : [s.contract, s.internet_service, s.tenure_months != null ? `${s.tenure_months} months as a customer` : null,
                  s.monthly_charges != null ? `${fmtMoney(s.monthly_charges)} a month` : null, fmtMonth(s.snapshot_month)]
                  .filter(Boolean).join(' · ')}
            </div>
          )}
        </div>
      </div>

      <Status loading={d.loading && !d.data} error={d.error}>
        {d.data && !s && (
          <section className="panel"><p>No score found for this customer{month ? ` in ${fmtMonth(month)}` : ''}.</p></section>
        )}
        {s && d.data && (
          <div className="grid-2">
            <div className="page" style={{ gap: 20 }}>
              <section className="panel">
                <div className="panel-head"><h2>Chance of leaving</h2>{s.risk_band && <Pill tone={s.risk_band}>{label(s.risk_band)} risk</Pill>}</div>
                <div className="meter">
                  <span className="big">{fmtRisk(s.risk_score)}</span>
                  <span className="rail" role="img" aria-label={`Risk ${fmtRisk(s.risk_score)}`}>
                    <i className={s.risk_band ?? ''} style={{ width: `${(s.risk_score ?? 0) * 100}%` }} />
                  </span>
                  <span className="muted" style={{ fontSize: 12.5 }}>
                    {gen ? 'Scored by the model trained on this dataset' : <>Scored {fmtDate(s.scored_at)}{s.model_version ? ` · model ${shortId(s.model_version)}` : ''}</>}
                  </span>
                </div>
              </section>
              <section className="panel">
                <div className="panel-head"><h2>Why the model flags them</h2><span className="sub">Strongest first</span></div>
                <ol className="reasons">
                  {[s.reason_1, s.reason_2, s.reason_3].map((x) => (gen ? (x ? { k: '', v: x } : null) : reason(x))).filter((r) => r !== null).map((r, i) => (
                    <li key={i}>{r.k && <span className="k">{r.k}: </span>}<b>{r.v}</b></li>
                  ))}
                </ol>
              </section>
              <section className="panel">
                <div className="panel-head"><h2>Details</h2></div>
                <div className="tablewrap">
                  <table>
                    {gen ? (
                      <tbody>
                        <tr><th scope="row">Dataset</th><td>{gen.name}</td></tr>
                        <tr><th scope="row">Left in the data</th><td>{gen.score.churned == null ? '–' : gen.score.churned ? 'Yes' : 'No'}</td></tr>
                        <tr><th scope="row">Used to train the model</th><td>{gen.score.in_training_set ? 'Yes (its score is in-sample)' : 'No (held back)'}</td></tr>
                      </tbody>
                    ) : <tbody>
                      <tr><th scope="row">Contract</th><td>{s.contract ?? '–'}</td></tr>
                      <tr><th scope="row">Internet service</th><td>{s.internet_service ?? '–'}</td></tr>
                      <tr><th scope="row">Payment method</th><td>{s.payment_method ?? '–'}</td></tr>
                      <tr><th scope="row">Tenure</th><td>{s.tenure_months ?? '–'} months ({s.tenure_band ?? '–'})</td></tr>
                      <tr><th scope="row">Monthly charges</th><td>{fmtMoney(s.monthly_charges)}</td></tr>
                      <tr><th scope="row">Churned in the data</th><td>{s.churned == null ? '–' : s.churned ? 'Yes' : 'No'}</td></tr>
                    </tbody>}
                  </table>
                </div>
              </section>
            </div>

            <div className="page" style={{ gap: 20 }}>
              <section className="panel">
                <div className="panel-head"><h2>Log a retention action</h2><span className="sub">You can undo for 5 seconds</span></div>
                <form className="stack" style={{ maxWidth: 'none' }} onSubmit={onSubmit}>
                  <Select field label="What did you do?" value={actionType} onChange={(v) => setActionType(v as ActionType)}
                    options={ACTION_TYPES.map((a) => ({ value: a, label: label(a) }))} />
                  <label>
                    Note <span className="hint" id="note-hint">Optional · {note.length}/500</span>
                    <textarea value={note} maxLength={500} rows={3} aria-describedby="note-hint"
                      placeholder="e.g. Offered a one-year plan, will call back Friday" onChange={(e) => setNote(e.target.value)} />
                  </label>
                  <div><button type="submit">Log action</button></div>
                </form>
                {formError && <p className="error" role="alert">{formError}</p>}
              </section>
              <section className="panel">
                <div className="panel-head"><h2>Activity</h2><span className="sub">Scores and actions, newest first</span></div>
                <Timeline history={d.data.history} actions={d.data.actions} userId={userId} onOutcome={setOutcome} />
              </section>
            </div>
          </div>
        )}
      </Status>
    </div>
  )
}

interface Event { key: string; at: string; node: 'score' | 'risk' | 'action'; icon: ReactNode; what: ReactNode; body?: ReactNode }

function Timeline({ history, actions, userId, onOutcome }: {
  history: CustomerScore[]; actions: RetentionAction[]; userId: string | undefined
  onOutcome: (id: string, o: Outcome) => void
}) {
  const events: Event[] = [
    ...actions.map((a): Event => ({
      key: `a-${a.id}`, at: a.created_at, node: 'action', icon: <IconCheck />,
      what: <>{label(a.action_type)} <span className="muted" style={{ fontWeight: 500 }}>by {a.created_by === userId ? 'you' : 'a teammate'}</span></>,
      body: (
        <div style={{ display: 'grid', gap: 8, marginTop: 4 }}>
          {a.note && <span>{a.note}</span>}
          <span style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            Outcome:
            {a.created_by === userId && a.outcome === 'pending' ? (
              <Select small hideLabel label="Outcome" value={a.outcome} onChange={(v) => onOutcome(a.id, v as Outcome)}
                options={OUTCOMES.map((o) => ({ value: o, label: label(o) }))} />
            ) : <Pill tone={a.outcome}>{label(a.outcome)}</Pill>}
          </span>
        </div>
      ),
    })),
    ...history.map((h, i): Event => {
      const prev = history[i + 1]
      const delta = prev?.risk_score != null && h.risk_score != null ? (h.risk_score - prev.risk_score) * 100 : null
      return {
        key: `s-${h.snapshot_month}`, at: h.scored_at ?? `${h.snapshot_month}-01`, node: h.risk_band === 'high' ? 'risk' : 'score',
        icon: h.risk_band === 'high' ? <IconRisk /> : <IconPulse />,
        what: <>Scored {fmtRisk(h.risk_score)} for {fmtMonth(h.snapshot_month)}</>,
        body: delta == null ? (h.risk_band ? `${label(h.risk_band)} risk` : undefined)
          : `${label(h.risk_band)} risk · ${delta >= 0 ? 'up' : 'down'} ${Math.abs(delta).toFixed(2)} points from ${fmtMonth(prev!.snapshot_month)}`,
      }
    }),
  ].sort((x, y) => y.at.localeCompare(x.at))

  if (!events.length) return <p className="empty-line">Nothing yet. Actions you log appear here.</p>
  return (
    <ol className="timeline">
      {events.map((e) => (
        <li key={e.key}>
          <span className={`node ${e.node}`} aria-hidden="true">{e.icon}</span>
          <div>
            <div className="what">{e.what}</div>
            <div className="when">{fmtDate(e.at)}</div>
            {e.body && <div className="body">{e.body}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}
