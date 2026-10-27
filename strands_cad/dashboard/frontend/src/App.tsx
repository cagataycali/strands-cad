// The printer cockpit — expo's structure: AuthGate first, a hud-top strip (brand · state pill · agent badge · Models · Jobs ·
// Settings · STOP), three zones — LEFT the chamber camera, CENTER the state strip, RIGHT the agent dock full height.
// Phone: one column (camera, strip, dock). Drawers: Models, Jobs, Settings. A confirm sheet sits in front of every control.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dash, stateWord } from './lib/api'
import { usePrinter } from './usePrinter'
import { useAgent } from './useAgent'
import AuthGate from './components/AuthGate'
import Camera from './components/Camera'
import StateStrip from './components/StateStrip'
import AgentDock from './components/AgentDock'
import Models from './Models'
import Jobs from './Jobs'
import Settings from './Settings'
import { Brand } from './components/Brand'
import { Ico } from './components/Icons'
import { apply as applyScheme } from './lib/scheme'

type Toast = { id: number; text: string; kind: 'ok' | 'bad' | 'info' }
let tseq = 1

export function App() {
  useEffect(() => { applyScheme((localStorage.getItem('printer-scheme') as any) || 'auto') }, [])
  return <AuthGate><Cockpit /></AuthGate>
}

export function Drawer({ title, icon, onClose, children, className = '' }: { title: string; icon?: React.ReactNode; onClose: () => void; children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('input, textarea, select, button:not([aria-label^="Close"])')?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => { window.removeEventListener('keydown', onKey, true); opener?.focus() }
  }, [onClose])
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <div className={`drawer ${className}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()} ref={ref}>
        <div className="drawer-head"><span className="drawer-title">{icon && <span className="ic">{icon}</span>}{title}</span><button className="icon-btn sm" onClick={onClose} aria-label={`Close ${title.toLowerCase()}`} title="Close (Esc)">{Ico.close()}</button></div>
        {children}
      </div>
    </div>
  )
}

type Ask = { q: string; danger: boolean; resolve: (ok: boolean) => void }

function Cockpit() {
  const { s, link } = usePrinter()
  const agent = useAgent()
  const [toasts, setToasts] = useState<Toast[]>([])
  const [input, setInput] = useState('')
  const [drawer, setDrawer] = useState<null | 'models' | 'jobs' | 'settings'>(null)
  const [ask, setAsk] = useState<Ask | null>(null)
  const [stopping, setStopping] = useState(false)
  const [phone, setPhone] = useState(() => matchMedia('(max-width: 899px)').matches)
  useEffect(() => { const mq = matchMedia('(max-width: 899px)'); const h = () => setPhone(mq.matches); mq.addEventListener('change', h); return () => mq.removeEventListener('change', h) }, [])

  const toast = useCallback((text: string, kind: Toast['kind'] = 'info') => {
    const id = tseq++
    setToasts((t) => [...t, { id, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'bad' ? 6000 : 3200)
  }, [])
  const confirm = useCallback((q: string, danger: boolean) => new Promise<boolean>((resolve) => setAsk({ q, danger, resolve })), [])
  const send = (t: string) => { agent.send(t).then((ok) => { if (ok) setInput('') }) }

  const stop = useCallback(async () => {
    const active = s?.gcode_state === 'RUNNING' || s?.gcode_state === 'PAUSE'
    if (!(await confirm(active ? `STOP the print "${s?.subtask_name || ''}"? The job is lost.` : 'Nothing is printing. Send STOP to the printer anyway?', true))) return
    setStopping(true); agent.note('pressed STOP')
    try { await dash.control('stop'); toast('stop sent', 'ok') } catch (e: any) { toast(`stop: ${e.message}`, 'bad') } finally { setStopping(false) }
  }, [s, confirm, agent, toast])

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement
      if (!typing && !e.metaKey && !e.ctrlKey && !drawer && !ask) {
        if (e.key === 'm') { setDrawer('models'); return }
        if (e.key === 'j') { setDrawer('jobs'); return }
      }
      if (e.key === 'Escape' && !drawer && !ask && !(e.target instanceof HTMLInputElement && e.target.value)) stop()
    }
    addEventListener('keydown', k); return () => removeEventListener('keydown', k)
  }, [stop, drawer, ask])

  useEffect(() => { if (link === 'reconnecting') toast('lost the dash link — reconnecting', 'bad') }, [link, toast])
  useEffect(() => { if (agent.conn === 'unauthorized') toast('the agent needs a key — sign in again', 'bad') }, [agent.conn, toast])
  useEffect(() => { if (agent.pending.length) toast(`operator gate: a print is waiting for your approval`, 'bad') }, [agent.pending.length, toast])

  const word = stateWord(s, link)
  const conn = link === 'live' && s?.connected ? 'open' : link === 'connecting' ? 'connecting' : 'closed'
  const closeDrawer = useCallback(() => setDrawer(null), [])
  const gateCount = useMemo(() => agent.pending.length, [agent.pending])

  return (
    <div className="stage" data-testid="cockpit" data-agent={agent.conn} data-state={s?.gcode_state || ''}>
      <header className="hud-top">
        <h1 className="sr-only">printer cockpit</h1>
        <Brand />
        <div className="hud-top-right">
          <div className={`conn-pill ${conn} ${word === 'PRINTER DOWN' || word === 'FAILED' ? 'closed' : ''}`} role="status" aria-live="polite" title={s?.serial || ''}><span className={`dot ${conn}`} />{word}</div>
          {s?.serial && <span className="badge" title="printer serial">{s.serial}</span>}
          {gateCount > 0 && <span className="badge warn">{gateCount} gate{gateCount > 1 ? 's' : ''}</span>}
          <button className={drawer === 'models' ? 'icon-tab on' : 'icon-tab'} onClick={() => setDrawer('models')} aria-pressed={drawer === 'models'} aria-label="Models" title="Models (m)" data-testid="tab-models">{Ico.cube()}</button>
          <button className={drawer === 'jobs' ? 'icon-tab on' : 'icon-tab'} onClick={() => setDrawer('jobs')} aria-pressed={drawer === 'jobs'} aria-label="Jobs" title="Jobs (j)" data-testid="tab-jobs">{Ico.list()}</button>
          <button className="icon-tab" onClick={() => setDrawer('settings')} aria-label="Settings" title="Settings" data-testid="tab-config">{Ico.settings()}</button>
          <button className="stop-btn" onClick={stop} aria-label="Stop" data-busy={stopping ? 1 : 0} data-testid="stop"><span className="ic">{Ico.stop()}</span>STOP</button>
        </div>
      </header>

      <main className="cockpit">
        <section className="zone left" aria-label="Camera and printer state">
          <div className="card cam"><Camera s={s} onMsg={(m) => toast(m, 'bad')} /></div>
          <div className="card strip-card"><StateStrip s={s} link={link} toast={toast} confirm={confirm} /></div>
        </section>
        <section className="zone right" aria-label="Agent">
          <AgentDock agent={agent} input={input} setInput={setInput} onSend={send} phone={phone} toast={toast} />
        </section>
      </main>

      <div className="toasts" aria-live="polite">{toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}</div>

      {drawer === 'models' && <Drawer title="Models" icon={Ico.cube()} onClose={closeDrawer} className="modelsdrawer"><Models toast={toast} onQueued={() => { setDrawer('jobs') }} /></Drawer>}
      {drawer === 'jobs' && <Drawer title="Jobs" icon={Ico.list()} onClose={closeDrawer} className="jobsdrawer"><Jobs toast={toast} /></Drawer>}
      {drawer === 'settings' && <Drawer title="Settings" icon={Ico.settings()} onClose={closeDrawer}><Settings toast={toast} onSignedOut={() => location.reload()} /></Drawer>}

      {ask && (
        <div className="drawer-scrim confirm-scrim" onClick={() => { ask.resolve(false); setAsk(null) }}>
          <div className="confirm" role="alertdialog" aria-modal="true" aria-label="confirm" onClick={(e) => e.stopPropagation()}>
            <div className="confirm-q">{ask.q}</div>
            <div className="gate-actions">
              <button className={`gate-btn primary ${ask.danger ? 'danger' : ''}`} autoFocus onClick={() => { ask.resolve(true); setAsk(null) }} data-testid="confirm-yes">{ask.danger ? 'Yes, stop' : 'Yes'}</button>
              <button className="gate-btn ghost" onClick={() => { ask.resolve(false); setAsk(null) }} data-testid="confirm-no">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
