export type UploadStatus = 'queued' | 'processing' | 'needs_review' | 'done' | 'rejected' | 'failed'
export type RiskBand = 'high' | 'medium' | 'low'
export type Outcome = 'pending' | 'retained' | 'churned' | 'no_response'

export const ACTION_TYPES = [
  'call',
  'email',
  'discount_offer',
  'contract_upgrade_offer',
  'tech_support_session',
  'other',
] as const
export type ActionType = (typeof ACTION_TYPES)[number]

export const OUTCOMES: Outcome[] = ['pending', 'retained', 'churned', 'no_response']
export const RISK_BANDS: RiskBand[] = ['high', 'medium', 'low']

export interface Organisation {
  id: string
  name: string
}

export interface Profile {
  user_id: string
  org_id: string
  role: 'analyst' | 'manager'
  display_name: string | null
  email: string | null
  is_active: boolean
  must_change_password: boolean
}

export interface AuditEntry {
  id: number
  action: string
  actor_id: string | null
  target_user_id: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export const ROLES = ['analyst', 'manager'] as const

export interface UploadRow {
  id: string
  org_id: string
  uploaded_by: string
  file_path: string
  original_name: string
  size_bytes: number
  snapshot_month: string
  status: UploadStatus
  status_reason: string | null
  run_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface PipelineRun {
  run_id: string
  org_id: string
  upload_id: string | null
  snapshot_month: string | null
  status: string | null
  started_at: string | null
  finished_at: string | null
  stage_seconds: Record<string, number> | null
  rows: Record<string, unknown> | null
  dq_summary: Record<string, number> | null
  recon_summary: Record<string, number> | null
  model_version: string | null
}

export interface DqResult {
  run_id: string
  rule_id: string
  description: string | null
  columns: string | null
  checked_rows: number | null
  failing_rows: number | null
  failing_rate: number | null
  threshold: number | null
  status: string | null
  action: string | null
}

export interface ReconResult {
  run_id: string
  check_id: string
  description: string | null
  expected: string | null
  actual: string | null
  difference: number | null
  status: string | null
}

export interface CustomerScore {
  org_id: string
  snapshot_month: string
  customer_id: string
  risk_score: number | null
  risk_band: RiskBand | null
  reason_1: string | null
  reason_2: string | null
  reason_3: string | null
  contract: string | null
  internet_service: string | null
  payment_method: string | null
  tenure_months: number | null
  tenure_band: string | null
  monthly_charges: number | null
  churned: boolean | null
  model_version: string | null
  scored_at: string | null
}

export interface SegmentAggregate {
  snapshot_month: string
  segment_type: string
  segment: string
  customers: number | null
  churned: number | null
  churn_rate_pct: number | null
  mrr: number | null
  mrr_lost: number | null
}

export interface RetentionAction {
  id: string
  org_id: string
  customer_id: string
  snapshot_month: string
  dataset_id: string | null
  action_type: ActionType
  note: string | null
  outcome: Outcome
  created_by: string
  created_at: string
  updated_at: string | null
}

// ---- Mode 2: bring-your-own churn dataset (see docs/design/MODE2.md) ----
export interface ProfileColumn {
  key: string
  name: string
  kind: 'number' | 'text' | 'date' | 'empty'
  filled: number
  blank: number
  missing_pct: number
  distinct: number
  top: [string, number][]
  values_hidden?: boolean
  integer?: boolean
  min?: number | null
  median?: number | null
  max?: number | null
  mean?: number | null
  histogram?: { from: number; to: number; count: number }[] | null
}

export interface Suggestions {
  target: string | null
  positive: string | null
  target_note: string
  id: string | null
  id_note: string
  exclude: Record<string, { kind: string; reason: string }>
  features: string[]
  is_churn_data: boolean
}

export interface Segment { segment: string; customers: number; churned: number; churn_rate_pct: number }
export interface Capture { top_pct: number; customers: number; churners_captured: number; capture_rate_pct: number; lift: number; max_possible_pct: number }

export interface DatasetResult {
  explore_only?: boolean
  rows?: { total: number; train: number; test: number }
  base_churn_rate_pct?: number
  metrics?: { auc_roc_test: number; auc_roc_train: number; auc_pr_test: number; capture: Record<string, Capture> }
  suspicious?: boolean
  leakage?: {
    removed: { name: string; single_column_auc: number }[]
    warned: { name: string; single_column_auc: number }[]
  }
  drivers?: { feature: string; value: string | null; coefficient: number; direction: string }[]
  insights?: { key: string; name: string; kind: string; spread_pct_points: number; segments: Segment[] }[]
  reconciliation?: { check_id: string; description: string; status: string }[]
  reconciled?: boolean
}

export interface DatasetCheck { rule_id: string; description: string; status: string; detail: string }

export interface Dataset {
  upload_id: string
  name: string
  kind: 'telco' | 'generic'
  source_info: { sheet?: string; delimiter?: string; rows?: number; columns?: number } | null
  profile: { rows: number; columns: ProfileColumn[] } | null
  suggestions: Suggestions | null
  review: { explore_only?: boolean; target?: string; positive?: string; id?: string | null; features?: string[]; auto?: boolean } | null
  checks: DatasetCheck[] | null
  result: DatasetResult | null
  created_at: string
  updated_at: string
}

export interface DatasetScore {
  row_id: string
  risk_score: number
  risk_band: RiskBand
  reason_1: string | null
  reason_2: string | null
  reason_3: string | null
  churned: boolean | null
  in_training_set: boolean
}
