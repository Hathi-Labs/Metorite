"""A background job's model call names a member, so the Router can bill it. H-152.

Spec: ``ai_metering_and_analytics.md`` §5 · H-73 (the member proof) · H-152
(the per-box deployment key) · H-181 (memberless runs).

🔴 **Under the deployment key a call with no member is a 400.** The Console
derives the organization from ``X-CC-Member``
(``auth.organization_from_key_or_deployment``). A signed-in request binds its
member per request, but a schedule, a webhook or a sandbox has no session, so
it reached the Router with nobody named.

The answer is option (a): the member the job belongs to, read from OUR tables
and never from a request body. Each class below pins one path.

Run::

    uv run pytest tests/unit/test_background_ai_member.py -v

The ``TestTheBillingMemberSqlOnARealDatabase`` class needs R8's database::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_common import bind_run_context, get_run_context, run_context_scope
from acb_llm.attribution import attribution_headers

ROOT = Path(__file__).resolve().parents[2]
SECRET = "test-session-secret"


@pytest.fixture(autouse=True)
def _clean_context():
    import structlog

    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def secret(monkeypatch):
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "gateway_session_secret", SECRET, raising=False)
    return SECRET


# ── The workflow agent node ─────────────────────────────────────────────────


class _Row(SimpleNamespace):
    pass


class _FakeDb:
    """Answers the billing-member read, and records what it was asked."""

    def __init__(self, row):
        self.row = row
        self.params: list[dict] = []

    async def execute(self, _sql, params=None):
        self.params.append(dict(params or {}))
        row = self.row
        return SimpleNamespace(fetchone=lambda: row)

    async def close(self):
        return None


class _Stop(BaseException):
    """Ends the run right after it bound its member.

    A ``BaseException`` so the executor's ``except Exception`` arm, which
    would try a self-mutation, never sees it.
    """


def _capture_run(monkeypatch) -> dict:
    """Run the REAL ``run_agent`` and record the Router headers it would send.

    ⚠️ Not a stub of ``run_agent``. The executor binds the run's member
    itself, from ``session_user``, and that binding is what this proves. Only
    the agent load is replaced, and it reads the headers at the point a model
    call would.
    """
    from orchestrator import executor

    seen: dict = {}

    @contextlib.contextmanager
    def _fake_load(*_a, **_kw):
        seen.update(attribution_headers())
        seen["_ctx"] = dict(get_run_context())
        raise _Stop
        yield  # pragma: no cover

    monkeypatch.setattr(executor, "load_agent", _fake_load)
    return seen


def _run_node(workflow_id: str, *, bound: dict | None = None):
    from gateway.routes.workflows import service

    async def go():
        with run_context_scope():
            if bound:
                bind_run_context(**bound)
            node = service._agent_runner(workflow_id)
            with contextlib.suppress(_Stop):
                await node("some-agent", "hello", None)

    asyncio.run(go())


class TestTheWorkflowAgentNode:
    """🔴 ``workflows/service.py`` called ``run_agent`` with no member.

    Mutation-checked 2026-09-28: pass ``session_user=None`` in
    ``_agent_runner`` and the scheduled, the unverified and the payload
    tests fail. The manual test still passes, because the clicker is
    inherited from the request, as it was before this change. Drop the
    ``member_verified`` check in ``_workflow_billing_member`` and
    ``test_an_UNVERIFIED_ambient_member_is_replaced_by_the_owner`` fails.
    """

    def test_a_SCHEDULED_run_bills_the_owner_proven(self, monkeypatch, secret):
        from acb_auth.member_proof import verify_member
        from gateway.routes.workflows import service

        db = _FakeDb(_Row(owner_email="owner@acme.com", ambient_email=None))

        async def _db():
            return db

        monkeypatch.setattr(service, "_get_db", _db)
        seen = _capture_run(monkeypatch)
        _run_node("11111111-1111-1111-1111-111111111111")

        assert seen.get("X-CC-Member") == "owner@acme.com"
        assert verify_member(seen["X-CC-Member-Proof"], secret) == "owner@acme.com"
        assert db.params[0]["ambient"] == "", "a scheduled run has nobody bound"

    def test_a_MANUAL_run_bills_the_person_who_pressed_Run(self, monkeypatch, secret):
        """The clicker is verified by the session and is in the workflow's
        organization, so the SQL hands them back and the bill names them."""
        from gateway.routes.workflows import service

        db = _FakeDb(_Row(owner_email="owner@acme.com",
                          ambient_email="dana@acme.com"))

        async def _db():
            return db

        monkeypatch.setattr(service, "_get_db", _db)
        seen = _capture_run(monkeypatch)
        _run_node("wf", bound={"user": "dana@acme.com", "member_verified": True})

        assert db.params[0]["ambient"] == "dana@acme.com"
        assert seen.get("X-CC-Member") == "dana@acme.com"
        assert seen.get("X-CC-Member-Proof")

    def test_an_UNVERIFIED_ambient_member_is_replaced_by_the_owner(self, monkeypatch):
        """🔴 A body claim (an event payload's ``user_email``) must never reach
        the SQL. The member picks the organization that pays."""
        from gateway.routes.workflows import service

        db = _FakeDb(_Row(owner_email="owner@acme.com", ambient_email=None))

        async def _db():
            return db

        monkeypatch.setattr(service, "_get_db", _db)
        seen = _capture_run(monkeypatch)
        _run_node("wf", bound={"user": "mallory@evil.com"})

        assert db.params[0]["ambient"] == ""
        assert seen.get("X-CC-Member") == "owner@acme.com"

    def test_an_UNRESOLVABLE_owner_runs_as_before_and_is_logged(self, monkeypatch):
        """No active owner is not a guess. The node runs memberless, as it did
        before, and the deployment arm then refuses it loudly."""
        from gateway.routes.workflows import service

        async def _db():
            return _FakeDb(None)

        monkeypatch.setattr(service, "_get_db", _db)
        seen = _capture_run(monkeypatch)
        _run_node("wf")
        assert "X-CC-Member" not in seen

    def test_the_payload_carries_NO_member(self, monkeypatch):
        """The member travels as a keyword, never in the payload a tool or a
        prompt can read and rewrite."""
        from gateway.routes.workflows import service
        from orchestrator import executor

        calls: list = []

        async def _fake_run_agent(agent, payload, **kw):
            calls.append((payload, kw))
            return {"result": "ok"}

        async def _db():
            return _FakeDb(_Row(owner_email="owner@acme.com", ambient_email=None))

        monkeypatch.setattr(service, "_get_db", _db)
        monkeypatch.setattr(executor, "run_agent", _fake_run_agent)
        asyncio.run(service._agent_runner("wf")("a", "m", None))
        payload, kw = calls[0]
        assert "user_email" not in payload and "user_id" not in payload
        assert kw["session_user"] == "owner@acme.com"


class TestTheWholeWorkflowRunActsForItsMember:
    """A tool node can emit an event whose sink starts an agent run
    (``projects/agent_dispatch.py``). That run inherits the workflow task's
    member, so ``_execute_run`` binds it for the WHOLE run.

    Mutation-checked 2026-09-28: delete the ``enter_context`` in
    ``_execute_run`` and the first test fails.
    """

    @staticmethod
    def _run(monkeypatch, row, bound=None):
        from gateway.routes.workflows import service

        seen: dict = {}

        async def _db():
            return _FakeDb(row)

        async def _execute(*_a, **_kw):
            seen.update(attribution_headers())
            return SimpleNamespace(status="completed", error=None, state={},
                                   node_results={}, outputs=[],
                                   paused_nodes=[], wait_until={})

        async def _noop(*_a, **_kw):
            return None

        monkeypatch.setattr(service, "_get_db", _db)
        monkeypatch.setattr(service, "execute_workflow", _execute)
        monkeypatch.setattr(service, "_finish_run", _noop)
        monkeypatch.setattr(service, "evaluate_automation_health", _noop)
        monkeypatch.setattr(service, "publish_workflow_activity",
                            lambda *a, **k: None)

        async def go():
            if bound:
                bind_run_context(**bound)
            await service._execute_run(
                run_id="r-1", workflow_id="wf", workflow_name="wf",
                serialized={}, trigger_payload={}, variables={},
                started_by="scheduler", trigger_kind="schedule")
            return dict(get_run_context())

        after = asyncio.run(go())
        return seen, after

    def test_a_scheduled_run_acts_as_the_OWNER_throughout(self, monkeypatch):
        seen, after = self._run(
            monkeypatch, _Row(owner_email="owner@acme.com", ambient_email=None))
        assert seen.get("X-CC-Member") == "owner@acme.com"
        assert seen.get("X-CC-Module") == "workflows"
        assert "user" not in after, "the run's member outlived the run"

    def test_an_unresolved_owner_keeps_the_CLICKER(self, monkeypatch):
        seen, _after = self._run(
            monkeypatch, None,
            bound={"user": "dana@acme.com", "member_verified": True})
        assert seen.get("X-CC-Member") == "dana@acme.com"


# ── The self-mutation sandbox ───────────────────────────────────────────────


def _load_runner():
    path = ROOT / "apps/services/orchestrator/mutation_runner.py"
    spec = importlib.util.spec_from_file_location("_mutation_runner_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheMutationSandbox:
    """🔴 ``mutation_runner.py`` runs in a container with no ``acb_llm`` and
    called gateway ``/v1`` with only the LLM key.

    Mutation-checked 2026-09-28: drop the ``MUTATION_ROUTER_HEADERS`` ``-e``
    in ``_run_mutation_sandbox`` and the launch test fails. Drop
    ``provider["headers"] = headers`` in the runner and the wire test fails.
    """

    def test_the_launch_hands_the_FAILED_RUNS_headers_in(self, monkeypatch, secret):
        from acb_auth.member_proof import verify_member
        from orchestrator import mutation

        seen: list = []

        async def _fake_exec(*cmd, **_kw):
            seen.extend(cmd)
            raise RuntimeError("no docker in a unit test")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        settings = SimpleNamespace(llm_api_key="k", github_token=None)

        async def go():
            with run_context_scope():
                bind_run_context(user="dana@acme.com", member_verified=True,
                                 app="tasks", run_id="run-7", agent="task-manager")
                await mutation._run_mutation_sandbox(
                    "task-manager", "run-7", "run-7",
                    mutation._build_telemetry(
                        "task-manager", "run-7", "run-7", "Boom: x",
                        SimpleNamespace()),
                    settings)

        asyncio.run(go())
        env = [seen[i + 1] for i, v in enumerate(seen) if v == "-e"]
        raw = next(e for e in env if e.startswith(mutation.ROUTER_HEADERS_ENV + "="))
        headers = json.loads(raw.split("=", 1)[1])
        assert headers["X-CC-Member"] == "dana@acme.com"
        assert verify_member(headers["X-CC-Member-Proof"], secret) == "dana@acme.com"
        assert (headers["X-CC-Module"], headers["X-CC-Run"]) == ("tasks", "run-7")

    def test_a_CLAIMED_member_crosses_unsigned(self, monkeypatch, secret):
        """A claim is never signed here. On the deployment key the Router
        then REFUSES it (PR #511), which is the fail-closed answer."""
        from orchestrator import mutation

        with run_context_scope():
            bind_run_context(user="dana@acme.com")
            headers = mutation._router_headers()
        assert headers["X-CC-Member"] == "dana@acme.com"
        assert "X-CC-Member-Proof" not in headers

    @pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '{"a": 1}'])
    def test_the_runner_reads_NOTHING_from_a_bad_value(self, raw):
        assert _load_runner().router_headers(raw) == {}

    def test_the_runner_keeps_only_X_CC_headers(self):
        raw = json.dumps({"X-CC-Member": "dana@acme.com", "Authorization": "x",
                          "X-CC-Run": "", "X-CC-Module": 5})
        assert _load_runner().router_headers(raw) == {"X-CC-Member": "dana@acme.com"}

    def test_the_session_the_runner_opens_CARRIES_the_member(self, monkeypatch):
        """The REAL SDK ``create_session`` builds the wire payload. Only the
        pipe to the CLI is replaced, as ``test_usage_attribution`` does."""
        import copilot

        rpc_calls: list = []

        class _Rpc:
            async def request(self, method, params=None, **_kw):
                rpc_calls.append((method, params or {}))
                if method == "session.create":
                    return {"sessionId": params["sessionId"]}
                return {"messageId": "m-1"}

        class _Client(copilot.CopilotClient):
            async def start(self):
                self._client = _Rpc()

            async def stop(self):
                return None

        monkeypatch.setattr(copilot, "CopilotClient", _Client)
        monkeypatch.setenv("MUTATION_PROMPT", "fix it")
        monkeypatch.setenv("GATEWAY_API_KEY", "k")
        monkeypatch.setenv("GATEWAY_BASE_URL", "http://gw")
        monkeypatch.setenv("MUTATION_ROUTER_HEADERS",
                           json.dumps({"X-CC-Member": "dana@acme.com",
                                       "X-CC-Run": "run-7"}))
        runner = _load_runner()

        async def go():
            task = asyncio.ensure_future(runner.main())
            for _ in range(200):
                if any(m == "session.create" for m, _ in rpc_calls):
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

        asyncio.run(go())
        created = [p for m, p in rpc_calls if m == "session.create"]
        assert created, "the runner never opened a session"
        headers = created[0]["provider"]["headers"]
        assert (headers["X-CC-Member"], headers["X-CC-Run"]) == ("dana@acme.com", "run-7")


# ── R8: the billing-member SQL on a real tenant schema ──────────────────────


_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "")


@pytest.mark.skipif(not _URL, reason="R8: set TENANT_LADDER_DATABASE_URL (scripts/dev_db.sh)")
class TestTheBillingMemberSqlOnARealDatabase:
    """The SQL in ``_SQL_WORKFLOW_BILLING_MEMBER``, against the real ladder.

    Everything runs in one transaction that is rolled back, so the scratch
    tenant keeps no row.
    """

    @pytest.fixture
    def conn(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from tests.unit._tenant_ladder import apply_ladder

        eng = create_engine(_URL, future=True, poolclass=NullPool)
        # ⚠️ Build only an EMPTY database. `scripts/dev_db.sh` has already
        # built the scratch tenant, and a second replay there dies with
        # "tables can have at most 1600 columns" (measured 2026-09-28).
        with eng.begin() as c:
            if c.execute(text("SELECT to_regclass('public.workflows')")).scalar() is None:
                apply_ladder(c)
        c = eng.connect()
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()
            c.close()
            eng.dispose()

    @staticmethod
    def _org(c) -> str:
        from sqlalchemy import text

        slug = f"bm-{uuid.uuid4().hex[:10]}"
        return str(c.execute(
            text("INSERT INTO organization (slug, display_name) "
                 "VALUES (:s, :s) RETURNING id"), {"s": slug},
        ).scalar_one())

    @staticmethod
    def _user(c, email: str, org: str | None, status: str = "active") -> None:
        from sqlalchemy import text

        c.execute(
            text("INSERT INTO app_user (email, organization_id, status) "
                 "VALUES (:e, CAST(:o AS uuid), :s)"),
            {"e": email, "o": org, "s": status},
        )

    @staticmethod
    def _workflow(c, owner: str) -> str:
        from sqlalchemy import text

        return str(c.execute(
            text("INSERT INTO workflows (name, owner_email) "
                 "VALUES ('wf', :o) RETURNING id"), {"o": owner},
        ).scalar_one())

    @staticmethod
    def _ask(c, wid: str, ambient: str = ""):
        from gateway.routes.workflows.service import _SQL_WORKFLOW_BILLING_MEMBER
        from sqlalchemy import text

        return c.execute(
            text(_SQL_WORKFLOW_BILLING_MEMBER), {"wid": wid, "ambient": ambient},
        ).fetchone()

    def _tag(self) -> str:
        return uuid.uuid4().hex[:8]

    def test_the_owner_is_returned(self, conn):
        t = self._tag()
        org = self._org(conn)
        self._user(conn, f"owner-{t}@acme.com", org)
        row = self._ask(conn, self._workflow(conn, f"OWNER-{t}@acme.com"))
        assert row.owner_email == f"owner-{t}@acme.com"
        assert row.ambient_email is None

    def test_a_clicker_in_the_SAME_org_is_returned(self, conn):
        t = self._tag()
        org = self._org(conn)
        self._user(conn, f"owner-{t}@acme.com", org)
        self._user(conn, f"dana-{t}@acme.com", org)
        wid = self._workflow(conn, f"owner-{t}@acme.com")
        assert self._ask(conn, wid, f"Dana-{t}@acme.com").ambient_email == (
            f"dana-{t}@acme.com")

    def test_a_clicker_in_ANOTHER_org_is_never_billed(self, conn):
        """🔴 The member picks the organization that pays. A person from
        another tenant must never be the answer for this workflow."""
        t = self._tag()
        org, other = self._org(conn), self._org(conn)
        self._user(conn, f"owner-{t}@acme.com", org)
        self._user(conn, f"eve-{t}@other.com", other)
        wid = self._workflow(conn, f"owner-{t}@acme.com")
        row = self._ask(conn, wid, f"eve-{t}@other.com")
        assert row.ambient_email is None
        assert row.owner_email == f"owner-{t}@acme.com"

    @pytest.mark.parametrize("status", ["suspended", "removed", "invited"])
    def test_an_owner_who_is_not_ACTIVE_resolves_nobody(self, conn, status):
        t = self._tag()
        org = self._org(conn)
        self._user(conn, f"owner-{t}@acme.com", org, status=status)
        assert self._ask(conn, self._workflow(conn, f"owner-{t}@acme.com")) is None

    def test_an_owner_with_NO_organization_resolves_nobody(self, conn):
        t = self._tag()
        self._user(conn, f"owner-{t}@acme.com", None)
        assert self._ask(conn, self._workflow(conn, f"owner-{t}@acme.com")) is None


# ── The shared seam: `job_member_scope` ─────────────────────────────────────


class TestTheJobMemberScope:
    def test_the_OWNER_is_bound_verified_and_signed(self, secret):
        from acb_auth.member_proof import verify_member
        from acb_common import job_member_scope

        with job_member_scope("owner@acme.com", app="email"):
            out = attribution_headers()
        assert out["X-CC-Member"] == "owner@acme.com"
        assert verify_member(out["X-CC-Member-Proof"], secret) == "owner@acme.com"
        assert out["X-CC-Module"] == "email"

    def test_an_INHERITED_member_never_outlives_the_owner_lookup(self):
        """🔴 A sync loop started inside a request copies that request's
        member for its whole life. An unresolvable owner must leave the job
        memberless, never billed to whoever pressed Save."""
        from acb_common import job_member_scope

        bind_run_context(user="creator@acme.com", member_verified=True)
        with job_member_scope(None, app="email"):
            assert "user" not in get_run_context()
            assert "member_verified" not in get_run_context()
        assert get_run_context()["user"] == "creator@acme.com", "not restored"

    @pytest.mark.parametrize("who", ["anonymous", "", "  ", "svc-cron"])
    def test_a_name_that_is_not_an_ADDRESS_binds_nobody(self, who):
        from acb_common import job_member_scope

        with job_member_scope(who):
            assert "X-CC-Member" not in attribution_headers()


# ── A nested run that names the member its parent already verified ─────────


class TestANestedRunNamingTheSameOwner:
    """The email specialist run passes the mailbox owner as ``user_email``.
    Inside the owner's scope that claim names the member the task already
    holds verified, so it keeps the verification.

    Mutation-checked 2026-09-28: delete the same-member branch in
    ``executor._run_member`` and the first test fails.
    """

    def test_a_claim_EQUAL_to_the_verified_member_stays_verified(self):
        from orchestrator.executor import _run_member

        bind_run_context(user="owner@acme.com", member_verified=True)
        assert _run_member({"user_email": "Owner@acme.com"}) == ("owner@acme.com", True)

    def test_a_claim_naming_ANYONE_ELSE_is_still_only_a_claim(self):
        from orchestrator.executor import _run_member

        bind_run_context(user="owner@acme.com", member_verified=True)
        assert _run_member({"user_email": "mallory@evil.com"}) == (
            "mallory@evil.com", False)

    def test_a_claim_beside_an_UNVERIFIED_member_is_still_a_claim(self):
        from orchestrator.executor import _run_member

        bind_run_context(user="owner@acme.com")
        assert _run_member({"user_email": "owner@acme.com"}) == ("owner@acme.com", False)


# ── The email sync loop ─────────────────────────────────────────────────────


class TestTheEmailJobs:
    """🔴 Rules, Reply Zero, the morning brief and the follow-up drafts run
    from the email sync loop or the Graph webhook, with no session.

    Mutation-checked 2026-09-28: drop ``@as_mailbox_owner`` from
    ``process_new_mail`` and the new-mail test fails. Register the bare
    ``_maybe_send_digest`` and the hook test fails for ``send_digest``.
    """

    @staticmethod
    def _owner_db(monkeypatch, owner):
        from gateway.routes.email import scheduler_hooks

        async def _db():
            return _FakeDb(_Row(user_id=owner) if owner else None)

        monkeypatch.setattr(scheduler_hooks, "_get_db", _db)

    def test_new_mail_runs_as_the_MAILBOX_OWNER(self, monkeypatch, secret):
        from gateway.routes.email import scheduler_hooks
        from gateway.routes.email.automation import cleanup, replyzero, senders

        self._owner_db(monkeypatch, "owner@acme.com")
        seen: dict = {}

        async def _rules(_aid):
            seen.update(attribution_headers())

        async def _noop(*_a, **_kw):
            return None

        monkeypatch.setattr(scheduler_hooks, "auto_run_rules_for_account", _rules)
        monkeypatch.setattr(cleanup, "sweep_uncategorized", _noop)
        monkeypatch.setattr(replyzero, "_maybe_classify_threads", _noop)
        monkeypatch.setattr(senders, "_categorize_senders_job", _noop)
        monkeypatch.setattr(senders, "_maybe_auto_archive", _noop)

        async def go():
            # The loop was started inside somebody else's request.
            bind_run_context(user="creator@acme.com", member_verified=True)
            await scheduler_hooks.process_new_mail("acc-1")
            return dict(get_run_context())

        after = asyncio.run(go())
        assert seen.get("X-CC-Member") == "owner@acme.com"
        assert seen.get("X-CC-Member-Proof")
        assert seen.get("X-CC-Module") == "email"
        assert after.get("user") == "creator@acme.com", "the scope leaked"

    def test_every_model_calling_HOOK_runs_as_the_owner(self, monkeypatch):
        from gateway.routes.email import digest, scheduler_hooks
        from gateway.routes.email.automation import followups, replyzero

        self._owner_db(monkeypatch, "owner@acme.com")
        seen: dict[str, str | None] = {}

        def _recorder(name):
            async def _job(_aid):
                seen[name] = attribution_headers().get("X-CC-Member")
            return _job

        monkeypatch.setattr(replyzero, "_maybe_classify_threads", _recorder("classify"))
        monkeypatch.setattr(digest, "_maybe_send_digest", _recorder("digest"))
        monkeypatch.setattr(followups, "_maybe_send_follow_up_reminders",
                            _recorder("follow_up"))
        registered: dict = {}
        monkeypatch.setattr(scheduler_hooks, "register_post_sync_hooks",
                            lambda **kw: registered.update(kw))
        scheduler_hooks.register_email_post_sync_hooks()

        async def go():
            for key in ("classify_threads", "send_digest", "send_follow_up_reminders"):
                await registered[key]("acc-1")

        asyncio.run(go())
        assert seen == {"classify": "owner@acme.com", "digest": "owner@acme.com",
                        "follow_up": "owner@acme.com"}

    def test_an_UNKNOWN_mailbox_runs_memberless(self, monkeypatch):
        from gateway.routes.email import scheduler_hooks

        self._owner_db(monkeypatch, None)
        seen: dict = {}

        async def _job(_aid):
            seen.update(attribution_headers())

        async def go():
            bind_run_context(user="creator@acme.com", member_verified=True)
            await scheduler_hooks.as_mailbox_owner(_job)("gone")

        asyncio.run(go())
        assert "X-CC-Member" not in seen


# ── The WhatsApp enrichment loop ────────────────────────────────────────────


class _WaDb:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    async def execute(self, sql, _params=None):
        if "FROM wa_accounts" in str(sql):
            owner = self.owner
            return SimpleNamespace(scalar=lambda: owner)
        return SimpleNamespace(fetchall=lambda: [SimpleNamespace(id="chat-1")])

    async def commit(self):
        return None

    async def close(self):
        self.closed = True


class TestTheWhatsAppGroupSummaries:
    """Mutation-checked 2026-09-28: call ``_summarize_stale_groups`` outside
    the ``job_member_scope`` block and the first test fails."""

    @staticmethod
    def _run(monkeypatch, owner):
        from gateway.routes.whatsapp.automation import groups

        db = _WaDb(owner)

        async def _db():
            return db

        seen: dict = {}

        async def _summarize(_db, _aid, _cid):
            seen.update(attribution_headers())
            return {"ok": True}

        monkeypatch.setattr(groups, "_get_db", _db)
        monkeypatch.setattr(groups, "summarize_group", _summarize)
        n = asyncio.run(groups.summarize_stale_groups("acc-1"))
        return n, seen, db

    def test_the_pass_runs_as_the_ACCOUNT_OWNER(self, monkeypatch):
        n, seen, db = self._run(monkeypatch, "owner@acme.com")
        assert n == 1
        assert seen.get("X-CC-Member") == "owner@acme.com"
        assert seen.get("X-CC-Module") == "whatsapp"
        assert db.closed

    def test_a_missing_account_runs_memberless(self, monkeypatch):
        _n, seen, _db = self._run(monkeypatch, None)
        assert "X-CC-Member" not in seen


# ── The fences: a NEW memberless call site fails here ───────────────────────


def _py_files():
    for base in ("apps", "packages"):
        for py in (ROOT / base).rglob("*.py"):
            if {"node_modules", "tests", ".venv"} & set(py.parts):
                continue
            yield py


def _calls_named(name: str):
    """Every call to ``name``, bare or as an attribute.

    Yields ``(path, enclosing_function, line, node)``. The enclosing function
    is the innermost ``def`` around the call, or ``<module>``.
    """
    import ast

    for py in _py_files():
        try:
            # `utf-8-sig`: some files open with a BOM, and a plain read
            # hands `ast` a U+FEFF it refuses. A skipped file is a blind spot.
            tree = ast.parse(py.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError as exc:  # pragma: no cover - a fence must not go blind
            raise AssertionError(f"cannot parse {py}: {exc}") from exc
        rel = py.relative_to(ROOT).as_posix()

        def visit(node, fn, rel=rel):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(child, ast.Call):
                    f = child.func
                    called = f.id if isinstance(f, ast.Name) else (
                        f.attr if isinstance(f, ast.Attribute) else "")
                    if called == name:
                        yield rel, fn, child.lineno, child
                yield from visit(child, inner, rel)

        yield from visit(tree, "<module>")


def _passes_a_member(node) -> bool:
    """A ``session_user=`` keyword whose value is not a literal ``None``.

    ⚠️ ``session_user=None`` is memberless, and counting the keyword's mere
    presence would let it through.
    """
    import ast

    for k in node.keywords:
        if k.arg == "session_user":
            return not (isinstance(k.value, ast.Constant) and k.value.value is None)
    return False


#: ``run_agent(...)`` calls that pass NO member, keyed on
#: ``(file, enclosing function)`` with the EXACT number of such calls there,
#: and why each is safe. A new call must pass ``session_user=`` with a
#: SERVER-derived member, or be added here with its reason, in its own PR.
#: A second memberless call in an already-listed function changes the count
#: and fails too.
_RUN_AGENT_WITHOUT_SESSION_USER: dict[tuple[str, str], tuple[int, str]] = {
    ("apps/services/orchestrator/orchestrator/agents.py", "delegate_to_agent"): (
        1, "a sub-agent tool call on its parent's task; it inherits the "
           "parent run's member"),
    ("packages/acb_skills/acb_skills/agent_tools.py", "call_agent"): (
        1, "a delegated run on its parent's task; it inherits the parent's "
           "member"),
    ("packages/acb_skills/acb_skills/agent_tools.py", "_run_one"): (
        1, "call_agents_parallel: one delegated run per task, each on a task "
           "that copies the parent's context and so its member"),
    ("packages/acb_skills/acb_skills/agent_tools.py", "call_agent_background"): (
        1, "create_task copies the parent run's context, and so its member"),
    ("apps/services/orchestrator/orchestrator/executor.py", "_run_sub_agent_streaming"): (
        1, "the sub-agent fallback inside a run; it inherits the parent's "
           "member"),
    ("apps/services/gateway/gateway/routes/email/automation/drafting.py",
     "_draft_via_maf_agent"): (
        1, "dead code (drafting.py marks it so); its `user_email` claim "
           "stays unverified"),
    ("apps/services/gateway/gateway/routes/email/automation/drafting.py",
     "_orchestrate_draft"): (
        1, "runs inside `as_mailbox_owner`; its `user_email` is that same "
           "owner, which `_run_member` keeps verified"),
    ("apps/services/gateway/gateway/routes/projects/agent_dispatch.py",
     "_run_and_record"): (
        1, "an inline event sink. From PUT /tasks/{id}/assignees it inherits "
           "the assigner's session member. From a workflow tool node it "
           "inherits the member `_execute_run` binds for the whole run"),
    ("apps/services/gateway/gateway/routes/agent.py", "_run"): (
        1, "OPEN (H-152): the HMAC-signed /agent/webhook has no owning member "
           "or organization; it runs memberless or on a signed body claim"),
    ("apps/services/gateway/gateway/routes/workflows/engine/handlers.py",
     "execute_node"): (
        1, "`services.run_agent` is the workflow seam, `_agent_runner`, which "
           "passes the billing member itself"),
}


class TestNoNewMemberlessCallSite:
    """R7 fence for H-152. Deliberately STATIC: a path that would run with no
    member fails here, before anyone flips the deployment key.

    ⚠️ **Why ``acompletion_with_fallback`` and ``complete`` are NOT fenced the
    same way.** They have about 80 call sites. Most run inside a signed-in
    request, where ``get_current_user`` binds the member. No static reading
    can tell a request handler from a loop body three calls away. The
    background loops are instead covered at their ENTRY: the email hooks by
    ``as_mailbox_owner``, the WhatsApp pass by ``job_member_scope``, and a
    workflow run in ``_execute_run``. Each has a test in this file.

    Mutation-checked 2026-09-28: add a second memberless ``run_agent`` in
    ``_run_and_record`` and the count test fails. Change the workflow
    node's keyword to ``session_user=None`` and the first test fails.
    """

    @staticmethod
    def _memberless() -> dict[tuple[str, str], list[int]]:
        out: dict[tuple[str, str], list[int]] = {}
        for path, fn, line, node in _calls_named("run_agent"):
            if not _passes_a_member(node):
                out.setdefault((path, fn), []).append(line)
        return out

    def test_every_run_agent_call_passes_a_member_or_is_JUSTIFIED(self):
        offenders = {
            f"{path}::{fn}": lines
            for (path, fn), lines in self._memberless().items()
            if (path, fn) not in _RUN_AGENT_WITHOUT_SESSION_USER
        }
        assert not offenders, (
            "run_agent() with no member and no recorded reason: "
            f"{offenders}. Pass a SERVER-derived `session_user=`, or justify "
            "it in _RUN_AGENT_WITHOUT_SESSION_USER.")

    def test_each_justified_function_holds_its_EXACT_count(self):
        live = self._memberless()
        drift = {
            f"{path}::{fn}": (len(live.get((path, fn), [])), n)
            for (path, fn), (n, _why) in _RUN_AGENT_WITHOUT_SESSION_USER.items()
            if len(live.get((path, fn), [])) != n
        }
        assert not drift, (
            f"memberless run_agent() counts (measured, pinned): {drift}. A "
            "higher count is a NEW memberless call; a lower one is progress "
            "to bank by lowering or removing the entry.")

    def test_a_literal_None_is_MEMBERLESS(self):
        import ast

        call = ast.parse("run_agent(a, b, session_user=None)").body[0].value
        assert _passes_a_member(call) is False
        call = ast.parse("run_agent(a, b, session_user=owner)").body[0].value
        assert _passes_a_member(call) is True

    def test_no_OpenAI_client_is_built_outside_the_attribution_seam(self):
        """🔴 graphiti built a bare ``AsyncOpenAI`` against gateway ``/v1``,
        so every extraction reached the Router with no member. Only
        ``attributed_openai`` may build one. ``main.py``'s embeddings client
        calls the vendor directly and never meets the Router.

        Mutation-checked 2026-09-28: restore the bare ``AsyncOpenAI`` in
        ``graphiti_client.py`` and this fails.
        """
        allowed = {
            "packages/acb_llm/acb_llm/attribution.py",
            "apps/services/gateway/gateway/main.py",
        }
        offenders = [
            f"{path}:{line}"
            for name in ("AsyncOpenAI", "OpenAI")
            for path, _fn, line, _node in _calls_named(name)
            if path not in allowed
        ]
        assert not offenders, f"OpenAI clients that cannot attribute: {offenders}"
