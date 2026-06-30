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
# Comparison groups (PRD 0002 Open Item 3, RBAC mappings confirmed 2026-06-19): draw /
# single-feature is analyst-level, bulk multi-feature upload is admin-level. The view_group,
# create_cohort and manage_cohort principals are added when their slices (3, 6) wire endpoints.
CreateRegionClusterPrincipal = Annotated[
    Principal, Depends(require(Permission.CREATE_REGION_CLUSTER))
]
UploadRegionBoundaryPrincipal = Annotated[
    Principal, Depends(require(Permission.UPLOAD_REGION_BOUNDARY))
]
# Ward Watch field-diagnosis capture (backlog 0038). RECORD_DIAGNOSIS is the ward-officer permission
# (rs_core.rbac); ⚑ CONFIRM the role mapping with the owner alongside the other Ward Watch perms.
RecordDiagnosisPrincipal = Annotated[Principal, Depends(require(Permission.RECORD_DIAGNOSIS))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReadSessionDep = Annotated[AsyncSession, Depends(get_read_session)]
