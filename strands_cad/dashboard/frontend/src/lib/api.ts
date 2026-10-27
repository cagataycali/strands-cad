// The dash's shapes (strands_cad/dashboard/server.py + printer.py snapshot) and the small fetch client.
// The session is the HttpOnly cookie `strands_cad_session`; a 401 means "signed out" → EXPIRED_EVENT → the gate asks again.
export const EXPIRED_EVENT = 'printer-auth-expired'
export const expired = () => window.dispatchEvent(new Event(EXPIRED_EVENT))

export type Temps = { nozzle: number | null; nozzle_target: number | null; bed: number | null; bed_target: number | null; chamber: number | null }
export type AmsSlot = { id: number | string | null; material: string | null; color: string | null; remaining_pct: number | null }
export type Ams = { id: number | string | null; humidity: number | string | null; temp: number | string | null; slots: AmsSlot[] }
export type Telemetry = {
  connected: boolean; serial: string; gcode_state: string | null; progress: number | null; layer: number | null; total_layers: number | null
  remaining_min: number | null; subtask_name: string | null; sdcard: boolean | null; nozzle_count: number
  temps: Temps; fan: { cooling: number | null; big_fan1: number | null; big_fan2: number | null }; speed_level: number | null
  ams: Ams[]; wifi_signal: string | null; age_seconds: number | null; error?: string
}
export type ModelRow = { name: string; ext: string; size: number; mtime: number }
export type ModelMeta = { name: string; size: number; ext: string; triangles?: number; vertices?: number; bbox?: { min: number[]; max: number[]; size: number[] }; volume_cm3?: number; weight_pla_g?: number; meta_error?: string; error?: string }
export type Job = {
  id: string; kind: 'slice' | 'print' | 'pipeline' | string; state: 'queued' | 'running' | 'awaiting_approval' | 'done' | 'error' | 'denied' | string
  created: number; log: string[]; result: any; error: string | null; input?: string; gcode?: string; profile?: string; printer_model?: string
  step?: string; steps?: { name: string; state: string; t?: number; text?: string }[]; preview?: string | null; model?: string | null; gate?: string | null; title?: string
}
export type Gate = { id: string; kind: string; detail: string; t: number; decision: boolean | null; by?: string; job?: string; preview?: string | null; timeout_s?: number; timed_out?: boolean }
export type ChatStatus = { ready: boolean; error: string | null; tools: number; turns: number; building?: boolean }
export type Config = Record<string, any>

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, { method, credentials: 'include', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (r.status === 401) expired()
  if (!r.ok) throw new Error(j?.detail?.error || (typeof j?.detail === 'string' ? j.detail : '') || j?.error || `${r.status}`)
  return j
}

export const dash = {
  telemetry: () => api<Telemetry>('GET', '/api/telemetry'),
  control: (action: 'pause' | 'resume' | 'stop') => api('POST', '/api/control', { action }),
  cameraStatus: () => api('GET', '/api/camera/status'),
  ticket: () => api<{ ticket: string; expires_in: number }>('POST', '/auth/ticket', { scope: 'camera' }),
  models: () => api<{ workdir: string; models: ModelRow[] }>('GET', '/api/models'),
  meta: (name: string) => api<ModelMeta>('GET', `/api/model/meta/${encodeURIComponent(name)}`),
  modelUrl: (name: string) => `/api/model/${name.split('/').map(encodeURIComponent).join('/')}`,
  jobs: () => api<{ jobs: Job[] }>('GET', '/api/jobs'),
  job: (id: string) => api<Job>('GET', `/api/job/${id}`),
  gates: () => api<{ gates: Gate[] }>('GET', '/api/gates'),
  answerGate: (id: string, approve: boolean) => api('POST', `/api/gate/${id}`, { approve }),
  pipeline: (body: { model?: string; prompt?: string; profile?: string }) => api<{ job_id: string }>('POST', '/api/pipeline', body),
  chatStatus: () => api<ChatStatus>('GET', '/api/chat/status'),
  chatReset: () => api('POST', '/api/chat/reset', {}),
  config: () => api<Config>('GET', '/api/config'),
  setConfig: (patch: Config) => api<Config>('POST', '/api/config', patch),
  version: () => api<{ version: string; service?: string }>('GET', '/api/version'),
}

export const fmtMin = (m: number | null | undefined) => (m == null ? '—' : m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} m` : `${m} min`)
export const fmtTemp = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)}°`)
export const ago = (t: number, now = Date.now()) => { const s = Math.max(0, Math.round((now - t) / 1000)); return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d` }
/** gcode_state → the word on the pill */
export const stateWord = (t: Telemetry | null, link: string) => !t ? 'CONNECTING' : link !== 'live' ? 'OFFLINE' : !t.connected ? 'PRINTER DOWN' : ({ RUNNING: 'PRINTING', PAUSE: 'PAUSED', FINISH: 'FINISHED', FAILED: 'FAILED', IDLE: 'IDLE', PREPARE: 'PREPARING', SLICING: 'SLICING' } as Record<string, string>)[t.gcode_state || ''] || (t.gcode_state || 'LIVE')
