"""No-DB tests for the RBAC permission model (Phase 7, R-5)."""

from __future__ import annotations

from rs_core.rbac import Permission, Principal, Role, permissions_for


def test_role_permission_mapping() -> None:
    assert permissions_for([Role.VIEWER]) == frozenset({Permission.VIEW, Permission.VIEW_GROUP})
    assert Permission.PUBLISH in permissions_for([Role.PUBLISHER])
    assert Permission.PUBLISH not in permissions_for([Role.ANALYST])
    assert permissions_for([Role.ADMIN]) == frozenset(Permission)


def test_comparison_group_permissions() -> None:
    """PRD 0002 Open Item 3 confirmed mappings (2026-06-19)."""
    viewer = permissions_for([Role.VIEWER])
    analyst = permissions_for([Role.ANALYST])
    admin = permissions_for([Role.ADMIN])

    # view_group sits at viewer level (any authenticated farm-access role).
    assert Permission.VIEW_GROUP in viewer

    # Analyst gets the create capabilities but not the admin-only bulk upload.
    assert {
        Permission.CREATE_REGION_CLUSTER,
        Permission.CREATE_COHORT,
        Permission.MANAGE_COHORT,
        Permission.VIEW_GROUP,
    } <= analyst
    assert Permission.UPLOAD_REGION_BOUNDARY not in analyst

    # upload_region_boundary is admin-only.
    assert Permission.UPLOAD_REGION_BOUNDARY in admin
    for role in (Role.VIEWER, Role.ANALYST, Role.PUBLISHER):
        assert Permission.UPLOAD_REGION_BOUNDARY not in permissions_for([role])

    # Publisher inherits the analyst comparison-group permissions on top of publish.
    publisher = permissions_for([Role.PUBLISHER])
    assert {Permission.CREATE_COHORT, Permission.MANAGE_COHORT, Permission.PUBLISH} <= publisher


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
