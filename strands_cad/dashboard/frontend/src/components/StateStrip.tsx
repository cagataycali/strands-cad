// The state strip: progress + job, layer, remaining, temps with targets, AMS slots as colour chips, controls with a confirm.
import { useState } from 'react'
import { dash, fmtMin, fmtTemp, stateWord, type Telemetry } from '../lib/api'
import type { Link } from '../usePrinter'
import { Ico } from './Icons'

export default function StateStrip({ s, link, toast, confirm }: { s: Telemetry | null; link: Link; toast: (t: string, k?: 'ok' | 'bad' | 'info') => void; confirm: (q: string, danger: boolean) => Promise<boolean> }) {
  const [busy, setBusy] = useState('')
  const word = stateWord(s, link)
  const printing = s?.gcode_state === 'RUNNING'
  const paused = s?.gcode_state === 'PAUSE'
  const pct = s?.progress ?? null
  const ctl = async (action: 'pause' | 'resume' | 'stop') => {
    const q = action === 'stop' ? 'Stop the print? The job is lost; the printer cools down.' : action === 'pause' ? 'Pause the print?' : 'Resume the print?'
    if (!(await confirm(q, action === 'stop'))) return
    setBusy(action)
    try { await dash.control(action); toast(`${action} sent`, 'ok') } catch (e: any) { toast(`${action}: ${e.message}`, 'bad') } finally { setBusy('') }
  }
  if (s?.error && !s.connected) return <div className="panel"><div className="panel-title"><span className="ic">{Ico.alert()}</span>printer</div><div className="hint">{s.error}</div></div>
  return (
    <div className="panel strip" data-testid="state-strip" data-state={s?.gcode_state || ''}>
      <div className="tg-hero">
        <div className="tg-hero-main"><span className={`tg-big ${word === 'PRINTING' ? '' : word === 'PAUSED' || word === 'FAILED' ? 'warn' : 'off'}`}>{pct == null ? '—' : pct}</span><span className="tg-big-unit">%</span><span className="chip state-chip">{word.toLowerCase()}</span></div>
        <div className="tg-hero-side">
          <div><b>{s?.layer ?? '—'}</b><span>/ {s?.total_layers ?? '—'} layers</span></div>
          <div><b>{fmtMin(s?.remaining_min)}</b><span>left</span></div>
        </div>
      </div>
      <div className="job-name" title={s?.subtask_name || ''}>{s?.subtask_name ? s.subtask_name.replace(/_/g, ' ') : s?.connected ? 'no job on the printer' : ''}</div>
      <div className="bar"><span className={paused ? 'warn' : ''} style={{ width: `${pct ?? 0}%` }} /></div>
      <div className="tg-grid temps">
        <Temp label="nozzle" v={s?.temps?.nozzle} target={s?.temps?.nozzle_target} />
        <Temp label="bed" v={s?.temps?.bed} target={s?.temps?.bed_target} />
        <Temp label="chamber" v={s?.temps?.chamber} />
      </div>
      <div className="ams-row" aria-label="AMS">
        {(s?.ams || []).flatMap((u) => u.slots.map((sl, i) => (
          <span key={`${u.id}-${i}`} className="ams-slot" title={`${sl.material || '?'} · ${sl.remaining_pct ?? '?'} % left`}>
            <i style={{ background: sl.color ? `#${String(sl.color).replace('#', '').slice(0, 6)}` : 'transparent' }} />
            <span className="mono">{sl.material || '—'}</span>{sl.remaining_pct != null && <small>{sl.remaining_pct}%</small>}
          </span>
        )))}
        {(!s?.ams || s.ams.length === 0) && <span className="txt-muted mono" style={{ fontSize: 11 }}>no AMS reported</span>}
        {s?.wifi_signal && <span className="chip" style={{ marginLeft: 'auto' }}>wifi {s.wifi_signal}</span>}
      </div>
      <div className="seat-actions badges">
        {paused ? <button className="gate-btn sm" disabled={!!busy} onClick={() => ctl('resume')} data-testid="resume"><span className="ic">{Ico.play()}</span>Resume</button>
          : <button className="gate-btn sm" disabled={!printing || !!busy} onClick={() => ctl('pause')} data-testid="pause"><span className="ic">{Ico.pause()}</span>Pause</button>}
        <button className="gate-btn sm danger" disabled={!(printing || paused) || !!busy} onClick={() => ctl('stop')} data-testid="stop-print"><span className="ic">{Ico.stop()}</span>Stop print</button>
        <span className="cfg-note" style={{ marginLeft: 'auto' }}>{s?.age_seconds != null ? `report ${Math.round(s.age_seconds)} s ago` : ''}</span>
      </div>
    </div>
  )
}

function Temp({ label, v, target }: { label: string; v?: number | null; target?: number | null }) {
  const heating = target != null && target > 0 && v != null && v < target - 2
  return <div className={`spark ${heating ? 'warn' : ''}`}><div className="spark-head"><span className="spark-label">{label}</span>{target != null && target > 0 && <span className="spark-unit">→ {fmtTemp(target)}</span>}</div><span className="spark-value">{fmtTemp(v)}</span></div>
}
