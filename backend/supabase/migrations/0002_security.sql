-- Phase 7a: row-level security, grants and policies.
-- Nothing is reachable through the Data API until granted below. No signup trigger:
-- a user without a profile row has a null current_org_id() and sees nothing.

create function private.current_org_id() returns uuid
language sql stable security definer set search_path = ''
as $$ select org_id from public.profiles where user_id = auth.uid() $$;

revoke execute on function private.current_org_id() from public, anon;
grant execute on function private.current_org_id() to authenticated;
-- Needed so policies can call the function; the schema itself stays out of the Data API.
grant usage on schema private to authenticated;

alter table public.organisations enable row level security;
alter table public.profiles enable row level security;
alter table public.uploads enable row level security;
alter table public.pipeline_runs enable row level security;
alter table public.dq_results enable row level security;
alter table public.reconciliation_results enable row level security;
alter table public.customer_scores enable row level security;
alter table public.segment_aggregates enable row level security;
alter table public.retention_actions enable row level security;

revoke all on all tables in schema public from anon, authenticated;

grant select on public.organisations, public.profiles, public.uploads, public.pipeline_runs,
    public.dq_results, public.reconciliation_results, public.customer_scores,
    public.segment_aggregates, public.retention_actions to authenticated;
grant insert (org_id, uploaded_by, file_path, original_name, size_bytes, snapshot_month)
    on public.uploads to authenticated;
grant insert (org_id, customer_id, snapshot_month, action_type, note, created_by)
    on public.retention_actions to authenticated;
grant update (outcome, note, updated_at) on public.retention_actions to authenticated;

create policy organisations_select on public.organisations for select to authenticated
    using (id = (select private.current_org_id()));

create policy profiles_select on public.profiles for select to authenticated
    using (org_id = (select private.current_org_id()));

create policy uploads_select on public.uploads for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy uploads_insert on public.uploads for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and uploaded_by = (select auth.uid())
        and status = 'queued'
        and file_path like org_id::text || '/%'
    );

create policy pipeline_runs_select on public.pipeline_runs for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy dq_results_select on public.dq_results for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy reconciliation_results_select on public.reconciliation_results for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy customer_scores_select on public.customer_scores for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy segment_aggregates_select on public.segment_aggregates for select to authenticated
    using (org_id = (select private.current_org_id()));

create policy retention_actions_select on public.retention_actions for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy retention_actions_insert on public.retention_actions for insert to authenticated
    with check (org_id = (select private.current_org_id()) and created_by = (select auth.uid()));
create policy retention_actions_update on public.retention_actions for update to authenticated
    using (org_id = (select private.current_org_id()) and created_by = (select auth.uid()))
    with check (org_id = (select private.current_org_id()) and created_by = (select auth.uid()));
