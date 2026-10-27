// The Gate: the first thing anyone sees. Empty store → enroll the admin passkey (bootstrap token when the server was started
// with STRANDS_CAD_AUTH_BOOTSTRAP); afterwards → "Continue with passkey". The server gates everything else.
import { useCallback, useEffect, useState } from 'react'
import { EXPIRED_EVENT, fetchMe, fetchStatus, login, logout, register, type AuthStatus } from '../lib/auth'
import { Brand } from './Brand'
import { Ico } from './Icons'

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [failed, setFailed] = useState(false)
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [bootstrap, setBootstrap] = useState('')

  const refresh = useCallback(() => Promise.all([fetchStatus(), fetchMe()])
    .then(([s, me]) => { setStatus(s); setAuthed(me || !s.enabled); setFailed(false) })
    .catch(() => setFailed(true)), [])
  useEffect(() => {
    refresh()
    const onExpired = () => { setAuthed(false); setErr('session expired — sign in again'); refresh() }
    window.addEventListener(EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(EXPIRED_EVENT, onExpired)
  }, [refresh])

  if (failed) return <main className="gate"><div className="gate-card"><Brand /><h1 className="gate-title">The dash is not answering</h1><p className="gate-sub">printer.cagatay.my did not answer /auth/status — the tunnel or the service on Thor is down.</p><button className="gate-btn primary" onClick={() => { setFailed(false); refresh() }}>Try again</button></div></main>
  if (authed) return <>{children}</>

  const run = (fn: () => Promise<unknown>) => async () => {
    setBusy(true); setErr('')
    try { await fn(); setAuthed(true) } catch (e: any) { setErr(e?.name === 'NotAllowedError' ? 'cancelled or timed out — try again' : e?.message || String(e)) } finally { setBusy(false) }
  }
  const setup = status?.setup_required
  const insecure = status && status.secure_context === false
  const badRp = status && status.rpid_usable === false
  const locked = !!status?.store_error
  const blocked = busy || !status || authed === null || !!insecure || !!badRp || locked

  return (
    <main className="gate" data-testid="auth-gate">
      <div className="gate-card">
        <Brand />
        <h1 className="gate-title">{!status ? 'Checking the lock' : setup ? 'Seal the printer with a passkey' : 'Sign in to the printer'}</h1>
        <p className="gate-sub">The Bambu X2D: chamber camera, job state, the prompt-to-print agent and STOP. A printer heats and moves — everything behind this door needs a key.</p>
        {(insecure || badRp || locked) && <div className="gate-warn" role="alert"><span className="ic">{Ico.alert()}</span><span>{status?.warning || status?.store_error || 'This origin cannot run passkeys. Open over HTTPS with a hostname.'}</span></div>}
        {err && <div className="gate-err" role="alert"><span className="ic">{Ico.alert()}</span><span>{err}</span></div>}
        {setup ? (
          <>
            {status?.bootstrap_required && (<>
              <label className="lbl" htmlFor="gate-bootstrap">Bootstrap token</label>
              <input id="gate-bootstrap" className="gate-input mono" type="password" placeholder="STRANDS_CAD_AUTH_BOOTSTRAP from Thor's .env" autoComplete="off" value={bootstrap} onChange={(e) => setBootstrap(e.target.value)} />
            </>)}
            <button className="gate-btn primary" onClick={run(() => register('admin passkey', bootstrap))} disabled={blocked || (!!status?.bootstrap_required && !bootstrap.trim())} aria-busy={busy} data-testid="gate-register">
              <span className="ic">{Ico.key()}</span>{busy ? 'creating' : 'Create the admin passkey'}
            </button>
            <p className="gate-note">The private key never leaves this device (Face ID, Touch ID or a security key). From then on the cockpit is sealed; more passkeys are added from a signed-in device in Settings.</p>
          </>
        ) : (
          <>
            <button className="gate-btn primary" onClick={run(login)} disabled={blocked} aria-busy={busy} data-testid="gate-login">
              <span className="ic">{Ico.key()}</span>{busy ? 'verifying' : 'Continue with passkey'}
            </button>
            <button className="gate-btn ghost" onClick={() => { logout().then(() => { setAuthed(false); refresh() }) }}>use a different device</button>
          </>
        )}
        <div className="gate-foot"><span>{status?.rp_id ? `rp ${status.rp_id}` : ''}</span><span>{status ? `${status.credentials.length} passkey${status.credentials.length === 1 ? '' : 's'}` : ''}</span></div>
      </div>
    </main>
  )
}
