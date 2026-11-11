"""Task 1317 — each App Workshop write lands its audit row in its own org (R8).

Code: ``gateway/routes/apps/`` — ``publish.py``, ``grants.py``,
``runtime.py``, ``durability.py``, ``tools.py``, ``actions.py`` and the audit
writers in ``_common.py``.

``app_audit`` has FORCE RLS. Before this fix, ``_common.record_app_audit``
opened its own UNBOUND session. As ``acb_app`` (NOBYPASSRLS) the insert failed
with "new row violates row-level security policy", and the helper swallowed
the error. So every App Workshop audit row was lost, and no test saw it.

The suite runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members, one app each.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_app_audit_bound_r8.py -v -rs

Without the database every R8 test here SKIPS, and the run reads green. A
skip is not a pass.

**Acceptance.** For each write path, the audit row lands in the org of the
app, and never in the other org. The other org cannot read it.

a. Publish, rollback and a tracked open (``publish.py``).
b. Grant upsert, grant revoke and consent (``grants.py``).
c. Storage put, storage delete and an AI call with its tokens (``runtime.py``).
d. Sync and restore (``durability.py``).
e. A read-only tool and a destructive tool (``tools.py``).
f. An app action from a request, and from an agent run with no bound tenant
   (``actions.py``). The row goes to the org of the app row.
g. The source fence (R7): no unbound ``record_app_audit(`` call remains.

**The mutation (R8).** On 2026-11-11 the old ``_common.py`` and the old
callers from ``origin/main`` ran against this suite. Groups a to f went red:
each found no audit row.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.routes.apps import (
    _common,
    actions,
    durability,
    grants,
    lifecycle,
    publish,
    runtime,
    tools,
)
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@audit1317.test"
BEN = f"ben.{TAG}@audit1317.test"
_LIKE = f"%.{TAG}@audit1317.test"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(tmp_path / "apps"))
    # The activity feed is Redis. This suite proves the audit rows.
    for mod in (lifecycle, publish, runtime):
        monkeypatch.setattr(mod, "publish_app_activity", lambda *a, **k: None)
    w = SimpleNamespace(p=promoted, a=promoted.org_a, b=promoted.org_b)
    w.apps = {}
    for org, member, name in ((w.a, ANA, f"Audit A {TAG}"), (w.b, BEN, f"Audit B {TAG}")):
        detail = _run(w, org, lambda n=name, m=member: lifecycle.create_app(
            lifecycle.AppCreate(name=n), user=_user(m)))
        assert isinstance(detail, lifecycle.AppDetail), detail
        w.apps[org] = SimpleNamespace(slug=detail.slug, id=_app_id(w, detail.slug),
                                      member=member)
    yield w
    with promoted.admin_engine.begin() as c:
        ids = [x.id for x in w.apps.values()]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _run(world: Any, org: str | None, call: Any) -> Any:
    """Run ``call()`` on the app role, bound to *org*, or unbound for None.

    Raises what the call raised."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            tok = bind_tenant(org) if org else None
            try:
                return await call()
            finally:
                if tok is not None:
                    release_tenant(tok)
    return asyncio.run(go())


def _app_id(world: Any, slug: str) -> str:
    with world.p.admin_engine.connect() as c:
        return str(c.execute(text("SELECT id FROM apps WHERE slug = :s"),
                             {"s": slug}).scalar_one())


def _audit(world: Any, app_id: str) -> list[tuple[str, str, dict[str, Any], int]]:
    """The audit rows of one app, as the superuser: org, kind, detail, tokens_in."""
    with world.p.admin_engine.connect() as c:
        return [(r[0], r[1], r[2], r[3]) for r in c.execute(text(
            "SELECT organization_id::text, kind, detail, tokens_in FROM app_audit "
            "WHERE app_id = :id ORDER BY id"), {"id": app_id})]


def _visible_to(world: Any, org: str, app_id: str) -> int:
    """How many audit rows of *app_id* the app role sees when bound to *org*."""
    async def go() -> int:
        async with tenant_engine_scope(_dsn(world.p.app_url)), tenant_session(org) as db:
            return int((await db.execute(text(
                "SELECT COUNT(*) FROM app_audit WHERE app_id = :id"),
                {"id": app_id})).scalar_one())
    return asyncio.run(go())


def _for_each_org(world: Any, act: Any) -> None:
    """Run ``act(app)`` for the app of each org, as its member, bound to it."""
    for org, app in world.apps.items():
        _run(world, org, lambda a=app: act(a))


def _assert_in_own_org(world: Any, want: list[tuple[str, str | None]]) -> None:
    """Each app holds exactly the rows *want* (kind, op), all in its own org.

    The op is ``detail.op`` or ``detail.action``. The other org sees none."""
    for org, app in world.apps.items():
        other = world.b if org == world.a else world.a
        rows = _audit(world, app.id)
        got = [(k, d.get("op") or d.get("action")) for _, k, d, _ in rows]
        assert got == want, f"the audit rows of the app of org {org}: {rows}"
        assert {o for o, *_ in rows} == {org}, f"an audit row left its org: {rows}"
        assert _visible_to(world, org, app.id) == len(want)
        assert _visible_to(world, other, app.id) == 0, "the other org reads the rows"


# ── a. Publish, rollback and a tracked open ───────────────────────────────


@_DB_GATE
def test_r8_publish_rollback_and_open_audit_in_their_own_org(world) -> None:
    async def act(app: Any) -> None:
        u = _user(app.member)
        await publish.publish_app(app.slug, publish.PublishRequest(notes="one"), user=u)
        await publish.publish_app(app.slug, publish.PublishRequest(notes="two"), user=u)
        await publish.rollback_app(app.slug, publish.RollbackRequest(version=1), user=u)
        await publish.get_app_bundle(app.slug, version="live", track=1, user=u)

    _for_each_org(world, act)

    _assert_in_own_org(world, [
        ("publish", None), ("publish", None), ("rollback", None), ("open", None)])


# ── b. Grants and consent ─────────────────────────────────────────────────


@_DB_GATE
def test_r8_grants_and_consent_audit_in_their_own_org(world) -> None:
    async def act(app: Any) -> None:
        u = _user(app.member)
        await publish.publish_app(app.slug, publish.PublishRequest(), user=u)
        friend = f"friend.{TAG}@audit1317.test"
        await grants.upsert_app_grant(app.slug, grants.GrantCreate(subject=friend), user=u)
        await grants.revoke_app_grant(app.slug, friend, user=u)
        async with tenant_session() as db:
            live = await grants.load_live_version_scopes(db, app.id, 1)
        await grants.consent_app(
            app.slug, grants.ConsentRequest(scope_set_hash=live[1]), user=u)

    _for_each_org(world, act)

    _assert_in_own_org(world, [
        ("publish", None), ("action", "grant_upsert"), ("action", "grant_revoke"),
        ("action", "consent")])


# ── c. Storage and the AI call ────────────────────────────────────────────


class _Msg:
    content = "Hello!"


class _Resp:
    choices: ClassVar[list[Any]] = [SimpleNamespace(message=_Msg())]

    def get(self, key: str, default: Any = None) -> Any:
        return {"prompt_tokens": 10, "completion_tokens": 5} if key == "usage" else default


@_DB_GATE
def test_r8_storage_and_ai_audit_in_their_own_org(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _llm(**_kw: Any) -> tuple[Any, str]:
        return _Resp(), "tier-fast"

    monkeypatch.setattr("acb_llm.acompletion_with_fallback", _llm)

    async def act(app: Any) -> None:
        u = _user(app.member)
        await runtime.put_storage_row(
            app.slug, "notes", "k1", runtime.StoragePut(value={"n": 1}), user=u)
        await runtime.delete_storage_row(app.slug, "notes", "k1", scope="shared", user=u)
        await runtime.ai_complete(app.slug, runtime.AiCompleteRequest(prompt="hi"), user=u)

    _for_each_org(world, act)

    _assert_in_own_org(world, [("storage", "put"), ("storage", "delete"), ("ai", None)])
    for app in world.apps.values():
        assert _audit(world, app.id)[-1][3] == 10, "the AI row lost its tokens"


# ── d. Sync and restore ───────────────────────────────────────────────────


@_DB_GATE
def test_r8_sync_and_restore_audit_in_their_own_org(world) -> None:
    async def act(app: Any) -> None:
        u = _user(app.member)
        out = await durability.sync_app(app.slug, user=u)
        assert out["checkpoint"], "this host made no git checkpoint"
        await durability.restore_app_checkpoint(
            app.slug, durability.RestoreRequest(sha=out["checkpoint"]), user=u)

    _for_each_org(world, act)

    _assert_in_own_org(world, [("storage", "sync"), ("storage", "restore")])


# ── e. Tools ──────────────────────────────────────────────────────────────


@_DB_GATE
def test_r8_tool_calls_audit_in_their_own_org(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        return {"done": True}

    registry = {
        "demo.read": tools.ToolSpec("demo", True, False, False, _handler),
        "demo.write": tools.ToolSpec("demo", False, True, False, _handler),
    }
    monkeypatch.setattr(tools, "_TOOL_REGISTRY", registry)

    async def _manifest(_db: Any, _row: Any) -> dict[str, Any]:
        return {"scopes": ["tool:demo.read", "tool:demo.write"]}

    async def _submit(_proposal: Any) -> dict[str, Any]:
        return {"ok": True, "result": {"done": True}}

    monkeypatch.setattr(tools, "_load_live_manifest", _manifest)
    monkeypatch.setattr(tools, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(tools, "submit", _submit)

    async def act(app: Any) -> None:
        u = _user(app.member)
        await tools.call_app_tool(app.slug, "demo.read", tools.ToolCallRequest(), user=u)
        await tools.call_app_tool(
            app.slug, "demo.write", tools.ToolCallRequest(confirm=True), user=u)

    _for_each_org(world, act)

    _assert_in_own_org(world, [("tool", None), ("tool", None)])
    for app in world.apps.values():
        assert [d["tool"] for _, _, d, _ in _audit(world, app.id)] == [
            "demo.read", "demo.write"]


# ── f. Actions, from a request and from an agent run ──────────────────────


@_DB_GATE
def test_r8_app_actions_audit_in_the_org_of_the_app(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    """``execute_app_action`` still reads the app on the unbound seam (H4),
    and that read sees no row as ``acb_app``. This test reads the row in the
    org of the app, as H4 will. The audit is the code under test. The agent
    run binds no tenant, and its row still lands in the org of the app.
    The storage read of the action is unbound too, so the action fails, and
    the audit row records ``ok`` False."""
    orgs = {app.slug: org for org, app in world.apps.items()}
    real_get = actions.get_app_or_404

    async def _get(_db: Any, slug: str, user: UserContext) -> Any:
        async with tenant_session(orgs[slug]) as db:
            return await real_get(db, slug, user)

    async def _manifest(_db: Any, _row: Any) -> dict[str, Any]:
        return {"actions": [{"name": "list_notes", "kind": "storage.list",
                             "table": "notes"}]}

    monkeypatch.setattr(actions, "get_app_or_404", _get)
    monkeypatch.setattr(actions, "_load_live_manifest", _manifest)

    async def _act(app: Any) -> None:
        # The storage read of the action is an H4 site too. As `acb_app` it
        # fails, and the route answers 502 after it writes the audit row.
        with pytest.raises(HTTPException) as err:
            await actions.execute_app_action(app.slug, "list_notes", {}, _user(app.member))
        assert err.value.status_code == 502

    for org, app in world.apps.items():
        _run(world, org, lambda a=app: _act(a))
        _run(world, None, lambda a=app: _act(a))

    _assert_in_own_org(world, [("action", "list_notes"), ("action", "list_notes")])
    for app in world.apps.values():
        assert [d["ok"] for _, _, d, _ in _audit(world, app.id)] == [False, False]


# ── g. The source fence (R7) ──────────────────────────────────────────────


def test_no_unbound_app_audit_writer_remains() -> None:
    """The unbound ``record_app_audit`` is gone, and no gateway file calls it.

    A call of ``record_app_audit(`` is an insert into ``app_audit`` with no
    bound tenant, which FORCE RLS refuses in silence. Use
    ``record_app_audit_bound`` in a request, or ``record_app_audit_in(db, ...)``
    in an open ``tenant_session``."""
    assert not hasattr(_common, "record_app_audit")
    call = re.compile(r"\brecord_app_audit\s*\(")
    offenders = [
        str(p.relative_to(_ROOT))
        for p in sorted(_GATEWAY.rglob("*.py"))
        if call.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_app_audit_bound_r8\.py \\?$", workflow, re.M), name
