# Environment Variables

All the knobs, in one place.

## Model / agent

| Var | Default | Meaning |
|---|---|---|
| `STRANDS_MODEL_ID` | provider default | Override the LLM used by the chat agent |
| `MODEL_PROVIDER` | auto-detect | Force a model provider (bedrock, anthropic, …) |
| `OPENAI_API_KEY` | — | Needed for the dashboard's Realtime voice token minting |

## Printer (Bambu Lab)

| Var | Default | Meaning |
|---|---|---|
| `BAMBU_IP` | — | Printer LAN IP |
| `BAMBU_ACCESS_CODE` | — | LAN access code (printer *Settings → Network*) |
| `BAMBU_SERIAL` | — | Printer serial (some flows) |

## Dashboard

| Var | Default | Meaning |
|---|---|---|
| `STRANDS_CAD_DASH_PORT` | `8099` | Dashboard port |
| `STRANDS_CAD_TLS` | `false` | Serve HTTPS (needed for LAN passkeys) |
| `STRANDS_CAD_AUTH_ENABLED` | `true` | Master WebAuthn switch |
| `STRANDS_CAD_AUTH_RP_ID` | derived | Pin the WebAuthn relying-party id (a hostname) |
| `STRANDS_CAD_AUTH_ORIGIN` | derived from `Host` | Comma-separated expected-origin allowlist (set behind a reverse proxy) |
| `STRANDS_CAD_AUTH_TICKET_TTL` | `60` | Lifetime of camera-only stream tickets, seconds |
| `STRANDS_CAD_AUTH_BOOTSTRAP` | — | One-time secret gating the *first* enrollment |
| `STRANDS_CAD_DASH_CORS` | *(empty)* | Comma-separated CORS origins. Empty = no CORS (same-origin needs none) |
| `STRANDS_CAD_DASH_DOCS` | `false` | Expose `/docs`, `/redoc`, `/openapi.json` (still auth-gated) |

## Slicer

| Var | Default | Meaning |
|---|---|---|
| `STRANDS_CAD_SLICER` | auto | Pin a host slicer binary path |
| `STRANDS_CAD_SLICER_DOCKER` | `1` | `0` disables the Dockerized OrcaSlicer path |
| `STRANDS_CAD_SLICER_DOCKER_IMAGE` | `strands-cad/orcaslicer:2.5.0` | Override the slicer image tag |

## Config & state files

strands-cad keeps a little local state (git-ignored) in the working directory:

| File | Purpose |
|---|---|
| `.strands_cad_config.json` | Printer config (model, bed, filaments, nozzles) |
| `.strands_cad_auth.json` | Enrolled WebAuthn credentials (public keys) |
| `.strands_cad_tls/` | Self-signed TLS certs |
| `.strands_cad_plate.json` | Current build-plate layout |
