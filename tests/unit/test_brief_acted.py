"""WS-BRIEF BR-7b-2 with no database: the lines, the links, the rank, the
collapse, the cut and the lead of the ``acted`` section of the close-out.

Spec: ``project-docs/specs/core_brief.md`` §8. Code: ``gateway/brief/gather.py``
(``acted_claims``, ``closeout_lead``). The R8 half is
``test_brief_acted_r8.py``. This file needs no ``TENANT_LADDER_DATABASE_URL``.

**Mutations (R7).**

* B3. ``gather.audit_href``: answer the target's path with no pane check.
  ``test_the_audit_link_is_a_live_pane_or_my_day`` goes red.
* B4. ``gather.acted_claims``: sort by time only, not by rank.
  ``test_twenty_door_reads_and_one_push_show_the_push`` goes red.
* B5. ``gather.closeout_lead``: count the kept lines, not ``acted.total``.
  ``test_the_lead_names_the_true_total_and_the_shown_count`` goes red.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.brief import gather, store
from gateway.brief import run as brief_run
from gateway.reach import plan

NOON = datetime(2099, 3, 10, 6, 0, tzinfo=UTC)
DAY = date(2099, 3, 10)


def card(sid: str, **kw: Any) -> dict[str, Any]:
    return {"signal_id": sid, "kind": "needs_reply", "href": f"/{sid.split(':')[0]}?x=1", **kw}


def closed(key: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, href=f"/{key.split(':')[0]}?x=1")


def _act(action: str, *, policy: str | None = None, target: str = "agent:sales",
         at: datetime = NOON, **payload: Any) -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid.uuid4()), at=at, action=action, target=target,
                           policy=policy, payload=payload)


def _read(app: str = "projects", at: datetime = NOON) -> SimpleNamespace:
    return _act("agent_door_call", policy=f"{app}:read", target=f"app:{app}.list_tasks",
                at=at, app=app, op="list_tasks", decision="allowed")


def _row(kind: str = "brief", channel: str = "whatsapp", ids: tuple[str, ...] = ("tasks:1",),
         at: datetime = NOON) -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid.uuid4()), kind=kind, channel=channel, signal_ids=ids,
                           sent_at=at)


def test_the_audit_link_is_a_live_pane_or_my_day() -> None:
    """Mutation B3."""
    assert gather.audit_href("app:projects.list_tasks") == "/projects"
    assert gather.audit_href("app:crm.get_deal") == "/crm"
    assert gather.audit_href("app:settings.x") == gather.MY_DAY_PATH
    assert gather.audit_href("app:..%2Fapi.x") == gather.MY_DAY_PATH
    assert gather.audit_href("app:.x") == gather.MY_DAY_PATH
    assert gather.audit_href("agent:sales") == gather.MY_DAY_PATH
    assert gather.audit_href("deal:42") == gather.MY_DAY_PATH
    assert gather.audit_href(None) == gather.MY_DAY_PATH
    assert gather.in_app_href(gather.MY_DAY_PATH) == "/"


def test_the_reach_link_is_the_card_of_the_first_signal() -> None:
    cards = [card("tasks:1"), card("email:acc:t1")]
    done = [closed("approvals:9")]
    assert gather.reach_href(("tasks:1", "email:acc:t1"), cards, done) == "/tasks?x=1"
    assert gather.reach_href(("email:acc:t1",), cards, done) == "/email?x=1"
    assert gather.reach_href(("approvals:9",), cards, done) == "/approvals?x=1"
    assert gather.reach_href(("tasks:404",), cards, done) == gather.MY_DAY_PATH
    assert gather.reach_href((), cards, done) == gather.MY_DAY_PATH
    assert gather.reach_href(("crm:1",), [card("crm:1", href="https://x.test/")], []) == "/"


def test_each_line_names_its_policy_or_says_no_policy_recorded() -> None:
    door = _read()
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
    assert gather.sent_line(_row("due_summary", "web_push")) == (
        "Sent you the summary of what is due as a push (no policy recorded)")


def test_acted_claims_are_built_by_code_ranked_and_counted() -> None:
    """The fence of §8.2 item 2: each claim with no policy is counted. The
    changes and the sent rows go before a door read."""
    acts = [_read("crm", at=NOON - timedelta(minutes=5)),
            _act("execute:send_email", at=NOON + timedelta(seconds=30))]
    sent = [_row("brief", at=NOON + timedelta(minutes=1)),
            _row("interrupt", ids=("email:acc:t1",), at=NOON - timedelta(minutes=1))]
    got = gather.acted_claims(acts, sent, [card("email:acc:t1")], [], 3)
    claims = got.claims
    assert [c.position for c in claims] == [3, 4, 5, 6]
    assert all(c.section == "acted" for c in claims)
    assert [c.source_app for c in claims] == ["reach", "audit", "reach", "audit"]
    assert [c.source_kind for c in claims] == [
        "outbox", "execute:send_email", "outbox", "agent_door_call"]
    assert [c.href for c in claims] == ["/email?x=1", "/", "/", "/crm"]
    assert claims[0].source_id == f"reach:{sent[1].id}"
    assert claims[3].source_id == f"audit:{acts[0].id}"
    assert gather.no_policy_count(claims) == 2
    assert (got.total, got.shown) == (4, 4)


def test_twenty_door_reads_and_one_push_show_the_push() -> None:
    """Mutation B4. One busy client reads 20 times in the morning. The push
    at 15:00 still shows, first, and the reads make ONE line."""
    reads = [_read(at=NOON + timedelta(minutes=i)) for i in range(20)]
    push = _act("agent_push_by_reviewer", policy="chat:autopush",
                at=datetime(2099, 3, 10, 9, 30, tzinfo=UTC))
    got = gather.acted_claims([*reads, push], [], [], [], 0)
    assert [c.source_kind for c in got.claims] == ["agent_push_by_reviewer", "agent_door_call"]
    assert got.claims[0].source_id == f"audit:{push.id}"
    collapsed = got.claims[1]
    assert collapsed.text == "An outside agent used projects: 20 reads (policy: projects:read)"
    # The collapsed line names the OLDEST read of the group, a real row.
    assert collapsed.source_id == f"audit:{reads[0].id}"
    assert collapsed.href == "/projects"
    assert (got.total, got.shown) == (21, 21)
    lead = gather.closeout_lead(DAY, got.claims, got)
    assert lead == "Your close-out, Tue 10 Mar. 21 acts for you."


def test_the_door_reads_collapse_by_app_and_policy_and_a_write_keeps_its_line() -> None:
    reads = [_read("projects"), _read("projects", NOON + timedelta(minutes=1)),
             _read("crm", NOON - timedelta(minutes=1))]
    write = _act("agent_door_call", policy="projects:write", target="app:projects.add_task",
                 app="projects", op="add_task", decision="allowed")
    got = gather.acted_claims([*reads, write], [], [], [], 0)
    assert [c.text for c in got.claims] == [
        "An outside agent used projects: add_task (policy: projects:write)",
        "An outside agent used crm: list_tasks (policy: crm:read)",
        "An outside agent used projects: 2 reads (policy: projects:read)"]
    assert len({c.source_id for c in got.claims}) == 3


def test_the_lead_names_the_true_total_and_the_shown_count() -> None:
    """Mutation B5. 25 acts on 25 lines: 7 show, and the lead says so."""
    sent = [_row(at=NOON + timedelta(minutes=i)) for i in range(25)]
    got = gather.acted_claims([], sent, [], [], 0)
    assert len(got.claims) == plan.BRIEF_LINES
    assert (got.total, got.shown) == (25, plan.BRIEF_LINES)

    def claim(section: str) -> store.ClaimIn:
        return store.ClaimIn(position=0, section=section, text="x", source_app="tasks",
                             source_kind="k", source_id="tasks:1", href="/tasks")

    lead = gather.closeout_lead(DAY, [claim("done"), claim("moved"), *got.claims], got)
    assert lead == ("Your close-out, Tue 10 Mar. 1 done, 1 moved to tomorrow and "
                    "25 acts for you, 7 shown.")


def test_a_section_with_no_claim_names_no_act() -> None:
    got = gather.acted_claims([], [], [], [], 0)
    assert got.claims == [] and got.total == 0
    assert gather.closeout_lead(DAY, [], got) is None
    # A count with no claim behind it never reaches the lead.
    fake = gather.Acted(claims=[], total=9, shown=0)
    assert gather.closeout_lead(DAY, [], fake) is None


def test_the_lead_counts_the_acts() -> None:
    def claim(section: str) -> store.ClaimIn:
        return store.ClaimIn(position=0, section=section, text="x", source_app="tasks",
                             source_kind="k", source_id="tasks:1", href="/tasks")

    assert gather.closeout_lead(DAY, [claim("acted")]) == "Your close-out, Tue 10 Mar. 1 act for you."
    assert gather.closeout_lead(DAY, [claim("done"), claim("moved"), claim("acted"),
                                      claim("acted")]) == (
        "Your close-out, Tue 10 Mar. 1 done, 1 moved to tomorrow and 2 acts for you.")
    assert gather.closeout_lead(DAY, [claim("done"), claim("moved")]) == (
        "Your close-out, Tue 10 Mar. 1 done and 1 moved to tomorrow.")


def test_the_day_window_is_the_local_day_of_the_slot() -> None:
    from zoneinfo import ZoneInfo

    slot = datetime(2099, 3, 10, 18, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    since, until = brief_run.day_window(DAY, slot)
    assert since == datetime(2099, 3, 9, 18, 30, tzinfo=UTC)
    assert until - since == timedelta(days=1)
    with pytest.raises(ValueError):
        brief_run.day_window(DAY, slot.replace(tzinfo=None))


def test_the_brief_package_stays_free_of_send_seams() -> None:
    """The BR-7a fence, after the new import of ``outbox.sent_rows``."""
    from tests.unit.test_job_effects_fence import (
        test_the_brief_package_imports_no_send_seam_and_no_write_route as fence,
    )

    fence()
