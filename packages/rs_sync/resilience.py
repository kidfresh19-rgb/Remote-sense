"""Shared gateway-push resilience policy (L7). One place decides which push failures are worth a
retry and which are permanent, so the two `GatewayPort` adapters never drift (CLAUDE.md §2:
resilience lives in the adapter, centralized, never scattered).

The rule: transport/timeout errors and HTTP 429/5xx are transient (retry with backoff); any other
4xx (bad key, wrong URL, malformed body) is permanent and must fail fast - retrying a 401 four
times only delays a dead-letter the operator needs to see now."""

from __future__ import annotations

import re

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

# The broad catch set: every failure an adapter converts into a dead-letter `PushResult` rather than
# letting it crash the worker. Narrower than "any exception" on purpose - a bug in our own code
# should still surface, not masquerade as a delivery failure.
GATEWAY_PUSH_ERRORS = (httpx.TransportError, httpx.HTTPStatusError)


def is_retryable(exc: BaseException) -> bool:
    """True when re-attempting the push could plausibly succeed: a transport/timeout error, or a
    429 / 5xx from the gateway. A 4xx other than 429 is a permanent client error and returns
    False so tenacity stops immediately."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


# Tenacity predicate form, shared by both adapters' AsyncRetrying loops.
retry_transient_push = retry_if_exception(is_retryable)


def push_retrying(*, max_attempts: int, backoff: float) -> AsyncRetrying:
    """The shared retry loop for a single gateway request (CLAUDE.md §2: one resilience policy, not
    scattered). Stop after `max_attempts`; back off exponentially with **jitter** so the concurrent
    record POSTs of one farm don't synchronize their retries and hammer the gateway in lockstep;
    retry only transient failures (`is_retryable`) and re-raise the last error so the adapter can
    dead-letter it. `backoff=0` yields zero waits (used in tests for determinism)."""
    return AsyncRetrying(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(multiplier=backoff, max=10),
        retry=retry_transient_push,
    )


def _body_snippet(response: httpx.Response, *, limit: int = 120) -> str:
    """A short, single-line snippet of a failed response body, surfaced in the dead-letter and
    the workspace push modal. When the gateway returns an HTML error page (CDN 503, nginx default,
    ngrok tunnel error), we extract the page title or first heading rather than emitting raw markup
    — collapsed HTML is unreadable in a UI modal. For JSON/plain-text bodies the raw collapsed text
    is used. A body that cannot be read yields an empty string."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - body is best-effort diagnostics, never fatal
        return ""

    stripped = text.lstrip()
    if stripped.lower().startswith(("<!doctype html", "<html")):
        # Try to pull a human-readable title from the HTML.
        title_m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
        h1_m = re.search(r"<h1[^>]*>([^<]+)</h1>", text, re.IGNORECASE)
        label = title_m or h1_m
        if label:
            readable = " ".join(label.group(1).split())
            return f"HTML error page: {readable}"[:limit]
        return "HTML error page (no title found)"

    collapsed = " ".join(text.split())
    return collapsed[:limit] + "…" if len(collapsed) > limit else collapsed


def describe_push_error(exc: BaseException) -> str:
    """A compact, operator-facing reason for a dead-lettered push, surfaced in the outbox and the
    workspace push modal. Names the status code (so a 401 reads as an auth/key problem and a 404 as
    a wrong-URL problem) and appends a snippet of the gateway's response body when there is one,
    instead of a raw stack-style repr."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        reason = httpx.codes.get_reason_phrase(code) or "error"
        base = f"gateway returned HTTP {code} {reason}".rstrip()
        snippet = _body_snippet(exc.response)
        return f"{base}: {snippet}" if snippet else base
    if isinstance(exc, httpx.TransportError):
        return f"cannot reach gateway: {exc}"
    return str(exc)
