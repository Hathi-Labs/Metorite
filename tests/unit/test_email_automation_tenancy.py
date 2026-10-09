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

**EM-T4a-2, PR-B1.** Four more fences. The first three watch the same
leaves:

* ``email-decision-core-no-session-across-the-match-ask``. The rule-match
  ask of ``_run_rules_job`` and of the gap loop of ``_maybe_classify_threads``
  sees zero open blocks, in ``off``, ``shadow`` and ``on`` of
  ``email.rule_match``. Block W is the next block after Block R, and it holds
  every write. A companion plants the composed ``classify_matches`` in a
  block. R8: the split jobs write their rows in org B only.
* ``email-decision-core-steps``. Each new step takes ``db`` (the ask step
  takes none), opens no block and calls no ``commit()``.
* ``email-decision-core-read-fails-closed`` (review round 1). A reader of
  Block R that swallows a failed statement leaves the transaction aborted.
  The ``SELECT 1`` at the end of Block R then raises, so the row gets no
  ask, no Block W, no stamp and no provider call. Hermetic for each job and
  each reader, and R8 for the runner.
* ``email-decision-core-apply-raises-no-unavailable`` (review round 1). An
  AST walk of ``routes/email``: no function that the apply of Block W
  reaches raises ``LLMUnavailable``, so the one handler of each job never
  rolls back an action that the job already took. A companion plants a
  raise and shows that the fence fails.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_automation_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm
import acb_llm.context as llm_context
import structlog
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
from gateway.routes.email.automation import rules as rules_mod
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
        # EM-T4a-2 PR-B1: the ask step answers no match. The read and the
        # resolver run their real SQL in B.
        monkeypatch.setattr(runner_mod, "ask_rule_match",
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
        # EM-T4a-2 PR-B1: the ask step answers no match.
        monkeypatch.setattr(engine_mod, "ask_rule_match",
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
        # EM-T4a-2 PR-B1: the ask step answers no match.
        monkeypatch.setattr(runner_mod, "ask_rule_match",
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


def _watch_sessions(monkeypatch, state: dict, db, *, double=None) -> list[str]:
    """Give each loaded module of ``routes/email`` that binds
    ``_tenant_session`` the double of :func:`_open_count_session`, or
    ``double`` when the case gives one.

    Each module imports the seam under its own name, so one patch of
    ``core`` would leave a block of ``replyzero`` unseen. Returns the names
    of the patched modules."""
    double = double or _open_count_session(state, db)
    patched = []
    for name, module in sorted(sys.modules.items()):
        if name.startswith("gateway.routes.email") and module is not None \
                and hasattr(module, "_tenant_session"):
            monkeypatch.setattr(module, "_tenant_session", double)
            patched.append(name)
    return patched


def _watch_model(monkeypatch, state: dict, seen: list[tuple[str, int]], *,
                 on_ask=None, tagged: list[tuple[str, str, int]] | None = None,
                 match: bool = False) -> None:
    """Watch the model at its two leaves, and patch each leaf once.

    ``acb_llm.decide``: ``decide_features._ask_all`` imports it at call
    time. ``acb_llm.context.acompletion_with_fallback``: ``core._llm_json``
    imports it at call time. Each call records the blocks open right now.
    The old call answers DONE, and ``decide`` chooses DONE.

    EM-T4a-2 PR-B1: ``tagged`` also records the TAG of each call: the
    question ids of a ``decide`` call, or the tier of an old call. With
    ``match``, the rule match matches its first rule in every mode: the old
    call answers index 0, and each ``decide`` boolean answers 0.92."""
    fake = FakeDecide(choices={"status": "DONE"}, p=0.92 if match else 0.0)
    reply = ('{"status": "DONE", "index": 0, "reason": "fits", "matches": '
             '[{"index": 0, "reason": "fits", "primary": true}]}'
             if match else '{"status": "DONE"}')

    async def _decide(state_, questions, **kw):
        seen.append(("decide", state["open"]))
        if tagged is not None:
            tagged.append(("decide", ",".join(sorted(questions)), state["open"]))
        if on_ask is not None:
            on_ask()
        return await fake(state_, questions, **kw)

    async def _completion(model=None, messages=None, **kw):
        seen.append(("completion", state["open"]))
        if tagged is not None:
            tagged.append(("completion", str(model), state["open"]))
        if on_ask is not None:
            on_ask()
        msg = SimpleNamespace(content=reply)
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


# ── EM-T4a-2 PR-B1: no session open across the rule-match ask ──────────────
#
# Spec: ``email_app_master_plan.md`` §10.4.6, "EM-T4a-2 — the decision core",
# PR-B1. Each job reads the rule match in Block R, asks it with NO block
# open, and writes in ONE Block W: the resolver, the apply, the projection,
# the label reconcile and (in the runner) the stamp.

#: The rule of the PR-B1 cases. It only labels, with no ``{{``, so
#: ``_render_template`` asks no model. The cold blocker is ``OFF`` and the
#: sender has no history, so the cold check and the sender pin ask nothing.
#: Until EM-T4a-3 those three reach the same leaves inside Block W.
_B1_RULE = {"id": "r-receipt", "name": "Receipt", "enabled": True,
            "instructions": "receipts and invoices", "system_type": None,
            "actions": [{"type": "LABEL", "label": "Receipt"}]}
_B1_DONE_RULE = {"id": "r-done", "name": "Done", "enabled": True,
                 "instructions": "a finished conversation",
                 "system_type": "DONE",
                 "actions": [{"type": "LABEL", "label": "Done"}]}

#: The two jobs, and the runner in its multi-rule mode.
_B1_JOBS = {
    "runner": lambda: runner_mod._run_rules_job(_DC_ACC, 50, False, "scheduler"),
    "runner-multi": lambda: runner_mod._run_rules_job(
        _DC_ACC, 50, False, "scheduler"),
    "backfill": lambda: replyzero_mod._maybe_classify_threads(_DC_ACC),
}

#: The writes of Block W, in each job. The backfill writes no stamp.
_B1_WRITES = {
    "runner": ("resolve_classification", "_apply_matches",
               "project_reply_status_from_matches", "_reconcile_thread_labels",
               "_stamp_processed_watermark"),
    "backfill": ("resolve_classification", "project_reply_status_from_matches",
                 "_reconcile_thread_labels"),
}


def _rm_mode(monkeypatch, clear, mode: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES",
                       "" if mode == "off" else f"email.rule_match={mode}")
    clear()


def _b1_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="m-b1", provider_message_id="pm-b1", thread_id="t-b1",
        subject="Invoice 42", body_text="Your invoice is attached.", snippet="",
        from_address={"email": "billing@vendor-b1.test", "name": "Billing"},
        to_addresses=[{"email": "box@t4a2.test"}], cc_addresses=[],
        received_at=_DC_SEEN, folder="inbox")


def _b1_db(*, multi: bool) -> AsyncMock:
    """One database for both jobs. The SELECT of phase 0 answers one row,
    each other read answers no rows, and each ``fetchone`` answers one row
    with every column that a job reads (the cold blocker is ``OFF``)."""
    one = SimpleNamespace(
        user_id=_DC_OWNER, email_address="box@t4a2.test", provider="microsoft",
        credentials_encrypted="x", cold_email_blocker="OFF",
        multi_rule_execution=multi, org_domains=None)

    async def execute(stmt, params=None):
        sql = str(stmt)
        phase0 = "rules_processed_at IS NULL" in sql or "LIMIT 200" in sql
        return MagicMock(
            fetchall=MagicMock(return_value=[_b1_row()] if phase0 else []),
            fetchone=MagicMock(return_value=one))

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    return db


def _b1_env(monkeypatch, clear, mode: str, *, job: str = "runner",
            conversation: bool = False, match: bool = False):
    """Run a job with each read patched, the model watched at its two leaves
    and each write a spy. Returns the block state, the tagged model calls
    and the spy calls, each spy call as (name, block ordinal, open blocks,
    args). The read and the resolver steps are spies that call through."""
    _rm_mode(monkeypatch, clear, mode)
    state = {"open": 0, "opens": 0}
    tagged: list[tuple[str, str, int]] = []
    _watch_sessions(monkeypatch, state, _b1_db(multi=job == "runner-multi"))
    _watch_model(monkeypatch, state, [], tagged=tagged, match=match)
    for mod in (runner_mod, replyzero_mod):
        monkeypatch.setattr(mod, "_load_assistant_about",
                            AsyncMock(return_value=("", "")))
        monkeypatch.setattr(mod, "_attachment_summaries",
                            AsyncMock(return_value={}))
        monkeypatch.setattr(mod, "resolve_org_domains",
                            AsyncMock(return_value=set()))
        monkeypatch.setattr(mod, "_persist_rotated_creds", AsyncMock())
    selves = frozenset({"box@t4a2.test"})
    monkeypatch.setattr(runner_mod, "resolve_self_addresses",
                        AsyncMock(return_value=selves))
    monkeypatch.setattr(replyzero_mod, "resolve_self", AsyncMock(
        return_value=SimpleNamespace(address="box@t4a2.test",
                                     self_addresses=selves)))
    for name, value in (("_load_rules", [_B1_RULE]),
                        ("_is_reply_candidate", (True, "")),
                        ("_load_rule_patterns", {}),
                        ("_load_rule_guidance", {}),
                        ("_fetch_sender_history", [])):
        monkeypatch.setattr(engine_mod, name, AsyncMock(return_value=value))
    monkeypatch.setattr(rules_mod, "_load_rules",
                        AsyncMock(return_value=[_B1_RULE, _B1_DONE_RULE]))
    monkeypatch.setattr(replyzero_mod, "_thread_is_conversation",
                        AsyncMock(return_value=conversation))
    calls: list[tuple[str, int, int, tuple]] = []

    def _spy(mod, name, *, result=None, through=False) -> None:
        real = getattr(mod, name)

        async def _call(*a, **kw):
            calls.append((name, state["opens"], state["open"], a))
            return await real(*a, **kw) if through else result
        monkeypatch.setattr(mod, name, _call)

    for mod in (runner_mod, engine_mod):
        _spy(mod, "read_classification", through=True)
        _spy(mod, "resolve_classification", through=True)
    # PR-B2: the read of Block S. Both jobs call it through `replyzero`.
    _spy(replyzero_mod, "read_job_status", through=True)
    _spy(runner_mod, "_apply_matches")
    _spy(runner_mod, "_stamp_processed_watermark")
    _spy(replyzero_mod, "project_reply_status_from_matches", result="Receipt")
    _spy(replyzero_mod, "_reconcile_thread_labels")
    _dc_provider(monkeypatch)
    return state, tagged, calls


def _is_match_ask(leaf: str, tag: str) -> bool:
    """A rule-match ask: the old call on ``tier-fast``, or a ``decide`` call
    whose questions are the booleans ``r<i>``, ``conv`` and ``best``."""
    if leaf == "completion":
        return tag == "tier-fast"
    return all(q in ("conv", "best") or (q[:1] == "r" and q[1:].isdigit())
               for q in tag.split(","))


def _match_asks(tagged: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    return [t for t in tagged if _is_match_ask(t[0], t[1])]


def _blocks(calls) -> dict[str, tuple[int, int]]:
    """(block ordinal, open blocks) of each spy, by name."""
    return {name: (n, open_) for name, n, open_, _a in calls}


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_the_match_ask_runs_with_no_session_open(
    job, mode, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-no-session-across-the-match-ask``.

    The rule-match ask of each job reaches a watched leaf with ZERO open
    blocks, in each mode of ``email.rule_match``. Block R reads before it.
    ONE Block W after it holds each write, so the apply and the stamp
    commit together."""
    state, tagged, calls = _b1_env(monkeypatch, decide_env, mode, job=job)

    await _B1_JOBS[job]()

    asks = _match_asks(tagged)
    assert {leaf for leaf, _t, _n in asks} == _LEAVES[mode], tagged
    assert [n for _l, _t, n in asks] == [0] * len(asks), (
        f"a session was open during the rule-match ask: {tagged}")
    assert asks == tagged, f"a model call that is not the rule match: {tagged}"
    blocks = _blocks(calls)
    read, block_w = blocks["read_classification"], blocks["resolve_classification"]
    assert read[1] == 1 and block_w[1] == 1
    # PR-B2 item 10: Block W is the next block after Block R, or after
    # Block S when the job asks the thread status.
    before_w = blocks.get("read_job_status", read)
    assert before_w[0] in (read[0], read[0] + 1), blocks
    assert block_w[0] == before_w[0] + 1, (
        "the resolver ran in the read block, or a block opened between "
        f"Block R (or Block S) and Block W (inside a read or across an "
        f"ask): {blocks}")
    writes = _B1_WRITES["backfill" if job == "backfill" else "runner"]
    assert {name: blocks.get(name) for name in writes} == dict.fromkeys(
        writes, block_w), f"Block W is not ONE block: {blocks}"
    assert state["open"] == 0


async def test_the_match_fence_can_fail(monkeypatch, decide_env):
    """The companion of ``email-decision-core-no-session-across-the-match-ask``.

    The plant is the shape before PR-B1: one block open across the composed
    ``classify_matches``. The fence must see the rule-match ask inside it."""
    _state, tagged, _calls = _b1_env(monkeypatch, decide_env, "off")
    email = {"from": "billing@vendor-b1.test", "subject": "Invoice 42",
             "body": "x", "to": "box@t4a2.test"}

    async def _planted() -> None:
        async with runner_mod._tenant_session() as db:
            await engine_mod.classify_matches(
                db, _DC_ACC, _b1_row(), email, resolve=False)

    await _planted()
    assert _match_asks(tagged) == [("completion", "tier-fast", 1)]


@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_a_failed_match_ask_writes_nothing_and_stamps_nothing(
    job, mode, monkeypatch, decide_env,
):
    """A model that is down (``off``) or a ``decide`` with no answer
    (``on``) leaves the email undecided (D-EM-8). The job opens no Block W
    for it: no resolver, no apply, no projection and no stamp. The runner
    logs the skip once."""
    _state, tagged, calls = _b1_env(monkeypatch, decide_env, mode, job=job)

    async def _down(*_a, **_kw):
        tagged.append(("down", "", 0))
        raise acb_llm.DecideUnavailable("HTTP 503") if mode == "on" \
            else RuntimeError("the model is down")

    monkeypatch.setattr(acb_llm, "decide", _down)
    monkeypatch.setattr(llm_context, "acompletion_with_fallback", _down)

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    assert tagged, "the ask reached no leaf — the case went blind"
    names = [name for name, *_rest in calls]
    assert names == ["read_classification"], (
        f"an undecided email reached Block W: {names}")
    skipped = [c for c in caps
               if c.get("event") == "email.classify_unavailable_skip"]
    assert len(skipped) == (0 if job == "backfill" else 1)


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_an_undecided_resolver_writes_nothing_and_stamps_nothing(
    job, monkeypatch, decide_env,
):
    """The resolver raises ``DecisionUnavailable`` at the head of Block W
    (``on`` of ``email.thread_status``, D-EM-8). Block W writes nothing,
    and the runner stamps nothing."""
    _state, _tagged, calls = _b1_env(monkeypatch, decide_env, "off", job=job)

    async def _undecided(*_a, **_kw):
        calls.append(("resolve_classification", 0, 0, ()))
        raise engine_mod.DecisionUnavailable("no status")

    for mod in (runner_mod, engine_mod):
        monkeypatch.setattr(mod, "resolve_classification", _undecided)

    await _B1_JOBS[job]()

    names = [name for name, *_rest in calls]
    assert names == ["read_classification", "resolve_classification"], names


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_a_suppressed_match_stays_suppressed(
    job, monkeypatch, decide_env,
):
    """The rule match picks Receipt. The thread is a conversation, and its
    status is DONE. Block W gets the Done rule as the ONE live match and
    Receipt flagged ``suppressed``, so its actions never run (#110)."""
    _state, tagged, calls = _b1_env(
        monkeypatch, decide_env, "off", job=job, conversation=True, match=True)
    monkeypatch.setattr(replyzero_mod, "_thread_is_self_only",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(replyzero_mod, "build_thread_context",
                        AsyncMock(return_value=_dc_context()))
    monkeypatch.setattr(replyzero_mod, "_status_corrections_block",
                        AsyncMock(return_value=""))

    await _B1_JOBS[job]()

    assert _match_asks(tagged) == [("completion", "tier-fast", 0)], tagged
    spy = "project_reply_status_from_matches" if job == "backfill" \
        else "_apply_matches"
    (matches,) = [a[5 if spy == "_apply_matches" else 3]
                  for name, _n, _o, a in calls if name == spy]
    assert [(m["rule"]["id"], m.get("suppressed")) for m in matches] == [
        ("r-done", None), ("r-receipt", "conversation")], matches


# ── EM-T4a-2 PR-B1 review round 1: an aborted Block R fails closed ─────────

#: Each best-effort reader of Block R: a fragment of its SQL, and the line it
#: logs when it swallows its own failure and returns an empty value.
_B1_READERS = {
    "_is_reply_candidate": ("unsubscribe_link", "email.reply_candidate_gate_failed"),
    "_load_rule_patterns": ("email_rule_patterns", "email.rule_patterns_load_failed"),
    "_load_rule_guidance": ("email_rule_guidance", "email.rule_guidance_load_failed"),
    "_fetch_sender_history": ("email_executed_rules",
                              "email.classification_hints_failed"),
}

#: The line that the outer handler of each job logs.
_B1_JOB_FAILED = {"runner": "email.run_rules_failed",
                  "runner-multi": "email.run_rules_failed",
                  "backfill": "email.classify_threads_failed"}


def _aborting_db(*, multi: bool, fail_on: str) -> AsyncMock:
    """The database of :func:`_b1_db`, with the abort of a real transaction.

    The first statement whose SQL holds ``fail_on`` fails. Each statement
    after it in the same block fails too, because Postgres refuses each
    statement of an aborted transaction (measured through asyncpg: both are
    ``DBAPIError``, and the COMMIT after them raises nothing). A new block
    is a new transaction (``db.tx``)."""
    from sqlalchemy.exc import DBAPIError

    db = _b1_db(multi=multi)
    answer = db.execute.side_effect
    db.tx = {"aborted": False}

    async def execute(stmt, params=None):
        sql = str(stmt)
        if db.tx["aborted"]:
            raise DBAPIError(sql, params, Exception(
                "current transaction is aborted, commands ignored until end "
                "of transaction block"))
        if fail_on in sql:
            db.tx["aborted"] = True
            raise DBAPIError(sql, params, Exception(
                "canceling statement due to statement timeout"))
        return await answer(stmt, params)

    db.execute = AsyncMock(side_effect=execute)
    return db


def _transaction_per_block(state: dict, db):
    """:func:`_open_count_session`, and each block begins a new transaction."""
    from contextlib import asynccontextmanager

    counted = _open_count_session(state, db)

    @asynccontextmanager
    async def _ts():
        db.tx["aborted"] = False
        async with counted() as session:
            yield session
    return _ts


@pytest.mark.parametrize("reader", sorted(_B1_READERS))
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_an_aborted_read_block_stops_the_row(
    job, reader, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-read-fails-closed`` (review round 1).

    A best-effort reader of Block R swallows a failed statement, and the
    transaction stays aborted. Each reader after it fails and returns an
    empty value, and the seam commits the aborted block with no error. The
    ``SELECT 1`` at the end of Block R raises instead. So the row gets no
    ask, no Block W, no stamp and no provider call. The outer handler of
    the job logs the failure, and the next cycle selects the row again,
    because nothing stamped it. Remove the ``SELECT 1`` and the ask runs on
    the empty context, and Block W applies and stamps the result."""
    real = {name: getattr(engine_mod, name) for name in _B1_READERS}
    state, tagged, calls = _b1_env(monkeypatch, decide_env, "off", job=job)
    for name, fn in real.items():
        monkeypatch.setattr(engine_mod, name, fn)
    fail_on, swallowed = _B1_READERS[reader]
    db = _aborting_db(multi=job == "runner-multi", fail_on=fail_on)
    _watch_sessions(monkeypatch, state, db,
                    double=_transaction_per_block(state, db))
    provider = _dc_provider(monkeypatch)

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    events = [c.get("event") for c in caps]
    assert swallowed in events, f"{reader} did not swallow a failure: {events}"
    assert tagged == [], f"the ask ran on an aborted read: {tagged}"
    names = [name for name, *_rest in calls]
    assert names == ["read_classification"], (
        f"an aborted read reached Block W: {names}")
    assert provider.calls == ["authenticate"], provider.calls
    assert [e for e in events if e in _B1_JOB_FAILED.values()] == [
        _B1_JOB_FAILED[job]], events
    assert "email.classify_unavailable_skip" not in events
    assert state["open"] == 0


# ── EM-T4a-2 PR-B1: the source fences ──────────────────────────────────────

#: Each new step of PR-B1, and whether it takes ``db``. None opens a block
#: or calls ``commit()``: the blocks stay in the two job bodies.
_B1_STEPS = {"read_rule_match": True, "read_classification": True,
             "resolve_classification": True, "ask_rule_match": False}

#: The steps of the status ask of a job (PR-B2), in ``replyzero.py``.
_B2_STEPS = {"read_job_status": True, "ask_job_status": False,
             "status_ask_needed": False, "_resolve_asked": True,
             # PR-B3: the split of `status_before_match` for the jobs.
             "read_status_first": True, "ask_status_first": False,
             "status_move_keys": False}

#: Each step, with its file and whether it takes ``db``.
_STEPS = {**{n: ("automation/engine.py", d) for n, d in _B1_STEPS.items()},
          **{n: ("automation/replyzero.py", d) for n, d in _B2_STEPS.items()}}

#: The two job bodies that run the split form.
_B1_JOB_BODIES = {"automation/runner.py": "_run_rules_job",
                  "automation/replyzero.py": "_maybe_classify_threads"}


_SESSION_OPENERS = ("tenant_session", "get_db", "get_session_factory")


def _step_violations(source: str, name: str, *, takes_db: bool) -> list[str]:
    """What breaks the step rule in one function: the ``db`` parameter, each
    ``async with`` (a step opens no context at all, so an alias of the seam
    cannot hide one), each import or call of a session opener, and each
    ``.commit()``."""
    fn = _function(source, name)
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    found = [] if ("db" in params) == takes_db else ["db"]
    for inner in ast.walk(fn):
        if isinstance(inner, ast.AsyncWith):
            found.append(f"{inner.lineno}:async with")
        elif isinstance(inner, ast.ImportFrom):
            found.extend(f"{inner.lineno}:import {a.name}" for a in inner.names
                         if a.name.endswith(_SESSION_OPENERS))
        elif isinstance(inner, ast.Call):
            called = _call_name(inner)
            if called.endswith(_SESSION_OPENERS):
                found.append(f"{inner.lineno}:{called}")
            elif isinstance(inner.func, ast.Attribute) and called == "commit":
                found.append(f"{inner.lineno}:commit")
    return sorted(found)


@pytest.mark.parametrize("name", sorted(_STEPS))
def test_each_new_step_takes_db_and_opens_no_block(name):
    """R7 fence ``email-decision-core-steps``. A read or resolver step takes
    the session of its job and opens none, and the ask step takes no ``db``.
    A step that opens a block escapes the count of the fence above. PR-B2
    adds the steps of the status ask in ``replyzero.py``."""
    rel, takes_db = _STEPS[name]
    source = (_EMAIL / rel).read_text(encoding="utf-8")
    assert _step_violations(source, name, takes_db=takes_db) == []


def test_the_step_fence_can_fail():
    planted = (
        "async def read_x(account_id):\n"
        "    async with _tenant_session() as db:\n"
        "        await db.commit()\n"
        "\n"
        "async def read_y(db):\n"
        "    from gateway.routes.email.core import _tenant_session as _ts\n"
        "    async with _ts() as own:\n"
        "        return own\n"
        "\n"
        "async def ask_x(db, read):\n"
        "    return read\n"
    )
    assert _step_violations(planted, "read_x", takes_db=True) == [
        "2:_tenant_session", "2:async with", "3:commit", "db"]
    assert _step_violations(planted, "read_y", takes_db=True) == [
        "6:import _tenant_session", "7:async with"]
    assert _step_violations(planted, "ask_x", takes_db=False) == ["db"]


@pytest.mark.parametrize(("rel", "name"), sorted(_B1_JOB_BODIES.items()))
def test_both_jobs_call_the_split_form(rel, name):
    """Each job calls the three steps of the split form, and not a composed
    form, which would hold its block across the ask."""
    fn = _function((_EMAIL / rel).read_text(encoding="utf-8"), name)
    called = {_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert {"read_classification", "ask_rule_match",
            "resolve_classification", "status_ask_needed",
            "read_job_status", "ask_job_status", "ask_status_first",
            "status_move_keys"} <= called, called
    assert not called & {"classify_matches", "_match_email_to_rule",
                         "_match_email_to_rules_multi"}, called


#: What Block W of each job runs after ``resolve_classification``: the apply,
#: the projection, the label reconcile and the stamp. The single
#: ``except LLMUnavailable`` of each job (PR-B1, agent decision D16) covers
#: them too. A raise there after a provider action would roll Block W back,
#: leave the row unstamped and run the action again next cycle.
_B1_APPLY_ROOTS = ("_apply_matches", "_stamp_processed_watermark",
                   "project_reply_status_from_matches", "_reconcile_thread_labels")

#: The four functions that raise ``LLMUnavailable`` today (measured
#: 2026-10-05). Each one is an ask, before Block W writes.
_B1_RAISE_SITES = {"_decide_rule_match", "_llm_pick_rule", "_llm_pick_rules",
                   "_decide_thread_status"}

#: The model calls of the apply. Each one catches every error today.
_B1_APPLY_MODEL_CALLS = {"_render_template", "_llm_is_cold",
                         "_ai_confirms_sender_pattern"}

_UNAVAILABLE = {"LLMUnavailable", "DecisionUnavailable"}


def _function_index(sources: dict[str, str]) -> dict[str, list[tuple[str, ast.AST]]]:
    """Each module-level function of ``sources`` (path to text), by name."""
    index: dict[str, list[tuple[str, ast.AST]]] = {}
    for rel, source in sources.items():
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                index.setdefault(node.name, []).append((rel, node))
    return index


def _reach(index, roots) -> dict[str, list[ast.AST]]:
    """Each function that ``roots`` reach through calls, by name. A call
    resolves by its last name to each function of that name, so for a
    direct call the walk can see too much, and never too little."""
    seen: dict[str, list[ast.AST]] = {}
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen or name not in index:
            continue
        seen[name] = [fn for _rel, fn in index[name]]
        todo.extend(_call_name(n) for fn in seen[name] for n in ast.walk(fn)
                    if isinstance(n, ast.Call))
    return seen


def _unavailable_raises(fn: ast.AST) -> list[int]:
    """Each line of ``fn`` that raises ``LLMUnavailable`` or its subclass:
    a ``raise`` of one by name, and a bare ``raise`` in a handler of one."""
    def _names(node) -> set[str]:
        nodes = node.elts if isinstance(node, ast.Tuple) else [node]
        return {n.id if isinstance(n, ast.Name) else getattr(n, "attr", "")
                for n in nodes}

    lines = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if _names(exc) & _UNAVAILABLE:
                lines.append(node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.type is not None \
                and _names(node.type) & _UNAVAILABLE:
            lines.extend(r.lineno for r in ast.walk(node)
                         if isinstance(r, ast.Raise) and r.exc is None)
    return sorted(lines)


def _raises_by_name(functions: dict[str, list[ast.AST]]) -> dict[str, list[int]]:
    """The raise lines of each function that has one, by name."""
    found = {name: sorted(line for fn in fns for line in _unavailable_raises(fn))
             for name, fns in functions.items()}
    return {name: lines for name, lines in found.items() if lines}


def test_the_apply_raises_no_llm_unavailable():
    """R7 fence ``email-decision-core-apply-raises-no-unavailable`` (review
    round 1). No function that the apply of Block W reaches in
    ``routes/email`` raises ``LLMUnavailable``, and none of the four raise
    sites is among them. EM-T4a-3 must keep it so: catch it before the
    apply, or the job rolls back an action that it already took."""
    sources = {p.relative_to(_EMAIL).as_posix(): p.read_text(encoding="utf-8")
               for p in sorted(_EMAIL.rglob("*.py"))}
    index = _function_index(sources)
    every = _raises_by_name({name: [fn for _r, fn in fns]
                             for name, fns in index.items()})
    assert set(every) >= _B1_RAISE_SITES, f"the walk lost a raise site: {every}"
    reached = _reach(index, _B1_APPLY_ROOTS)
    assert set(reached) >= _B1_APPLY_MODEL_CALLS, (
        f"the walk lost a model call of the apply: {sorted(reached)}")
    assert not _B1_RAISE_SITES & set(reached), sorted(_B1_RAISE_SITES & set(reached))
    assert _raises_by_name(reached) == {}


def test_the_apply_fence_can_fail():
    planted = (
        "async def _apply_matches(db, provider):\n"
        "    await _helper(db)\n"
        "    await _llm_pick_rule({}, [])\n"
        "\n"
        "async def _helper(db):\n"
        "    try:\n"
        "        await db.execute('x')\n"
        "    except DecisionUnavailable:\n"
        "        raise\n"
        "    raise LLMUnavailable('down')\n"
        "\n"
        "async def _llm_pick_rule(email, rules):\n"
        "    raise engine.LLMUnavailable('down')\n"
    )
    reached = _reach(_function_index({"automation/planted.py": planted}),
                     _B1_APPLY_ROOTS)
    assert _raises_by_name(reached) == {"_helper": [9, 10], "_llm_pick_rule": [13]}


# ── EM-T4a-2 PR-B2: the status ask of the two jobs ─────────────────────────
#
# Spec: ``email_app_master_plan.md`` §10.4.6, "EM-T4a-2 — the decision core",
# PR-B2. In ``off`` and ``shadow`` of ``email.thread_status``, each job reads
# the thread status in Block S, asks it with NO block open, and gives the
# answer to the resolver in Block W.


def _status_asks(tagged: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """Each watched call that is not a rule-match ask: the status ask."""
    return [t for t in tagged if not _is_match_ask(t[0], t[1])]


def _b2_env(monkeypatch, clear, mode: str, *, job: str, conversation: bool = True):
    """:func:`_b1_env` for a conversation thread, a match on Receipt, and
    ``mode`` of ``email.thread_status``. The rule match stays ``off``. The
    reads of the status stand in, and its ask reaches the watched leaves."""
    out = _b1_env(monkeypatch, clear, "off", job=job,
                  conversation=conversation, match=True)
    _dc_mode(monkeypatch, clear, mode)
    monkeypatch.setattr(replyzero_mod, "_thread_is_self_only",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(replyzero_mod, "build_thread_context",
                        AsyncMock(return_value=_dc_context()))
    monkeypatch.setattr(replyzero_mod, "_status_corrections_block",
                        AsyncMock(return_value=""))
    return out


def _applied(job: str, calls) -> list[tuple[str, str | None]]:
    """The matches that Block W applied (runner) or projected (backfill)."""
    spy = "project_reply_status_from_matches" if job == "backfill" \
        else "_apply_matches"
    (matches,) = [a[5 if spy == "_apply_matches" else 3]
                  for name, _n, _o, a in calls if name == spy]
    return [(m["rule"]["id"], m.get("suppressed")) for m in matches]


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_the_job_status_ask_runs_with_no_session_open(
    job, mode, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-no-session-across-the-job-status-ask``.

    The status ask of each job reaches a watched leaf with ZERO open blocks,
    in ``off`` and ``shadow`` of ``email.thread_status``. Block S reads after
    Block R, and Block W follows Block S. The status reaches the resolver:
    Done is the ONE live match, and Receipt is suppressed (#110)."""
    state, tagged, calls = _b2_env(monkeypatch, decide_env, mode, job=job)

    await _B1_JOBS[job]()

    asks = _status_asks(tagged)
    assert {leaf for leaf, _t, _n in asks} == _LEAVES[mode], tagged
    assert [n for _l, _t, n in asks] == [0] * len(asks), (
        f"a session was open during the status ask: {tagged}")
    blocks = _blocks(calls)
    read, block_s = blocks["read_classification"], blocks["read_job_status"]
    block_w = blocks["resolve_classification"]
    assert block_s == (read[0] + 1, 1), blocks
    assert block_w == (block_s[0] + 1, 1), (
        f"Block W is not the next block after Block S: {blocks}")
    writes = _B1_WRITES["backfill" if job == "backfill" else "runner"]
    assert {name: blocks.get(name) for name in writes} == dict.fromkeys(
        writes, block_w), f"Block W is not ONE block: {blocks}"
    assert _applied(job, calls) == [("r-done", None),
                                    ("r-receipt", "conversation")]
    assert state["open"] == 0


async def test_the_job_status_fence_can_fail(monkeypatch, decide_env):
    """The companion of
    ``email-decision-core-no-session-across-the-job-status-ask``. The plant
    keeps Block S open across the ask. The fence must see the ask inside it."""
    _state, tagged, _calls = _b2_env(monkeypatch, decide_env, "off", job="runner")

    async def _planted() -> None:
        async with replyzero_mod._tenant_session() as db:
            seen = await replyzero_mod.read_job_status(db, _DC_ACC, _b1_row())
            await replyzero_mod.ask_job_status(seen)

    await _planted()
    assert _status_asks(tagged) == [("completion", "tier-balanced", 1)]


#: A conversation rule that the rule match picks (review round 1).
_B2_REPLY_RULE = {"id": "r-reply", "name": "Reply", "enabled": True,
                  "instructions": "a mail that asks the owner a question",
                  "system_type": "REPLY",
                  "actions": [{"type": "LABEL", "label": "Reply"}]}


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_a_conversation_match_asks_the_status_of_a_new_thread(
    job, mode, monkeypatch, decide_env,
):
    """``email-decision-core-no-session-across-the-job-status-ask``, the
    other half of item 2 (review round 1, reviewer P2).

    The first inbound mail of a new thread matches the conversation rule
    Reply, and the thread is not a conversation yet. The job still asks the
    status: Block S reads, the ask sees zero open blocks, and the status
    rule Done is the ONE live match."""
    state, tagged, calls = _b2_env(monkeypatch, decide_env, mode, job=job,
                                   conversation=False)
    monkeypatch.setattr(engine_mod, "_load_rules",
                        AsyncMock(return_value=[_B2_REPLY_RULE]))

    await _B1_JOBS[job]()

    blocks = _blocks(calls)
    assert "read_job_status" in blocks, (
        f"a conversation match on a new thread asked no status: {blocks}")
    asks = _status_asks(tagged)
    assert {leaf for leaf, _t, _n in asks} == _LEAVES[mode], tagged
    assert [n for _l, _t, n in asks] == [0] * len(asks), tagged
    assert blocks["resolve_classification"][0] == blocks["read_job_status"][0] + 1
    assert _applied(job, calls) == [("r-done", None)]
    assert state["open"] == 0


def _bound_args(fn, args, kwargs) -> dict:
    """The arguments of one call of ``fn``, with each default filled in."""
    bound = inspect.signature(fn).bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


@pytest.mark.parametrize("self_only", [False, True])
async def test_the_split_status_asks_what_the_composed_form_asks(
    self_only, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-status-parity``.

    ``read_job_status`` and ``ask_job_status`` give
    ``_llm_determine_thread_status`` the arguments of
    ``_determine_status_of``: the ``about`` text with no knowledge base,
    the self addresses, the corrections, the member and the id of the ROW,
    not ``ctx.last_message_id``. A self-only thread asks no model in either
    form, and both give FYI."""
    _dc_mode(monkeypatch, decide_env, "off")
    selves = frozenset({"box@t4a2.test", "alias@t4a2.test"})

    async def _about(db, account_id, *, include_kb=True, query=None):
        return ("about+kb" if include_kb else "about", "sig")

    real_build = replyzero_mod.build_thread_context
    build = AsyncMock(return_value=_dc_context(last_message_id="m9"))
    for name, value in (
            ("_load_assistant_about", _about),
            ("resolve_self", AsyncMock(return_value=SimpleNamespace(
                address="box@t4a2.test", self_addresses=selves))),
            ("build_thread_context", build),
            ("_thread_is_self_only", AsyncMock(return_value=self_only)),
            ("_status_corrections_block", AsyncMock(return_value="\n\nFIX"))):
        monkeypatch.setattr(replyzero_mod, name, value)
    monkeypatch.setattr(engine_mod, "_decide_member",
                        AsyncMock(return_value=_DC_OWNER))
    real_ask = replyzero_mod._llm_determine_thread_status
    asked: list[dict] = []

    async def _record(*args, **kwargs):
        asked.append(_bound_args(real_ask, args, kwargs))
        return "DONE", True

    monkeypatch.setattr(replyzero_mod, "_llm_determine_thread_status", _record)
    row, db = _b1_row(), AsyncMock()

    composed = await replyzero_mod._determine_status_of(db, _DC_ACC, row)
    split = await replyzero_mod.ask_job_status(
        await replyzero_mod.read_job_status(db, _DC_ACC, row))

    if self_only:
        assert asked == [], "a self-only thread asked a model"
        assert composed == ("FYI", True)
        assert split == replyzero_mod.JobStatus("verdict", ("FYI", True))
        return
    assert len(asked) == 2, asked
    assert asked[0] == asked[1], "the split form asks with other arguments"
    assert asked[0]["about"] == "about"
    assert asked[0]["message_id"] == "m-b1"
    assert asked[0]["member"] == _DC_OWNER
    builds = [_bound_args(real_build, c.args, c.kwargs)
              for c in build.await_args_list]
    assert len(builds) == 2 and builds[0] == builds[1], builds
    assert builds[0]["self_addresses"] == selves
    assert split == replyzero_mod.JobStatus("verdict", composed)


async def test_a_self_only_thread_needs_no_other_read(monkeypatch, decide_env):
    """``email-decision-core-status-parity``, a self-only thread on a failure
    path (review round 1, verifier F1).

    ``_determine_status_of`` tests self-only first and reads nothing more.
    So a self-only thread is FYI even when the ``about`` read and the self
    read fail. The split read keeps that order and gives FYI too, with no
    failure logged."""
    _dc_mode(monkeypatch, decide_env, "off")

    async def _broken(*_a, **_kw):
        raise RuntimeError("the read failed")

    build = AsyncMock(side_effect=_broken)
    for name, value in (("_load_assistant_about", _broken),
                        ("resolve_self", _broken),
                        ("build_thread_context", build),
                        ("_thread_is_self_only", AsyncMock(return_value=True))):
        monkeypatch.setattr(replyzero_mod, name, value)
    row, db = _b1_row(), AsyncMock()

    with structlog.testing.capture_logs() as caps:
        composed = await replyzero_mod._determine_status_of(db, _DC_ACC, row)
        split = await replyzero_mod.ask_job_status(
            await replyzero_mod.read_job_status(db, _DC_ACC, row))

    assert composed == ("FYI", True)
    assert split == replyzero_mod.JobStatus("verdict", ("FYI", True)), split
    build.assert_not_awaited()
    assert "email.resolve_conversation_status_failed" not in [
        c.get("event") for c in caps]


@pytest.mark.parametrize("mode", ["on", "off"])
async def test_the_conversation_read_stays_out_of_on(mode, monkeypatch, decide_env):
    """Review round 1 (verifier F2). Item 1 reads the conversation test in
    Block R outside ``on`` only. In ``on`` the plan of ``read_status_first``
    owns it (PR-B3), so ``read_classification`` must not read it again.
    ``off`` is the control, and it shows that the case can see the read."""
    _dc_mode(monkeypatch, decide_env, mode)
    conversation = AsyncMock(return_value=True)
    monkeypatch.setattr(replyzero_mod, "_thread_is_conversation", conversation)
    monkeypatch.setattr(replyzero_mod, "read_status_first", AsyncMock(
        return_value=replyzero_mod.StatusFirst(rules={})))
    monkeypatch.setattr(engine_mod, "read_rule_match", AsyncMock(return_value=None))
    email = {"from": "billing@vendor-b1.test", "subject": "Invoice 42"}

    plan = await engine_mod.read_classification(
        AsyncMock(), _DC_ACC, _b1_row(), email, resolve=True)

    if mode == "on":
        conversation.assert_not_awaited()
        assert plan.conversation is False
    else:
        conversation.assert_awaited_once()
        assert plan.conversation is True


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_a_failed_status_ask_keeps_the_per_message_matches(
    job, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-status-degrades`` (PR-B2 item 7).

    The status ask raises ``LLMBudgetExhausted``. The job logs
    ``email.resolve_conversation_status_failed`` and keeps the per-message
    matches: Block W applies Receipt, and the runner stamps the row."""
    state, tagged, calls = _b2_env(monkeypatch, decide_env, "off", job=job)
    leaf = llm_context.acompletion_with_fallback

    async def _spent_on_status(model=None, messages=None, **kw):
        if model != "tier-fast":
            tagged.append(("spent", str(model), state["open"]))
            raise LLMBudgetExhausted("spent")
        return await leaf(model=model, messages=messages, **kw)

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", _spent_on_status)

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    events = [c.get("event") for c in caps]
    assert ("spent", "tier-balanced", 0) in tagged, tagged
    assert "email.resolve_conversation_status_failed" in events, events
    assert _applied(job, calls) == [("r-receipt", None)]
    names = [name for name, *_rest in calls]
    if job != "backfill":
        assert "_stamp_processed_watermark" in names, names
    assert "email.classify_unavailable_skip" not in events
    assert not {"email.run_rules_failed", "email.classify_threads_failed"} \
        & set(events), events


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_an_undecided_status_skips_the_row(job, monkeypatch, decide_env):
    """``email-decision-core-status-degrades`` (PR-B2 item 9, D-EM-8).

    ``decide`` gives no status, so the carrier is "undecided". The resolver
    raises ``DecisionUnavailable`` at the head of Block W: no apply, no
    projection and no stamp. The runner logs the skip once."""
    _state, _tagged, calls = _b2_env(monkeypatch, decide_env, "off", job=job)
    monkeypatch.setattr(replyzero_mod, "ask_thread_status",
                        AsyncMock(return_value=None))

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    names = [name for name, *_rest in calls]
    assert names == ["read_classification", "read_job_status",
                     "resolve_classification"], names
    skipped = [c for c in caps
               if c.get("event") == "email.classify_unavailable_skip"]
    assert len(skipped) == (0 if job == "backfill" else 1)


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_an_aborted_status_block_stops_the_job(job, monkeypatch, decide_env):
    """``email-decision-core-status-degrades`` (PR-B2 item 6).

    A statement of Block S fails, and the read swallows it and logs it, as
    the resolver does. The transaction stays aborted, so the ``SELECT 1`` at
    the end of Block S raises. The job stops: no status ask, no Block W, no
    stamp and no provider call after the authentication. Remove the
    ``SELECT 1`` and Block W applies and stamps the per-message matches."""
    real_build = replyzero_mod.build_thread_context
    state, tagged, calls = _b2_env(monkeypatch, decide_env, "off", job=job)
    monkeypatch.setattr(replyzero_mod, "build_thread_context", real_build)
    db = _aborting_db(multi=job == "runner-multi", fail_on="NULLS FIRST")
    _watch_sessions(monkeypatch, state, db,
                    double=_transaction_per_block(state, db))
    provider = _dc_provider(monkeypatch)

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    events = [c.get("event") for c in caps]
    assert "email.resolve_conversation_status_failed" in events, events
    assert _status_asks(tagged) == [], f"the status ask ran: {tagged}"
    names = [name for name, *_rest in calls]
    assert names == ["read_classification", "read_job_status"], (
        f"an aborted Block S reached Block W: {names}")
    assert provider.calls == ["authenticate"], provider.calls
    assert [e for e in events if e in _B1_JOB_FAILED.values()] == [
        _B1_JOB_FAILED[job]], events
    assert state["open"] == 0


# ── EM-T4a-2 PR-B3: the status ask in `on` ─────────────────────────────────
#
# Spec: ``email_app_master_plan.md`` §10.4.6, PR-B3. In ``on`` of
# ``email.thread_status``, Block R reads the status-first plan, and the job
# asks it with NO block open BEFORE the rule-match ask. A status that only a
# conversation match asks is read in Block S and asked with no block open.

#: The two status asks of ``on``. ``first``: the thread is a conversation,
#: so the job asks before the rule match. ``late``: a new thread whose match
#: is the conversation rule Reply, so the job asks after the match.
_B3_PATHS = ("first", "late")


def _b3_env(monkeypatch, clear, *, job: str, path: str):
    """:func:`_b2_env` in ``on`` of ``email.thread_status``, on one path."""
    out = _b2_env(monkeypatch, clear, "on", job=job,
                  conversation=path == "first")
    if path == "late":
        monkeypatch.setattr(engine_mod, "_load_rules",
                            AsyncMock(return_value=[_B2_REPLY_RULE]))
    return out


@pytest.mark.parametrize("path", _B3_PATHS)
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_the_on_status_ask_runs_with_no_session_open(
    job, path, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-no-session-across-the-on-status-ask``.

    In ``on``, the status ask of each job reaches ``decide`` with ZERO open
    blocks. On the ``first`` path, Block R reads the status and the ask
    comes before the rule-match ask (fix round 3). On the ``late`` path,
    Block S reads it after the match. Block W comes next, holds each
    write, and makes Done the ONE live match (#110)."""
    state, tagged, calls = _b3_env(monkeypatch, decide_env, job=job, path=path)

    await _B1_JOBS[job]()

    asks = _status_asks(tagged)
    assert [(leaf, n) for leaf, _t, n in asks] == [("decide", 0)], (
        f"a session was open during the status ask in on: {tagged}")
    match_ask = _match_asks(tagged)[0]
    blocks = _blocks(calls)
    read, block_s = blocks["read_classification"], blocks["read_job_status"]
    block_w = blocks["resolve_classification"]
    if path == "first":
        assert tagged.index(asks[0]) < tagged.index(match_ask), (
            f"the status-first order of fix round 3 is lost: {tagged}")
        assert block_s == read, f"the status read left Block R: {blocks}"
        before_w = read
        applied = [("r-done", None), ("r-receipt", "conversation")]
    else:
        assert tagged.index(asks[0]) > tagged.index(match_ask), tagged
        assert block_s == (read[0] + 1, 1), blocks
        before_w = block_s
        applied = [("r-done", None)]
    assert block_w == (before_w[0] + 1, 1), (
        f"Block W is not the next block: {blocks}")
    writes = _B1_WRITES["backfill" if job == "backfill" else "runner"]
    assert {name: blocks.get(name) for name in writes} == dict.fromkeys(
        writes, block_w), f"Block W is not ONE block: {blocks}"
    assert _applied(job, calls) == applied
    assert state["open"] == 0


@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_a_conversation_match_on_a_conversation_asks_once(
    job, monkeypatch, decide_env,
):
    """``email-decision-core-no-session-across-the-on-status-ask``, the
    cost half (verifier F1).

    The thread is a conversation AND the match is the conversation rule
    Reply. ``ask_status_first`` asks before the match, so the job must not
    ask again in Block S. Exactly ONE status ask, read in Block R, and
    Block W follows Block R."""
    state, tagged, calls = _b3_env(monkeypatch, decide_env, job=job,
                                   path="first")
    monkeypatch.setattr(engine_mod, "_load_rules",
                        AsyncMock(return_value=[_B2_REPLY_RULE]))

    await _B1_JOBS[job]()

    asks = _status_asks(tagged)
    assert [(leaf, n) for leaf, _t, n in asks] == [("decide", 0)], (
        f"the job asked the status more than once: {tagged}")
    reads = [(n, o) for name, n, o, _a in calls if name == "read_job_status"]
    blocks = _blocks(calls)
    assert reads == [blocks["read_classification"]], (
        f"a second status read ran outside Block R: {calls}")
    assert blocks["resolve_classification"][0] == reads[0][0] + 1, blocks
    assert _applied(job, calls) == [("r-done", None)]
    assert state["open"] == 0


@pytest.mark.parametrize("mode", ["on", "off"])
@pytest.mark.parametrize("job", ["runner", "runner-multi"])
async def test_each_row_of_a_thread_asks_in_on(job, mode, monkeypatch, decide_env):
    """Verifier F2. Two new rows of ONE thread in one run. In ``on``, each
    row asks the late status, as before PR-B3: the per-run memo of #753
    stays out of the decide path. ``off`` is the control, and there the
    memo gives one ask for the thread."""
    if mode == "on":
        state, tagged, calls = _b3_env(monkeypatch, decide_env, job=job,
                                       path="late")
    else:
        state, tagged, calls = _b2_env(monkeypatch, decide_env, "off", job=job,
                                       conversation=False)
        monkeypatch.setattr(engine_mod, "_load_rules",
                            AsyncMock(return_value=[_B2_REPLY_RULE]))
    db = _b1_db(multi=job == "runner-multi")
    first_row, second_row = _b1_row(), SimpleNamespace(
        **{**vars(_b1_row()), "id": "m-b1-2", "provider_message_id": "pm-b1-2"})
    phase0 = db.execute.side_effect

    async def execute(stmt, params=None):
        result = await phase0(stmt, params)
        if "rules_processed_at IS NULL" in str(stmt):
            result.fetchall.return_value = [first_row, second_row]
        return result

    db.execute = AsyncMock(side_effect=execute)
    _watch_sessions(monkeypatch, state, db)

    await _B1_JOBS[job]()

    asks = _status_asks(tagged)
    want = 2 if mode == "on" else 1
    assert [n for _l, _t, n in asks] == [0] * want, (
        f"the status asks of two rows of one thread: {tagged}")
    reads = [name for name, *_rest in calls if name == "read_job_status"]
    assert len(reads) == want, calls
    assert state["open"] == 0


async def test_the_on_status_fence_can_fail(monkeypatch, decide_env):
    """The companion of
    ``email-decision-core-no-session-across-the-on-status-ask``. The plant
    is the shape before PR-B3: Block R open across the composed
    ``status_before_match``. The fence must see the ask inside it."""
    _state, tagged, _calls = _b3_env(monkeypatch, decide_env, job="runner",
                                     path="first")

    async def _planted() -> None:
        async with runner_mod._tenant_session() as db:
            await replyzero_mod.status_before_match(db, _DC_ACC, _b1_row())

    await _planted()
    assert _status_asks(tagged) == [("decide", "status", 1)]


@pytest.mark.parametrize("path", _B3_PATHS)
@pytest.mark.parametrize("job", sorted(_B1_JOBS))
async def test_an_undecided_on_status_skips_the_row(
    job, path, monkeypatch, decide_env,
):
    """``email-decision-core-on-status-degrades`` (D-EM-8).

    ``decide`` gives no status in ``on``. The job skips the row: no apply,
    no projection and no stamp, and the runner logs the skip once. On the
    ``first`` path the rule match is not paid, and no Block W opens. On the
    ``late`` path the resolver raises at the head of Block W."""
    state, tagged, calls = _b3_env(monkeypatch, decide_env, job=job, path=path)

    async def _no_status(_state, questions, **_kw):
        tagged.append(("decide", ",".join(sorted(questions)), state["open"]))
        raise acb_llm.DecideUnavailable("HTTP 503")

    monkeypatch.setattr(acb_llm, "decide", _no_status)

    with structlog.testing.capture_logs() as caps:
        await _B1_JOBS[job]()

    assert [(leaf, n) for leaf, _t, n in _status_asks(tagged)] == [
        ("decide", 0)], tagged
    names = [name for name, *_rest in calls]
    if path == "first":
        assert _match_asks(tagged) == [], f"the rule match was paid: {tagged}"
        assert names == ["read_classification", "read_job_status"], names
    else:
        assert names == ["read_classification", "read_job_status",
                         "resolve_classification"], names
    skipped = [c for c in caps
               if c.get("event") == "email.classify_unavailable_skip"]
    assert len(skipped) == (0 if job == "backfill" else 1)
    assert state["open"] == 0


#: A Done rule that moves mail, so the status ask carries a move key.
_B3_MOVING_DONE_RULE = {**_B1_DONE_RULE,
                        "actions": [{"type": "ARCHIVE", "label": None}]}


@pytest.mark.parametrize("path", _B3_PATHS)
async def test_the_on_split_asks_what_the_composed_form_asks(
    path, monkeypatch, decide_env,
):
    """R7 fence ``email-decision-core-on-status-parity``.

    The split steps give ``_llm_determine_thread_status`` the arguments of
    the composed form of ``on``: ``status_before_match`` on the ``first``
    path, and the ask of ``_resolve_on`` after the match on the ``late``
    path. Both carry the move keys of the conversation rules and the id of
    the row."""
    _dc_mode(monkeypatch, decide_env, "on")
    selves = frozenset({"box@t4a2.test"})
    for name, value in (
            ("_load_assistant_about", AsyncMock(return_value=("about", "sig"))),
            ("resolve_self", AsyncMock(return_value=SimpleNamespace(
                address="box@t4a2.test", self_addresses=selves))),
            ("build_thread_context", AsyncMock(return_value=_dc_context())),
            ("_thread_is_self_only", AsyncMock(return_value=False)),
            ("_thread_is_conversation", AsyncMock(return_value=path == "first")),
            ("_status_corrections_block", AsyncMock(return_value="\n\nFIX")),
            ("_restore_conversation_messages", AsyncMock())):
        monkeypatch.setattr(replyzero_mod, name, value)
    monkeypatch.setattr(rules_mod, "_load_rules",
                        AsyncMock(return_value=[_B3_MOVING_DONE_RULE]))
    monkeypatch.setattr(engine_mod, "_decide_member",
                        AsyncMock(return_value=_DC_OWNER))
    real_ask = replyzero_mod._llm_determine_thread_status
    asked: list[dict] = []

    async def _record(*args, **kwargs):
        asked.append(_bound_args(real_ask, args, kwargs))
        return "DONE", True

    monkeypatch.setattr(replyzero_mod, "_llm_determine_thread_status", _record)
    row, db = _b1_row(), AsyncMock()
    reply = [{"rule": _B2_REPLY_RULE, "reason": "fits"}]

    first = await replyzero_mod.read_status_first(db, _DC_ACC, row)
    if path == "first":
        composed = await replyzero_mod.status_before_match(db, _DC_ACC, row)
        split = await replyzero_mod.ask_status_first(first)
        assert split == replyzero_mod.JobStatus("verdict", composed.verdict)
    else:
        await replyzero_mod._resolve_on(
            db, _DC_ACC, row, reply, provider=None,
            first=replyzero_mod.StatusFirst(rules=first.rules))
        assert await replyzero_mod.ask_status_first(first) \
            == replyzero_mod.NOT_ASKED
        plan = engine_mod.ClassifyRead(match=None, first=first)
        assert replyzero_mod.status_ask_needed(plan, row, reply)
        await replyzero_mod.ask_job_status(await replyzero_mod.read_job_status(
            db, _DC_ACC, row, move_keys=replyzero_mod.status_move_keys(plan)))
    assert len(asked) == 2, asked
    assert asked[0] == asked[1], "the split form asks with other arguments"
    assert asked[0]["move_keys"] == frozenset({"DONE"})
    assert asked[0]["message_id"] == "m-b1"
    assert asked[0]["member"] == _DC_OWNER


# ── EM-T4a-2 PR-B1: R8, the writes of the split jobs ───────────────────────


def _seed_label_rule(admin, *, org: str, account_id: str) -> None:
    """An enabled rule that only labels, created a day before any message."""
    with admin.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, instructions, enabled, "
            "created_at, organization_id) VALUES (CAST(:a AS uuid), 'Receipt', "
            "'receipts and invoices', true, :c, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "c": datetime.now(UTC) - timedelta(days=1),
             "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, organization_id) "
            "VALUES (CAST(:r AS uuid), 'LABEL', 'Receipt', CAST(:o AS uuid))"),
            {"r": rid, "o": org})


def _seed_done_rule(admin, *, org: str, account_id: str) -> None:
    """An enabled conversation rule for DONE that only labels (PR-B2)."""
    with admin.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, instructions, enabled, "
            "system_type, created_at, organization_id) VALUES "
            "(CAST(:a AS uuid), 'Done', 'a finished conversation', true, "
            "'DONE', :c, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "c": datetime.now(UTC) - timedelta(days=1),
             "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, organization_id) "
            "VALUES (CAST(:r AS uuid), 'LABEL', 'Done', CAST(:o AS uuid))"),
            {"r": rid, "o": org})


@_DB_GATE
class TestTheSplitJobsWriteTheirOwnTenant:
    """The R8 done-when of PR-B1, on a real Postgres as the non-owner role
    ``acb_app_h3rls`` under FORCE RLS. Only the two model LEAVES are fakes,
    so the read SQL of the split steps runs as that role."""

    @pytest.mark.parametrize("mode", ["off", "shadow", "on"])
    async def test_the_runner_writes_its_rows_and_stamps_in_b(
        self, mode, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        _rm_mode(monkeypatch, decide_env, mode)
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2b1.test")
        _seed_label_rule(p.admin_engine, org=p.org_b, account_id=acc)
        mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                            thread_id=f"t-b1-{acc}")
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")

        assert {leaf for leaf, _n in seen} == _LEAVES[mode], seen
        logs = _rows(p.admin_engine,
                     "SELECT message_id::text AS mid, status, rule_name, "
                     "match_source, organization_id::text AS org FROM "
                     f"email_executed_rules {_BY_ACCOUNT}", {"a": acc})
        assert logs == [{"mid": mid, "status": "APPLIED", "rule_name": "Receipt",
                         "match_source": "ai", "org": p.org_b}], logs
        assert "set_labels" in provider.calls
        stamped = ("SELECT count(*) FROM email_messages "
                   f"{_BY_ACCOUNT} AND rules_processed_at IS NOT NULL")
        assert _count_as(p.app_url, p.org_b, stamped, {"a": acc}) == 1
        assert _count_as(p.app_url, p.org_a, stamped, {"a": acc}) == 0
        _isolated(p, "email_executed_rules", acc, expect_b=1)
        _isolated(p, "email_thread_status", acc, expect_b=1)

    @pytest.mark.parametrize("mode", ["off", "shadow", "on"])
    async def test_the_backfill_writes_its_status_in_b(
        self, mode, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        _rm_mode(monkeypatch, decide_env, mode)
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2b1.test")
        _seed_label_rule(p.admin_engine, org=p.org_b, account_id=acc)
        tid = f"t-b1-gap-{acc}"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid)
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        _patch_providers(monkeypatch, _FakeProvider())
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await replyzero_mod._maybe_classify_threads(acc)

        assert {leaf for leaf, _n in seen} == _LEAVES[mode], seen
        rows = _rows(p.admin_engine,
                     "SELECT thread_id, status, organization_id::text AS org "
                     f"FROM email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert rows == [{"thread_id": tid, "status": "FYI", "org": p.org_b}], rows
        _isolated(p, "email_thread_status", acc, expect_b=1)
        _isolated(p, "email_executed_rules", acc, expect_b=0)

    async def test_an_aborted_read_block_stops_the_row_in_b(
        self, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        """R8 of ``email-decision-core-read-fails-closed`` (review round 1).

        On a real Postgres, a statement that fails in Block R aborts the
        transaction, and each reader after it fails and returns an empty
        value. The ``SELECT 1`` raises, so the runner asks nothing, logs no
        rule, stamps nothing and calls no provider. Without it, the seam
        commits the aborted block with no error, and Block W applies and
        stamps the row on the empty context."""
        _rm_mode(monkeypatch, decide_env, "off")
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2b1.test")
        _seed_label_rule(p.admin_engine, org=p.org_b, account_id=acc)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      thread_id=f"t-b1-abort-{acc}")
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        gate = engine_mod._is_reply_candidate

        async def _gate_after_a_failed_statement(db, account_id, email):
            # One statement fails and the caller swallows it, as a reader
            # does with a statement timeout. The real gate then runs.
            with suppress(Exception):
                await db.execute(text("SELECT 1 / 0"))
            return await gate(db, account_id, email)

        monkeypatch.setattr(engine_mod, "_is_reply_candidate",
                            _gate_after_a_failed_statement)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        with structlog.testing.capture_logs() as caps:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    await runner_mod._run_rules_job(acc, 50, False, "scheduler")

        events = [c.get("event") for c in caps]
        assert "email.rule_patterns_load_failed" in events, (
            f"the readers after the failure did not fail: {events}")
        assert seen == [], f"the ask ran on an aborted read: {seen}"
        assert "email.run_rules_failed" in events, events
        logs = _rows(p.admin_engine, "SELECT status FROM email_executed_rules "
                     f"{_BY_ACCOUNT}", {"a": acc})
        assert logs == [], f"an aborted read applied a rule: {logs}"
        stamped = ("SELECT count(*) FROM email_messages "
                   f"{_BY_ACCOUNT} AND rules_processed_at IS NOT NULL")
        assert _count_as(p.app_url, p.org_b, stamped, {"a": acc}) == 0
        assert provider.calls == ["authenticate"], provider.calls

    # ── PR-B2: a conversation thread, with the status asked in Block S ──

    def _seed_conversation(self, p, *, label: str) -> tuple[str, str, str]:
        """An account of org B with the Receipt rule and an enabled Done
        rule, and a thread that is a conversation: an older reply of our
        side in ``sent``, then the inbox row from outside. Returns the
        account, the thread and the inbox row."""
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2b2.test")
        _seed_label_rule(p.admin_engine, org=p.org_b, account_id=acc)
        _seed_done_rule(p.admin_engine, org=p.org_b, account_id=acc)
        tid = f"t-b2-{label}-{acc}"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      folder="sent", thread_id=tid,
                      received_at=datetime.now(UTC) - timedelta(hours=1))
        mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                            thread_id=tid)
        return acc, tid, mid

    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_the_runner_resolves_a_conversation_in_b(
        self, mode, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        """R8 of PR-B2. The runner reads the thread in Block S as
        ``acb_app_h3rls``, asks the status with no block open, and applies
        the Done rule as the ONE live match in org B. Org A reads none of
        it."""
        _dc_mode(monkeypatch, decide_env, mode)
        _assert_non_priv(app_engine)
        p = promoted
        acc, tid, mid = self._seed_conversation(p, label="runner")
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        provider = _FakeProvider()
        _patch_providers(monkeypatch, provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")

        assert {leaf for leaf, _n in seen} == _LEAVES[mode], seen
        logs = _rows(p.admin_engine,
                     "SELECT message_id::text AS mid, status, rule_name, "
                     "organization_id::text AS org FROM email_executed_rules "
                     f"{_BY_ACCOUNT} AND status = 'APPLIED'", {"a": acc})
        assert logs == [{"mid": mid, "status": "APPLIED", "rule_name": "Done",
                         "org": p.org_b}], logs
        rows = _rows(p.admin_engine,
                     "SELECT thread_id, status, organization_id::text AS org "
                     f"FROM email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert rows == [{"thread_id": tid, "status": "DONE", "org": p.org_b}], rows
        stamped = ("SELECT count(*) FROM email_messages "
                   f"{_BY_ACCOUNT} AND rules_processed_at IS NOT NULL")
        assert _count_as(p.app_url, p.org_b, stamped, {"a": acc}) == 1
        assert _count_as(p.app_url, p.org_a, stamped, {"a": acc}) == 0
        _isolated(p, "email_thread_status", acc, expect_b=1)
        assert _count_as(p.app_url, p.org_a, "SELECT count(*) FROM "
                         f"email_executed_rules {_BY_ACCOUNT}", {"a": acc}) == 0

    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_the_backfill_resolves_a_conversation_in_b(
        self, mode, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        """R8 of PR-B2, for the gap loop. The thread gets DONE from its
        status rule in org B, the backfill writes no rule log, and org A
        reads none of it."""
        _dc_mode(monkeypatch, decide_env, mode)
        _assert_non_priv(app_engine)
        p = promoted
        acc, tid, _mid = self._seed_conversation(p, label="gap")
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        _patch_providers(monkeypatch, _FakeProvider())
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                await replyzero_mod._maybe_classify_threads(acc)

        assert {leaf for leaf, _n in seen} == _LEAVES[mode], seen
        rows = _rows(p.admin_engine,
                     "SELECT thread_id, status, organization_id::text AS org "
                     f"FROM email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert rows == [{"thread_id": tid, "status": "DONE", "org": p.org_b}], rows
        _isolated(p, "email_thread_status", acc, expect_b=1)
        _isolated(p, "email_executed_rules", acc, expect_b=0)

    # ── PR-B3: the status ask in `on`, on a real pool ──

    def _seed_new_thread(self, p, *, label: str) -> tuple[str, str, str]:
        """An account of org B with the conversation rules Reply and Done,
        and a new thread of one inbox row from outside. The rule match picks
        a conversation rule, and the thread is not a conversation yet."""
        acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@t4a2b3.test")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_rules (account_id, name, instructions, "
                "enabled, system_type, created_at, organization_id) VALUES "
                "(CAST(:a AS uuid), 'Reply', 'a mail that asks a question', "
                "true, 'REPLY', :c, CAST(:o AS uuid))"),
                {"a": acc, "c": datetime.now(UTC) - timedelta(days=2),
                 "o": p.org_b})
        _seed_done_rule(p.admin_engine, org=p.org_b, account_id=acc)
        tid = f"t-b3-{label}-{acc}"
        mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                            thread_id=tid)
        return acc, tid, mid

    @pytest.mark.parametrize("path", _B3_PATHS)
    @pytest.mark.parametrize("job", ["runner", "backfill"])
    async def test_the_on_status_ask_holds_no_connection_in_b(
        self, job, path, promoted, app_engine, monkeypatch, decide_env,  # noqa: F811
    ):
        """R8 of PR-B3, the fence
        ``email-decision-core-no-session-across-the-on-status-ask`` on a real
        pool. In ``on``, each model call reads the pool of the shared engine,
        and no connection is checked out then. The job runs as
        ``acb_app_h3rls``, Done is the ONE live match in org B, and org A
        reads none of it."""
        _dc_mode(monkeypatch, decide_env, "on")
        _assert_non_priv(app_engine)
        p = promoted
        seed = self._seed_conversation if path == "first" else self._seed_new_thread
        acc, tid, mid = seed(p, label=f"on-{job}-{path}")
        seen: list[tuple[str, int]] = []
        held: list[int] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True,
                     on_ask=lambda: held.append(
                         common_db.get_engine().pool.checkedout()))
        _patch_providers(monkeypatch, _FakeProvider())
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                if job == "runner":
                    await runner_mod._run_rules_job(acc, 50, False, "scheduler")
                else:
                    await replyzero_mod._maybe_classify_threads(acc)

        assert ("decide", 0) in seen, f"the status ask went blind: {seen}"
        assert held == [0] * len(seen), (
            f"a connection was checked out during a model call: "
            f"{list(zip(seen, held, strict=True))}")
        rows = _rows(p.admin_engine,
                     "SELECT thread_id, status, organization_id::text AS org "
                     f"FROM email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert rows == [{"thread_id": tid, "status": "DONE", "org": p.org_b}], rows
        _isolated(p, "email_thread_status", acc, expect_b=1)
        logs = _rows(p.admin_engine,
                     "SELECT message_id::text AS mid, rule_name FROM "
                     f"email_executed_rules {_BY_ACCOUNT} AND status = 'APPLIED'",
                     {"a": acc})
        if job == "runner":
            assert logs == [{"mid": mid, "rule_name": "Done"}], logs
            assert _count_as(p.app_url, p.org_a, "SELECT count(*) FROM "
                             f"email_executed_rules {_BY_ACCOUNT}", {"a": acc}) == 0
        else:
            _isolated(p, "email_executed_rules", acc, expect_b=0)
