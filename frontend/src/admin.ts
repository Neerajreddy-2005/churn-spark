import { FunctionsHttpError } from '@supabase/supabase-js'
import { supabase } from './supabase'

// Calls the admin-users Edge Function as the signed-in user (JWT is sent automatically).
// Never send org_id for manager calls: the server takes the org from the caller's profile.
export async function callAdmin<T>(body: Record<string, unknown>): Promise<T> {
  const { data, error } = await supabase.functions.invoke('admin-users', { body })
  if (error) {
    let message = 'Request failed'
    if (error instanceof FunctionsHttpError) {
      const j = (await error.context.json().catch(() => null)) as { error?: string } | null
      if (j?.error) message = j.error
    }
    throw new Error(message)
  }
  return data as T
}
