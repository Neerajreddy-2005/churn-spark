// CSV upload: month + drag-and-drop (or click) file zone. Used by the Upload page and the empty Overview.
import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import { useAuth } from './auth'
import { supabase } from './supabase'
import { IconFile, IconUpload } from './icons'

const MAX_BYTES = 5 * 1024 * 1024
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/
const DENIED = 'Upload limit reached (20 per day) or not allowed'

function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function check(file: File | null): string | null {
  if (!file) return 'Choose a CSV file.'
  if (!file.name.toLowerCase().endsWith('.csv')) return 'The file must be a .csv.'
  if (file.size === 0) return 'The file is empty.'
  if (file.size > MAX_BYTES) return 'The file is larger than 5 MB.'
  return null
}

export function UploadDrop({ onUploaded }: { onUploaded?: () => void }) {
  const { session, profile } = useAuth()
  const [month, setMonth] = useState(currentMonth())
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
    if (!MONTH_RE.test(month)) return setError('The month must look like YYYY-MM, for example 2026-11.')
    const bad = check(file)
    if (bad) return setError(bad)
    if (!session || !profile || !file) return setError('Your account has no organisation profile.')

    setBusy(true)
    const path = `${profile.org_id}/${crypto.randomUUID()}.csv`
    const up = await supabase.storage.from('uploads').upload(path, file, { contentType: 'text/csv' })
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
    <form className="stack" onSubmit={onSubmit} style={{ maxWidth: 'none' }}>
      <label>
        Month this data is for
        <input value={month} onChange={(e) => setMonth(e.target.value)} placeholder="2026-11" inputMode="numeric"
          pattern="\d{4}-(0[1-9]|1[0-2])" required aria-describedby="month-hint" style={{ maxWidth: 200 }} />
        <span className="hint" id="month-hint">Year and month, like 2026-11</span>
      </label>
      <label
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}>
        {file ? <IconFile /> : <IconUpload />}
        <strong>{file ? file.name : 'Drop your customer CSV here'}</strong>
        <span className="muted" id="file-hint">
          {file ? `${(file.size / 1024).toFixed(0)} KB · click to choose a different file` : 'or click to choose a file · .csv, up to 5 MB'}
        </span>
        <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" aria-describedby="file-hint"
          onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      </label>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="submit" disabled={busy || !file}>{busy ? 'Uploading…' : 'Upload and process'}</button>
        {info && <span className="ok" role="status">{info}</span>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  )
}
