#!/usr/bin/env python3
"""
📱 strands-cad dashboard — Telegram bridge.

Lets the printer cockpit notify you and take commands over Telegram:
  • notify(text, photo)         → push job/print/camera alerts to your chat
  • send_camera_snapshot()      → grab a chamber frame and send it
  • poll loop (optional)        → accept /status /snapshot /pause /stop /print
    commands from allowed users, routing them to the same dashboard actions.

Config keys (seeded from scout .env):
  telegram_bot_token, telegram_chat_id, telegram_allowed_users
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("strands_cad.dashboard.telegram")
_API = "https://api.telegram.org/bot{token}/{method}"
_poll = {"thread": None, "running": False, "offset": 0}


def _cfg():
    from strands_cad.dashboard import config_store
    c = config_store.load()
    return (c.get("telegram_bot_token", ""), c.get("telegram_chat_id", ""),
            c.get("telegram_allowed_users", ""))


def _call(method: str, data: Dict[str, Any] = None, files: Dict = None) -> Dict:
    token, _, _ = _cfg()
    if not token:
        return {"ok": False, "error": "telegram_bot_token not set"}
    url = _API.format(token=token, method=method)
    try:
        if files:
            # multipart for photo upload
            boundary = "----cadboundary" + str(int(time.time()))
            body = io.BytesIO()
            for k, v in (data or {}).items():
                body.write(f"--{boundary}\r\n".encode())
                body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (fname, fbytes, ctype) in files.items():
                body.write(f"--{boundary}\r\n".encode())
                body.write(f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode())
                body.write(f"Content-Type: {ctype}\r\n\r\n".encode())
                body.write(fbytes); body.write(b"\r\n")
            body.write(f"--{boundary}--\r\n".encode())
            req = urllib.request.Request(url, data=body.getvalue(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            payload = urllib.parse.urlencode(data or {}).encode()
            req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return {"ok": False, "error": body.get("description", str(e)),
                    "error_code": body.get("error_code")}
        except Exception:
            return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def notify(text: str, chat_id: str = "") -> Dict[str, Any]:
    """Send a text message to the default (or given) chat."""
    _, default_chat, _ = _cfg()
    chat = chat_id or default_chat
    if not chat:
        return {"ok": False, "error": "no chat_id"}
    r = _call("sendMessage", {"chat_id": chat, "text": text[:4000],
                              "parse_mode": "Markdown"})
    if not r.get("ok"):  # Markdown can 400 on unescaped chars → retry plain
        r = _call("sendMessage", {"chat_id": chat, "text": text[:4000]})
    return r


def send_photo(jpeg_bytes: bytes, caption: str = "", chat_id: str = "") -> Dict[str, Any]:
    _, default_chat, _ = _cfg()
    chat = chat_id or default_chat
    if not chat:
        return {"ok": False, "error": "no chat_id"}
    return _call("sendPhoto", {"chat_id": chat, "caption": caption[:900]},
                 files={"photo": ("snapshot.jpg", jpeg_bytes, "image/jpeg")})


def send_camera_snapshot(caption: str = "📷 chamber") -> Dict[str, Any]:
    from strands_cad.dashboard import camera as _camera, config_store
    ip = config_store.get("ip"); access = config_store.get("access_code")
    if not ip or not access:
        return {"ok": False, "error": "printer not configured"}
    cam = _camera.get_camera(ip, access)
    jpg = cam.latest() or _camera.placeholder_jpeg()
    return send_photo(jpg, caption)


def status() -> Dict[str, Any]:
    token, chat, allowed = _cfg()
    return {"configured": bool(token), "chat_id": chat,
            "allowed_users": allowed, "polling": _poll["running"]}


# ── optional command poll loop ──────────────────────────────────────────────
def _allowed(user: Dict) -> bool:
    _, _, allowed = _cfg()
    if not allowed:
        return True
    names = {a.strip() for a in allowed.split(",") if a.strip()}
    return (str(user.get("id")) in names or user.get("username", "") in names)


def _handle(text: str, chat_id: str):
    from strands_cad.dashboard import printer as _printer, config_store, chat_agent
    t = text.strip().lower()
    ip, access, serial = (config_store.get("ip"), config_store.get("access_code"),
                          config_store.get("serial"))
    if t in ("/status", "status"):
        p = _printer.get_printer(ip, access, serial); s = p.snapshot()
        notify(f"🖨️ *{s.get('gcode_state')}* · {s.get('progress')}%\n"
               f"nozzle {(s.get('temps') or {}).get('nozzle')}° bed {(s.get('temps') or {}).get('bed')}°\n"
               f"job: {s.get('subtask_name') or 'idle'}", chat_id)
    elif t in ("/snapshot", "/photo", "snapshot"):
        send_camera_snapshot()
    elif t in ("/pause", "/resume", "/stop"):
        p = _printer.get_printer(ip, access, serial); p.control(t.lstrip("/"))
        notify(f"sent {t}", chat_id)
    elif t.startswith("/print "):
        from strands_cad.dashboard import jobs
        name = text.split(" ", 1)[1].strip()
        jid = jobs.start_print(name)
        notify(f"🖨️ print job {jid} started for {name}", chat_id)
    elif t in ("/help", "help", "/start"):
        notify("Just talk to me in plain language — I'm the strands-cad "
               "printer agent (design → slice → print). Quick commands:\n"
               "/status · /snapshot · /pause · /resume · /stop · "
               "/print <file>", chat_id)
    elif t.startswith("/ask ") or t.startswith("/cad "):
        q = text.split(" ", 1)[1]
        r = chat_agent.ask(q)
        notify(r.get("reply") or ("⚠️ " + str(r.get("error"))), chat_id)
    else:
        # Anything that isn't a defined command → natural language to the agent.
        # Strip a leading unknown slash-command token if present (e.g. "/foo ...").
        q = text.strip()
        r = chat_agent.ask(q)
        notify(r.get("reply") or ("⚠️ " + str(r.get("error"))), chat_id)


def _loop():
    log.info("telegram poll loop started")
    while _poll["running"]:
        r = _call("getUpdates", {"offset": _poll["offset"], "timeout": 25})
        if not r.get("ok"):
            time.sleep(5); continue
        for upd in r.get("result", []):
            _poll["offset"] = upd["update_id"] + 1
            msg = upd.get("message") or {}
            user = msg.get("from") or {}
            text = msg.get("text") or ""
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if not text or not _allowed(user):
                continue
            try:
                _handle(text, chat_id)
            except Exception as e:
                log.warning(f"telegram handle error: {e}")
    log.info("telegram poll loop stopped")


def detect_chat_id() -> Dict[str, Any]:
    """Return the chat_id of the most recent person who messaged the bot.

    Use this if `chat not found`: message the bot once, then call this to learn
    your chat_id and save it into config.
    """
    r = _call("getUpdates", {"timeout": 1})
    if not r.get("ok"):
        return r
    ids = []
    for upd in r.get("result", []):
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            ids.append({"id": chat["id"], "username": (msg.get("from") or {}).get("username"),
                        "text": msg.get("text", "")[:40]})
    return {"ok": True, "recent_chats": ids}


def start_polling() -> Dict[str, Any]:
    token, _, _ = _cfg()
    if not token:
        return {"ok": False, "error": "telegram_bot_token not set"}
    if _poll["running"]:
        return {"ok": True, "already": True}
    _poll["running"] = True
    _poll["thread"] = threading.Thread(target=_loop, daemon=True, name="cad-telegram")
    _poll["thread"].start()
    return {"ok": True, "polling": True}


def stop_polling() -> Dict[str, Any]:
    _poll["running"] = False
    return {"ok": True}


def main() -> None:
    """Standalone entrypoint: run only the Telegram poll loop as its own process
    (systemd unit `strands-cad-telegram` / docker `telegram` svc). Blocks."""
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
    r = start_polling()
    log.info(f"📱 telegram standalone: {r}")
    if not r.get("ok"):
        raise SystemExit(1)
    try:
        while _poll.get("running"):
            _t.sleep(1)
    except KeyboardInterrupt:
        stop_polling()
        log.info("📱 telegram interrupted — exiting")


if __name__ == "__main__":
    main()
