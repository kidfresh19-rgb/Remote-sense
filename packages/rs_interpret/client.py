"""Anthropic-backed interpretation client (Phase 4b). This is the ONLY module in rs_interpret
that imports the Anthropic SDK - isolated here so thresholds/grounding/prompts/service stay
importable and testable without the `interpret` extra installed.

Prompt caching: the static agronomic system context is sent as a `cache_control: ephemeral`
block, so it is written to cache once and read (~0.1x cost) on every later interpretation while
the per-field evidence stays the volatile suffix. NOTE: the Anthropic cache has a per-model
minimum cacheable prefix (4096 tokens on Opus 4.8); the current `SYSTEM_CONTEXT` is below that, so
caching will not actually engage until the agronomic context grows (it is sized to grow as the
agronomist adds crop guidance + worked examples). The breakpoint is correct either way."""

from __future__ import annotations

import anthropic
from rs_core.config import Settings, get_settings


class AnthropicInterpretClient:
    """`rs_interpret.service.InterpretClient` implementation backed by the Claude API: one call
    sends the cached system context plus the grounded user evidence and returns the model's
    plain-language narrative. Model and API key come from settings."""

    def __init__(self, settings: Settings | None = None, *, max_tokens: int = 1024) -> None:
        self._settings = settings or get_settings()
        self._model = self._settings.anthropic_model
        self._max_tokens = max_tokens
        # An unset key falls back to the SDK's own env resolution (ANTHROPIC_API_KEY).
        self._client = anthropic.AsyncAnthropic(api_key=self._settings.anthropic_api_key or None)

    async def complete(self, system: str, user: str) -> str:
        """Send one interpretation request and return the narrative text. `system` (the static
        agronomic context) is marked for prompt caching; `user` (the per-field evidence block) is
        the volatile suffix. Short output, so a plain non-streaming call; the SDK retries 429/5xx
        on its own."""
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in message.content if block.type == "text").strip()
