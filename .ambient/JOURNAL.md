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

## Iter 3 (16:19) — fix android360 PiP overlap → ALL GREEN ✅
Mobile telemetry PiP width min(62vw,220)→min(52vw,176) so top-right + bottom-left
corners no longer collide on 360px screens.
Harness: 1→0 fails — PASS ✅ all 4 viewports. pytest 37 green.
All 9 baseline issues resolved. Moving to P2 polish.

## Iter 4 (16:22) — fix drag/click conflict + add behavioral drag invariant
BUG: grip kept onclick=toggleDock → after a drag, the synthetic click re-cycled the
sheet, corrupting state (drag-down did nothing). FIX: removed grip onclick; setupDockDrag
tap-vs-drag logic is now authoritative on mobile; desktop grip = click-toggle only.
Added harness invariant dock_drag_resizes: simulates real UP-drag (grows) + DOWN-drag
(shrinks) and asserts height changes. Verified: 371→792 up, 792→371 down.
Harness: PASS ✅ (now incl. behavioral drag). pytest 37 green.

## Iter 5 (16:25) — bigger pip control buttons on mobile (P1 touch targets)
Mobile: .pipbtns button 22px→32px, font 11→14, gap 4→6, piphead padding bumped.
Added harness invariant pip_btn_touch (>=30px). Harness PASS ✅, pytest 37 green.

## Iter 6 (16:27) — polish: grip discoverability + hardening invariants
Added harness invariants: no_h_overflow (all vp) + chat_input_tall (>=40px) — both PASS,
confirming layout has no hidden horizontal scroll and the chat field is always tappable.
Visual: collapsed sheet's grip pill now "breathes" (gentle width/opacity pulse) to invite
the pull-up gesture. Harness PASS ✅ (11 invariants now). pytest 37 green.

## Iter 7 (16:29) — behavioral guard: PiP corner-snap drag
Verified telemetry PiP drags top-right→bottom-left and snaps (corner=bl, x 198→16, y 48→101).
Added harness invariant pip_drag_snaps (asserts real drag moves the pip >40px). All 12
invariants PASS ✅ across 4 viewports. pytest 37 green. Both primary touch surfaces
(chat sheet resize + pip repositioning) now behaviorally regression-guarded.

## Iter 8 (16:31) — double-tap grip → jump full/default
Added double-tap (within 300ms) on the sheet grip to snap between full and default —
faster than dragging when you want max chat space. Single tap still cycles. Confirmed
chat transcript already auto-scrolls (addMsg sets scrollTop). Harness PASS ✅, pytest 37 green.

## 2026-07-19 17:xx — fix: "Invalid typed array length" on load (telemetry blocked)
Root cause: loadModels() auto-loaded MODELS[0] = cc_monogram.gcode. loadModel() set a
"STL only" label for non-STL but did NOT return — it fell through to STLLoader.parse()
on gcode bytes, reading a garbage triangle count (16.5B) → RangeError: Invalid typed
array length. The uncaught throw aborted the boot chain, so telemetry never rendered.
Fix (2 lines): (1) early-return in loadModel() for non-STL (dispose preview mesh + loadMeta only);
(2) auto-load the first STL model instead of MODELS[0]. Verified: 0 console errors,
telemetry visible, preview shows cc_monogram.stl. uicheck PASS, 36 pass/1 skip.
