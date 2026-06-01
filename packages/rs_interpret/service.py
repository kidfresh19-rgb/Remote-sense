"""The interpretation service: ground -> prompt -> model -> a reviewable Interpretation. The
structured fields (status, confidence) are derived from the evidence, never trusted to the model;
the model only writes the narrative. Nothing is ever auto-published (risk #6) - every result is
needs_review=True / published=False until an agronomist signs off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rs_interpret.grounding import Evidence
from rs_interpret.prompts import PROMPT_VERSION, SYSTEM_CONTEXT, build_user_prompt

# The primary vigour index whose band drives the headline status.
_STATUS_INDEX = "ndvi"


@dataclass(frozen=True)
class Interpretation:
    """A drafted, unpublished agronomic read. Stored as-is; an agronomist reviews/edits and only
    then publishes (publishing flips `published`). `status` and `confidence` come from the grounded
    numbers, so they cannot drift from the evidence even if the narrative is edited."""

    narrative: str
    status: str
    confidence: str
    needs_review: bool
    published: bool
    prompt_version: str
    model: str


class InterpretClient(Protocol):
    """The single call the service needs from a model client. The real adapter
    (rs_interpret.client.AnthropicInterpretClient) applies prompt caching on the system context;
    tests pass a fake."""

    async def complete(self, system: str, user: str) -> str: ...


def _status(evidence: Evidence) -> str:
    """The headline status: the primary vigour index's band, else any classified reading."""
    for reading in evidence.readings:
        if reading.index == _STATUS_INDEX and reading.band is not None:
            return reading.band
    for reading in evidence.readings:
        if reading.band is not None:
            return reading.band
    return "unknown"


async def interpret(evidence: Evidence, client: InterpretClient, *, model: str) -> Interpretation:
    """Draft a plain-language read for one field/pass. The narrative is the model's; the status and
    confidence are derived from the grounded evidence. Always returned needs_review/unpublished."""
    narrative = (await client.complete(SYSTEM_CONTEXT, build_user_prompt(evidence))).strip()
    return Interpretation(
        narrative=narrative,
        status=_status(evidence),
        confidence=evidence.confidence,
        needs_review=True,
        published=False,
        prompt_version=PROMPT_VERSION,
        model=model,
    )
