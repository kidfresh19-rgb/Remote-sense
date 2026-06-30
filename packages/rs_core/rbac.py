"""Role-based access control (Phase 7, R-5). A pure permission model - no web framework, no JWT -
so it is testable in isolation. The API layer maps a verified token to a `Principal` and enforces
a required permission per endpoint (services/api/auth.py).

Permissions are derived from roles server-side; a token never carries a permissions claim
directly. Adding a role or re-mapping a permission is a change here, in one place."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    VIEW = "view"  # read analyses, interpretations, history
    ANNOTATE = "annotate"  # add/edit annotations + saved AOIs
    RUN_ANALYSIS = "run_analysis"  # trigger collection / analysis
    PUBLISH = "publish"  # push results to the gateway / publish interpretations
    # Comparison groups (PRD 0002 Open Item 3). Role mappings confirmed 2026-06-19 (engineering
    # sign-off, Mishael Gwede, owner/engineer) - an access-control decision with no agronomy
    # dependency; agronomist sign-off not applicable. See PRD 0002 Open Item 3.
    VIEW_GROUP = "view_group"  # read a comparison group / group-context (any farm-access role)
    CREATE_REGION_CLUSTER = "create_region_cluster"  # analyst: draw / single-feature region
    UPLOAD_REGION_BOUNDARY = "upload_region_boundary"  # admin: bulk multi-feature boundary upload
    CREATE_COHORT = "create_cohort"  # analyst: define a peer cohort
    MANAGE_COHORT = "manage_cohort"  # analyst: edit / delete a peer cohort
    # Ward Watch (PRD 0003, backlog 0041). Role-to-permission mapping signed off 2026-06-30 (owner
    # sign-off, Mishael Gwede) - the settled least-privilege model: enroll + triage + diagnose for
    # officers (server-side ward-scoped, never a client `ward`); view + triage + rollup for the
    # district tier; view + rollup for ministry. Settled jointly with the PRD 0002 RBAC open item
    # (PRD 0003 §12.7); an access-control decision, no agronomy dependency.
    ENROLL_HOUSEHOLD = "enroll_household"  # officer: register household + plot offline
    VIEW_TRIAGE_QUEUE = "view_triage_queue"  # officer + district: weekly ranked distress queue
    RECORD_DIAGNOSIS = "record_diagnosis"  # officer: field-diagnosis capture (flywheel)
    VIEW_FOOD_SECURITY_ROLLUP = "view_food_security_rollup"  # district/province/ministry rollups


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    PUBLISHER = "publisher"
    ADMIN = "admin"
    # Ward Watch roles (PRD 0003 §3, backlog 0041); mapping signed off 2026-06-30 (owner).
    WARD_OFFICER = "ward_officer"  # AGRITEX officer: enroll, triage, diagnose (ward-scoped)
    DISTRICT_AGRONOMIST = "district_agronomist"  # read + rollup, no enrollment/diagnosis
    MINISTRY = "ministry"  # read-only, widest rollup view


# Role -> permissions, cumulative by responsibility; admin holds everything. Comparison-group
# permissions (PRD 0002 Open Item 3) confirmed as proposed: view_group at viewer level (any
# authenticated farm-access role); create_region_cluster, create_cohort and manage_cohort at
# analyst level; upload_region_boundary admin-only (granted solely via the all-permissions ADMIN
# role). engineering_review: Mishael Gwede, 2026-06-19 (owner/engineer). agronomist_signoff: n/a
# (access-control decision, no agronomy dependency).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW, Permission.VIEW_GROUP}),
    Role.ANALYST: frozenset(
        {
            Permission.VIEW,
            Permission.VIEW_GROUP,
            Permission.ANNOTATE,
            Permission.RUN_ANALYSIS,
            Permission.CREATE_REGION_CLUSTER,
            Permission.CREATE_COHORT,
            Permission.MANAGE_COHORT,
        }
    ),
    Role.PUBLISHER: frozenset(
        {
            Permission.VIEW,
            Permission.VIEW_GROUP,
            Permission.ANNOTATE,
            Permission.RUN_ANALYSIS,
            Permission.PUBLISH,
            Permission.CREATE_REGION_CLUSTER,
            Permission.CREATE_COHORT,
            Permission.MANAGE_COHORT,
        }
    ),
    Role.ADMIN: frozenset(Permission),
    # Ward Watch roles, owner-signed-off 2026-06-30 (see the Permission note above): officers enroll
    # + triage + diagnose; district views + triages + rollups; ministry views + rollups.
    Role.WARD_OFFICER: frozenset(
        {
            Permission.VIEW,
            Permission.VIEW_GROUP,
            Permission.ENROLL_HOUSEHOLD,
            Permission.VIEW_TRIAGE_QUEUE,
            Permission.RECORD_DIAGNOSIS,
        }
    ),
    Role.DISTRICT_AGRONOMIST: frozenset(
        {
            Permission.VIEW,
            Permission.VIEW_GROUP,
            Permission.VIEW_TRIAGE_QUEUE,
            Permission.VIEW_FOOD_SECURITY_ROLLUP,
        }
    ),
    Role.MINISTRY: frozenset(
        {
            Permission.VIEW,
            Permission.VIEW_FOOD_SECURITY_ROLLUP,
        }
    ),
}


def permissions_for(roles: Iterable[Role]) -> frozenset[Permission]:
    """The union of permissions granted by a set of roles. Unknown roles contribute nothing."""
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: a subject id plus the roles carried by its token. Permissions are
    derived from the roles - never trusted directly from the token."""

    subject: str
    roles: frozenset[Role]

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.roles)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
