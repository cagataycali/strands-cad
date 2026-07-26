"""Full WebAuthn ceremony against a software authenticator.

The unit tests in test_auth_hardening.py check _expected_origins() in isolation;
this drives the real enroll → login path through py_webauthn and then replays a
cross-origin assertion, which is the attack the origin check exists to stop.

Needs `soft-webauthn` (the [dev] extra) — skipped if absent.
"""
import base64
import importlib

import pytest

pytest.importorskip("webauthn")
SoftWebauthnDevice = pytest.importorskip("soft_webauthn").SoftWebauthnDevice

HOST = "printer.local:8099"
GOOD_ORIGIN = "https://printer.local:8099"
EVIL_ORIGIN = "https://attacker.example"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "==")


class Req:
    """Stand-in for starlette Request."""
    class _U:
        scheme = "https"

    def __init__(self, **headers):
        self.headers = {"host": HOST, **headers}
        self.cookies, self.query_params, self.url = {}, {}, self._U()


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "auth.json"))
    monkeypatch.setenv("STRANDS_CAD_AUTH_ENABLED", "true")
    monkeypatch.delenv("STRANDS_CAD_AUTH_RP_ID", raising=False)
    monkeypatch.delenv("STRANDS_CAD_AUTH_ORIGIN", raising=False)
    from strands_cad.dashboard import auth as _a
    return importlib.reload(_a)


def _enroll(auth, dev, req):
    begin = auth.begin_registration(req, label="test key")
    o = begin["options"]
    att = dev.create({"publicKey": {
        "rp": o["rp"],
        "user": {"id": unb64u(o["user"]["id"]), "name": o["user"]["name"],
                 "displayName": o["user"]["displayName"]},
        "challenge": unb64u(o["challenge"]),
        "pubKeyCredParams": o["pubKeyCredParams"]}}, GOOD_ORIGIN)
    cred = {"id": b64u(att["rawId"]), "rawId": b64u(att["rawId"]), "type": "public-key",
            "response": {"clientDataJSON": b64u(att["response"]["clientDataJSON"]),
                         "attestationObject": b64u(att["response"]["attestationObject"])}}
    return auth.finish_registration(req, begin["challenge_id"], cred)


def _assert_login(auth, dev, req, origin):
    """Run a login ceremony, signing clientData for `origin`."""
    begin = auth.begin_authentication(req)
    o = begin["options"]
    asr = dev.get({"publicKey": {
        "challenge": unb64u(o["challenge"]), "rpId": o["rpId"],
        "allowCredentials": [{"id": unb64u(c["id"]), "type": "public-key"}
                             for c in o.get("allowCredentials", [])]}}, origin)
    uh = asr["response"].get("userHandle")
    cred = {"id": b64u(asr["rawId"]), "rawId": b64u(asr["rawId"]), "type": "public-key",
            "response": {"clientDataJSON": b64u(asr["response"]["clientDataJSON"]),
                         "authenticatorData": b64u(asr["response"]["authenticatorData"]),
                         "signature": b64u(asr["response"]["signature"]),
                         "userHandle": b64u(uh) if uh else None}}
    return auth.finish_authentication(req, begin["challenge_id"], cred)


def test_enroll_then_login_succeeds(auth):
    """The happy path must still work with a list of expected origins."""
    dev, req = SoftWebauthnDevice(), Req()
    reg = _enroll(auth, dev, req)
    assert reg["ok"] and len(auth._load()["credentials"]) == 1

    res = _assert_login(auth, dev, req, GOOD_ORIGIN)
    assert res["ok"]
    claims = auth.verify_token(res["token"])
    assert claims["sub"] == reg["credential_id"] and claims["scope"] == "session"


def test_cross_origin_assertion_is_rejected(auth):
    """An assertion signed by attacker.example must not unlock the printer.

    The attacker also sends `Origin: https://attacker.example` — that header was
    what the old _derive_origin() echoed back as the expected value, making the
    comparison vacuous.
    """
    dev, req = SoftWebauthnDevice(), Req()
    _enroll(auth, dev, req)
    with pytest.raises(Exception) as e:
        _assert_login(auth, dev, Req(origin=EVIL_ORIGIN), EVIL_ORIGIN)
    assert "origin" in str(e.value).lower()


def test_replayed_challenge_is_rejected(auth):
    """Challenges are single-use: a captured assertion can't be replayed."""
    dev, req = SoftWebauthnDevice(), Req()
    _enroll(auth, dev, req)
    begin = auth.begin_authentication(req)
    o = begin["options"]
    asr = dev.get({"publicKey": {
        "challenge": unb64u(o["challenge"]), "rpId": o["rpId"],
        "allowCredentials": [{"id": unb64u(c["id"]), "type": "public-key"}
                             for c in o.get("allowCredentials", [])]}}, GOOD_ORIGIN)
    cred = {"id": b64u(asr["rawId"]), "rawId": b64u(asr["rawId"]), "type": "public-key",
            "response": {"clientDataJSON": b64u(asr["response"]["clientDataJSON"]),
                         "authenticatorData": b64u(asr["response"]["authenticatorData"]),
                         "signature": b64u(asr["response"]["signature"]),
                         "userHandle": None}}
    assert auth.finish_authentication(req, begin["challenge_id"], cred)["ok"]
    with pytest.raises(auth.HTTPException) as e:      # same challenge_id again
        auth.finish_authentication(req, begin["challenge_id"], cred)
    assert e.value.status_code == 400


def test_unknown_credential_rejected(auth):
    """A passkey from a different device can't log in."""
    dev, req = SoftWebauthnDevice(), Req()
    _enroll(auth, dev, req)
    other = SoftWebauthnDevice()
    other.cred_init(rp_id="printer.local", user_handle=b"x")
    begin = auth.begin_authentication(req)
    o = begin["options"]
    asr = other.get({"publicKey": {
        "challenge": unb64u(o["challenge"]), "rpId": "printer.local",
        "allowCredentials": [{"id": other.credential_id, "type": "public-key"}]}},
        GOOD_ORIGIN)
    cred = {"id": b64u(asr["rawId"]), "rawId": b64u(asr["rawId"]), "type": "public-key",
            "response": {"clientDataJSON": b64u(asr["response"]["clientDataJSON"]),
                         "authenticatorData": b64u(asr["response"]["authenticatorData"]),
                         "signature": b64u(asr["response"]["signature"]),
                         "userHandle": None}}
    with pytest.raises(auth.HTTPException) as e:
        auth.finish_authentication(req, begin["challenge_id"], cred)
    assert e.value.status_code == 404


def test_bootstrap_token_gates_first_enrollment(tmp_path, monkeypatch):
    """On an open LAN the first enroll must need the bootstrap secret."""
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "a.json"))
    monkeypatch.setenv("STRANDS_CAD_AUTH_BOOTSTRAP", "s3cret")
    from strands_cad.dashboard import auth as _a
    a = importlib.reload(_a)
    with pytest.raises(a.HTTPException) as e:
        a.begin_registration(Req(), bootstrap="wrong")
    assert e.value.status_code == 403
    assert a.begin_registration(Req(), bootstrap="s3cret")["challenge_id"]
