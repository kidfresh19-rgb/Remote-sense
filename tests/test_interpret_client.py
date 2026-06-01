"""Smoke test for the Anthropic interpretation client (Phase 4b). Skips when the `interpret`
extra (the anthropic SDK) is not installed, and runs in CI where it is. Verifies the request
shape - model from settings, the static system context sent as a cached block, the evidence as the
user message - with the SDK call monkeypatched, so no real API request is made."""

from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from rs_core.config import Settings  # noqa: E402
from rs_interpret.client import AnthropicInterpretClient  # noqa: E402


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


async def test_complete_caches_system_and_returns_text(monkeypatch) -> None:
    client = AnthropicInterpretClient(
        Settings(anthropic_model="claude-opus-4-8", anthropic_api_key="sk-test")
    )
    captured: dict = {}

    async def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeMessage("  A vigorous canopy with adequate moisture.  ")

    monkeypatch.setattr(client._client.messages, "create", _fake_create)

    out = await client.complete("SYSTEM CONTEXT", "EVIDENCE BLOCK")

    assert out == "A vigorous canopy with adequate moisture."  # joined + stripped
    assert captured["model"] == "claude-opus-4-8"
    # the static system context is sent as a single cache_control:ephemeral block
    assert captured["system"][0]["text"] == "SYSTEM CONTEXT"
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    # the volatile evidence is the user message, with no cache marker
    assert captured["messages"][0]["content"] == "EVIDENCE BLOCK"
