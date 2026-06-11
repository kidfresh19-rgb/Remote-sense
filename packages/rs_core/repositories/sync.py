"""The gateway push outbox: delivery records keyed by idempotency key so a retried push
converges rather than duplicating (R-2), plus the latest-entry lookup behind the publish-status
endpoint."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import SyncOutbox


async def get_outbox(session: AsyncSession, *, idempotency_key: str) -> SyncOutbox | None:
    """The outbox row for a push idempotency key, or None. Lets the publisher skip a payload it
    has already delivered (R-2)."""
    return (
        await session.execute(
            select(SyncOutbox).where(SyncOutbox.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()


async def _ensure_outbox(
    session: AsyncSession,
    *,
    idempotency_key: str,
    canonical_farm_id: str,
    payload_version: str,
    result_count: int,
) -> SyncOutbox:
    await session.execute(
        pg_insert(SyncOutbox)
        .values(
            idempotency_key=idempotency_key,
            canonical_farm_id=canonical_farm_id,
            payload_version=payload_version,
            result_count=result_count,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    return (
        await session.execute(
            select(SyncOutbox).where(SyncOutbox.idempotency_key == idempotency_key)
        )
    ).scalar_one()


async def record_push(
    session: AsyncSession,
    *,
    idempotency_key: str,
    canonical_farm_id: str,
    payload_version: str,
    result_count: int,
    ok: bool,
    detail: str | None,
    pushed_at: datetime,
) -> SyncOutbox:
    """Record the outcome of one gateway push. On success the row is `published`; on failure it is
    `dead_letter` (retryable, the error retained). Idempotent on the key - re-recording bumps
    `attempts` and updates the status, so a retried push converges rather than duplicating."""
    state = await _ensure_outbox(
        session,
        idempotency_key=idempotency_key,
        canonical_farm_id=canonical_farm_id,
        payload_version=payload_version,
        result_count=result_count,
    )
    state.attempts += 1
    if ok:
        state.status = "published"
        state.pushed_at = pushed_at
        state.last_error = None
    else:
        state.status = "dead_letter"
        state.last_error = detail
    await session.flush()
    return state


async def get_latest_outbox_for_farm(
    session: AsyncSession, canonical_farm_id: str
) -> SyncOutbox | None:
    """The most recent outbox entry for a farm (by update time), or None if no push has ever been
    recorded. Used by the publish-status endpoint so the frontend can confirm delivery."""
    return (
        await session.execute(
            select(SyncOutbox)
            .where(SyncOutbox.canonical_farm_id == canonical_farm_id)
            .order_by(SyncOutbox.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
