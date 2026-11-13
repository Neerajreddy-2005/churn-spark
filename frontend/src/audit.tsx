import { Table } from './components'
import { fmtDate } from './format'
import type { AuditEntry } from './types'

const ACTION_WORDS: Record<string, string> = {
  create_org: 'created organisation',
  create_user: 'created user',
  set_role: 'changed role',
  deactivate_user: 'deactivated',
  reactivate_user: 'reactivated',
  reset_password: 'reset password',
  set_name: 'changed name',
}

const LABEL_KEYS = ['actor_label', 'target_label']
const short = (id: string) => id.slice(0, 8)

// "role: manager, email: a@b.c"; the labels are shown in their own columns.
function describeDetails(details: Record<string, unknown> | null): string {
  if (!details) return '-'
  const parts = Object.entries(details)
    .filter(([k, v]) => !LABEL_KEYS.includes(k) && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  return parts.length ? parts.join(', ') : '-'
}

const label = (details: Record<string, unknown> | null, key: string, id: string | null) => {
  const v = details?.[key]
  if (typeof v === 'string' && v) return v
  return id ? short(id) : '-'
}

// Emails may break only right after the "@" (never mid-word).
function breakable(s: string) {
  const parts = s.split('@')
  return parts.map((part, i) => <span key={i}>{i > 0 && <>@<wbr /></>}{part}</span>)
}

export type AuditRow = AuditEntry & { org?: string }

export function AuditTable({ rows, showOrg }: { rows: AuditRow[]; showOrg?: boolean }) {
  const cols = showOrg ? 6 : 5
  return (
    <Table className="audit">
      <thead>
        <tr>
          <th>When</th>{showOrg && <th>Organisation</th>}<th>Action</th><th>By</th><th>Target</th><th>Details</th>
        </tr>
      </thead>
      <tbody>
        {!rows.length && <tr><td colSpan={cols} className="muted">No entries</td></tr>}
        {rows.map((a) => (
          <tr key={a.id}>
            <td className="nowrap">{fmtDate(a.created_at)}</td>
            {showOrg && <td>{a.org ?? '-'}</td>}
            <td>{ACTION_WORDS[a.action] ?? a.action}</td>
            <td>{breakable(label(a.details, 'actor_label', a.actor_id))}</td>
            <td>{breakable(label(a.details, 'target_label', a.target_user_id))}</td>
            <td className="details">{breakable(describeDetails(a.details))}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}
