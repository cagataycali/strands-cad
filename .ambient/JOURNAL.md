# Ambient UI Iteration Journal

## Baseline (2026-07-19 16:10) — before any changes
Harness: FAIL ❌ (9)
- telem_not_top_hug (all viewports): telem top=16, need >=40
- dock_finger_draggable (mobile): window.__dockDraggable not set — chat can't be finger-dragged
- grip_touch_target (mobile): grip h=5, want >=28
- pips_no_overlap (android 360w): telem overlaps plate
pytest: 37 passing (green)

## Iter 1 (16:15) — telemetry top clearance
Added topMargin()=max(48, safe-area-top+44); top-docked pips + snap-zones now use it.
Harness: 9→5 fails. telem_not_top_hug PASS all viewports (telem top 16→48). pytest 37 green.

## Iter 2 (16:17) — chat dock FINGER-DRAG + grip touch target (P0 #1)
Grip: 5px→34px tall transparent drag zone, visible pill via ::before (grows on active).
Added setupDockDrag(): touch/mouse drag on grip fluidly resizes sheet; snaps to
collapsed/44%/70%/full; no-move = tap→cycle (backward compat). Sets window.__dockDraggable.
Harness: 5→1 fails. dock_finger_draggable + grip_touch_target PASS. pytest 37 green.
Remaining: android360 telem/plate overlap.
