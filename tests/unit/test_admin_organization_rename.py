"""``PATCH /admin/organization`` — an admin renames their organization.

Hermetic: validation is a pure function and the gate is read off the route.
The UPDATE's own SQL is ``organization.rename_org_row``, the one body both
gateway doors call. ``test_operator_door.py::TestTheRenameDoorOnRealPostgres``
runs it against a real tenant database (R8).
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


# ── CP-8 "Rename an organization": the post-commit mirror (2026-10-27) ──────
#
# The admin rename commits on the tenant plane first. Then a best-effort
# mirror calls the Console's deployment-arm door, so the Console name follows.
# The mirror logs `org_rename_mirror_failed` only when the Console is wired
# and the call fails. Its SQL half is R8-fenced in
# `test_customer_console_org_rename.py` and `test_operator_door.py`.

from contextlib import asynccontextmanager  # noqa: E402

from acb_auth.console_resolve import ConsoleMemberWriteUnavailable  # noqa: E402
from gateway.routes.admin import organization  # noqa: E402

ORG = "00000000-0000-0000-0000-00000000000a"
ADMIN = "admin@shaurrya.example"


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str, dict]] = []

    def info(self, event, **kw):
        self.lines.append(("info", event, kw))

    def warning(self, event, **kw):
        self.lines.append(("warning", event, kw))

    def named(self, event):
        return [line for line in self.lines if line[1] == event]


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


@pytest.fixture
def world(monkeypatch):
    """The admin route with a fake tenant session and a fake Console."""
    events: list[str] = []
    w = {"events": events, "params": [], "console": [], "audit": [],
         "row": {"id": ORG, "slug": "dewin", "display_name": None},
         "console_answer": (200, {"slug": "dewin"}), "wired": True}
    log = _Log()
    w["log"] = log

    class _DB:
        async def execute(self, statement, params=None):
            w["params"].append(dict(params or {}))
            row = w["row"]
            if row is not None:
                row = {**row, "display_name": params["name"]}
            return _Result(row)

    @asynccontextmanager
    async def _session():
        events.append("open")
        yield _DB()
        events.append("commit")

    async def _org_id(db, user):
        return ORG

    async def _console(**kw):
        events.append("console")
        w["console"].append(kw)
        answer = w["console_answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(organization, "_tenant_session", _session)
    monkeypatch.setattr(organization, "get_org_id", _org_id)
    monkeypatch.setattr(organization, "rename_org_on_console", _console)
    monkeypatch.setattr(organization, "is_wired", lambda: w["wired"])
    monkeypatch.setattr(organization, "_log", log)
    monkeypatch.setattr(
        organization, "record_admin_change",
        lambda actor, action, target, **p: w["audit"].append((actor, action, target)),
    )
    return w


def _admin():
    return UserContext(
        email=ADMIN, role=UserRole.EXECUTIVE,
        access=build_access(["admin:settings:manage", "admin:members:read"],
                            roles=["admin"]),
    )


async def _patch(name="Shaurrya Tele"):
    return await organization.update_organization(
        organization.OrganizationPatch(display_name=name), admin=_admin(),
    )


async def test_an_admin_rename_writes_the_tenant_and_mirrors_the_console(world):
    """Done-when 2: one admin rename changes the name on both planes."""
    entry = await _patch("  Shaurrya   Tele ")
    assert entry.display_name == "Shaurrya Tele"
    assert world["params"] == [{"name": "Shaurrya Tele", "id": ORG}]
    # The mirror runs AFTER the tenant session committed, never inside it.
    assert world["events"] == ["open", "commit", "console"]
    # The actor is the authenticated admin (R11), and no org is on the wire.
    assert world["console"] == [{"actor_email": ADMIN, "name": "Shaurrya Tele"}]
    assert world["audit"] == [(ADMIN, "org.renamed", "org:dewin")]
    assert world["log"].named("org_rename_mirror_failed") == []


async def test_with_the_console_down_the_tenant_rename_still_succeeds(world):
    """Done-when 2: a Console outage logs the drift line and changes nothing."""
    world["console_answer"] = ConsoleMemberWriteUnavailable("HTTP 503")
    entry = await _patch()
    assert entry.display_name == "Shaurrya Tele"
    [line] = world["log"].named("org_rename_mirror_failed")
    assert line[0] == "warning"
    assert line[2]["slug"] == "dewin"


async def test_a_console_refusal_is_logged_with_its_status(world):
    """The spec's two-org admin case: the Console answers 409."""
    world["console_answer"] = (409, {"detail": "more than one organization"})
    entry = await _patch()
    assert entry.display_name == "Shaurrya Tele"
    [line] = world["log"].named("org_rename_mirror_failed")
    assert line[2]["slug"] == "dewin"
    assert line[2]["status"] == 409


async def test_an_unexpected_error_never_turns_a_rename_into_a_500(world):
    world["console_answer"] = RuntimeError("surprise")
    entry = await _patch()
    assert entry.display_name == "Shaurrya Tele"
    assert len(world["log"].named("org_rename_mirror_failed")) == 1


async def test_an_unwired_box_makes_no_call_and_logs_nothing(world):
    """A dev box has no Console, so the mirror stays silent."""
    world["wired"] = False
    entry = await _patch()
    assert entry.display_name == "Shaurrya Tele"
    assert world["console"] == []
    assert world["log"].named("org_rename_mirror_failed") == []


async def test_a_bad_name_is_422_and_neither_plane_is_touched(world):
    """Done-when 4, admin path: ``clean_display_name`` runs before any write."""
    with pytest.raises(HTTPException) as exc:
        await _patch("Acme" + chr(7) + "Corp")
    assert exc.value.status_code == 422
    assert world["events"] == []


async def test_no_row_is_404_and_the_console_is_not_called(world):
    world["row"] = None
    with pytest.raises(HTTPException) as exc:
        await _patch()
    assert exc.value.status_code == 404
    assert world["console"] == []
