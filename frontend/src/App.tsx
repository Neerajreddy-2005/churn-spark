import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { supabase } from './supabase'
import { AccountMenu } from './components'
import { CommandPalette, type PageEntry } from './CommandPalette'
import { useGo, VNavLink } from './nav'
import { Skeleton } from './ui'
import { Handoff } from './Handoff'
import { startSignout } from './Signout'
import {
  IconAudit, IconOrg, IconOverview, IconRisk, IconRuns, IconTable, IconSearch, IconTeam, IconUpload, Mark,
} from './icons'

// Routes load on demand, so the sign-in page does not download every screen.
const Login = lazy(() => import('./pages/Login'))
const Overview = lazy(() => import('./pages/Overview'))
const Upload = lazy(() => import('./pages/Upload'))
const Runs = lazy(() => import('./pages/Runs'))
const Datasets = lazy(() => import('./pages/Datasets'))
const DatasetDetail = lazy(() => import('./pages/DatasetDetail'))
const RunDetail = lazy(() => import('./pages/RunDetail'))
const Customers = lazy(() => import('./pages/Customers'))
const CustomerDetail = lazy(() => import('./pages/CustomerDetail'))
const ChangePassword = lazy(() => import('./pages/ChangePassword'))
const AdminTeam = lazy(() => import('./pages/AdminTeam'))
const AdminOrgs = lazy(() => import('./pages/AdminOrgs'))
const AdminOrgDetail = lazy(() => import('./pages/AdminOrgDetail'))
const AdminAudit = lazy(() => import('./pages/AdminAudit'))
const ForgotPassword = lazy(() => import('./pages/ForgotPassword'))
const ResetPassword = lazy(() => import('./pages/ResetPassword'))
const AccountPassword = lazy(() => import('./pages/AccountPassword'))
const MfaGate = lazy(() => import('./pages/MfaGate'))

function Shell() {
  const { session, loading, org, profile, isPlatformAdmin } = useAuth()
  const { pathname } = useLocation()
  const go = useGo()
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Phone tab row: keep the current page's tab in view.
  useEffect(() => {
    document.querySelector('.sidenav a.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [pathname])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Same frame as the signed-in page, so a refresh (or the sign-in hand-off) has a stable target.
  if (loading) {
    return (
      <div className="shell">
        <header className="topbar"><span className="wordmark"><Mark />Churn-Spark</span></header>
        <nav className="sidenav" aria-label="Main" />
        <main className="sheet" id="main"><div className="sheet-inner"><Skeleton /></div></main>
      </div>
    )
  }
  if (!session) return <Navigate to="/login" replace />
  const signOut = () => void supabase.auth.signOut()
  // No profile: either a platform admin ("admin mode", no organisation, no business data) or an
  // account that is not set up.
  if (!profile && !isPlatformAdmin) {
    return (
      <div className="auth-simple">
        <span className="wordmark"><Mark />Churn-Spark</span>
        <div className="auth-sheet">
          <h1>Your account isn't set up yet</h1>
          <p className="muted">Ask your manager or administrator to finish setting up your account, then sign in again.</p>
          <button className="plain" onClick={signOut}>Sign out</button>
        </div>
      </div>
    )
  }
  if (isPlatformAdmin && !/^\/admin\/(organisations|audit)(\/|$)/.test(pathname) &&
      pathname !== '/account/password') {
    return <Navigate to="/admin/organisations" replace />
  }
  const mustChange = profile?.must_change_password === true
  if (mustChange && pathname !== '/change-password') return <Navigate to="/change-password" replace />
  const isManager = profile?.role === 'manager'

  const pages: PageEntry[] = isPlatformAdmin
    ? [
        { label: 'Organisations', to: '/admin/organisations', icon: <IconOrg /> },
        { label: 'Audit log', to: '/admin/audit', icon: <IconAudit /> },
      ]
    : mustChange
      ? []
      : [
          { label: 'Overview', to: '/', icon: <IconOverview /> },
          { label: 'At-risk customers', to: '/customers', icon: <IconRisk /> },
          { label: 'Upload data', to: '/upload', icon: <IconUpload /> },
          { label: 'Datasets', to: '/datasets', icon: <IconTable /> },
          { label: 'Pipeline runs', to: '/runs', icon: <IconRuns /> },
          ...(isManager ? [{ label: 'Team', to: '/admin/team', icon: <IconTeam /> }] : []),
        ]
  const main = pages.filter((p) => p.to !== '/admin/team')
  const manage = pages.filter((p) => p.to === '/admin/team')

  return (
    <div className="shell">
      <header className="topbar">
        <Link to={isPlatformAdmin ? '/admin/organisations' : '/'} className="wordmark" aria-label="Churn-Spark home">
          <Mark />Churn-Spark
        </Link>
        <span className="spacer" />
        {pages.length > 0 && (
          <button type="button" className="search-trigger" onClick={() => setPaletteOpen(true)} aria-label="Search (Ctrl+D)">
            <IconSearch /><span>{isPlatformAdmin ? 'Search organisations' : 'Search customers'}</span><kbd>Ctrl D</kbd>
          </button>
        )}
        <AccountMenu
          label={isPlatformAdmin ? 'Platform admin' : (org?.name ?? 'No organisation')}
          name={profile?.display_name ?? null}
          email={session.user.email}
          showChangePassword={!mustChange}
          onChangePassword={() => go('/account/password')}
          onSignOut={() => void startSignout(() => supabase.auth.signOut())} />
      </header>
      <nav className="sidenav" aria-label="Main">
        {main.map((p) => (
          <VNavLink key={p.to} to={p.to} end={p.to === '/'}>{p.icon}{p.label}</VNavLink>
        ))}
        {manage.length > 0 && <span className="group">Manage</span>}
        {manage.map((p) => (
          <VNavLink key={p.to} to={p.to}>{p.icon}{p.label}</VNavLink>
        ))}
      </nav>
      <main className="sheet" id="main">
        <div className="sheet-inner">
          <Suspense fallback={<Skeleton />}>
            {isPlatformAdmin ? <MfaGate><Outlet /></MfaGate> : <Outlet />}
          </Suspense>
        </div>
      </main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} pages={pages} isPlatformAdmin={isPlatformAdmin} />
    </div>
  )
}

export default function App() {
  return (
    <Suspense fallback={null}>
      <Handoff />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route element={<Shell />}>
          <Route path="/" element={<Overview />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="/datasets/:uploadId" element={<DatasetDetail />} />
          <Route path="/runs/:runId" element={<RunDetail />} />
          <Route path="/customers" element={<Customers />} />
          <Route path="/customers/:customerId" element={<CustomerDetail />} />
          <Route path="/change-password" element={<ChangePassword />} />
          <Route path="/account/password" element={<AccountPassword />} />
          <Route path="/admin/team" element={<AdminTeam />} />
          <Route path="/admin/organisations" element={<AdminOrgs />} />
          <Route path="/admin/organisations/:orgId" element={<AdminOrgDetail />} />
          <Route path="/admin/audit" element={<AdminAudit />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
