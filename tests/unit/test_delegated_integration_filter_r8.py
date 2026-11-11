"""MCP-C3 on a real database (R8): a delegated run keeps the member's deny.

Spec: ``project-docs/specs/org_access_control.md`` §8a (Metorite task 1627).
The hermetic half, with each call site and each mutation, is
``test_delegated_integration_filter.py``. This half keeps its harness and
replaces the stubbed access read with the REAL ``resolve_access`` and the
REAL ``resolve_session_access``.

Each member holds the REAL ``member`` system role of org A, seeded by the
real ``provision_org_roles``, and so holds ``integrations:use:*``. Priya also
holds a ``user_permission_override`` that denies ``integrations:use:zoho-crm``.
The reads run on a phase-4 promoted scratch database, as the non-privileged
app role, with the org bound, so FORCE RLS binds.

Run (a PRIVATE database, one R8 suite at a time, because the ``promoted``
fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_delegated_integration_filter_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The mutations (R7).** Each ran on 2026-11-11, and each named test went red.

* M1: ``_integration_authorizer`` drops the ``_bound_run_member()`` fallback.
  Red: ``test_r1_a_delegated_batch_run_keeps_the_zoho_deny``,
  ``test_r3_the_streaming_sub_run_keeps_the_zoho_deny`` and
  ``test_r5_a_delegated_run_takes_the_cap_of_the_room``.
* M3: the batch path folds on its own thread. Red:
  ``test_r5_a_delegated_run_takes_the_cap_of_the_room``.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_delegated_integration_filter import (
    AGENT,
    install_world,
    parent_chat,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]

#: The people of org A, by key: their overrides on top of the member role.
PEOPLE: dict[str, tuple[tuple[str, str], ...]] = {
    "priya": (("integrations:use:zoho-crm", "deny"),),
    "bob": (),
}


def _email(key: str) -> str:
    return f"{key}.{TAG}@mcpc3.test"


@pytest.fixture(scope="module")
def people(promoted):  # noqa: F811 — a fixture
    a = promoted.org_a
    with promoted.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": a})
        c.execute(text("SELECT provision_org_roles(:o)"), {"o": a})
        role = str(c.execute(text(
            "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
            "AND slug = 'member'"), {"o": a}).scalar())
        for key, overrides in PEOPLE.items():
            uid = c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": _email(key), "o": a}).scalar()
            c.execute(text(
                "INSERT INTO user_role (user_id, role_id, organization_id) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
                {"u": uid, "r": role, "o": a})
            for permission, effect in overrides:
                c.execute(text(
                    "INSERT INTO user_permission_override (user_id, permission, effect, "
                    "reason, set_by, organization_id) VALUES (:u, :p, :x, 'MCP-C3 R8', "
                    "'seed@mcpc3.test', CAST(:o AS uuid))"),
                    {"u": uid, "p": permission, "x": effect, "o": a})
        # One chat of Bob that Priya reads: a shared room.
        room = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, organization_id) "
            "VALUES (:s, :u, 'orchestrator', CAST(:o AS uuid))"),
            {"s": room, "u": _email("bob"), "o": a})
        for key in ("bob", "priya"):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :u, 'member', CAST(:o AS uuid))"),
                {"s": room, "u": _email(key), "o": a})
    yield SimpleNamespace(p=promoted, a=a, room=room)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": room})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@mcpc3.test"})


@pytest.fixture
def world(tmp_path, monkeypatch, people):
    """The hermetic harness, with the REAL access reads (``access=False``)."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    access_mod.invalidate()
    w = install_world(tmp_path, monkeypatch, access=False)
    w.people = people
    yield w
    access_mod.invalidate()


def _run(world: Any, member: str | None, *, sub_run: bool = False,
         session: str | None = None) -> Any:
    """One delegated run under a parent chat of *member*, on the app role's
    engine. *sub_run* takes the streaming seam, else the batch path."""
    from orchestrator import executor

    dsn = world.people.p.app_url.render_as_string(hide_password=False)

    async def go() -> Any:
        async with tenant_engine_scope(dsn):
            if sub_run:
                return await executor._run_sub_agent_streaming(
                    AGENT, "the deals of Welmont", "r-sub", event_queue=asyncio.Queue(),
                )
            return await executor.run_agent(
                AGENT, {"message": "the deals of Welmont", "mode": "sub_task"},
                run_id="r-child", organization_id=world.people.a,
            )

    with parent_chat(world, member, session=session or str(uuid.uuid4()),
                     org=world.people.a):
        return asyncio.run(go())


def test_r1_a_delegated_batch_run_keeps_the_zoho_deny(world) -> None:
    """The case of the task, on the real access store."""
    assert _run(world, _email("priya")) == {"answer": "ok"}
    assert world.state.ran == [["apollo"]], world.state.ran


def test_r2_a_member_with_no_deny_gets_zoho(world) -> None:
    _run(world, _email("bob"))
    assert world.state.ran == [["apollo", "zoho-crm"]], world.state.ran


def test_r3_the_streaming_sub_run_keeps_the_zoho_deny(world) -> None:
    world.state.agents = lambda: []
    out = _run(world, _email("priya"), sub_run=True)
    assert "returned empty agent list" in out, out
    assert world.state.bound == [["apollo"]], world.state.bound


def test_r4_a_run_with_no_member_is_unfiltered(world) -> None:
    """A delegation inside a cron run: no member, so no filter, as before."""
    _run(world, None)
    assert world.state.ran == [["apollo", "zoho-crm"]], world.state.ran
    assert world.state.builds == [(["apollo", "zoho-crm"], False)]


def test_r5_a_delegated_run_takes_the_cap_of_the_room(world) -> None:
    """Bob may use Zoho, and Priya reads his chat. The real fold reads the
    participants of the chat that asked, so the delegated run gets the
    intersection, and no Zoho."""
    _run(world, _email("bob"), session=world.people.room)
    assert world.state.ran == [["apollo"]], "the room's cap was lost on the hand-off"
