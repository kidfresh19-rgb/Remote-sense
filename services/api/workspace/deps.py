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
# Ward Watch RBAC (PRD 0003, backlog 0041; role->permission mapping owner-signed-off 2026-06-30,
# see rs_core.rbac). The officer queue and the household visit drill-down need VIEW_TRIAGE_QUEUE
# (officer + district); the food-security rollup and the diagnosis labelled-set export need
# VIEW_FOOD_SECURITY_ROLLUP (district + ministry); capture needs RECORD_DIAGNOSIS (officer).
ViewTriageQueuePrincipal = Annotated[Principal, Depends(require(Permission.VIEW_TRIAGE_QUEUE))]
ViewFoodSecurityRollupPrincipal = Annotated[
    Principal, Depends(require(Permission.VIEW_FOOD_SECURITY_ROLLUP))
]
RecordDiagnosisPrincipal = Annotated[Principal, Depends(require(Permission.RECORD_DIAGNOSIS))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReadSessionDep = Annotated[AsyncSession, Depends(get_read_session)]
