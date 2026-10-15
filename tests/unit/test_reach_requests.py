"""WS-59 RE-5 — "Tell me on WhatsApp", one primitive for every app
(``proactive_reach.md`` §11).

* **The call** checks every argument, lower-cases both emails, refuses a link
  out of the app, and makes a key when the caller gives none.
* **The class** of a ``notify_me`` card: the member's own request and a
  rule's ``urgent`` are ``urgent``, and a rule's ``normal`` interrupts.
* **No caller imports a channel adapter**: only ``gateway/reach/`` imports
  ``gateway.reach.whatsapp``.
* **The email rule action** ``NOTIFY_ME`` tells the mailbox owner, never the
  sender, and its key makes a second run record nothing new.
* **The workflow step** ``notify.whatsapp`` tells the workflow's owner, and
  the step's arguments cannot name another member.
* **R8, on a real Postgres**: the table by content with FORCE RLS, one key
  records one request, the open read, and org A's request is invisible in
  org B. It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a
  pass.

The loop's rules for requests live in ``test_reach_loop.py::TestTellMe``.
"""

from __future__ import annotations

import ast
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from gateway.reach import classes, plan, requests

_REPO = Path(__file__).resolve().parents[2]
ORG = "11111111-1111-1111-1111-111111111111"


class FakeSession:
    """Takes the INSERT of :func:`requests.tell_me`, and answers like
    ``ON CONFLICT DO NOTHING``."""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        #: What the counts read answers: the open rule and workflow requests,
        #: and the member's own requests of the last 24 hours.
        self.backlog, self.own_today = 0, 0

    @asynccontextmanager
    async def __call__(self, org=None):
        self.org = org
        yield self

    async def execute(self, sql, params):
        fake = self
        if "count(*)" in str(sql):
            counts = (self.backlog, self.own_today)

            class _C:
                def one(self):
                    return counts

            return _C()

        class _R:
            def first(self):
                if params["key"] in fake.keys:
                    return None
                fake.keys[params["key"]] = dict(params)
                return (f"id-{len(fake.keys)}",)

        return _R()


@pytest.fixture
def db(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(requests, "tenant_session", fake)
    return fake


class TestTheCall:
    async def test_it_records_with_clean_values(self, db):
        rid = await requests.tell_me(
            ORG, " Me@Org.Test ", source="chat", title="  Call\nthe   bank  ",
            body="  more  ", href="/email?email=1&account=2",
            entity={"type": "email", "id": "m1"}, urgency="urgent",
            requested_by="ME@org.test", key="k1")
        assert rid == "id-1" and db.org == ORG
        row = db.keys["k1"]
        assert (row["m"], row["by"], row["title"], row["body"]) == (
            "me@org.test", "me@org.test", "Call the bank", "more")
        assert (row["et"], row["eid"], row["urg"], row["src"]) == (
            "email", "m1", "urgent", "chat")

    async def test_the_same_key_records_once(self, db):
        args = {"source": "email.rule", "title": "T", "requested_by": "me@org.test"}
        assert await requests.tell_me(ORG, "me@org.test", key="dup", **args) is not None
        assert await requests.tell_me(ORG, "me@org.test", key="dup", **args) is None

    async def test_no_key_makes_a_new_one_each_time(self, db):
        args = {"source": "chat", "title": "T", "requested_by": "me@org.test"}
        await requests.tell_me(ORG, "me@org.test", **args)
        await requests.tell_me(ORG, "me@org.test", **args)
        assert len(db.keys) == 2 and all(k.startswith("auto:") for k in db.keys)

    @pytest.mark.parametrize(("field", "value"), [
        ("title", ""), ("title", "   "), ("title", "x" * 301),
        ("body", "x" * 1001),
        ("href", "https://evil.test/x"), ("href", "//evil.test/x"), ("href", "/\\evil.test"),
        ("href", "javascript:alert(1)"), ("href", "/ok path"), ("href", "email"),
        ("urgency", "loud"), ("source", "sms"), ("member", "not-an-email"),
        ("requested_by", ""), ("entity", {"type": "email"}), ("entity", "m1"),
        ("key", "k" * 301),
    ])
    async def test_a_bad_argument_is_refused_and_records_nothing(self, db, field, value):
        args: dict[str, Any] = {"source": "chat", "title": "T", "requested_by": "me@org.test",
                                "member": "me@org.test"}
        args[field] = value
        member = args.pop("member")
        with pytest.raises(requests.RequestInvalid):
            await requests.tell_me(ORG, member, **args)
        assert db.keys == {}

    async def test_a_full_backlog_refuses_a_rule_and_never_the_member(self, db):
        # RE-5 review, P1-2.
        db.backlog = requests.BACKLOG_LIMIT
        with pytest.raises(requests.RequestRefused) as got:
            await requests.tell_me(ORG, "me@org.test", source="email.rule", title="T",
                                   requested_by="me@org.test")
        assert got.value.reason == "backlog_full" and db.keys == {}
        assert isinstance(got.value, requests.RequestInvalid)
        assert await requests.tell_me(ORG, "me@org.test", source="chat", title="T",
                                      requested_by="me@org.test") is not None
        db.backlog = requests.BACKLOG_LIMIT - 1
        assert await requests.tell_me(ORG, "me@org.test", source="workflow", title="T",
                                      requested_by="me@org.test") is not None

    async def test_the_members_own_requests_stop_at_the_daily_limit(self, db):
        # RE-5 review, P3-1.
        db.own_today = requests.SELF_DAILY_LIMIT
        with pytest.raises(requests.RequestRefused) as got:
            await requests.tell_me(ORG, "me@org.test", source="chat", title="T",
                                   requested_by="me@org.test")
        assert got.value.reason == "daily_limit" and db.keys == {}
        # A rule does not count against the member's own limit.
        assert await requests.tell_me(ORG, "me@org.test", source="email.rule", title="T",
                                      requested_by="me@org.test") is not None

    async def test_no_org_is_refused(self, db):
        with pytest.raises(requests.RequestInvalid):
            await requests.tell_me("", "me@org.test", source="chat", title="T",
                                   requested_by="me@org.test")

    def test_a_title_of_300_fits_and_clip_cuts_to_the_limit(self):
        assert requests.clean_title("x" * 300) == "x" * 300
        cut = requests.clip("Email from a: " + "s" * 400, requests.TITLE_MAX)
        assert len(cut) == 300 and cut.endswith("…")


def _req(**kw) -> requests.Request:
    now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    base = {"id": "r1", "source": "chat", "title": "T", "body": None, "href": None,
            "entity_type": None, "entity_id": None, "urgency": "normal",
            "requested_by": "me@org.test", "created_at": now,
            "expires_at": now + timedelta(hours=48)}
    return requests.Request(**{**base, **kw})


class TestTheCard:
    def test_the_members_own_request_is_urgent(self):
        card = requests.card(_req(), "ME@org.test")
        assert card["signal_id"] == "request:r1" and card["kind"] == "notify_me"
        assert card["facts"]["self"] is True
        assert classes.reach_class(card) == "urgent"

    @pytest.mark.parametrize(("source", "urgency", "cls"), [
        ("email.rule", "normal", "interrupt"), ("workflow", "normal", "interrupt"),
        ("email.rule", "urgent", "urgent"), ("workflow", "urgent", "urgent"),
    ])
    def test_a_rule_or_a_workflow_is_never_the_members_own(self, source, urgency, cls):
        card = requests.card(_req(source=source, urgency=urgency), "me@org.test")
        assert card["facts"]["self"] is False
        assert classes.reach_class(card) == cls

    def test_a_chat_request_by_another_member_is_not_self(self):
        card = requests.card(_req(requested_by="boss@org.test"), "me@org.test")
        assert card["facts"]["self"] is False

    def test_the_card_never_moves_its_fingerprint(self):
        card = requests.card(_req(), "me@org.test")
        assert plan.pair(card) == plan.pair(requests.card(_req(), "me@org.test"))

    def test_split_parts_open_and_expired(self):
        now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        live = _req(id="a")
        gone = _req(id="b", expires_at=now - timedelta(seconds=1))
        assert requests.split([live, gone], now) == ([live], [gone])

    def test_a_batch_line_is_the_title_and_the_body(self):
        now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        rule = requests.card(_req(source="workflow", title="Sync failed"), "me@org.test")
        assert plan.line(rule, now, kind="notify_me") == "Sync failed"
        rule = requests.card(_req(source="workflow", title="Sync failed", body="Step 3\nlost"),
                             "me@org.test")
        assert plan.line(rule, now, kind="notify_me") == "Sync failed: Step 3 lost"


# ── The fence: no caller imports a channel adapter (§4.1, RE-1 TestNoDirectSend) ─


def _imports_of(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8-sig")
    if "gateway.reach" not in source:
        return set()
    tree = ast.parse(source)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_no_module_outside_reach_imports_the_whatsapp_channel_of_reach():
    reach_dir = _REPO / "apps" / "services" / "gateway" / "gateway" / "reach"
    offenders = []
    for root in ("apps", "packages"):
        for path in (_REPO / root).rglob("*.py"):
            if "node_modules" in path.parts or reach_dir in path.parents:
                continue
            if "gateway.reach.whatsapp" in _imports_of(path):
                offenders.append(str(path.relative_to(_REPO)))
    assert offenders == []


# ── The email rule action NOTIFY_ME ─────────────────────────────────────────


class _NoDb:
    async def execute(self, *a, **k):
        raise AssertionError("NOTIFY_ME reads no mail table")


@pytest.fixture
def told(monkeypatch):
    from acb_common.db import bind_tenant, release_tenant

    got: list[dict] = []
    keys: set[str] = set()

    async def tell_me(org, member, **kw):
        got.append({"org": org, "member": member, **kw})
        if kw.get("key") is not None and kw["key"] in keys:
            return None
        keys.add(kw.get("key"))
        return "r"

    monkeypatch.setattr(requests, "tell_me", tell_me)
    token = bind_tenant(ORG)
    yield got
    release_tenant(token)


class TestTheEmailAction:
    async def _run(self, action, rule_id="rule-1"):
        from gateway.routes.email.automation.actions import _apply_rule_actions

        errors: list = []
        done = await _apply_rule_actions(
            _NoDb(), None, "msg-1", "pmid", [action],
            {"from": "Priya Shah", "subject": "Defect in lot 7", "thread_id": "t"},
            user_email="owner@org.test", account_id="acc-1", errors_out=errors,
            rule_id=rule_id)
        return done, errors

    async def test_it_tells_the_mailbox_owner_with_the_thread_link(self, told):
        done, errors = await self._run({"type": "NOTIFY_ME", "label": None})
        assert done == ["NOTIFY_ME"] and errors == []
        assert told == [{
            "org": ORG, "member": "owner@org.test", "source": "email.rule",
            "title": "Email from Priya Shah: Defect in lot 7",
            "href": "/email?email=msg-1&account=acc-1",
            "entity": {"type": "email", "id": "msg-1"}, "urgency": "normal",
            "requested_by": "owner@org.test", "key": "email:acc-1:msg-1:rule-1"}]

    async def test_urgent_is_its_one_option(self, told):
        await self._run({"type": "NOTIFY_ME", "label": "urgent"})
        assert told[0]["urgency"] == "urgent"

    async def test_a_second_run_records_nothing_new(self, told):
        await self._run({"type": "NOTIFY_ME"})
        await self._run({"type": "NOTIFY_ME"})
        assert told[0]["key"] == told[1]["key"]

    async def test_no_rule_id_is_an_action_issue_and_records_nothing(self, told):
        done, errors = await self._run({"type": "NOTIFY_ME"}, rule_id="")
        assert done == [] and errors[0]["type"] == "NOTIFY_ME" and told == []

    def test_the_rule_generator_knows_it_and_a_retry_skips_it(self):
        from gateway.routes.email.automation.rules import (
            _GEN_ACTION_TYPES,
            _normalize_generated_rules,
        )
        from gateway.routes.email.automation.runner import _RETRY_SKIPPED_ACTIONS

        assert "NOTIFY_ME" in _GEN_ACTION_TYPES and "NOTIFY_ME" in _RETRY_SKIPPED_ACTIONS
        got = _normalize_generated_rules([{"name": "Bank", "actions": [
            {"type": "notify_me", "label": "urgent"}]}])
        assert got[0]["actions"][0]["type"] == "NOTIFY_ME"


class TestProcessPastEmails:
    """A backfill never tells the member on WhatsApp (RE-5 review, P1-3)."""

    @staticmethod
    def _match(*types):
        return {"rule": {"name": "R", "actions": [{"type": t} for t in types]}}

    @pytest.mark.parametrize("draft_replies", [False, True])
    def test_notify_me_is_stripped_whatever_the_run_asked(self, draft_replies):
        from gateway.routes.email.automation import runner

        got, skipped = runner._backfill_matches(
            [self._match("LABEL", "NOTIFY_ME", "REPLY")], draft_replies)
        types = [a["type"] for a in got[0]["rule"]["actions"]]
        assert "NOTIFY_ME" not in types and "LABEL" in types
        assert ("REPLY" in types) is draft_replies
        assert skipped == (0 if draft_replies else 1)

    def test_the_backfill_job_uses_the_pre_pass_and_the_live_run_does_not(self):
        import inspect

        from gateway.routes.email.automation import runner

        assert "_backfill_matches(" in inspect.getsource(runner._process_past_emails_job)
        assert "_backfill_matches" not in inspect.getsource(runner._run_rules_job)


@pytest.mark.parametrize(("stored", "address"), [
    ("priya@vendor.test", "priya@vendor.test"),
    ('{"name": "Priya", "email": "priya@vendor.test"}', "priya@vendor.test"),
    ({"name": "Priya", "email": "priya@vendor.test"}, "priya@vendor.test"),
    (None, ""), ("{not json", "{not json"),
])
def test_an_approved_execution_reads_the_sender_address(stored, address):
    """RE-5 review, P3-2: the title never shows the raw stored object."""
    from gateway.routes.email.automation.runner import _sender_email

    assert _sender_email(stored) == address


# ── The workflow step notify.whatsapp ───────────────────────────────────────


class TestTheWorkflowStep:
    async def test_the_run_binds_the_owner_and_the_args_cannot_name_a_member(
            self, monkeypatch, told):
        from gateway.routes.workflows import service

        async def owner(wid):
            assert wid == "wf-1"
            return ORG, "owner@org.test"

        monkeypatch.setattr(service, "_workflow_owner", owner)
        run = service.build_node_services("someone@else.test", "wf-1").run_tool
        got = await run("notify.whatsapp", {"title": "Nightly sync failed",
                                            "urgency": "urgent"}, "someone@else.test")
        assert got == {"recorded": True, "request_id": "r"}
        assert told[0]["member"] == "owner@org.test"
        assert told[0]["requested_by"] == "owner@org.test"
        assert (told[0]["source"], told[0]["urgency"]) == ("workflow", "urgent")
        from gateway.routes.workflows.engine.handlers import NodeExecutionError

        with pytest.raises(NodeExecutionError):
            await run("notify.whatsapp", {"title": "x", "member": "boss@org.test"}, "a")

    async def test_outside_a_run_the_step_refuses(self, told):
        from gateway.routes.workflows.engine.handlers import NodeExecutionError
        from gateway.routes.workflows.tools import execute_tool

        with pytest.raises(NodeExecutionError):
            await execute_tool("notify.whatsapp", {"title": "x"}, "a@org.test")
        assert told == []

    async def test_a_draft_with_no_workflow_refuses(self, told):
        from gateway.routes.workflows import service
        from gateway.routes.workflows.engine.handlers import NodeExecutionError

        with pytest.raises(NodeExecutionError):
            await service.build_node_services("a", "").run_tool(
                "notify.whatsapp", {"title": "x"}, "a")
        assert told == []


# ── R8: the real table ──────────────────────────────────────────────────────

_MIGRATIONS = _REPO / "infra" / "postgres"


def test_the_migration_is_found_by_content_once():
    found = [f.name for f in _MIGRATIONS.glob("[0-9]*_*.sql")
             if "CREATE TABLE IF NOT EXISTS reach_requests" in f.read_text(encoding="utf-8")]
    assert len(found) == 1, found


# The catalog, the app role and the binding, shared as test_reach_r8.py takes
# them. ``promoted`` and ``app_engine`` are fixtures, used by name.
from tests.unit.test_email_keep_separate import (  # noqa: E402, F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

TAG = uuid.uuid4().hex[:8]
A = f"reach-req-a-{TAG}@re5.test"
B = f"reach-req-b-{TAG}@re5.test"


@_DB_GATE
class TestOnARealDatabase:
    @pytest.fixture(autouse=True)
    def _clean(self, promoted):  # noqa: F811
        from sqlalchemy import text

        def wipe():
            with promoted.admin_engine.begin() as c:
                for table in ("reach_requests", "reach_signal_log"):
                    c.execute(text(f"DELETE FROM {table} WHERE member_email IN (:a, :b)"),
                              {"a": A, "b": B})

        wipe()
        yield
        wipe()

    def test_forced_rls_and_the_checks(self, promoted):  # noqa: F811
        from sqlalchemy import text

        with promoted.admin_engine.connect() as c:
            rls = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'reach_requests'::regclass")).one()
            defs = set(c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN "
                "('reach_requests'::regclass, 'reach_signal_log'::regclass, "
                "'reach_outbox'::regclass)")).scalars())
        assert tuple(rls) == (True, True)
        assert "UNIQUE (organization_id, dedupe_key)" in defs
        assert any("'expired'" in d for d in defs)
        assert any("'request'" in d for d in defs)

    async def test_one_key_one_request_and_the_open_read(self, promoted):  # noqa: F811
        from gateway.reach import outbox

        p = promoted
        async with _as_member(p, p.org_a):
            first = await requests.tell_me(p.org_a, A, source="chat", title="T",
                                           body="b", requested_by=A, key="k1",
                                           href="/email?email=1")
            again = await requests.tell_me(p.org_a, A, source="chat", title="T",
                                           requested_by=A, key="k1")
            assert first is not None and again is None
            now = datetime.now(UTC)
            rows = await requests.open_requests(p.org_a, A, now)
            assert [r.id for r in rows] == [first]
            assert rows[0].href == "/email?email=1" and rows[0].urgency == "normal"
            assert rows[0].expires_at - rows[0].created_at == timedelta(hours=48)
            # A log row for its signal closes it.
            await outbox.log(p.org_a, A, [(f"request:{first}", "fp")], "expired")
            assert await requests.open_requests(p.org_a, A, now) == []
            # Expired rows are not open.
            later = await requests.tell_me(p.org_a, A, source="chat", title="T2",
                                           requested_by=A)
            assert await requests.open_requests(p.org_a, A, now + timedelta(hours=49)) == []
            assert [r.id for r in await requests.undelivered(p.org_a, A)] == [later]

    async def test_the_members_own_request_reads_first_and_the_limits_count(
            self, promoted):  # noqa: F811
        # RE-5 review, P1-2 and P3-1, on the real SQL.
        from sqlalchemy import text

        p = promoted
        async with _as_member(p, p.org_a):
            rules = [await requests.tell_me(p.org_a, A, source="email.rule",
                                            title=f"R{i}", requested_by=A)
                     for i in range(3)]
            own = await requests.tell_me(p.org_a, A, source="chat", title="Mine",
                                         requested_by=A)
            rows = await requests.undelivered(p.org_a, A)
            assert [r.id for r in rows] == [own, *rules]
        # Fill the backlog to the limit, with the admin role.
        with promoted.admin_engine.begin() as c:
            for i in range(requests.BACKLOG_LIMIT - 3):
                c.execute(text(
                    "INSERT INTO reach_requests (organization_id, member_email, source, "
                    "title, requested_by, dedupe_key) VALUES (CAST(:o AS uuid), :m, "
                    "'workflow', 't', :m, :k)"), {"o": p.org_a, "m": A, "k": f"fill{i}"})
        async with _as_member(p, p.org_a):
            with pytest.raises(requests.RequestRefused, match="backlog_full"):
                await requests.tell_me(p.org_a, A, source="workflow", title="One more",
                                       requested_by=A)
        # A log row closes one, and an expired one does not count.
        from gateway.reach import outbox

        async with _as_member(p, p.org_a):
            await outbox.log(p.org_a, A, [(f"request:{rules[0]}", "fp")], "sent")
            assert await requests.tell_me(p.org_a, A, source="workflow", title="Now",
                                          requested_by=A) is not None
        with promoted.admin_engine.begin() as c:
            c.execute(text("UPDATE reach_requests SET expires_at = now() - interval '1 s' "
                           "WHERE member_email = :m AND dedupe_key = 'fill0'"), {"m": A})
        async with _as_member(p, p.org_a):
            assert await requests.tell_me(p.org_a, A, source="workflow", title="Again",
                                          requested_by=A) is not None
        # The member's own: 20 in 24 hours. One is already there.
        with promoted.admin_engine.begin() as c:
            for i in range(requests.SELF_DAILY_LIMIT - 1):
                c.execute(text(
                    "INSERT INTO reach_requests (organization_id, member_email, source, "
                    "title, requested_by, dedupe_key) VALUES (CAST(:o AS uuid), :m, "
                    "'chat', 't', :m, :k)"), {"o": p.org_a, "m": A, "k": f"own{i}"})
        async with _as_member(p, p.org_a):
            with pytest.raises(requests.RequestRefused, match="daily_limit"):
                await requests.tell_me(p.org_a, A, source="chat", title="21st",
                                       requested_by=A)
        # Org B's member is not touched by A's counts.
        async with _as_member(p, p.org_b):
            assert await requests.tell_me(p.org_b, B, source="chat", title="B",
                                          requested_by=B) is not None

    async def test_org_b_never_sees_the_request_of_org_a(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            rid = await requests.tell_me(p.org_a, A, source="chat", title="Secret",
                                         requested_by=A, key="shared")
        async with _as_member(p, p.org_b):
            assert await requests.undelivered(p.org_b, A) == []
            # The same key is free in org B.
            assert await requests.tell_me(p.org_b, B, source="chat", title="B",
                                          requested_by=B, key="shared") is not None
            assert await requests.undelivered(p.org_b, B) != []
        async with _as_member(p, p.org_a):
            assert [r.id for r in await requests.undelivered(p.org_a, A)] == [rid]
            # A request for A is never one for B.
            assert await requests.undelivered(p.org_a, B) == []

    async def test_with_check_refuses_a_row_stamped_with_another_org(self, promoted):  # noqa: F811
        from gateway.db import tenant_session
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        p = promoted
        with pytest.raises(DBAPIError, match="row-level security"):
            async with _as_member(p, p.org_b), tenant_session() as db:
                await db.execute(text(
                    "INSERT INTO reach_requests (organization_id, member_email, source, "
                    "title, requested_by, dedupe_key) VALUES (CAST(:o AS uuid), :m, "
                    "'chat', 't', :m, 'x')"), {"o": p.org_a, "m": B})

    def test_the_database_refuses_a_link_out_of_the_app(self, promoted):  # noqa: F811
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        with (pytest.raises(DBAPIError, match="reach_requests_href_relative"),
              promoted.admin_engine.begin() as c):
            c.execute(text(
                "INSERT INTO reach_requests (organization_id, member_email, source, title, "
                "requested_by, dedupe_key, href) VALUES (CAST(:o AS uuid), :m, 'chat', 't', "
                ":m, 'h', '//evil.test')"), {"o": promoted.org_a, "m": A})
