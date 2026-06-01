"""Collection planning - the pure decision logic of the pipeline (Phase 3), separated from
Celery/Redis/DB so it is unit-testable with no infrastructure. Tasks call these functions;
the functions never do I/O.

Covers: the backfill window, enqueue-time dedup + gap detection (R-1: never process a scene
twice), the forward-fill cadence check, and the idempotency key a scene-collection task is
locked on."""

from __future__ import annotations

import calendar
from collections.abc import Collection, Iterable, Sequence
from datetime import date, datetime, timedelta

# Sentinel-2 combined-constellation revisit at the equator. Forward-fill polls on this cadence;
# it does not predict exact pass dates (those come from the archive search), it decides how
# often to look.
SENTINEL2_REVISIT_DAYS = 5


def _subtract_months(d: date, months: int) -> date:
    """Calendar-correct month subtraction, clamping the day to the target month's length."""
    month_index = d.month - 1 - months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def backfill_window(today: date, months: int) -> tuple[date, date]:
    """The historical range to backfill: `months` before today, up to today. `months` is the
    configurable backfill depth (default 18, PLAN §6)."""
    if months <= 0:
        raise ValueError("backfill months must be positive")
    return _subtract_months(today, months), today


def plan_scenes(
    discovered: Sequence[str],
    already_processed: Collection[str],
) -> list[str]:
    """The scenes to enqueue: those discovered by an archive search that have not already been
    processed, in discovery (chronological) order. This is dedup and gap detection in one - a
    scene that failed earlier is simply 'not processed' and gets replanned. Duplicate ids in
    the search result are collapsed."""
    processed = set(already_processed)
    plan: list[str] = []
    seen: set[str] = set()
    for scene_id in discovered:
        if scene_id in processed or scene_id in seen:
            continue
        plan.append(scene_id)
        seen.add(scene_id)
    return plan


def due_for_forward_fill(
    last_poll_at: datetime | None,
    now: datetime,
    *,
    cadence_days: int = SENTINEL2_REVISIT_DAYS,
) -> bool:
    """Whether a field is due for a forward-fill poll. A field never polled is always due; a
    polled field becomes due once a revisit cycle has elapsed."""
    if last_poll_at is None:
        return True
    return now - last_poll_at >= timedelta(days=cadence_days)


def select_forward_fill_due(
    candidates: Iterable[tuple[str, datetime | None, bool]],
    now: datetime,
    *,
    cadence_days: int = SENTINEL2_REVISIT_DAYS,
) -> list[str]:
    """From `(field_id, last_poll_at, backfill_complete)` candidates, the field ids due for a
    forward-fill poll: the historical backfill has finished and a revisit cycle has elapsed since
    the last poll. Pure - the scheduler runs the DB query, this decides who is due."""
    return [
        field_id
        for field_id, last_poll_at, backfill_complete in candidates
        if backfill_complete and due_for_forward_fill(last_poll_at, now, cadence_days=cadence_days)
    ]


def collection_key(field_id: str, scene_id: str, geometry_version: int) -> str:
    """The idempotency key a scene-collection task locks on, so two workers can't process the
    same field/scene/boundary concurrently (R-1). Tied to geometry_version: a boundary change
    is a genuinely different unit of work (DI-5)."""
    return f"collect:{field_id}:{scene_id}:v{geometry_version}"


def field_collection_key(field_id: str, geometry_version: int) -> str:
    """The enqueue-lock key for a whole-field collection run (a backfill, or one forward-fill
    poll), so two runs for the same field at the same boundary cannot overlap (R-1). Per-scene
    work uses `collection_key`; this is the coarser, field-level unit the Celery tasks lock on."""
    return f"collect:field:{field_id}:v{geometry_version}"
