"""Gateway push resilience + selection (L7). Zero network, zero DB: the shared retry policy is
unit-tested directly, the two adapters are driven through an injected httpx.MockTransport so a 4xx
fails fast while a 429/5xx/transport error is retried, and the config factory is checked against
synthetic Settings (built with _env_file=None so a developer's populated .env never leaks in)."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from rs_core.config import Settings
from rs_sync import (
    AgriTrackGatewayPort,
    HttpGatewayPort,
    IndexResult,
    RecordingGatewayPort,
    build_payload,
)
from rs_sync.resilience import describe_push_error, is_retryable

from services.worker.publish import (
    gateway_config_error,
    gateway_from_settings,
    gateway_is_dry_run,
)


def _ir(field: str | None, index: str, mean: float) -> IndexResult:
    return IndexResult(
        canonical_field_id=field,
        index_name=index,
        pass_date=date(2026, 5, 28),
        mean=mean,
        min=None,
        max=None,
        std=None,
        p10=None,
        p90=None,
        clear_fraction=0.95,
        confidence="high",
        resolution_m=10.0,
        formula_version="v1",
        provider="cdse",
        provider_scene_id="S2_X",
        processing_mode="windowed_cog",
    )


def _settings(**overrides: object) -> Settings:
    # _env_file=None: assert the configured values, not whatever the local .env happens to hold.
    return Settings(_env_file=None, **overrides)


# -- the shared retry policy ----------------------------------------------------------------------


def test_is_retryable_classifies_status_codes() -> None:
    req = httpx.Request("POST", "https://gw.example/results")

    def status_error(code: int) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))

    # transient: server + rate-limit
    assert is_retryable(status_error(500)) is True
    assert is_retryable(status_error(502)) is True
    assert is_retryable(status_error(503)) is True
    assert is_retryable(status_error(429)) is True
    # permanent: every other 4xx fails fast
    assert is_retryable(status_error(400)) is False
    assert is_retryable(status_error(401)) is False
    assert is_retryable(status_error(404)) is False
    assert is_retryable(status_error(422)) is False
    # transport errors are always transient; a non-HTTP bug is not a push failure
    assert is_retryable(httpx.ConnectError("boom")) is True
    assert is_retryable(httpx.ReadTimeout("slow")) is True
    assert is_retryable(ValueError("not a push error")) is False


def test_describe_push_error_names_the_status() -> None:
    req = httpx.Request("POST", "https://gw.example/results")
    err = httpx.HTTPStatusError("e", request=req, response=httpx.Response(401, request=req))
    assert "401" in describe_push_error(err)
    assert "cannot reach gateway" in describe_push_error(httpx.ConnectError("down"))


def test_describe_push_error_includes_body_snippet() -> None:
    # A real gateway rejection carries a reason in the body; surface a single-line, truncated copy.
    req = httpx.Request("POST", "https://gw.example/results")
    resp = httpx.Response(422, json={"error": "field 4 not found"}, request=req)
    detail = describe_push_error(httpx.HTTPStatusError("e", request=req, response=resp))
    assert "422" in detail
    assert "field 4 not found" in detail

    long_html = httpx.Response(502, html="<html>\n  " + "x" * 500 + "\n</html>", request=req)
    long_detail = describe_push_error(httpx.HTTPStatusError("e", request=req, response=long_html))
    assert long_detail.endswith("...")  # collapsed + truncated, not a multi-line dump
    assert "\n" not in long_detail


# -- AgriTrackGatewayPort: fail fast on 4xx, retry 429/5xx ----------------------------------------


async def test_agritrack_push_fails_fast_on_client_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, json={"error": "bad record"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort(
        "https://agri.example", "atk_key", client=client, max_attempts=4, backoff=0.0
    )
    result = await port.push(build_payload("2", [_ir("4", "ndvi", 0.5)]))
    await client.aclose()

    assert calls == 1  # a permanent 4xx is not retried
    assert result.ok is False
    assert "422" in (result.detail or "")


async def test_agritrack_push_retries_then_dead_letters_on_429() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort(
        "https://agri.example", "atk_key", client=client, max_attempts=3, backoff=0.0
    )
    result = await port.push(build_payload("2", [_ir("4", "ndvi", 0.5)]))
    await client.aclose()

    assert calls == 3  # 429 is transient: retried to exhaustion, then dead-letters
    assert result.ok is False


async def test_agritrack_push_reports_partial_failure_ratio() -> None:
    # A farm with two records where one fails must dead-letter the whole push (re-sent whole on
    # retry, extId-deduped) and report the ratio, not a lone error with no sense of scale.
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # the sub-plot record fails server-side; the field record succeeds
        return httpx.Response(500) if body.get("subPlotId") is not None else httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort(
        "https://agri.example", "atk_key", client=client, max_attempts=1, backoff=0.0
    )
    result = await port.push(build_payload("2", [_ir("4", "ndvi", 0.62), _ir("4.1", "ndvi", 0.4)]))
    await client.aclose()

    assert result.ok is False
    assert "1/2 records failed" in (result.detail or "")


async def test_agritrack_push_non_integer_farm_dead_letters_not_raises() -> None:
    # A non-integer canonical_farm_id can never satisfy the contract. The push must dead-letter with
    # the reason, not raise out and crash the publish task (which would hang the workspace).
    posted = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal posted
        posted += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort("https://agri.example", "atk_key", client=client)
    result = await port.push(build_payload("FARM-X", [_ir("4", "ndvi", 0.5)]))
    await client.aclose()

    assert posted == 0  # never even attempted - the data is structurally invalid
    assert result.ok is False
    assert "FARM-X" in (result.detail or "")


async def test_agritrack_push_owns_one_client_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    # No client injected: the adapter must open exactly one client for the whole batch and reuse it
    # across every record, not a fresh connection per POST. The old code opened a client per record
    # per retry. We count instantiations by wrapping httpx.AsyncClient with our MockTransport.
    import rs_sync.agritrack as agritrack_mod

    posts = 0
    created = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(200, json={"ok": True})

    real_client_cls = httpx.AsyncClient

    class _CountingClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal created
            created += 1
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(agritrack_mod.httpx, "AsyncClient", _CountingClient)
    port = AgriTrackGatewayPort("https://agri.example", "atk_key")  # no client injected
    result = await port.push(build_payload("2", [_ir("4", "ndvi", 0.62), _ir("4.1", "ndvi", 0.4)]))

    assert result.ok is True
    assert posts == 2  # two records, two POSTs
    assert created == 1  # one client owned for the whole batch, not one per record


# -- HttpGatewayPort: same retry classification ---------------------------------------------------


async def test_http_gateway_fails_fast_on_4xx() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = HttpGatewayPort(
        "https://gw.example/push", "token", client=client, max_attempts=4, backoff=0.0
    )
    result = await port.push(build_payload("FARM-1", [_ir("4", "ndvi", 0.5)]))
    await client.aclose()

    assert calls == 1  # permanent 4xx: not retried
    assert result.ok is False
    assert "403" in (result.detail or "")


# -- gateway selection + config validation --------------------------------------------------------


def test_gateway_config_error_flags_broken_agritrack() -> None:
    base = {"gateway_adapter": "agritrack", "agritrack_api_key": "atk_k"}
    assert gateway_config_error(_settings(**base, agritrack_base_url="")) is not None
    # an obvious placeholder counts as unset even though it is non-empty
    assert (
        gateway_config_error(_settings(**base, agritrack_base_url="http://your-ngrok-url"))
        is not None
    )
    assert (
        gateway_config_error(
            _settings(
                gateway_adapter="agritrack",
                agritrack_base_url="https://a.example",
                agritrack_api_key="",
            )
        )
        is not None
    )
    # fully configured -> no error
    assert gateway_config_error(_settings(**base, agritrack_base_url="https://a.example")) is None


def test_gateway_config_error_recording_is_valid() -> None:
    # The dry-run sink is a deliberate no-op, not a misconfiguration.
    assert gateway_config_error(_settings(gateway_adapter="recording")) is None


def test_gateway_config_error_flags_missing_http_url() -> None:
    assert gateway_config_error(_settings(gateway_adapter="http", gateway_push_url="")) is not None
    assert (
        gateway_config_error(
            _settings(gateway_adapter="http", gateway_push_url="https://gw.example")
        )
        is None
    )


def test_gateway_config_error_flags_schemeless_url() -> None:
    # A bare host with no scheme is a common .env slip; httpx cannot POST to it, so fail fast here.
    err = gateway_config_error(
        _settings(
            gateway_adapter="agritrack",
            agritrack_base_url="chapped-frenzied-tweet.ngrok-free.dev",
            agritrack_api_key="atk_k",
        )
    )
    assert err is not None and "scheme" in err
    # the same value with a scheme is accepted
    assert (
        gateway_config_error(
            _settings(
                gateway_adapter="agritrack",
                agritrack_base_url="https://chapped-frenzied-tweet.ngrok-free.dev",
                agritrack_api_key="atk_k",
            )
        )
        is None
    )


def test_gateway_from_settings_selects_the_adapter() -> None:
    agri = gateway_from_settings(
        _settings(
            gateway_adapter="agritrack",
            agritrack_base_url="https://a.example",
            agritrack_api_key="k",
        )
    )
    assert isinstance(agri, AgriTrackGatewayPort)
    http = gateway_from_settings(
        _settings(gateway_adapter="http", gateway_push_url="https://gw.example")
    )
    assert isinstance(http, HttpGatewayPort)
    assert isinstance(
        gateway_from_settings(_settings(gateway_adapter="recording")), RecordingGatewayPort
    )
    # http selected but no URL degrades to the recording sink rather than crashing
    assert isinstance(
        gateway_from_settings(_settings(gateway_adapter="http", gateway_push_url="")),
        RecordingGatewayPort,
    )


def test_gateway_is_dry_run_reflects_delivery() -> None:
    assert gateway_is_dry_run(_settings(gateway_adapter="recording")) is True
    assert (
        gateway_is_dry_run(
            _settings(
                gateway_adapter="agritrack",
                agritrack_base_url="https://a.example",
                agritrack_api_key="k",
            )
        )
        is False
    )
    assert (
        gateway_is_dry_run(_settings(gateway_adapter="http", gateway_push_url="https://gw.example"))
        is False
    )
    # http with no URL has degraded to the recording sink -> still a dry run
    assert gateway_is_dry_run(_settings(gateway_adapter="http", gateway_push_url="")) is True
