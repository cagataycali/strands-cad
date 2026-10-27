// The chamber camera as the hero: MJPEG /api/camera/stream with a 60 s camera-scoped ticket (an <img> cannot carry the
// cookie's Authorization — the ticket can touch nothing but the stream). On error: new ticket, then snapshots every 2 s.
import { useEffect, useRef, useState } from 'react'
import { dash, type Telemetry } from '../lib/api'
import { Ico } from './Icons'

export default function Camera({ s, onMsg }: { s: Telemetry | null; onMsg: (m: string) => void }) {
  const [src, setSrc] = useState('')
  const [mode, setMode] = useState<'stream' | 'snapshot' | 'none'>('none')
  const [fps, setFps] = useState<number | null>(null)
  const errs = useRef(0)
  const start = async () => {
    try { const { ticket } = await dash.ticket(); setSrc(`/api/camera/stream?ticket=${encodeURIComponent(ticket)}&t=${Date.now()}`); setMode('stream') }
    catch (e: any) { setMode('snapshot'); onMsg(`camera ticket: ${e?.message || e}`) }
  }
  useEffect(() => { start(); dash.cameraStatus().then((c: any) => setFps(c?.fps ?? null)).catch(() => {}) }, [])
  useEffect(() => {
    if (mode !== 'snapshot') return
    const id = setInterval(() => setSrc(`/api/camera/snapshot?t=${Date.now()}`), 2000)
    setSrc(`/api/camera/snapshot?t=${Date.now()}`)
    return () => clearInterval(id)
  }, [mode])
  const onError = () => { errs.current++; if (mode === 'stream' && errs.current <= 2) start(); else setMode('snapshot') }
  const live = s?.connected
  return (
    <div className="cam-tile hero" data-state={live ? 'live' : 'frozen'} data-testid="camera">
      <div className="cam-tile-head">
        <span><span className="ic">{Ico.camera()}</span> chamber · {mode === 'stream' ? `live${fps ? ` · ${fps} fps` : ''}` : mode === 'snapshot' ? 'snapshots · 2 s' : 'connecting'}</span>
        <span className="cam-tile-right">{live && <span className="act-live on" aria-label="live" />}<button className="mini-btn cam-reset" onClick={() => { errs.current = 0; start() }} title="reconnect the stream">reconnect</button></span>
      </div>
      <div className="cam-frame">{src && <img className="cam-img" src={src} alt="printer chamber" onError={onError} />}</div>
    </div>
  )
}
