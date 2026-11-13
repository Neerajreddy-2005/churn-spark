-- Separation of duties: a platform admin has NO organisation, an org member is never a platform
-- admin. Enforced here in the database, so no code path (Edge Function, CLI, SQL editor) can
-- create a dual account. The triggers check only new inserts/updates, so an account that is
-- already both (the owner's) keeps working until the owner revokes it with the CLI.

create function private.profiles_not_platform_admin() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
    if exists (select 1 from private.platform_admins where user_id = new.user_id) then
        raise exception 'A platform admin cannot belong to an organisation'
            using errcode = 'check_violation';
    end if;
    return new;
end
$$;

create function private.platform_admin_has_no_profile() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
    if exists (select 1 from public.profiles where user_id = new.user_id) then
        raise exception 'This account belongs to an organisation and cannot be a platform admin'
            using errcode = 'check_violation';
    end if;
    return new;
end
$$;

revoke execute on function private.profiles_not_platform_admin() from public, anon, authenticated;
revoke execute on function private.platform_admin_has_no_profile() from public, anon, authenticated;

create trigger profiles_not_platform_admin
    before insert or update of user_id on public.profiles
    for each row execute function private.profiles_not_platform_admin();

create trigger platform_admin_has_no_profile
    before insert on private.platform_admins
    for each row execute function private.platform_admin_has_no_profile();

-- Platform admins read org-management data across organisations (SELECT only).
-- organisations already has one (0005). Business data (customer_scores, segment_aggregates,
-- uploads, pipeline_runs, dq_results, reconciliation_results, retention_actions, storage) gets
-- no platform-admin policy: a platform admin has no org, so current_org_id() is NULL for them.
-- Platform admins must use TOTP MFA: every platform-admin policy also requires an aal2 session
-- (the JWT's aal claim is set by Supabase Auth only after a verified second factor).
drop policy organisations_select_platform on public.organisations;
create policy organisations_select_platform on public.organisations for select to authenticated
    using ((select private.is_platform_admin()) and (select auth.jwt() ->> 'aal') = 'aal2');

create policy profiles_select_platform on public.profiles for select to authenticated
    using ((select private.is_platform_admin()) and (select auth.jwt() ->> 'aal') = 'aal2');

create policy audit_log_select_platform on public.audit_log for select to authenticated
    using ((select private.is_platform_admin()) and (select auth.jwt() ->> 'aal') = 'aal2');
