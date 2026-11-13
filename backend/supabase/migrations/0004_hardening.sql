-- Phase 7a hardening for a publicly reachable demo:
-- 1) referential integrity for pipeline outputs, 2) per-organisation upload limits
-- (table rows and storage objects), 3) retention actions must target a scored customer.

-- 1) Pipeline outputs belong to an existing organisation.
alter table public.pipeline_runs
    add constraint pipeline_runs_org_fk foreign key (org_id) references public.organisations (id);
alter table public.dq_results
    add constraint dq_results_org_fk foreign key (org_id) references public.organisations (id);
alter table public.reconciliation_results
    add constraint reconciliation_results_org_fk foreign key (org_id) references public.organisations (id);
alter table public.customer_scores
    add constraint customer_scores_org_fk foreign key (org_id) references public.organisations (id);
alter table public.segment_aggregates
    add constraint segment_aggregates_org_fk foreign key (org_id) references public.organisations (id);

-- 2) At most 20 uploads per organisation in any 24 hours. Counted with SECURITY DEFINER
-- helpers so the count does not depend on what the caller may see.
create function private.uploads_last_24h(p_org uuid) returns integer
language sql stable security definer set search_path = ''
as $$
    select count(*)::integer from public.uploads
    where org_id = p_org and created_at > now() - interval '24 hours'
$$;

create function private.upload_objects_last_24h(p_org text) returns integer
language sql stable security definer set search_path = ''
as $$
    select count(*)::integer from storage.objects
    where bucket_id = 'uploads'
      and (storage.foldername(name))[1] = p_org
      and created_at > now() - interval '24 hours'
$$;

revoke execute on function private.uploads_last_24h(uuid) from public, anon;
revoke execute on function private.upload_objects_last_24h(text) from public, anon;
grant execute on function private.uploads_last_24h(uuid) to authenticated;
grant execute on function private.upload_objects_last_24h(text) to authenticated;

drop policy uploads_insert on public.uploads;
create policy uploads_insert on public.uploads for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and uploaded_by = (select auth.uid())
        and status = 'queued'
        and file_path like org_id::text || '/%'
        and private.uploads_last_24h(org_id) < 20
    );

drop policy uploads_bucket_insert on storage.objects;
create policy uploads_bucket_insert on storage.objects for insert to authenticated
    with check (
        bucket_id = 'uploads'
        and (storage.foldername(name))[1] = (select private.current_org_id())::text
        and private.upload_objects_last_24h((storage.foldername(name))[1]) < 20
    );

-- 3) A retention action must reference a customer scored for the same org and month.
-- The subquery runs under the caller's RLS, which already limits it to their own org.
drop policy retention_actions_insert on public.retention_actions;
create policy retention_actions_insert on public.retention_actions for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and created_by = (select auth.uid())
        and exists (
            select 1 from public.customer_scores s
            where s.org_id = retention_actions.org_id
              and s.customer_id = retention_actions.customer_id
              and s.snapshot_month = retention_actions.snapshot_month
        )
    );
