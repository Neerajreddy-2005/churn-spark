// The dataset has no currency, so money is shown as a plain amount with 2 decimals.
export const fmtNum = (n: number | null | undefined) => (n == null ? '–' : Number(n).toLocaleString('en-US'))
export const fmtMoney = (n: number | null | undefined) =>
  n == null ? '–' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const fmtPct = (n: number | null | undefined) => (n == null ? '–' : `${Number(n).toFixed(2)}%`)
export const fmtDate = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–'
export const fmtDay = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '–'
// "2026-11" -> "November 2026"
export const fmtMonth = (m: string | null | undefined) => {
  if (!m) return '–'
  const [y, mo] = m.split('-').map(Number)
  return y && mo ? new Date(y, mo - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : m
}
// risk_score is a 0..1 probability
export const fmtRisk = (n: number | null | undefined) => (n == null ? '–' : `${(Number(n) * 100).toFixed(2)}%`)
// snake_case database values -> "Sentence case" labels (e.g. discount_offer -> Discount offer)
export const label = (s: string | null | undefined) => (s ? (s.charAt(0).toUpperCase() + s.slice(1)).replace(/_/g, ' ') : '–')

// Model reason "tenure_months = 2 (low)" -> { k: "Tenure", v: "2 months (low)" }
const FEATURE: Record<string, string> = {
  tenure_months: 'Tenure', monthly_charges: 'Monthly charges', total_charges: 'Total charges',
  internet_service: 'Internet service', payment_method: 'Payment method', tech_support: 'Tech support',
  online_security: 'Online security', online_backup: 'Online backup', device_protection: 'Device protection',
  streaming_tv: 'Streaming TV', streaming_movies: 'Streaming movies', paperless_billing: 'Paperless billing',
  senior_citizen: 'Senior citizen', multiple_lines: 'Multiple lines', phone_service: 'Phone service',
}
export function reason(r: string | null | undefined): { k: string; v: string } | null {
  if (!r) return null
  const m = /^\s*([a-z_]+)\s*=\s*(.+)$/i.exec(r)
  if (!m) return { k: '', v: r }
  const key = m[1]!.toLowerCase()
  let v = m[2]!.trim()
  if (key === 'tenure_months') v = v.replace(/^(\d+)/, (n) => `${n} month${n === '1' ? '' : 's'}`)
  return { k: FEATURE[key] ?? label(key), v }
}
export const fmtReason = (r: string | null | undefined) => {
  const x = reason(r)
  return !x ? '–' : x.k ? `${x.k}: ${x.v}` : x.v
}
export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : '–')

// Supabase Auth password errors in plain words (same_password: the new password equals the current one).
export function passwordError(e: { message?: string; code?: string }): string {
  if (e.code === 'same_password' || /different from the old password/i.test(e.message ?? '')) {
    return 'Your new password must be different from your current password.'
  }
  if (e.code === 'weak_password') return 'That password is too weak. Use at least 12 characters and avoid common words.'
  return e.message ?? 'The password could not be changed.'
}
