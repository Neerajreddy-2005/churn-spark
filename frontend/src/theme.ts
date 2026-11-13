import { useState } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
const KEY = 'cs-theme'

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

// System by default; an explicit choice is stored per browser and applied as data-theme on <html>.
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [theme, setThemeState] = useState<ThemeChoice>(read)
  const setTheme = (t: ThemeChoice) => {
    setThemeState(t)
    try {
      if (t === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, t)
    } catch { /* storage blocked: still applies for this visit */ }
    const apply = () => {
      if (t === 'system') document.documentElement.removeAttribute('data-theme')
      else document.documentElement.setAttribute('data-theme', t)
    }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (document.startViewTransition && !reduce) document.startViewTransition(apply)
    else apply()
  }
  return [theme, setTheme]
}
