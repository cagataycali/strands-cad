// The agent dock: the printer agent's thread full height on the right, the composer under it, readiness on top.
import { useEffect, useRef } from 'react'
import type { useAgent } from '../useAgent'
import { Log, SUGGEST } from '../Log'
import { Ico } from './Icons'

type Agent = ReturnType<typeof useAgent>

export default function AgentDock({ agent, input, setInput, onSend, phone = false, toast }: {
  agent: Agent; input: string; setInput: (v: string) => void; onSend: (t: string) => void; phone?: boolean; toast: (t: string, k?: 'ok' | 'bad' | 'info') => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (!phone && agent.conn === 'ready') inputRef.current?.focus() }, [phone, agent.conn])
  const canSend = agent.conn === 'ready' && !agent.busy
  const status = agent.conn === 'ready' ? `ready · ${agent.tools} tools` : agent.conn === 'building' ? 'building the agent…' : agent.conn === 'unauthorized' ? 'needs a key' : agent.conn === 'error' ? 'agent error' : 'checking'
  return (
    <section className="agent-dock" aria-label="Agent" data-testid="agent-dock" data-conn={agent.conn}>
      <div className="panel-title dock-head">
        <span className="ic">{Ico.brain()}</span><span>printer agent</span>
        <span className={`composer-status ${agent.conn === 'ready' ? 'ok' : agent.conn === 'building' || agent.conn === 'checking' ? '' : 'err'}`} role="img" aria-label={status} title={status} />
        <span className="dock-status">{status}</span>
        {agent.pending.length > 0 && <span className="badge warn">{agent.pending.length} waiting</span>}
        <button className="mini-btn" style={{ marginLeft: 'auto' }} onClick={() => { agent.reset(); toast('thread cleared', 'ok') }} title="forget this conversation">clear</button>
      </div>
      {agent.lastError && <div className="err dock-err">{agent.lastError}</div>}
      <Log turns={agent.turns} busy={agent.busy} onSuggest={onSend} canSend={canSend} onGate={agent.answer} />
      <div className="composer">
        {agent.turns.length > 0 && canSend && <div className="composer-chips">{SUGGEST.slice(0, 2).map((c) => <button key={c} className="cchip" onClick={() => onSend(c)}>{c.replace(/[.,].*$/, '')}</button>)}</div>}
        <form className="composer-bar" onSubmit={(e) => { e.preventDefault(); onSend(input) }}>
          <input ref={inputRef} className="composer-input" value={input} aria-label="Message to the printer agent" enterKeyHint="send"
            placeholder={agent.busy ? 'the agent is working…' : agent.conn === 'ready' ? '"design a 20 mm calibration cube and print it"' : 'the agent is not ready yet'}
            onChange={(e) => setInput(e.target.value)} disabled={agent.conn !== 'ready'} />
          <button type="submit" className="composer-send" disabled={!canSend || !input.trim()} aria-label="Send" title="Send (Enter)">{Ico.send()}</button>
        </form>
      </div>
    </section>
  )
}
