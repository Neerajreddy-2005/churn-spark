// Admin panel API. Runs with the service key (auto-provided by Supabase, never sent to a
// browser). The caller's org always comes from the database, never from the request body
// (only a platform admin may name an org).
import { createClient } from 'npm:@supabase/supabase-js@2.119.0'

const ROLES = ['analyst', 'manager']
const ACTIONS = ['create_org', 'create_user', 'set_role', 'deactivate_user', 'reactivate_user', 'reset_password', 'set_name']
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const MAX_CREATES_PER_DAY = 50

const url = Deno.env.get('SUPABASE_URL')!
const anon = createClient(url, Deno.env.get('SUPABASE_ANON_KEY')!, {
  auth: { persistSession: false, autoRefreshToken: false },
})
const db = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {
  auth: { persistSession: false, autoRefreshToken: false },
})
const allowedOrigins = (Deno.env.get('ALLOWED_ORIGINS') ?? 'http://localhost:5173')
  .split(',').map((s) => s.trim()).filter(Boolean)

class HttpError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message)
  }
}
const serverError = () => new HttpError(500, 'server_error', 'Something went wrong')

function corsHeaders(origin: string | null): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json', Vary: 'Origin' }
  if (origin) {
    h['Access-Control-Allow-Origin'] = origin
    h['Access-Control-Allow-Headers'] = 'authorization, apikey, content-type, x-client-info'
    h['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
  }
  return h
}

// Uniform random integer in [0, n) from crypto.getRandomValues (rejection sampling, no bias).
function randInt(n: number): number {
  const limit = 256 - (256 % n)
  const b = new Uint8Array(1)
  do crypto.getRandomValues(b); while (b[0] >= limit)
  return b[0] % n
}

function tempPassword(length = 20): string {
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnopqrstuvwxyz', '23456789', '!@#$%^&*-_=+?']
  const all = sets.join('')
  const out = sets.map((s) => s[randInt(s.length)]) // one of each class
  while (out.length < length) out.push(all[randInt(all.length)])
  for (let i = out.length - 1; i > 0; i--) {
    const j = randInt(i + 1)
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out.join('')
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

function uuid(v: unknown, field: string): string {
  const s = str(v)
  if (!UUID_RE.test(s)) throw new HttpError(400, 'bad_request', `Invalid ${field}`)
  return s
}
function email(v: unknown): string {
  const s = str(v).trim().toLowerCase()
  if (s.length > 254 || !EMAIL_RE.test(s)) throw new HttpError(400, 'bad_request', 'Invalid email')
  return s
}
function text(v: unknown, field: string): string {
  const s = str(v).trim()
  if (s.length < 1 || s.length > 100) throw new HttpError(400, 'bad_request', `Invalid ${field}`)
  return s
}
function role(v: unknown): string {
  if (!ROLES.includes(str(v))) throw new HttpError(400, 'bad_request', 'Invalid role')
  return str(v)
}

interface Caller {
  id: string; isPlatformAdmin: boolean; orgId: string | null; role: string | null; aal: string; label: string
  mustChangePassword: boolean
}

// The token was already verified by auth.getUser, so its payload can be read without re-checking.
function aalOf(token: string): string {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '=')))
    return typeof claims.aal === 'string' ? claims.aal : 'aal1'
  } catch {
    return 'aal1'
  }
}

async function loadCaller(req: Request): Promise<Caller> {
  const m = /^Bearer (.+)$/.exec(req.headers.get('Authorization') ?? '')
  if (!m) throw new HttpError(401, 'unauthenticated', 'Sign in required')
  const { data, error } = await anon.auth.getUser(m[1])
  if (error || !data.user) throw new HttpError(401, 'unauthenticated', 'Sign in required')
  const id = data.user.id
  const [p, pa] = await Promise.all([
    db.from('profiles').select('org_id, role, is_active, must_change_password, display_name, email').eq('user_id', id).maybeSingle(),
    db.rpc('admin_is_platform_admin', { p_user: id }),
  ])
  if (p.error || pa.error) throw serverError()
  if (p.data && !p.data.is_active) throw new HttpError(403, 'forbidden', 'Not allowed')
  // Separation of duties: platform admin means "in platform_admins AND no organisation". A legacy
  // account that is both (until the owner revokes it) acts as an ordinary org member here.
  const isPlatformAdmin = pa.data === true && !p.data
  // Human-readable name stored in audit details (so the log stays readable if the account is deleted).
  const label = isPlatformAdmin
    ? `Platform admin (${data.user.email ?? id})`
    : (p.data?.display_name || p.data?.email || data.user.email || id)
  return { id, isPlatformAdmin, orgId: p.data?.org_id ?? null, role: p.data?.role ?? null, aal: aalOf(m[1]), label,
    mustChangePassword: p.data?.must_change_password === true }
}

// Org the action applies to: a manager's own org; a platform admin (no org of their own) must
// name one for every org-targeting action.
async function targetOrg(c: Caller, body: Record<string, unknown>): Promise<string> {
  if (!c.isPlatformAdmin) return c.orgId!
  const orgId = uuid(body.org_id, 'org_id')
  const { data, error } = await db.from('organisations').select('id').eq('id', orgId).maybeSingle()
  if (error) throw serverError()
  if (!data) throw new HttpError(404, 'not_found', 'Not found')
  return orgId
}

// Existing user the caller may manage: a manager's own org; a platform admin must name the
// org (org_id) and the user must belong to it. Never themselves, unless allowSelf (set_name only).
async function targetUser(c: Caller, body: Record<string, unknown>, allowSelf = false) {
  const userId = uuid(body.user_id, 'user_id')
  if (userId === c.id && !allowSelf) throw new HttpError(400, 'self_action', 'You cannot do this to yourself')
  const orgId = await targetOrg(c, body)
  const { data, error } = await db.from('profiles').select('user_id, org_id, role, display_name, email').eq('user_id', userId).maybeSingle()
  if (error) throw serverError()
  if (!data || data.org_id !== orgId) throw new HttpError(404, 'not_found', 'Not found')
  return data as { user_id: string; org_id: string; role: string; display_name: string | null; email: string | null }
}

// Writes actor_label/target_label into details so the log is readable. Never put secrets in details.
async function audit(c: Caller, orgId: string, action: string, target: string | null,
  targetLabel: string | null, details: Record<string, unknown> | null) {
  const { error } = await db.from('audit_log').insert({
    org_id: orgId, actor_id: c.id, action, target_user_id: target,
    details: { ...(details ?? {}), actor_label: c.label, ...(targetLabel ? { target_label: targetLabel } : {}) },
  })
  if (error) throw serverError()
}

async function createUser(c: Caller, orgId: string, mail: string, display: string, userRole: string) {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString()
  const { count, error: cErr } = await db.from('audit_log').select('id', { count: 'exact', head: true })
    .eq('org_id', orgId).eq('action', 'create_user').gt('created_at', since)
  if (cErr) throw serverError()
  if ((count ?? 0) >= MAX_CREATES_PER_DAY) throw new HttpError(429, 'rate_limited', 'Too many users created today')

  const password = tempPassword()
  const created = await db.auth.admin.createUser({ email: mail, password, email_confirm: true })
  if (created.error || !created.data.user) {
    const dup = /already|exists|registered/i.test(created.error?.message ?? '')
    // Also covers an email that belongs to a platform admin (their auth user exists); the
    // profiles trigger in 0008 is the backstop.
    if (dup) throw new HttpError(409, 'email_taken', 'That email is already registered')
    throw serverError()
  }
  const userId = created.data.user.id
  const ins = await db.from('profiles').insert({
    user_id: userId, org_id: orgId, role: userRole, display_name: display, email: mail,
    must_change_password: true,
  })
  if (ins.error) {
    await db.auth.admin.deleteUser(userId) // no half-created users
    throw serverError()
  }
  await audit(c, orgId, 'create_user', userId, display || mail, { email: mail, role: userRole })
  return { user_id: userId, temporary_password: password }
}

async function handle(c: Caller, body: Record<string, unknown>) {
  const action = str(body.action)
  if (action === 'whoami') return { is_platform_admin: c.isPlatformAdmin, has_org: c.orgId !== null, role: c.role, aal: c.aal }
  if (!ACTIONS.includes(action)) throw new HttpError(400, 'bad_request', 'Unknown action')
  // Same rule as private.current_org_id(): no management until a forced password change is done.
  if (c.mustChangePassword) throw new HttpError(403, 'password_change_required', 'Change your password first')
  if (c.isPlatformAdmin && c.aal !== 'aal2') throw new HttpError(403, 'mfa_required', 'Two-factor code required')
  if (!c.isPlatformAdmin && c.role !== 'manager') throw new HttpError(403, 'forbidden', 'Not allowed')

  if (action === 'create_org') {
    if (!c.isPlatformAdmin) throw new HttpError(403, 'forbidden', 'Not allowed')
    const orgName = text(body.org_name, 'org_name')
    const mail = email(body.manager_email)
    const display = text(body.manager_name, 'manager_name')
    const org = await db.from('organisations').insert({ name: orgName }).select('id').single()
    if (org.error) throw serverError()
    let manager
    try {
      manager = await createUser(c, org.data.id, mail, display, 'manager')
    } catch (e) {
      await db.from('organisations').delete().eq('id', org.data.id) // nothing else references it yet
      throw e
    }
    await audit(c, org.data.id, 'create_org', manager.user_id, display, { org_name: orgName })
    return { org_id: org.data.id, ...manager }
  }

  if (action === 'create_user') {
    const mail = email(body.email)
    const display = text(body.display_name, 'display_name')
    const userRole = role(body.role)
    // Managers create analysts only; manager accounts are a platform-admin decision.
    if (!c.isPlatformAdmin && userRole !== 'analyst') {
      throw new HttpError(403, 'forbidden_role', 'Only the platform admin can create managers')
    }
    return await createUser(c, await targetOrg(c, body), mail, display, userRole)
  }

  // Role changes and password resets are platform-admin only (users reset by email themselves).
  if (!c.isPlatformAdmin && (action === 'set_role' || action === 'reset_password')) {
    throw new HttpError(403, 'forbidden', 'Only the platform admin can do this')
  }
  const target = await targetUser(c, body, action === 'set_name')
  // A manager may act only on analysts (and rename themselves), never on other managers.
  if (!c.isPlatformAdmin && target.user_id !== c.id && target.role !== 'analyst') {
    throw new HttpError(403, 'forbidden', 'Managers can only manage analysts')
  }
  const targetLabel = target.display_name || target.email
  if (action === 'set_name') {
    const newName = text(body.display_name, 'display_name')
    const { error } = await db.from('profiles').update({ display_name: newName }).eq('user_id', target.user_id)
    if (error) throw serverError()
    await audit(c, target.org_id, 'set_name', target.user_id, newName, { name: newName, previous_name: targetLabel })
    return { ok: true }
  }
  if (action === 'set_role') {
    const newRole = role(body.role)
    const { error } = await db.from('profiles').update({ role: newRole }).eq('user_id', target.user_id)
    if (error) throw serverError()
    await audit(c, target.org_id, 'set_role', target.user_id, targetLabel, { role: newRole })
    return { ok: true }
  }
  if (action === 'deactivate_user' || action === 'reactivate_user') {
    const active = action === 'reactivate_user'
    // Ban first so a failure never leaves an "inactive" user who can still sign in.
    const ban = await db.auth.admin.updateUserById(target.user_id, { ban_duration: active ? 'none' : '876000h' })
    if (ban.error) throw serverError()
    const { error } = await db.from('profiles').update({ is_active: active }).eq('user_id', target.user_id)
    if (error) throw serverError()
    await audit(c, target.org_id, action, target.user_id, targetLabel, null)
    return { ok: true }
  }
  // reset_password
  const password = tempPassword()
  const upd = await db.auth.admin.updateUserById(target.user_id, { password })
  if (upd.error) throw serverError()
  const { error } = await db.from('profiles').update({ must_change_password: true }).eq('user_id', target.user_id)
  if (error) throw serverError()
  // End every existing session: whoever held the old password is signed out (migration 0012).
  const revoked = await db.rpc('admin_revoke_sessions', { p_user: target.user_id })
  if (revoked.error) throw serverError()
  await audit(c, target.org_id, 'reset_password', target.user_id, targetLabel, { sessions_ended: revoked.data })
  return { user_id: target.user_id, temporary_password: password }
}

Deno.serve(async (req) => {
  const origin = req.headers.get('Origin')
  const allowed = origin !== null && allowedOrigins.includes(origin)
  const headers = corsHeaders(allowed ? origin : null)
  const reply = (status: number, payload: unknown) => new Response(JSON.stringify(payload), { status, headers })
  const fail = (status: number, code: string, message: string) => reply(status, { error: message, code })

  if (origin !== null && !allowed) return fail(403, 'origin_not_allowed', 'Origin not allowed')
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers })
  if (req.method !== 'POST') return fail(405, 'method_not_allowed', 'POST only')
  try {
    const caller = await loadCaller(req)
    let body: unknown
    try {
      body = await req.json()
    } catch {
      throw new HttpError(400, 'bad_request', 'Invalid JSON')
    }
    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
      throw new HttpError(400, 'bad_request', 'Invalid body')
    }
    return reply(200, await handle(caller, body as Record<string, unknown>))
  } catch (e) {
    if (e instanceof HttpError) return fail(e.status, e.code, e.message)
    console.error('admin-users: unexpected error') // no details: they could contain secrets
    return fail(500, 'server_error', 'Something went wrong')
  }
})
