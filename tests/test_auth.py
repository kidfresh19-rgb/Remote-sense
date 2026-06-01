"""No-DB tests for the stdlib HS256 JWT verification (Phase 7, R-5): valid tokens decode to a
Principal; expired, tampered, wrong-algorithm, audience-mismatch, and malformed tokens are
rejected; unknown role names are dropped."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from rs_core.rbac import Role

from services.api.auth import AuthError, decode_principal

_SECRET = "s3cret"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_token(payload: dict, *, secret: str = _SECRET, alg: str = "HS256") -> str:
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    sig = (
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if alg == "HS256"
        else b"x"
    )
    return f"{header}.{body}.{_b64(sig)}"


def test_decode_valid_token() -> None:
    token = _make_token({"sub": "alice", "roles": ["analyst", "viewer"], "exp": time.time() + 60})
    principal = decode_principal(token, secret=_SECRET)
    assert principal.subject == "alice"
    assert principal.roles == frozenset({Role.ANALYST, Role.VIEWER})


def test_unknown_roles_are_dropped() -> None:
    token = _make_token({"sub": "bob", "roles": ["analyst", "superuser"]})
    assert decode_principal(token, secret=_SECRET).roles == frozenset({Role.ANALYST})


def test_expired_token_rejected() -> None:
    token = _make_token({"sub": "alice", "exp": time.time() - 1})
    with pytest.raises(AuthError, match="expired"):
        decode_principal(token, secret=_SECRET)


def test_bad_signature_rejected() -> None:
    token = _make_token({"sub": "alice"})
    with pytest.raises(AuthError, match="signature"):
        decode_principal(token, secret="wrong-secret")


def test_none_algorithm_rejected() -> None:
    token = _make_token({"sub": "alice"}, alg="none")
    with pytest.raises(AuthError, match="algorithm"):
        decode_principal(token, secret=_SECRET)


def test_missing_subject_rejected() -> None:
    token = _make_token({"roles": ["viewer"]})
    with pytest.raises(AuthError, match="subject"):
        decode_principal(token, secret=_SECRET)


def test_audience_mismatch_rejected() -> None:
    token = _make_token({"sub": "alice", "aud": "other"})
    with pytest.raises(AuthError, match="audience"):
        decode_principal(token, secret=_SECRET, audience="expected")


def test_malformed_token_rejected() -> None:
    with pytest.raises(AuthError, match="malformed"):
        decode_principal("not-a-jwt", secret=_SECRET)
