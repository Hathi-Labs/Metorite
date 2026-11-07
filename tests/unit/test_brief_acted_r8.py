"""WS-BRIEF BR-7b-2 on a real database: the ``acted`` section of the close-out.

Spec: ``project-docs/specs/core_brief.md`` §8 and acceptance rows 15 and 16.
The suite reuses the world of ``test_brief_closeout_r8.py``: two orgs, the
app role, a fake clock, and the feed read and the Handled read faked. The
acts are REAL rows: the admin seeds ``audit_event`` and ``reach_outbox``,
and the run reads them through ``acb_audit.read.acts`` and
``outbox.sent_rows`` under FORCE RLS.

Part 1 needs no database: the lines, the links and the policy words of
``gather``.

**Mutations (R7).**

* B1. ``run._closeout_reads``: drop ``claims += acted``.
  ``test_r8_three_sent_rows_and_two_acts_give_five_acted_claims`` goes red.
* B2. ``run._acted_reads``: catch the fault and answer ``[], []``.
  ``test_r8_an_acted_read_that_fails_retries`` goes red.
* B3. ``gather.audit_href``: answer the target's path with no pane check.
  ``test_the_audit_link_is_a_live_pane_or_my_day`` goes red.
"""

# ruff: noqa: F811
# The fixtures `world`, `promoted`, `app_engine` and `acted_graph` come from
# the suites of the run and the close-out.
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.brief import gather, store
from gateway.brief import run as brief_run
from gateway.reach import plan
from sqlalchemy import text

from tests.unit.test_brief_closeout_r8 import (  # noqa: F401
    TC0,
    TC1,
    _closed,
    _closeouts,
    _handled,
    _on,
    _plan_and_run,
    acted_graph,
)
from tests.unit.test_brief_run_r8 import (  # noqa: F401
    _DB_GATE,
    ANA,
    BEN,
    CARA,
    TAG,
    _claims,
    _jobs,
    _tick,
    app_engine,
    card,
    promoted,
    world,
)

#: Inside the member's day of 2099-03-10 in Kolkata, before the run at TC1.
NOON = datetime(2099, 3, 10, 6, 0, tzinfo=UTC)


# ── Part 1: the lines and the links, with no database ───────────────────────


def _act(action: str, *, policy: str | None = None, target: str = "agent:sales",
         at: datetime = NOON, **payload: Any) -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid.uuid4()), at=at, action=action, target=target,
                           policy=policy, payload=payload)


def _row(kind: str = "brief", channel: str = "whatsapp", ids: tuple[str, ...] = ("tasks:1",),
         at: datetime = NOON) -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid.uuid4()), kind=kind, channel=channel, signal_ids=ids,
                           sent_at=at)


def test_the_audit_link_is_a_live_pane_or_my_day() -> None:
    """Mutation B3."""
    assert gather.audit_href("app:projects.list_tasks") == "/projects"
    assert gather.audit_href("app:crm.get_deal") == "/crm"
    # Not a live pane, a path trick, an agent, a record: the My Day path.
    assert gather.audit_href("app:settings.x") == gather.MY_DAY_PATH
    assert gather.audit_href("app:..%2Fapi.x") == gather.MY_DAY_PATH
    assert gather.audit_href("app:.x") == gather.MY_DAY_PATH
    assert gather.audit_href("agent:sales") == gather.MY_DAY_PATH
    assert gather.audit_href("deal:42") == gather.MY_DAY_PATH
    assert gather.audit_href(None) == gather.MY_DAY_PATH
    assert gather.in_app_href(gather.MY_DAY_PATH) == "/"


def test_the_reach_link_is_the_card_of_the_first_signal() -> None:
    cards = [card("tasks:1"), card("email:acc:t1")]
    closed = [_closed("approvals:9", "Nine")]
    assert gather.reach_href(("tasks:1", "email:acc:t1"), cards, closed) == "/tasks?x=1"
    assert gather.reach_href(("email:acc:t1",), cards, closed) == "/email?x=1"
    # A card that was closed today: the Handled row has its href.
    assert gather.reach_href(("approvals:9",), cards, closed) == "/approvals?x=1"
    # No card, no signal, or a card with an outside link: the My Day path.
    assert gather.reach_href(("tasks:404",), cards, closed) == gather.MY_DAY_PATH
    assert gather.reach_href((), cards, closed) == gather.MY_DAY_PATH
    assert gather.reach_href(("crm:1",), [card("crm:1", href="https://x.test/")], []) == "/"


def test_each_line_names_its_policy_or_says_no_policy_recorded() -> None:
    door = _act("agent_door_call", policy="projects:read", target="app:projects.list_tasks",
                app="projects", op="list_tasks")
    push = _act("agent_push_by_reviewer", policy="chat:autopush")
    broker = _act("execute:send_email")
    assert gather.act_line(door) == (
        "An outside agent used projects: list_tasks (policy: projects:read)")
    assert gather.act_line(push) == (
        "Mira pushed the reviewed changes of the agent sales (policy: chat:autopush)")
    assert gather.act_line(broker) == "The Action Broker ran send_email (no policy recorded)"
    assert gather.sent_line(_row("brief")) == "Sent your brief on WhatsApp (policy: brief)"
    assert gather.sent_line(_row("reminder_due", "web_push")) == (
        "Sent you a reminder as a push (no policy recorded)")


def test_acted_claims_are_built_by_code_and_counted() -> None:
    """The fence of §8.2 item 2: each claim with no policy is counted."""
    acts = [_act("agent_door_call", policy="crm:read", target="app:crm.get_deal",
                 app="crm", op="get_deal"),
            _act("execute:send_email", at=NOON + timedelta(seconds=30))]
    sent = [_row("brief", at=NOON + timedelta(minutes=1)),
            _row("interrupt", ids=("email:acc:t1",), at=NOON - timedelta(minutes=1))]
    got = gather.acted_claims(acts, sent, [card("email:acc:t1")], [], 3)
    assert [c.position for c in got] == [3, 4, 5, 6]
    assert all(c.section == "acted" for c in got)
    assert [c.source_app for c in got] == ["reach", "audit", "audit", "reach"]
    assert [c.source_kind for c in got] == [
        "outbox", "agent_door_call", "execute:send_email", "outbox"]
    assert [c.href for c in got] == ["/email?x=1", "/crm", "/", "/"]
    assert got[0].source_id == f"reach:{sent[1].id}" and got[1].source_id == f"audit:{acts[0].id}"
    assert gather.no_policy_count(got) == 2


def test_acted_claims_are_cut_to_the_brief_lines() -> None:
    sent = [_row(at=NOON + timedelta(minutes=i)) for i in range(plan.BRIEF_LINES + 3)]
    assert len(gather.acted_claims([], sent, [], [], 0)) == plan.BRIEF_LINES


def test_the_lead_counts_the_acts() -> None:
    def claim(section: str) -> store.ClaimIn:
        return store.ClaimIn(position=0, section=section, text="x", source_app="tasks",
                             source_kind="k", source_id="tasks:1", href="/tasks")

    day = date(2099, 3, 10)
    assert gather.closeout_lead(day, [claim("acted")]) == "Your close-out, Tue 10 Mar. 1 act for you."
    assert gather.closeout_lead(day, [claim("done"), claim("moved"), claim("acted"),
                                      claim("acted")]) == (
        "Your close-out, Tue 10 Mar. 1 done, 1 moved to tomorrow and 2 acts for you.")
    assert gather.closeout_lead(day, [claim("done"), claim("moved")]) == (
        "Your close-out, Tue 10 Mar. 1 done and 1 moved to tomorrow.")


def test_the_day_window_is_the_local_day_of_the_slot() -> None:
    from zoneinfo import ZoneInfo

    slot = datetime(2099, 3, 10, 18, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    since, until = brief_run.day_window(date(2099, 3, 10), slot)
    assert since == datetime(2099, 3, 9, 18, 30, tzinfo=UTC)
    assert until - since == timedelta(days=1)
    with pytest.raises(ValueError):
        brief_run.day_window(date(2099, 3, 10), slot.replace(tzinfo=None))


def test_the_brief_package_stays_free_of_send_seams() -> None:
    """The BR-7a fence, after the new import of ``outbox.sent_rows``."""
    from tests.unit.test_job_effects_fence import (
        test_the_brief_package_imports_no_send_seam_and_no_write_route as fence,
    )

    fence()


# ── Part 2: the close-out on a real database ────────────────────────────────


def _audit(p: Any, org: str, action: str, payload: dict[str, Any], *,
           target: str = "agent:sales", at: datetime = NOON) -> str:
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO audit_event (id, at, actor, action, target, payload, organization_id) "
            "VALUES (CAST(:id AS uuid), :at, 'agent:test', :a, :t, CAST(:p AS jsonb), "
            "CAST(:o AS uuid))"),
            {"id": rid, "at": at, "a": action, "t": target,
             "p": json.dumps({**payload, "tag": TAG}), "o": org})
    return rid


def _sent(p: Any, org: str, member: str, *, kind: str = "brief", channel: str = "whatsapp",
          ids: tuple[str, ...] = ("tasks:1",), state: str = "sent",
          at: datetime | None = NOON) -> str:
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO reach_outbox (id, organization_id, member_email, channel, kind, "
            "signal_ids, dedupe_key, body, state, sent_at) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :m, :ch, :k, CAST(:ids AS text[]), :key, 'b', :st, :at)"),
            {"id": rid, "o": org, "m": member, "ch": channel, "k": kind, "ids": list(ids),
             "key": f"br7b:{TAG}:{rid}", "st": state, "at": at})
    return rid


@pytest.fixture
def acts(world: Any) -> Any:
    yield world
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM audit_event WHERE payload->>'tag' = :t"), {"t": TAG})
        c.execute(text("DELETE FROM reach_outbox WHERE dedupe_key LIKE :t"),
                  {"t": f"br7b:{TAG}:%"})


def _seed_day(p: Any) -> dict[str, str]:
    """Ana's day: 3 sent rows and 2 allow-listed acts in org A, and the rows
    that must never show."""
    ids = {
        "brief": _sent(p, p.org_a, ANA, kind="brief", ids=("tasks:1",)),
        "interrupt": _sent(p, p.org_a, ANA, kind="interrupt", ids=("approvals:9",),
                           at=NOON + timedelta(minutes=1)),
        "reminder": _sent(p, p.org_a, ANA, kind="reminder_due", channel="web_push",
                          ids=("reminders:7",), at=NOON + timedelta(minutes=2)),
        "door": _audit(p, p.org_a, "agent_door_call",
                       {"member": ANA, "decision": "allowed", "policy": "projects:read",
                        "app": "projects", "op": "list_tasks"},
                       target="app:projects.list_tasks", at=NOON + timedelta(minutes=3)),
        "push": _audit(p, p.org_a, "agent_push_by_reviewer",
                       {"member": ANA, "reviewed_by": "chat:autopush",
                        "policy": "chat:autopush"}, at=NOON + timedelta(minutes=4)),
    }
    # Never listed for Ana in org A.
    _audit(p, p.org_b, "agent_door_call", {"member": ANA, "decision": "allowed"})
    _sent(p, p.org_b, ANA)
    _audit(p, p.org_a, "llm_completion", {"member": ANA})
    _audit(p, p.org_a, "email_triage", {"member": ANA})
    _audit(p, p.org_a, "agent_door_call", {"member": ANA, "decision": "door_scope"})
    _audit(p, p.org_a, "mutation_commit_auto_pushed", {"run_id": "r1"})
    _audit(p, p.org_a, "agent_door_call", {"member": BEN, "decision": "allowed"})
    _sent(p, p.org_a, ANA, state="planned", at=None)
    _sent(p, p.org_a, ANA, at=NOON - timedelta(days=1))
    return ids


@_DB_GATE
class TestActedSection:
    def test_r8_three_sent_rows_and_two_acts_give_five_acted_claims(self, acts, monkeypatch):
        """Rows 15 and 16. Mutation B1."""
        w = acts
        _on(w, monkeypatch)
        _handled(w, monkeypatch, {ANA: [_closed("approvals:9", "Nine")]})
        w.cards[ANA] = [card("tasks:1")]
        ids = _seed_day(w.p)
        _plan_and_run(w)
        run = _closeouts(w, ANA)[0]
        assert run.state == "ready" and run.lead_source == "template"
        assert run.lead == "Your close-out, Tue 10 Mar. 1 done, 1 moved to tomorrow and 5 acts for you."
        got = _claims(w, str(run.id))
        assert [c.section for c in got] == ["done", "moved"] + ["acted"] * 5
        acted = got[2:]
        assert [c.source_id for c in acted] == [
            f"reach:{ids['brief']}", f"reach:{ids['interrupt']}", f"reach:{ids['reminder']}",
            f"audit:{ids['door']}", f"audit:{ids['push']}"]
        assert [c.source_app for c in acted] == ["reach"] * 3 + ["audit"] * 2
        assert [c.source_kind for c in acted] == [
            "outbox"] * 3 + ["agent_door_call", "agent_push_by_reviewer"]
        assert [c.href for c in acted] == [
            "/tasks?x=1", "/approvals?x=1", "/", "/projects", "/"]
        assert [c.text for c in acted] == [
            "Sent your brief on WhatsApp (policy: brief)",
            "Sent you an alert on WhatsApp (no policy recorded)",
            "Sent you a reminder as a push (no policy recorded)",
            "An outside agent used projects: list_tasks (policy: projects:read)",
            "Mira pushed the reviewed changes of the agent sales (policy: chat:autopush)"]
        assert [c.position for c in got] == list(range(7))
        # Ben's day holds his one act, and nothing of Ana's.
        ben = _closeouts(w, BEN)[0]
        assert [c.source_kind for c in _claims(w, str(ben.id))] == ["agent_door_call"]

    def test_r8_a_day_with_no_act_gives_no_acted_claim(self, acts, monkeypatch):
        w = acts
        _on(w, monkeypatch)
        _handled(w, monkeypatch, {ANA: [_closed()]})
        # Only rows that are not acts of Ana in org A.
        _audit(w.p, w.p.org_b, "agent_door_call", {"member": ANA, "decision": "allowed"})
        _audit(w.p, w.p.org_a, "agent_run_complete", {"member": ANA})
        _sent(w.p, w.p.org_b, ANA)
        _plan_and_run(w)
        ana, ben = _closeouts(w, ANA)[0], _closeouts(w, BEN)[0]
        assert [c.section for c in _claims(w, str(ana.id))] == ["done"]
        assert ana.lead == "Your close-out, Tue 10 Mar. 1 done."
        assert ben.state == "empty" and _claims(w, str(ben.id)) == []

    def test_r8_an_act_of_org_b_never_shows_for_org_a(self, acts, monkeypatch):
        """CARA is in org B. Org B's close-out lists its own act only, and
        org A's lists none of it."""
        w = acts
        monkeypatch.setattr(brief_run.flags, "get_settings", lambda: SimpleNamespace(
            brief_enabled=True, brief_orgs=f"{w.p.org_a},{w.p.org_b}", brief_closeout=True))
        _handled(w, monkeypatch)
        b_act = _audit(w.p, w.p.org_b, "agent_door_call",
                       {"member": CARA, "decision": "allowed", "policy": "crm:read",
                        "app": "crm", "op": "get_deal"}, target="app:crm.get_deal")
        _audit(w.p, w.p.org_b, "agent_door_call", {"member": ANA, "decision": "allowed"})
        from tests.unit.test_brief_run_r8 import _set_org_zone

        _set_org_zone(w, w.p.org_b, "Asia/Kolkata")
        for org in (w.p.org_a, w.p.org_b):
            _tick(w, org, TC0)
        for org in (w.p.org_a, w.p.org_b):
            _tick(w, org, TC1)
        cara = _closeouts(w, CARA)[0]
        assert [c.source_id for c in _claims(w, str(cara.id))] == [f"audit:{b_act}"]
        assert _closeouts(w, ANA)[0].state == "empty"

    def test_r8_an_acted_read_that_fails_retries(self, acts, monkeypatch):
        """Mutation B2. A lost act read must not read as a quiet day."""
        from gateway.reach import outbox

        w = acts
        _on(w, monkeypatch)
        _handled(w, monkeypatch, {ANA: [_closed()]})
        _seed_day(w.p)
        real = outbox.sent_rows

        async def down(*a: Any, **k: Any) -> Any:
            raise ConnectionError("reach_outbox is down")

        monkeypatch.setattr(outbox, "sent_rows", down)
        _plan_and_run(w)
        jobs = [j for j in _jobs(w, brief_run.planner.RUN_KIND)]
        assert jobs and all(j.state == "queued" and j.attempts == 1 for j in jobs)
        assert all(j.last_error.startswith("ActedIncomplete") for j in jobs)
        assert all(r.state == "planned" for r in _closeouts(w))
        monkeypatch.setattr(outbox, "sent_rows", real)
        _tick(w, w.p.org_a, TC1 + brief_run.queue.backoff(1) + timedelta(seconds=1))
        ana = _closeouts(w, ANA)[0]
        assert ana.state == "ready"
        assert [c.section for c in _claims(w, str(ana.id))].count("acted") == 5
