// Settings drawer, thin on purpose: printer (read-only), agent model, slice profile, passkeys, scheme, version, sign out.
import { useEffect, useState } from 'react'
import { dash, type Config } from './lib/api'
import { listCredentials, logout, register, type Credential } from './lib/auth'
import { useScheme } from './lib/scheme'
import { Ico } from './components/Icons'

const PROFILES = ['PLA_0_20', 'PETG_0_20', 'ABS_0_20', 'TPU_0_20', 'PLA_SILK_0_16']

export default function Settings({ toast, onSignedOut }: { toast: (t: string, k?: 'ok' | 'bad' | 'info') => void; onSignedOut: () => void }) {
  const [cfg, setCfg] = useState<Config | null>(null)
  const [creds, setCreds] = useState<Credential[]>([])
  const [version, setVersion] = useState('')
  const [model, setModel] = useState('')
  const [scheme, setScheme] = useScheme()
  useEffect(() => {
    dash.config().then((c) => { setCfg(c); setModel(c.model || '') }).catch((e) => toast(`config: ${e.message}`, 'bad'))
    listCredentials().then(setCreds).catch(() => {})
    dash.version().then((v) => setVersion(v.version)).catch(() => {})
  }, [])
  const save = async (patch: Config) => { try { const c = await dash.setConfig(patch); setCfg(c); toast('saved', 'ok') } catch (e: any) { toast(`save: ${e.message}`, 'bad') } }
  const addKey = async () => { try { await register(`passkey ${new Date().toLocaleDateString()}`); setCreds(await listCredentials()); toast('passkey added', 'ok') } catch (e: any) { toast(e?.message || String(e), 'bad') } }
  return (
    <div className="config-body cfg-section">
      <div className="cfg-label">printer</div>
      <div className="cfg-current">{cfg ? `${cfg.ip || '(no ip)'} · serial ${cfg.serial || 'auto'} · ${cfg.printer_model || 'Bambu Lab X2D'} · ${cfg.nozzle_count ?? 2} nozzles` : '…'}</div>
      <div className="cfg-note">Credentials live in Thor's <span className="mono">.env</span> (BAMBU_IP / ACCESS_CODE / SERIAL); not editable from here on purpose.</div>

      <div className="cfg-label">slice profile</div>
      <div className="cfg-chips">{PROFILES.map((p) => <button key={p} className={`cfg-chip ${cfg?.slice_profile === p ? 'on' : ''}`} onClick={() => save({ slice_profile: p })}>{p}</button>)}</div>

      <div className="cfg-label">agent model <span className="cfg-note-inline">STRANDS_MODEL_ID · empty = default Bedrock</span></div>
      <div className="cfg-row"><input className="inp mono" value={model} onChange={(e) => setModel(e.target.value)} placeholder="us.anthropic.claude-…" /><button className="gate-btn ghost sm" onClick={() => save({ model })}>save</button></div>

      <div className="cfg-label">passkeys</div>
      <div className="env-list">{creds.map((c) => <div key={c.id} className="env-row"><span className="env-key"><span className="ic">{Ico.key()}</span>{c.name}</span><span className="mono txt-muted" style={{ fontSize: 11 }}>{c.created ? new Date(c.created * 1000).toLocaleDateString() : ''}</span></div>)}{creds.length === 0 && <div className="cfg-note">none listed</div>}</div>
      <button className="gate-btn ghost sm" onClick={addKey}><span className="ic">{Ico.key()}</span>add a passkey for this device</button>

      <div className="cfg-label">appearance</div>
      <div className="cfg-chips">{(['auto', 'paper', 'dark'] as const).map((s) => <button key={s} className={`cfg-chip ${scheme === s ? 'on' : ''}`} onClick={() => setScheme(s)}>{s}</button>)}</div>

      <div className="cfg-label">about</div>
      <div className="cfg-note">strands-cad {version || '…'} · workdir <span className="mono">{cfg?.workdir || '…'}</span> · <a href="https://github.com/cagataycali/strands-cad" target="_blank" rel="noreferrer">source</a></div>
      <button className="gate-btn ghost sm" style={{ marginTop: 10 }} onClick={() => logout().then(onSignedOut)}>sign out</button>
    </div>
  )
}
