-- Phase 7a: tables. Security (RLS, grants, policies) is in 0002; storage in 0003.
-- "private" holds helper functions and is not exposed through the Data API.
create schema if not exists private;

create table public.organisations (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    created_at timestamptz not null default now()
);

create table public.profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    org_id uuid not null references public.organisations (id),
    role text not null default 'analyst' check (role in ('analyst', 'manager')),
    display_name text,
    created_at timestamptz not null default now()
);

create table public.uploads (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organisations (id),
    uploaded_by uuid not null references auth.users (id),
    file_path text not null unique,
    original_name text not null,
    size_bytes int not null check (size_bytes > 0 and size_bytes <= 5242880),
    snapshot_month text not null check (snapshot_month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
    status text not null default 'queued'
        check (status in ('queued', 'processing', 'done', 'rejected', 'failed')),
    status_reason text,
    run_id text,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    check (file_path like org_id::text || '/%')
);

create table public.pipeline_runs (
    run_id text primary key,
    org_id uuid not null,
    upload_id uuid references public.uploads (id),
    snapshot_month text,
    status text,
    started_at timestamptz,
    finished_at timestamptz,
    stage_seconds jsonb,
    rows jsonb,
    dq_summary jsonb,
    recon_summary jsonb,
    model_version text
);

create table public.dq_results (
    run_id text,
    org_id uuid not null,
    rule_id text,
    description text,
    columns text,
    checked_rows bigint,
    failing_rows bigint,
    failing_rate double precision,
    threshold double precision,
    status text,
    action text,
    run_ts timestamptz,
    primary key (run_id, rule_id)
);

create table public.reconciliation_results (
    run_id text,
    org_id uuid not null,
    check_id text,
    description text,
    expected text,
    actual text,
    difference double precision,
    status text,
    run_ts timestamptz,
    primary key (run_id, check_id)
);

create table public.customer_scores (
    org_id uuid not null,
    snapshot_month text not null,
    customer_id text not null,
    risk_score numeric(5, 4),
    risk_band text check (risk_band in ('high', 'medium', 'low')),
    reason_1 text,
    reason_2 text,
    reason_3 text,
    contract text,
    internet_service text,
    payment_method text,
    tenure_months int,
    tenure_band text,
    monthly_charges numeric(8, 2),
    churned boolean,
    model_version text,
    scored_at timestamptz,
    primary key (org_id, snapshot_month, customer_id)
);

create table public.segment_aggregates (
    org_id uuid not null,
    snapshot_month text not null,
    segment_type text not null,
    segment text not null,
    customers bigint,
    churned bigint,
    churn_rate_pct numeric(5, 2),
    mrr numeric(12, 2),
    mrr_lost numeric(12, 2),
    primary key (org_id, snapshot_month, segment_type, segment)
);

create table public.retention_actions (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organisations (id),
    customer_id text not null,
    snapshot_month text not null,
    action_type text not null
        check (action_type in ('call', 'email', 'discount_offer', 'contract_upgrade_offer',
                               'tech_support_session', 'other')),
    note text check (char_length(note) <= 500),
    outcome text not null default 'pending'
        check (outcome in ('pending', 'retained', 'churned', 'no_response')),
    created_by uuid not null references auth.users (id),
    created_at timestamptz not null default now(),
    updated_at timestamptz
);

-- Every org_id is indexed (customer_scores and segment_aggregates are covered by their
-- primary keys, which lead with org_id, snapshot_month).
create index profiles_org_idx on public.profiles (org_id);
create index uploads_org_month_idx on public.uploads (org_id, snapshot_month);
create index uploads_status_created_idx on public.uploads (status, created_at);
create index pipeline_runs_org_month_idx on public.pipeline_runs (org_id, snapshot_month);
create index dq_results_org_idx on public.dq_results (org_id);
create index reconciliation_results_org_idx on public.reconciliation_results (org_id);
create index customer_scores_band_idx
    on public.customer_scores (org_id, snapshot_month, risk_band, risk_score desc);
create index retention_actions_org_month_idx on public.retention_actions (org_id, snapshot_month);
