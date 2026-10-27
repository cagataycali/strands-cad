// The narrative: prompts, the agent's streamed answers, tool rows (words, not JSON), preview images, GATE CARDS (approve/deny).
import { useEffect, useRef, useState } from 'react'
import type { GateCard, Receipt, Turn } from './useAgent'
import { toolWords, humanise } from './words'
import { Md } from './md'
import { Ico } from './components/Icons'

export const SUGGEST = ['What is the printer doing?', 'Design a 20 mm calibration cube and print it.', 'Design a phone stand for an iPhone, 15° tilt, and show me a preview.', 'List the models in the workdir.']

export function Log({ turns, busy, onSuggest, canSend, onGate }: { turns: Turn[]; busy: boolean; onSuggest: (s: string) => void; canSend: boolean; onGate: (id: string, ok: boolean) => void }) {
  const el = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)
  useEffect(() => { if (pinned) el.current?.scrollTo({ top: el.current.scrollHeight }) }, [turns, pinned])
  const onScroll = () => { const e = el.current!; setPinned(e.scrollHeight - e.scrollTop - e.clientHeight < 80) }
  return (
    <div className="log" ref={el} onScroll={onScroll} aria-live="polite">
      {turns.length === 0 && (
        <div className="hello">
          <p>This is the <b>printer agent</b>: it designs (OpenSCAD, CadQuery, SDF), checks the mesh against the X2D bed, slices with Orca and prints on the Bambu. <b>No print starts without your approval</b> — a gate card appears here first.</p>
          <div className="suggest">{SUGGEST.map((s) => <button key={s} onClick={() => onSuggest(s)} disabled={!canSend}>{s}</button>)}</div>
        </div>
      )}
      {turns.map((t) => <TurnView key={t.id} t={t} onGate={onGate} />)}
      {busy && turns[turns.length - 1]?.role === 'you' && <div className="turn fomo"><span className="who">agent</span><div className="body"><div className="thinking"><i /><i /><i /></div></div></div>}
      {!pinned && <button className="totail" onClick={() => { setPinned(true); el.current?.scrollTo({ top: el.current.scrollHeight }) }}>↓ latest</button>}
    </div>
  )
}

function TurnView({ t, onGate }: { t: Turn; onGate: (id: string, ok: boolean) => void }) {
  if (t.role === 'note') return <div className="turn note"><span className="who">you</span><div className="body">{t.text}</div></div>
  return (
    <div className={`turn ${t.role === 'agent' ? 'fomo' : t.role}`}>
      <span className="who">{t.role}</span>
      <div className="body">
        {t.receipts.map((r) => <ToolRow key={r.id} r={r} />)}
        {t.gates.map((g) => <Gate key={g.id} g={g} onGate={onGate} />)}
        {t.text && <div className="text"><Md text={t.text} />{!t.done && <span className="caret" />}</div>}
        {t.error && <div className="err">{t.error}</div>}
        {t.role === 'agent' && !t.done && !t.text && t.receipts.length === 0 && t.gates.length === 0 && <div className="thinking"><i /><i /><i /></div>}
        {t.role === 'agent' && t.done && t.firstTokenMs != null && <span className="lat" title="time to first token">{(t.firstTokenMs / 1000).toFixed(1)} s</span>}
      </div>
    </div>
  )
}

function ToolRow({ r }: { r: Receipt }) {
  const w = toolWords(r.name, r.input)
  const pending = !r.status
  const secs = r.t1 != null ? `${((r.t1 - r.t0) / 1000).toFixed(1)} s` : ''
  const short = r.text && (r.status === 'error' || r.text.length < 240) ? humanise(r.text) : ''
  return (
    <div className={`tool ${r.status || 'pending'}`} data-verb={w.verb}>
      <div className="tool-head"><span className="spin" aria-hidden /><span className="verb">{w.verb}</span>{w.detail && <span className="detail">· {w.detail}</span>}<span className="st">{pending ? 'working…' : secs}</span></div>
      {short && <div className="rt">{short}</div>}
      {r.image && <a href={r.image} target="_blank" rel="noreferrer" className="preview-link"><img className="preview" src={r.image} alt="preview" /></a>}
    </div>
  )
}

/** The operator gate as a card: what is about to be printed (with its preview), Approve / Deny. Default is deny (timeout). */
export function Gate({ g, onGate }: { g: GateCard; onGate: (id: string, ok: boolean) => void }) {
  const open = !g.answered
  const [left, setLeft] = useState<number | null>(null)
  useEffect(() => {
    if (!open || !g.timeout_s) return
    const end = g.t + g.timeout_s * 1000
    const id = setInterval(() => setLeft(Math.max(0, Math.round((end - Date.now()) / 1000))), 500)
    return () => clearInterval(id)
  }, [open, g.t, g.timeout_s])
  const verdict = g.timed_out ? 'timed out → denied' : g.approve ? `approved${g.by ? ` by ${g.by}` : ''}` : `denied${g.by ? ` by ${g.by}` : ''}`
  return (
    <div className={`gate-card-row ${open ? 'open' : g.approve ? 'ok' : 'no'}`} data-testid="gate-card" role={open ? 'alertdialog' : undefined} aria-label={`operator gate: ${g.kind}`}>
      <div className="gate-row-head"><span className="ic">{Ico.lock()}</span><b>operator gate</b><span className="mono">{g.kind === 'print' ? 'start a print' : g.kind}</span>{!open && <span className={`verdict ${g.approve ? 'ok' : 'no'}`}>{verdict}</span>}</div>
      {g.preview && <a href={g.preview} target="_blank" rel="noreferrer" className="preview-link"><img className="preview gate-preview" src={g.preview} alt="what will be printed" /></a>}
      <pre className="gate-detail">{g.detail}</pre>
      {open && (
        <div className="gate-actions">
          <button className="gate-btn primary" onClick={() => onGate(g.id, true)} data-testid="gate-approve"><span className="ic">{Ico.play()}</span>Approve · print</button>
          <button className="gate-btn ghost" onClick={() => onGate(g.id, false)} data-testid="gate-deny"><span className="ic">{Ico.close()}</span>Deny</button>
          {g.timeout_s && <small>{left != null ? `denies itself in ${left}s` : `denies itself after ${g.timeout_s}s`}</small>}
        </div>
      )}
    </div>
  )
}
