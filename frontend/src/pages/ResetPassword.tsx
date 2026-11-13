import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from '../supabase'
import { passwordError } from '../format'
import { Skeleton } from '../ui'
import { AuthLayout, PasswordInput } from '../components'

const MIN_LENGTH = 12
const WAIT_MS = 4000

type Step = 'checking' | 'form' | 'mfa' | 'invalid'

// Read once, synchronously, before supabase-js strips the URL: did the link carry a recovery
// token (implicit flow: #...type=recovery, PKCE: ?code=) or an error (expired / already used)?
function readLink() {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const query = new URLSearchParams(window.location.search)
  return {
    failed: hash.has('error') || hash.has('error_code') || query.has('error') || query.has('error_code'),
    hasToken: hash.get('type') === 'recovery' || query.has('code'),
  }
}

const needsAal2 = (e: { message?: string; code?: string }) =>
  e.code === 'insufficient_aal' || /aal2|mfa|multi-factor/i.test(e.message ?? '')

export default function ResetPassword() {
  const navigate = useNavigate()
  const [link] = useState(readLink)
  const [step, setStep] = useState<Step>(link.failed ? 'invalid' : 'checking')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [factorId, setFactorId] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // The recovery session comes from supabase-js reading the link; the form opens only for that.
  useEffect(() => {
    if (link.failed) return
    let done = false
    const ready = () => {
      done = true
      setStep((s) => (s === 'checking' ? 'form' : s))
    }
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'PASSWORD_RECOVERY') ready()
    })
    // The event may already have fired before this effect ran; a session plus a recovery link counts.
    void supabase.auth.getSession().then(({ data: d }) => {
      if (d.session && link.hasToken) ready()
    })
    const timer = setTimeout(() => {
      if (!done) setStep((s) => (s === 'checking' ? 'invalid' : s))
    }, WAIT_MS)
    return () => {
      clearTimeout(timer)
      data.subscription.unsubscribe()
    }
  }, [link])

  async function finish() {
    await supabase.auth.signOut({ scope: 'global' }).catch(() => undefined)
    navigate('/login', { replace: true, state: { message: 'Password updated — sign in with your new password' } })
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < MIN_LENGTH) return setError(`Password must be at least ${MIN_LENGTH} characters.`)
    if (password !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    const up = await supabase.auth.updateUser({ password })
    if (!up.error) return void (await finish())
    if (needsAal2(up.error)) {
      const f = await supabase.auth.mfa.listFactors()
      const totp = f.data?.totp[0]
      setBusy(false)
      if (!totp) return setError(passwordError(up.error))
      setFactorId(totp.id)
      return setStep('mfa')
    }
    setBusy(false)
    setError(passwordError(up.error))
  }

  async function onVerify(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const v = await supabase.auth.mfa.challengeAndVerify({ factorId, code: code.trim() })
    if (v.error) {
      setBusy(false)
      return setError('That code was not accepted. Try the current code from your app.')
    }
    setCode('')
    const up = await supabase.auth.updateUser({ password })
    if (up.error) {
      setBusy(false)
      return setError(passwordError(up.error))
    }
    await finish()
  }

  if (step === 'checking') {
    return <AuthLayout title="Checking your reset link"><Skeleton label="Checking your reset link" /></AuthLayout>
  }
  if (step === 'invalid') {
    return (
      <AuthLayout title="Reset link not valid">
        <p className="error" role="alert">This reset link is invalid, has expired or was already used. Request a new one to continue.</p>
        <div className="auth-links"><Link to="/forgot-password">Request a new link</Link></div>
      </AuthLayout>
    )
  }
  if (step === 'mfa') {
    return (
      <AuthLayout title="Enter your two-factor code" lede="Your account uses two-factor sign-in. Enter the current 6-digit code to finish.">
        <form className="stack" onSubmit={(e) => void onVerify(e)}>
          <label>
            6-digit code
            <input value={code} onChange={(e) => setCode(e.target.value)} required inputMode="numeric"
              autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} />
          </label>
          <button type="submit" disabled={busy}>{busy ? 'Checking...' : 'Verify and set password'}</button>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Choose a new password">
      <form className="stack" onSubmit={(e) => void onSubmit(e)}>
        <label>
          New password
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} required
            autoComplete="new-password" aria-describedby="reset-pw-hint" />
          <span className="hint" id="reset-pw-hint">At least {MIN_LENGTH} characters.</span>
        </label>
        <label>
          Confirm new password
          <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)} required
            autoComplete="new-password" />
        </label>
        <button type="submit" disabled={busy}>{busy ? 'Saving...' : 'Set new password'}</button>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </AuthLayout>
  )
}
