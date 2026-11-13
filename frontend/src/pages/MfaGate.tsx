import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { supabase } from '../supabase'
import { isMfaHeld, onMfaRelease } from '../mfaHold'
import { Skeleton } from '../ui'

type Step =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'challenge'; factorId: string }
  | { kind: 'enroll'; factorId: string; qr: string; secret: string }
  | { kind: 'error'; message: string }

// Platform admins must hold an aal2 session (password plus TOTP). Children render only then.
// The database and the Edge Function enforce the same rule; this gate is just the user flow.
export default function MfaGate({ children }: { children: ReactNode }) {
  const [step, setStep] = useState<Step>({ kind: 'loading' })
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Re-run whenever auth state changes (sign-in, token refresh, password update): a password-only
  // re-authentication drops the session to aal1, and the gate must ask for the code again instead of
  // showing admin pages that the database would now refuse.
  const [tick, setTick] = useState(0)
  const stepRef = useRef(step)
  useEffect(() => { stepRef.current = step }, [step])
  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((event) => {
      // A page running its own re-auth + 2FA flow holds the gate; re-check once it releases.
      if (isMfaHeld()) return
      if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED' || event === 'USER_UPDATED' || event === 'MFA_CHALLENGE_VERIFIED') {
        setTick((t) => t + 1)
      }
    })
    const off = onMfaRelease(() => setTick((t) => t + 1))
    return () => { data.subscription.unsubscribe(); off() }
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const aal = await supabase.auth.mfa.getAuthenticatorAssuranceLevel()
      if (cancelled) return
      if (aal.error) return setStep({ kind: 'error', message: aal.error.message })
      if (aal.data.currentLevel === 'aal2') return setStep({ kind: 'ready' })
      const factors = await supabase.auth.mfa.listFactors()
      if (cancelled) return
      if (factors.error) return setStep({ kind: 'error', message: factors.error.message })
      const verified = factors.data.totp[0]
      if (verified) return setStep({ kind: 'challenge', factorId: verified.id })
      // An enrolment already on screen stays; restarting it would invalidate the QR code being scanned.
      if (stepRef.current.kind === 'enroll') return
      // Drop abandoned, never-verified enrolments so a new one can start.
      for (const f of factors.data.all) {
        if (f.factor_type === 'totp' && f.status === 'unverified') await supabase.auth.mfa.unenroll({ factorId: f.id })
      }
      const enrol = await supabase.auth.mfa.enroll({ factorType: 'totp', friendlyName: `admin-${Date.now()}` })
      if (cancelled) return
      if (enrol.error) return setStep({ kind: 'error', message: enrol.error.message })
      setStep({ kind: 'enroll', factorId: enrol.data.id, qr: enrol.data.totp.qr_code, secret: enrol.data.totp.secret })
    })()
    return () => {
      cancelled = true
    }
  }, [tick])

  async function onVerify(e: FormEvent, factorId: string) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const res = await supabase.auth.mfa.challengeAndVerify({ factorId, code: code.trim() })
    setBusy(false)
    if (res.error) return setError('That code was not accepted. Try the current code from your app.')
    setCode('')
    setStep({ kind: 'ready' })
  }

  if (step.kind === 'ready') return <>{children}</>
  if (step.kind === 'loading') return <div className="login"><Skeleton label="Checking your sign-in" /></div>
  if (step.kind === 'error') return <p className="error" role="alert">Something went wrong: {step.message}</p>

  return (
    <div className="login rise">
      <h1>{step.kind === 'enroll' ? 'Set up two-factor sign-in' : 'Enter your two-factor code'}</h1>
      {step.kind === 'enroll' ? (
        <>
          <p className="muted">Scan this QR code with an authenticator app, then enter the 6-digit code it shows.</p>
          <img src={step.qr} alt="Authenticator QR code" width={200} height={200} />
          <p className="muted">Cannot scan? Enter this key manually: <code>{step.secret}</code></p>
        </>
      ) : (
        <p className="muted">Open your authenticator app and enter the current 6-digit code.</p>
      )}
      <form className="stack" onSubmit={(e) => void onVerify(e, step.factorId)}>
        <label>
          6-digit code
          <input value={code} onChange={(e) => setCode(e.target.value)} required inputMode="numeric"
            autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} />
        </label>
        <button type="submit" disabled={busy}>{busy ? 'Checking...' : 'Verify'}</button>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </div>
  )
}
