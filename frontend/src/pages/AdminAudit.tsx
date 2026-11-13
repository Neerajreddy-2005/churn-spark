import { useAuth } from '../auth'
import { supabase } from '../supabase'
import { must, useLoad } from '../useLoad'
import { PageHead, Status } from '../components'
import { AuditTable, type AuditRow } from '../audit'
import type { Organisation } from '../types'

export default function AdminAudit() {
  const { isPlatformAdmin } = useAuth()

  const log = useLoad(
    async () => {
      const [a, o] = await Promise.all([
        supabase.from('audit_log').select('*').order('created_at', { ascending: false }).limit(100),
        supabase.from('organisations').select('id, name'),
      ])
      const orgs = new Map((must(o) as Organisation[]).map((x) => [x.id, x.name]))
      return (must(a) as (AuditRow & { org_id: string | null })[])
        .map((r) => ({ ...r, org: (r.org_id && orgs.get(r.org_id)) || '-' }))
    },
    [],
  )

  if (!isPlatformAdmin) return <p className="error" role="alert">Not allowed.</p>

  return (
    <div className="page rise">
      <PageHead title="Audit log" meta="Latest 100 entries across all organisations." />
      <section className="panel">
        <Status loading={log.loading} error={log.error}>
          <AuditTable rows={log.data ?? []} showOrg />
        </Status>
      </section>
    </div>
  )
}
