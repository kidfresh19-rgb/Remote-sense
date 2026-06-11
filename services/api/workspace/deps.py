"""Shared workspace dependencies: the RBAC-gated principals (one per permission the BFF
uses) and the request-scoped database session."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from rs_core import Permission, Principal
from rs_core.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require

ViewPrincipal = Annotated[Principal, Depends(require(Permission.VIEW))]
AnnotatePrincipal = Annotated[Principal, Depends(require(Permission.ANNOTATE))]
PublishPrincipal = Annotated[Principal, Depends(require(Permission.PUBLISH))]
RunAnalysisPrincipal = Annotated[Principal, Depends(require(Permission.RUN_ANALYSIS))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
