"""WS-BRIEF BR-7b-1: the reader of the acts of one member's day.

Spec: ``project-docs/specs/core_brief.md`` §8.1 to §8.3 and acceptance row 15.
Code: ``packages/acb_audit/acb_audit/read.py`` (``acts``) and
``gateway/reach/outbox.py`` (``sent_rows``).

Part 1 runs with no database: the allow-list of §8.1 as constants and as the
pure rule ``is_act``. Part 2 is R8. It runs on the phase-4 promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the NOBYPASSRLS app role, with two
orgs. The admin seeds the rows, so each read is the first thing that the app
role does.

Run (a PRIVATE ladder database)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_audit_acts_read_r8.py -v -rs -p no:xdist

Without the database part 2 SKIPS. A skip is not a pass.

**Mutations (R7).**

* A1. ``read.acts``: open ``acb_graph.get_session()`` and not
  ``tenant_session(org)``. ``test_r8_the_acts_of_the_member_and_the_org_only``
  goes red, because the unbound read sees no row.
* A2. ``read._ACTS_SQL``: drop the member filter. The same test goes red,
  because Ben's act and the row with no member show.
* A3. ``outbox._SENT_ROWS_SQL``: drop ``state = 'sent'``.
  ``test_r8_the_sent_rows_of_the_member_and_the_org_only`` goes red.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import read
from acb_common.db import TenantUnbound
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@br7b.test"
BEN = f"ben.{TAG}@br7b.test"
#: The day of the test, 00:00 to 24:00 in Kolkata, as UTC bounds.
SINCE = datetime(2099, 3, 9, 18, 30, tzinfo=UTC)
UNTIL = SINCE + timedelta(days=1)
NOON = SINCE + timedelta(hours=6)


# ==========================================================================
# 1. No database: the allow-list of §8.1.
# ==========================================================================


def test_the_allow_list_is_the_four_actions_of_the_spec() -> None:
    assert frozenset({
        "agent_door_call", "agent_push_by_reviewer", "mutation_commit_auto_pushed"}) == read.ACT_ACTIONS
    assert read.ACT_PREFIXES == ("execute:",)


def test_the_excluded_names_are_never_an_act() -> None:
    assert frozenset({
        "llm_completion", "agent_run_complete", "agent_run_error",
        "agent_self_commit_detected", "email_triage"}) == read.EXCLUDED
    assert not (read.EXCLUDED & read.ACT_ACTIONS)
    assert not any(n.startswith(read.ACT_PREFIXES) for n in read.EXCLUDED)
    for name in read.EXCLUDED:
        assert not read.is_act(name, {"member": ANA})


@pytest.mark.parametrize(("action", "payload", "expected"), [
    ("agent_door_call", {"decision": "allowed"}, True),
    ("agent_door_call", {"decision": "door_scope"}, False),
    ("agent_door_call", {}, True),
    ("agent_push_by_reviewer", {"reviewed_by": "chat:autopush"}, True),
    ("mutation_commit_auto_pushed", None, True),
    ("execute:send_email", {"authority": "auto"}, True),
    ("execute_refused:send_email", {}, False),
    ("mutation_monorepo_pr_opened", {}, False),
    ("agent_push_request_refused", {}, False),
    ("", {}, False),
    (None, {}, False),
])
def test_is_act(action, payload, expected) -> None:
    assert read.is_act(action, payload) is expected


def test_acts_refuses_no_member_and_a_naive_bound_before_any_session(monkeypatch) -> None:
    import acb_graph

    def no_session(*a, **k):
        raise AssertionError("acts() opened a session")

    monkeypatch.setattr(acb_graph, "tenant_session", no_session)
    with pytest.raises(ValueError, match="member"):
        read.acts(str(uuid.uuid4()), "  ", SINCE, UNTIL)
    with pytest.raises(ValueError, match="aware"):
        read.acts(str(uuid.uuid4()), ANA, SINCE.replace(tzinfo=None), UNTIL)


def test_acts_with_no_org_raises_tenant_unbound() -> None:
    with pytest.raises(TenantUnbound):
        read.acts("", ANA, SINCE, UNTIL)


def test_the_sql_holds_the_bound_org_the_member_and_the_window() -> None:
    sql = read._ACTS_SQL
    assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id', true)" in sql
    assert "lower(payload->>'member') = :m" in sql
    assert "at >= :since AND at < :until" in sql


# ==========================================================================
# 2. R8: two orgs, the app role, FORCE RLS.
# ==========================================================================


@pytest.fixture
def graph(promoted, monkeypatch):  # noqa: F811
    """The ``acb_graph`` sync engine on the promoted catalog as the app role.
    The same shape as ``test_acb_graph_audit_bind.graph_on_promoted``."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield promoted
    finally:
        eng.dispose()
        with promoted.admin_engine.begin() as c:
            c.execute(text("DELETE FROM audit_event WHERE target LIKE :t"), {"t": f"%{TAG}%"})
            c.execute(text("DELETE FROM reach_outbox WHERE dedupe_key LIKE :t"),
                      {"t": f"%{TAG}%"})


def _audit(p, org: str, action: str, payload: dict, *, at: datetime = NOON) -> str:
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO audit_event (id, at, actor, action, target, payload, organization_id) "
            "VALUES (CAST(:id AS uuid), :at, 'agent:test', :a, :t, CAST(:p AS jsonb), "
            "CAST(:o AS uuid))"),
            {"id": rid, "at": at, "a": action, "t": f"app:projects.list_tasks.{TAG}",
             "p": json.dumps(payload), "o": org})
    return rid


def _outbox(p, org: str, member: str, *, state: str = "sent", kind: str = "brief",
            channel: str = "whatsapp", sent_at: datetime | None = NOON,
            signal_ids: tuple[str, ...] = ("tasks:1",)) -> str:
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO reach_outbox (id, organization_id, member_email, channel, kind, "
            "signal_ids, dedupe_key, body, state, sent_at) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :m, :ch, :k, CAST(:ids AS text[]), :key, 'b', :st, :at)"),
            {"id": rid, "o": org, "m": member, "ch": channel, "k": kind, "ids": list(signal_ids),
             "key": f"{TAG}:{rid}", "st": state, "at": sent_at})
    return rid


@_DB_GATE
class TestActsUnderRls:
    def test_r8_the_acts_of_the_member_and_the_org_only(self, graph) -> None:
        """Mutations A1 and A2."""
        p = graph
        door = _audit(p, p.org_a, "agent_door_call",
                      {"member": ANA, "decision": "allowed", "policy": "projects:read",
                       "app": "projects", "op": "list_tasks"})
        push = _audit(p, p.org_a, "agent_push_by_reviewer",
                      {"member": ANA.upper(), "reviewed_by": "chat:autopush",
                       "policy": "chat:autopush"}, at=NOON + timedelta(minutes=1))
        broker = _audit(p, p.org_a, "execute:send_email", {"member": ANA},
                        at=NOON + timedelta(minutes=2))
        # Not listed: a refusal, the excluded names, an action off the list,
        # a row with no member, another member, another day, and org B.
        _audit(p, p.org_a, "agent_door_call", {"member": ANA, "decision": "door_scope"})
        for name in sorted(read.EXCLUDED):
            _audit(p, p.org_a, name, {"member": ANA})
        _audit(p, p.org_a, "execute_refused:send_email", {"member": ANA})
        _audit(p, p.org_a, "mutation_monorepo_pr_opened", {"member": ANA})
        _audit(p, p.org_a, "mutation_commit_auto_pushed", {"run_id": "r1"})
        _audit(p, p.org_a, "agent_door_call", {"member": BEN, "decision": "allowed"})
        _audit(p, p.org_a, "agent_door_call", {"member": ANA, "decision": "allowed"},
               at=SINCE - timedelta(minutes=1))
        _audit(p, p.org_a, "agent_door_call", {"member": ANA, "decision": "allowed"},
               at=UNTIL)
        b_row = _audit(p, p.org_b, "agent_door_call", {"member": ANA, "decision": "allowed"})

        got = read.acts(p.org_a, ANA, SINCE, UNTIL)
        assert [a.id for a in got] == [door, push, broker]
        assert [a.policy for a in got] == ["projects:read", "chat:autopush", None]
        assert got[0].action == "agent_door_call" and got[0].payload["op"] == "list_tasks"
        assert all(a.member.lower() == ANA for a in got)
        assert [a.id for a in read.acts(p.org_b, ANA, SINCE, UNTIL)] == [b_row]
        assert [a.action for a in read.acts(p.org_a, BEN, SINCE, UNTIL)] == ["agent_door_call"]

    def test_r8_a_row_with_no_member_is_never_listed(self, graph) -> None:
        p = graph
        _audit(p, p.org_a, "mutation_commit_auto_pushed", {"run_id": "r1"})
        _audit(p, p.org_a, "execute:send_email", {"authority": "auto"})
        _audit(p, p.org_a, "agent_push_by_reviewer", {"member": ""})
        assert read.acts(p.org_a, ANA, SINCE, UNTIL) == []

    def test_r8_an_unknown_org_reads_nothing(self, graph) -> None:
        p = graph
        _audit(p, p.org_a, "agent_door_call", {"member": ANA, "decision": "allowed"})
        assert read.acts(str(uuid.uuid4()), ANA, SINCE, UNTIL) == []

    def test_r8_the_sent_rows_of_the_member_and_the_org_only(self, graph) -> None:
        """Mutation A3."""
        from gateway.reach import outbox

        p = graph
        first = _outbox(p, p.org_a, ANA, kind="brief")
        second = _outbox(p, p.org_a, ANA, kind="reminder_due", channel="web_push",
                         sent_at=NOON + timedelta(minutes=5), signal_ids=("reminders:9",))
        _outbox(p, p.org_a, ANA, state="planned", sent_at=None)
        _outbox(p, p.org_a, ANA, state="failed", sent_at=None)
        _outbox(p, p.org_a, ANA, state="skipped", sent_at=NOON)
        _outbox(p, p.org_a, ANA, sent_at=SINCE - timedelta(seconds=1))
        _outbox(p, p.org_a, BEN)
        b_row = _outbox(p, p.org_b, ANA)

        async def go() -> tuple[list, list]:
            dsn = p.app_url.render_as_string(hide_password=False)
            async with tenant_engine_scope(dsn):
                a = await outbox.sent_rows(p.org_a, ANA, SINCE, UNTIL)
                b = await outbox.sent_rows(p.org_b, ANA, SINCE, UNTIL)
            return a, b

        a, b = asyncio.run(go())
        assert [r.id for r in a] == [first, second]
        assert [(r.channel, r.kind, r.signal_ids) for r in a] == [
            ("whatsapp", "brief", ("tasks:1",)), ("web_push", "reminder_due", ("reminders:9",))]
        assert [r.id for r in b] == [b_row]
