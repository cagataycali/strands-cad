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
