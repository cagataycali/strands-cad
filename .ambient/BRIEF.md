# AMBIENT UI IMPROVEMENT BRIEF — strands-cad dashboard

## Mission
Iteratively improve the dashboard's DESIGN, USABILITY, and TOUCH SURFACES.
Owner is away ~2h. Work autonomously, one focused improvement per cycle.

## Target file
`strands_cad/dashboard/frontend/index.html` (single-file SPA, served on :8099)

## Known problems (owner-reported)
1. Chat panel (`.dock`) can't be dragged down with fingers — only a tap-to-cycle grip.
   → Add a real touch DRAG gesture on the grip/handle to resize the sheet fluidly.
   → Set `window.__dockDraggable = true` when the drag handler is wired (harness checks this).
2. Telemetry PiP starts at top:16px (top-right) — too close to top edge, can't pull down.
   → Give top-docked PiPs more clearance (>=40px) so there's finger room above them.
3. General: better spacing, larger touch targets (>=28-44px), no PiP overlap on small screens.

## Workflow EVERY cycle (strict)
1. `cd /home/cagatay/strands-cad`
2. Make ONE focused change to index.html (small, reviewable).
3. VALIDATE in real browser: `/home/cagatay/miniconda3/bin/python .ambient/uicheck.py`
   - Exit 0 = all invariants pass. Fix regressions before moving on.
4. Run unit tests: `.venv/bin/python -m pytest tests/ -q -o addopts="" 2>&1 | tail -5`
   - Must stay green (37 tests).
5. Append a dated entry to `.ambient/JOURNAL.md` (what changed + harness result).
6. `git add -A && git commit -m "ui(ambient): <what>"` — commit each green iteration.

## Rules
- The dashboard hot-serves index.html (no rebuild needed) — just reload in harness.
- NEVER break the 37 pytest tests or introduce console errors.
- Keep changes small & atomic so each is independently revertable.
- Prefer progressive enhancement; keep desktop working while fixing mobile.
- Don't touch auth (owner will re-auth). Server runs with AUTH disabled now.
- If harness needs a new invariant for a fix you made, ADD it to uicheck.py.

## Priorities (do in order)
P0: dock finger-drag gesture (#1)  •  P0: telemetry top clearance (#2)
P1: touch target sizes, snap-zone clarity, no overlap
P2: visual polish — spacing, contrast, motion, empty states
