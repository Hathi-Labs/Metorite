"""``PATCH /admin/organization`` — an admin renames their organization.

Hermetic: validation is a pure function and the gate is read off the route.
The UPDATE's own SQL is one statement scoped by ``get_org_id``; see R8 note in
the PR for the live check.
"""
from __future__ import annotations

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.admin import router
from gateway.routes.admin.organization import (
    DISPLAY_NAME_MAX,
    clean_display_name,
)


def test_name_is_trimmed_and_inner_space_collapsed():
    assert clean_display_name("  Fracktal   Works \n") == "Fracktal Works"


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_blank_name_is_refused(raw):
    with pytest.raises(HTTPException) as exc:
        clean_display_name(raw)
    assert exc.value.status_code == 422


def test_name_at_limit_passes_and_over_limit_is_refused():
    assert clean_display_name("a" * DISPLAY_NAME_MAX) == "a" * DISPLAY_NAME_MAX
    with pytest.raises(HTTPException) as exc:
        clean_display_name("a" * (DISPLAY_NAME_MAX + 1))
    assert exc.value.status_code == 422


def test_control_character_is_refused():
    with pytest.raises(HTTPException) as exc:
        clean_display_name("Acme\x00Corp")
    assert exc.value.status_code == 422


def _gate():
    route = next(
        r for r in router.routes
        if getattr(r, "path", "").endswith("/organization") and "PATCH" in r.methods
    )
    deps = [d.dependency for d in route.dependencies]
    assert deps, "the rename route has no permission dependency"
    return deps[0]


def _user(*perms):
    return UserContext(
        email="u@example.com", role=UserRole.EXECUTIVE,
        access=build_access(list(perms), roles=["x"]),
    )


async def test_gate_refuses_a_caller_without_settings_manage():
    with pytest.raises(HTTPException) as exc:
        await _gate()(_user("admin:members:manage"))
    assert exc.value.status_code == 403


async def test_gate_admits_a_caller_with_settings_manage():
    await _gate()(_user("admin:settings:manage"))
