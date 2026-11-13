import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { supabase } from '../supabase'
import { passwordError } from '../format'
import { PageHead, PasswordInput } from '../components'

const MIN_LENGTH = 12

export default function ChangePassword() {
  const { profile, reloadProfile } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!profile?.must_change_password) return <Navigate to="/" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < MIN_LENGTH) return setError(`Password must be at least ${MIN_LENGTH} characters.`)
    if (password !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    const up = await supabase.auth.updateUser({ password })
    if (up.error) {
      setBusy(false)
      return setError(passwordError(up.error))
    }
    // The database clears must_change_password itself when the password really changes
    // (trigger on auth.users), so the browser never writes the flag.
    setBusy(false)
    reloadProfile()
    navigate('/', { replace: true })
  }

  return (
    <div className="page rise">
      <PageHead title="Choose a new password" meta="You signed in with a temporary password. Set your own to continue." />
      <section className="panel">
        <form className="stack" onSubmit={onSubmit}>
          <label>
            New password
            <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} required
              autoComplete="new-password" aria-describedby="change-pw-hint" />
            <span className="hint" id="change-pw-hint">At least {MIN_LENGTH} characters.</span>
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
