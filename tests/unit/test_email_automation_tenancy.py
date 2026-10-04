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

**EM-T4a-2, PR-A** (spec §10.4.6, "EM-T4a-2 — the decision core"). Two more
fences live here, because the scheduler fence cannot see a block in
``routes/email``:

* ``email-decision-core-no-session-across-the-ask``. The model is watched at
  its two leaves, ``acb_llm.decide`` and
  ``acb_llm.context.acompletion_with_fallback``, and each module of
  ``routes/email`` that binds ``_tenant_session`` gets a double that counts
  the open blocks. The status ask of ``_mark_thread_replied`` must see zero
  open blocks, in ``off``, ``shadow`` and ``on``. A companion plants the ask
  inside a block and shows that the fence fails.
* ``email-status-write-guard``. The write after the ask carries the guard of
  item 6 in one statement. R8: a newer stored inbound row of org B voids
  the write in org B, and the job reconciles no labels. A newer row in
  ``sent`` or ``drafts``, a row with no date, a tie, and a row of org A with
  the same account and thread do not void it.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_automation_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm
import acb_llm.context as llm_context
from acb_common import db as common_db
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant, current_tenant, release_tenant
from email_ingestion.llm_cap import LLMBudgetExhausted
from fastapi import BackgroundTasks
from gateway import decide_features as df
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
from tests.unit.test_email_decide_questions import THREAD, FakeDecide

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


def _seed_enabled_rule(admin, *, org: str, account_id: str) -> None:
    """One enabled rule, created a day before any seeded message.

    The AUTOMATIC run (caller "scheduler") and the Reply Zero backfill touch
    only mail received after the first enabled rule of the mailbox was
    created (EM-T5b-2, owner decision (d) of 2026-10-02). The cases below
    need this rule for their inbox rows to be selected."""
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_rules (account_id, name, instructions, enabled, "
            "created_at, organization_id) VALUES (CAST(:a AS uuid), 'Receipt', "
            "'receipts', true, :c, CAST(:o AS uuid))"),
            {"a": account_id, "c": datetime.now(UTC) - timedelta(days=1), "o": org})


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
        _seed_enabled_rule(p.admin_engine, org=p.org_b, account_id=acc)
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
        # The backfill selects an INBOX gap thread only after the first
        # enabled rule (the new-mail floor, EM-T5b-2 fix round 2).
        _seed_enabled_rule(p.admin_engine, org=p.org_b, account_id=acc)
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
    """A ``_tenant_session`` double that counts the blocks open right now.

    EM-T4a-2 extends it: ``state["opens"]``, when the key is present, counts
    each block that opened."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ts():
        state["open"] += 1
        if "opens" in state:
            state["opens"] += 1
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
        _seed_enabled_rule(p.admin_engine, org=p.org_b, account_id=acc)
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


# ── EM-T4a-2 PR-A: no session open across the status ask ──────────────────
#
# Spec: ``email_app_master_plan.md`` §10.4.6, "EM-T4a-2 — the decision core".
# The scheduler fence (``test_email_scheduler_tenancy.py``) cannot see a block
# in ``routes/email``, so the fence of the decision core lives here.

#: The organization of the hermetic cases below. ``mode_for`` reads it.
_DC_ORG = "55555555-5555-5555-5555-555555555555"
_DC_ACC = "acc-t4a2"
_DC_OWNER = "owner@t4a2.test"
_DC_SEEN = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


@pytest.fixture()
def decide_env(monkeypatch):
    """A clean ``decide`` registry and a budget that counts nothing, bound
    to :data:`_DC_ORG`. Each case sets its own mode with :func:`_dc_mode`."""
    def _clear() -> None:
        get_settings.cache_clear()
        df._parse_modes.cache_clear()
        df._parse_orgs.cache_clear()
        df._cooldown_until.clear()

    monkeypatch.setenv("DECIDE_FEATURE_MODES", "")
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "*")
    monkeypatch.setenv("EMAIL_LLM_BUDGET_MODE", "off")
    _clear()
    with _bound(_DC_ORG):
        yield _clear
    monkeypatch.undo()
    _clear()


def _dc_mode(monkeypatch, clear, mode: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES",
                       "" if mode == "off" else f"email.thread_status={mode}")
    clear()


def _watch_sessions(monkeypatch, state: dict, db) -> list[str]:
    """Give each loaded module of ``routes/email`` that binds
    ``_tenant_session`` the double of :func:`_open_count_session`.

    Each module imports the seam under its own name, so one patch of
    ``core`` would leave a block of ``replyzero`` unseen. Returns the names
    of the patched modules."""
    double = _open_count_session(state, db)
    patched = []
    for name, module in sorted(sys.modules.items()):
        if name.startswith("gateway.routes.email") and module is not None \
                and hasattr(module, "_tenant_session"):
            monkeypatch.setattr(module, "_tenant_session", double)
            patched.append(name)
    return patched


def _watch_model(monkeypatch, state: dict, seen: list[tuple[str, int]], *,
                 on_ask=None) -> None:
    """Watch the model at its two leaves, and patch each leaf once.

    ``acb_llm.decide``: ``decide_features._ask_all`` imports it at call
    time. ``acb_llm.context.acompletion_with_fallback``: ``core._llm_json``
    imports it at call time. Each call records the blocks open right now.
    The old call answers DONE, and ``decide`` chooses DONE."""
    fake = FakeDecide(choices={"status": "DONE"})

    async def _decide(state_, questions, **kw):
        seen.append(("decide", state["open"]))
        if on_ask is not None:
            on_ask()
        return await fake(state_, questions, **kw)

    async def _completion(model=None, messages=None, **kw):
        seen.append(("completion", state["open"]))
        if on_ask is not None:
            on_ask()
        msg = SimpleNamespace(content='{"status": "DONE"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)]), model

    monkeypatch.setattr(acb_llm, "decide", _decide)
    monkeypatch.setattr(llm_context, "acompletion_with_fallback", _completion)


def _dc_context(**kw) -> replyzero_mod.ThreadContext:
    """A thread with an outside participant. The owner sent last."""
    fields = {"thread_id": "t1", "last_message_id": "m9",
              "last_message_at": datetime.now(UTC), "our_side_last": True,
              "has_external": True, "thread_text": "thread",
              "messages": THREAD, "newest_received_at": _DC_SEEN}
    fields.update(kw)
    return replyzero_mod.ThreadContext(**fields)


def _dc_reads(monkeypatch, *, self_only: bool = False, ctx=None,
              upsert=None) -> dict[str, AsyncMock]:
    """Stand in for the reads of the read step and for each write. The read,
    ask and write steps, ``_llm_determine_thread_status``, the ``decide``
    helpers and ``_llm_json`` stay real, so each model call reaches a
    watched leaf."""
    mocks = {
        "build_thread_context": AsyncMock(return_value=ctx or _dc_context()),
        "_thread_is_self_only": AsyncMock(return_value=self_only),
        "_status_corrections_block": AsyncMock(return_value=""),
        "_load_assistant_about": AsyncMock(return_value=("", "")),
        "_upsert_thread_status": upsert or AsyncMock(return_value=True),
        "_reconcile_thread_labels": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(replyzero_mod, name, mock)
    return mocks


def _dc_db() -> AsyncMock:
    """Answers the account read of ``_mark_thread_replied`` and the owner
    read of ``_decide_member``."""
    row = SimpleNamespace(email_address="box@t4a2.test", provider="microsoft",
                          credentials_encrypted="x", user_id=_DC_OWNER)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
    return db


def _dc_provider(monkeypatch):
    provider = _FakeProvider()
    _patch_providers(monkeypatch, provider)
    return provider


def _asks_inside_a_block(seen: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """The fence: each watched model call that saw an open block."""
    return [(leaf, n) for leaf, n in seen if n != 0]


#: The leaves that each mode reaches. ``shadow`` runs the old call and
#: ``decide`` side by side, and ``on`` makes no LLM call (D-EM-8).
_LEAVES = {"off": {"completion"}, "shadow": {"completion", "decide"},
           "on": {"decide"}}


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
async def test_the_status_ask_runs_with_no_session_open(
    mode, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-no-session-across-the-ask``.

    The status ask of ``_mark_thread_replied`` reaches a watched leaf with
    ZERO open blocks. The read is one block before the ask, and the write is
    a NEW block after it."""
    _dc_mode(monkeypatch, decide_env, mode)
    state = {"open": 0, "opens": 0}
    seen: list[tuple[str, int]] = []
    patched = _watch_sessions(monkeypatch, state, _dc_db())
    assert "gateway.routes.email.automation.replyzero" in patched
    _watch_model(monkeypatch, state, seen)
    writes: list[int] = []

    async def _upsert(*_a, **_kw):
        writes.append(state["open"])
        return True

    mocks = _dc_reads(monkeypatch, upsert=AsyncMock(side_effect=_upsert))
    provider = _dc_provider(monkeypatch)

    await replyzero_mod._mark_thread_replied(
        _DC_ACC, "t1", sent_body="Thanks", sent_subject="Re")

    assert {leaf for leaf, _ in seen} == _LEAVES[mode], seen
    assert _asks_inside_a_block(seen) == [], (
        f"a session was open during the status ask: {seen}")
    assert writes == [1], "the status write ran outside a block"
    assert mocks["_upsert_thread_status"].await_args.kwargs["guard"] is True
    mocks["_reconcile_thread_labels"].assert_awaited_once()
    assert provider.calls[0] == "authenticate"
    # Block A, the write block W and the label block B.
    assert state["opens"] == 3 and state["open"] == 0


async def test_the_ask_fence_can_fail(monkeypatch, decide_env):
    """The companion of ``email-decision-core-no-session-across-the-ask``.

    The plant is the shape before EM-T4a-2: one block open across the
    composed ``recompute_thread_status``. The fence must see the ask inside
    the block."""
    _dc_mode(monkeypatch, decide_env, "off")
    state = {"open": 0, "opens": 0}
    seen: list[tuple[str, int]] = []
    _watch_sessions(monkeypatch, state, _dc_db())
    _watch_model(monkeypatch, state, seen)
    _dc_reads(monkeypatch)

    async def _planted() -> None:
        async with replyzero_mod._tenant_session() as db:
            await replyzero_mod.recompute_thread_status(
                db, _DC_ACC, "t1", trigger="outbound")

    await _planted()
    assert seen, "the plant reached no watched leaf — the fence went blind"
    assert _asks_inside_a_block(seen) == [("completion", 1)]


async def test_a_self_only_thread_writes_in_the_read_block_and_asks_nothing(
    monkeypatch, decide_env,
):
    """The self-only path keeps its write with no ask (D-EM-27)."""
    _dc_mode(monkeypatch, decide_env, "on")
    state = {"open": 0, "opens": 0}
    seen: list[tuple[str, int]] = []
    _watch_sessions(monkeypatch, state, _dc_db())
    _watch_model(monkeypatch, state, seen)
    mocks = _dc_reads(monkeypatch, self_only=True)
    _dc_provider(monkeypatch)

    await replyzero_mod._mark_thread_replied(_DC_ACC, "t1")

    assert seen == [], "a self-only thread asked a model"
    upsert = mocks["_upsert_thread_status"]
    assert upsert.await_args.args[3] == "FYI"
    assert upsert.await_args.args[6] == replyzero_mod.SELF_ONLY_REASON
    mocks["_reconcile_thread_labels"].assert_awaited_once()
    # Block A writes the FYI row, and block B reconciles the labels.
    assert state["opens"] == 2


async def test_the_guard_compares_with_the_newest_stored_row(
    monkeypatch, decide_env,
):
    """R7 fence ``email-status-write-guard``, its hermetic half.

    For a pending reply ``last_message_at`` is ``now()``, so a guard on it
    lets a newer inbound row through. The write passes the newest stored
    ``received_at`` that the read saw."""
    _dc_mode(monkeypatch, decide_env, "off")
    state = {"open": 0}
    _watch_sessions(monkeypatch, state, _dc_db())
    _watch_model(monkeypatch, state, [])
    now = datetime.now(UTC)
    mocks = _dc_reads(monkeypatch, ctx=_dc_context(last_message_at=now))
    _dc_provider(monkeypatch)

    await replyzero_mod._mark_thread_replied(
        _DC_ACC, "t1", sent_body="Thanks", sent_subject="Re")

    upsert = mocks["_upsert_thread_status"].await_args
    assert upsert.kwargs["guard"] is True
    assert upsert.kwargs["seen_at"] == _DC_SEEN, (
        "the guard must compare with the newest STORED received_at")
    # The row still stamps `last_message_at` as before.
    assert upsert.args[5] == now


async def test_a_voided_write_reconciles_no_labels(monkeypatch, decide_env):
    """``email-status-write-guard``: the guard voided the write, so the job
    ends. It builds no provider and reconciles no labels."""
    _dc_mode(monkeypatch, decide_env, "off")
    state = {"open": 0}
    _watch_sessions(monkeypatch, state, _dc_db())
    _watch_model(monkeypatch, state, [])
    mocks = _dc_reads(monkeypatch, upsert=AsyncMock(return_value=False))
    provider = _dc_provider(monkeypatch)

    await replyzero_mod._mark_thread_replied(
        _DC_ACC, "t1", sent_body="Thanks", sent_subject="Re")

    mocks["_upsert_thread_status"].assert_awaited_once()
    mocks["_reconcile_thread_labels"].assert_not_awaited()
    assert provider.calls == []


async def test_a_spent_budget_in_the_ask_writes_nothing(monkeypatch, decide_env):
    """``LLMBudgetExhausted`` passes up from the ask step (EM-T4b item 13).
    The job writes no status and reconciles no labels."""
    _dc_mode(monkeypatch, decide_env, "off")
    state = {"open": 0}
    _watch_sessions(monkeypatch, state, _dc_db())

    async def _spent(*_a, **_kw):
        raise LLMBudgetExhausted("spent")

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", _spent)
    mocks = _dc_reads(monkeypatch)
    provider = _dc_provider(monkeypatch)

    with pytest.raises(LLMBudgetExhausted):
        await replyzero_mod.ask_thread_status(replyzero_mod.StatusRead(
            account_id=_DC_ACC, thread_id="t1", trigger="outbound",
            ctx=_dc_context(), acc_email="box@t4a2.test", about="",
            model="tier-balanced", self_only=False))
    await replyzero_mod._mark_thread_replied(
        _DC_ACC, "t1", sent_body="Thanks", sent_subject="Re")

    mocks["_upsert_thread_status"].assert_not_awaited()
    mocks["_reconcile_thread_labels"].assert_not_awaited()
    assert provider.calls == []


# ── EM-T4a-2 PR-A: R8, the guard of the status write ───────────────────────


def _seed_thread_row(admin, *, org: str, account_id: str, thread_id: str,
                     folder: str, received_at: datetime | None,
                     categories: list[str] | None = None) -> str:
    """One message of a thread, with any ``received_at``, NULL included."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "body_text, snippet, received_at, categories, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'hello', 'Can you send the quote?', 'quote', "
            ":rcv, CAST(:cats AS text[]), CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "tid": thread_id, "f": folder,
             "frm": json.dumps({"email": "buyer@ext-t4a2.test", "name": "B"}),
             "to": json.dumps([{"email": "box@ext-t4a2.test"}]),
             "rcv": received_at, "cats": categories or [],
             "o": org}).scalar_one())


#: Each case plants one row during the ask: (organization, folder, the
#: offset of ``received_at`` from the stored inbound row, voids the write).
#: ``None`` as an offset is a row with no date. The newer rows sit five
#: minutes AFTER the stored row and still BEFORE the read, so a guard on
#: ``ctx.last_message_at`` (``now()`` for a pending reply) misses them.
_GUARD_CASES = {
    "newer-inbound-in-b": ("b", "inbox", timedelta(minutes=5), True),
    "newer-sent-in-b": ("b", "sent", timedelta(minutes=5), False),
    "newer-draft-in-b": ("b", "drafts", timedelta(minutes=5), False),
    "no-date-in-b": ("b", "inbox", None, False),
    "tie-in-b": ("b", "inbox", timedelta(0), False),
    "newer-inbound-in-a": ("a", "inbox", timedelta(minutes=5), False),
}


@_DB_GATE
class TestTheStatusWriteGuard:
    """R7 fence ``email-status-write-guard``, on a real Postgres as the
    non-owner role ``acb_app_h3rls`` under FORCE RLS (the ``promoted``
    fixture). The job is the real ``_mark_thread_replied``. Only the model
    leaf is a fake, and it plants a row while the ask runs."""

    @pytest.mark.parametrize("case", sorted(_GUARD_CASES))
    async def test_a_row_that_lands_during_the_ask(
        self, case, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        _dc_mode(monkeypatch, decide_env, "off")
        _assert_non_priv(app_engine)
        p = promoted
        org_key, folder, offset, voids = _GUARD_CASES[case]
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2.test")
        tid = f"t-guard-{uuid.uuid4().hex[:8]}"
        stored_at = datetime.now(UTC) - timedelta(minutes=10)
        inbound = _seed_thread_row(
            p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid,
            folder="inbox", received_at=stored_at, categories=["Reply"])
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, "
                "status, last_message_id, last_message_at, reason, "
                "organization_id) VALUES (CAST(:a AS uuid), :t, "
                "'NEEDS_REPLY', CAST(:m AS uuid), :at, 'seed', "
                "CAST(:o AS uuid))"),
                {"a": acc, "t": tid, "m": inbound, "at": stored_at,
                 "o": p.org_b})
        planted: list[str] = []

        def _plant() -> None:
            if planted:
                return
            planted.append(_seed_thread_row(
                p.admin_engine, org=p.org_a if org_key == "a" else p.org_b,
                account_id=acc, thread_id=tid, folder=folder,
                received_at=None if offset is None else stored_at + offset))

        state = {"open": 0}
        _watch_model(monkeypatch, state, [], on_ask=_plant)
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await replyzero_mod._mark_thread_replied(
                    acc, tid, sent_body="Thanks, all done.",
                    sent_subject="Re: hello")

        assert planted, "the model leaf was never asked"
        row = _rows(p.admin_engine,
                    "SELECT status, reason, organization_id::text AS org "
                    f"FROM email_thread_status {_BY_ACCOUNT} AND thread_id = :t",
                    {"a": acc, "t": tid})
        cats = _scalar(p.admin_engine,
                       "SELECT categories FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": inbound})
        if voids:
            assert row == [{"status": "NEEDS_REPLY", "reason": "seed",
                            "org": p.org_b}], (
                f"{case}: a newer inbound row did not void the write: {row}")
            assert cats == ["Reply"], f"{case}: a voided write reconciled {cats}"
            assert provider.calls == [], (
                f"{case}: a voided write reached the provider: {provider.calls}")
        else:
            assert row == [{"status": "DONE", "reason": "Replied — DONE",
                            "org": p.org_b}], (
                f"{case}: the row should not void the write: {row}")
            assert "Reply" not in cats, f"{case}: the labels were not reconciled"
            assert "set_labels" in provider.calls
        _isolated(p, "email_thread_status", acc, expect_b=1)
