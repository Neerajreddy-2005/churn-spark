// Sign-out: the sign-in hand-off (Handoff.tsx) played exactly in reverse — same durations, mirrored easing curves.
//
//   sign-in (forward)                                   sign-out (this file)
//   card glides to centre        560 ms ease-in-out     <- last: card glides back      560 ms ease-in-out
//   cross grows middle -> ends   260 ms ease-out        <- cross shrinks ends -> middle 260 ms ease-in (V first, H +60)
//   pieces fly out               560 ms ease-in         <- pieces fly in               560 ms mirrored ease (land at 900)
//   sheet opens point->card->full 840 ms (from +60)     <- sheet closes full->card->point 840 ms (from 0)
//   chrome fades in last                                <- chrome fades out first
//
// Overlay nodes are created imperatively: they must outlive the shell, which unmounts at sign-out.
import { reduceMotion } from './ui'

type Rect = { left: number; top: number; width: number; height: number }
const root = () => document.documentElement
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
// Mirrors of the sign-in curves: easeInOut is symmetric; easeOut4 run backwards gives the reversed shrink.
const easeInOut = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2)
const easeOut4 = (k: number) => 1 - Math.pow(1 - k, 4)

const OPEN_MS = 840 // same as sign-in's sheet opening
const FLY_MS = 560 // same as sign-in's pieces
const CRACK_MS = 260 // same as sign-in's cross
const GLIDE_MS = 560 // same as sign-in's glide
const EASE_IN_OUT = 'cubic-bezier(0.65, 0, 0.35, 1)'
const FLY_IN = 'cubic-bezier(0.1, 0.65, 0.4, 1)' // time-reverse of sign-in's fly-out (0.6, 0, 0.9, 0.35)
const CRACK_IN = 'cubic-bezier(0.7, 0, 0.84, 0)' // time-reverse of sign-in's crack (0.16, 1, 0.3, 1)

let lastCard: { width: number; height: number } | null = null
export const rememberCardSize = (r: { width: number; height: number }) => { lastCard = { width: r.width, height: r.height } }

let pendingReturn: { layer: HTMLElement; whole: HTMLElement; card: Rect } | null = null
export const isReturning = () => pendingReturn !== null

function el(cls: string, style: Partial<CSSStyleDeclaration>, parent: HTMLElement) {
  const d = document.createElement('div')
  d.className = cls
  Object.assign(d.style, style)
  parent.appendChild(d)
  return d
}
const px = (n: number) => `${n}px`

// Exact time-reverse of Handoff's openSheet: full -> card over the first 70% (ease-in-out), then card -> point over
// the last 30% (the opening's ease-out, run backwards).
function closeSheet(card: Rect, ms: number) {
  return new Promise<void>((done) => {
    const s0 = document.querySelector('main.sheet')?.getBoundingClientRect()
    if (!s0) return done()
    const t0 = performance.now()
    const cx = window.innerWidth / 2
    const cy = window.innerHeight / 2
    const frame = (now: number) => {
      const k = Math.min(1, (now - t0) / ms)
      const s = document.querySelector('main.sheet')?.getBoundingClientRect() ?? s0
      const cl = cx - card.width / 2
      const ct = cy - card.height / 2
      let x: number; let y: number; let w: number; let h: number
      if (k < 0.7) {
        const e = easeInOut(1 - k / 0.7) // 1 -> 0: full -> card
        x = cl + (s.left - cl) * e; y = ct + (s.top - ct) * e
        w = card.width + (s.width - card.width) * e; h = card.height + (s.height - card.height) * e
      } else {
        const e = easeOut4(1 - (k - 0.7) / 0.3) // 1 -> 0: card -> point
        w = card.width * e; h = 4 + (card.height - 4) * e; x = cx - w / 2; y = cy - h / 2
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

export async function startSignout(signOut: () => Promise<unknown>) {
  if (reduceMotion()) { await signOut(); return }
  void import('./pages/Login') // fetch the sign-in page now, so the card can land the moment we sign out
  const w = lastCard?.width ?? 440
  const h = lastCard?.height ?? 380
  const card = { left: window.innerWidth / 2 - w / 2, top: window.innerHeight / 2 - h / 2, width: w, height: h }
  const hw = w / 2
  const hh = h / 2

  const layer = el('handoff', {}, document.body)
  layer.setAttribute('aria-hidden', 'true')
  const quads = [
    { left: card.left, top: card.top, r: '16px 0 0 0', f: [-1, -1, -5] }, { left: card.left + hw, top: card.top, r: '0 16px 0 0', f: [1, -1, 4] },
    { left: card.left, top: card.top + hh, r: '0 0 0 16px', f: [-1, 1, 4] }, { left: card.left + hw, top: card.top + hh, r: '0 0 16px 0', f: [1, 1, -5] },
  ]
  const pieces = quads.map((q) => el('ho-piece', {
    left: px(q.left), top: px(q.top), width: px(hw), height: px(hh), borderRadius: q.r, visibility: 'visible', opacity: '0',
  }, layer))
  const whole = el('ho-card', { left: px(card.left), top: px(card.top), width: px(w), height: px(h), visibility: 'hidden' }, layer)
  const crackH = el('ho-crack h', { left: px(card.left + 10), top: px(card.top + hh - 1), width: px(w - 20), opacity: '0' }, layer)
  const crackV = el('ho-crack v', { left: px(card.left + hw - 1), top: px(card.top + 10), height: px(h - 20), opacity: '0' }, layer)

  try {
    // 1. chrome and page content fade out first (sign-in fades chrome in last)
    root().style.setProperty('--ho-clip', 'inset(0px 0px 0px 0px round 16px)') // full size first: no one-frame flash
    root().setAttribute('data-handoff', 'closing')
    await wait(380)

    // 2. sheet closes full -> card -> point; pieces fly in from 340 ms and land 60 ms after the sheet vanishes
    const fly = pieces.map((p, i) => {
      const [sx, sy, rot] = quads[i]!.f
      return p.animate([
        { transform: `translate(${sx! * 70}px, ${sy! * 48}px) rotate(${rot}deg) scale(0.94)`, opacity: 0 },
        { transform: 'none', opacity: 1 },
      ], { duration: FLY_MS, delay: OPEN_MS + 60 - FLY_MS, easing: FLY_IN, fill: 'forwards' }).finished
    })
    await Promise.all([closeSheet(card, OPEN_MS), ...fly])

    // 3. the pieces have met: the whole card with the full cobalt cross, which shrinks from its ends to the middle
    whole.style.visibility = 'visible'
    pieces.forEach((p) => { p.style.visibility = 'hidden' })
    crackH.style.opacity = '1'
    crackV.style.opacity = '1'
    crackH.style.transform = 'scaleX(1)'
    crackV.style.transform = 'scaleY(1)'
    const shrink = { duration: CRACK_MS, easing: CRACK_IN, fill: 'forwards' as const }
    crackV.animate([{ transform: 'scaleY(1)' }, { transform: 'scaleY(0)' }], shrink)
    await crackH.animate([{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }], { ...shrink, delay: 60 }).finished

    // 4. sign out; the sign-in page claims the card and it glides home (landCard)
    pendingReturn = { layer, whole, card }
    root().setAttribute('data-handoff', 'landing') // sign-in page mounts with its scene and card hidden
    root().style.removeProperty('--ho-clip')
    await signOut()
    // If the sign-in page never claims the card (e.g. it was already open), finish after a moment.
    setTimeout(() => { if (pendingReturn?.layer === layer) finishReturn() }, 2500)
  } catch {
    finishReturn()
    await signOut()
  }
}

function finishReturn() {
  pendingReturn?.layer.remove()
  pendingReturn = null
  root().removeAttribute('data-handoff')
  root().style.removeProperty('--ho-clip')
}

// Called by the sign-in page once it has laid out its card: the card glides there (reverse of sign-in's glide)
// while the dot scene fades in; then the real card takes over and its contents rise.
export async function landCard(target: Rect) {
  const p = pendingReturn
  if (!p) return
  root().setAttribute('data-handoff', 'arrive')
  const sx = target.width / p.card.width
  const sy = target.height / p.card.height
  p.whole.style.transformOrigin = 'top left'
  await p.whole.animate([
    { transform: 'none' },
    { transform: `translate(${target.left - p.card.left}px, ${target.top - p.card.top}px) scale(${sx}, ${sy})` },
  ], { duration: GLIDE_MS, easing: EASE_IN_OUT, fill: 'forwards' }).finished.catch(() => undefined)
  finishReturn()
}
