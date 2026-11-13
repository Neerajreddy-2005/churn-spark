// One list of everything the Overview and At-risk pages can show: certified Telco months and trained
// datasets of any other layout. The choice lives in the URL (?data=telco:2026-11 or ?data=ds:<upload id>).
import { useSearchParams } from 'react-router-dom'
import { supabase } from './supabase'
import { must, useLoad, type Loaded } from './useLoad'
import { fmtMonth } from './format'

export type Source =
  | { kind: 'telco'; value: string; label: string; month: string }
  | { kind: 'dataset'; value: string; label: string; id: string; month: string }

export function useSources(): Loaded<Source[]> {
  return useLoad(async () => {
    const [months, ds] = await Promise.all([
      supabase.from('segment_aggregates').select('snapshot_month').eq('segment_type', 'overall')
        .order('snapshot_month', { ascending: false }),
      supabase.from('datasets').select('upload_id,name,auc:result->metrics->auc_roc_test,uploads(status,snapshot_month)')
        .eq('kind', 'generic').order('created_at', { ascending: false }).limit(50),
    ])
    const telco = [...new Set((must(months) as { snapshot_month: string }[]).map((r) => r.snapshot_month))]
      .map((m): Source => ({ kind: 'telco', value: `telco:${m}`, label: `Telco · ${fmtMonth(m)}`, month: m }))
    const trained = (must(ds) as unknown as { upload_id: string; name: string; auc: number | null;
      uploads: { status: string; snapshot_month: string } | null }[])
      .filter((d) => d.auc != null && d.uploads?.status === 'done')
      .map((d): Source => ({ kind: 'dataset', value: `ds:${d.upload_id}`, label: d.name, id: d.upload_id,
        month: d.uploads!.snapshot_month }))
    return [...telco, ...trained]
  }, [])
}

/** The selected source (from ?data=, else the newest Telco month, else the newest dataset) and a setter. */
export function useSelectedSource(sources: Source[] | null) {
  const [params, setParams] = useSearchParams()
  const wanted = params.get('data')
  const selected = sources?.find((s) => s.value === wanted) ?? sources?.[0] ?? null
  const select = (value: string) => setParams((p) => {
    const n = new URLSearchParams(p)
    n.set('data', value)
    return n
  }, { replace: true })
  return [selected, select] as const
}

/** Dropdown options, grouped by putting Telco months first. */
export const sourceOptions = (sources: Source[]) => sources.map((s) => ({ value: s.value, label: s.label }))
