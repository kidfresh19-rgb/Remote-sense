"""Prompt construction for the interpretation layer. The system context is static (the agronomic
framing plus the rules that keep the model grounded), so it is cacheable across every call; the
user block is the rendered evidence. Bump PROMPT_VERSION when either changes - it travels into the
stored interpretation's provenance."""

from __future__ import annotations

from rs_interpret.grounding import Evidence

PROMPT_VERSION = "interp/v1"

# Static and identical across calls, so the client can mark it for prompt caching.
SYSTEM_CONTEXT = (
    "You are an agronomy assistant for Zimbabwean agriculture, both smallholder and commercial. "
    "You write a short, plain-language field-health read from satellite vegetation indices.\n\n"
    "Rules:\n"
    "- Use ONLY the numbers and band labels provided. Never invent index values, dates, causes, "
    "or field history you were not given.\n"
    "- The indices: NDVI, EVI2 and SAVI measure canopy vigour and biomass; NDRE tracks "
    "chlorophyll and nitrogen status; NDMI tracks canopy moisture.\n"
    "- Clear-pixel fraction is the share of the field not obscured by cloud or shadow. Below "
    "about 0.5 the read is low-confidence: say so and hedge.\n"
    "- Output 2 to 4 sentences: the overall state, the most notable index signal, and any caveat. "
    "No markdown, no bullet points, no numbers beyond restating the ones you were given.\n"
    "- You are drafting for an agronomist who reviews and edits before anything reaches a farmer. "
    "Do not give prescriptive instructions such as fertiliser rates or irrigation schedules; "
    "describe what the imagery shows."
)


def build_user_prompt(evidence: Evidence) -> str:
    """Render the grounded evidence as the user message. Deterministic, so the same field/pass
    always produces the same prompt."""
    lines = [
        f"Crop: {evidence.crop or 'unknown'}",
        f"Pass date: {evidence.pass_date.isoformat() if evidence.pass_date else 'unknown'}",
        f"Clear-pixel fraction: {evidence.clear_fraction:.2f} ({evidence.confidence} confidence)",
        "Index readings (field mean):",
    ]
    for reading in evidence.readings:
        if reading.mean is None:
            lines.append(f"- {reading.index.upper()}: no clear pixels")
        else:
            lines.append(
                f"- {reading.index.upper()}: {reading.mean:.3f} -> {reading.band} ({reading.note})"
            )
    lines.append("\nWrite the field-health read.")
    return "\n".join(lines)
