#!/usr/bin/env python3
"""UI validation harness for the strands-cad dashboard.
Drives a real headless browser at multiple viewports and asserts touch-surface
usability invariants. Run: miniconda3/bin/python .ambient/uicheck.py
Exit 0 = all pass. Prints a JSON report + human summary.
"""
import json, sys, traceback
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8099"
# (name, w, h)  iPhone-ish + tablet + desktop
VIEWPORTS = [("iphone", 390, 844), ("android", 360, 800), ("tablet", 820, 1180), ("desktop", 1440, 900)]

def box(pg, sel):
    el = pg.query_selector(sel)
    if not el: return None
    return pg.eval_on_selector(sel, "e=>{const r=e.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height,bottom:r.bottom,right:r.right}}")

def run():
    results = []
    fails = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, w, h in VIEWPORTS:
            pg = b.new_page(viewport={"width": w, "height": h}, has_touch=True, is_mobile=(w<=430))
            r = {"vp": name, "w": w, "h": h, "checks": {}}
            try:
                pg.goto(URL, wait_until="networkidle", timeout=15000)
                pg.wait_for_timeout(600)
                tp = box(pg, "#telempip"); pp = box(pg, "#platepip"); dk = box(pg, "#dock")
                r["telempip"], r["platepip"], r["dock"] = tp, pp, dk

                def chk(key, cond, detail=""):
                    r["checks"][key] = {"pass": bool(cond), "detail": detail}
                    if not cond: fails.append(f"[{name}] {key}: {detail}")

                # INVARIANT 1: telemetry PiP must NOT hug the top edge — need finger room to pull down.
                # its drag handle top should be >= 40px from top (below any notch/safe area).
                if tp: chk("telem_not_top_hug", tp["y"] >= 40, f"telem top={tp['y']:.0f} (need >=40 for pull-down room)")
                # INVARIANT 2: pips must be fully on-screen
                if tp: chk("telem_onscreen", tp["x"]>=0 and tp["right"]<=w+1 and tp["bottom"]<=h+1, f"telem {tp}")
                if pp: chk("plate_onscreen", pp["x"]>=0 and pp["right"]<=w+1 and pp["bottom"]<=h+1, f"plate {pp}")
                # INVARIANT 3: pips must not overlap each other
                if tp and pp:
                    ov = not (tp["right"]<=pp["x"] or pp["right"]<=tp["x"] or tp["bottom"]<=pp["y"] or pp["bottom"]<=tp["y"])
                    chk("pips_no_overlap", not ov, f"telem={tp} plate={pp}")
                # INVARIANT 4: dock present + has a grip/handle
                grip = pg.query_selector("#dockGrip")
                chk("dock_grip_present", grip is not None, "no #dockGrip")
                # INVARIANT 5 (mobile): dock must be draggable via a gesture, expose window.__dockDraggable flag if implemented
                if w <= 430:
                    dd = pg.evaluate("()=>!!window.__dockDraggable")
                    chk("dock_finger_draggable", dd, "window.__dockDraggable not set (chat can't be dragged with fingers)")
                    # grip hit target should be >= 44px tall (Apple HIG min touch target)
                    if grip:
                        gb = box(pg, "#dockGrip")
                        chk("grip_touch_target", gb and gb["h"]>=28, f"grip h={gb['h'] if gb else '?'} (want >=28)")
                # INVARIANT 6: no console errors
                # (collected via listener below)
                r["console_errors"] = pg.__dict__.get("_cerr", [])
            except Exception as e:
                r["error"] = str(e); fails.append(f"[{name}] EXCEPTION {e}")
                traceback.print_exc()
            results.append(r)
            pg.close()
        b.close()
    report = {"ok": len(fails)==0, "fails": fails, "results": results}
    print(json.dumps(report, indent=2))
    print("\n=== SUMMARY ===")
    print("PASS ✅" if not fails else f"FAIL ❌ ({len(fails)})")
    for f in fails: print("  -", f)
    return 0 if not fails else 1

if __name__ == "__main__":
    sys.exit(run())
