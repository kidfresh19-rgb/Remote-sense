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


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    PUBLISHER = "publisher"
    ADMIN = "admin"


# Role -> permissions, cumulative by responsibility; admin holds everything.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW}),
    Role.ANALYST: frozenset({Permission.VIEW, Permission.ANNOTATE, Permission.RUN_ANALYSIS}),
    Role.PUBLISHER: frozenset(
        {Permission.VIEW, Permission.ANNOTATE, Permission.RUN_ANALYSIS, Permission.PUBLISH}
    ),
    Role.ADMIN: frozenset(Permission),
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
