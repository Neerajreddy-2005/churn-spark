import { useEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Mark } from './icons'
import { LoginScene } from './LoginScene'
import { useTheme, type ThemeChoice } from './theme'
import { Skeleton } from './ui'

export function Status({ loading, error, children }: { loading: boolean; error: string | null; children?: ReactNode }) {
  if (error) return <p className="error" role="alert">Something went wrong: {error}</p>
  if (loading) return <Skeleton />
  return <>{children}</>
}

// Sign-in, forgot/reset password: the apricot frame, a living scene of customers kept, and one sheet.
export function AuthLayout({ title, lede, children, leaving }: { title: string; lede?: ReactNode; children: ReactNode; leaving?: boolean }) {
  return (
    <div className={`auth${leaving ? ' leaving' : ''}`}>
      <section className="auth-scene" aria-label="Churn-Spark">
        <Link to="/login" className="wordmark"><Mark />Churn-Spark</Link>
        <LoginScene />
        <p className="auth-headline"><span>See who's about to leave.</span><span>Keep them.</span></p>
      </section>
      <main className="auth-sheet rise" id="auth-card">
        <div>
          <h1>{title}</h1>
          {lede && <p className="lede">{lede}</p>}
        </div>
        {children}
      </main>
    </div>
  )
}

// Page header: title (h1), optional meta line and actions on the right.
export function PageHead({ title, meta, actions, back }: {
  title: ReactNode; meta?: ReactNode; actions?: ReactNode; back?: { to: string; label: string }
}) {
  return (
    <>
      {back && <Link to={back.to} className="back-link">&larr; {back.label}</Link>}
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {meta && <div className="meta">{meta}</div>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </div>
    </>
  )
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="tablewrap">
      <table className={className}>{children}</table>
    </div>
  )
}

export function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
    </div>
  )
}

// Shows a temporary password exactly once; the parent clears it with onDone.
export function TempPassword({ email, password, onDone }: { email: string; password: string; onDone: () => void }) {
  return (
    <div className="card" style={{ marginBottom: '1.25rem' }}>
      <p>Temporary password for <strong>{email}</strong>:</p>
      <p><code style={{ fontSize: '1.2rem' }}>{password}</code>{' '}
        <button type="button" onClick={() => void navigator.clipboard.writeText(password)}>Copy</button></p>
      <p className="warn">Give this to the employee; they must change it at first login. It will not be shown again.</p>
      <button type="button" className="plain" onClick={onDone}>Done</button>
    </div>
  )
}

// Password field with a show/hide toggle. Pass the usual input props (value, onChange,
// autoComplete, required...). The text hides again when the surrounding form is submitted.
export function PasswordInput(props: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  const [visible, setVisible] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const form = ref.current?.form
    if (!form) return
    const hide = () => setVisible(false)
    form.addEventListener('submit', hide)
    return () => form.removeEventListener('submit', hide)
  }, [])
  return (
    <span className="pwfield">
      <input {...props} ref={ref} type={visible ? 'text' : 'password'} />
      <button type="button" className="plain icon" aria-label={visible ? 'Hide password' : 'Show password'}
        aria-pressed={visible} onClick={() => setVisible((v) => !v)}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
          {visible && <path d="M3 3l18 18" />}
        </svg>
      </button>
    </span>
  )
}

// Inline "Edit name" control. onSave resolves true when saved (the editor then closes).
export function EditName({ name, disabled, onSave }: {
  name: string | null; disabled: boolean; onSave: (name: string) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  if (!editing) {
    return (
      <>
        {name ?? '-'}{' '}
        <button type="button" className="ghost small" disabled={disabled}
          onClick={() => { setValue(name ?? ''); setEditing(true) }}>Edit name</button>
      </>
    )
  }
  return (
    <form className="inline" onSubmit={(e) => {
      e.preventDefault()
      void onSave(value.trim()).then((ok) => { if (ok) setEditing(false) })
    }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} required maxLength={100}
        aria-label="New name" autoFocus />{' '}
      <button type="submit" disabled={disabled || !value.trim()}>Save</button>{' '}
      <button type="button" className="plain" onClick={() => setEditing(false)}>Cancel</button>
    </form>
  )
}

const initials = (s: string) =>
  s.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join('') || '?'

// Header account menu: who is signed in, theme (system / light / dark), change password, sign out.
// Opens on click; Escape or an outside click closes it; arrow keys move between items.
export function AccountMenu({ label, name, email, showChangePassword, onChangePassword, onSignOut }: {
  label: string; name: string | null; email: string | undefined; showChangePassword: boolean
  onChangePassword: () => void; onSignOut: () => void
}) {
  const [open, setOpen] = useState(false)
  const [theme, setTheme] = useTheme()
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)

  const close = (refocus: boolean) => {
    setOpen(false)
    if (refocus) button.current?.focus()
  }
  const items = () => Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) close(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!open) return
    if (e.key === 'Escape') {
      e.preventDefault()
      return close(true)
    }
    if (e.key === 'Tab') return close(false)
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const list = items()
      const i = list.indexOf(document.activeElement as HTMLButtonElement)
      const next = e.key === 'ArrowDown' ? (i + 1) % list.length : (i <= 0 ? list.length - 1 : i - 1)
      list[next]?.focus()
    }
  }

  function toggle() {
    if (open) return close(true)
    setOpen(true)
    setTimeout(() => items()[0]?.focus(), 0)
  }

  const choose = (fn: () => void) => () => {
    close(false)
    fn()
  }
  const themes: [ThemeChoice, string][] = [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']]

  return (
    <div className="acct" ref={wrap} onKeyDown={onKeyDown}>
      <button type="button" className="acct-button" ref={button} aria-haspopup="menu" aria-expanded={open} onClick={toggle}
        aria-label={`Account: ${name ?? email ?? ''}, ${label}`}>
        <span className="who"><span>{name ?? email}</span><small>{label}</small></span>
        <span className="avatar" aria-hidden="true">{initials(name ?? email ?? '?')}</span>
      </button>
      {open && (
        <div className="acct-menu" role="menu" ref={menu} aria-label="Account">
          <div className="label" role="presentation">{email}</div>
          <div className="sep" role="separator" />
          <div className="label" role="presentation">Theme</div>
          <div className="themes" role="group" aria-label="Theme"
            style={{ ['--pos' as string]: themes.findIndex(([v]) => v === theme) }}>
            <span className="thumb" aria-hidden="true" />
            {themes.map(([v, l]) => (
              <button key={v} type="button" role="menuitemradio" aria-checked={theme === v} onClick={() => setTheme(v)}>{l}</button>
            ))}
          </div>
          <div className="sep" role="separator" />
          {showChangePassword && (
            <button type="button" role="menuitem" onClick={choose(onChangePassword)}>Change password</button>
          )}
          <button type="button" role="menuitem" onClick={choose(onSignOut)}>Sign out</button>
        </div>
      )}
    </div>
  )
}

// Centred confirmation: a green circle pops in and the tick draws itself, then the text rises.
export function SuccessPanel({ title, body, children }: { title: string; body?: ReactNode; children?: ReactNode }) {
  return (
    <section className="panel success-panel" role="status" aria-live="polite">
      <svg className="success-mark" viewBox="0 0 64 64" aria-hidden="true">
        <circle cx="32" cy="32" r="30" />
        <path d="M19 33.5 28 42 45.5 23.5" />
      </svg>
      <h1>{title}</h1>
      {body && <p className="muted">{body}</p>}
      {children}
    </section>
  )
}
