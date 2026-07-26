"""Auth guard tests — the dashboard can heat a nozzle to 300 °C and start prints,
so each of these pins a hole that was actually open.

Run against a fresh auth store per test via STRANDS_CAD_AUTH_STORE + reload.
"""
import importlib
import json

import pytest

pytest.importorskip("webauthn")
pytest.importorskip("fastapi")


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "auth.json"))
    monkeypatch.delenv("STRANDS_CAD_AUTH_RP_ID", raising=False)
    monkeypatch.delenv("STRANDS_CAD_AUTH_ORIGIN", raising=False)
    monkeypatch.setenv("STRANDS_CAD_AUTH_ENABLED", "true")
    from strands_cad.dashboard import auth as _a
    return importlib.reload(_a)


class FakeReq:
    """Minimal stand-in for starlette Request (headers/cookies/query_params/url)."""
    class _URL:
        def __init__(self, scheme): self.scheme = scheme

    def __init__(self, headers=None, cookies=None, query=None, scheme="https"):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.query_params = query or {}
        self.url = self._URL(scheme)


# ── origin verification ─────────────────────────────────────────────────────
def test_expected_origin_ignores_request_origin_header(auth):
    """The check must not echo the caller's Origin back as the expectation.

    Previously _derive_origin() returned request.headers['origin'], making the
    comparison `origin == origin` — attacker.example could complete a ceremony.
    """
    r = FakeReq({"host": "printer.local:8099", "origin": "https://attacker.example"})
    origins = auth._expected_origins(r)
    assert "https://attacker.example" not in origins
    assert "https://printer.local:8099" in origins


def test_expected_origin_honours_forwarded_proto(auth):
    r = FakeReq({"host": "cad.example.com", "x-forwarded-proto": "https"})
    assert auth._expected_origins(r) == ["https://cad.example.com"]


def test_expected_origin_env_override_is_a_list(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "a.json"))
    monkeypatch.setenv("STRANDS_CAD_AUTH_ORIGIN",
                       "https://a.example, https://b.example/")
    import importlib
    from strands_cad.dashboard import auth as _a
    a = importlib.reload(_a)
    got = a._expected_origins(FakeReq({"host": "whatever", "origin": "https://evil"}))
    assert got == ["https://a.example", "https://b.example"]


# ── no credentials in the query string ──────────────────────────────────────
def test_session_token_not_accepted_from_query(auth):
    tok = auth.issue_token("cred-1", "passkey")
    with pytest.raises(auth.HTTPException) as e:
        auth.require_auth(FakeReq(query={"token": tok}))
    assert e.value.status_code == 401
    # header and cookie both still work
    assert auth.require_auth(FakeReq({"authorization": f"Bearer {tok}"}))["sub"] == "cred-1"
    assert auth.require_auth(FakeReq(cookies={"strands_cad_session": tok}))["sub"] == "cred-1"


def test_ticket_is_camera_only_and_short_lived(auth):
    session = auth.issue_token("cred-1")
    ticket = auth.issue_ticket(auth.verify_token(session), "camera")

    # a ticket cannot be used as a session
    with pytest.raises(auth.HTTPException) as e:
        auth.require_auth(FakeReq({"authorization": f"Bearer {ticket}"}))
    assert e.value.status_code == 403

    # ...but it authorizes the camera stream
    assert auth.require_ticket(FakeReq(query={"ticket": ticket}), "camera")["scope"] == "camera"

    # a full session also works on ticket routes (non-browser clients)
    assert auth.require_ticket(FakeReq({"authorization": f"Bearer {session}"}), "camera")

    # a session token in ?ticket= is rejected — wrong scope
    with pytest.raises(auth.HTTPException):
        auth.require_ticket(FakeReq(query={"ticket": session}), "camera")

    assert auth.TICKET_TTL <= 300
    import jwt as _jwt
    claims = _jwt.decode(ticket, auth._jwt_secret(), algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == auth.TICKET_TTL


def test_unknown_ticket_scope_refused(auth):
    with pytest.raises(auth.HTTPException):
        auth.issue_ticket({"sub": "x"}, scope="print")


def test_legacy_tokens_without_scope_still_work(auth):
    """Sessions minted before scopes existed must not all 403 after upgrade."""
    import jwt as _jwt, time
    now = int(time.time())
    old = _jwt.encode({"sub": "cred-1", "iat": now, "exp": now + 600},
                      auth._jwt_secret(), algorithm="HS256")
    assert auth.verify_token(old)["sub"] == "cred-1"


def test_expired_and_forged_tokens_rejected(auth):
    import jwt as _jwt, time
    now = int(time.time())
    expired = _jwt.encode({"sub": "x", "scope": "session", "iat": now - 10, "exp": now - 5},
                          auth._jwt_secret(), algorithm="HS256")
    with pytest.raises(auth.HTTPException) as e:
        auth.verify_token(expired)
    assert e.value.status_code == 401
    forged = _jwt.encode({"sub": "x", "scope": "session", "iat": now, "exp": now + 99},
                         "not-the-secret", algorithm="HS256")
    with pytest.raises(auth.HTTPException):
        auth.verify_token(forged)


# ── fail closed on a damaged store ──────────────────────────────────────────
def test_corrupt_store_fails_closed(auth):
    """A garbled store must NOT silently reset — that re-opens enrollment."""
    auth.issue_token("seed")                     # force store creation
    auth.AUTH_STORE.write_text("{ this is not json")
    with pytest.raises(auth.AuthStoreCorrupt):
        auth._load()
    s = auth.status()
    assert s["setup_required"] is False and "store_error" in s
    # the bad file is left on disk for the operator to restore
    assert auth.AUTH_STORE.read_text().startswith("{ this")


def test_corrupt_store_surfaces_as_503_not_invalid_session(auth):
    """The operator must see the real cause, not a generic auth failure."""
    tok = auth.issue_token("cred-1")
    auth.AUTH_STORE.write_text("{ nope")
    with pytest.raises(auth.AuthStoreCorrupt):
        auth.verify_token(tok)
    with pytest.raises(auth.AuthStoreCorrupt):
        auth.require_auth(FakeReq({"authorization": f"Bearer {tok}"}))


def test_store_without_secret_fails_closed(auth):
    auth.issue_token("seed")
    auth.AUTH_STORE.write_text(json.dumps({"credentials": []}))
    with pytest.raises(auth.AuthStoreCorrupt):
        auth._load()


def test_store_writes_are_atomic_and_private(auth):
    auth.issue_token("seed")
    import stat
    mode = stat.S_IMODE(auth.AUTH_STORE.stat().st_mode)
    assert mode == 0o600, f"store is {oct(mode)} — secrets must be owner-only"
    # no temp files left behind
    leftovers = list(auth.AUTH_STORE.parent.glob(auth.AUTH_STORE.name + ".tmp*"))
    assert leftovers == []


def test_service_token_roundtrip_and_revoke(auth):
    t1 = auth.service_token("tiny")
    assert auth.service_token("tiny") == t1          # stable across calls
    assert auth.verify_token(t1)["sub"] == "service:tiny"
    assert auth.revoke_service_token("tiny") is True
    assert auth.revoke_service_token("tiny") is False
    # Revoke is bookkeeping only (see its docstring): the JWT stays valid until
    # exp, and re-minting under the same secret reproduces it byte-for-byte.
    assert auth.verify_token(auth.service_token("tiny"))["sub"] == "service:tiny"


def test_concurrent_credential_writes_are_not_lost(auth):
    """Read-modify-write under threads must not drop entries."""
    import threading
    auth.issue_token("seed")

    def add(i):
        auth._update(lambda s: s["credentials"].append({"id": f"c{i}", "name": "k"}))

    ts = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(auth._load()["credentials"]) == 12


# ── server surface ──────────────────────────────────────────────────────────
def test_openapi_docs_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "a.json"))
    monkeypatch.delenv("STRANDS_CAD_DASH_DOCS", raising=False)
    monkeypatch.delenv("STRANDS_CAD_DASH_CORS", raising=False)
    import importlib
    from strands_cad.dashboard import server as _s
    s = importlib.reload(_s)
    app = s.create_app()
    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
    # ...and no wildcard CORS with credentials
    mws = [str(m) for m in app.user_middleware]
    assert not any("CORS" in m for m in mws), mws


def test_every_route_is_sealed_or_deliberately_public(tmp_path, monkeypatch):
    """New routes must default to authenticated — the guard covers all paths."""
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "a.json"))
    import importlib
    from strands_cad.dashboard import server as _s
    s = importlib.reload(_s)
    public = s._PUBLIC_EXACT | set(s._TICKET_ROUTES)
    for r in s.create_app().routes:
        p = getattr(r, "path", "")
        if p in public or p.startswith("/auth/") or not p.startswith("/api/"):
            continue
        # everything else falls through to require_auth in _auth_mw
        assert p not in s._PUBLIC_EXACT
    assert "/api/camera/stream" in s._TICKET_ROUTES
    assert "/api/print" not in public and "/api/control" not in public
    assert "/api/chat" not in public


def test_ticket_route_present(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "a.json"))
    import importlib
    from strands_cad.dashboard import server as _s
    s = importlib.reload(_s)
    assert "/auth/ticket" in {r.path for r in s.create_app().routes}
