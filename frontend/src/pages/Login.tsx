import { useLayoutEffect, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { startHandoff } from '../Handoff'
import { isReturning, landCard, rememberCardSize } from '../Signout'
import { reduceMotion } from '../ui'
import { useAuth } from '../auth'
import { supabase } from '../supabase'
import { AuthLayout, PasswordInput } from '../components'

export default function Login() {
  const { session, loading, notice } = useAuth()
  const message = (useLocation().state as { message?: string } | null)?.message
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const navigate = useNavigate()

  // Coming back from sign-out: the overlay card glides into this card's place.
  useLayoutEffect(() => {
    if (!isReturning()) return
    const card = document.getElementById('auth-card')
    if (card) void landCard(card.getBoundingClientRect())
  }, [])

  // While signing in or playing the exit, don't redirect early: the exit hands the card over to the shell.
  if (!loading && session && !busy && !leaving) return <Navigate to="/" replace />

  // The form's contents and the scene fade, then the overlay takes over the card (see Handoff.tsx).
  function leave() {
    const card = document.getElementById('auth-card')
    if (!card || reduceMotion()) return navigate('/', { replace: true })
    const r = card.getBoundingClientRect()
    rememberCardSize(r)
    setLeaving(true)
    setTimeout(() => {
      startHandoff({ left: r.left, top: r.top, width: r.width, height: r.height })
      navigate('/', { replace: true })
    }, 260)
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) { setError(error.message); setBusy(false); return }
    setBusy(false)
    leave()
  }

  return (
    <AuthLayout title="Sign in to Churn-Spark" lede="Your team's retention workbench." leaving={leaving}>
      <form className="stack" onSubmit={onSubmit}>
        {message && <p className="ok" role="status">{message}</p>}
        {(error ?? notice) && <p className="error" role="alert">{error ?? notice}</p>}
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" />
        </label>
        <label>
          Password
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
        </label>
        <button type="submit" disabled={busy}>{busy ? 'Signing in...' : 'Sign in'}</button>
      </form>
      <div className="auth-links"><Link to="/forgot-password">Forgot password?</Link></div>
    </AuthLayout>
  )
}
