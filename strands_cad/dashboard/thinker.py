#!/usr/bin/env python3
"""🧠 strands-cad slow-thinker — background reflective loop.

Every THINKER_INTERVAL seconds (default 60s), this loop:
  1. Reads live printer state (gcode_state, progress, temps, AMS, camera)
  2. Reads recent Telegram history (if a default chat is set)
  3. Spawns/refreshes a Strands agent with the "printer cockpit thinker" persona
  4. Lets the agent decide ONE useful background action:
        - send a chamber snapshot + progress caption to Telegram
        - alert on problems (thermal runaway, stall, spaghetti, filament runout)
        - log a milestone (layer % crossed, print finished)
        - or NOOP if nothing's worth doing

Goals:
  • Keep the operator (Cagatay) IN THE LOOP on long prints via Telegram
  • Catch slow problems pure event loops miss (temp drift, stalled progress)
  • Celebrate milestones + finished prints with a photo

Anti-goals:
  • Don't spam Telegram — at most ONE outbound per cycle
  • Never pause/stop a print autonomously (safety: operator decides)

Env knobs:
    THINKER_INTERVAL          seconds between cycles (default 60)
    THINKER_DISABLED          "1" to no-op the loop (debugging)
    THINKER_TELEGRAM_CHAT_ID  if set, thinker can send updates here
    SCOUT_THINKER_DRIVE       "1" to allow proactive photo sends (default 1;
                              0 = observe-only, alert on problems only)
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

log = logging.getLogger("strands_cad.dashboard.thinker")

_state = {"thread": None, "running": False, "agent": None, "cycles": 0,
          "last_snapshot_ts": 0.0, "last_progress": None, "last_state": None}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _interval() -> int:
    try:
        return int(os.getenv("THINKER_INTERVAL", "60"))
    except Exception:
        return 60


def _tg_chat() -> str:
    return (os.getenv("THINKER_TELEGRAM_CHAT_ID")
            or os.getenv("TELEGRAM_CHAT_ID")
            or os.getenv("SCOUT_TELEGRAM_DEFAULT_CHAT_ID", ""))


def _allow_send() -> bool:
    return os.getenv("SCOUT_THINKER_DRIVE", "1") not in ("0", "false", "no")


def _printer_snapshot() -> dict:
    """Cached printer telemetry (no blocking MQTT roundtrip in the path)."""
    try:
        from strands_cad.dashboard import printer as _printer, config_store
        cfg = config_store.load()
        ip, access, serial = cfg.get("ip"), cfg.get("access_code"), cfg.get("serial")
        p = _printer.get_printer(ip, access, serial)
        return p.snapshot()
    except Exception as e:
        log.debug(f"snapshot failed: {e}")
        return {}


def _thinker_prompt() -> str:
    s = _printer_snapshot()
    temps = s.get("temps") or {}
    chat = _tg_chat()
    interval = _interval()

    tg_clause = (
        f"  • You MAY send ONE update to Telegram chat `{chat}` if worth it: "
        f"a status photo on a long print, a milestone, or a PROBLEM alert. "
        f"Use telegram_send_snapshot(caption='...') for a chamber photo, or "
        f"telegram_notify(text='...') for text-only. Skip if nothing's "
        f"interesting — silence is fine."
        if (chat and _allow_send()) else
        "  • Telegram sends disabled this cycle — only alert on real problems."
    )

    extra = f"""
## 🖨️ COCKPIT THINKER — autonomous print watchdog (printer: Bambu Lab X2D)
You are the strands-cad printer cockpit's slow-thinker. You run every {interval}s
in the BACKGROUND. Your job: keep the operator (Cagatay) informed about the
printer and catch slow problems that event-driven code misses.

## Live printer snapshot ({_now()})
- State: {s.get('gcode_state') or '?'}   Progress: {s.get('progress')}%
- Job: {s.get('subtask_name') or 'idle'}
- Layer: {s.get('layer')}/{s.get('total_layers')}   Remaining: {s.get('remaining_min')} min
- Nozzle: {temps.get('nozzle')}°C (→{temps.get('nozzle_target')}°C)  Bed: {temps.get('bed')}°C (→{temps.get('bed_target')}°C)  Chamber: {temps.get('chamber')}°C
- SD/USB storage: {s.get('sdcard')}   WiFi: {s.get('wifi_signal')}   Connected: {s.get('connected')}
- Snapshot age: {s.get('age_seconds')}s

## Your job EVERY CYCLE (be USEFUL, not noisy):
1. **ASSESS** — what changed since last cycle? Is a print running, finishing,
   idle, or in trouble?
2. **ACT** — pick ONE meaningful action:
   • **REPORT**: on an active print, send a chamber snapshot every 3-4 cycles
     with a short caption ("🖨️ 42% · layer 88/210 · nozzle 218°C · looking clean").
   • **MILESTONE**: print just finished → send a celebration photo. Crossed
     25/50/75% → optional progress ping.
   • **ALERT** (always allowed, even if sends "disabled"): nozzle/bed temp far
     from target mid-print, progress stalled for 2+ cycles, gcode_state=FAILED,
     filament runout, storage missing → telegram_notify a clear warning.
   • **NOOP**: printer idle and nothing changed → do nothing.

## Heuristics
- Idle + no job → NOOP (don't send "still idle" spam).
- Active print + haven't sent a photo in 3+ cycles → send a status snapshot.
- gcode_state FAILED/PAUSE unexpectedly → ALERT immediately.
- Nozzle temp >15°C off target while RUNNING → ALERT (possible thermal issue).
- Progress unchanged for 2+ cycles while RUNNING → ALERT (possible stall).
- Print reached 100% / state FINISH → send a "done!" photo, then NOOP after.

## Hard rules (safety)
- NEVER pause/stop/resume the print autonomously. Only the operator decides.
- At most ONE Telegram outbound per cycle.
{tg_clause}

## Telegram style
- Captions SHORT (1 line, ≤80 chars), a little playful, printer-personality.
  Examples: "🖨️ 42% · layer 88/210 · clean lines", "✅ print done — nice!",
  "⚠️ nozzle 195°C but target 220°C — check the hotend", "🟠 progress stuck
  at 63% for 2min — possible stall".
- Use emojis. Be a helpful cockpit, not a status-bot.

## Output format
- Your final text response is logged (not user-shown). Be terse.
- Format: `ACTION: <what you did>  |  WHY: <one-liner reason>`
- Examples:
  - `ACTION: tg snapshot  |  WHY: 42% print, 4 cycles since last photo`
  - `ACTION: tg alert  |  WHY: nozzle 30°C below target mid-print`
  - `ACTION: NOOP  |  WHY: printer idle, nothing changed`
"""
    return extra


def _build_agent():
    from strands import Agent, tool
    from strands_tools import shell
    from strands_cad.dashboard import telegram as _tg

    @tool
    def telegram_notify(text: str) -> dict:
        """Send a Telegram text message to the operator's chat."""
        return _tg.notify(text)

    @tool
    def telegram_send_snapshot(caption: str = "🖨️ chamber") -> dict:
        """Grab a chamber-camera frame and send it over Telegram with a caption."""
        return _tg.send_camera_snapshot(caption)

    return Agent(
        tools=[telegram_notify, telegram_send_snapshot, shell],
        system_prompt=_thinker_prompt(),
    )


def _cycle(agent) -> None:
    """One reflection cycle — refresh prompt, clear history, run."""
    t0 = time.time()
    _state["cycles"] += 1
    n = _state["cycles"]
    log.info(f"🧠 thinker cycle #{n} start")

    try:
        agent.messages.clear()
    except Exception:
        pass
    try:
        agent.system_prompt = _thinker_prompt()
    except Exception as e:
        log.warning(f"prompt refresh failed: {e}")

    # give the agent a hint about recency to help the "N cycles since photo" logic
    since = int(time.time() - _state["last_snapshot_ts"]) if _state["last_snapshot_ts"] else 9999
    user_turn = (
        f"Run one background watchdog cycle. Seconds since your last Telegram "
        f"photo: {since}. Decide what's worth doing per your persona rules and "
        f"execute (at most one Telegram send). Be terse."
    )
    try:
        result = agent(user_turn)
        text = str(result)[:600]
        log.info(f"🧠 #{n} → {text[:240]}")
        # track whether a photo was likely sent (heuristic: caption/snapshot keyword)
        if "snapshot" in text.lower() or "photo" in text.lower():
            _state["last_snapshot_ts"] = time.time()
    except Exception as e:
        log.warning(f"cycle error: {type(e).__name__}: {e}")
        log.debug(traceback.format_exc())

    log.info(f"🧠 cycle #{n} done in {time.time()-t0:.1f}s")


def _loop() -> None:
    interval = _interval()
    log.info(f"🧠 slow-thinker starting (interval={interval}s, "
             f"send={'on' if _allow_send() else 'alerts-only'}, chat={_tg_chat() or 'unset'})")
    try:
        _state["agent"] = _build_agent()
        log.info("🧠 thinker agent built")
    except Exception as e:
        log.error(f"failed to build thinker agent: {e}")
        log.debug(traceback.format_exc())
        _state["running"] = False
        return

    # initial settle
    for _ in range(min(interval, 15)):
        if not _state["running"]:
            return
        time.sleep(1)

    while _state["running"]:
        try:
            _cycle(_state["agent"])
        except Exception as e:
            log.warning(f"outer cycle error: {e}")
        for _ in range(_interval()):
            if not _state["running"]:
                break
            time.sleep(1)
    log.info("🧠 thinker stopped")


def status() -> dict:
    return {"running": _state["running"], "cycles": _state["cycles"],
            "interval": _interval(), "chat_id": _tg_chat(),
            "send_enabled": _allow_send()}


def start() -> dict:
    if os.getenv("THINKER_DISABLED", "").lower() in ("1", "true", "yes"):
        log.info("THINKER_DISABLED set — not starting thinker")
        return {"ok": False, "disabled": True}
    if _state["running"]:
        return {"ok": True, "already": True}
    _state["running"] = True
    _state["thread"] = threading.Thread(target=_loop, daemon=True, name="cad-thinker")
    _state["thread"].start()
    return {"ok": True, "running": True}


def stop() -> dict:
    _state["running"] = False
    return {"ok": True}


def main() -> None:
    """Standalone entrypoint: run the slow-thinker loop as its own process
    (used by systemd unit `strands-cad-thinker` and the docker `thinker` svc).
    Blocks forever; the loop runs in a daemon thread so we just idle-wait."""
    import time as _t
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        from strands_cad.dashboard import config_store as _cfg
        _cfg.load()
    except Exception as e:  # pragma: no cover
        log.warning(f"config load failed: {e}")
    r = start()
    log.info(f"🧠 thinker standalone: {r}")
    if not r.get("ok"):
        raise SystemExit(1 if not r.get("disabled") else 0)
    try:
        while _state.get("running"):
            _t.sleep(1)
    except KeyboardInterrupt:
        stop()
        log.info("🧠 thinker interrupted — exiting")


if __name__ == "__main__":
    main()
