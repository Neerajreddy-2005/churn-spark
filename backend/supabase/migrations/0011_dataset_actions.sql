-- Retention actions on customers of any dataset (Mode 2), not only the certified Telco scores.
-- An action targets EITHER a Telco score (dataset_id is null, as before) OR a scored row of one of the
-- organisation's own datasets (dataset_id = datasets.upload_id, customer_id = dataset_scores.row_id).
-- snapshot_month stays required: for a dataset it is the month of its upload.

alter table public.retention_actions
    add column dataset_id uuid references public.datasets (upload_id);
create index retention_actions_dataset_idx on public.retention_actions (org_id, dataset_id, customer_id);

grant insert (dataset_id) on public.retention_actions to authenticated;

-- Same rule as 0004, extended: the subqueries run under the caller's RLS (own organisation only).
drop policy retention_actions_insert on public.retention_actions;
create policy retention_actions_insert on public.retention_actions for insert to authenticated
    with check (
        org_id = (select private.current_org_id())
        and created_by = (select auth.uid())
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

-- "Change columns": a dataset may be reviewed again after it was trained (done) or refused (rejected),
-- which queues a retrain. Never while it is queued or processing. Same checks as 0010 otherwise.
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

    update public.datasets
        set review = p_review, reviewed_by = (select auth.uid()), reviewed_at = now(), updated_at = now()
        where upload_id = p_upload;
    update public.uploads set status = 'queued', status_reason = null where id = p_upload;
end;
$$;
