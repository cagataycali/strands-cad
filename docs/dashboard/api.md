# Dashboard API Reference

The dashboard is a FastAPI app on `:8099`. All `/api/*` routes require a valid
WebAuthn-minted JWT (unless `STRANDS_CAD_AUTH_ENABLED=false`).

## Auth

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/auth/status` | Sealed / enrolled state |
| `POST` | `/auth/register/begin` | Start passkey enrollment |
| `POST` | `/auth/register/finish` | Complete enrollment (store public key) |
| `POST` | `/auth/login/begin` | Start passkey login (challenge) |
| `POST` | `/auth/login/finish` | Complete login → JWT |
| `POST` | `/auth/logout` | Drop session |
| `GET` | `/auth/credentials` | List enrolled credentials |

## Health & telemetry

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/telemetry` | Temps, progress %, state, SD status |
| `POST` | `/api/control` | Pause / resume / stop |

## Camera

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/camera/status` | Camera reachable? |
| `GET` | `/api/camera/snapshot` | Single JPEG |
| `GET` | `/api/camera/stream` | MJPEG multipart stream |

## Config & models

| Method | Path | Purpose |
|---|---|---|
| `GET` / `POST` | `/api/config` | Read / update printer config |
| `GET` | `/api/models` | List available models |
| `GET` | `/api/model/{name}` | Fetch a model file |
| `GET` | `/api/model/meta/{name}` | Model metadata |

## Filaments (AMS)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/filaments` | Configured filaments |
| `GET` | `/api/filaments/live` | Live AMS state |
| `POST` | `/api/filaments/sync` | Sync from printer |
| `POST` | `/api/filaments` | Update filament config |

## Build plate

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/plate` | Current plate state |
| `POST` | `/api/plate/add` | Add a model to the plate |
| `POST` | `/api/plate/update` | Move / scale / rotate an item |
| `POST` | `/api/plate/recolor` | Recolor an item |
| `POST` | `/api/plate/remove` | Remove an item |
| `POST` | `/api/plate/clear` | Clear the plate |
| `POST` | `/api/plate/arrange` | Auto-arrange |
| `POST` | `/api/plate/export` | Export plate → 3MF |
| `POST` | `/api/plate/print` | Slice + print the plate |

## Jobs

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/slice` | Slice a 3MF |
| `POST` | `/api/print` | Start a print |
| `GET` | `/api/job/{jid}` | Job status |
| `GET` | `/api/jobs` | List jobs |

## Agent chat

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/chat/status` | Chat agent state |
| `POST` | `/api/chat` | Send a message |
| `POST` | `/api/chat/reset` | Reset the conversation |

## Realtime voice

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/realtime/token` | Mint an ephemeral OpenAI Realtime token |

## Telegram & thinker

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/telegram/status` | Bridge status |
| `POST` | `/api/telegram/notify` | Push a notification |
| `POST` | `/api/telegram/snapshot` | Send a camera snapshot |
| `GET` | `/api/telegram/detect` | Detection status |
| `POST` | `/api/telegram/poll` | Poll for commands |
| `GET` | `/api/thinker/status` | Autonomous thinker state |
| `POST` | `/api/thinker/control` | Start / stop the thinker |

## Root

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The single-page dashboard UI (HTML) |
