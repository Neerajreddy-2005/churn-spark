// Sign-in → workbench hand-off, always played in full (about 1.8 s) however fast sign-in and loading are:
//   1. the card glides to the centre of the screen        0 – 560 ms
//   2. a cobalt cross cracks it                            560 – 820 ms
//   3. it breaks into four pieces that fly apart           820 – 1380 ms
//   4. the workbench sheet opens from the centre, with
//      the dashboard already on it, to its full size       880 – 1720 ms
//   5. the sidebar and top bar fade in                     1300 – 1800 ms
// Overlay pieces use transform/opacity; the real <main class="sheet"> is revealed with a clip-path driven per frame,
// so it works even if the shell re-renders underneath.
import { useEffect, useRef, useState } from 'react'

type Rect = { left: number; top: number; width: number; height: number }
let pending: Rect | null = null
const listeners = new Set<() => void>()
const root = () => document.documentElement

export function startHandoff(card: Rect) {
  pending = card
  root().setAttribute('data-handoff', 'wait') // shell sheet hidden, its animations paused
  listeners.forEach((l) => l())
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const easeInOut = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2)
const easeOut = (k: number) => 1 - Math.pow(1 - k, 4)

function sheetRect(): Rect {
  const r = document.querySelector('main.sheet')?.getBoundingClientRect()
  return r && r.width > 0 ? r : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
}

// Reveal the real sheet: a rectangle centred on the screen grows from a point, through the card's size, to the sheet.
function openSheet(card: Rect, ms: number) {
  return new Promise<void>((done) => {
    const t0 = performance.now()
    const cx = window.innerWidth / 2
    const cy = window.innerHeight / 2
    const frame = (now: number) => {
      const k = Math.min(1, (now - t0) / ms)
      const s = sheetRect()
      // two stages: point -> card (first 30%), card -> full sheet (rest)
      let w: number; let h: number; let x: number; let y: number
      if (k < 0.3) {
        const e = easeOut(k / 0.3)
        w = card.width * e; h = 4 + (card.height - 4) * e; x = cx - w / 2; y = cy - h / 2
      } else {
        const e = easeInOut((k - 0.3) / 0.7)
        const cl = cx - card.width / 2; const ct = cy - card.height / 2
        x = cl + (s.left - cl) * e; y = ct + (s.top - ct) * e
        w = card.width + (s.width - card.width) * e; h = card.height + (s.height - card.height) * e
      }
      const t = Math.max(0, y - s.top); const l = Math.max(0, x - s.left)
      const r = Math.max(0, s.left + s.width - (x + w)); const b = Math.max(0, s.top + s.height - (y + h))
      root().style.setProperty('--ho-clip', `inset(${t}px ${r}px ${b}px ${l}px round 16px)`)
      if (k < 1) requestAnimationFrame(frame)
      else done()
    }
    requestAnimationFrame(frame)
  })
}

export function Handoff() {
  const [card, setCard] = useState<Rect | null>(pending)
  const whole = useRef<HTMLDivElement>(null)
  const pieces = useRef<(HTMLDivElement | null)[]>([])
  const crackH = useRef<HTMLDivElement>(null)
  const crackV = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const l = () => setCard(pending)
    listeners.add(l)
    return () => { listeners.delete(l) }
  }, [])

  useEffect(() => {
    if (!card) return
    let cancelled = false
    const dx = window.innerWidth / 2 - (card.left + card.width / 2)
    const dy = window.innerHeight / 2 - (card.top + card.height / 2)
    void (async () => {
      // 1. glide to centre
      await whole.current!.animate([{ transform: 'none' }, { transform: `translate(${dx}px, ${dy}px)` }],
        { duration: 560, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', fill: 'forwards' }).finished
      if (cancelled) return
      // 2. crack
      const crack = { duration: 260, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' as const }
      crackH.current!.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], crack)
      await crackV.current!.animate([{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { ...crack, delay: 60 }).finished
      if (cancelled) return
      // 3. break into four
      whole.current!.style.visibility = 'hidden'
      crackH.current!.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' })
      crackV.current!.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' })
      const fly = [[-1, -1, -5], [1, -1, 4], [-1, 1, 4], [1, 1, -5]]
      const breaks = pieces.current.map((p, i) => {
        const [sx, sy, rot] = fly[i]!
        p!.style.visibility = 'visible'
        return p!.animate([
          { transform: 'none', opacity: 1 },
          { transform: `translate(${sx * 70}px, ${sy * 48}px) rotate(${rot}deg) scale(0.94)`, opacity: 0 },
        ], { duration: 560, easing: 'cubic-bezier(0.6, 0, 0.9, 0.35)', fill: 'forwards' }).finished
      })
      // 4. open the real sheet (the dashboard is already on it); content starts rising as it opens
      await wait(60)
      if (cancelled) return
      root().setAttribute('data-handoff', 'open')
      await Promise.all([...breaks, openSheet(card, 840).then(() => root().setAttribute('data-handoff', 'chrome'))])
      // 5. chrome fade-in is CSS (data-handoff="chrome"); finish after it
      await wait(450)
      if (cancelled) return
      root().removeAttribute('data-handoff')
      root().style.removeProperty('--ho-clip')
      pending = null
      setCard(null)
    })().catch(() => {
      root().removeAttribute('data-handoff'); root().style.removeProperty('--ho-clip'); pending = null; setCard(null)
    })
    return () => { cancelled = true }
  }, [card])

  if (!card) return null
  const cx = card.left + (window.innerWidth / 2 - (card.left + card.width / 2))
  const cy = card.top + (window.innerHeight / 2 - (card.top + card.height / 2))
  const hw = card.width / 2
  const hh = card.height / 2
  const quads = [
    { left: cx, top: cy, r: '16px 0 0 0' }, { left: cx + hw, top: cy, r: '0 16px 0 0' },
    { left: cx, top: cy + hh, r: '0 0 0 16px' }, { left: cx + hw, top: cy + hh, r: '0 0 16px 0' },
  ]
  return (
    <div className="handoff" aria-hidden="true">
      <div className="ho-card" ref={whole} style={{ left: card.left, top: card.top, width: card.width, height: card.height }} />
      {quads.map((q, i) => (
        <div key={i} className="ho-piece" ref={(el) => { pieces.current[i] = el }}
          style={{ left: q.left, top: q.top, width: hw, height: hh, borderRadius: q.r, transformOrigin: `${i % 2 ? 'left' : 'right'} ${i < 2 ? 'bottom' : 'top'}` }} />
      ))}
      <div className="ho-crack h" ref={crackH} style={{ left: cx + 10, top: cy + hh - 1, width: card.width - 20 }} />
      <div className="ho-crack v" ref={crackV} style={{ left: cx + hw - 1, top: cy + 10, height: card.height - 20 }} />
    </div>
  )
}
