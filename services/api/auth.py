"""API auth (Phase 7, R-5): verify a bearer JWT and enforce a required permission per endpoint.

HS256 verification uses the standard library (no JWT dependency): the algorithm is pinned, the
signature is checked in constant time, and `exp` is enforced. Tokens are issued by the IdP /
gateway; the API only verifies them. `sub` is the subject; `roles` is a list of role names mapped
to permissions server-side (a permissions claim is never trusted)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from rs_core import Permission, Principal, Role, get_settings

_ROLE_VALUES = {role.value for role in Role}


class AuthError(Exception):
    """A token that fails verification. Surfaced to the caller as HTTP 401."""


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_principal(token: str, *, secret: str, audience: str = "") -> Principal:
    """Verify an HS256 JWT and return the Principal. Raises AuthError on any problem: malformed,
    unsupported/`none` algorithm, bad signature, expiry, audience mismatch, or missing subject."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("malformed token")
    header_b64, payload_b64, signature_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed token") from exc

    # Pin the algorithm: this rejects `none` and any asymmetric-key confusion outright.
    if header.get("alg") != "HS256":
        raise AuthError("unsupported algorithm")
    expected = hmac.new(
        secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, signature):
        raise AuthError("bad signature")

    exp = claims.get("exp")
    if exp is not None and time.time() >= float(exp):
        raise AuthError("token expired")
    if audience and claims.get("aud") != audience:
        raise AuthError("audience mismatch")

    subject = claims.get("sub")
    if not subject:
        raise AuthError("missing subject")
    roles = frozenset(Role(r) for r in claims.get("roles", []) if r in _ROLE_VALUES)
    return Principal(subject=str(subject), roles=roles)


async def get_principal(request: Request) -> Principal:
    """FastAPI dependency: verify the bearer token and return the Principal (401 on failure)."""
    settings = get_settings()
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    if not settings.jwt_secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "auth is not configured")
    try:
        return decode_principal(
            header.removeprefix("Bearer "),
            secret=settings.jwt_secret,
            audience=settings.jwt_audience,
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: allow the request only if the caller holds `permission`, else 403."""

    async def _dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires {permission.value}")
        return principal

    return _dependency
