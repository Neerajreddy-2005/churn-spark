import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode,
} from 'react'

/* ------------------------------------------------------------------ toasts
   show(): a plain message. deferred(): an undoable write — nothing is sent until the undo window ends.
   Pending writes survive in-app navigation (provider sits above the routes); closing the tab while one is
   pending triggers the browser's "leave site?" prompt. */

type Kind = 'info' | 'error'
interface Toast { id: number; message: string; kind: Kind; undo?: () => void; ms: number }
interface DeferredOpts { message: string; commit: () => Promise<string | null>; done?: string; onCommitted?: () => void; ms?: number }
interface ToastApi { show: (message: string, kind?: Kind) => void; deferred: (o: DeferredOpts) => void }

const ToastCtx = createContext<ToastApi | null>(null)
export const useToast = () => {
  const c = useContext(ToastCtx)
  if (!c) throw new Error('useToast outside ToastProvider')
  return c
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const pending = useRef(0)
  const next = useRef(1)

  const remove = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const show = useCallback((message: string, kind: Kind = 'info') => {
    const id = next.current++
    const ms = kind === 'error' ? 7000 : 3200
    setToasts((t) => [...t.slice(-2), { id, message, kind, ms }])
    window.setTimeout(() => remove(id), ms)
  }, [remove])

  const deferred = useCallback((o: DeferredOpts) => {
    const id = next.current++
    const ms = o.ms ?? 5000
    let cancelled = false
    pending.current++
    const timer = window.setTimeout(async () => {
      remove(id)
      const err = await o.commit()
      pending.current--
      if (err) show(err, 'error')
      else {
        if (o.done) show(o.done)
        o.onCommitted?.()
      }
    }, ms)
    const undo = () => {
      if (cancelled) return
      cancelled = true
      window.clearTimeout(timer)
      pending.current--
      remove(id)
      show('Undone. Nothing was saved.')
    }
    setToasts((t) => [...t.slice(-2), { id, message: o.message, kind: 'info', undo, ms }])
  }, [remove, show])

  useEffect(() => {
    const onLeave = (e: BeforeUnloadEvent) => {
      if (pending.current > 0) e.preventDefault()
    }
    window.addEventListener('beforeunload', onLeave)
    return () => window.removeEventListener('beforeunload', onLeave)
  }, [])

  const api = useMemo(() => ({ show, deferred }), [show, deferred])
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === 'error' ? 'error' : ''}`}>
            <span className="msg">{t.message}</span>
            {t.undo && <button type="button" onClick={t.undo}>Undo</button>}
            {t.undo && <i className="timer" style={{ animationDuration: `${t.ms}ms` }} aria-hidden="true" />}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

/* ------------------------------------------------------------------ explain
   A figure with a dotted underline; hover or keyboard focus shows how it was calculated and where it came from. */
export function Explain({ children, title, body }: { children: ReactNode; title?: string; body: ReactNode }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <span className="explain" onPointerEnter={(e) => e.pointerType === 'mouse' && setOpen(true)}
      onPointerLeave={(e) => e.pointerType === 'mouse' && setOpen(false)}>
      <button type="button" className="explain-trigger" aria-describedby={open ? id : undefined} aria-expanded={open}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}>
        {children}
      </button>
      {open && (
        <span className="explain-pop" role="tooltip" id={id}>
          {title && <b>{title}</b>}
          {body}
        </span>
      )}
    </span>
  )
}

/* ------------------------------------------------------------------ count-up (runs once per value) */
export function CountUp({ value, format }: { value: number; format: (n: number) => string }) {
  const [shown, setShown] = useState(() => (reduceMotion() ? value : 0))
  useEffect(() => {
    if (reduceMotion()) return setShown(value)
    const t0 = performance.now()
    const dur = 800
    let raf = 0
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / dur)
      setShown(value * (1 - Math.pow(1 - k, 3)))
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value])
  // While counting, screen readers get the final figure; once settled there is a single copy (clean copy-paste).
  if (shown === value) return <>{format(value)}</>
  return <><span aria-hidden="true">{format(shown)}</span><span className="sr-only">{format(value)}</span></>
}

export const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

export function Skeleton({ label = 'Loading' }: { label?: string }) {
  return <div className="skeleton" role="status" aria-label={label}><i /><i /><i /></div>
}

export function Pill({ tone, children, dot }: { tone: string; children: ReactNode; dot?: boolean }) {
  return <span className={`pill ${tone}`}>{dot && <span className="dot" aria-hidden="true" />}{children}</span>
}
