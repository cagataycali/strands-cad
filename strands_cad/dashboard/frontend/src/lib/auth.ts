// Passkey (WebAuthn) login against strands_cad/dashboard/auth.py — expo's lib/auth.ts adapted: same ceremonies
// (/auth/register|login/begin+finish return {challenge_id, options}), the session is the HttpOnly cookie
// `strands_cad_session`. strands-cad has no /api/auth/me: "am I in" = an authed probe of /api/chat/status (401 → no).
import { EXPIRED_EVENT, expired } from './api'
export { EXPIRED_EVENT }

export interface AuthStatus {
  enabled: boolean; setup_required: boolean; credentials: Credential[]; bootstrap_required?: boolean
  rp_id?: string; secure_context?: boolean; rpid_usable?: boolean; warning?: string; store_error?: string; expected_origins?: string[]
}
export interface Credential { id: string; name: string; created?: number | null; last_used?: number | null }

export async function fetchStatus(): Promise<AuthStatus> {
  const r = await fetch('/auth/status', { cache: 'no-store', credentials: 'include' })
  if (!r.ok) throw new Error(`auth status ${r.status}`)
  return r.json()
}
/** Does the cookie open the door? (auth disabled on the server ⇒ yes) */
export async function fetchMe(): Promise<boolean> {
  const r = await fetch('/api/chat/status', { cache: 'no-store', credentials: 'include' })
  return r.status !== 401 && r.status !== 403
}
export async function authedFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const r = await fetch(url, { ...opts, credentials: 'include' })
  if (r.status === 401) expired()
  return r
}
export async function logout(): Promise<void> { await fetch('/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {}) }

const b64uToBuf = (s: string): ArrayBuffer => {
  const pad = '='.repeat((4 - (s.length % 4)) % 4)
  const b = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const arr = new Uint8Array(b.length)
  for (let i = 0; i < b.length; i++) arr[i] = b.charCodeAt(i)
  return arr.buffer
}
const bufToB64u = (buf: ArrayBuffer): string => {
  let s = ''
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
const detail = async (r: Response, fallback: string) => {
  const j = await r.json().catch(() => ({}))
  return j?.detail?.error || j?.error || (typeof j?.detail === 'string' ? j.detail : '') || fallback
}
const json = (body: unknown): RequestInit => ({ method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

/** Enroll a passkey (the first one seals the printer; needs the bootstrap token when the server demands one). */
export async function register(label = 'admin passkey', bootstrap = ''): Promise<void> {
  if (!window.PublicKeyCredential) throw new Error('this browser has no WebAuthn')
  const begin = await authedFetch('/auth/register/begin', json({ label, bootstrap }))
  if (!begin.ok) throw new Error(await detail(begin, 'register begin failed'))
  const { challenge_id, options } = await begin.json()
  const publicKey: any = { ...options, challenge: b64uToBuf(options.challenge), user: { ...options.user, id: b64uToBuf(options.user.id) } }
  if (options.excludeCredentials) publicKey.excludeCredentials = options.excludeCredentials.map((c: any) => ({ ...c, id: b64uToBuf(c.id) }))
  const cred: any = await navigator.credentials.create({ publicKey })
  if (!cred) throw new Error('no credential created')
  const fin = await fetch('/auth/register/finish', json({
    challenge_id,
    credential: { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufToB64u(cred.response.clientDataJSON), attestationObject: bufToB64u(cred.response.attestationObject) } },
  }))
  if (!fin.ok) throw new Error(await detail(fin, 'register finish failed'))
}

/** Log in with an enrolled passkey (Face ID / Touch ID). */
export async function login(): Promise<void> {
  if (!window.PublicKeyCredential) throw new Error('this browser has no WebAuthn')
  const begin = await fetch('/auth/login/begin', { method: 'POST', credentials: 'include' })
  if (!begin.ok) throw new Error(await detail(begin, 'login begin failed'))
  const { challenge_id, options } = await begin.json()
  const publicKey: any = { ...options, challenge: b64uToBuf(options.challenge) }
  if (options.allowCredentials) publicKey.allowCredentials = options.allowCredentials.map((c: any) => ({ ...c, id: b64uToBuf(c.id) }))
  const cred: any = await navigator.credentials.get({ publicKey })
  if (!cred) throw new Error('no credential')
  const fin = await fetch('/auth/login/finish', json({
    challenge_id,
    credential: { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufToB64u(cred.response.clientDataJSON), authenticatorData: bufToB64u(cred.response.authenticatorData),
        signature: bufToB64u(cred.response.signature), userHandle: cred.response.userHandle ? bufToB64u(cred.response.userHandle) : null } },
  }))
  if (!fin.ok) throw new Error(await detail(fin, 'login failed'))
}

export const listCredentials = async (): Promise<Credential[]> => {
  const r = await authedFetch('/auth/credentials'); if (!r.ok) throw new Error(await detail(r, 'cannot list passkeys'))
  return (await r.json()).credentials
}
