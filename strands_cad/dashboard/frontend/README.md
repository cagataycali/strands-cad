# printer cockpit — the dashboard SPA

React 18 + Vite 5 + TypeScript, the same stack and `styles.css` vocabulary as expo-the-arm's dashboard
(Strands design language: paper/dark, green accent, JetBrains Mono labels, 1 px borders, no glass).

```
cd strands_cad/dashboard/frontend
npm ci
npm run dev      # :4199, proxies /api and /auth to a local strands-cad-dashboard on :8099
npm run build    # → dist/  (tsc -b && vite build)
```

**`dist/` is committed.** The deploy host (Thor) has no node, and the PyPI wheel ships the built UI
(`[tool.hatch.build.targets.wheel.force-include]` in pyproject.toml). After a UI change: build, commit `dist/`
with the source, push. The server serves `dist/index.html` at `/` and mounts `/assets`, `/fonts`, `/icons`
publicly (hashed static files, no data); every `/api/*` route except `health`/`version` stays behind the passkey.

Screens: AuthGate (passkeys) → Cockpit: chamber camera hero (MJPEG with a 60 s camera ticket), state strip
(progress · job · layer · remaining · temps · AMS · pause/stop with a confirm), the agent dock (streamed turns,
tool receipts, gate cards). Drawers: Models (three.js STL preview → "Print this (asks first)"), Jobs, Settings.
The pre-revamp single-file UI (1,213 lines, draggable PiPs, plate editor, filament palette) was removed in favour of this
SPA once the gated pipeline landed; the plate/filament APIs and agent tools still exist for the agent's use.
