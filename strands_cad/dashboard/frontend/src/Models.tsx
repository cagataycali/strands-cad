// Models drawer: the workdir's STL/3MF/G-code files, a 3D preview of the picked one, "Print this" → the gated pipeline.
import { Suspense, lazy, useEffect, useState } from 'react'
import { dash, type ModelMeta, type ModelRow, ago } from './lib/api'
import { Ico } from './components/Icons'
const StlView = lazy(() => import('./StlView'))

const kb = (n: number) => n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`

export default function Models({ toast, onQueued, initial }: { toast: (t: string, k?: 'ok' | 'bad' | 'info') => void; onQueued: (jobId: string, name: string) => void; initial?: string | null }) {
  const [rows, setRows] = useState<ModelRow[] | null>(null)
  const [workdir, setWorkdir] = useState('')
  const [pick, setPick] = useState<string | null>(initial || null)
  const [meta, setMeta] = useState<ModelMeta | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => dash.models().then((r) => { setRows(r.models.sort((a, b) => b.mtime - a.mtime)); setWorkdir(r.workdir); if (!pick && r.models.length) setPick(r.models.find((m) => m.ext === 'stl')?.name || r.models[0].name) }).catch((e) => toast(`models: ${e.message}`, 'bad'))
  useEffect(() => { load() }, [])
  useEffect(() => { if (pick && /\.stl$/i.test(pick)) dash.meta(pick).then(setMeta).catch(() => setMeta(null)); else setMeta(null) }, [pick])
  const print = async () => {
    if (!pick) return
    setBusy(true)
    try { const r = await dash.pipeline({ model: pick }); toast(`queued ${pick} → slice → gate`, 'ok'); onQueued(r.job_id, pick) } catch (e: any) { toast(`print: ${e.message}`, 'bad') } finally { setBusy(false) }
  }
  const stl = pick && /\.stl$/i.test(pick)
  return (
    <div className="models">
      <div className="card model-preview">
        {stl ? <Suspense fallback={<div className="twin-wait">loading the viewer…</div>}><StlView url={dash.modelUrl(pick!)} /></Suspense> : <div className="empty">{pick ? `${pick.split('.').pop()} — no 3D preview, printable if sliced` : rows && rows.length === 0 ? 'no models in the workdir yet — ask the agent to design one' : 'pick a model'}</div>}
        {pick && (
          <div className="model-meta">
            <div className="mono name" title={pick}>{pick}</div>
            {meta?.bbox && <div className="chips"><span className="chip">{meta.bbox.size.map((v) => v.toFixed(1)).join(' × ')} mm</span>{meta.volume_cm3 != null && <span className="chip">{meta.volume_cm3} cm³</span>}{meta.weight_pla_g != null && <span className="chip">≈ {meta.weight_pla_g} g PLA</span>}{meta.triangles != null && <span className="chip">{meta.triangles.toLocaleString()} tris</span>}</div>}
            <div className="cfg-row"><button className="gate-btn primary sm" onClick={print} disabled={busy} data-testid="model-print"><span className="ic">{Ico.printer()}</span>{busy ? 'queueing…' : 'Print this (asks first)'}</button><a className="gate-btn ghost sm" href={dash.modelUrl(pick)} download>download</a></div>
          </div>
        )}
      </div>
      <div className="panel-title" style={{ marginTop: 14 }}><span className="ic">{Ico.cube()}</span>workdir <span className="cfg-note-inline">{workdir}</span><button className="icon-btn sm" style={{ marginLeft: 'auto' }} onClick={load} aria-label="refresh">{Ico.refresh()}</button></div>
      <div className="rp-list">
        {rows === null && <div className="empty">loading…</div>}
        {rows?.map((m) => <button key={m.name} className={`rp-row ${pick === m.name ? 'on' : ''}`} onClick={() => setPick(m.name)}><span className="mono">{m.ext}</span><span className="rp-row-task">{m.name}</span><span className="mono txt-muted">{kb(m.size)} · {ago(m.mtime * 1000)}</span></button>)}
      </div>
    </div>
  )
}
