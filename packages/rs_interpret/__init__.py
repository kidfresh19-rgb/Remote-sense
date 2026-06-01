"""rs_interpret (L4b): the plain-language agronomic interpretation layer. Grounds each stored
analysis result in numeric bands, drafts a read via the Claude API, and stores it for agronomist
review before publish (never auto-published, risk #6). Prompt caching on the static agronomic
context. The Anthropic SDK is isolated in `rs_interpret.client`, so everything here is importable
and testable without it."""

from rs_interpret.grounding import Evidence, IndexReading, ground
from rs_interpret.prompts import PROMPT_VERSION, SYSTEM_CONTEXT, build_user_prompt
from rs_interpret.service import Interpretation, InterpretClient, interpret
from rs_interpret.thresholds import CROP_BANDS, CROPS, Band, bands_for, classify

__all__ = [
    "Evidence",
    "IndexReading",
    "ground",
    "PROMPT_VERSION",
    "SYSTEM_CONTEXT",
    "build_user_prompt",
    "Interpretation",
    "InterpretClient",
    "interpret",
    "CROP_BANDS",
    "CROPS",
    "Band",
    "bands_for",
    "classify",
]
