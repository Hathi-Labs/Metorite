"""EM-T1b-2 — the ten automation jobs that the sync pipeline reaches bind a
tenant, in phases, with no commit part way.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.2, EM-T1b-2.

Each of the ten functions opens ``_tenant_session()`` with the AMBIENT tenant.
The sync loop binds it (EM-T1b-1), and a request binds it for the request
jobs. None of them calls ``commit()``: a commit inside a block ends
``SET LOCAL``, and each statement after it runs with no tenant.

**Hermetic.**

* An AST fence: no ``get_db()`` and no ``.commit()`` in any of the ten, keyed
  by file and function, and each one opens ``_tenant_session``. A companion
  test proves that the fence can fail.
* With no tenant bound, each function opens no session, calls no provider and
  writes nothing.
* A FastAPI BackgroundTask sees ``current_tenant()`` equal to the organization
  of the request, under ``TenantScopeMiddleware``. That is why the request
  jobs can use the ambient tenant.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the non-privileged role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS). Five families, each run for
organization B. In each one, the write that came after a former commit part
way lands in B, and organization A reads none of the rows of B. The provider
and the model calls are fakes.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_automation_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import db as common_db
from acb_common.db import bind_tenant, clear_tenant, current_tenant, release_tenant
from fastapi import BackgroundTasks
from gateway.routes.email import core as email_core
from gateway.routes.email import digest as digest_mod
from gateway.routes.email.automation import cleanup as cleanup_mod
from gateway.routes.email.automation import engine as engine_mod
from gateway.routes.email.automation import followups as followups_mod
from gateway.routes.email.automation import replyzero as replyzero_mod
from gateway.routes.email.automation import runner as runner_mod
from gateway.routes.email.automation import senders as senders_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _count_as,
    _seed_account,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_EMAIL = (Path(__file__).resolve().parents[2]
          / "apps/services/gateway/gateway/routes/email")

#: The ten functions of EM-T1b-2, keyed by file. The R7 fence
#: ``email-automation-no-mid-session-commit`` reads exactly this table.
THE_TEN: dict[str, tuple[str, ...]] = {
    "automation/runner.py": ("_run_rules_job",),
    "automation/cleanup.py": ("sweep_uncategorized",),
    "automation/senders.py": (
        "_categorize_senders_job", "_maybe_auto_archive",
        "_bulk_reconcile_provider",
    ),
    "automation/replyzero.py": (
        "_maybe_classify_threads", "_mark_thread_replied",
        "apply_thread_status_correction",
    ),
    "digest.py": ("_maybe_send_digest",),
    "automation/followups.py": ("_maybe_send_follow_up_reminders",),
}


# ── hermetic: the AST fence ─────────────────────────────────────────────────


def _call_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _function(source: str, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name} is gone — the fence went blind")


def _violations(source: str, name: str) -> list[tuple[int, str]]:
    """Each ``get_db()`` call and each ``.commit()`` call inside one function."""
    hits: list[tuple[int, str]] = []
    for inner in ast.walk(_function(source, name)):
        if not isinstance(inner, ast.Call):
            continue
        called = _call_name(inner)
        if called.endswith("get_db"):
            hits.append((inner.lineno, "get_db"))
        elif isinstance(inner.func, ast.Attribute) and called == "commit":
            hits.append((inner.lineno, "commit"))
    return hits


def _opens_tenant_session(source: str, name: str) -> bool:
    return any(
        isinstance(inner, ast.Call) and _call_name(inner).endswith("tenant_session")
        for inner in ast.walk(_function(source, name))
    )


@pytest.mark.parametrize(
    ("rel", "name"),
    [(rel, name) for rel, names in THE_TEN.items() for name in names],
)
def test_no_unbound_session_and_no_commit_in_the_ten(rel: str, name: str):
    """R7 fence ``email-automation-no-mid-session-commit``."""
    source = (_EMAIL / rel).read_text(encoding="utf-8")
    hits = _violations(source, name)
    assert hits == [], (
        f"{rel}::{name} calls {hits}. A get_db() session binds no tenant, and "
        "a commit inside a tenant_session ends SET LOCAL, so each statement "
        "after it runs with no tenant. Open another _tenant_session() block "
        "instead (spec §10.4.2, mechanism (A))."
    )
    assert _opens_tenant_session(source, name), (
        f"{rel}::{name} opens no _tenant_session — the fence would pass on a "
        "function that reads nothing"
    )


def test_the_fence_can_fail():
    planted = (
        "async def job(account_id):\n"
        "    db = await _get_db()\n"
        "    await db.commit()\n"
        "    async with _tenant_session() as db:\n"
        "        await db.execute(x)\n"
        "        await db.commit()\n"
        "\n"
        "async def clean(account_id):\n"
        "    async with _tenant_session() as db:\n"
        "        await db.execute(x)\n"
    )
    assert _violations(planted, "job") == [
        (2, "get_db"), (3, "commit"), (6, "commit")]
    assert _violations(planted, "clean") == []
    assert _opens_tenant_session(planted, "clean")
    with pytest.raises(AssertionError, match="went blind"):
        _violations(planted, "renamed")


def test_the_table_names_ten_functions():
    assert sum(len(v) for v in THE_TEN.values()) == 10


# ── hermetic: fail closed with no tenant ────────────────────────────────────


@pytest.fixture()
def unbound():
    """Run the test with NO tenant bound, whatever an earlier test left."""
    token = clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _tripwire(opened: list[str], what: str):
    def _hit(*_a, **_k):
        opened.append(what)
        raise AssertionError(f"{what} ran with no tenant bound")
    return _hit


_UNBOUND_CALLS = {
    "_run_rules_job": lambda: runner_mod._run_rules_job(
        "acc-1", 50, False, "scheduler"),
    "sweep_uncategorized": lambda: cleanup_mod.sweep_uncategorized(
        "acc-1", 100, dry_run=False, owner="scheduler", max_apply=100),
    "_categorize_senders_job": lambda: senders_mod._categorize_senders_job(
        "acc-1", 25),
    "_maybe_auto_archive": lambda: senders_mod._maybe_auto_archive("acc-1"),
    "_bulk_reconcile_provider": lambda: senders_mod._bulk_reconcile_provider(
        "acc-1", ["pm-1"], "archive"),
    "_maybe_classify_threads": lambda: replyzero_mod._maybe_classify_threads(
        "acc-1"),
    "_mark_thread_replied": lambda: replyzero_mod._mark_thread_replied(
        "acc-1", "t-1"),
    "apply_thread_status_correction":
        lambda: replyzero_mod.apply_thread_status_correction(
            "acc-1", "t-1", "DONE"),
    "_maybe_send_digest": lambda: digest_mod._maybe_send_digest("acc-1"),
    "_maybe_send_follow_up_reminders":
        lambda: followups_mod._maybe_send_follow_up_reminders("acc-1"),
}


def test_every_function_of_the_ten_has_an_unbound_case():
    assert set(_UNBOUND_CALLS) == {
        n for names in THE_TEN.values() for n in names}


@pytest.mark.parametrize("name", sorted(_UNBOUND_CALLS))
async def test_with_no_tenant_each_function_opens_no_session(
    name, monkeypatch, unbound,
):
    """The REAL ``tenant_session`` runs here. It raises ``TenantUnbound``
    before it asks the factory for a session, and the broad handler of each
    function logs that. A session factory, a ``get_db`` or a provider that
    runs is a failure."""
    opened: list[str] = []
    monkeypatch.setattr(common_db, "get_session_factory",
                        _tripwire(opened, "get_session_factory"))
    for mod in (runner_mod, cleanup_mod, senders_mod, replyzero_mod,
                followups_mod, digest_mod, email_core):
        monkeypatch.setattr(mod, "_get_db", _tripwire(opened, "_get_db"),
                            raising=False)
        monkeypatch.setattr(mod, "_instantiate_provider",
                            _tripwire(opened, "_instantiate_provider"),
                            raising=False)
    assert current_tenant() is None
    result = await _UNBOUND_CALLS[name]()
    assert opened == [], f"{name} reached {opened} with no tenant bound"
    if name == "sweep_uncategorized":
        assert "no tenant bound" in result["error"]
        assert result["categorized"] == 0
    if name == "apply_thread_status_correction":
        assert result == {"ok": False}
    if name == "_maybe_send_follow_up_reminders":
        assert result == {"configured": False, "scanned": 0,
                          "labeled": 0, "drafted": 0}


# ── hermetic: a request job keeps the organization of its request ──────────


ORG = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def identity(monkeypatch):
    """Resolve every email to (user-id, ORG) without a database."""
    import acb_auth.deps as deps
    from acb_auth.permissions import NO_ACCESS

    async def fake_access(email, legacy_role=None, record_request=False):
        return NO_ACCESS

    async def fake_identity(email):
        return ("22222222-2222-2222-2222-222222222222", ORG)

    monkeypatch.setattr(deps, "resolve_access", fake_access)
    monkeypatch.setattr(deps, "resolve_identity", fake_identity)
    monkeypatch.setattr(deps, "_get_internal_token", lambda: "tok")
    return deps


def test_a_background_task_sees_the_organization_of_its_request(
    identity, monkeypatch,
):
    """The dual callers of five of the ten are FastAPI BackgroundTasks. Their
    sessions take the ambient tenant, so the task must run inside the scope
    of its request: after the response, before the middleware releases it.

    The second task is a REAL job of the ten. Its ``_tenant_session`` double
    records the tenant that is bound when the block opens.

    Sync (not ``async def``) on purpose: ``TestClient`` runs its own loop and
    cannot be driven from inside one. ``BackgroundTasks`` is imported at module
    level, because this module defers its annotations and FastAPI resolves
    them against the module globals.
    """
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from gateway.main import TenantScopeMiddleware

    seen: dict[str, object] = {}
    opened_under: list[str | None] = []

    class _NoRows:
        async def execute(self, *_a, **_k):
            class _R:
                def fetchall(self):
                    return []
            return _R()

    @asynccontextmanager
    async def _recording_session():
        opened_under.append(current_tenant())
        yield _NoRows()

    monkeypatch.setattr(senders_mod, "_tenant_session", _recording_session)

    async def _job():
        seen["job"] = current_tenant()

    app = FastAPI()
    app.add_middleware(TenantScopeMiddleware)

    @app.post("/job")
    async def start(background: BackgroundTasks,
                    user=Depends(identity.get_current_user)):
        seen["request"] = current_tenant()
        background.add_task(_job)
        background.add_task(senders_mod._categorize_senders_job, "acc-1", 25)
        return {"scheduled": True}

    token = clear_tenant()
    try:
        with TestClient(app) as client:
            answer = client.post("/job", headers={
                "X-User-Email": "priya@fracktal.in",
                "Authorization": "Bearer tok",
            })
    finally:
        release_tenant(token)

    assert answer.status_code == 200
    assert seen == {"request": ORG, "job": ORG}
    assert opened_under == [ORG], (
        "the converted job opened its block with no tenant, or did not run"
    )


# ── R8 helpers ──────────────────────────────────────────────────────────────


class _Store:
    def decrypt(self, raw: str) -> str:
        return json.dumps({"access_token": "at"})

    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


class _FakeProvider:
    """A provider that authenticates, rotates its credentials and records
    each call. ``bulk_apply`` never succeeds, so the reconcile reverts."""

    def __init__(self, on_bulk=None) -> None:
        self.calls: list[str] = []
        self._on_bulk = on_bulk

    async def authenticate(self) -> bool:
        self.calls.append("authenticate")
        return True

    def credentials_dirty(self) -> bool:
        return True

    def export_credentials(self) -> dict:
        return {"access_token": "rotated"}

    async def set_labels(self, pmid, add=None, remove=None):
        self.calls.append("set_labels")

    async def send_message(self, **_kw):
        self.calls.append("send_message")

    async def create_draft(self, **_kw):
        self.calls.append("create_draft")

    async def bulk_apply(self, pmids, action, failed_out=None):
        self.calls.append("bulk_apply")
        if self._on_bulk is not None:
            self._on_bulk()
        if failed_out is not None:
            failed_out.extend(pmids)
        return {}


def _seed_message(admin, *, org: str, account_id: str, folder: str = "inbox",
                  sender: str = "s@sender.test", thread_id: str | None = None,
                  categories: list[str] | None = None,
                  unsubscribe: str | None = None,
                  received_at: datetime | None = None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "body_text, snippet, received_at, categories, unsubscribe_link, "
            "organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'hello', 'body', 'body', :rcv, "
            "CAST(:cats AS text[]), :unsub, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "tid": thread_id, "f": folder,
             "frm": json.dumps({"email": sender, "name": "S"}),
             "to": json.dumps([{"email": "to@ext-em-t1b2.test"}]),
             "rcv": received_at or datetime.now(UTC),
             "cats": categories or [], "unsub": unsubscribe,
             "o": org}).scalar_one())


def _seed_settings(admin, *, org: str, account_id: str, **cols) -> None:
    names = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    with admin.begin() as c:
        c.execute(text(
            f"INSERT INTO email_assistant_settings (account_id, organization_id"
            f"{', ' if cols else ''}{names}) VALUES (CAST(:aid AS uuid), "
            f"CAST(:org AS uuid){', ' if cols else ''}{binds})"),
            {"aid": account_id, "org": org, **cols})


@contextmanager
def _bound(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _patch_providers(monkeypatch, provider) -> None:
    from acb_llm import key_store

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    for mod in (runner_mod, replyzero_mod, followups_mod, email_core):
        monkeypatch.setattr(mod, "_instantiate_provider",
                            lambda name, creds: provider)


def _scalar(admin, sql: str, params: dict):
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar()


def _rows(admin, sql: str, params: dict) -> list:
    with admin.connect() as c:
        return list(c.execute(text(sql), params).mappings().all())


_BY_ACCOUNT = "WHERE account_id = CAST(:a AS uuid)"


def _isolated(p, table: str, account_id: str, *, expect_b: int) -> None:
    """Org B reads ``expect_b`` rows of ``table`` for the account, and org A
    reads none of them."""
    sql = f"SELECT count(*) FROM {table} {_BY_ACCOUNT}"
    params = {"a": account_id}
    assert _count_as(p.app_url, p.org_b, sql, params) == expect_b
    assert _count_as(p.app_url, p.org_a, sql, params) == 0, (
        f"org A read the {table} rows of org B"
    )


# ── R8: the five families ───────────────────────────────────────────────────


@_DB_GATE
class TestTheAutomationJobsWriteTheirOwnTenant:

    async def test_runner_stamps_each_row_and_logs_it_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The per-row commit is gone. The SECOND row's block is the write
        that came after a former commit, and it lands in B."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        m1 = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                           thread_id=f"t-r1-{acc}")
        m2 = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                           thread_id=f"t-r2-{acc}",
                           received_at=datetime.now(UTC) - timedelta(hours=1))
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        monkeypatch.setattr(runner_mod, "classify_matches",
                            AsyncMock(return_value=[]))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")

        stamped = _rows(p.admin_engine,
                        "SELECT id::text AS id, rules_processed_at FROM "
                        f"email_messages {_BY_ACCOUNT}", {"a": acc})
        assert {r["id"] for r in stamped} == {m1, m2}
        assert all(r["rules_processed_at"] is not None for r in stamped), (
            "a row of org B was left unstamped — its block ran with no tenant"
        )
        logs = _rows(p.admin_engine,
                     "SELECT message_id::text AS mid, status, "
                     "organization_id::text AS org FROM email_executed_rules "
                     f"{_BY_ACCOUNT}", {"a": acc})
        assert sorted(r["mid"] for r in logs) == sorted([m1, m2])
        assert {r["org"] for r in logs} == {p.org_b}
        assert {r["status"] for r in logs} == {"SKIPPED"}
        creds = _scalar(p.admin_engine,
                        "SELECT credentials_encrypted FROM email_accounts "
                        "WHERE id = CAST(:a AS uuid)", {"a": acc})
        assert creds.startswith("enc:"), "the rotated credentials did not land"
        assert provider.calls[0] == "authenticate"
        _isolated(p, "email_executed_rules", acc, expect_b=2)

    async def test_cleanup_applies_two_decided_messages_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        for _ in range(2):
            _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                          sender="news@shop-em-t1b2.test",
                          unsubscribe="https://shop-em-t1b2.test/unsub")
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                res = await cleanup_mod.sweep_uncategorized(
                    acc, 100, dry_run=False, owner="scheduler", max_apply=100)

        assert "error" not in res, res
        assert res["categorized"] == 2 and res["failed"] == 0, res
        logs = _rows(p.admin_engine,
                     "SELECT rule_name, organization_id::text AS org FROM "
                     f"email_executed_rules {_BY_ACCOUNT}", {"a": acc})
        assert len(logs) == 2
        assert {r["org"] for r in logs} == {p.org_b}
        assert {r["rule_name"] for r in logs} == {"Email Cleaner · Newsletter"}
        cats = _rows(p.admin_engine,
                     f"SELECT categories FROM email_messages {_BY_ACCOUNT}",
                     {"a": acc})
        assert all("Newsletter" in r["categories"] for r in cats)
        assert provider.calls.count("set_labels") == 2
        _isolated(p, "email_executed_rules", acc, expect_b=2)

    async def test_senders_categorize_writes_both_phases_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The `inferred` delete is the second block — the write that came
        after the former commit."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        for _ in range(3):
            _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                          sender="promo@shop-em-t1b2.test",
                          categories=["Newsletter"])
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_senders (account_id, email, category, "
                "category_source, organization_id) VALUES (CAST(:a AS uuid), "
                "'old@guess-em-t1b2.test', 'Marketing', 'inferred', "
                "CAST(:o AS uuid))"), {"a": acc, "o": p.org_b})
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await senders_mod._categorize_senders_job(acc, 25)

        rows = _rows(p.admin_engine,
                     "SELECT email, category, category_source, "
                     "organization_id::text AS org FROM email_senders "
                     f"{_BY_ACCOUNT}", {"a": acc})
        assert rows == [{"email": "promo@shop-em-t1b2.test",
                         "category": "Newsletter", "category_source": "rule",
                         "org": p.org_b}], (
            f"categorize left {rows}: the projection or the inferred delete "
            "did not land in org B"
        )
        _isolated(p, "email_senders", acc, expect_b=1)

    async def test_a_failed_auto_archive_reverts_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The folder UPDATE commits with its block BEFORE the reconcile
        starts, and the revert is the write after that former commit."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                            sender="auto@news-em-t1b2.test")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_newsletters (account_id, email, status, "
                "organization_id) VALUES (CAST(:a AS uuid), "
                "'auto@news-em-t1b2.test', 'AUTO_ARCHIVED', CAST(:o AS uuid))"),
                {"a": acc, "o": p.org_b})
        folder_sql = "SELECT folder FROM email_messages WHERE id = CAST(:m AS uuid)"
        during: list[str] = []
        provider = _FakeProvider(on_bulk=lambda: during.append(
            _scalar(p.admin_engine, folder_sql, {"m": mid})))
        _patch_providers(monkeypatch, provider)
        monkeypatch.setattr(senders_mod.asyncio, "sleep", AsyncMock())
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await senders_mod._maybe_auto_archive(acc)

        assert during and set(during) == {"archive"}, (
            f"the provider saw folder {during}: the archive did not commit in "
            "org B before the reconcile"
        )
        assert len(during) == senders_mod._RECONCILE_ATTEMPTS
        assert _scalar(p.admin_engine, folder_sql, {"m": mid}) == "inbox", (
            "the failed archive was not reverted — the revert ran unbound"
        )
        _isolated(p, "email_messages", acc, expect_b=1)

    async def test_replyzero_writes_filed_gap_and_correction_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        filed_tid, gap_tid = f"t-filed-{acc}", f"t-gap-{acc}"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      folder="archive", sender="x@ext-em-t1b2.test",
                      thread_id=filed_tid)
        gap_mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                folder="inbox", sender="y@ext-em-t1b2.test",
                                thread_id=gap_tid, categories=["Reply"])
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        monkeypatch.setattr(engine_mod, "classify_matches",
                            AsyncMock(return_value=[]))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await replyzero_mod._maybe_classify_threads(acc)
                status_after_backfill = _rows(
                    p.admin_engine,
                    "SELECT thread_id, status, organization_id::text AS org "
                    f"FROM email_thread_status {_BY_ACCOUNT} "
                    "ORDER BY thread_id", {"a": acc})
                fixed = await replyzero_mod.apply_thread_status_correction(
                    acc, gap_tid, "DONE")

        assert status_after_backfill == [
            {"thread_id": filed_tid, "status": "FYI", "org": p.org_b},
            {"thread_id": gap_tid, "status": "FYI", "org": p.org_b},
        ], status_after_backfill
        assert fixed == {"ok": True, "status": "DONE", "label": "Done"}
        status = _scalar(p.admin_engine,
                         "SELECT status FROM email_thread_status "
                         f"{_BY_ACCOUNT} AND thread_id = :t",
                         {"a": acc, "t": gap_tid})
        assert status == "DONE"
        # The label swap is the write after the former commit.
        cats = _scalar(p.admin_engine,
                       "SELECT categories FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": gap_mid})
        assert "Reply" not in cats and "Done" in cats, cats
        _isolated(p, "email_thread_status", acc, expect_b=2)

    async def test_digest_and_followups_stamp_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc,
                       digest_frequency="DAILY", digest_time_of_day="00:00",
                       follow_up_awaiting_days=1, follow_up_auto_draft=False)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      sender="new@ext-em-t1b2.test")
        sent_mid = _seed_message(
            p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
            sender="b@em-t1b2.test", thread_id=f"t-fu-{acc}",
            received_at=datetime.now(UTC) - timedelta(days=5))
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, "
                "status, last_message_id, last_message_at, reason, "
                "organization_id) VALUES (CAST(:a AS uuid), :t, 'AWAITING', "
                "CAST(:m AS uuid), :at, 'seed', CAST(:o AS uuid))"),
                {"a": acc, "t": f"t-fu-{acc}", "m": sent_mid,
                 "at": datetime.now(UTC) - timedelta(days=5), "o": p.org_b})
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await digest_mod._maybe_send_digest(acc)
                result = await followups_mod._maybe_send_follow_up_reminders(acc)

        assert provider.calls.count("send_message") == 1
        stamped = _rows(p.admin_engine,
                        "SELECT last_digest_at, organization_id::text AS org "
                        f"FROM email_assistant_settings {_BY_ACCOUNT}",
                        {"a": acc})
        assert len(stamped) == 1 and stamped[0]["last_digest_at"] is not None, (
            "last_digest_at did not land — the digest block ran unbound"
        )
        assert stamped[0]["org"] == p.org_b
        assert result == {"configured": True, "scanned": 1, "labeled": 1,
                          "drafted": 0}, result
        reminded = _scalar(p.admin_engine,
                           "SELECT follow_up_reminded_at FROM "
                           f"email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert reminded is not None, "follow_up_reminded_at did not land in B"
        _isolated(p, "email_assistant_settings", acc, expect_b=1)
        _isolated(p, "email_thread_status", acc, expect_b=1)


# ── fix round 1: hermetic ───────────────────────────────────────────────────


def _open_count_session(state: dict, db):
    """A ``_tenant_session`` double that counts the blocks open right now."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ts():
        state["open"] += 1
        try:
            yield db
        finally:
            state["open"] -= 1
    return _ts


async def test_the_sweep_holds_no_session_across_set_labels(monkeypatch):
    """R-b. The provider label call of each swept message runs with NO
    session open. Each item then writes the mirror and the audit row in its
    own block, so no pooled connection sits `idle in transaction` across Graph
    I/O."""
    from types import SimpleNamespace

    state = {"open": 0}
    seen: list[int] = []
    writes: list[str] = []

    class _Db:
        async def execute(self, clause, params=None):
            writes.append(str(clause).split()[0])

    class _Provider:
        async def authenticate(self):
            return True

        async def set_labels(self, pmid, add=None, remove=None):
            seen.append(state["open"])

        def credentials_dirty(self):
            return False

    rows = [SimpleNamespace(id=f"m{i}", provider_message_id=f"p-m{i}",
                            subject="Hi", received_at=None,
                            from_address={"email": "news@site.com"})
            for i in (1, 2)]

    async def _page(db, aid, limit, offset=0, internal=frozenset()):
        return rows if offset == 0 else []

    provider = _Provider()
    monkeypatch.setattr(cleanup_mod, "_tenant_session",
                        _open_count_session(state, _Db()))
    monkeypatch.setattr(cleanup_mod, "_provider_for_account_any",
                        AsyncMock(return_value=(provider, None, "o@x.test")))
    monkeypatch.setattr(cleanup_mod, "_uncategorized_inbox", _page)
    monkeypatch.setattr(cleanup_mod, "_load_rule_patterns",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(cleanup_mod, "_rule_label_by_id",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(cleanup_mod, "_label_tallies", AsyncMock(
        return_value=({"news@site.com": {"Newsletter": 6}}, {})))
    monkeypatch.setattr(cleanup_mod, "_internal_domains",
                        AsyncMock(return_value=frozenset()))
    res = await cleanup_mod.sweep_uncategorized(
        "acc-1", 100, dry_run=False, owner="scheduler")

    assert res["categorized"] == 2 and res["failed"] == 0, res
    assert seen == [0, 0], f"a session was open during set_labels: {seen}"
    assert writes.count("UPDATE") == 2 and writes.count("INSERT") == 2


async def test_a_failed_filed_write_does_not_skip_the_sent_threads(monkeypatch):
    """The filed phase has its own handler. One failed FYI write must not
    skip the `_mark_thread_replied` calls of the same cycle."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    def _row(tid, folder):
        return SimpleNamespace(
            thread_id=tid, id=f"m-{tid}", subject="S",
            from_address={"email": "a@b.test"}, to_addresses=[],
            cc_addresses=[], body_text="", snippet="", folder=folder,
            received_at=None)

    def _res(*, one=None, many=None):
        r = MagicMock()
        r.fetchone.return_value = one
        r.fetchall.return_value = many or []
        return r

    db = AsyncMock()
    db.execute.side_effect = [
        _res(many=[_row("t-filed", "archive"), _row("t-sent", "sent")]),
        _res(many=[]),
        _res(one=SimpleNamespace(email_address="me@x.test")),
        _res(one=None),
    ]
    from tests.unit._email_fakes import bind_db

    mark = AsyncMock()
    monkeypatch.setattr(replyzero_mod, "_tenant_session", bind_db(db))
    monkeypatch.setattr(replyzero_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(replyzero_mod, "_upsert_thread_status",
                        AsyncMock(side_effect=RuntimeError("db blip")))
    monkeypatch.setattr(replyzero_mod, "_mark_thread_replied", mark)
    await replyzero_mod._maybe_classify_threads("acc-1")
    mark.assert_awaited_once_with("acc-1", "t-sent")


async def test_the_session_double_rolls_back_a_block_that_raised():
    """`bind_db` mirrors the real seam: a block that raised commits nothing
    and awaits the fake's `rollback`, so a hermetic test cannot read an
    aborted block as committed."""
    from tests.unit._email_fakes import bind_db

    db = AsyncMock()
    session = bind_db(db)
    with pytest.raises(RuntimeError):
        async with session():
            raise RuntimeError("statement failed")
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    assert session.rolled_back == [1]
    async with session():
        pass
    db.commit.assert_awaited_once()


# ── fix round 1: R8 ─────────────────────────────────────────────────────────


@_DB_GATE
class TestFixRoundOne:

    async def test_a_savepoint_rollback_keeps_the_tenant(
        self, promoted, app_engine,  # noqa: F811
    ):
        """`ROLLBACK TO SAVEPOINT` keeps the `SET LOCAL` tenant that the seam
        set before the savepoint, on the real seam as the non-owner role."""
        from gateway.routes.email.core import _savepoint

        _assert_non_priv(app_engine)
        p = promoted
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn),                 common_db.tenant_session(p.org_b) as db:
            with pytest.raises(Exception):  # noqa: B017
                async with _savepoint(db):
                    await db.execute(text("SELECT 1/0"))
            after = (await db.execute(text(
                "SELECT current_setting('app.tenant_id', true)"))).scalar()
        assert after == p.org_b

    async def test_one_failed_item_leaves_the_other_items_of_the_sweep(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Per-item isolation. Item 2's block fails on a REAL statement. Item 1
        still persists, and the counts are honest. If the items shared one
        block, the failure would abort it and item 1 would be lost."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        mids = [
            _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                          sender="news@shop-em-t1b2.test",
                          unsubscribe="https://shop-em-t1b2.test/unsub",
                          received_at=datetime.now(UTC) - timedelta(minutes=i))
            for i in range(2)
        ]
        real_mirror = runner_mod.mirror_label

        async def _mirror(db, message_id, lbl):
            if message_id == mids[1]:
                await db.execute(text("SELECT 1/0"))
            await real_mirror(db, message_id, lbl)

        _patch_providers(monkeypatch, _FakeProvider())
        monkeypatch.setattr(runner_mod, "mirror_label", _mirror)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                res = await cleanup_mod.sweep_uncategorized(
                    acc, 100, dry_run=False, owner="scheduler", max_apply=100)

        assert res["categorized"] == 1 and res["failed"] == 1, res
        logged = _rows(p.admin_engine,
                       "SELECT message_id::text AS mid FROM "
                       f"email_executed_rules {_BY_ACCOUNT}", {"a": acc})
        assert [r["mid"] for r in logged] == [mids[0]], logged
        cats = {r["id"]: r["categories"] for r in _rows(
            p.admin_engine,
            f"SELECT id::text AS id, categories FROM email_messages {_BY_ACCOUNT}",
            {"a": acc})}
        assert "Newsletter" in cats[mids[0]]
        assert "Newsletter" not in cats[mids[1]]
        _isolated(p, "email_executed_rules", acc, expect_b=1)

    async def test_a_failed_send_leaves_last_digest_at_and_a_good_one_stamps(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The stamp lands only when the send returned. A stamp in a block of
        its own before the send would land on a failed send."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc,
                       digest_frequency="DAILY", digest_time_of_day="00:00")
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      sender="new@ext-em-t1b2.test")

        class _SendFails(_FakeProvider):
            async def send_message(self, **_kw):
                raise RuntimeError("Graph 503")

        stamp_sql = ("SELECT last_digest_at FROM email_assistant_settings "
                     f"{_BY_ACCOUNT}")
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                _patch_providers(monkeypatch, _SendFails())
                await digest_mod._maybe_send_digest(acc)
                assert _scalar(p.admin_engine, stamp_sql, {"a": acc}) is None, (
                    "a failed send stamped last_digest_at"
                )
                good = _FakeProvider()
                _patch_providers(monkeypatch, good)
                await digest_mod._maybe_send_digest(acc)
        assert good.calls.count("send_message") == 1
        assert _scalar(p.admin_engine, stamp_sql, {"a": acc}) is not None

    async def test_a_failed_projection_keeps_the_runner_stamp_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The Reply Zero projection fails on a REAL statement inside the row
        block. Its savepoint rolls back, and `rules_processed_at` still lands
        in B. Without the savepoint the stamp fails on the aborted block."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                            thread_id=f"t-proj-{acc}")

        async def _projection_fails(db, *_a, **_k):
            await db.execute(text("SELECT 1/0"))

        _patch_providers(monkeypatch, _FakeProvider())
        monkeypatch.setattr(runner_mod, "classify_matches",
                            AsyncMock(return_value=[]))
        monkeypatch.setattr(replyzero_mod, "project_reply_status_from_matches",
                            _projection_fails)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")

        stamped = _scalar(p.admin_engine,
                          "SELECT rules_processed_at FROM email_messages "
                          "WHERE id = CAST(:m AS uuid)", {"m": mid})
        assert stamped is not None, (
            "the failed projection aborted the row block, so the stamp was lost"
        )
        _isolated(p, "email_executed_rules", acc, expect_b=1)

    async def test_a_failed_label_mirror_keeps_the_reminder_stamp_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The follow-up label mirror fails on a REAL statement: a NUL byte in
        the label, which Postgres refuses in `text`. The savepoint rolls it
        back, and `follow_up_reminded_at` still lands in B."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t1b2.test")
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc,
                       follow_up_awaiting_days=1, follow_up_auto_draft=False)
        thread = f"t-nul-{acc}"
        sent_mid = _seed_message(
            p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
            sender="b@em-t1b2.test", thread_id=thread,
            received_at=datetime.now(UTC) - timedelta(days=5))
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, "
                "status, last_message_id, last_message_at, reason, "
                "organization_id) VALUES (CAST(:a AS uuid), :t, 'AWAITING', "
                "CAST(:m AS uuid), :at, 'seed', CAST(:o AS uuid))"),
                {"a": acc, "t": thread, "m": sent_mid,
                 "at": datetime.now(UTC) - timedelta(days=5), "o": p.org_b})
        _patch_providers(monkeypatch, _FakeProvider())
        monkeypatch.setattr(followups_mod, "_FOLLOW_UP_LABEL",
                            "Follow-up" + chr(0))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                result = await followups_mod._maybe_send_follow_up_reminders(acc)

        assert result["scanned"] == 1, result
        reminded = _scalar(p.admin_engine,
                           "SELECT follow_up_reminded_at FROM "
                           f"email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert reminded is not None, (
            "the failed label mirror aborted the block, so the stamp was lost"
        )
        cats = _scalar(p.admin_engine,
                       "SELECT categories FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": sent_mid})
        assert not any("Follow-up" in c for c in cats)
