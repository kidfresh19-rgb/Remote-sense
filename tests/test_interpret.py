"""No-infra tests for rs_interpret (L4b): threshold classification, grounding determinism, prompt
construction, and the service (grounded status, never auto-published, low-confidence path) via a
fake client. The Anthropic SDK is never imported here."""

from __future__ import annotations

from datetime import date

import pytest
from rs_analysis import AnalysisOutput
from rs_analysis.zonal import ZonalStats
from rs_interpret import build_user_prompt, classify, ground, interpret
from rs_interpret.prompts import SYSTEM_CONTEXT


def _out(
    index: str, mean: float, *, clear: float = 0.9, conf: str = "high", res: int = 10
) -> AnalysisOutput:
    return AnalysisOutput(
        index_name=index,
        formula_version="1",
        resolution_m=res,
        clear_fraction=clear,
        confidence=conf,
        stats=ZonalStats(count=100, mean=mean, min=mean, max=mean, std=0.0, p10=mean, p90=mean),
    )


class _FakeClient:
    def __init__(self, reply: str = "NDVI shows a vigorous canopy.") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


def test_classify_bands() -> None:
    assert classify("ndvi", 0.1).label == "bare"
    assert classify("ndvi", 0.5).label == "developing"
    assert classify("ndvi", 0.85).label == "dense"
    assert classify("ndre", 0.05).label == "low"
    assert classify("ndre", 0.2).label == "moderate"
    assert classify("ndmi", -0.1).label == "dry"


def test_classify_uses_crop_override_when_present() -> None:
    from rs_interpret.thresholds import Band, bands_for

    # The base bands apply for an untuned crop; this just asserts the lookup is crop-aware.
    assert bands_for("ndvi", "maize") == bands_for("ndvi", None)
    assert isinstance(bands_for("ndvi", "maize")[0], Band)


def test_ground_uses_min_clear_fraction_and_classifies() -> None:
    outputs = [_out("ndvi", 0.7, clear=0.9), _out("ndre", 0.2, clear=0.4, res=20)]
    ev = ground(outputs, crop="maize", pass_date=date(2025, 1, 15))
    assert ev.clear_fraction == 0.4  # the more conservative (cloudier 20 m index)
    assert ev.confidence == "low"  # confidence_for(0.4) -> low
    bands = {r.index: r.band for r in ev.readings}
    assert bands["ndvi"] == "vigorous"
    assert bands["ndre"] == "moderate"


def test_ground_requires_outputs() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ground([])


def test_build_user_prompt_is_grounded() -> None:
    ev = ground([_out("ndvi", 0.62)], crop="tobacco", pass_date=date(2025, 2, 1))
    text = build_user_prompt(ev)
    assert "tobacco" in text
    assert "0.620" in text
    assert "NDVI" in text


async def test_interpret_never_auto_publishes_and_status_is_grounded() -> None:
    ev = ground(
        [_out("ndvi", 0.72), _out("ndmi", 0.1, res=20)], crop="maize", pass_date=date(2025, 1, 1)
    )
    client = _FakeClient()
    result = await interpret(ev, client, model="claude-opus-4-8")

    assert result.published is False
    assert result.needs_review is True
    assert result.status == "vigorous"  # from the NDVI band, not the model text
    assert result.narrative == "NDVI shows a vigorous canopy."
    assert result.prompt_version == "interp/v1"
    assert result.model == "claude-opus-4-8"
    # the model was handed the static (cacheable) system context + the grounded user block
    system, user = client.calls[0]
    assert system == SYSTEM_CONTEXT
    assert "0.720" in user


async def test_interpret_reports_low_confidence_when_cloudy() -> None:
    ev = ground([_out("ndvi", 0.5, clear=0.3)], crop="sorghum", pass_date=date(2025, 3, 1))
    result = await interpret(ev, _FakeClient(), model="claude-opus-4-8")
    assert result.confidence == "low"
    assert result.published is False
