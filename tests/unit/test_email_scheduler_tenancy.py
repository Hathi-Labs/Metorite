"""EM-T1b-1 — the email sync scheduler and the sync core bind a tenant.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.2, EM-T1b-1.

Two halves.

**Hermetic.** The order of the checks, with no database:

* ``_sync_account`` with no organization and no bound tenant raises
  ``TenantUnbound`` and opens no session.
* ``refresh_account_sync`` with no organization raises and starts no loop.
* ``_account_sync_loop`` binds its organization over an inherited one, and
  releases it when the loop ends.
* With ``EMAIL_SYNC_ENABLED`` off, ``start_background_sync`` returns ``{}``
  and opens no session.
* The OAuth callback passes the organization of the verified state.
* An AST fence: no ``.commit()`` inside a ``tenant_session`` block in
  ``scheduler.py``. A commit there ends ``SET LOCAL``, and each statement
  after it runs with no tenant.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the non-privileged role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS):

* ``start_background_sync`` starts exactly the ``sync_enabled`` accounts of
  both organizations, each with its own organization, and
  ``_close_orphaned_syncs`` resets the in-flight rows of both.
* ``_sync_account`` with a fake provider writes ``email_messages`` and
  ``email_sync_log`` rows with the right ``organization_id``, and the other
  organization reads none of them.
* ``mailbox_owner`` with a bound tenant returns the owner under FORCE RLS.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_scheduler_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.db import (
    TenantUnbound,
    bind_tenant,
    clear_tenant,
    current_tenant,
    release_tenant,
)
from email_ingestion.providers.base import EmailAddress, EmailMessage, SyncResult
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_SCHEDULER = _REPO / "apps/services/email_ingestion/email_ingestion/scheduler.py"


@pytest.fixture()
def unbound():
    """Run the test with NO tenant bound, whatever an earlier test left."""
    token = clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _session_tripwire(opened: list[str]):
    """A ``tenant_session`` stand-in that records an open and refuses it."""
    def _ts(*_a, **_k):
        opened.append("tenant_session")
        raise AssertionError("a session opened where none may open")
    return _ts


# ── hermetic: refuse before any write ───────────────────────────────────────


async def test_sync_account_unbound_raises_and_writes_nothing(monkeypatch, unbound):
    opened: list[str] = []
    monkeypatch.setattr(sched, "tenant_session", _session_tripwire(opened))
    with pytest.raises(TenantUnbound):
        await sched._sync_account("acc-1")
    assert opened == [], "an unbound sync opened a session before it refused"


async def test_refresh_account_sync_without_org_raises(monkeypatch, unbound):
    opened: list[str] = []
    monkeypatch.setattr(sched, "tenant_session", _session_tripwire(opened))
    before = dict(sched._scheduler_tasks)
    for missing in (None, ""):
        with pytest.raises(TenantUnbound):
            await sched.refresh_account_sync("acc-1", organization_id=missing)
    with pytest.raises(TenantUnbound):
        await sched.refresh_account_sync("acc-1")
    assert opened == []
    assert sched._scheduler_tasks == before, "a loop started with no organization"


@pytest.mark.parametrize("off", ["false", "0", "no", "off"])
async def test_flag_off_returns_empty_and_opens_no_session(monkeypatch, off):
    opened: list[str] = []

    async def _no_db():
        opened.append("get_db")
        raise AssertionError("the organization read ran with the flag off")

    monkeypatch.setenv("EMAIL_SYNC_ENABLED", off)
    monkeypatch.setattr(sched, "_scheduler_running", False)
    monkeypatch.setattr(sched, "get_db", _no_db)
    monkeypatch.setattr(sched, "tenant_session", _session_tripwire(opened))
    assert await sched.start_background_sync() == {}
    assert opened == []


async def test_the_loop_binds_its_org_over_an_inherited_one(monkeypatch):
    """H4 forbids a forever-loop on a request context. The loop binds its own
    organization, every hook sees it, and ``finally`` releases it."""
    seen: dict[str, object] = {}

    async def _sync(account_id, *, organization_id=None, **_kw):
        seen["sync_arg"] = organization_id
        seen["sync_bound"] = current_tenant()
        return {"synced": 0}

    async def _hook(hook, account_id):
        seen.setdefault("hooks", set()).add(current_tenant())

    async def _interval(account_id, organization_id):
        seen["interval_org"] = organization_id
        return 300

    async def _stop(_secs):
        raise asyncio.CancelledError

    monkeypatch.setattr(sched, "_sync_account", _sync)
    monkeypatch.setattr(sched, "run_hook", _hook)
    monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
    monkeypatch.setattr(sched.asyncio, "sleep", _stop)

    inherited = bind_tenant("org-of-the-request")
    try:
        with pytest.raises(asyncio.CancelledError):
            await sched._account_sync_loop("acc-1", 300, organization_id="org-b")
        assert current_tenant() == "org-of-the-request", (
            "the loop leaked its binding past its own end"
        )
    finally:
        release_tenant(inherited)
    assert seen["sync_arg"] == "org-b"
    assert seen["sync_bound"] == "org-b"
    assert seen["hooks"] == {"org-b"}
    assert seen["interval_org"] == "org-b"


def test_the_oauth_callback_passes_the_org_of_the_state():
    from gateway.routes.email.transport import oauth

    src = inspect.getsource(oauth.oauth_callback)
    assert "refresh_account_sync(account_id, organization_id=org)" in src


def test_the_account_routes_pass_the_org_of_the_session():
    from gateway.routes.email.transport import accounts

    src = inspect.getsource(accounts)
    assert src.count("organization_id=user.organization_id") == 2


# ── hermetic: the AST fence ─────────────────────────────────────────────────


def _commits_inside_tenant_sessions(source: str) -> list[int]:
    """Line numbers of each ``.commit()`` call inside an ``async with`` block
    whose context expression calls ``tenant_session``."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens = False
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call):
                fn = call.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                opens = opens or name.endswith("tenant_session")
        if not opens:
            continue
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "commit"):
                    hits.append(inner.lineno)
    return hits


def test_no_commit_inside_a_tenant_session_in_the_scheduler():
    """R7 fence ``email-scheduler-no-mid-session-commit``."""
    hits = _commits_inside_tenant_sessions(
        _SCHEDULER.read_text(encoding="utf-8"))
    assert hits == [], (
        f"scheduler.py calls .commit() inside a tenant_session at lines {hits}. "
        "A commit ends SET LOCAL, so each statement after it runs with no "
        "tenant. Split the work into another phase instead."
    )


def test_the_commit_fence_is_not_vacuous():
    planted = (
        "async def f(org):\n"
        "    async with tenant_session(org) as db:\n"
        "        await db.execute(x)\n"
        "        await db.commit()\n"
        "    async with other() as db:\n"
        "        await db.commit()\n"
    )
    assert _commits_inside_tenant_sessions(planted) == [4]


def test_the_scheduler_opens_no_engine_of_its_own():
    src = _SCHEDULER.read_text(encoding="utf-8")
    for gone in ("create_async_engine", "_get_db_url", "_connect_args",
                 "NullPool", "async_sessionmaker"):
        assert gone not in src, f"scheduler.py still names {gone}"


# ── R8 helpers ──────────────────────────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin_engine, *, org: str, owner: str, enabled: bool = True,
                  status: str = "idle") -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, "
            "sync_status, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', :e, 120, :s, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t1b.test",
             "e": enabled, "s": status, "o": org}).scalar_one())


def _seed_running_log(admin_engine, *, org: str, account_id: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_sync_log (account_id, started_at, status, "
            "organization_id) VALUES (CAST(:a AS uuid), now(), 'running', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "o": org}).scalar_one())


def _purge(admin_engine, account_ids: list[str]) -> None:
    with admin_engine.begin() as c:
        for aid in account_ids:
            c.execute(text("DELETE FROM email_sync_log WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
            c.execute(text("DELETE FROM email_accounts WHERE id = "
                           "CAST(:a AS uuid)"), {"a": aid})


def _count_as(app_url, org: str | None, sql: str, params: dict) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            return c.execute(text(sql), params).scalar_one()
    finally:
        eng.dispose()


# ── R8: the startup sweep ───────────────────────────────────────────────────


@_DB_GATE
class TestTheSweepBindsPerOrganization:

    async def test_it_starts_the_enabled_accounts_of_both_orgs_with_their_org(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        a_on = _seed_account(p.admin_engine, org=p.org_a, owner="a@em-t1b.test",
                             status="syncing")
        a_off = _seed_account(p.admin_engine, org=p.org_a, owner="a@em-t1b.test",
                              enabled=False)
        b_on = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b.test",
                             status="syncing")
        log_a = _seed_running_log(p.admin_engine, org=p.org_a, account_id=a_on)
        log_b = _seed_running_log(p.admin_engine, org=p.org_b, account_id=b_on)

        started: dict[str, tuple[int, str, str | None]] = {}

        async def _loop(account_id, interval, *, organization_id):
            started[account_id] = (interval, organization_id, current_tenant())

        monkeypatch.setenv("EMAIL_SYNC_ENABLED", "true")
        monkeypatch.setattr(sched, "_scheduler_running", False)
        monkeypatch.setattr(sched, "_account_sync_loop", _loop)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                launched = await sched.start_background_sync()
                await asyncio.gather(*sched._scheduler_tasks.values())
            assert set(launched) == {a_on, b_on}, (
                f"the sweep launched {sorted(launched)}, not exactly the "
                "sync_enabled accounts of both orgs"
            )
            assert started[a_on][1] == p.org_a
            assert started[b_on][1] == p.org_b
            assert launched[a_on] == 120 and launched[b_on] == 120

            # _close_orphaned_syncs ran in each org's own session.
            with p.admin_engine.connect() as c:
                logs = dict(c.execute(text(
                    "SELECT id::text, status FROM email_sync_log "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                    {"a": log_a, "b": log_b}).all())
                statuses = dict(c.execute(text(
                    "SELECT id::text, sync_status FROM email_accounts "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                    {"a": a_on, "b": b_on}).all())
            assert logs == {log_a: "error", log_b: "error"}, (
                "an orphaned sync-log row of one org stayed 'running'"
            )
            assert statuses == {a_on: "idle", b_on: "idle"}
        finally:
            release_tenant(token)
            sched._scheduler_tasks.clear()
            _purge(p.admin_engine, [a_on, a_off, b_on])


_RLS_TABLES = ("email_accounts", "email_sync_log")


@contextmanager
def _rls_off(admin_engine):
    """Turn row security OFF on the sweep's two tables, then restore it.

    This is the catalog without RLS: each per-org read sees every row. Only
    the ``organization_id`` filter of the sweep can bind the right org."""
    with admin_engine.begin() as c:
        for t in _RLS_TABLES:
            c.execute(text(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY"))
            c.execute(text(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY"))
    try:
        yield
    finally:
        with admin_engine.begin() as c:
            for t in _RLS_TABLES:
                c.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
                c.execute(text(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))


@_DB_GATE
class TestTheSweepFiltersOnTheOwnerWithoutRls:
    """Reviewer P1, fix round 1: on a catalog without RLS, an account binds
    ONLY the organization that owns it, and the orphan close of one org
    leaves the rows of another org alone."""

    async def test_each_account_binds_only_its_owning_org(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        a_on = _seed_account(p.admin_engine, org=p.org_a, owner="a@em-t1b.test")
        b_on = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b.test")
        calls: list[tuple[str, str]] = []

        async def _loop(account_id, interval, *, organization_id):
            calls.append((account_id, organization_id))

        monkeypatch.setenv("EMAIL_SYNC_ENABLED", "true")
        monkeypatch.setattr(sched, "_scheduler_running", False)
        monkeypatch.setattr(sched, "_account_sync_loop", _loop)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            with _rls_off(p.admin_engine):
                # The mechanism: with RLS off, org A's session sees org B.
                sql = ("SELECT count(*) FROM email_accounts "
                       "WHERE id = CAST(:b AS uuid)")
                assert _count_as(p.app_url, p.org_a, sql, {"b": b_on}) == 1
                async with tenant_engine_scope(app_dsn):
                    await sched.start_background_sync()
                    await asyncio.gather(*sched._scheduler_tasks.values())
            mine = sorted(c for c in calls if c[0] in (a_on, b_on))
            assert mine == sorted([(a_on, p.org_a), (b_on, p.org_b)]), (
                f"without RLS the sweep bound {mine}; each account must bind "
                "only the organization that owns it, exactly once"
            )
        finally:
            release_tenant(token)
            sched._scheduler_tasks.clear()
            _purge(p.admin_engine, [a_on, b_on])

    async def test_the_orphan_close_of_one_org_leaves_another_alone(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        a_acc = _seed_account(p.admin_engine, org=p.org_a, owner="a@em-t1b.test",
                              status="syncing")
        b_acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b.test",
                              status="syncing")
        log_a = _seed_running_log(p.admin_engine, org=p.org_a, account_id=a_acc)
        log_b = _seed_running_log(p.admin_engine, org=p.org_b, account_id=b_acc)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            with _rls_off(p.admin_engine):
                async with tenant_engine_scope(app_dsn):
                    await sched._sweep_one_org(p.org_a)
            with p.admin_engine.connect() as c:
                logs = dict(c.execute(text(
                    "SELECT id::text, status FROM email_sync_log "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                    {"a": log_a, "b": log_b}).all())
                statuses = dict(c.execute(text(
                    "SELECT id::text, sync_status FROM email_accounts "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                    {"a": a_acc, "b": b_acc}).all())
            assert logs == {log_a: "error", log_b: "running"}, (
                "the orphan close of org A touched a sync-log row of org B"
            )
            assert statuses == {a_acc: "idle", b_acc: "syncing"}
        finally:
            _purge(p.admin_engine, [a_acc, b_acc])


async def test_one_org_failing_does_not_stop_the_sweep(monkeypatch, caplog):
    """Reviewer P2: one organization's failure is logged with its id, and the
    sweep goes on to the next organization."""
    async def _orgs():
        return ["org-1", "org-2", "org-3"]

    async def _sweep(org):
        if org == "org-2":
            raise RuntimeError("org-2 is down")
        return [(f"acc-{org}", 60)]

    started: list[tuple[str, str]] = []

    async def _loop(account_id, interval, *, organization_id):
        started.append((account_id, organization_id))

    monkeypatch.setenv("EMAIL_SYNC_ENABLED", "true")
    monkeypatch.setattr(sched, "_scheduler_running", False)
    monkeypatch.setattr(sched, "_list_organizations", _orgs)
    monkeypatch.setattr(sched, "_sweep_one_org", _sweep)
    monkeypatch.setattr(sched, "_account_sync_loop", _loop)
    try:
        with caplog.at_level("WARNING", logger=sched.logger.name):
            launched = await sched.start_background_sync()
            await asyncio.gather(*sched._scheduler_tasks.values())
    finally:
        sched._scheduler_tasks.clear()
    assert launched == {"acc-org-1": 60, "acc-org-3": 60}
    assert sorted(started) == [("acc-org-1", "org-1"), ("acc-org-3", "org-3")]
    failed = [r.getMessage() for r in caplog.records
              if "email.sync_sweep_org_failed" in r.getMessage()]
    assert len(failed) == 1 and "org-2" in failed[0]


# ── hermetic: no session across the provider calls (item 6(b)) ────────────


class _Result:
    def fetchone(self):
        return SimpleNamespace(
            id="log-1", provider="microsoft", credentials_encrypted="x",
            last_history_id=None, sync_interval_secs=300,
            initial_sync_done=True, categories=[], user_id="o@x.test")

    def fetchall(self):
        return []

    def scalar(self):
        return None


class _Db:
    async def execute(self, *_a, **_k):
        return _Result()

    async def commit(self):
        return None


async def test_no_session_is_open_during_the_provider_calls(monkeypatch):
    """R7 fence ``email-sync-no-session-across-provider-io``.

    The fake provider asserts that no ``tenant_session`` is open while it
    authenticates and while it fetches. The short creds-persist session
    BETWEEN the two calls is allowed, and the provider makes it happen
    (``credentials_dirty`` is True). A failed assertion lands in the error
    path, so the test checks for a clean result."""
    state = {"open": 0, "opens": 0}
    seen: list[tuple[str, int]] = []

    @asynccontextmanager
    async def _ts(org=None):
        state["open"] += 1
        state["opens"] += 1
        try:
            yield _Db()
        finally:
            state["open"] -= 1

    class _Watched(_FakeProvider):
        async def authenticate(self) -> bool:
            seen.append(("authenticate", state["open"]))
            assert state["open"] == 0, "a session is open during authenticate"
            return True

        async def sync_messages(self, **kw) -> SyncResult:
            seen.append(("sync_messages", state["open"]))
            assert state["open"] == 0, "a session is open during sync_messages"
            return await super().sync_messages(**kw)

    async def _upsert(db, account_id, msg):
        return None

    async def _no_hook(*_a, **_k):
        return None

    from acb_llm import key_store

    monkeypatch.setattr(sched, "tenant_session", _ts)
    monkeypatch.setattr(sched, "upsert_message", _upsert)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_hook)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider",
                        lambda name, creds: _Watched([_message(1)]))
    res = await sched._sync_account("acc-1", organization_id="org-1")
    assert "error" not in res, f"the sync failed: {res}"
    assert seen == [("authenticate", 0), ("sync_messages", 0)]
    assert state["open"] == 0
    assert state["opens"] >= 4, "the phases did not each open a session"


# ── R8: the sync core ───────────────────────────────────────────────────────


class _Store:
    def decrypt(self, raw: str) -> str:
        return json.dumps({"access_token": "at"})

    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


class _FakeProvider:
    def __init__(self, messages: list[EmailMessage]) -> None:
        self.messages = messages

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return True  # exercise both credential writes

    def export_credentials(self) -> dict:
        return {"access_token": "rotated"}

    async def sync_messages(self, **_kw) -> SyncResult:
        return SyncResult(messages=self.messages, new_history_id="h-1")

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill in this test")


def _message(i: int) -> EmailMessage:
    return EmailMessage(
        provider_message_id=f"pm-{uuid.uuid4().hex[:10]}-{i}",
        thread_id=f"t-{i}",
        folder="INBOX",
        from_address=EmailAddress(name="S", email="s@sender.test"),
        subject=f"hello {i}",
        body_text="body",
        snippet="body",
        received_at=datetime.now(UTC),
    )


@_DB_GATE
class TestTheSyncCoreWritesItsOwnTenant:

    async def test_a_sync_of_org_b_writes_org_b_rows_org_a_cannot_read(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t1b.test")

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(
            sched, "build_provider",
            lambda name, creds: _FakeProvider([_message(1), _message(2)]))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b)
            assert res == {"synced": 2, "history_id": "h-1"}, res

            with p.admin_engine.connect() as c:
                msg_orgs = c.execute(text(
                    "SELECT DISTINCT organization_id::text FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).scalars().all()
                log = c.execute(text(
                    "SELECT status, messages_synced, organization_id::text AS org "
                    "FROM email_sync_log WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).mappings().all()
                acct = c.execute(text(
                    "SELECT sync_status, initial_sync_done, last_history_id, "
                    "credentials_encrypted FROM email_accounts "
                    "WHERE id = CAST(:a AS uuid)"),
                    {"a": account_id}).mappings().one()
            assert msg_orgs == [p.org_b]
            assert len(log) == 1
            assert log[0]["status"] == "success" and log[0]["org"] == p.org_b
            assert log[0]["messages_synced"] == 2
            assert acct["sync_status"] == "idle"
            assert acct["initial_sync_done"] is True
            assert acct["last_history_id"] == "h-1"
            assert acct["credentials_encrypted"].startswith("enc:")

            msgs = ("SELECT count(*) FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)")
            logs = ("SELECT count(*) FROM email_sync_log "
                    "WHERE account_id = CAST(:a AS uuid)")
            params = {"a": account_id}
            assert _count_as(p.app_url, p.org_b, msgs, params) == 2
            assert _count_as(p.app_url, p.org_b, logs, params) == 1
            assert _count_as(p.app_url, p.org_a, msgs, params) == 0, (
                "org A read the messages of org B"
            )
            assert _count_as(p.app_url, p.org_a, logs, params) == 0
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": account_id})
            _purge(p.admin_engine, [account_id])

    async def test_a_failed_sync_writes_its_error_in_its_own_tenant(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The error path opens a NEW tenant_session(org), so the error
        status and the log row land under FORCE RLS."""
        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_a,
                                   owner="a@em-t1b.test")

        class _Refuses(_FakeProvider):
            async def authenticate(self) -> bool:
                return False

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider",
                            lambda name, creds: _Refuses([]))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_a)
            assert "error" in res
            with p.admin_engine.connect() as c:
                acct = c.execute(text(
                    "SELECT sync_status FROM email_accounts "
                    "WHERE id = CAST(:a AS uuid)"), {"a": account_id}).scalar_one()
                log = c.execute(text(
                    "SELECT status FROM email_sync_log "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).scalars().all()
            assert acct == "error"
            assert log == ["error"]
        finally:
            release_tenant(token)
            _purge(p.admin_engine, [account_id])


# ── R8: the mailbox owner ───────────────────────────────────────────────────


@_DB_GATE
class TestTheMailboxOwnerReadsBound:

    async def test_a_bound_tenant_returns_the_owner_under_force_rls(
        self, promoted, app_engine,  # noqa: F811
    ):
        from gateway.routes.email.scheduler_hooks import mailbox_owner

        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="owner-b@em-t1b.test")
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                token = bind_tenant(p.org_b)
                try:
                    assert await mailbox_owner(account_id) == "owner-b@em-t1b.test"
                finally:
                    release_tenant(token)
                # Another tenant cannot see the mailbox.
                token = bind_tenant(p.org_a)
                try:
                    assert await mailbox_owner(account_id) is None
                finally:
                    release_tenant(token)
                # The mechanism: the unbound discovery read sees nothing.
                token = clear_tenant()
                try:
                    assert await mailbox_owner(account_id) is None
                finally:
                    release_tenant(token)
        finally:
            _purge(p.admin_engine, [account_id])
