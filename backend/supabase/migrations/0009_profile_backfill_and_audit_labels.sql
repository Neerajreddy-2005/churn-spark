-- 1) Older accounts (created before the Team page) may have no email on their profile.
update public.profiles p set email = u.email
from auth.users u
where u.id = p.user_id and p.email is null;

-- 2) New audit action 'set_name' (the Edge Function's "Edit name").
alter table public.audit_log drop constraint if exists audit_log_action_check;
alter table public.audit_log add constraint audit_log_action_check
    check (action in ('create_org', 'create_user', 'set_role', 'deactivate_user',
                      'reactivate_user', 'reset_password', 'set_name'));

-- 3) Readable audit rows: the function now writes actor_label / target_label into details.
-- Backfill the same for existing rows. A platform admin (no profile) reads
-- 'Platform admin (<email>)'. Labels that cannot be resolved (deleted accounts) are left out.
update public.audit_log a
set details = (case when jsonb_typeof(a.details) = 'object' then a.details else '{}'::jsonb end)
    || jsonb_strip_nulls(jsonb_build_object(
        'actor_label', (
            select case when p.user_id is null and pa.user_id is not null
                        then 'Platform admin (' || u.email || ')'
                        else coalesce(p.display_name, p.email, u.email) end
            from auth.users u
            left join public.profiles p on p.user_id = u.id
            left join private.platform_admins pa on pa.user_id = u.id
            where u.id = a.actor_id),
        'target_label', (
            select coalesce(p.display_name, p.email, u.email)
            from auth.users u
            left join public.profiles p on p.user_id = u.id
            where u.id = a.target_user_id)))
where a.actor_id is not null or a.target_user_id is not null;
