"""No-DB tests for the RBAC permission model (Phase 7, R-5)."""

from __future__ import annotations

from rs_core.rbac import Permission, Principal, Role, permissions_for


def test_role_permission_mapping() -> None:
    assert permissions_for([Role.VIEWER]) == frozenset({Permission.VIEW})
    assert Permission.PUBLISH in permissions_for([Role.PUBLISHER])
    assert Permission.PUBLISH not in permissions_for([Role.ANALYST])
    assert permissions_for([Role.ADMIN]) == frozenset(Permission)


def test_permissions_union_across_roles() -> None:
    perms = permissions_for([Role.VIEWER, Role.PUBLISHER])
    assert Permission.VIEW in perms
    assert Permission.PUBLISH in perms


def test_principal_has() -> None:
    analyst = Principal(subject="u1", roles=frozenset({Role.ANALYST}))
    assert analyst.has(Permission.RUN_ANALYSIS)
    assert not analyst.has(Permission.PUBLISH)
    assert analyst.subject == "u1"


def test_empty_roles_grant_nothing() -> None:
    nobody = Principal(subject="u2", roles=frozenset())
    assert nobody.permissions == frozenset()
    assert not nobody.has(Permission.VIEW)
