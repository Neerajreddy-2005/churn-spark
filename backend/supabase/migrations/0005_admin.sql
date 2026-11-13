-- Admin panel: platform admins, profile status columns, audit log, role helpers.
-- Writes to profiles/audit_log for user management happen only in the admin-users Edge
-- Function (secret key). The one write a user may do is clear their own must_change_password.

-- Platform admins (the owner). Not reachable through the Data API; granted by CLI only.
create table private.platform_admins (
    user_id uuid primary key references auth.users (id) on delete cascade,
    created_at timestamptz not null default now()
);
revoke all on private.platform_admins from public, anon, authenticated;

alter table public.profiles
    add column email text,
    add column is_active boolean not null default true,
    add column must_change_password boolean not null default false;

create table public.audit_log (
    id bigint generated always as identity primary key,
    org_id uuid references public.organisations (id),
    -- set null so deleting a user (CLI) is not blocked by their history
    actor_id uuid references auth.users (id) on delete set null,
    action text not null check (action in ('create_org', 'create_user', 'set_role',
        'deactivate_user', 'reactivate_user', 'reset_password')),
    target_user_id uuid,
    details jsonb,
    created_at timestamptz not null default now()
);
create index audit_log_org_created_idx on public.audit_log (org_id, created_at desc);
alter table public.audit_log enable row level security;
-- New tables get Supabase's default grants; strip them, then grant only SELECT.
revoke all on public.audit_log from public, anon, authenticated;
grant select on public.audit_log to authenticated;

-- Helpers (callers' own identity via auth.uid()).
create function private.is_manager() returns boolean
language sql stable security definer set search_path = ''
as $$ select exists (select 1 from public.profiles
                     where user_id = auth.uid() and role = 'manager' and is_active) $$;

create function private.is_platform_admin() returns boolean
language sql stable security definer set search_path = ''
as $$ select exists (select 1 from private.platform_admins where user_id = auth.uid()) $$;

revoke execute on function private.is_manager() from public, anon;
revoke execute on function private.is_platform_admin() from public, anon;
grant execute on function private.is_manager() to authenticated;
grant execute on function private.is_platform_admin() to authenticated;

-- The Edge Function (service role) checks platform-admin status of the JWT's user with this.
-- Executable by service_role only, so the private table stays unreachable to users.
create function public.admin_is_platform_admin(p_user uuid) returns boolean
language sql stable security definer set search_path = ''
as $$ select exists (select 1 from private.platform_admins where user_id = p_user) $$;
revoke execute on function public.admin_is_platform_admin(uuid) from public, anon, authenticated;
grant execute on function public.admin_is_platform_admin(uuid) to service_role;

-- Deactivated users get NULL, so every org policy returns nothing for them.
create or replace function private.current_org_id() returns uuid
language sql stable security definer set search_path = ''
as $$ select org_id from public.profiles where user_id = auth.uid() and is_active $$;

create policy audit_log_select on public.audit_log for select to authenticated
    using (org_id = (select private.current_org_id()) and (select private.is_manager()));

-- A platform admin can list every organisation.
create policy organisations_select_platform on public.organisations for select to authenticated
    using ((select private.is_platform_admin()));

-- A user can always read their own profile (a deactivated user must still learn why).
create policy profiles_select_self on public.profiles for select to authenticated
    using (user_id = (select auth.uid()));

-- A user may clear only their own must_change_password flag; the column grant blocks the rest.
grant update (must_change_password) on public.profiles to authenticated;
create policy profiles_clear_must_change on public.profiles for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()) and must_change_password = false);
