// CSV upload: month + drag-and-drop (or click) file zone. Used by the Upload page and the empty Overview.
import { useId, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { useAuth } from './auth'
import { supabase } from './supabase'
import { IconFile, IconUpload } from './icons'

const MAX_BYTES = 5 * 1024 * 1024
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/
const DENIED = 'Upload limit reached (20 per day) or not allowed'
const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

// Mirrors backend/pipeline/config/dq_rules.json (allowed values and ranges).
const ACCEPTED: [string, string][] = [
  ['customerID', 'Any text, unique per row'],
  ['gender', 'Male · Female'],
  ['SeniorCitizen', '0 · 1'],
  ['Partner · Dependents · PhoneService · PaperlessBilling', 'Yes · No'],
  ['tenure', 'Whole months, 0 to 600'],
  ['MultipleLines', 'Yes · No · No phone service'],
  ['InternetService', 'DSL · Fiber optic · No'],
  ['OnlineSecurity · OnlineBackup · DeviceProtection · TechSupport · StreamingTV · StreamingMovies', 'Yes · No · No internet service'],
  ['Contract', 'Month-to-month · One year · Two year'],
  ['PaymentMethod', 'Electronic check · Mailed check · Bank transfer (automatic) · Credit card (automatic)'],
  ['MonthlyCharges', 'Number from 0 to 10,000, no currency sign'],
  ['TotalCharges', 'Number; may be blank only when tenure is 0'],
  ['Churn (optional)', 'Yes · No. Leave the column out to score customers whose outcome is not known yet'],
]

function check(file: File | null): string | null {
  if (!file) return 'Choose a CSV file.'
  if (!/\.(csv|xlsx)$/i.test(file.name)) return 'The file must be a .csv or .xlsx.'
  if (file.size === 0) return 'The file is empty.'
  if (file.size > MAX_BYTES) return 'The file is larger than 5 MB.'
  return null
}

// Floating panel: opens on hover or keyboard focus, toggles on tap (no hover on phones), Esc closes.
function AcceptedFiles() {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <div className="accepted" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false) }}
      onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}>
      <button type="button" className="linkish" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        Which files are accepted?
      </button>
      <div id={id} className="accepted-pop" hidden={!open}>
        <p className="muted">
          <strong>Any other customer churn file</strong> (CSV or Excel, one row per customer) is accepted too. When a column
          clearly says whether the customer left (a name like Churn, Exited or Attrition), a model is trained on that file
          straight away; otherwise you confirm the columns under Datasets. Its results appear in the Data picker on Overview and At-risk.
        </p>
        <p className="muted">
          <strong>Certified Telco layout</strong>, shown on the Overview and At-risk pages: the IBM Telco customer layout (Kaggle “Telco Customer Churn”). One row per customer · comma-separated .csv up to 5 MB ·
          column names exactly as below (case-sensitive), in any order, with no extra columns.
        </p>
        <dl className="accepted-grid">
          {ACCEPTED.map(([cols, values]) => (
            <div key={cols} className={cols.length + values.length > 70 ? 'wide' : undefined}>
              <dt>{cols}</dt>
              <dd>{values}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}

export function UploadDrop({ onUploaded }: { onUploaded?: () => void }) {
  const { session, profile } = useAuth()
  const [month, setMonth] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const pick = (f: File | null) => {
    setInfo(null)
    setFile(f)
    setError(f ? check(f) : null)
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    pick(e.dataTransfer.files?.[0] ?? null)
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setInfo(null)
    if (!MONTH_RE.test(month)) return setError('Enter the month as YYYY-MM, for example 2026-01.')
    const bad = check(file)
    if (bad) return setError(bad)
    if (!session || !profile || !file) return setError('Your account has no organisation profile.')

    setBusy(true)
    const xlsx = /\.xlsx$/i.test(file.name)
    const path = `${profile.org_id}/${crypto.randomUUID()}${xlsx ? '.xlsx' : '.csv'}`
    const up = await supabase.storage.from('uploads').upload(path, file, { contentType: xlsx ? XLSX_TYPE : 'text/csv' })
    if (up.error) {
      setBusy(false)
      return setError(`${DENIED} (${up.error.message})`)
    }
    const ins = await supabase.from('uploads').insert({
      org_id: profile.org_id,
      uploaded_by: session.user.id,
      file_path: path,
      original_name: file.name,
      size_bytes: file.size,
      snapshot_month: month,
    })
    setBusy(false)
    if (ins.error) return setError(`${DENIED} (${ins.error.message})`)
    setInfo(`Uploaded ${file.name}. It's queued for the pipeline, which takes about 2–3 minutes.`)
    setFile(null)
    if (input.current) input.current.value = ''
    onUploaded?.()
  }

  return (
    <form className="stack upload-form" onSubmit={onSubmit} style={{ maxWidth: 'none' }}>
      <label>
        Month this data is for
        <input value={month} onChange={(e) => setMonth(e.target.value)} placeholder="2026-01" inputMode="numeric"
          pattern="\d{4}-(0[1-9]|1[0-2])" required aria-describedby="month-hint" style={{ maxWidth: 200 }} />
        <span className="hint" id="month-hint">
          For Telco files this is the month the customers belong to: uploading the same
          month again replaces it. For other datasets it is only a label.
        </span>
      </label>
      <label
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}>
        {file ? <IconFile /> : <IconUpload />}
        <strong>{file ? file.name : 'Drop your customer file here (CSV or Excel)'}</strong>
        <span className="muted" id="file-hint">
          {file ? `${(file.size / 1024).toFixed(0)} KB · click to choose a different file` : 'or click to choose a file · .csv or .xlsx, up to 5 MB'}
        </span>
        <input ref={input} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="sr-only" aria-describedby="file-hint"
          onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      </label>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="submit" disabled={busy || !file}>{busy ? 'Uploading…' : 'Upload and process'}</button>
        {info && <span className="ok" role="status">{info}</span>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <AcceptedFiles />
    </form>
  )
}
