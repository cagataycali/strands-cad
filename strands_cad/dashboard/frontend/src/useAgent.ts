// The agent link. Preferred: POST /api/chat/stream (SSE frames in expo's vocabulary — text{delta} · tool{id,name,input} ·
// tool_result{id,status,text,image?} · gate{id,kind,detail,timeout_s | approve,answered,timed_out} · done · error).
// Fallback (older server): POST /api/chat → one {reply|result, error}. Independently, /api/gates is polled every 3 s so a
// gate opened by ANOTHER caller (tiny's device path, Telegram) shows up in the dock as a card and can be answered here.
import { useCallback, useEffect, useRef, useState } from 'react'
import { dash, type Gate } from './lib/api'

export type Receipt = { id: string; name: string; input: unknown; status?: string; text?: string; image?: string | null; t0: number; t1?: number }
export type GateCard = { id: string; kind: string; detail: string; t: number; approve?: boolean; answered?: boolean; timed_out?: boolean; timeout_s?: number; preview?: string | null; by?: string }
export type Turn = { id: number; role: 'you' | 'agent' | 'note'; text: string; receipts: Receipt[]; gates: GateCard[]; error?: string; done?: boolean; t: number; firstTokenMs?: number }
export type AgentLink = 'checking' | 'building' | 'ready' | 'error' | 'unauthorized'

let seq = 1

export function useAgent() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [conn, setConn] = useState<AgentLink>('checking')
  const [tools, setTools] = useState(0)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<GateCard[]>([])
  const [lastError, setLastError] = useState('')
  const sentAt = useRef(0)
  const seen = useRef<Set<string>>(new Set())

  // agent readiness (/api/chat/status builds the agent lazily on the server)
  useEffect(() => {
    let alive = true, timer: any
    const tick = async () => {
      try {
        const st = await dash.chatStatus()
        if (!alive) return
        setTools(st.tools); setConn(st.ready ? 'ready' : st.error ? 'error' : 'building'); if (st.error) setLastError(st.error)
        if (!st.ready && !st.error) timer = setTimeout(tick, 2500)
      } catch (e: any) { if (alive) { setConn(/401/.test(String(e?.message)) ? 'unauthorized' : 'error'); setLastError(String(e?.message || e)) } }
    }
    tick(); return () => { alive = false; clearTimeout(timer) }
  }, [])

  // gates opened elsewhere → cards in the dock
  useEffect(() => {
    let alive = true, timer: any
    const tick = async () => {
      try {
        const { gates } = await dash.gates()
        if (!alive) return
        const open = gates.filter((g) => g.decision == null && !g.timed_out)
        setPending(open.map(toCard))
        for (const g of gates) {
          if (seen.current.has(g.id)) {
            if (g.decision != null || g.timed_out) setTurns((ts) => ts.map((t) => t.gates.some((c) => c.id === g.id) ? { ...t, gates: t.gates.map((c) => c.id === g.id ? toCard(g) : c), done: true } : t))
            continue
          }
          seen.current.add(g.id)
          if (g.decision != null || g.timed_out) continue              // history: not worth a card
          setTurns((ts) => ts.some((t) => t.gates.some((c) => c.id === g.id)) ? ts : [...ts, { id: seq++, role: 'agent', text: '', receipts: [], gates: [toCard(g)], done: false, t: Date.now() }])
        }
      } catch { /* 404 on an old server, 401 handled by api() */ }
      timer = setTimeout(tick, 3000)
    }
    tick(); return () => { alive = false; clearTimeout(timer) }
  }, [])

  const patchLast = (fn: (last: Turn) => void) => setTurns((ts) => {
    const out = ts.slice(); let last = out[out.length - 1]
    if (!last || last.role !== 'agent' || last.done) { last = { id: seq++, role: 'agent', text: '', receipts: [], gates: [], t: Date.now() }; out.push(last) }
    else last = out[out.length - 1] = { ...last, receipts: last.receipts.slice(), gates: last.gates.slice() }
    fn(last); return out
  })

  const onFrame = useCallback((f: any) => {
    const now = performance.now()
    if (f.type === 'gate') {
      seen.current.add(f.id)
      if (f.answered || f.timed_out) setPending((p) => p.filter((g) => g.id !== f.id))
      else setPending((p) => [...p.filter((g) => g.id !== f.id), { id: f.id, kind: f.kind, detail: f.detail, t: Date.now(), timeout_s: f.timeout_s, preview: f.preview }])
    }
    if (f.type === 'error' && !f.id) setLastError(f.error || '')
    patchLast((last) => {
      if (last.firstTokenMs == null && (f.type === 'text' || f.type === 'tool') && sentAt.current) last.firstTokenMs = Math.round(now - sentAt.current)
      if (f.type === 'text') last.text += f.delta
      else if (f.type === 'tool') last.receipts.push({ id: f.id, name: f.name, input: f.input, t0: now })
      else if (f.type === 'tool_result') {
        const i = last.receipts.findIndex((x) => x.id === f.id)
        const r: Receipt = { id: f.id, name: i >= 0 ? last.receipts[i].name : '?', input: i >= 0 ? last.receipts[i].input : null, status: f.status, text: f.text, image: f.image, t0: i >= 0 ? last.receipts[i].t0 : now, t1: now }
        if (i >= 0) last.receipts[i] = r; else last.receipts.push(r)
      } else if (f.type === 'gate') {
        const i = last.gates.findIndex((g) => g.id === f.id)
        const card: GateCard = { id: f.id, kind: f.kind, detail: f.detail, t: i >= 0 ? last.gates[i].t : Date.now(), approve: f.approve, answered: f.answered, timed_out: f.timed_out, timeout_s: f.timeout_s, preview: f.preview }
        if (i >= 0) last.gates[i] = card; else last.gates.push(card)
      } else if (f.type === 'done') last.done = true
      else if (f.type === 'error') { last.error = f.error; last.done = true }
    })
    if (f.type === 'done' || f.type === 'error') setBusy(false)
  }, [])

  const send = useCallback(async (text: string) => {
    const t = text.trim()
    if (!t || busy || conn !== 'ready') return false
    setTurns((ts) => [...ts, { id: seq++, role: 'you', text: t, receipts: [], gates: [], done: true, t: Date.now() }])
    setBusy(true); sentAt.current = performance.now(); setLastError('')
    try {
      const r = await fetch('/api/chat/stream', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify({ prompt: t }) })
      if (r.status === 404 || r.status === 405) {                   // old server: one-shot reply
        const j = await fetch('/api/chat', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: t }) }).then((x) => x.json())
        onFrame(j.error ? { type: 'error', error: j.error } : { type: 'text', delta: j.result || j.reply || '' }); if (!j.error) onFrame({ type: 'done' })
        return true
      }
      if (r.status === 401) { setConn('unauthorized'); onFrame({ type: 'error', error: 'signed out — sign in again' }); return true }
      if (!r.ok || !r.body) { onFrame({ type: 'error', error: `chat ${r.status}` }); return true }
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = ''
      for (;;) {
        const { value, done } = await reader.read(); if (done) break
        buf += dec.decode(value, { stream: true })
        let i: number
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2)
          const data = chunk.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim()).join('\n')
          if (data) { try { onFrame(JSON.parse(data)) } catch { /* keepalive */ } }
        }
      }
      setBusy(false); patchLast((l) => { l.done = true })
    } catch (e: any) { onFrame({ type: 'error', error: e?.message || String(e) }) }
    return true
  }, [busy, conn, onFrame])

  const answer = useCallback(async (id: string, approve: boolean) => {
    try { await dash.answerGate(id, approve) } catch (e: any) { setLastError(e?.message || String(e)) }
    setPending((p) => p.filter((g) => g.id !== id))
    setTurns((ts) => ts.map((t) => t.gates.some((g) => g.id === id) ? { ...t, gates: t.gates.map((g) => g.id === id ? { ...g, approve, answered: true, by: 'you' } : g) } : t))
  }, [])
  const note = useCallback((text: string) => setTurns((ts) => [...ts, { id: seq++, role: 'note', text, receipts: [], gates: [], done: true, t: Date.now() }]), [])
  const reset = useCallback(async () => { await dash.chatReset().catch(() => {}); setTurns([]) }, [])
  return { turns, conn, tools, busy, pending, lastError, send, answer, note, reset }
}

const toCard = (g: Gate): GateCard => ({ id: g.id, kind: g.kind, detail: g.detail, t: (g.t || Date.now() / 1000) * 1000, approve: g.decision ?? undefined, answered: g.decision != null || !!g.timed_out, timed_out: g.timed_out, timeout_s: g.timeout_s, preview: g.preview, by: g.by })
