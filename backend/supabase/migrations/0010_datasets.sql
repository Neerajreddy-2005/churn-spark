-- Mode 2: bring-your-own churn dataset (docs/design/MODE2.md).
-- Flow: upload -> worker profiles it -> status 'needs_review' -> a member of the org confirms the columns
-- through submit_dataset_review() -> status 'queued' again -> worker trains, scores -> 'done'.
-- Same security pattern as 0002: org-scoped RLS through private.current_org_id(); members never write
-- these tables directly (the worker writes as postgres; the review goes through one checked function).

-- 1) A new upload status: profiled and waiting for a person to confirm the columns.
alter table public.uploads drop constraint uploads_status_check;
alter table public.uploads add constraint uploads_status_check
    check (status in ('queued', 'processing', 'needs_review', 'done', 'rejected', 'failed'));

-- 2) One row per uploaded file that went through Mode 2 (and a marker row for Telco files).
create table public.datasets (
    upload_id uuid primary key references public.uploads (id),
    org_id uuid not null references public.organisations (id),
    kind text not null check (kind in ('telco', 'generic')),
    name text not null,
    source_info jsonb,          -- sheet / delimiter / rows / columns
    profile jsonb,              -- column statistics; values of ID and personal columns are never stored
    suggestions jsonb,          -- suggested target, ID, columns to leave out, with reasons
    review jsonb,               -- what the person confirmed (set only by submit_dataset_review)
    reviewed_by uuid references auth.users (id),
    reviewed_at timestamptz,
    checks jsonb,               -- general data checks g01..g07
    result jsonb,               -- metrics, leakage, drivers, insights, reconciliation
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index datasets_org_idx on public.datasets (org_id, created_at desc);

-- 3) Scores per row of a generic dataset.
create table public.dataset_scores (
    upload_id uuid not null references public.datasets (upload_id),
    org_id uuid not null references public.organisations (id),
    row_id text not null check (length(row_id) <= 200),
    risk_score numeric(5, 4) not null check (risk_score between 0 and 1),
    risk_band text not null check (risk_band in ('high', 'medium', 'low')),
    reason_1 text, reason_2 text, reason_3 text,
    churned boolean,
    in_training_set boolean not null default false,
    primary key (upload_id, row_id)
);
create index dataset_scores_rank_idx on public.dataset_scores (org_id, upload_id, risk_score desc);

alter table public.datasets enable row level security;
alter table public.dataset_scores enable row level security;
revoke all on public.datasets, public.dataset_scores from anon, authenticated;
grant select on public.datasets, public.dataset_scores to authenticated;

create policy datasets_select on public.datasets for select to authenticated
    using (org_id = (select private.current_org_id()));
create policy dataset_scores_select on public.dataset_scores for select to authenticated
    using (org_id = (select private.current_org_id()));

-- 4) The only way a member changes anything: confirm the review of a dataset of their own organisation.
-- Checks shape and that every column key exists in the stored profile; the worker re-checks the data.
create function public.submit_dataset_review(p_upload uuid, p_review jsonb) returns void
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
    if v_status <> 'needs_review' then
        raise exception 'This dataset is not waiting for a review' using errcode = '22023';
    end if;
    if jsonb_typeof(p_review) <> 'object' or pg_column_size(p_review) > 20000 then
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
        -- the chosen "left" value must be one of the target's stored values
        select array_agg(t ->> 0) into v_values
            from jsonb_array_elements(v_profile -> 'columns') c, jsonb_array_elements(c -> 'top') t
            where c ->> 'key' = v_target;
        if not ((p_review ->> 'positive') = any (coalesce(v_values, '{}'))) then
            raise exception 'The churn value is not a value of that column' using errcode = '22023';
        end if;
        p_review := jsonb_build_object('target', v_target, 'positive', p_review ->> 'positive',
                                       'id', p_review -> 'id', 'features', p_review -> 'features');
    end if;

    update public.datasets
        set review = p_review, reviewed_by = (select auth.uid()), reviewed_at = now(), updated_at = now()
        where upload_id = p_upload;
    update public.uploads set status = 'queued', status_reason = null where id = p_upload;
end;
$$;
revoke execute on function public.submit_dataset_review(uuid, jsonb) from public, anon;
grant execute on function public.submit_dataset_review(uuid, jsonb) to authenticated;

-- 5) Excel workbooks may be uploaded (same 5 MB limit, same org folder rule).
update storage.buckets
    set allowed_mime_types = array['text/csv', 'application/vnd.ms-excel',
                                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']
    where id = 'uploads';
