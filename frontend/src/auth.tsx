import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './supabase'
import { callAdmin } from './admin'
import type { Organisation, Profile } from './types'

interface AuthState {
  session: Session | null
  loading: boolean
  profile: Profile | null
  org: Organisation | null
  isPlatformAdmin: boolean
  notice: string | null
  reloadProfile: () => void
}

const AuthContext = createContext<AuthState>({
  session: null, loading: true, profile: null, org: null, isPlatformAdmin: false, notice: null,
  reloadProfile: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [org, setOrg] = useState<Organisation | null>(null)
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const reloadProfile = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setSessionLoading(false)
    })
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s))
    return () => data.subscription.unsubscribe()
  }, [])

  const userId = session?.user.id
  useEffect(() => {
    if (!userId) {
      setProfile(null)
      setOrg(null)
      setIsPlatformAdmin(false)
      return
    }
    let cancelled = false
    setProfileLoading(true)
    void (async () => {
      // profiles SELECT is org-wide under RLS, so filter to the signed-in user explicitly.
      const p = await supabase.from('profiles').select('*').eq('user_id', userId).maybeSingle()
      if (cancelled) return
      const prof = (p.data as Profile | null) ?? null
      if (prof && !prof.is_active) {
        setNotice('Your account has been deactivated. Contact your manager.')
        setProfileLoading(false)
        void supabase.auth.signOut()
        return
      }
      setNotice(null)
      setProfile(prof)
      // A platform admin can read every organisation, so ask for their own by id.
      const o = prof
        ? await supabase.from('organisations').select('id, name').eq('id', prof.org_id).maybeSingle()
        : null
      // "Admin mode" = signed in, no profile, and whoami says platform admin (profile-less only).
      const who = await callAdmin<{ is_platform_admin: boolean; has_org: boolean; role: string | null }>(
        { action: 'whoami' }).catch(() => null)
      if (cancelled) return
      setOrg((o?.data as Organisation | null) ?? null)
      setIsPlatformAdmin(!prof && who?.is_platform_admin === true && who.has_org === false)
      setProfileLoading(false)
    })()
    return () => {
      cancelled = true
    }
  }, [userId, tick])

  const loading = sessionLoading || (!!userId && profileLoading)
  return (
    <AuthContext.Provider value={{ session, loading, profile, org, isPlatformAdmin, notice, reloadProfile }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(AuthContext)
}
