# 🔧 strands-cad → Interactive Voice+3D Print Cockpit — BUILD PLAN

Mirror of scout-the-rover / neon-the-g1 dashboards, adapted for CAD/print.
Printer: Bambu P1 @ 192.168.1.164 (access 7e7f5d23, serial 20P6AJ632801669).

## 🎯 Goal
A single passkey-sealed web page where you can:
1. **Talk** to a CAD agent (OpenAI Realtime — text + voice, natural language).
2. **See** the 3D object live in the browser (three.js STL viewer).
3. **Recolor** parts interactively (per-mesh color pickers).
4. **Slice → upload → print** to the Bambu in ONE click.
5. **Watch** the chamber camera (already working RTSPS→MJPEG).
6. **Configure** everything (printer creds, model, voice, profiles) like neon-the-g1.

## 📐 Architecture (what exists ✓ / what we add ➕)

```
Browser (index.html)
 ├─ 🔐 WebAuthn overlay            ✓ exists
 ├─ 📷 Camera <img> MJPEG          ✓ exists  (/api/camera/stream)
 ├─ 🖨️ Telemetry + pause/stop      ✓ exists  (/api/telemetry,/api/control)
 ├─ 🧊 three.js STL viewer         ➕ NEW  (STLLoader + OrbitControls, CDN)
 ├─ 🎨 Per-part color pickers      ➕ NEW  (mesh.material.color)
 ├─ 💬 Chat panel (text)           ➕ NEW  (/api/chat → in-dash agent)
 ├─ 🎙️ Voice (OpenAI Realtime)     ➕ NEW  (WebRTC + ephemeral token)
 └─ ⚙️ Config panel                ➕ NEW  (/api/config GET/POST)

FastAPI server.py
 ├─ auth / camera / telemetry / control   ✓ exists
 ├─ /api/chat            ➕ in-dash agent (chat_agent.py pattern from neon)
 ├─ /api/chat/status     ➕
 ├─ /api/realtime/token  ➕ mint OpenAI ephemeral key (server holds real key)
 ├─ /api/config GET/POST ➕ live printer creds + model + voice + slice profile
 ├─ /api/models          ➕ list STL/3MF files in workdir
 ├─ /api/model/{name}    ➕ serve STL bytes to three.js
 ├─ /api/slice           ➕ slice_bambu wrapper (async job)
 ├─ /api/print           ➕ upload+send in one call (closed loop)
 └─ /api/job/{id}        ➕ poll slice/print job status
```

## 🧩 Files to create/modify

| File | Action | What |
|---|---|---|
| `strands_cad/dashboard/chat_agent.py`   | ➕ NEW | in-dashboard Strands agent w/ ALL_TOOLS + live printer state injection |
| `strands_cad/dashboard/realtime.py`     | ➕ NEW | OpenAI Realtime ephemeral-token minting + session config |
| `strands_cad/dashboard/config_store.py` | ➕ NEW | JSON-backed live config (creds/model/voice/profile), chmod 600 |
| `strands_cad/dashboard/jobs.py`         | ➕ NEW | async slice/print job runner + status registry |
| `strands_cad/dashboard/models.py`       | ➕ NEW | list/serve STL & 3MF from a working dir |
| `strands_cad/dashboard/server.py`       | ✏️ EDIT | add the 9 new routes above, keep auth guard |
| `strands_cad/dashboard/frontend/index.html` | ✏️ REWRITE | add viewer + chat + voice + config tabs (keep camera/telemetry) |
| `pyproject.toml`                        | ✏️ EDIT | dashboard extra += `openai>=1.40` (realtime), keep rest |
| `tests/test_dashboard.py`               | ✏️ EDIT | add route-presence + config_store + jobs unit tests |
| `.strands_cad_auth.json`                | ✅ DONE | rotated (JWT secret fresh, creds cleared) |

## 🎙️ Voice = OpenAI Realtime (browser WebRTC, "realtime 2" = gpt-realtime)
- Server keeps `OPENAI_API_KEY` secret; browser NEVER sees it.
- `/api/realtime/token` → POST to OpenAI `/v1/realtime/client_secrets`,
  returns short-lived ephemeral key + session config (voice, instructions,
  tools). Browser opens WebRTC PeerConnection to OpenAI directly.
- Realtime **tools** map to dashboard actions (recolor, slice, print, status)
  via data-channel function-calling → POST back to `/api/*`.
- Fallback: if no OPENAI_API_KEY, voice tab shows "set key in config".

## ⚙️ Config abilities (neon-the-g1 parity)
Editable live from UI (persisted to `.strands_cad_config.json`, 600):
- Printer: IP / access_code / serial
- Model: STRANDS_MODEL_ID (chat agent)
- Voice: provider(openai) / VOICE_MODEL(gpt-realtime) / VOICE_NAME(alloy…)
- Slice: default profile (PLA_0_20…) / printer_model
- Camera: fps / quality
POST hot-applies (rebuilds printer/camera singletons, updates env).

## 🔒 Security
- All `/api/*` stay behind WebAuthn JWT (existing middleware).
- Realtime token endpoint is auth-gated (only sealed users mint keys).
- Bootstrap token for first enrollment: set STRANDS_CAD_AUTH_BOOTSTRAP.

## 📋 Build order (continuous, each step verified)
1. config_store.py + /api/config  (foundation)          ← START
2. models.py + /api/models + /api/model/{name} + viewer
3. chat_agent.py + /api/chat  (text chat works)
4. jobs.py + /api/slice + /api/print + status polling
5. realtime.py + /api/realtime/token + voice UI (WebRTC)
6. Rewrite index.html: tabs (Viewer|Chat|Camera|Config), color pickers
7. pyproject + tests, run pytest, launch dashboard --tls, smoke test
