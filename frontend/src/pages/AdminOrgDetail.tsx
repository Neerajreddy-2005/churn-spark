import { useState, type FormEvent } from 'react'
import { Select } from '../Select'
import { useParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { callAdmin } from '../admin'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { EditName, PageHead, Status, Table, TempPassword } from '../components'
import { Pill } from '../ui'
import { label } from '../format'
import { ROLES, type Organisation, type Profile } from '../types'

interface Issued { email: string; password: string }
interface Created { temporary_password: string }

export default function AdminOrgDetail() {
  const { isPlatformAdmin } = useAuth()
  const { orgId = '' } = useParams()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<string>('analyst')
  const [issued, setIssued] = useState<Issued | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const org = useLoad(
    async () => must(await supabase.from('organisations').select('id, name').eq('id', orgId).maybeSingle()) as Organisation | null,
    [orgId],
  )
  const members = useLoad(
    async () => must(await supabase.from('profiles').select('*').eq('org_id', orgId).order('display_name')) as Profile[],
    [orgId],
  )

  async function run(body: Record<string, unknown>) {
    setError(null)
    setBusy(true)
    try {
      const res = await callAdmin<Created>({ ...body, org_id: orgId })
      members.reload()
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
    const res = await run({ action: 'create_user', email, display_name: name, role })
    if (res) {
      setIssued({ email, password: res.temporary_password })
      setName('')
      setEmail('')
      setRole('analyst')
    }
  }

  async function onReset(p: Profile) {
    if (!window.confirm(`Break-glass reset for ${p.display_name ?? p.email}? Their current password stops working and they will be forced to change the temporary one at next sign-in. Prefer asking them to use "Forgot password?" (email reset) instead.`)) return
    const res = await run({ action: 'reset_password', user_id: p.user_id })
    if (res) setIssued({ email: p.email ?? p.display_name ?? '', password: res.temporary_password })
  }

  if (!isPlatformAdmin) return <p className="error" role="alert">Not allowed.</p>

  return (
    <div className="page rise">
      <PageHead title={org.data ? org.data.name : 'Organisation'} back={{ to: '/admin/organisations', label: 'Organisations' }} />
      {org.error && <p className="error" role="alert">Something went wrong: {org.error}</p>}
      {!org.loading && !org.error && !org.data && <p className="error" role="alert">Organisation not found.</p>}
      {issued && <TempPassword email={issued.email} password={issued.password} onDone={() => setIssued(null)} />}
      {error && <p className="error" role="alert">{error}</p>}

      <section className="panel">
        <div className="panel-head"><h2>Add member</h2></div>
        <form className="stack" onSubmit={onAdd}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} /></label>
          <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required maxLength={254} /></label>
          <Select field label="Role" value={role} onChange={setRole} options={ROLES.map((r) => ({ value: r, label: r.charAt(0).toUpperCase() + r.slice(1) }))} />
          <button type="submit" disabled={busy}>Add member</button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Members</h2></div>
        <Status loading={members.loading} error={members.error}>
          <Table>
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Must change password</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {!members.data?.length && <tr><td colSpan={6} className="muted">No members</td></tr>}
              {members.data?.map((p) => (
                <tr key={p.user_id}>
                  <td><EditName name={p.display_name} disabled={busy} onSave={(n) => onRename(p, n)} /></td>
                  <td>{p.email ?? '-'}</td>
                  <td><Pill tone="neutral">{label(p.role)}</Pill></td>
                  <td><Pill tone={p.is_active ? 'active' : 'inactive'}>{p.is_active ? 'Active' : 'Inactive'}</Pill></td>
                  <td>{p.must_change_password ? 'Yes' : 'No'}</td>
                  <td className="actions"><div className="cell-actions">
                    <Select small hideLabel label={`Role for ${p.display_name ?? p.email ?? 'member'}`} value={p.role} disabled={busy}
                      onChange={(v) => void run({ action: 'set_role', user_id: p.user_id, role: v })} options={ROLES.map((r) => ({ value: r, label: r.charAt(0).toUpperCase() + r.slice(1) }))} />
                    <button className={p.is_active ? 'danger small' : 'plain small'} disabled={busy}
                      onClick={() => void run({ action: p.is_active ? 'deactivate_user' : 'reactivate_user', user_id: p.user_id })}>
                      {p.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                    <button className="plain small" disabled={busy} onClick={() => void onReset(p)} title="Break-glass reset: sets a temporary password the member must change">Reset password</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Status>
      </section>
    </div>
  )
}
