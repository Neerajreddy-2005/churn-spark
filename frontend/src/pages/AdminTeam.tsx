import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth'
import { callAdmin } from '../admin'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { EditName, PageHead, Status, Table, TempPassword } from '../components'
import { Pill } from '../ui'
import { label } from '../format'
import { AuditTable } from '../audit'
import { type AuditEntry, type Profile } from '../types'

interface Issued { email: string; password: string }
interface Created { temporary_password: string }

export default function AdminTeam() {
  const { profile } = useAuth()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [issued, setIssued] = useState<Issued | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const team = useLoad(
    async () => must(await supabase.from('profiles').select('*').order('display_name')) as Profile[],
    [],
  )
  // Only managers can read the audit log (RLS).
  const audit = useLoad(
    async () =>
      must(await supabase.from('audit_log').select('*').order('created_at', { ascending: false }).limit(50)) as AuditEntry[],
    [],
  )

  async function run(body: Record<string, unknown>) {
    setError(null)
    setBusy(true)
    try {
      const res = await callAdmin<Created>(body)
      team.reload()
      audit.reload()
      return res
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  async function onRename(p: Profile, name: string) {
    return (await run({ action: 'set_name', user_id: p.user_id, display_name: name })) !== undefined
  }

  async function onAdd(e: FormEvent) {
    e.preventDefault()
    const res = await run({ action: 'create_user', email, display_name: name, role: 'analyst' })
    if (res) {
      setIssued({ email, password: res.temporary_password })
      setName('')
      setEmail('')
    }
  }

  if (profile?.role !== 'manager') return <p className="error" role="alert">Not allowed.</p>

  return (
    <div className="page rise">
      <PageHead title="Team" meta="Add analysts, rename them and switch their access on or off." />
      {issued && <TempPassword email={issued.email} password={issued.password} onDone={() => setIssued(null)} />}
      {error && <p className="error" role="alert">{error}</p>}

      <section className="panel">
        <div className="panel-head"><h2>Add employee</h2></div>
        <form className="stack" onSubmit={onAdd}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} /></label>
          <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required maxLength={254} /></label>
          <p className="muted">New employees join as analysts. Managers are created by the platform admin.</p>
          <button type="submit" disabled={busy}>Add employee</button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>People</h2></div>
        <Status loading={team.loading} error={team.error}>
          <Table>
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Must change password</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {team.data?.map((p) => (
                <tr key={p.user_id}>
                  <td>
                    {p.user_id === profile?.user_id || p.role === 'analyst'
                      ? <EditName name={p.display_name} disabled={busy} onSave={(n) => onRename(p, n)} />
                      : (p.display_name ?? '-')}
                  </td>
                  <td>{p.email ?? '-'}</td>
                  <td><Pill tone="neutral">{label(p.role)}</Pill></td>
                  <td><Pill tone={p.is_active ? 'active' : 'inactive'}>{p.is_active ? 'Active' : 'Inactive'}</Pill></td>
                  <td>{p.must_change_password ? 'Yes' : 'No'}</td>
                  <td>
                    {p.user_id === profile?.user_id ? <span className="muted">(you)</span>
                      : p.role !== 'analyst' ? <span className="muted">manager</span> : (
                        <button className={p.is_active ? 'danger small' : 'plain small'} disabled={busy}
                          onClick={() => void run({ action: p.is_active ? 'deactivate_user' : 'reactivate_user', user_id: p.user_id })}>
                          {p.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Status>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Audit log</h2><span className="sub">Latest 50 entries</span></div>
        <Status loading={audit.loading} error={audit.error}>
          <AuditTable rows={audit.data ?? []} />
        </Status>
      </section>
    </div>
  )
}
