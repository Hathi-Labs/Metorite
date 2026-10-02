"""EM-T4a-0 — the request jobs bind a tenant.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4a-0.

Ten functions held the last twelve ``_get_db()`` sites of ``routes/email``
apart from ``mailbox_owner``. A request starts each one: a BackgroundTask, an
``asyncio.create_task`` inside a stream, or a plain call from a route. Each
one now opens ``_tenant_session()`` with the AMBIENT tenant, which the request
binds, and no block calls ``commit()``: a commit inside a block ends
``SET LOCAL``, and each statement after it runs with no tenant (mechanism (A)
of §10.4.2).

**Hermetic.**

* An AST fence: no ``get_db()`` call in ``routes/email`` other than the one
  tenant-discovery read of ``scheduler_hooks.mailbox_owner``. A companion
  test proves that the fence can fail.
* An AST fence over the ten jobs and the three helpers that lost a commit:
  no ``get_db()`` and no ``.commit()``, and each job opens ``_tenant_session``.
* With no tenant bound, each job opens no session, makes no provider or model
  call, and writes nothing. It logs the refusal, or the route raises it.
* The compose-assist stream runs the job in a task that it creates inside the
  response. That task keeps the organization of its request.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the non-privileged role
``acb_app_h3rls``. Each job runs for organization B, writes in B, and
organization A reads none of it. The provider and the model calls are fakes.

⚠️ **Limit (R7).** The site fence matches a call whose name ends in
``get_db``. An alias under another name (``from gateway.db import get_db as
x``) escapes it, as it escapes the regex ratchet of ``test_db_engine_seam.py``.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_request_jobs_tenancy.py -v -rs
"""
from __future__ import annotations

import ast
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import db as common_db
from acb_common.db import TenantUnbound, clear_tenant, current_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email import core as email_core
from gateway.routes.email.automation import cleanup as cleanup_mod
from gateway.routes.email.automation import drafting as drafting_mod
from gateway.routes.email.automation import replyzero as replyzero_mod
from gateway.routes.email.automation import runner as runner_mod
from gateway.routes.email.automation import senders as senders_mod
from gateway.routes.email.automation import voice_profile as voice_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``identity``, ``unbound``, ``promoted`` and ``app_engine`` are fixtures,
# used by name, so the imports are load-bearing even though they read as
# unused.
from tests.unit.test_email_automation_tenancy import (  # noqa: F401
    _BY_ACCOUNT,
    ORG,
    _bound,
    _FakeProvider,
    _isolated,
    _opens_tenant_session,
    _patch_providers,
    _rows,
    _scalar,
    _seed_message,
    _seed_settings,
    _tripwire,
    _violations,
    identity,
    unbound,
)
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_EMAIL = (Path(__file__).resolve().parents[2]
          / "apps/services/gateway/gateway/routes/email")

#: The one unbound read that stays in ``routes/email``: the tenant-discovery
#: read of ``mailbox_owner`` (``test_db_engine_seam.H2_TENANT_DISCOVERY_SITES``).
#: Keyed by (file, function), with the count of its sites.
ALLOWED_UNBOUND: dict[tuple[str, str], int] = {
    ("scheduler_hooks.py", "mailbox_owner"): 1,
}

#: The ten jobs of EM-T4a-0, keyed by file. They held twelve sites:
#: ``_backfill_and_clean_job`` and ``_reclassify_reply_zero_job`` held two each.
#: The R7 fence ``email-request-jobs-bind-a-tenant`` reads this table.
THE_JOBS: dict[str, tuple[str, ...]] = {
    "automation/drafting.py": (
        "_cleanup_thread_drafts", "_learn_from_sent", "_compose_assist_run",
    ),
    "automation/cleanup.py": ("_backfill_and_clean_job",),
    "automation/voice_profile.py": ("_build_voice_profile_job",),
    "automation/replyzero.py": (
        "_reconcile_labels_bg", "_reclassify_reply_zero_job",
    ),
    "automation/senders.py": ("_create_block_filter", "_remove_block_filter"),
    "automation/runner.py": ("_process_past_emails_job",),
}

#: Helpers that a job calls inside a block, and that committed before
#: EM-T4a-0. Each one now leaves the commit to the block.
THE_HELPERS: dict[str, tuple[str, ...]] = {
    "automation/drafting.py": ("_maybe_refresh_learned_style",),
    "automation/cleanup.py": ("_mark_history_held_back",),
    "automation/runner.py": ("_project_thread_status_for_backfill",),
}


# ── hermetic: the site fence over routes/email ──────────────────────────────


def _call_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def unbound_sites(source: str) -> list[tuple[str, int]]:
    """``(enclosing function, line)`` for each ``get_db()`` call in a module.

    A call at module level reports ``<module>``. ``core._get_db()`` counts the
    same as ``_get_db()``, and so does a call that nobody awaits."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call) and _call_name(child).endswith("get_db"):
                found.append((owner, child.lineno))
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


def email_unbound_sites() -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for path in sorted(_EMAIL.rglob("*.py")):
        rel = path.relative_to(_EMAIL).as_posix()
        for owner, _line in unbound_sites(path.read_text(encoding="utf-8-sig")):
            counts[(rel, owner)] = counts.get((rel, owner), 0) + 1
    return counts


def test_routes_email_holds_no_unbound_session_but_mailbox_owner():
    """R7 fence ``email-request-jobs-bind-a-tenant``, part 1. A new site is a
    job that reads zero rows under FORCE RLS. A missing ``mailbox_owner`` site
    is a stale entry."""
    measured = email_unbound_sites()
    assert measured == ALLOWED_UNBOUND, (
        f"routes/email get_db() sites drifted: {measured}. A get_db() session "
        "binds no tenant, so it reads zero rows under FORCE RLS. Open "
        "`async with _tenant_session() as db:` instead (spec §10.4.6, "
        "EM-T4a-0). Only the discovery read of mailbox_owner may stay."
    )


def test_the_site_fence_can_fail():
    planted = (
        "from gateway.routes.email import core\n"
        "X = _get_db()\n"
        "async def job(account_id):\n"
        "    db = await _get_db()\n"
        "    async def inner():\n"
        "        return core._get_db()\n"
        "    async with _tenant_session() as db:\n"
        "        await db.execute(x)\n"
        "\n"
        "async def clean(account_id):\n"
        "    async with _tenant_session() as db:\n"
        "        await db.execute(x)\n"
        "    _get_db_url()\n"
    )
    assert unbound_sites(planted) == [
        ("<module>", 2), ("job", 4), ("inner", 6)]
    assert unbound_sites(
        "async def clean():\n    async with _tenant_session() as db:\n"
        "        await db.execute(x)\n") == []


# ── hermetic: the job fence ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rel", "name"),
    [(rel, name) for rel, names in THE_JOBS.items() for name in names],
)
def test_each_job_opens_a_tenant_session_and_never_commits(rel: str, name: str):
    """R7 fence ``email-request-jobs-bind-a-tenant``, part 2."""
    source = (_EMAIL / rel).read_text(encoding="utf-8")
    hits = _violations(source, name)
    assert hits == [], (
        f"{rel}::{name} calls {hits}. A get_db() session binds no tenant, and "
        "a commit inside a tenant_session ends SET LOCAL. Open another "
        "_tenant_session() block instead (spec §10.4.2, mechanism (A))."
    )
    assert _opens_tenant_session(source, name), (
        f"{rel}::{name} opens no _tenant_session"
    )


@pytest.mark.parametrize(
    ("rel", "name"),
    [(rel, name) for rel, names in THE_HELPERS.items() for name in names],
)
def test_each_helper_leaves_the_commit_to_its_block(rel: str, name: str):
    source = (_EMAIL / rel).read_text(encoding="utf-8")
    assert _violations(source, name) == [], (
        f"{rel}::{name} commits or opens get_db(). Its caller holds a "
        "_tenant_session block, and the seam commits on a clean exit."
    )


def test_the_tables_name_ten_jobs_and_three_helpers():
    assert sum(len(v) for v in THE_JOBS.values()) == 10
    assert sum(len(v) for v in THE_HELPERS.values()) == 3


# ── hermetic: with no tenant, each job stops before any session ────────────


_ACC = "acc-em-t4a0"
_OWNER = "priya@fracktal.in"


class _Log:
    """Records each call to a module logger as ``(level, event, fields)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __getattr__(self, level: str):
        def _record(event: str, *_a, **fields) -> None:
            self.events.append((level, event, fields))
        return _record


async def _no_tenant_backfill():
    token = cleanup_mod._SWEEP_JOBS.start(
        _ACC, owner=_OWNER, status="running", phase="downloading")
    await cleanup_mod._backfill_and_clean_job(_ACC, None, _OWNER, token)
    return cleanup_mod._SWEEP_JOBS.pop(_ACC)


async def _no_tenant_voice():
    token = voice_mod._VOICE_JOBS.start(
        _ACC, owner=_OWNER, status="running", phase="collecting")
    await voice_mod._build_voice_profile_job(
        _ACC, ["sent"], None, None, True, token)
    return voice_mod._VOICE_JOBS.pop(_ACC)


async def _no_tenant_reclassify():
    token = replyzero_mod._RECLASSIFY_JOBS.start(_ACC, status="running")
    await replyzero_mod._reclassify_reply_zero_job(_ACC, token=token)
    return replyzero_mod._RECLASSIFY_JOBS.pop(_ACC)


async def _no_tenant_process_past():
    token = runner_mod._past_job_start(
        _ACC, _OWNER, 0, False, downloading=True)
    now = datetime.now(UTC)
    await runner_mod._process_past_emails_job(
        _ACC, now - timedelta(days=7), now, 50, False, _OWNER,
        job_token=token)
    return runner_mod._PAST_JOBS.pop(_ACC)


async def _no_tenant_compose():
    return await drafting_mod._compose_assist_run(
        drafting_mod.ComposeAssistRequest(account_id=_ACC, body="hi"),
        SimpleNamespace(email=_OWNER))


_NO_TENANT_CALLS = {
    "_cleanup_thread_drafts":
        lambda: drafting_mod._cleanup_thread_drafts(_ACC, "t-1"),
    "_learn_from_sent":
        lambda: drafting_mod._learn_from_sent(_ACC, "t-1", "Thanks, Priya"),
    "_compose_assist_run": _no_tenant_compose,
    "_backfill_and_clean_job": _no_tenant_backfill,
    "_build_voice_profile_job": _no_tenant_voice,
    "_reconcile_labels_bg":
        lambda: replyzero_mod._reconcile_labels_bg(_ACC, "t-1", "Done"),
    "_reclassify_reply_zero_job": _no_tenant_reclassify,
    "_create_block_filter":
        lambda: senders_mod._create_block_filter(_ACC, "spam@x.test"),
    "_remove_block_filter":
        lambda: senders_mod._remove_block_filter(_ACC, "spam@x.test"),
    "_process_past_emails_job": _no_tenant_process_past,
}

#: The jobs that keep a progress row: the row must record the refusal.
_TRACKED = {"_backfill_and_clean_job", "_build_voice_profile_job",
            "_reclassify_reply_zero_job", "_process_past_emails_job"}

_MODULES = (drafting_mod, cleanup_mod, voice_mod, replyzero_mod, senders_mod,
            runner_mod, email_core)


def test_every_job_has_a_no_tenant_case():
    assert set(_NO_TENANT_CALLS) == {
        n for names in THE_JOBS.values() for n in names}


@pytest.mark.parametrize("name", sorted(_NO_TENANT_CALLS))
async def test_with_no_tenant_each_job_opens_no_session(
    name, monkeypatch, unbound,  # noqa: F811
):
    """The REAL ``tenant_session`` runs here. It raises ``TenantUnbound``
    before it asks the factory for a session. A session factory, a
    ``get_db``, a provider or a model call that runs is a failure."""
    opened: list[str] = []
    monkeypatch.setattr(common_db, "get_session_factory",
                        _tripwire(opened, "get_session_factory"))
    log = _Log()
    for mod in _MODULES:
        monkeypatch.setattr(mod, "_get_db", _tripwire(opened, "_get_db"),
                            raising=False)
        monkeypatch.setattr(mod, "_instantiate_provider",
                            _tripwire(opened, "_instantiate_provider"),
                            raising=False)
        monkeypatch.setattr(mod, "_log", log, raising=False)
    for mod, fn in ((drafting_mod, "_llm_extract_reply_memories"),
                    (drafting_mod, "_llm_compose_assist"),
                    (drafting_mod, "_agent_draft_reply"),
                    (voice_mod, "_llm_observe_batch"),
                    (voice_mod, "_llm_synthesize_profile"),
                    (replyzero_mod, "_maybe_classify_threads")):
        monkeypatch.setattr(mod, fn, _tripwire(opened, fn))
    assert current_tenant() is None

    if name == "_compose_assist_run":
        with pytest.raises(TenantUnbound):
            await _NO_TENANT_CALLS[name]()
        assert opened == [], f"{name} reached {opened} with no tenant bound"
        return

    row = await _NO_TENANT_CALLS[name]()
    assert opened == [], f"{name} reached {opened} with no tenant bound"
    refusals = [fields for level, _event, fields in log.events
                if level == "warning"
                and "no tenant bound" in str(fields.get("error", ""))]
    assert refusals, (
        f"{name} stopped with no tenant but logged no refusal: {log.events}"
    )
    if name in _TRACKED:
        assert row["status"] == "error", row
        assert "no tenant bound" in row["error"], row


# ── hermetic: the compose stream keeps the organization of its request ─────


def test_the_compose_stream_task_keeps_the_organization_of_its_request(
    identity, monkeypatch,  # noqa: F811
):
    """``compose_assist_stream`` runs ``_compose_assist_run`` in a task that
    ``asyncio.create_task`` starts inside the streamed response. The task
    copies the context of the request, so its block opens under the
    organization of the member. The ``_tenant_session`` double records the
    tenant that is bound when the block opens.

    Sync on purpose: ``TestClient`` runs its own loop."""
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from gateway.main import TenantScopeMiddleware

    opened_under: list[str | None] = []

    @asynccontextmanager
    async def _recording_session():
        opened_under.append(current_tenant())
        yield AsyncMock()

    monkeypatch.setattr(drafting_mod, "_tenant_session", _recording_session)
    monkeypatch.setattr(drafting_mod, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting_mod, "_account_models", AsyncMock(
        return_value={"rule": "tier-fast", "draft": "tier-powerful",
                      "compose": "tier-fast", "chat": "tier-powerful"}))
    monkeypatch.setattr(drafting_mod, "_llm_compose_assist",
                        AsyncMock(return_value="Hello Priya"))

    app = FastAPI()
    app.add_middleware(TenantScopeMiddleware)

    @app.post("/stream")
    async def stream(user=Depends(identity.get_current_user)):
        return await drafting_mod.compose_assist_stream(
            drafting_mod.ComposeAssistRequest(account_id=_ACC, body=""), user)

    token = clear_tenant()
    try:
        with TestClient(app) as client:
            answer = client.post("/stream", headers={
                "X-User-Email": _OWNER, "Authorization": "Bearer tok"})
    finally:
        release_tenant(token)

    assert answer.status_code == 200
    events = [json.loads(line[len("data: "):])
              for line in answer.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == {"type": "done", "draft": "Hello Priya"}, events
    assert opened_under == [ORG], (
        "the stream task opened its block with no tenant, or did not run"
    )


# ── R8 helpers ──────────────────────────────────────────────────────────────


class _JobProvider(_FakeProvider):
    """The EM-T1b-2 fake, plus the calls of the request jobs."""

    async def trash_message(self, pmid):
        self.calls.append(f"trash:{pmid}")

    async def create_filter(self, from_email, archive=True, label=None):
        self.calls.append(f"create_filter:{from_email}")
        return "filter-b"

    async def delete_filter(self, fid):
        self.calls.append(f"delete_filter:{fid}")


def _seed_body(admin, *, org: str, account_id: str, folder: str, body: str,
               thread_id: str | None = None,
               received_at: datetime | None = None,
               created_at: datetime | None = None) -> str:
    """One message with a body of our choice. ``_seed_message`` writes the
    fixed body 'body', which is too short for the voice profile."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "body_text, snippet, received_at, created_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'hello', :b, :b, :rcv, :crt, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "tid": thread_id, "f": folder,
             "frm": json.dumps({"email": "s@em-t4a0.test", "name": "S"}),
             "to": json.dumps([{"email": "to@ext-em-t4a0.test"}]), "b": body,
             "rcv": received_at or datetime.now(UTC),
             "crt": created_at or datetime.now(UTC), "o": org}).scalar_one())


def _insert(admin, sql: str, params: dict) -> None:
    with admin.begin() as c:
        c.execute(text(sql), params)


def _app_dsn(p) -> str:
    return p.app_url.render_as_string(hide_password=False)


_B_OWNER = "b@em-t4a0.test"


# ── R8: each job writes in its own tenant ──────────────────────────────────


@_DB_GATE
class TestTheRequestJobsWriteTheirOwnTenant:

    async def test_compose_assist_finds_the_account_of_a_member_of_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The live defect. Before EM-T4a-0 the unbound session read zero
        rows, so compose assist answered "Account not found" to every
        member. Bound to A, the same call must still refuse."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc,
                       signature="Sig-B")
        compose = AsyncMock(return_value="Hello there")
        monkeypatch.setattr(drafting_mod, "_llm_compose_assist", compose)
        req = drafting_mod.ComposeAssistRequest(account_id=acc, body="")
        member = SimpleNamespace(email=_B_OWNER)
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                got = await drafting_mod._compose_assist_run(req, member)
            with _bound(p.org_a), pytest.raises(HTTPException) as refused:
                await drafting_mod._compose_assist_run(req, member)

        assert got == {"draft": "Hello there\n\nSig-B"}, got
        assert compose.await_args.kwargs["signature"] == "Sig-B", (
            "the settings of org B were not read"
        )
        assert refused.value.status_code == 404

    async def test_process_past_stamps_its_range_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Phase 0 reads, each row is its own block (the former per-row
        commit), and each thread projection is its own block (the former
        per-thread commit). The second row and both projections are the
        writes that came after a former commit."""
        import email_ingestion.scheduler as sched_mod

        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        now = datetime.now(UTC)
        mids = [
            _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                          thread_id=f"t-pp{i}-{acc}",
                          received_at=now - timedelta(days=2 - i))
            for i in range(2)
        ]
        _patch_providers(monkeypatch, _JobProvider())
        monkeypatch.setattr(runner_mod, "_match_email_to_rule",
                            AsyncMock(return_value=None))
        monkeypatch.setattr(sched_mod, "_sync_account", AsyncMock(return_value={}))
        token = runner_mod._past_job_start(acc, _B_OWNER, 0, False,
                                           downloading=True)
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                await runner_mod._process_past_emails_job(
                    acc, now - timedelta(days=7), now + timedelta(days=1), 50,
                    False, _B_OWNER, job_token=token)
        job = runner_mod._PAST_JOBS.pop(acc)

        assert job["status"] == "done" and job["processed"] == 2, job
        stamped = _rows(p.admin_engine,
                        "SELECT id::text AS id, rules_processed_at FROM "
                        f"email_messages {_BY_ACCOUNT}", {"a": acc})
        assert {r["id"] for r in stamped} == set(mids)
        assert all(r["rules_processed_at"] is not None for r in stamped), (
            "a row of org B was left unstamped — its block ran with no tenant"
        )
        logs = _rows(p.admin_engine,
                     "SELECT status, organization_id::text AS org FROM "
                     f"email_executed_rules {_BY_ACCOUNT}", {"a": acc})
        assert [(r["status"], r["org"]) for r in logs] == [
            ("SKIPPED", p.org_b)] * 2
        statuses = _rows(p.admin_engine,
                         "SELECT status, organization_id::text AS org FROM "
                         f"email_thread_status {_BY_ACCOUNT}", {"a": acc})
        assert [(r["status"], r["org"]) for r in statuses] == [
            ("FYI", p.org_b)] * 2, statuses
        _isolated(p, "email_executed_rules", acc, expect_b=2)
        _isolated(p, "email_thread_status", acc, expect_b=2)
        _isolated(p, "email_messages", acc, expect_b=2)

    async def test_backfill_holds_history_back_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The second block counts what the sync added and holds it back.
        Unbound, both counts read zero and the hold-back touches no row."""
        import email_ingestion.scheduler as sched_mod

        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        kept = _seed_body(p.admin_engine, org=p.org_b, account_id=acc,
                          folder="inbox", body="current mail")
        downloaded: list[str] = []
        sync_tenant: list[str | None] = []

        async def _fake_sync(account_id, *, organization_id=None, deep=None,
                             since=None):
            sync_tenant.append(current_tenant())
            downloaded.append(_seed_body(
                p.admin_engine, org=p.org_b, account_id=account_id,
                folder="inbox", body="old mail",
                received_at=datetime(2020, 1, 1, tzinfo=UTC),
                created_at=datetime.now(UTC) + timedelta(minutes=5)))
            return {}

        monkeypatch.setattr(sched_mod, "_sync_account", _fake_sync)
        _patch_providers(monkeypatch, _JobProvider())
        token = cleanup_mod._SWEEP_JOBS.start(
            acc, owner=_B_OWNER, status="running", phase="downloading")
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                await cleanup_mod._backfill_and_clean_job(
                    acc, None, _B_OWNER, token)
        job = cleanup_mod._SWEEP_JOBS.pop(acc)

        assert job["status"] == "done", job
        assert (job["synced"], job["held_back"]) == (1, 1), job
        assert sync_tenant == [p.org_b]
        held = {r["id"]: r["rules_held_back_at"] for r in _rows(
            p.admin_engine,
            "SELECT id::text AS id, rules_held_back_at FROM email_messages "
            f"{_BY_ACCOUNT}", {"a": acc})}
        assert held[downloaded[0]] is not None, "the history was not held back"
        assert held[kept] is None
        _isolated(p, "email_messages", acc, expect_b=2)

    async def test_voice_profile_builds_and_fails_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The build writes the profile and the knowledge suggestion in B. A
        second build fails, and the FAILED row is the write in a block of its
        own after the failed block rolled back."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        for i in range(2):
            _seed_body(p.admin_engine, org=p.org_b, account_id=acc,
                       folder="sent",
                       body=f"Hi Asha, thanks for the quick reply number {i}. "
                            "I will send the quote by Friday. Regards, Priya")
        _insert(p.admin_engine,
                "INSERT INTO email_voice_profiles (account_id, status, "
                "organization_id) VALUES (CAST(:a AS uuid), 'BUILDING', "
                "CAST(:o AS uuid))", {"a": acc, "o": p.org_b})
        monkeypatch.setattr(voice_mod, "_llm_observe_batch", AsyncMock(
            return_value={"style_notes": ["Short and warm."],
                          "greetings": ["Hi <first name>,"],
                          "signoffs": ["Regards,"], "phrases": [],
                          "facts": [{"title": "Quotes",
                                     "content": "Quotes go out by Friday."}]}))
        monkeypatch.setattr(voice_mod, "_llm_synthesize_profile", AsyncMock(
            return_value=({"tone": "warm"}, "Keep it short.")))
        profile_sql = ("SELECT status, style_guide, analyzed_count, last_error, "
                       "organization_id::text AS org FROM email_voice_profiles "
                       f"{_BY_ACCOUNT}")
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                token = voice_mod._VOICE_JOBS.start(
                    acc, owner=_B_OWNER, status="running")
                await voice_mod._build_voice_profile_job(
                    acc, ["sent"], None, None, True, token)
                built = _rows(p.admin_engine, profile_sql, {"a": acc})
                done = voice_mod._VOICE_JOBS.pop(acc)

                monkeypatch.setattr(voice_mod, "_llm_synthesize_profile",
                                    AsyncMock(side_effect=RuntimeError("model down")))
                token = voice_mod._VOICE_JOBS.start(
                    acc, owner=_B_OWNER, status="running")
                await voice_mod._build_voice_profile_job(
                    acc, ["sent"], None, None, True, token)
        failed = voice_mod._VOICE_JOBS.pop(acc)

        assert done["status"] == "done", done
        assert built == [{"status": "READY", "style_guide": "Keep it short.",
                          "analyzed_count": 2, "last_error": None,
                          "org": p.org_b}], built
        kb = _rows(p.admin_engine,
                   "SELECT title, status, organization_id::text AS org FROM "
                   f"email_knowledge {_BY_ACCOUNT}", {"a": acc})
        assert kb == [{"title": "Quotes", "status": "suggested",
                       "org": p.org_b}], kb
        assert failed["status"] == "error", failed
        after = _rows(p.admin_engine, profile_sql, {"a": acc})
        assert after[0]["status"] == "FAILED", after
        assert "model down" in after[0]["last_error"]
        _isolated(p, "email_voice_profiles", acc, expect_b=1)
        _isolated(p, "email_knowledge", acc, expect_b=1)

    async def test_reclassify_and_reconcile_write_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Reclassify: the DELETE is block one and the count is block two
        (the former commit sat between them). Reconcile: the label mirror and
        the rotated credentials land in one block."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        done_tid, open_tid = f"t-done-{acc}", f"t-open-{acc}"
        done_mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                 thread_id=done_tid)
        open_mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                 thread_id=open_tid, categories=["Reply"])
        for tid, mid, status in ((done_tid, done_mid, "DONE"),
                                 (open_tid, open_mid, "NEEDS_REPLY")):
            _insert(p.admin_engine,
                    "INSERT INTO email_thread_status (account_id, thread_id, "
                    "status, last_message_id, last_message_at, reason, "
                    "organization_id) VALUES (CAST(:a AS uuid), :t, :s, "
                    "CAST(:m AS uuid), now(), 'seed', CAST(:o AS uuid))",
                    {"a": acc, "t": tid, "s": status, "m": mid, "o": p.org_b})
        classify = AsyncMock()
        monkeypatch.setattr(replyzero_mod, "_maybe_classify_threads", classify)
        _patch_providers(monkeypatch, _JobProvider())
        token = replyzero_mod._RECLASSIFY_JOBS.start(acc, status="running")
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                await replyzero_mod._reclassify_reply_zero_job(acc, token=token)
                await replyzero_mod._reconcile_labels_bg(acc, open_tid, "Done")
        job = replyzero_mod._RECLASSIFY_JOBS.pop(acc)

        assert job["status"] == "done", job
        assert (job["total"], job["remaining"]) == (1, 1), (
            f"the backlog count read {job} — the count block ran unbound"
        )
        classify.assert_awaited_once_with(acc)
        left = _rows(p.admin_engine,
                     "SELECT thread_id, status FROM email_thread_status "
                     f"{_BY_ACCOUNT}", {"a": acc})
        assert left == [{"thread_id": done_tid, "status": "DONE"}], left
        cats = _scalar(p.admin_engine,
                       "SELECT categories FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": open_mid})
        assert "Reply" not in cats and "Done" in cats, cats
        creds = _scalar(p.admin_engine,
                        "SELECT credentials_encrypted FROM email_accounts "
                        "WHERE id = CAST(:a AS uuid)", {"a": acc})
        assert creds.startswith("enc:"), "the rotated credentials did not land"
        _isolated(p, "email_thread_status", acc, expect_b=1)

    async def test_block_filter_create_and_remove_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        sender = "promo@shop-em-t4a0.test"
        _insert(p.admin_engine,
                "INSERT INTO email_newsletters (account_id, email, status, "
                "organization_id) VALUES (CAST(:a AS uuid), :e, "
                "'AUTO_ARCHIVED', CAST(:o AS uuid))",
                {"a": acc, "e": sender, "o": p.org_b})
        provider = _JobProvider()
        _patch_providers(monkeypatch, provider)
        fid_sql = ("SELECT auto_archive_filter_id FROM email_newsletters "
                   f"{_BY_ACCOUNT}")
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                await senders_mod._create_block_filter(acc, sender)
                created = _scalar(p.admin_engine, fid_sql, {"a": acc})
                await senders_mod._remove_block_filter(acc, sender)

        assert created == "filter-b", (
            "the filter id did not land in org B — the block ran unbound"
        )
        assert _scalar(p.admin_engine, fid_sql, {"a": acc}) is None
        assert f"create_filter:{sender}" in provider.calls
        assert "delete_filter:filter-b" in provider.calls
        _isolated(p, "email_newsletters", acc, expect_b=1)

    async def test_learn_from_sent_and_draft_cleanup_write_in_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Learn: the draft DELETE is block one (the former first commit) and
        the learned pattern is the write after it. Cleanup: the provider
        trashes the draft and its mirror row goes, in one block."""
        import acb_memory

        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        tid = f"t-learn-{acc}"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                      thread_id=tid, sender="asha@ext-em-t4a0.test")
        draft_mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                  thread_id=tid, folder="drafts")
        _insert(p.admin_engine,
                "INSERT INTO email_ai_drafts (account_id, thread_id, "
                "draft_text, organization_id) VALUES (CAST(:a AS uuid), :t, "
                "'The draft the assistant wrote.', CAST(:o AS uuid))",
                {"a": acc, "t": tid, "o": p.org_b})
        monkeypatch.setattr(drafting_mod, "_llm_extract_reply_memories",
                            AsyncMock(return_value=[{
                                "content": "Sign off with the first name.",
                                "kind": "PREFERENCE", "scope": "GLOBAL",
                                "topic": ""}]))
        monkeypatch.setattr(acb_memory, "add_memories_background", AsyncMock())
        provider = _JobProvider()
        _patch_providers(monkeypatch, provider)
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                await drafting_mod._learn_from_sent(
                    acc, tid, "A reply that the member rewrote in full.")
                await drafting_mod._cleanup_thread_drafts(acc, tid)

        assert _scalar(p.admin_engine,
                       f"SELECT count(*) FROM email_ai_drafts {_BY_ACCOUNT}",
                       {"a": acc}) == 0, "the stored draft was not consumed"
        learned = _rows(p.admin_engine,
                        "SELECT pattern, organization_id::text AS org FROM "
                        f"email_learned_patterns {_BY_ACCOUNT}", {"a": acc})
        assert learned == [{"pattern": "Sign off with the first name.",
                            "org": p.org_b}], learned
        assert _scalar(p.admin_engine,
                       "SELECT count(*) FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": draft_mid}) == 0
        assert any(c.startswith("trash:") for c in provider.calls)
        _isolated(p, "email_learned_patterns", acc, expect_b=1)
        _isolated(p, "email_messages", acc, expect_b=1)


@_DB_GATE
class TestTheSwallowedWritesKeepTheBlock:

    async def test_a_failed_draft_store_and_style_refresh_leave_the_block(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Both helpers swallow their own failure, and both now run inside a
        block that the seam commits. Each fails on a REAL statement here: a
        NUL byte, which Postgres refuses in ``text``. Their savepoints roll
        back, so the next write of the block lands and the tenant stays.
        Without a savepoint the block is aborted and the commit fails."""
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_B_OWNER)
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc)
        for i in range(drafting_mod._MIN_STYLE_EVIDENCE):
            _insert(p.admin_engine,
                    "INSERT INTO email_learned_patterns (account_id, pattern, "
                    "kind, scope_type, scope_value, is_style_evidence, "
                    "organization_id) VALUES (CAST(:a AS uuid), :p, "
                    "'PREFERENCE', 'GLOBAL', '', true, CAST(:o AS uuid))",
                    {"a": acc, "p": f"preference {i}", "o": p.org_b})
        monkeypatch.setattr(drafting_mod, "_llm_summarize_writing_style",
                            AsyncMock(return_value="Short" + chr(0)))
        async with tenant_engine_scope(_app_dsn(p)), \
                common_db.tenant_session(p.org_b) as db:
            await drafting_mod._maybe_refresh_learned_style(db, acc)
            await drafting_mod._store_ai_draft(
                db, acc, "t-bad", "draft" + chr(0), commit=False)
            await drafting_mod._store_ai_draft(
                db, acc, "t-good", "a good draft", commit=False)
            after = (await db.execute(text(
                "SELECT current_setting('app.tenant_id', true)"))).scalar()

        assert after == p.org_b
        stored = _rows(p.admin_engine,
                       "SELECT thread_id, organization_id::text AS org FROM "
                       f"email_ai_drafts {_BY_ACCOUNT}", {"a": acc})
        assert stored == [{"thread_id": "t-good", "org": p.org_b}], stored
        assert _scalar(p.admin_engine,
                       "SELECT learned_writing_style FROM "
                       f"email_assistant_settings {_BY_ACCOUNT}",
                       {"a": acc}) is None
