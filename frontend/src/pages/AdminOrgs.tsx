import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth'
import { callAdmin } from '../admin'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status, Table, TempPassword } from '../components'
import { fmtDate, fmtNum } from '../format'

interface Org { id: string; name: string; created_at: string }
interface Member { org_id: string; role: 'analyst' | 'manager' }
interface Created { org_id: string; temporary_password: string }

export default function AdminOrgs() {
  const { isPlatformAdmin } = useAuth()
  const [orgName, setOrgName] = useState('')
  const [managerName, setManagerName] = useState('')
  const [managerEmail, setManagerEmail] = useState('')
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Platform admins can read organisations and profiles (RLS), never business data.
  const orgs = useLoad(
    async () => {
      const [o, m] = await Promise.all([
        supabase.from('organisations').select('*').order('created_at', { ascending: false }),
        supabase.from('profiles').select('org_id, role'),
      ])
      const members = must(m) as Member[]
      return (must(o) as Org[]).map((org) => {
        const mine = members.filter((p) => p.org_id === org.id)
        return { ...org, members: mine.length, managers: mine.filter((p) => p.role === 'manager').length }
      })
    },
    [],
  )

  async function onCreate(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const res = await callAdmin<Created>({
        action: 'create_org', org_name: orgName, manager_name: managerName, manager_email: managerEmail,
      })
      setIssued({ email: managerEmail, password: res.temporary_password })
      setOrgName('')
      setManagerName('')
      setManagerEmail('')
      orgs.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  if (!isPlatformAdmin) return <p className="error" role="alert">Not allowed.</p>

  return (
    <div className="page rise">
      <PageHead title="Organisations" meta="Create an organisation and its first manager." />
      {issued && <TempPassword email={issued.email} password={issued.password} onDone={() => setIssued(null)} />}

      <section className="panel">
        <div className="panel-head"><h2>Create organisation</h2></div>
        <form className="stack" onSubmit={onCreate}>
          <label>Organisation name<input value={orgName} onChange={(e) => setOrgName(e.target.value)} required maxLength={100} /></label>
          <label>First manager name<input value={managerName} onChange={(e) => setManagerName(e.target.value)} required maxLength={100} /></label>
          <label>First manager email<input type="email" value={managerEmail} onChange={(e) => setManagerEmail(e.target.value)} required maxLength={254} /></label>
          <button type="submit" disabled={busy}>Create organisation</button>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>All organisations</h2></div>
        <Status loading={orgs.loading} error={orgs.error}>
          <Table>
            <thead><tr><th>Name</th><th>Created</th><th className="num">Members</th><th className="num">Managers</th></tr></thead>
            <tbody>
              {orgs.data?.map((o) => (
                <tr key={o.id}>
                  <td><Link to={`/admin/organisations/${o.id}`}>{o.name}</Link></td>
                  <td>{fmtDate(o.created_at)}</td>
                  <td className="num">{fmtNum(o.members)}</td>
                  <td className="num">{fmtNum(o.managers)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Status>
      </section>
    </div>
  )
}
