"""SBX-F9 (the RLS half) and SBX-F23 (SBX-1a, task 3800), on a real database (R8).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1, §7.4, §9.2 and §12.1
(done-when 1, 2, 5, 7 and 12). Code: ``gateway/studio/tool_gaps.py``,
``gateway/studio/package_search.search_visible``,
``orchestrator/tool_gap_path.gap_hook`` and ``routes/studio/tool_gaps.py``.
Migration: the file that creates ``tool_gaps``, found by its content.

Each statement runs on a phase-4 promoted scratch database as the
non-privileged app role (NOSUPERUSER, NOBYPASSRLS), so FORCE RLS binds it.
Org A has Ana (who owns the packages), Ben and Pat (a holder of
``studio:publish``). Org B has Zed.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_user_tool_registry.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

What this proves:

1. The table: FORCE RLS, one policy with USING and WITH CHECK, and the
   CHECKs. A second run of the migration changes nothing.
2. As the app role, org B reads no row of org A, and cannot write a row for
   org A. A chat of org A written from org B gives no row.
3. A live package of the member that matches the need comes back. A draft
   with no live version never comes back.
4. Ben never sees the private package of Ana with no install that reaches
   him: not in the hits, the decide context or the row. An org install of it
   comes back, and a paused install does not.
5. The whole hook writes a ``reuse`` row with the app id of the package.
6. The route gives Pat the counts and the rows of org A only. Ben gets 403.

Mutation (R7), red here: replace ``packages_store.list_summaries`` in
``search_visible`` with a read of ``apps`` with no owner filter -> case 4
goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException, Response
from gateway.package_host import store as host_store
from gateway.routes.studio import packages
from gateway.routes.studio import tool_gaps as route
from gateway.studio import package_search
from gateway.studio import tool_gaps as gaps_store
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit import _r8_lists as r8_lists
from tests.unit import _tool_gap_fakes as fk
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MARK = "CREATE TABLE IF NOT EXISTS tool_gaps ("
TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@sbx1a.test"
BEN = f"ben.{TAG}@sbx1a.test"
PAT = f"pat.{TAG}@sbx1a.test"
ZED = f"zed.{TAG}@sbx1a.test"
_EMAIL_LIKE = f"%.{TAG}@sbx1a.test"
_SLUG_LIKE = f"sbx1a{TAG}-%"
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
#: The need of the member. The live package shares "leave", "request" and "track".
NEED = "track the leave requests of my team"


def _migration() -> Path:
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _manifest(mid: str, name: str, purpose: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": name, "purpose": purpose,
        "objects": [{"name": "entry", "fields": [{"key": "who", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:entry.list"}],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The body stream of a request, as the package handlers read it."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/packages")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


async def _call(route_name: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route_name* with *org* bound."""
    handler = getattr(packages, route_name)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route_name in ("create_package", "publish", "install_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route_name == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        default = 201 if route_name == "install_package" else 200
        return response.status_code or default, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _chat(world: Any, org: str, owner: str) -> str:
    sid = f"sbx1a-{TAG}-{uuid.uuid4().hex[:8]}"
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id) VALUES (:id, :u, 'orchestrator', 'Gaps', 1, "
            "'private', CAST(:o AS uuid))"), {"id": sid, "u": owner, "o": org})
    world.chats.append(sid)
    return sid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A with Ana, Ben and Pat, and org B with Zed. Ana owns one live
    package and one draft. A chat for each member."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    monkeypatch.setattr(get_settings(), "user_tool_find_enabled", True)
    with p.admin_engine.begin() as c:
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (PAT, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, chats=[])

    async def setup() -> None:
        status, got = await _call("create_package", w.a, ANA,
                                  payload=_manifest(f"sbx1a{TAG}-leave", "Leave tracker",
                                                    "Track the leave requests of each person."))
        assert status == 200, got
        w.live = str(got["package"]["id"])
        status, got = await _call("publish", w.a, ANA, w.live, payload={})
        assert status == 200, got
        status, got = await _call("create_package", w.a, ANA,
                                  payload=_manifest(f"sbx1a{TAG}-plan", "Leave planner",
                                                    "Plan the leave requests of the team."))
        assert status == 200, got
        w.draft = str(got["package"]["id"])

    _run(w, setup)
    w.chat_ana = _chat(w, w.a, ANA)
    w.chat_ben = _chat(w, w.a, BEN)
    w.chat_zed = _chat(w, w.b, ZED)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM tool_gaps WHERE member_email LIKE :e"), {"e": _EMAIL_LIKE})
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        c.execute(text("DELETE FROM package_install WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        for sid in w.chats:
            c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


async def _record(org: str, member: str, thread: str, **over: Any) -> str | None:
    values: dict[str, Any] = {
        "member": member, "thread_id": thread, "run_id": f"run-{TAG}", "need": NEED,
        "category": "people", "candidates": ["knowledge.search"], "answer": "none",
        "outcome": "no_match", "app_id": None}
    values.update(over)
    async with tenant_session(org) as db:
        return await gaps_store.record_gap(db, **values)


async def _search(org: str, viewer: str, query: str = NEED) -> list[str]:
    async with tenant_session(org) as db:
        return [h.app_id for h in await package_search.search_visible(
            db, viewer=viewer, query=query)]


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_force_rls_one_policy_and_its_checks(world) -> None:
    """Done-when 1. Each CHECK refuses its bad value, as the superuser."""
    with world.p.admin_engine.connect() as c:
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'tool_gaps'::regclass")).one()
        assert tuple(rls) == (True, True)
        policies = c.execute(text(
            "SELECT polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
            "FROM pg_policy WHERE polrelid = 'tool_gaps'::regclass")).all()
    assert [p[0] for p in policies] == ["tool_gaps_tenant_isolation"]
    assert "app.tenant_id" in policies[0][1] and "app.tenant_id" in policies[0][2]
    good = {"o": world.a, "m": ANA, "t": world.chat_ana, "need": "a need", "cat": "other",
            "cand": ["knowledge.search"], "ans": "none", "out": "no_match"}
    insert = ("INSERT INTO tool_gaps (organization_id, member_email, thread_id, run_id, need, "
              "category, candidates, answer, outcome) VALUES (CAST(:o AS uuid), :m, :t, 'r', "
              ":need, :cat, CAST(:cand AS text[]), :ans, :out)")
    for bad in ({"m": ANA.upper()}, {"need": ""}, {"need": "n" * 281}, {"cat": "Payroll"},
                {"cand": [f"x{i}" for i in range(11)]}, {"cand": ["two words"]},
                {"ans": "maybe"}, {"out": "found"}, {"t": "no-such-chat"}):
        with world.p.admin_engine.connect() as c, pytest.raises(DBAPIError), c.begin():
            c.execute(text(insert), {**good, **bad})
    with world.p.admin_engine.begin() as c:
        c.execute(text(insert), good)
        c.execute(text("DELETE FROM tool_gaps WHERE member_email = :m"), {"m": ANA})


def test_1_a_second_run_of_the_migration_changes_nothing(world) -> None:
    """Done-when 1: the file is idempotent on the current schema."""
    def shape() -> tuple[Any, ...]:
        with world.p.admin_engine.connect() as c:
            return tuple(c.execute(text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'tool_gaps'::regclass ORDER BY conname")).all())

    before = shape()
    sql = _migration().read_text(encoding="utf-8")
    auto = world.p.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    assert shape() == before and len(before) >= 10


# ── 2. RLS ──────────────────────────────────────────────────────────────────


def test_2_org_b_reads_no_row_of_org_a_and_cannot_write_one(world) -> None:
    """Done-when 2. Mutation: drop the policy -> org B reads the row -> red."""
    async def go() -> tuple[Any, ...]:
        made = await _record(world.a, ANA, world.chat_ana)
        async with tenant_session(world.b) as db:
            seen_b = (await db.execute(text("SELECT count(*) FROM tool_gaps"))).scalar_one()
            summary_b = await gaps_store.gap_summary(db)
        async with tenant_session(world.a) as db:
            seen_a = (await db.execute(text(
                "SELECT count(*) FROM tool_gaps WHERE id = CAST(:i AS uuid)"), {"i": made}
            )).scalar_one()
        refused = False
        try:
            async with tenant_session(world.b) as db:
                await db.execute(text(
                    "INSERT INTO tool_gaps (organization_id, member_email, thread_id, run_id, "
                    "need, answer, outcome) VALUES (CAST(:o AS uuid), :m, :t, 'r', 'x', "
                    "'none', 'no_match')"), {"o": world.a, "m": ZED, "t": world.chat_zed})
        except DBAPIError:
            refused = True
        return made, seen_b, summary_b, seen_a, refused

    made, seen_b, summary_b, seen_a, refused = _run(world, go)
    assert made is not None
    assert seen_b == 0 and summary_b.rows == () and summary_b.by_outcome == {}
    assert seen_a == 1
    assert refused, "WITH CHECK refused a row of org A written from org B"


def test_2_a_chat_of_org_a_written_from_org_b_gives_no_row(world) -> None:
    async def go() -> Any:
        return await _record(world.b, ZED, world.chat_ana)

    assert _run(world, go) is None
    with world.p.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tool_gaps WHERE member_email = :m"),
                         {"m": ZED}).scalar_one() == 0


# ── 3, 4. The library ─────────────────────────────────────────────────────


def test_3_a_live_package_of_the_member_comes_back_and_a_draft_never(world) -> None:
    """Done-when 5 (the search half)."""
    found = _run(world, lambda: _search(world.a, ANA))
    assert found == [world.live]
    assert world.draft not in found
    planner = _run(world, lambda: _search(world.a, ANA, "plan the leave requests"))
    assert world.draft not in planner


def test_4_another_member_never_sees_a_private_package(world) -> None:
    """Done-when 7. Mutation: read ``apps`` with no owner filter -> red."""
    assert _run(world, lambda: _search(world.a, BEN)) == []
    assert _run(world, lambda: _search(world.b, ZED)) == []


def test_4_an_org_install_comes_back_and_a_paused_one_does_not(world) -> None:
    """Done-when 7. ``install_at_scope`` finds the org install, and
    ``pause_install`` pauses it."""
    async def install() -> Any:
        # The store writes the org install. The route asks for the approval of
        # J2b-1 first, and that review is not what this case tests.
        async with tenant_session(world.a) as db:
            await host_store.install(
                db, app_id=world.live, version=1, scope_subject="org", scope_kind="org",
                installed_by=PAT, ends_at=None, contributions=())
        return await _search(world.a, BEN)

    assert _run(world, install) == [world.live]

    async def pause() -> Any:
        async with tenant_session(world.a) as db:
            found = await host_store.install_at_scope(
                db, app_id=world.live, scope_kind="org", scope_subject="org")
            assert found is not None
            assert await host_store.pause_install(db, install_id=found.id) is True
        return await _search(world.a, BEN)

    assert _run(world, pause) == []


# ── 4, 5. The whole hook on the real store ────────────────────────────────


def _hook_fakes(monkeypatch: Any, pick: Callable[[list[Any]], str]) -> list[str]:
    """The gate, the features, the catalog and the engine as fakes. The
    session, the library check and the store are real."""
    from acb_skills import decide_tools
    from acb_skills.system_one import Answer
    from gateway.routes.studio import actions
    from orchestrator import _tool_injection as ti
    from orchestrator import in_app_path as iap
    from orchestrator import tool_gap_path as tgp

    contexts: list[str] = []

    async def _open() -> bool:
        return True

    async def _features() -> frozenset[str]:
        return fk.holder_features()

    async def _catalog() -> Any:
        return actions.CatalogRows([])

    async def _ask(context: str, items: list[Any], *, purpose: str) -> list[Any]:
        contexts.append(context + json.dumps([list(i.options) for i in items]))
        return [Answer(id="answer", choice=pick(items), confidence=0.95, reason=""),
                Answer(id="category", choice="people", confidence=0.95, reason="")]

    monkeypatch.setattr(ti, "_studio_member_open", _open)
    monkeypatch.setattr(iap, "member_features", _features)
    monkeypatch.setattr(actions, "catalog_rows", _catalog)
    monkeypatch.setattr(decide_tools, "typed_choices_on", lambda: True)
    monkeypatch.setattr(decide_tools, "ask_typed", _ask)
    monkeypatch.setattr(decide_tools, "_run_threshold", lambda: 0.7)
    monkeypatch.setattr(tgp, "_log", fk.Logs())
    monkeypatch.setattr(get_settings(), "tool_search_orgs", "*")
    return contexts


def _hook(world: Any, monkeypatch: Any, member: str, chat: str) -> Any:
    from orchestrator import executor as ex
    from orchestrator import tool_gap_path as tgp

    monkeypatch.setattr(ex, "_verified_run_member", lambda: member)

    async def go() -> Any:
        with fk.in_run(org=world.a, session_id=chat, run_id=f"run-{TAG}"):
            return await tgp.gap_hook(NEED, [])

    return _run(world, go)


def _rows_of(world: Any, member: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT organization_id::text, member_email, thread_id, need, category, "
            "candidates, answer, outcome, app_id::text FROM tool_gaps "
            "WHERE member_email = :m ORDER BY created_at"), {"m": member}))


def test_5_the_hook_writes_a_reuse_row_with_the_app_id(world, monkeypatch) -> None:
    """Done-when 5: the package comes back as ``reuse``, with its id."""
    _hook_fakes(monkeypatch, lambda items: items[0].options[0])
    told = _hook(world, monkeypatch, ANA, world.chat_ana)
    assert told is not None and f"pkg:{world.live}" in told
    [row] = _rows_of(world, ANA)
    assert row.organization_id == world.a and row.thread_id == world.chat_ana
    assert (row.answer, row.outcome, row.app_id) == ("tool", "reuse", world.live)
    assert row.candidates[0] == f"pkg:{world.live}"
    assert row.need == NEED and row.category == "people"
    assert world.draft not in json.dumps(list(row.candidates))


def test_4_the_private_package_never_reaches_the_context_or_the_row(world, monkeypatch) -> None:
    """Done-when 7, through the whole hook: Ben's context, options and row
    never name Ana's packages."""
    contexts = _hook_fakes(monkeypatch, lambda items: "none")
    assert _hook(world, monkeypatch, BEN, world.chat_ben) is not None
    [row] = _rows_of(world, BEN)
    assert (row.answer, row.outcome, row.app_id) == ("none", "no_match", None)
    seen = " ".join(contexts) + json.dumps(list(row.candidates))
    assert contexts, "non-vacuity: the engine was asked"
    for secret in (world.live, world.draft, "Leave tracker", "Track the leave requests of each"):
        assert secret not in seen, secret


def test_5_the_real_catalog_runs_in_the_hook(world, monkeypatch) -> None:
    """Done-when 9 on the real seam: the hook calls the REAL
    ``routes/studio/actions.catalog_rows`` in the bound frame, before any
    session of its own, and writes one row."""
    from gateway.routes.studio import actions

    real = actions.catalog_rows
    _hook_fakes(monkeypatch, lambda items: "none")
    monkeypatch.setattr(actions, "catalog_rows", real)
    assert _hook(world, monkeypatch, BEN, world.chat_ben) is not None
    [row] = _rows_of(world, BEN)
    assert (row.answer, row.outcome) == ("none", "no_match")
    assert all(" " not in c for c in row.candidates)


# ── 6. The route ──────────────────────────────────────────────────────────


def test_6_the_route_gives_a_publisher_the_rows_of_their_org_only(world) -> None:
    """Done-when 12. Pat reads org A. Zed, a publisher of org B, reads none
    of it. Ben, with no share verb, gets 403."""
    async def go() -> tuple[Any, Any, Any]:
        await _record(world.a, ANA, world.chat_ana, category="documents", outcome="compose",
                      answer="compose")
        await _record(world.a, BEN, world.chat_ben)
        await _record(world.b, ZED, world.chat_zed, need="org b secret need")
        out = []
        for org, who, grants in ((world.a, PAT, PUBLISHER), (world.b, ZED, PUBLISHER),
                                 (world.a, BEN, STUDIO)):
            token = bind_tenant(org)
            try:
                out.append(await route.list_tool_gaps(user=_user(org, who, grants)))
            except HTTPException as exc:
                out.append(exc.status_code)
            finally:
                release_tenant(token)
        return tuple(out)

    pat, zed, ben = _run(world, go)
    assert pat["counts"]["by_outcome"] == {"compose": 1, "no_match": 1}
    assert pat["counts"]["by_category"] == {"documents": 1, "people": 1}
    assert [g["need"] for g in pat["gaps"]] == [NEED, NEED]
    assert all(set(g) == {"need", "category", "outcome", "created_at"} for g in pat["gaps"])
    assert "org b secret need" not in json.dumps(pat)
    assert [g["need"] for g in zed["gaps"]] == ["org b secret need"]
    assert ben == 403


# ── The CI guard ──────────────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    assert "tests/unit/test_user_tool_registry.py" in r8_lists.must_run()
