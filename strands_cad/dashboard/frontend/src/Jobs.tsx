// Jobs drawer: design → slice → gate → print, every job with its states; a failure shows the slicer's own words.
import { useEffect, useState } from 'react'
import { dash, type Job, ago } from './lib/api'
import { Ico } from './components/Icons'

const STATE_CLS: Record<string, string> = { done: 'ok', running: 'live', queued: '', awaiting_approval: 'warn', error: 'bad', denied: 'no' }
const STEP_ORDER = ['design', 'validate', 'preview', 'slice', 'gate', 'print']

export default function Jobs({ toast }: { toast: (t: string, k?: 'ok' | 'bad' | 'info') => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const tick = () => dash.jobs().then((r) => { if (alive) setJobs(r.jobs) }).catch((e) => { if (alive && jobs === null) toast(`jobs: ${e.message}`, 'bad') })
    tick(); const id = setInterval(tick, 3000); return () => { alive = false; clearInterval(id) }
  }, [])
  const now = Date.now()
  return (
    <div className="jobs">
      {jobs === null && <div className="empty">loading…</div>}
      {jobs?.length === 0 && <div className="empty">no jobs since the dash started</div>}
      {jobs?.map((j) => {
        const isOpen = open === j.id
        const what = j.title || j.model || j.input || j.gcode || j.id
        return (
          <div key={j.id} className={`job-row ${STATE_CLS[j.state] || ''}`} data-state={j.state}>
            <button className="job-head" onClick={() => setOpen(isOpen ? null : j.id)} aria-expanded={isOpen}>
              <span className={`badge ${STATE_CLS[j.state] === 'ok' ? 'ok' : STATE_CLS[j.state] === 'bad' || STATE_CLS[j.state] === 'warn' ? 'warn' : ''}`}>{j.state.replace('_', ' ')}</span>
              <span className="mono kind">{j.kind}</span>
              <span className="rp-row-task what" title={String(what)}>{what}</span>
              <span className="mono txt-muted">{ago(j.created * 1000, now)}</span>
            </button>
            {j.steps && j.steps.length > 0 && (
              <div className="steps" aria-label="pipeline steps">
                {STEP_ORDER.filter((n) => j.steps!.some((s) => s.name === n)).map((n) => { const s = j.steps!.find((x) => x.name === n)!; return <span key={n} className={`step ${s.state}`} title={s.text || ''}>{n}</span> })}
              </div>
            )}
            {isOpen && (
              <div className="job-body">
                {j.preview && <a href={j.preview} target="_blank" rel="noreferrer"><img className="preview" src={j.preview} alt="preview" /></a>}
                {j.error && <div className="err"><span className="ic">{Ico.alert()}</span> {j.error}</div>}
                {j.result && <pre className="code">{JSON.stringify(j.result, null, 1).slice(0, 1200)}</pre>}
                <pre className="code log-lines">{(j.log || []).slice(-30).join('\n') || '(no log lines)'}</pre>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
