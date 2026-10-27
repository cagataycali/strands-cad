// Telemetry poll: /api/telemetry every 2 s (10 s when the tab is hidden). `link` says whether the dash answers at all.
import { useEffect, useRef, useState } from 'react'
import { dash, type Telemetry } from './lib/api'

export type Link = 'connecting' | 'live' | 'reconnecting'

export function usePrinter() {
  const [t, setT] = useState<Telemetry | null>(null)
  const [link, setLink] = useState<Link>('connecting')
  const [tickMs, setTickMs] = useState(0)
  const fails = useRef(0)
  useEffect(() => {
    let alive = true, timer: any
    const tick = async () => {
      const t0 = performance.now()
      try {
        const s = await dash.telemetry()
        if (!alive) return
        setT(s); setLink('live'); fails.current = 0; setTickMs(Math.round(performance.now() - t0))
      } catch (e: any) {
        if (!alive) return
        fails.current++
        if (/503|not configured/.test(String(e?.message))) { setT((p) => p ?? ({ connected: false, error: String(e.message) } as any)); setLink('live') }
        else if (fails.current >= 2) setLink('reconnecting')
      }
      timer = setTimeout(tick, document.hidden ? 10000 : 2000)
    }
    tick()
    const vis = () => { if (!document.hidden) { clearTimeout(timer); tick() } }
    document.addEventListener('visibilitychange', vis)
    return () => { alive = false; clearTimeout(timer); document.removeEventListener('visibilitychange', vis) }
  }, [])
  return { s: t, link, tickMs }
}
