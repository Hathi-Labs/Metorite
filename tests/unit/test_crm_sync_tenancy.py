"""WS-53 CRM-T3 — the three CRM background paths and the tenant.

Spec: ``project-docs/specs/crm_platform.md`` §4.3 and §13.3, CRM-T3.

Migration 241 put FORCE row level security on every ``crm_*`` table. Three
paths ran outside a member's request on the unbound ``_get_db()``, so under
RLS they read no row and every write failed. This suite fences what CRM-T3
did to each one.

**Hermetic.** No database:

* ``create_leads_from_new_mail`` with no tenant bound opens NO session — a
  tripwire on every opener (done-when 1).
* An AST fence: no ``.commit()`` inside a ``tenant_session`` block in
  ``auto_lead.py`` or ``broker_handlers.py``, with its "not vacuous"
  self-test. A second fence: neither file calls ``.commit()`` or ``get_db()``
  at all, because their helpers run INSIDE a block that the AST cannot see
  (done-when 2).
* The approval handler makes no provider call with no tenant bound, none when
  the native row is not visible, and none for a table it may not record on. A
  watched fake ``execute_push`` sees no open session (done-when 3).
* ``start_crm_zoho_sync()`` returns False with the flag ON, and
  ``POST /crm/sync/zoho`` answers 409, opens no session and calls no Zoho
  client (done-when 4).

**R8.** The real SQL on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the non-privileged role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS), done-when 6:

* An auto-lead run for an org-B mailbox, with org B bound, writes a lead with
  ``organization_id = B``. Org A reads none of its rows.
* The same run with org A bound sees no mailbox and writes nothing.
* The approval handler bound to A, given a proposal that names an org-B row,
  refuses with no provider call. Bound to B, it pushes and records the result
  on the row of org B.

The R8 classes carry ``_DB_GATE`` per class, the way
``test_email_scheduler_tenancy.py`` does, so the hermetic fences above run in
every environment. A skip of an R8 case is not a pass.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_crm_sync_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import json
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_common.db as shared_db
from acb_common.db import (
    TenantUnbound,
    bind_tenant,
    clear_tenant,
    release_tenant,
)
from fastapi import HTTPException
from gateway.routes.crm import auto_lead
from gateway.routes.crm import broker_handlers as crm_broker
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import records as crm_records
from gateway.routes.crm import sync_zoho as crm_sync
from gateway.routes.crm.core import ORGANIZATIONS
from sqlalchemy import create_engine, text

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user
from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture and its DB gate (role acb_app_h3rls).
# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_CRM = _REPO / "apps/services/gateway/gateway/routes/crm"
#: The two files CRM-T3 binds. `sync_zoho.py` is not here: it stays on the
#: unbound seam until CRM-Z5, and `test_db_engine_seam.py` names it.
_BOUND_FILES = (_CRM / "auto_lead.py", _CRM / "broker_handlers.py")

USER = crm_user()


@pytest.fixture()
def unbound() -> Iterator[None]:
    """Run the test with NO tenant bound, whatever an earlier test left."""
    token = clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _tripwire(opened: list[str], name: str) -> Any:
    """A session opener that records the open and refuses it."""
    def _open(*_a: Any, **_k: Any) -> Any:
        opened.append(name)
        raise AssertionError(f"{name} opened where no session may open")
    return _open


def _trip_every_opener(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Trip every way a CRM module can open a session, down to the factory."""
    opened: list[str] = []
    for module in (auto_lead, crm_core, crm_records, crm_sync):
        for name in ("_tenant_session", "_get_db"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, _tripwire(opened, name))
    monkeypatch.setattr(shared_db, "get_session_factory",
                        _tripwire(opened, "get_session_factory"))
    return opened


class _Log:
    """A structlog stand-in that records ``(level, key, kwargs)``."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, key: str, **kwargs: Any) -> None:
        self.lines.append((level, key, kwargs))

    def info(self, key: str, **kwargs: Any) -> None:
        self._add("info", key, **kwargs)

    def warning(self, key: str, **kwargs: Any) -> None:
        self._add("warning", key, **kwargs)

    def error(self, key: str, **kwargs: Any) -> None:
        self._add("error", key, **kwargs)

    def names(self) -> list[str]:
        return [key for _lvl, key, _kw in self.lines]


# ── done-when 1: no tenant, no session ──────────────────────────────────────


async def test_auto_lead_with_no_tenant_opens_no_session(
    monkeypatch: pytest.MonkeyPatch, unbound: None,
) -> None:
    opened = _trip_every_opener(monkeypatch)
    log = _Log()
    monkeypatch.setattr(auto_lead, "_log", log)

    stats = await auto_lead.create_leads_from_new_mail(str(uuid.uuid4()))

    assert opened == [], f"an unbound auto-lead run opened {opened}"
    assert stats["created"] == 0 and stats["candidates"] == 0
    assert "crm.auto_lead_unbound" in log.names()


# ── done-when 2: the AST fences ─────────────────────────────────────────────


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _commits_inside_tenant_sessions(source: str) -> list[int]:
    """Line numbers of each ``.commit()`` call inside an ``async with`` block
    whose context expression calls ``tenant_session``.

    Copied from ``test_email_scheduler_tenancy.py``. The ``_tenant_session``
    alias of the CRM package ends in ``tenant_session`` too."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens = any(
            isinstance(item.context_expr, ast.Call)
            and _call_name(item.context_expr).endswith("tenant_session")
            for item in node.items
        )
        if not opens:
            continue
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "commit"):
                    hits.append(inner.lineno)
    return hits


@pytest.mark.parametrize("path", _BOUND_FILES, ids=lambda p: p.name)
def test_no_commit_inside_a_tenant_session(path: Path) -> None:
    """R7 fence ``crm-background-no-mid-session-commit``."""
    hits = _commits_inside_tenant_sessions(path.read_text(encoding="utf-8"))
    assert hits == [], (
        f"{path.name} calls .commit() inside a tenant_session at lines "
        f"{hits}. A commit ends SET LOCAL, so each statement after it runs "
        "with no tenant. Split the work into another block instead."
    )


def test_the_commit_fence_is_not_vacuous() -> None:
    planted = (
        "async def f(org):\n"
        "    async with _tenant_session(org) as db:\n"
        "        await db.execute(x)\n"
        "        await db.commit()\n"
        "    async with other() as db:\n"
        "        await db.commit()\n"
    )
    assert _commits_inside_tenant_sessions(planted) == [4]


#: A call whose name ends in one of these opens an UNBOUND session.
_UNBOUND_OPENERS = ("get_db", "get_session_factory")


def _commits_and_unbound_opens(source: str) -> tuple[list[int], list[int]]:
    """Each ``.commit()`` call and each unbound opener, ANYWHERE in *source*.

    The block fence above cannot see a helper that commits: ``_run_batch``
    runs inside the caller's block but holds no ``async with`` of its own. So
    these two files must not call ``.commit()`` anywhere."""
    commits: list[int] = []
    opens: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if isinstance(node.func, ast.Attribute) and name == "commit":
            commits.append(node.lineno)
        elif name.endswith(_UNBOUND_OPENERS):
            opens.append(node.lineno)
    return commits, opens


@pytest.mark.parametrize("path", _BOUND_FILES, ids=lambda p: p.name)
def test_neither_file_commits_or_opens_an_unbound_session(path: Path) -> None:
    commits, opens = _commits_and_unbound_opens(
        path.read_text(encoding="utf-8"))
    assert commits == [], f"{path.name} calls .commit() at lines {commits}"
    assert opens == [], (
        f"{path.name} opens an unbound session at lines {opens}. Use "
        "`_tenant_session(org)` with the tenant the caller bound."
    )


def test_the_whole_file_fence_is_not_vacuous() -> None:
    planted = (
        "async def f():\n"
        "    db = await _get_db()\n"
        "    await db.commit()\n"
    )
    assert _commits_and_unbound_opens(planted) == ([3], [2])


# ── done-when 3: the approval handler ───────────────────────────────────────


def _proposal(*, table: str | None, native_id: str | None) -> Any:
    args: dict[str, Any] = {
        "module": "Accounts", "op": "update", "record_id": "z-acc-1",
        "body": {"Account_Name": "Renamed"},
    }
    if table is not None:
        args["table"] = table
    if native_id is not None:
        args["native_id"] = native_id
    return SimpleNamespace(action="crm.zoho_update", payload={"args": args})


class _WatchedPush:
    """A fake ``execute_push`` that records each call and the number of
    sessions open at that moment."""

    def __init__(self, open_sessions: list[int]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.open_at_call: list[int] = []
        self._open = open_sessions

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        self.open_at_call.append(self._open[0])
        return {"ok": True, "zoho_id": kwargs.get("record_id") or "z-new"}


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A fake CRM DB whose ``_tenant_session`` counts open blocks and records
    the tenant each one was opened with, plus a watched ``execute_push``."""
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, (crm_core, crm_records, crm_sync))
    open_sessions = [0]
    tenants: list[str | None] = []

    @asynccontextmanager
    async def _counting(organization_id: str | None = None) -> Any:
        tenants.append(organization_id)
        open_sessions[0] += 1
        try:
            yield fake
            await fake.commit()
        finally:
            open_sessions[0] -= 1

    monkeypatch.setattr(crm_core, "_tenant_session", _counting)
    push = _WatchedPush(open_sessions)
    monkeypatch.setattr(crm_sync, "execute_push", push)
    yield SimpleNamespace(db=fake, push=push, tenants=tenants)


async def test_the_handler_with_no_tenant_makes_no_provider_call(
    monkeypatch: pytest.MonkeyPatch, unbound: None,
) -> None:
    opened = _trip_every_opener(monkeypatch)
    push = _WatchedPush([0])
    monkeypatch.setattr(crm_sync, "execute_push", push)

    with pytest.raises(TenantUnbound):
        await crm_broker._handle_crm_zoho_write(
            _proposal(table=ORGANIZATIONS.table, native_id=str(uuid.uuid4())))

    assert push.calls == [], "an unbound approval reached Zoho"
    assert opened == [], f"an unbound approval opened {opened}"


async def test_the_handler_refuses_a_row_its_tenant_cannot_see(
    watched: Any,
) -> None:
    """Nothing is seeded, so the bound tenant sees no such row. Under RLS that
    is exactly what another tenant's row reads as."""
    token = bind_tenant("org-a")
    try:
        with pytest.raises(RuntimeError, match="not in this tenant"):
            await crm_broker._handle_crm_zoho_write(_proposal(
                table=ORGANIZATIONS.table, native_id=str(uuid.uuid4())))
    finally:
        release_tenant(token)

    assert watched.push.calls == [], "a push for an invisible row reached Zoho"
    assert watched.tenants == ["org-a"], (
        "the visibility read must open exactly one session, bound to the "
        "tenant of the approve call"
    )


@pytest.mark.parametrize("table, native_id", [
    (None, None),
    ("crm_companies", None),
    ("pending_actions", "11111111-1111-1111-1111-111111111111"),
])
async def test_the_handler_refuses_a_proposal_with_no_row_it_may_record_on(
    watched: Any, table: str | None, native_id: str | None,
) -> None:
    token = bind_tenant("org-a")
    try:
        with pytest.raises(RuntimeError, match="refused before any Zoho call"):
            await crm_broker._handle_crm_zoho_write(
                _proposal(table=table, native_id=native_id))
    finally:
        release_tenant(token)

    assert watched.push.calls == []
    assert watched.tenants == [], "a refused proposal opened a session"


async def test_the_provider_call_runs_with_no_session_open(
    watched: Any,
) -> None:
    row = watched.db.seed(ORGANIZATIONS.table, name="Fracktal",
                          zoho_id="z-acc-1", zoho_dirty=True)
    token = bind_tenant("org-a")
    try:
        await crm_broker._handle_crm_zoho_write(_proposal(
            table=ORGANIZATIONS.table, native_id=str(row.id)))
    finally:
        release_tenant(token)

    assert len(watched.push.calls) == 1
    assert watched.push.open_at_call == [0], (
        "execute_push ran while a session was open — a provider call must "
        "not hold a pool slot or a transaction"
    )
    assert watched.tenants == ["org-a", "org-a"], (
        "the handler must open two sessions, both bound to the approve "
        "call's tenant: the visibility read and the result write"
    )
    [stored] = [r for r in watched.db.rows(ORGANIZATIONS.table)
                if str(r["id"]) == str(row.id)]
    assert stored["zoho_dirty"] is False


# ── done-when 4: the sync loop and the hand-run cycle fail closed ───────────


async def test_the_flag_on_starts_no_loop_until_crm_z5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: True)
    monkeypatch.setattr(crm_sync, "_sync_task", None)
    log = _Log()
    monkeypatch.setattr(crm_sync, "_log", log)

    started = await crm_sync.start_crm_zoho_sync()

    assert started is False, "the sync loop started with the flag on"
    assert crm_sync._sync_task is None
    assert "crm.sync.off_until_crm_z5" in log.names()


def test_the_old_engine_has_no_route_and_the_new_route_is_the_mirror() -> None:
    """CRM-Z5 moved ``POST /crm/sync/zoho`` to ``routes/crm/sync.py``.

    The old two-way engine keeps no route, so nothing can run ``run_cycle``
    (which pushes to Zoho) from a request.
    """
    assert not hasattr(crm_sync, "run_sync_cycle_now")
    from gateway.routes.crm import sync as mirror_sync
    from gateway.routes.crm.core import router

    paths = [
        (r.path, tuple(sorted(r.methods)))
        for r in router.routes if getattr(r, "path", "").endswith("/sync/zoho")
    ]
    assert paths == [("/crm/sync/zoho", ("POST",))], paths
    assert mirror_sync.sync_now.__module__ == "gateway.routes.crm.sync"


# ── R8 helpers ──────────────────────────────────────────────────────────────


def _assert_non_priv(app_engine: Any) -> None:  # noqa: F811
    with app_engine.connect() as c:
        su, bypass = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).one()
    assert not su and not bypass, "R8 must run as a role subject to RLS"


def _count_as(app_url: Any, org: str, sql: str, params: dict) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": org})
            return c.execute(text(sql), params).scalar_one()
    finally:
        eng.dispose()


def _seed_mailbox(admin_engine: Any, *, org: str, owner: str) -> str:
    """An org's mailbox, a lead status and an armed cursor, as the admin."""
    now = datetime.now(UTC)
    with admin_engine.begin() as c:
        account_id = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, organization_id) "
            "VALUES (:u, 'microsoft', :u, 'x', true, CAST(:o AS uuid)) "
            "RETURNING id"), {"u": owner, "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO crm_lead_statuses (name, position, type, is_default, "
            "organization_id) VALUES (:n, 1, 'open', true, CAST(:o AS uuid))"),
            {"n": f"New {uuid.uuid4().hex[:6]}", "o": org})
        c.execute(text(
            "INSERT INTO crm_auto_lead_cursors (account_id, activated_at, "
            "processed_watermark, last_run_at, organization_id) VALUES "
            "(CAST(:a AS uuid), :act, :act, :run, CAST(:o AS uuid))"),
            {"a": account_id, "act": now - timedelta(minutes=30),
             "run": now - timedelta(minutes=5), "o": org})
    return account_id


def _seed_inbound(admin_engine: Any, *, org: str, account_id: str,
                  sender: str) -> None:
    """One classified inbox message from a stranger, as the admin."""
    now = datetime.now(UTC)
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "folder, subject, from_address, to_addresses, received_at, "
            "rules_processed_at, organization_id) VALUES (CAST(:a AS uuid), "
            ":pm, 'inbox', 'Quote for 40 printers', CAST(:f AS jsonb), "
            "'[]'::jsonb, :recv, :proc, CAST(:o AS uuid))"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex}",
             "f": json.dumps({"email": sender, "name": "Pat Prospect"}),
             "recv": now - timedelta(minutes=10),
             "proc": now - timedelta(minutes=9), "o": org})


@asynccontextmanager
async def _bound_as_app(promoted: Any, org: str) -> Any:  # noqa: F811
    """The shared engine on the app role, with *org* bound for the block."""
    app_dsn = promoted.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            yield
    finally:
        release_tenant(token)


# ── R8: auto-lead writes into the mailbox's tenant ──────────────────────────


@_DB_GATE
class TestAutoLeadUnderRls:

    async def test_an_org_b_mailbox_writes_an_org_b_lead(
        self, promoted: Any, app_engine: Any,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _assert_non_priv(app_engine)
        p = promoted
        sender = f"pat-{uuid.uuid4().hex[:8]}@prospect-b.test"
        account_id = _seed_mailbox(p.admin_engine, org=p.org_b,
                                   owner="owner@crm-t3-b.test")
        _seed_inbound(p.admin_engine, org=p.org_b, account_id=account_id,
                      sender=sender)

        async with _bound_as_app(p, p.org_b):
            stats = await auto_lead.create_leads_from_new_mail(account_id)

        assert stats["created"] == 1, f"no lead was written: {stats}"
        assert stats["activity_errors"] == 0, stats
        with p.admin_engine.connect() as c:
            lead = c.execute(text(
                "SELECT id::text, organization_id::text FROM crm_leads "
                "WHERE email = :e"), {"e": sender}).one()
            activity_org = c.execute(text(
                "SELECT organization_id::text FROM crm_activities "
                "WHERE lead_id = CAST(:l AS uuid)"), {"l": lead[0]}).scalar_one()
            watermark = c.execute(text(
                "SELECT processed_watermark > activated_at FROM "
                "crm_auto_lead_cursors WHERE account_id = CAST(:a AS uuid)"),
                {"a": account_id}).scalar_one()
        assert lead[1] == p.org_b, "the lead landed outside the mailbox's org"
        assert activity_org == p.org_b
        assert watermark is True, "the cursor did not advance under RLS"

        sql = "SELECT count(*) FROM crm_leads WHERE email = :e"
        assert _count_as(p.app_url, p.org_a, sql, {"e": sender}) == 0, (
            "org A reads org B's auto-lead"
        )
        assert _count_as(p.app_url, p.org_b, sql, {"e": sender}) == 1

    async def test_with_org_a_bound_an_org_b_mailbox_writes_nothing(
        self, promoted: Any, app_engine: Any,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _assert_non_priv(app_engine)
        p = promoted
        sender = f"pat-{uuid.uuid4().hex[:8]}@prospect-b.test"
        account_id = _seed_mailbox(p.admin_engine, org=p.org_b,
                                   owner="owner2@crm-t3-b.test")
        _seed_inbound(p.admin_engine, org=p.org_b, account_id=account_id,
                      sender=sender)
        log = _Log()
        monkeypatch.setattr(auto_lead, "_log", log)

        async with _bound_as_app(p, p.org_a):
            stats = await auto_lead.create_leads_from_new_mail(account_id)

        assert stats["created"] == 0 and stats["candidates"] == 0
        assert "crm.auto_lead_account_unavailable" in log.names()
        with p.admin_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM crm_leads "
                               "WHERE email = :e"), {"e": sender}).scalar_one()
        assert n == 0, "a run bound to org A wrote a lead for org B's mail"


# ── R8: the approval handler records only in the bound tenant ───────────────


def _seed_company(admin_engine: Any, *, org: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_companies (name, zoho_id, zoho_dirty, "
            "organization_id) VALUES (:n, :z, true, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"n": f"Acme {uuid.uuid4().hex[:6]}",
             "z": f"z-{uuid.uuid4().hex[:8]}", "o": org}).scalar_one())


@_DB_GATE
class TestTheApprovalHandlerUnderRls:

    async def test_bound_to_a_it_refuses_an_org_b_row_with_no_provider_call(
        self, promoted: Any, app_engine: Any,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _assert_non_priv(app_engine)
        p = promoted
        row_b = _seed_company(p.admin_engine, org=p.org_b)
        push = _WatchedPush([0])
        monkeypatch.setattr(crm_sync, "execute_push", push)

        async with _bound_as_app(p, p.org_a):
            with pytest.raises(RuntimeError, match="not in this tenant"):
                await crm_broker._handle_crm_zoho_write(_proposal(
                    table=ORGANIZATIONS.table, native_id=row_b))

        assert push.calls == [], "a proposal naming org B's row reached Zoho"
        with p.admin_engine.connect() as c:
            dirty = c.execute(text(
                "SELECT zoho_dirty FROM crm_companies WHERE id = "
                "CAST(:i AS uuid)"), {"i": row_b}).scalar_one()
        assert dirty is True, "org B's row changed under org A's approval"

    async def test_bound_to_b_it_pushes_and_records_on_the_org_b_row(
        self, promoted: Any, app_engine: Any,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _assert_non_priv(app_engine)
        p = promoted
        row_b = _seed_company(p.admin_engine, org=p.org_b)
        push = _WatchedPush([0])
        monkeypatch.setattr(crm_sync, "execute_push", push)

        async with _bound_as_app(p, p.org_b):
            await crm_broker._handle_crm_zoho_write(_proposal(
                table=ORGANIZATIONS.table, native_id=row_b))

        assert len(push.calls) == 1
        with p.admin_engine.connect() as c:
            dirty, synced = c.execute(text(
                "SELECT zoho_dirty, zoho_synced_at IS NOT NULL FROM "
                "crm_companies WHERE id = CAST(:i AS uuid)"),
                {"i": row_b}).one()
        assert dirty is False and synced is True, (
            "the approved push did not record its result on org B's row"
        )
