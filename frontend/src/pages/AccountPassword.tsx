import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useGo } from '../nav'
import { holdMfaGate, releaseMfaGate } from '../mfaHold'
import { passwordError } from '../format'
import { useAuth } from '../auth'
import { supabase } from '../supabase'
import { PageHead, PasswordInput, SuccessPanel } from '../components'

const MIN_LENGTH = 12

const needsAal2 = (e: { message?: string; code?: string }) =>
  e.code === 'insufficient_aal' || /aal2|mfa|multi-factor/i.test(e.message ?? '')

// 'update': the code is needed before (or to retry) updateUser. 'restore': the password is already
// changed; the code only brings this session back to aal2 so admin pages keep working.
type Step = { kind: 'form' } | { kind: 'mfa'; factorId: string; purpose: 'update' | 'restore' }

export default function AccountPassword() {
  const { session, isPlatformAdmin } = useAuth()
  const email = session?.user.email ?? ''
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<Step>({ kind: 'form' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // form -> closing (fields fold away) -> success (animated tick)
  const [phase, setPhase] = useState<'form' | 'closing' | 'success'>('form')

  const go = useGo()
  const home = isPlatformAdmin ? '/admin/organisations' : '/'
  // After the tick has drawn (~1.1 s), wait 2 s, then cross-fade to the dashboard.
  useEffect(() => {
    if (phase !== 'success') return
    const t = setTimeout(() => go(home), 3100)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase])

  // Leaving the page mid-flow releases the gate so it can re-check the session.
  useEffect(() => () => releaseMfaGate(), [])
  // The new password is held here only while waiting for a 2FA code; cleared on every exit.
  const pending = useRef('')
  const retried = useRef(false)

  const clearAll = () => {
    setCurrent('')
    setNext('')
    setConfirm('')
    setCode('')
    pending.current = ''
    retried.current = false
  }
  const succeed = () => {
    clearAll()
    releaseMfaGate()
    setBusy(false)
    setStep({ kind: 'form' })
    setPhase('closing')
    setTimeout(() => setPhase('success'), 380)
  }
  const fail = (message: string) => {
    releaseMfaGate()
    clearAll()
    setStep({ kind: 'form' })
    setBusy(false)
    setError(message)
  }

  async function verifiedFactorId() {
    const f = await supabase.auth.mfa.listFactors()
    return f.data?.totp[0]?.id ?? null
  }

  // After the password is changed: sign out other devices, then make sure this session is aal2
  // if the account has a verified factor.
  async function finish() {
    await supabase.auth.signOut({ scope: 'others' }).catch(() => undefined)
    const aal = await supabase.auth.mfa.getAuthenticatorAssuranceLevel()
    if (aal.data && aal.data.currentLevel !== 'aal2' && aal.data.nextLevel === 'aal2') {
      const factorId = await verifiedFactorId()
      if (factorId) {
        setBusy(false)
        return setStep({ kind: 'mfa', factorId, purpose: 'restore' })
      }
    }
    succeed()
  }

  async function update() {
    const up = await supabase.auth.updateUser({ password: pending.current })
    if (!up.error) return void (await finish())
    if (needsAal2(up.error) && !retried.current) {
      retried.current = true
      const factorId = await verifiedFactorId()
      if (factorId) {
        setBusy(false)
        return setStep({ kind: 'mfa', factorId, purpose: 'update' })
      }
    }
    fail(passwordError(up.error))
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (next.length < MIN_LENGTH) return setError(`New password must be at least ${MIN_LENGTH} characters.`)
    if (next !== confirm) return setError('New passwords do not match.')
    if (next === current) return setError('New password must be different from the current password.')
    setBusy(true)
    holdMfaGate() // the re-auth below drops the session to aal1; this page asks for the code itself
    const reauth = await supabase.auth.signInWithPassword({ email, password: current })
    if (reauth.error) return fail('Current password is incorrect')
    pending.current = next
    retried.current = false
    setCurrent('')
    setNext('')
    setConfirm('')
    // Re-auth replaced the session at aal1; reach aal2 before updating if a factor exists.
    const factorId = await verifiedFactorId()
    if (factorId) {
      setBusy(false)
      return setStep({ kind: 'mfa', factorId, purpose: 'update' })
    }
    await update()
  }

  async function onVerify(e: FormEvent, factorId: string, purpose: 'update' | 'restore') {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const v = await supabase.auth.mfa.challengeAndVerify({ factorId, code: code.trim() })
    if (v.error) {
      setBusy(false)
      return setError('That code was not accepted. Try the current code from your app.')
    }
    setCode('')
    if (purpose === 'update') return await update()
    succeed()
  }

  if (step.kind === 'mfa') {
    const { factorId, purpose } = step
    return (
      <div className="page rise">
        <PageHead
          title="Enter your two-factor code"
          meta={purpose === 'update'
            ? 'Your account uses two-factor sign-in. Enter the current 6-digit code to change your password.'
            : 'Your password was changed. Enter the current 6-digit code to keep this session fully signed in.'}
        />
        <section className="panel">
          <form className="stack" onSubmit={(e) => void onVerify(e, factorId, purpose)}>
            <label>
              6-digit code
              <input value={code} onChange={(e) => setCode(e.target.value)} required inputMode="numeric"
                autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} autoFocus />
            </label>
            <button type="submit" disabled={busy}>{busy ? 'Checking...' : 'Verify'}</button>
            {error && <p className="error" role="alert">{error}</p>}
          </form>
        </section>
      </div>
    )
  }

  if (phase === 'success') {
    return (
      <div className="page">
        <SuccessPanel title="Password changed" body="Use your new password next time you sign in. Other devices have been signed out.">
          <p className="redirect-note">
            Taking you to {isPlatformAdmin ? 'Organisations' : 'your overview'}…{' '}
            <Link to={home}>Go now</Link>
          </p>
          <span className="redirect-bar" aria-hidden="true"><i /></span>
        </SuccessPanel>
      </div>
    )
  }

  return (
    <div className="page rise">
      <PageHead title="Change password" meta="Other devices are signed out when you change it." />
      <section className={`panel fold${phase === 'closing' ? ' folding' : ''}`}>
        <form className="stack" onSubmit={(e) => void onSubmit(e)}>
          <label>
            Current password
            <PasswordInput value={current} onChange={(e) => setCurrent(e.target.value)} required
              autoComplete="current-password" />
          </label>
          <label>
            New password
            <PasswordInput value={next} onChange={(e) => setNext(e.target.value)} required
              autoComplete="new-password" aria-describedby="account-pw-hint" />
            <span className="hint" id="account-pw-hint">At least {MIN_LENGTH} characters.</span>
          </label>
          <label>
            Confirm new password
            <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" />
          </label>
          <button type="submit" disabled={busy}>{busy ? 'Saving...' : 'Change password'}</button>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </section>
    </div>
  )
}
