"""WS-64 OP-6d: the report delivery tools of the chat against the real gateway (R8, D-OP-9).

Spec: ``project-docs/specs/one_projects_app.md`` §5a and §9 OP-6d, and
``projects_reports.md`` §9.12.8 (delivery is built and dark).

``add_report_recipient``, ``remove_report_recipient`` and ``set_report_schedule``
run through the skill's own client against the REAL gateway app, on a promoted
catalog of their own as a NON-privileged role. The world is the one of
``test_projects_write_authority.py``: CREATOR wrote the report, VIEWER did not
and is no admin, LEAD is a known member to add. ``REPADMIN`` holds
``admin:members:read``, the permission that makes an admin of a report.

What a fake cannot prove, and this file does:

* the author adds a directory member through the chat tool, and the row is there;
* a member who is not the author and no admin is refused BEFORE any card with the
  sentence of the route, and no row changes, for all three tools;
* an admin who is not the author changes the list and the schedule;
* an address the directory does not know is refused by the route, and no row;
* a declined card writes nothing;
* the schedule goes on, the receipt says that reports are not emailed yet, and
  nothing is sent: the SMTP door is shut for the test, and the only writes the
  chat made are the ones the manifest names.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_report_delivery_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red. Commit before each.

* R1. ``guarded.py`` ``_report_for_change``: no ``can_edit`` check, and
  ``reports.py`` ``_may_change``: always true.
  ``test_r8_a_member_who_is_not_the_author_is_refused_before_any_card``.
* R2. ``reports.py`` ``add_recipient``: no ``_visible_report`` refusal.
  ``test_r8_the_route_still_decides_without_the_flag``.
* R3. ``guarded.py`` ``add_report_recipient``: skip the card.
  ``test_r8_a_declined_card_writes_nothing``.
* R4. ``guarded.py`` ``remove_report_recipient``: send the DELETE for the
  first address of the list. ``test_r8_the_author_removes_one_recipient``.
* R5. ``reports.py`` ``add_recipient``: no ``_known_member`` check.
  ``test_r8_an_address_the_directory_does_not_know_is_refused``.
* R6. ``guarded.py`` ``set_report_schedule``: send ``{"enabled": False}`` for a
  true. ``test_r8_the_schedule_goes_on_and_nothing_is_emailed``.
"""

from __future__ import annotations

import smtplib
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from sqlalchemy import text

from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import approve, deny
from tests.unit.test_projects_write_authority import (  # noqa: F401 — the fixtures
    CREATOR,
    LEAD,
    VIEWER,
    _as,
    _chat,
    live,
    live_app,
    redis_fake,
    world,
)

pytestmark = w._DB_GATE

REPADMIN = "repadmin@op6d.test"
GHOST = "ghost@nowhere.test"


@pytest.fixture
def reps(live):
    """The shared world, and one more member who holds ``admin:members:read``."""
    if not getattr(live, "repadmin", False):
        live.seeder.member(REPADMIN, ["feature:projects", "admin:members:read"])
        live.repadmin = True
    return live


@pytest.fixture(autouse=True)
def _no_mail(monkeypatch):
    """The SMTP door is shut. A send anywhere in the gateway would raise here, and the
    list records any try. The gateway has no mail path for reports at all."""
    tries: list[str] = []

    def refuse(*_a: Any, **_k: Any) -> None:
        tries.append("smtp")
        raise AssertionError("a report tool tried to send mail")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)
    monkeypatch.delenv("PROJECT_REPORT_EMAIL_ENABLED", raising=False)
    yield tries
    assert tries == [], "mail was sent"


def _report(world: Any, name: str, author: str = CREATOR) -> str:
    return str(world.seeder.one(
        "INSERT INTO pm_reports (organization_id, name, config, created_by)"
        " VALUES (CAST(:o AS uuid), :n, '{}'::jsonb, :a) RETURNING id",
        o=world.org, n=f"{name} {uuid.uuid4().hex[:6]}", a=author))


def _list(world: Any, report: str) -> list[str]:
    with world.admin.begin() as c:
        return sorted(r.recipient for r in c.execute(text(
            "SELECT recipient FROM pm_report_recipients"
            " WHERE report_id = CAST(:r AS uuid)"), {"r": report}))


def _put(world: Any, report: str, *emails: str) -> None:
    for e in emails:
        world.seeder.one(
            "INSERT INTO pm_report_recipients (report_id, recipient, created_by,"
            " organization_id) VALUES (CAST(:r AS uuid), :e, :a, CAST(:o AS uuid))"
            " RETURNING report_id", r=report, e=e, a=CREATOR, o=world.org)


def _row(world: Any, report: str) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(
            "SELECT enabled, schedule FROM pm_reports WHERE id = CAST(:r AS uuid)"),
            {"r": report}).one()


def _words() -> str:
    from gateway.routes.projects.reports import CHANGE_REFUSED

    return CHANGE_REFUSED


async def test_r8_the_report_read_carries_can_edit_for_each_member(reps) -> None:
    rid = _report(reps, "flags")
    for who, may in ((CREATOR, True), (REPADMIN, True), (VIEWER, False), (LEAD, False)):
        got = (await _as(reps, who, "GET", f"/projects/reports/{rid}")).json()
        assert got["can_edit"] is may, (who, got)


async def test_r8_the_author_adds_a_directory_member(reps, monkeypatch) -> None:
    from skill_projects import guarded

    rid = _report(reps, "add")
    asked = approve(monkeypatch)
    out = await _chat(reps, monkeypatch, CREATOR, lambda: guarded.add_report_recipient(rid, LEAD))
    assert len(asked) == 1 and LEAD in asked[0]["detail"], asked
    assert "Delivery may be switched off" in asked[0]["context"]
    assert "is now on the list of" in out, out
    assert _list(reps, rid) == [LEAD]


async def test_r8_an_admin_who_is_not_the_author_may_change_the_list(reps, monkeypatch) -> None:
    from skill_projects import guarded

    rid = _report(reps, "admin")
    approve(monkeypatch)
    await _chat(reps, monkeypatch, REPADMIN, lambda: guarded.add_report_recipient(rid, VIEWER))
    assert _list(reps, rid) == [VIEWER]


async def test_r8_the_author_removes_one_recipient(reps, monkeypatch) -> None:
    from skill_projects import guarded

    rid = _report(reps, "remove")
    _put(reps, rid, LEAD, VIEWER)
    asked = approve(monkeypatch)
    out = await _chat(reps, monkeypatch, CREATOR,
                      lambda: guarded.remove_report_recipient(rid, VIEWER))
    assert len(asked) == 1 and VIEWER in asked[0]["detail"]
    assert "is off the list of" in out, out
    assert _list(reps, rid) == [LEAD]


async def test_r8_a_member_who_is_not_the_author_is_refused_before_any_card(
    reps, monkeypatch,
) -> None:
    from skill_projects import guarded

    rid = _report(reps, "refused")
    _put(reps, rid, LEAD)
    before = _list(reps, rid)
    asked = approve(monkeypatch)
    for act in (
        lambda: guarded.add_report_recipient(rid, VIEWER),
        lambda: guarded.remove_report_recipient(rid, LEAD),
        lambda: guarded.set_report_schedule(rid, True),
    ):
        out = await _chat(reps, monkeypatch, VIEWER, act)
        assert _words() in out, out
    assert asked == [], "no card for a change the server refuses"
    assert _list(reps, rid) == before
    assert tuple(_row(reps, rid)) == (False, None)


async def test_r8_the_route_still_decides_without_the_flag(reps, monkeypatch) -> None:
    """The flag is the chat's courtesy. Without it the card shows, and the route
    answers 403 with the same sentence and writes nothing."""
    from skill_projects import guarded
    from skill_projects.refusals import refusals_as_text

    rid = _report(reps, "route")

    async def no_flag(report_id: str, tool: str) -> Any:
        return report_id, {"name": "x"}, ""

    monkeypatch.setattr(guarded, "_report_for_change", no_flag)
    asked = approve(monkeypatch)
    out = await _chat(reps, monkeypatch, VIEWER,
                      lambda: refusals_as_text(guarded.add_report_recipient)(rid, LEAD))
    assert len(asked) == 1
    assert out.startswith("Refused:") and _words() in out, out
    assert _list(reps, rid) == []


async def test_r8_an_address_the_directory_does_not_know_is_refused(reps, monkeypatch) -> None:
    from skill_projects import guarded
    from skill_projects.refusals import refusals_as_text

    rid = _report(reps, "unknown")
    approve(monkeypatch)
    out = await _chat(reps, monkeypatch, CREATOR,
                      lambda: refusals_as_text(guarded.add_report_recipient)(rid, GHOST))
    assert out.startswith("Refused:") and "is not an address this directory knows" in out, out
    assert _list(reps, rid) == []


async def test_r8_a_declined_card_writes_nothing(reps, monkeypatch) -> None:
    from skill_projects import guarded

    rid = _report(reps, "declined")
    _put(reps, rid, LEAD)
    asked = deny(monkeypatch)
    for act in (
        lambda: guarded.add_report_recipient(rid, VIEWER),
        lambda: guarded.remove_report_recipient(rid, LEAD),
        lambda: guarded.set_report_schedule(rid, True),
    ):
        await _chat(reps, monkeypatch, CREATOR, act)
    assert len(asked) == 3
    assert _list(reps, rid) == [LEAD]
    assert tuple(_row(reps, rid)) == (False, None)


async def test_r8_the_schedule_goes_on_and_nothing_is_emailed(reps, monkeypatch) -> None:
    from skill_projects import guarded

    rid = _report(reps, "schedule")
    _put(reps, rid, LEAD)
    asked = approve(monkeypatch)
    out = await _chat(reps, monkeypatch, CREATOR, lambda: guarded.set_report_schedule(rid, True))
    assert "every week" in asked[0]["detail"]
    assert tuple(_row(reps, rid)) == (True, "weekly")
    # The route answered `delivery_armed_on_this_deployment` false: the switch is off here.
    assert "Reports are not emailed yet: delivery is switched off." in out, out
    off = await _chat(reps, monkeypatch, CREATOR, lambda: guarded.set_report_schedule(rid, False))
    assert "is off" in off and tuple(_row(reps, rid)) == (False, None)


async def test_r8_the_schedule_of_a_report_with_nobody_on_the_list_is_refused(
    reps, monkeypatch,
) -> None:
    from skill_projects import guarded

    rid = _report(reps, "empty")
    asked = approve(monkeypatch)
    out = await _chat(reps, monkeypatch, CREATOR, lambda: guarded.set_report_schedule(rid, True))
    assert "Add at least one recipient" in out and asked == []
    assert tuple(_row(reps, rid)) == (False, None)
