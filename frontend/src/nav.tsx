// Navigation wrapped in the View Transitions API (crossfade + shared customer-ID element).
// Browsers without it, and reduced-motion users, navigate instantly.
import { forwardRef, type AnchorHTMLAttributes, type MouseEvent } from 'react'
import { flushSync } from 'react-dom'
import { NavLink, useNavigate, type NavLinkProps, type To } from 'react-router-dom'

export function withTransition(update: () => void) {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (!document.startViewTransition || reduce) return update()
  document.startViewTransition(() => flushSync(update))
}

export function useGo() {
  const navigate = useNavigate()
  return (to: To) => withTransition(() => navigate(to))
}

const plainClick = (e: MouseEvent) => e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey

// NavLink that animates; modified clicks (new tab etc.) keep native behaviour.
export const VNavLink = forwardRef<HTMLAnchorElement, NavLinkProps & AnchorHTMLAttributes<HTMLAnchorElement>>(
  function VNavLink({ onClick, to, ...rest }, ref) {
    const go = useGo()
    return (
      <NavLink ref={ref} to={to} {...rest} onClick={(e) => {
        onClick?.(e)
        if (e.defaultPrevented || !plainClick(e)) return
        e.preventDefault()
        go(to)
      }} />
    )
  },
)
