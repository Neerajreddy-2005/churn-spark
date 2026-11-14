-- Security audit fixes (shared worker queue and shared database capacity).
-- Apply BEFORE deploying the worker that orders the queue by queued_at.
--   #2 A re-review (submit_dataset_review) re-queued work without using the 20-per-24h budget and kept
--      the upload's original created_at, so one organisation could keep jumping ahead of every other
--      organisation's newer uploads. Re-reviews now count against the budget and join the back of the queue.
--   #7 Nothing limited how much one organisation keeps in the shared project. New uploads are refused
--      once an organisation holds 200 datasets or 2,000,000 scored rows.
--   #8 Retention actions had no volume limit. Now at most 1,000 per organisation in any 24 hours.
--   Hardening: a review may not use an ID or personal column (values hidden in the profile) as a
--      feature or target, since their raw values would then appear in the reasons; and a service-role
--      function ends a user's sessions so a break-glass password reset signs them out everywhere.
--   A policy's count cannot see rows added by the same statement, so one multi-row insert (a JSON array
--   sent to the Data API) passed the 0004 upload limit and would pass the new action limit. Statement
--   triggers now recount after every insert by a signed-in user.
-- The counts are not locked against concurrent inserts, so parallel requests may pass a limit by a few
-- rows; they cap volume, they are not exact quotas. The re-review path takes a per-organisation lock.

-- ---- #2: queue order and re-review budget ----

alter table public.uploads add column queued_at timestamptz;
update public.uploads set queued_at = created_at;
alter table public.uploads alter column queued_at set default now(), alter column queued_at set not null;
create index uploads_status_queued_idx on public.uploads (status, queued_at);

-- One row per re-review that queued a retrain; counted with uploads in the same 24-hour budget.
create table private.dataset_requeues (
    id bigint generated always as identity primary key,
    org_id uuid not null references public.organisations (id),
    upload_id uuid not null references public.uploads (id),
    created_at timestamptz not null default now()
);
create index dataset_requeues_org_created_idx on private.dataset_requeues (org_id, created_at);
revoke all on private.dataset_requeues from public, anon, authenticated;

-- Same signature as 0004, so the uploads_insert policy now counts re-reviews too.
create or replace function private.uploads_last_24h(p_org uuid) returns integer
language sql stable security definer set search_path = ''
as $$
    select (select count(*) from public.uploads
            where org_id = p_org and created_at > now() - interval '24 hours')::integer
         + (select count(*) from private.dataset_requeues
            where org_id = p_org and created_at > now() - interval '24 hours')::integer
$$;

-- As 0011, plus: the budget check under a per-organisation lock, the re-review log, and queued_at = now().
create or replace function public.submit_dataset_review(p_upload uuid, p_review jsonb) returns void
    language plpgsql security definer set search_path = '' as $$
declare
    v_org uuid := private.current_org_id();
    v_status text;
    v_profile jsonb;
    v_keys text[];
    v_used text[];
    v_target text;
    v_values text[];
    v_hidden text[];
begin
    if v_org is null then
        raise exception 'Not allowed' using errcode = '42501';
    end if;
    select u.status, d.profile into v_status, v_profile
        from public.uploads u join public.datasets d on d.upload_id = u.id
        where u.id = p_upload and u.org_id = v_org and d.kind = 'generic'
        for update of u;
    if not found then
        raise exception 'Dataset not found' using errcode = 'P0002';
    end if;
    if v_status not in ('needs_review', 'done', 'rejected') then
        raise exception 'This dataset is being processed; try again when it has finished' using errcode = '22023';
    end if;
    if jsonb_typeof(p_review) is distinct from 'object' or pg_column_size(p_review) > 20000 then
        raise exception 'Invalid review' using errcode = '22023';
    end if;

    if coalesce((p_review ->> 'explore_only')::boolean, false) then
        p_review := jsonb_build_object('explore_only', true);
    else
        select array_agg(c ->> 'key') into v_keys from jsonb_array_elements(v_profile -> 'columns') c;
        -- IS DISTINCT FROM, not <>: jsonb_typeof of a missing key is NULL, and NULL <> 'array' is not true.
        if jsonb_typeof(p_review -> 'features') is distinct from 'array'
           or jsonb_typeof(p_review -> 'target') is distinct from 'string'
           or jsonb_typeof(p_review -> 'positive') is distinct from 'string'
           or coalesce(jsonb_typeof(p_review -> 'id'), 'null') not in ('string', 'null') then
            raise exception 'Invalid review' using errcode = '22023';
        end if;
        select array_agg(f) into v_used from jsonb_array_elements_text(p_review -> 'features') f;
        v_target := p_review ->> 'target';
        -- ID / personal columns: the model may not use them (the ID may still be named as the ID).
        select array_agg(c ->> 'key') into v_hidden from jsonb_array_elements(v_profile -> 'columns') c
            where coalesce((c ->> 'values_hidden')::boolean, false);
        if coalesce(v_used, '{}') && coalesce(v_hidden, '{}') or v_target = any (coalesce(v_hidden, '{}')) then
            raise exception 'ID and personal columns cannot be used by the model' using errcode = '22023';
        end if;
        v_used := coalesce(v_used, '{}') || v_target || coalesce(p_review ->> 'id', v_target);
        if not (v_used <@ coalesce(v_keys, '{}')) then
            raise exception 'Unknown column in review' using errcode = '22023';
        end if;
        select array_agg(t ->> 0) into v_values
            from jsonb_array_elements(v_profile -> 'columns') c, jsonb_array_elements(c -> 'top') t
            where c ->> 'key' = v_target;
        if not ((p_review ->> 'positive') = any (coalesce(v_values, '{}'))) then
            raise exception 'The churn value is not a value of that column' using errcode = '22023';
        end if;
        p_review := jsonb_build_object('target', v_target, 'positive', p_review ->> 'positive',
                                       'id', p_review -> 'id', 'features', p_review -> 'features', 'auto', false);
    end if;

    -- Serialise this organisation's re-reviews so two calls cannot both see 19.
    perform pg_advisory_xact_lock(hashtextextended('dataset_requeue:' || v_org::text, 0));
    if private.uploads_last_24h(v_org) >= 20 then
        raise exception 'Limit reached: 20 uploads or re-reviews per organisation in 24 hours; try again later'
            using errcode = '54000';
    end if;

    update public.datasets
        set review = p_review, reviewed_by = (select auth.uid()), reviewed_at = now(), updated_at = now()
        where upload_id = p_upload;
    insert into private.dataset_requeues (org_id, upload_id) values (v_org, p_upload);
    update public.uploads set status = 'queued', status_reason = null, queued_at = now() where id = p_upload;
end;
$$;

-- ---- #7: total stored per organisation ----

create function private.org_storage_full(p_org uuid) returns boolean
language sql stable security definer set search_path = ''
as $$
    select (select count(*) from public.datasets where org_id = p_org) >= 200
        or (select count(*) from public.dataset_scores where org_id = p_org) >= 2000000
$$;

revoke execute on function private.org_storage_full(uuid) from public, anon;
grant execute on function private.org_storage_full(uuid) to authenticated;

-- As 0004, plus the storage ceiling.
drop policy uploads_insert on public.uploads;
create policy uploads_insert on public.uploads for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and uploaded_by = (select auth.uid())
        and status = 'queued'
        and file_path like org_id::text || '/%'
        and file_path ~ ('^' || org_id::text || '/[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$')
        and private.uploads_last_24h(org_id) < 20
        and not private.org_storage_full(org_id)
    );

drop policy uploads_bucket_insert on storage.objects;
create policy uploads_bucket_insert on storage.objects for insert to authenticated
    with check (
        bucket_id = 'uploads'
        and (storage.foldername(name))[1] = (select private.current_org_id())::text
        and name ~ '^[0-9a-f-]{36}/[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$'  -- one plain file name, as the app writes
        and private.upload_objects_last_24h((storage.foldername(name))[1]) < 20
        and not private.org_storage_full((select private.current_org_id()))
    );

-- ---- #8: retention action volume ----

create function private.retention_actions_last_24h(p_org uuid) returns integer
language sql stable security definer set search_path = ''
as $$
    select count(*)::integer from public.retention_actions
    where org_id = p_org and created_at > now() - interval '24 hours'
$$;

revoke execute on function private.retention_actions_last_24h(uuid) from public, anon;
grant execute on function private.retention_actions_last_24h(uuid) to authenticated;
create index retention_actions_org_created_idx on public.retention_actions (org_id, created_at);

-- As 0011, plus the 24-hour volume limit.
drop policy retention_actions_insert on public.retention_actions;
create policy retention_actions_insert on public.retention_actions for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and created_by = (select auth.uid())
        and private.retention_actions_last_24h(org_id) < 1000
        and (
            (dataset_id is null and exists (
                select 1 from public.customer_scores s
                where s.org_id = retention_actions.org_id
                  and s.customer_id = retention_actions.customer_id
                  and s.snapshot_month = retention_actions.snapshot_month))
            or
            (dataset_id is not null and exists (
                select 1 from public.dataset_scores d
                join public.uploads u on u.id = d.upload_id
                where d.org_id = retention_actions.org_id
                  and d.upload_id = retention_actions.dataset_id
                  and d.row_id = retention_actions.customer_id
                  and u.snapshot_month = retention_actions.snapshot_month))
        )
    );

-- ---- Limits that also hold for multi-row inserts ----
-- Run as the caller (not SECURITY DEFINER) so current_user tells a signed-in user apart from the worker
-- and the operator scripts, which these limits do not apply to.

create function private.enforce_upload_budget() returns trigger
language plpgsql set search_path = ''
as $$
begin
    if current_user = 'authenticated' and exists (
        select 1 from (select distinct org_id from new_rows) o where private.uploads_last_24h(o.org_id) > 20) then
        raise exception 'Limit reached: 20 uploads or re-reviews per organisation in 24 hours; try again later'
            using errcode = '54000';
    end if;
    return null;
end;
$$;

create trigger uploads_budget after insert on public.uploads
    referencing new table as new_rows for each statement execute function private.enforce_upload_budget();

create function private.enforce_action_budget() returns trigger
language plpgsql set search_path = ''
as $$
begin
    if current_user = 'authenticated' and exists (
        select 1 from (select distinct org_id from new_rows) o
        where private.retention_actions_last_24h(o.org_id) > 1000) then
        raise exception 'Limit reached: 1,000 retention actions per organisation in 24 hours; try again later'
            using errcode = '54000';
    end if;
    return null;
end;
$$;

create trigger retention_actions_budget after insert on public.retention_actions
    referencing new table as new_rows for each statement execute function private.enforce_action_budget();

-- ---- Hardening: end a user's sessions (break-glass password reset) ----
-- Deleting the sessions also deletes their refresh tokens, so no new access token can be issued;
-- an access token already issued stays valid until it expires (the JWT expiry, 1 hour by default).
create function public.admin_revoke_sessions(p_user uuid) returns integer
language sql security definer set search_path = ''
as $$
    with gone as (delete from auth.sessions where user_id = p_user returning 1)
    select count(*)::integer from gone
$$;
revoke execute on function public.admin_revoke_sessions(uuid) from public, anon, authenticated;
grant execute on function public.admin_revoke_sessions(uuid) to service_role;
