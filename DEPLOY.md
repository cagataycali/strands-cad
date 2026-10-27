# 🚀 Deploying strands-cad (the printer cockpit)

Three loops make up the cockpit — all driven from `.env`:

| Loop        | What it does                                              | Entry |
|-------------|-----------------------------------------------------------|-------|
| `dashboard` | WebAuthn web UI on `:8099` (camera, plate, slice, print)  | `strands-cad-dashboard` |
| `thinker`   | slow-thinker: reflects on printer state every 60s         | `strands-cad-thinker`   |
| `telegram`  | chat-with-your-printer bridge                             | `strands-cad-telegram`  |

They talk to the Bambu printer **directly** (MQTT `:8883` + RTSPS `:322`), so
they need no shared HTTP server — each can run as its own process/container.

## 1. Quick start (bare metal)

```bash
make install          # venv + deps (uv or pip, auto-detected) + creates .env
$EDITOR .env          # set BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL / tokens
make run              # dashboard on :8099 (runs thinker+telegram in-process)
```

## 2. Durable systemd services (recommended for the Thor / any Linux box)

Runs each loop as a **separate** `--user` service that auto-restarts and
survives logout/reboot. The dashboard runs in *split-loops* mode so it does
**not** double-run thinker/telegram.

```bash
make persist          # render deploy/*.service → ~/.config/systemd/user → enable+start
make status           # systemctl status of all three
make logs             # journalctl -f across all three
make persist-restart  # after editing .env
make unpersist        # stop + remove everything

loginctl enable-linger $USER   # (once) start at boot without login
```

Individual control:
```bash
systemctl --user restart strands-cad-dashboard
systemctl --user stop    strands-cad-thinker
```

## 3. Docker (isolated, stop-anytime)

One image, entrypoint dispatches by command (`dashboard|thinker|telegram|mcp`).

```bash
make docker-build     # build strands-cad:latest (includes [dashboard] extra + ffmpeg)
make docker-up        # dashboard only → :8099
make docker-up-all    # dashboard + thinker + telegram
make docker-ps        # status
make docker-logs      # follow logs
make docker-restart   # after editing .env
make docker-down      # stop + remove
```

State (config + plate) persists in the `cad-state` named volume; sliced gcode
lands in `./examples`. Bedrock creds come from `.env`
(`AWS_BEARER_TOKEN_BEDROCK` + `AWS_REGION`).

### OrcaSlicer sidecar (aarch64 headless CLI)

Slicing needs OrcaSlicer, which drags in GTK/WebKit — kept OUT of the lean app
image. Build the sidecar once:

```bash
make docker-slicer-build   # → strands-cad/orcaslicer:2.5.0
```

Point `STRANDS_CAD_SLICER` at a wrapper that runs
`docker run --rm -v $PWD:/work strands-cad/orcaslicer:2.5.0 <args>` if you want the
app container to slice via the sidecar. `slice_bambu` **auto-detects** this
image (env `STRANDS_CAD_SLICER_DOCKER_IMAGE`, default `strands-cad/orcaslicer:2.5.0`)
and slices in-container for reproducible Bambu-flavored gcode. On bare metal, the native
`~/.local/share/OrcaSlicer/bin/orca-slicer` is used directly.

## `.env` reference (essentials)

```
BAMBU_IP=192.168.1.164
BAMBU_ACCESS_CODE=xxxxxxxx      # from the printer screen (rotates!)
BAMBU_SERIAL=...
MODEL_PROVIDER=bedrock
STRANDS_MODEL_ID=global.anthropic.claude-opus-4-8
AWS_BEARER_TOKEN_BEDROCK=...
AWS_REGION=us-west-2
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
THINKER_INTERVAL=60
STRANDS_CAD_SLICER=/home/you/.local/share/OrcaSlicer/bin/orca-slicer
```


## Auth is ON by default (2026-10-27)

`deploy/strands-cad-dashboard.service` and `make dashboard` used to start the cockpit with `--no-auth`.
They no longer do: `make persist` renders a unit that runs `strands-cad-dashboard $STRANDS_CAD_DASHBOARD_ARGS`
with the variable empty, so passkeys are required. Disabling auth is an explicit choice — put
`STRANDS_CAD_DASHBOARD_ARGS=--no-auth` in `.env` (LAN-only, never behind a public tunnel).

Behind a tunnel (e.g. https://printer.cagatay.my) also set in `.env`:

```
STRANDS_CAD_AUTH_RP_ID=printer.cagatay.my
STRANDS_CAD_AUTH_ORIGIN=https://printer.cagatay.my,http://localhost:8099
STRANDS_CAD_AUTH_BOOTSTRAP=<random>      # required for the FIRST passkey enrollment, then remove it
```

### tiny.technology device (tiny.body.json)

The dashboard is a tiny endpoint device (`platform bambu-x2d`). tiny dials `POST /api/chat`, `GET /api/telemetry`,
`GET /api/camera/snapshot`, `POST /api/stop` with a bearer = the service token:

```
cd ~/strands-cad && .venv/bin/python -m strands_cad.dashboard.auth service-token --name tiny
```

A print asked for through tiny ends at the **operator gate**: the reply names the model, carries the preview URL and
says it is waiting for approval on the dashboard. The service token may deny a gate but never approve one
(`STRANDS_CAD_GATE_FROM_DEVICE=1` opts in; default off).

### Updating the UI

The cockpit is a Vite + React SPA; `strands_cad/dashboard/frontend/dist/` is committed (the host needs no node).
After `git pull` + `pip install -e ".[dashboard]"` + `systemctl --user restart strands-cad.target` the new UI is live.
