"""Shared workspace dependencies: the RBAC-gated principals (one per permission the BFF
uses) and the request-scoped database sessions. `ReadSessionDep` (S4.4) serves the analytical
read endpoints and follows the replica when one is configured; anything that writes, or reads
its own writes back (annotations, the review queue, publish status), stays on `SessionDep` -
a replica may lag the primary."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from rs_core import Permission, Principal
from rs_core.db import get_read_session, get_session
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require

ViewPrincipal = Annotated[Principal, Depends(require(Permission.VIEW))]
AnnotatePrincipal = Annotated[Principal, Depends(require(Permission.ANNOTATE))]
PublishPrincipal = Annotated[Principal, Depends(require(Permission.PUBLISH))]
RunAnalysisPrincipal = Annotated[Principal, Depends(require(Permission.RUN_ANALYSIS))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReadSessionDep = Annotated[AsyncSession, Depends(get_read_session)]
