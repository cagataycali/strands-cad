#!/usr/bin/env python3
"""
🔐 strands-cad dashboard auth — WebAuthn (passkey) passwordless gate.

Why gate a 3D-printer dashboard?
--------------------------------
The dashboard can START PRINTS, MOVE THE TOOLHEAD, HEAT THE NOZZLE to 300 °C,
and watch a live camera inside your home/lab. Anyone who reaches the IP:port
could do the same — a real physical-safety + privacy problem. So the whole
dashboard sits behind **WebAuthn passkeys**: the same passwordless tech behind
Apple/Google/1Password/Windows-Hello. The private key never leaves your device
secure enclave; the server only stores public keys. Nothing to phish or leak.

Adapted from the scout-rover / neon-G1 robot dashboards (same author) — the
mechanism is generic robot-safety plumbing, reused here for a printer.

Flow
----
  1. FIRST RUN → /auth/status reports setup_required. Admin taps "Create
     passkey" (Touch ID / Face ID / YubiKey) → public key stored → sealed.
  2. LOGIN → challenge → device signs it → server verifies → short-lived JWT.
  3. GUARD → JWT in the Authorization header or the httponly session cookie on
     every non-public route. No token → 401.

Session tokens are NEVER accepted from the query string: URLs land in server
logs, browser history and Referer headers, so a `?token=` is a credential leak.
The one caller that cannot set a header — the `<img>` tag pulling the MJPEG
camera stream — uses a *ticket* instead: a 60-second, camera-only JWT minted by
`issue_ticket()` and rejected by `require_auth()`. Stealing one buys the thief a
minute of chamber video, not the printer.

Storage: one JSON file (STRANDS_CAD_AUTH_STORE, default ./.strands_cad_auth.json,
chmod 600). Self-contained, no DB. If that file exists but is unreadable we fail
CLOSED (see AuthStoreCorrupt) — silently regenerating it would drop every
enrolled passkey and re-open enrollment to whoever loads the page next.

Env knobs
---------
  STRANDS_CAD_AUTH_ENABLED   true/false (default true) — master switch
  STRANDS_CAD_AUTH_STORE     path       (default ./.strands_cad_auth.json)
  STRANDS_CAD_AUTH_RP_ID     rp id      (default derived from Host; must be a
                                          hostname/domain, NOT a raw IP)
  STRANDS_CAD_AUTH_RP_NAME   display    (default "strands-cad printer")
  STRANDS_CAD_AUTH_ORIGIN    origin(s)  (comma-separated; default derived from
                                          the Host header + connection scheme)
  STRANDS_CAD_AUTH_TOKEN_TTL seconds    (default 86400 = 24h)
  STRANDS_CAD_AUTH_BOOTSTRAP one-time secret to gate the FIRST enrollment
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt  # PyJWT
from fastapi import HTTPException, Request, WebSocket

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url


def _bool_env(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


AUTH_ENABLED = _bool_env("STRANDS_CAD_AUTH_ENABLED", True)
AUTH_STORE = Path(os.getenv("STRANDS_CAD_AUTH_STORE", "./.strands_cad_auth.json")).resolve()
RP_NAME = os.getenv("STRANDS_CAD_AUTH_RP_NAME", "strands-cad printer")
TOKEN_TTL = int(os.getenv("STRANDS_CAD_AUTH_TOKEN_TTL", "86400"))
BOOTSTRAP_TOKEN = os.getenv("STRANDS_CAD_AUTH_BOOTSTRAP", "").strip()
FORCE_RP_ID = os.getenv("STRANDS_CAD_AUTH_RP_ID", "").strip()
# Comma-separated allowlist. Kept as a list so a reverse-proxied deployment can
# name both the internal and external origin.
FORCE_ORIGINS = [o.strip().rstrip("/") for o in
                 os.getenv("STRANDS_CAD_AUTH_ORIGIN", "").split(",") if o.strip()]

# Reentrant: mutators hold the lock across read-modify-write and may call
# helpers (issue_token → _jwt_secret → _load) that take it again.
_lock = threading.RLock()

# Short-lived, single-purpose tokens for the one client that can't send an
# Authorization header (the MJPEG <img> stream).
TICKET_TTL = int(os.getenv("STRANDS_CAD_AUTH_TICKET_TTL", "60"))
TICKET_SCOPES = ("camera",)


class AuthStoreCorrupt(RuntimeError):
    """The store file exists but can't be parsed.

    Raised instead of regenerating: a fresh store means zero credentials, which
    flips the dashboard back to "setup_required" and lets the next visitor
    enroll their own passkey on your printer.
    """


def _default_store() -> Dict[str, Any]:
    return {"jwt_secret": secrets.token_urlsafe(48), "credentials": [], "created": time.time()}


def _read_locked() -> Dict[str, Any]:
    """Load the store, creating it on first run. Caller must hold _lock."""
    if AUTH_STORE.exists():
        raw = AUTH_STORE.read_text()
        try:
            store = json.loads(raw)
        except Exception as e:
            raise AuthStoreCorrupt(
                f"{AUTH_STORE} is not valid JSON ({e}). Refusing to reset it — "
                "that would delete your enrolled passkeys and re-open "
                "enrollment. Restore it from backup, or delete it deliberately "
                "to start over.") from e
        if not isinstance(store, dict) or not store.get("jwt_secret"):
            raise AuthStoreCorrupt(
                f"{AUTH_STORE} is missing jwt_secret — refusing to reset it. "
                "Restore from backup, or delete it deliberately to start over.")
        store.setdefault("credentials", [])
        return store
    store = _default_store()
    _save_locked(store)
    return store


def _load() -> Dict[str, Any]:
    with _lock:
        return _read_locked()


def _save_locked(store: Dict[str, Any]) -> None:
    """Atomically replace the store. Caller must hold _lock.

    Written to a sibling temp file first so a crash mid-write can't leave a
    truncated store behind — which _read_locked would (correctly) refuse to load.
    """
    tmp = AUTH_STORE.with_name(AUTH_STORE.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(store, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, AUTH_STORE)


def _save(store: Dict[str, Any]) -> None:
    with _lock:
        _save_locked(store)


def _update(mutator):
    """Run mutator(store) under the lock and persist the result."""
    with _lock:
        store = _read_locked()
        result = mutator(store)
        _save_locked(store)
        return result


def _jwt_secret() -> str:
    return _load()["jwt_secret"]


def has_credentials() -> bool:
    return len(_load().get("credentials", [])) > 0


def list_credentials() -> List[Dict[str, Any]]:
    return [{"id": c["id"], "name": c.get("name", "passkey"), "created": c.get("created")}
            for c in _load().get("credentials", [])]


def rename_credential(cred_id: str, name: str) -> Dict[str, Any]:
    def _do(store):
        match = next((c for c in store["credentials"] if c["id"] == cred_id), None)
        if not match:
            raise HTTPException(404, "credential not found")
        match["name"] = (name or "passkey").strip()[:64]
        return {"id": cred_id, "name": match["name"]}
    return _update(_do)


def delete_credential(cred_id: str) -> Dict[str, Any]:
    def _do(store):
        creds = store["credentials"]
        if not any(c["id"] == cred_id for c in creds):
            raise HTTPException(404, "credential not found")
        if len(creds) <= 1:
            raise HTTPException(409, "cannot remove the last passkey — enroll another first")
        store["credentials"] = [c for c in creds if c["id"] != cred_id]
        return {"ok": True, "removed": cred_id, "remaining": len(store["credentials"])}
    return _update(_do)


def _host_only(host: str) -> str:
    return host.split(":")[0]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def rpid_is_usable(host_only: str) -> bool:
    if host_only == "localhost":
        return True
    if _is_ip(host_only):
        return False
    return "." in host_only or host_only.endswith(".local") or host_only != ""


def _derive_rp_id(r) -> str:
    if FORCE_RP_ID:
        return FORCE_RP_ID
    return _host_only(r.headers.get("host", "localhost"))


def _expected_origins(r) -> List[str]:
    """Origins a WebAuthn assertion is allowed to come from.

    Deliberately does NOT consult the request's own `Origin` header: echoing it
    back as the expected value makes the check `origin == origin`, which always
    passes and lets any site drive the ceremony. We derive from `Host` (which the
    browser sets from the URL bar and CANNOT be forged cross-origin, unlike
    Origin) and offer both schemes, since the dashboard runs http on localhost
    and self-signed https on the LAN.
    """
    if FORCE_ORIGINS:
        return list(FORCE_ORIGINS)
    host = r.headers.get("host", "localhost:8099")
    fwd = (r.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if fwd in ("http", "https"):
        return [f"{fwd}://{host}"]
    return [f"https://{host}", f"http://{host}"]


def _display_origin(r) -> str:
    """First expected origin — for human-facing status/warnings only."""
    return _expected_origins(r)[0]


_challenges: Dict[str, Dict[str, Any]] = {}
_chal_lock = threading.Lock()
_CHAL_TTL = 300


def _stash_challenge(kind: str, challenge: bytes, extra: Optional[dict] = None) -> str:
    cid = secrets.token_urlsafe(16)
    with _chal_lock:
        now = time.time()
        for k in [k for k, v in _challenges.items() if now - v["t"] > _CHAL_TTL]:
            _challenges.pop(k, None)
        _challenges[cid] = {"kind": kind, "challenge": challenge, "t": now, "extra": extra or {}}
    return cid


def _pop_challenge(cid: str, kind: str) -> Dict[str, Any]:
    with _chal_lock:
        rec = _challenges.pop(cid, None)
    if not rec or rec["kind"] != kind:
        raise HTTPException(400, "invalid or expired challenge")
    if time.time() - rec["t"] > _CHAL_TTL:
        raise HTTPException(400, "challenge expired")
    return rec


def issue_token(subject: str, name: str = "") -> str:
    """Mint a full-access session JWT (scope "session")."""
    now = int(time.time())
    return jwt.encode({"sub": subject, "name": name, "scope": "session",
                       "iat": now, "exp": now + TOKEN_TTL},
                      _jwt_secret(), algorithm="HS256")


def issue_ticket(session: Dict[str, Any], scope: str = "camera") -> str:
    """Mint a short-lived, single-purpose token for URL-only clients.

    `require_auth` rejects these, so a ticket that leaks through a log or a
    Referer header can't touch /api/print, /api/control or the chat agent.
    """
    if scope not in TICKET_SCOPES:
        raise HTTPException(400, f"unknown ticket scope: {scope}")
    now = int(time.time())
    return jwt.encode({"sub": session.get("sub", "?"), "scope": scope,
                       "iat": now, "exp": now + TICKET_TTL},
                      _jwt_secret(), algorithm="HS256")


def verify_token(token: str, scope: str = "session") -> Dict[str, Any]:
    """Decode a JWT and require an exact scope match.

    Tokens minted before scopes existed carry no `scope` claim; they're treated
    as sessions so a running dashboard's logins survive the upgrade.
    """
    secret = _jwt_secret()  # outside the try: a corrupt store must surface as
    #                         AuthStoreCorrupt (503), not as "invalid session"
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "session expired")
    except Exception:
        raise HTTPException(401, "invalid session")
    if claims.get("scope", "session") != scope:
        raise HTTPException(403, f"token is not valid for {scope} access")
    return claims


def _extract_token(request: Request) -> Optional[str]:
    """Session token from the Authorization header or the httponly cookie.

    Query params are NOT consulted — see the module docstring. URL-bound clients
    use issue_ticket()/require_ticket() instead.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("strands_cad_session")


def require_auth(request: Request) -> Dict[str, Any]:
    if not AUTH_ENABLED:
        return {"sub": "auth-disabled"}
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "authentication required")
    return verify_token(token)


def require_ticket(request: Request, scope: str = "camera") -> Dict[str, Any]:
    """Authorize a URL-only client: a `?ticket=` of the right scope, or a
    normal session (header/cookie) when the caller can send one."""
    if not AUTH_ENABLED:
        return {"sub": "auth-disabled"}
    t = request.query_params.get("ticket")
    if t:
        return verify_token(t, scope=scope)
    return require_auth(request)


def require_ws_auth(ws: WebSocket) -> Optional[Dict[str, Any]]:
    """Authorize a WebSocket handshake. Returns None if the caller should close.

    Browsers can't set headers on `new WebSocket()`, so a scoped ticket in the
    query string is accepted here; a cookie or Authorization header (non-browser
    clients) works too.
    """
    if not AUTH_ENABLED:
        return {"sub": "auth-disabled"}
    ticket = ws.query_params.get("ticket")
    if ticket:
        try:
            return verify_token(ticket, scope="camera")
        except HTTPException:
            return None
    token = ws.cookies.get("strands_cad_session")
    if not token:
        auth = ws.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    try:
        return verify_token(token)
    except HTTPException:
        return None


def begin_registration(request: Request, label: str = "admin passkey", bootstrap: str = "") -> Dict[str, Any]:
    store = _load()
    first_time = len(store["credentials"]) == 0
    if first_time and BOOTSTRAP_TOKEN:
        if not secrets.compare_digest(bootstrap or "", BOOTSTRAP_TOKEN):
            raise HTTPException(403, "bootstrap token required for first enrollment")
    rp_id = _derive_rp_id(request)
    if not rpid_is_usable(rp_id):
        raise HTTPException(400,
            f"WebAuthn cannot use '{rp_id}' (a raw IP) as relying-party id. "
            "Open via a hostname (e.g. https://printer.local:PORT) or set "
            "STRANDS_CAD_AUTH_RP_ID to a domain.")
    user_id = store.get("user_id")
    if not user_id:
        user_id = _update(lambda s: s.setdefault(
            "user_id", bytes_to_base64url(secrets.token_bytes(16))))
    exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
               for c in store["credentials"]]
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME,
        user_id=base64url_to_bytes(user_id),
        user_name="cad-admin", user_display_name="strands-cad Admin",
        exclude_credentials=exclude or None,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    cid = _stash_challenge("reg", opts.challenge, {"label": label, "rp_id": rp_id})
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def finish_registration(request: Request, challenge_id: str, credential: dict) -> Dict[str, Any]:
    rec = _pop_challenge(challenge_id, "reg")
    rp_id = rec["extra"]["rp_id"]
    verification = verify_registration_response(
        credential=credential, expected_challenge=rec["challenge"],
        expected_rp_id=rp_id, expected_origin=_expected_origins(request))
    cred_id = bytes_to_base64url(verification.credential_id)
    label = rec["extra"].get("label", "passkey")

    def _do(store):
        if any(c["id"] == cred_id for c in store["credentials"]):
            raise HTTPException(409, "credential already registered")
        store["credentials"].append({
            "id": cred_id,
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "name": label,
            "created": time.time()})
    _update(_do)
    token = issue_token(cred_id, name=label)
    return {"ok": True, "token": token, "credential_id": cred_id}


def begin_authentication(request: Request) -> Dict[str, Any]:
    store = _load()
    if not store["credentials"]:
        raise HTTPException(400, "no credentials enrolled — setup required")
    rp_id = _derive_rp_id(request)
    if not rpid_is_usable(rp_id):
        raise HTTPException(400,
            f"WebAuthn cannot use '{rp_id}' (a raw IP). Use a hostname or set "
            "STRANDS_CAD_AUTH_RP_ID.")
    allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
             for c in store["credentials"]]
    opts = generate_authentication_options(
        rp_id=rp_id, allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED)
    cid = _stash_challenge("auth", opts.challenge, {"rp_id": rp_id})
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def finish_authentication(request: Request, challenge_id: str, credential: dict) -> Dict[str, Any]:
    rec = _pop_challenge(challenge_id, "auth")
    rp_id = rec["extra"]["rp_id"]
    store = _load()
    cred_id = credential.get("id") or credential.get("rawId")
    match = next((c for c in store["credentials"] if c["id"] == cred_id), None)
    if not match:
        raise HTTPException(404, "unknown credential")
    verification = verify_authentication_response(
        credential=credential, expected_challenge=rec["challenge"],
        expected_rp_id=rp_id, expected_origin=_expected_origins(request),
        credential_public_key=base64url_to_bytes(match["public_key"]),
        credential_current_sign_count=match.get("sign_count", 0),
        require_user_verification=False)

    def _do(s):
        # Re-find under the lock: a concurrent enroll/rename must not be lost.
        cur = next((c for c in s["credentials"] if c["id"] == cred_id), None)
        if cur is None:
            raise HTTPException(404, "unknown credential")
        cur["sign_count"] = verification.new_sign_count
        return cur.get("name", "passkey")
    name = _update(_do)
    return {"ok": True, "token": issue_token(cred_id, name=name),
            "credential_id": cred_id}


def service_token(name: str = "service") -> str:
    """Return a long-lived JWT for machine-to-machine callers (e.g. tiny.technology
    driving this printer as an endpoint device). Stable across restarts: cached in
    the auth store under 'service_tokens'.

    Lets a trusted remote service ride the same signed-JWT auth the middleware
    already checks, without an interactive passkey ceremony. Adapted from the
    neon-G1 dashboard (same author, same auth module lineage).
    """
    def _do(store):
        secret = store["jwt_secret"]
        toks = store.setdefault("service_tokens", {})
        if name in toks:
            # Reuse only if it still verifies under the CURRENT secret. A rotated
            # jwt_secret (or a re-created store) must mint a fresh token rather
            # than hand back one the middleware would reject.
            try:
                jwt.decode(toks[name], secret, algorithms=["HS256"],
                           options={"verify_exp": False})
                return toks[name]
            except Exception:
                pass
        now = int(time.time())
        payload = {"sub": f"service:{name}", "name": name, "scope": "session",
                   "iat": now, "exp": now + 3650 * 86400}  # ~10y
        toks[name] = jwt.encode(payload, secret, algorithm="HS256")
        return toks[name]
    return _update(_do)


def revoke_service_token(name: str) -> bool:
    """Drop a named service token. The JWT stays cryptographically valid until
    exp, so this is bookkeeping unless the secret is rotated -- callers wanting
    hard revocation must rotate jwt_secret (which also invalidates sessions).
    """
    def _do(store):
        toks = store.get("service_tokens") or {}
        if name not in toks:
            return False
        del toks[name]
        return True
    return _update(_do)


def status(request=None) -> Dict[str, Any]:
    try:
        store = _load()
    except AuthStoreCorrupt as e:
        # Stay locked, but tell the operator why instead of a bare 500. Never
        # setup_required — that would invite a stranger to enroll.
        return {"enabled": AUTH_ENABLED, "setup_required": False,
                "credentials": [], "bootstrap_required": False,
                "store_error": str(e),
                "warning": f"Auth store unreadable — dashboard is locked. {e}"}
    out = {
        "enabled": AUTH_ENABLED,
        "setup_required": len(store["credentials"]) == 0,
        "credentials": list_credentials(),
        "bootstrap_required": bool(BOOTSTRAP_TOKEN) and len(store["credentials"]) == 0,
    }
    if request is not None:
        out["expected_origins"] = _expected_origins(request)
        try:
            host = _host_only(request.headers.get("host", ""))
            # Scheme from the actual connection (uvicorn knows whether it
            # terminated TLS), not from a client-supplied header.
            scheme = getattr(getattr(request, "url", None), "scheme", "") or "http"
            fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
            secure = "https" in (scheme, fwd.lower()) or host == "localhost"
            usable = rpid_is_usable(host) if not FORCE_RP_ID else True
            out["rp_id"] = FORCE_RP_ID or host
            out["secure_context"] = secure
            out["rpid_usable"] = usable
            if not secure:
                out["warning"] = ("Not a secure context. WebAuthn needs HTTPS "
                                  "(or http://localhost). Set STRANDS_CAD_TLS=true.")
            elif not usable:
                out["warning"] = (f"'{host}' is a raw IP — WebAuthn needs a hostname "
                                  "as rpId. Use https://<name>:PORT or set "
                                  "STRANDS_CAD_AUTH_RP_ID.")
        except Exception:
            pass
    return out
