import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../supabase'
import { AuthLayout } from '../components'

const COOLDOWN_SECONDS = 60
// Same text whether or not the account exists (no account enumeration).
const MESSAGE = 'If an account exists for that email, a reset link has been sent. It expires in 1 hour.'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (cooldown > 0) return
    setCooldown(COOLDOWN_SECONDS)
    setSent(true)
    // The result is deliberately ignored: the user always sees the same message.
    await supabase.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${window.location.origin}/reset-password`,
    }).catch(() => undefined)
  }

  return (
    <AuthLayout title="Reset your password" lede="Enter your account email and we will send you a one-time reset link.">
      <form className="stack" onSubmit={(e) => void onSubmit(e)}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" />
        </label>
        <button type="submit" disabled={cooldown > 0}>
          {cooldown > 0 ? `Send again in ${cooldown}s` : 'Send reset link'}
        </button>
        {sent && <p className="ok" role="status">{MESSAGE}</p>}
      </form>
      <div className="auth-links"><Link to="/login">Back to sign in</Link></div>
    </AuthLayout>
  )
}
