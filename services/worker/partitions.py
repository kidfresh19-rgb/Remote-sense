"""Analysis-partition upkeep (S4.1). The `analysis` table is RANGE-partitioned by month on
`pass_date` (PRD 0001 R15/R18: scale is data volume - hundreds of thousands of farms accruing
passes indefinitely - so each month stays a small, separately-indexed table and date-bounded
reads skip cold history).

Migration 0008 seeds the months around its own run; this module keeps the window rolling. The
planner is pure, mirroring retention.py's shape: every month the system can currently write,
from one slack month behind the backfill horizon (the horizon floor moves day by day, so the
oldest backfill write must stay inside an existing partition) to a lookahead ahead of today.
The orchestrator CREATEs the missing months, each inside its own savepoint: a month that cannot
be created - the DEFAULT partition already holds rows in its range, e.g. after a horizon widened
by config - is reported as skipped, never fatal, because rows in DEFAULT are correct, merely
unpartitioned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from services.worker.planning import backfill_window

DEFAULT_LOOKAHEAD_MONTHS = 3
_SLACK_MONTHS_BEHIND = 1


def month_floor(day: date) -> date:
    """The first day of `day`'s month - the partition bound every row in that month shares."""
    return day.replace(day=1)


def add_months(month_start: date, months: int) -> date:
    """`months` whole months from a month start (negative steps back); always a month start."""
    total = month_start.year * 12 + (month_start.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def partition_name(month_start: date) -> str:
    """analysis_y2026m06: zero-padded so names sort chronologically."""
    return f"analysis_y{month_start.year:04d}m{month_start.month:02d}"


def plan_partition_months(
    today: date,
    *,
    backfill_months: int,
    lookahead_months: int = DEFAULT_LOOKAHEAD_MONTHS,
) -> list[date]:
    """Every month-start the system can currently write: one slack month behind the backfill
    horizon through `lookahead_months` ahead of today. Contiguous and ascending. Reuses the
    backfill window math so the partition window and the advertised history depth never drift."""
    if lookahead_months < 0:
        raise ValueError("lookahead months must be >= 0")
    horizon_start, _ = backfill_window(today, backfill_months)  # validates backfill_months
    first = add_months(month_floor(horizon_start), -_SLACK_MONTHS_BEHIND)
    last = add_months(month_floor(today), lookahead_months)
    months: list[date] = []
    month = first
    while month <= last:
        months.append(month)
        month = add_months(month, 1)
    return months


@dataclass(frozen=True)
class PartitionSummary:
    """One upkeep run: months newly created, already present, and skipped (un-creatable because
    DEFAULT holds rows in their range - correct data, merely unpartitioned)."""

    created: list[str]
    present: list[str]
    skipped: list[str]


_CHILDREN_SQL = text(
    """
    SELECT c.relname
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = 'analysis'
    """
)


async def ensure_analysis_partitions(
    session: AsyncSession,
    *,
    today: date,
    backfill_months: int,
    lookahead_months: int = DEFAULT_LOOKAHEAD_MONTHS,
) -> PartitionSummary:
    """Create whatever monthly partitions the current write window needs and report the run.
    Idempotent: existing months are left untouched, and a failed CREATE rolls back only its own
    savepoint. Runs inside the caller's transaction - the caller commits."""
    existing = frozenset((await session.execute(_CHILDREN_SQL)).scalars().all())
    created: list[str] = []
    present: list[str] = []
    skipped: list[str] = []
    for month in plan_partition_months(
        today, backfill_months=backfill_months, lookahead_months=lookahead_months
    ):
        name = partition_name(month)
        if name in existing:
            present.append(name)
            continue
        ddl = (
            f"CREATE TABLE {name} PARTITION OF analysis "
            f"FOR VALUES FROM ('{month.isoformat()}') TO ('{add_months(month, 1).isoformat()}')"
        )
        try:
            async with session.begin_nested():
                await session.execute(text(ddl))
        except DBAPIError:
            skipped.append(name)
        else:
            created.append(name)
    return PartitionSummary(created=created, present=present, skipped=skipped)
