// Sign-in scene: a field of customers (dots). Now and then one turns red and starts to drift away (at risk);
// a cobalt ring catches it and it eases back home in cobalt (kept). Illustrative only: no real data.
import { useEffect, useRef } from 'react'

interface Dot { hx: number; hy: number; x: number; y: number; state: 'calm' | 'risk' | 'caught' | 'kept'; t: number; dx: number; dy: number }

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
const easeOut = (k: number) => 1 - Math.pow(1 - k, 3)
const easeInOut = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2)

export function LoginScene() {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const ctx = el.getContext('2d')
    if (!ctx) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let colors = { ink: '', accent: '', risk: '' }
    const readColors = () => { colors = { ink: css('--frame-ink'), accent: css('--accent'), risk: css('--risk') } }
    readColors()

    let dots: Dot[] = []
    let w = 0
    let h = 0
    const gap = 26
    const layout = () => {
      const r = el.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = r.width
      h = r.height
      el.width = Math.round(w * dpr)
      el.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      dots = []
      const cols = Math.floor(w / gap)
      const rows = Math.floor(h / gap)
      const ox = (w - (cols - 1) * gap) / 2
      const oy = (h - (rows - 1) * gap) / 2
      for (let r2 = 0; r2 < rows; r2++)
        for (let c = 0; c < cols; c++) {
          const hx = ox + c * gap
          const hy = oy + r2 * gap
          dots.push({ hx, hy, x: hx, y: hy, state: 'calm', t: 0, dx: 0, dy: 0 })
        }
      if (reduce) {
        // A still frame that tells the same story: a few kept, one at risk.
        dots.forEach((d, i) => { if (i % 37 === 5) d.state = 'kept'; if (i % 97 === 40) d.state = 'risk' })
      }
    }

    const draw = (now: number) => {
      ctx.clearRect(0, 0, w, h)
      for (const d of dots) {
        const age = now - d.t
        let radius = 2.4
        let color = colors.ink
        let alpha = 0.22
        if (d.state === 'risk') {
          const k = Math.min(1, age / 1400)
          d.x = d.hx + d.dx * easeOut(k)
          d.y = d.hy + d.dy * easeOut(k)
          color = colors.risk; alpha = 0.95; radius = 3.4
          if (!reduce && age > 1400) { d.state = 'caught'; d.t = now }
        } else if (d.state === 'caught') {
          const k = Math.min(1, age / 1100)
          d.x = d.hx + d.dx * (1 - easeInOut(k))
          d.y = d.hy + d.dy * (1 - easeInOut(k))
          color = colors.accent; alpha = 1; radius = 3.6
          ctx.globalAlpha = 0.5 * (1 - k)
          ctx.strokeStyle = colors.accent
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.arc(d.x, d.y, 6 + 22 * easeOut(k), 0, Math.PI * 2)
          ctx.stroke()
          if (k >= 1) { d.state = 'kept'; d.t = now }
        } else if (d.state === 'kept') {
          const k = reduce ? 0 : Math.min(1, age / 6000)
          color = colors.accent; alpha = 1 - 0.78 * k; radius = 3.2 - 0.8 * k
          if (k >= 1) d.state = 'calm'
        }
        ctx.globalAlpha = alpha
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(d.x, d.y, radius, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    let raf = 0
    let last = 0
    const tick = (now: number) => {
      if (now - last > 900 && dots.length) {
        last = now
        const calm = dots.filter((d) => d.state === 'calm')
        const d = calm[Math.floor(Math.random() * calm.length)]
        if (d) {
          const a = Math.random() * Math.PI * 2
          d.state = 'risk'; d.t = now; d.dx = Math.cos(a) * 34; d.dy = Math.sin(a) * 34
        }
      }
      draw(now)
      raf = requestAnimationFrame(tick)
    }

    layout()
    if (reduce) draw(performance.now())
    else raf = requestAnimationFrame(tick)

    const ro = new ResizeObserver(() => { layout(); if (reduce) draw(performance.now()) })
    ro.observe(el)
    // Follow theme changes (toggle or system).
    const mo = new MutationObserver(() => { readColors(); if (reduce) draw(performance.now()) })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onScheme = () => { readColors(); if (reduce) draw(performance.now()) }
    mq.addEventListener('change', onScheme)
    // Pause when the tab is hidden.
    const onVis = () => {
      if (reduce) return
      if (document.hidden) cancelAnimationFrame(raf)
      else raf = requestAnimationFrame(tick)
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); mo.disconnect()
      mq.removeEventListener('change', onScheme); document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  return <canvas ref={canvas} className="scene-canvas" aria-hidden="true" />
}
