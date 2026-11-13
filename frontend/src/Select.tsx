// Animated single-choice dropdown (ARIA listbox pattern). The menu slides down to open and back up to close;
// transform + opacity only, so it stays smooth. Keyboard: ↑ ↓ Home End, Enter/Space to choose, Esc/Tab to close.
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'

export interface Option { value: string; label: string }

export function Select({ label, value, options, onChange, hideLabel, disabled, small, field }: {
  label: string; value: string; options: Option[]; onChange: (v: string) => void; hideLabel?: boolean
  disabled?: boolean; small?: boolean; field?: boolean // field: form-field look (ink label, full width)
}) {
  const [phase, setPhase] = useState<'closed' | 'open' | 'closing'>('closed')
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState<CSSProperties>({})
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const id = useId()
  const current = options.find((o) => o.value === value)

  const open = () => {
    // Anchored to the viewport so scrolling tables and later panels can never clip or cover it.
    const r = button.current!.getBoundingClientRect()
    const below = window.innerHeight - r.bottom
    const needed = Math.min(300, options.length * (small ? 30 : 34) + 12) + 12 // the menu's real height + gap
    const up = below < needed && r.top > below
    setPos(up
      ? { position: 'fixed', left: r.left, bottom: window.innerHeight - r.top + 6, minWidth: r.width, transformOrigin: 'bottom center', ['--dir' as string]: 1 }
      : { position: 'fixed', left: r.left, top: r.bottom + 6, minWidth: r.width })
    setActive(Math.max(0, options.findIndex((o) => o.value === value)))
    setPhase('open')
  }
  const close = (refocus = true) => {
    setPhase((p) => (p === 'open' ? 'closing' : p))
    if (refocus) button.current?.focus()
  }
  const choose = (i: number) => {
    const o = options[i]
    if (o && o.value !== value) onChange(o.value)
    close()
  }

  useEffect(() => {
    if (phase !== 'open') return
    list.current?.focus()
    list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
    const onDown = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) close(false) }
    // Page scroll or resize would detach the menu from its button: close it.
    const onMove = (e: Event) => { if (!list.current?.contains(e.target as Node)) close(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [phase])

  // Fallback in case animationend never fires (e.g. element hidden mid-animation).
  useEffect(() => {
    if (phase !== 'closing') return
    const t = setTimeout(() => setPhase('closed'), 220)
    return () => clearTimeout(t)
  }, [phase])

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`#${CSS.escape(`${id}-${active}`)}`)?.scrollIntoView({ block: 'nearest' })
  }, [active, id])

  const onButtonKey = (e: KeyboardEvent) => {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); open() }
  }
  const onListKey = (e: KeyboardEvent) => {
    const last = options.length - 1
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(last, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(last) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(active) }
    else if (e.key === 'Escape') { e.preventDefault(); close() }
    else if (e.key === 'Tab') close(false)
    else if (e.key.length === 1) {
      // Type-ahead: jump to the next option starting with that letter.
      const k = e.key.toLowerCase()
      const n = options.findIndex((o, i) => i > active && o.label.toLowerCase().startsWith(k))
      const f = n >= 0 ? n : options.findIndex((o) => o.label.toLowerCase().startsWith(k))
      if (f >= 0) setActive(f)
    }
  }

  return (
    <div className={`select${small ? ' small' : ''}${field ? ' field' : ''}`} ref={wrap}>
      <span id={`${id}-label`} className={hideLabel ? 'sr-only' : 'select-label'}>{label}</span>
      <button ref={button} type="button" className="select-trigger" aria-haspopup="listbox" aria-expanded={phase === 'open'}
        disabled={disabled} aria-labelledby={`${id}-label ${id}-value`} onClick={() => (phase === 'open' ? close() : open())} onKeyDown={onButtonKey}>
        <span id={`${id}-value`}>{current?.label ?? 'Choose'}</span>
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {phase !== 'closed' && (
        <ul ref={list} role="listbox" tabIndex={-1} aria-labelledby={`${id}-label`}
          aria-activedescendant={`${id}-${active}`} className={`select-menu ${phase}`} style={pos} onKeyDown={onListKey}
          onAnimationEnd={() => { if (phase === 'closing') setPhase('closed') }}>
          {options.map((o, i) => (
            <li key={o.value} id={`${id}-${i}`} role="option" aria-selected={o.value === value}
              className={i === active ? 'active' : undefined} onMouseMove={() => setActive(i)} onClick={() => choose(i)}>
              {o.label}
              {o.value === value && <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.4 8.4 3 3 6.2-6.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
