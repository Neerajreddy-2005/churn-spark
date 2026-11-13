// Ctrl/Cmd + D: jump to a page, a customer (by ID) or an organisation (platform admin).
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { supabase } from './supabase'
import { useGo } from './nav'
import { IconCustomer, IconOrg, IconSearch } from './icons'

export interface PageEntry { label: string; to: string; icon: ReactNode }
interface Item { key: string; group: string; label: string; hint?: string; to: string; icon: ReactNode }

const likeEscape = (s: string) => s.replace(/[\\%_]/g, (c) => `\\${c}`)

export function CommandPalette({ open, onClose, pages, isPlatformAdmin }: {
  open: boolean; onClose: () => void; pages: PageEntry[]; isPlatformAdmin: boolean
}) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Item[]>([])
  const [searching, setSearching] = useState(false)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const restore = useRef<HTMLElement | null>(null)
  const go = useGo()

  useEffect(() => {
    if (!open) return
    restore.current = document.activeElement as HTMLElement
    setQ('')
    setResults([])
    setActive(0)
    setTimeout(() => input.current?.focus(), 0)
    return () => restore.current?.focus()
  }, [open])

  // Debounced server search; RLS scopes customers to the user's own company.
  useEffect(() => {
    const term = q.trim()
    if (!open || term.length < 2) { setResults([]); setSearching(false); return }
    setSearching(true)
    let cancelled = false
    const t = setTimeout(async () => {
      let items: Item[] = []
      if (isPlatformAdmin) {
        const { data } = await supabase.from('organisations').select('id, name').ilike('name', `%${likeEscape(term)}%`).limit(6)
        items = (data ?? []).map((o: { id: string; name: string }) => ({
          key: `o-${o.id}`, group: 'Organisations', label: o.name, to: `/admin/organisations/${o.id}`, icon: <IconOrg />,
        }))
      } else {
        const { data } = await supabase.from('customer_scores')
          .select('customer_id, snapshot_month, risk_band')
          .ilike('customer_id', `%${likeEscape(term)}%`)
          .order('snapshot_month', { ascending: false }).limit(30)
        const seen = new Set<string>()
        for (const r of (data ?? []) as { customer_id: string; snapshot_month: string; risk_band: string | null }[]) {
          if (seen.has(r.customer_id) || seen.size >= 6) continue
          seen.add(r.customer_id)
          items.push({
            key: `c-${r.customer_id}`, group: 'Customers', label: r.customer_id,
            hint: `${r.risk_band ? `${r.risk_band} risk · ` : ''}${r.snapshot_month}`,
            to: `/customers/${encodeURIComponent(r.customer_id)}?month=${r.snapshot_month}`, icon: <IconCustomer />,
          })
        }
      }
      if (!cancelled) { setResults(items); setSearching(false) }
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [q, open, isPlatformAdmin])

  if (!open) return null
  const term = q.trim().toLowerCase()
  const pageItems: Item[] = pages
    .filter((p) => !term || p.label.toLowerCase().includes(term))
    .map((p) => ({ key: `p-${p.to}`, group: 'Pages', label: p.label, to: p.to, icon: p.icon }))
  const items = [...results, ...pageItems]
  const sel = Math.min(active, Math.max(0, items.length - 1))

  const choose = (it: Item) => { onClose(); go(it.to) }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((sel + 1) % Math.max(1, items.length)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((sel - 1 + items.length) % Math.max(1, items.length)) }
    else if (e.key === 'Enter' && items[sel]) { e.preventDefault(); choose(items[sel]) }
    else if (e.key === 'Tab') e.preventDefault() // focus stays in the dialog
  }

  let lastGroup = ''
  return (
    <div className="cmdk-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Search" onKeyDown={onKey}>
        <div className="cmdk-input">
          <IconSearch />
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setActive(0) }}
            placeholder={isPlatformAdmin ? 'Search organisations or pages' : 'Search a customer ID or a page'}
            role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list"
            aria-activedescendant={items[sel] ? `cmdk-${items[sel].key}` : undefined} />
        </div>
        <ul className="cmdk-list" id="cmdk-list" role="listbox">
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? it.group : null
            lastGroup = it.group
            return (
              <li key={it.key} role="presentation">
                {head && <div className="cmdk-group" role="presentation">{head}</div>}
                <div id={`cmdk-${it.key}`} role="option" aria-selected={i === sel} className="cmdk-item"
                  onMouseMove={() => setActive(i)} onClick={() => choose(it)}>
                  {it.icon}<span>{it.label}</span>{it.hint && <span className="hint">{it.hint}</span>}
                </div>
              </li>
            )
          })}
          {!items.length && (
            <li className="cmdk-empty">{searching ? 'Searching…' : `Nothing matches “${q.trim()}”.`}</li>
          )}
        </ul>
        <div className="cmdk-foot" aria-hidden="true">
          <span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span>
        </div>
      </div>
    </div>
  )
}
