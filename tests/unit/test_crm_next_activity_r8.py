"""R8 — the next activity on each card (WS-53 CRM-X3, ``crm_platform.md`` §19.7).

``core.attach_next_activity`` reads the next open activity of each deal and
lead in ONE ``DISTINCT ON`` statement over ``crm_activities``. The list route
calls it once for each page, and ``GET /crm/pipeline`` calls it once after the
lane loop. ``card_sort=next_activity`` orders the cards of a deal lane by the
same rule.

The suite runs against a REAL Postgres, through the real ``/crm`` router, as
the non-privileged role of the phase-4 rehearsal. A fake agrees with any SQL,
so only this suite proves the rule, the order, the statement count and the
tenant fence (done-when 1 to 8 of §19.7, and the CRM-X3 note of §19.10).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_activities", "crm_deal_contacts", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_pipeline_stages", "crm_pipelines",
    "crm_settings", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)


def _admin(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="admin@crm-x3-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


async def _call(
    p: Any, org: str, method: str, path: str, body: dict | None = None,
    *, statements: list[str] | None = None,
) -> Any:
    """One request through the real router. The tenant comes from the user.

    With ``statements``, each statement that the shared engine sends to
    Postgres during the request is appended to it. The listener hooks the
    engine, so a read that skips the helper is counted too.
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common import db as shared
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _admin(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    def _record(_conn, _cur, statement, _params, _ctx, _many) -> None:
        if statements is not None:
            statements.append(statement)

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            engine = shared.get_engine().sync_engine
            event.listen(engine, "before_cursor_execute", _record)
            try:
                return await client.request(method, path, json=body)
            finally:
                event.remove(engine, "before_cursor_execute", _record)
    finally:
        release_tenant(scope)


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


# ── Seeding, as the admin role (the routes under test only read) ────────────

async def _native(p: Any, org: str) -> dict[str, str]:
    """Native mode seeds the stages. Returns stage name → id."""
    res = await _call(p, org, "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT name, id FROM crm_deal_statuses "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).fetchall()
    return {r.name: str(r.id) for r in rows}


def _deal(
    p: Any, org: str, name: str, status_id: str, *,
    amount: float | None = None, changed: datetime | None = None,
) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_deals "
            "(organization_id, name, status_id, amount, status_changed_at) "
            "VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid), :amount, "
            "COALESCE(CAST(:changed AS timestamptz), now())) RETURNING id"),
            {"o": org, "n": name, "s": status_id, "amount": amount,
             "changed": changed}).scalar())


def _lead(p: Any, org: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        status = c.execute(text(
            "SELECT id FROM crm_lead_statuses WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY position LIMIT 1"), {"o": org}).scalar()
        return str(c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
            "VALUES (CAST(:o AS uuid), :n, :s) RETURNING id"),
            {"o": org, "n": name, "s": status}).scalar())


_TARGETS = frozenset({"deal_id", "lead_id"})


def _activity(
    p: Any, org: str, target: str, record: str, kind: str, *,
    subject: str | None = None, due: datetime | None = None,
    occurred: datetime | None = None, completed: datetime | None = None,
    zoho: bool = False,
) -> str:
    assert target in _TARGETS
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_activities "
            f"(organization_id, type, subject, due_at, occurred_at, completed_at, "
            f"{target}, created_by, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), :t, :s, :due, :occ, :done, "
            "CAST(:r AS uuid), 'seed@crm-x3.test', :src, :ext) RETURNING id"),
            {"o": org, "t": kind, "s": subject, "due": due, "occ": occurred,
             "done": completed, "r": record,
             "src": "zoho" if zoho else None,
             "ext": uuid.uuid4().hex if zoho else None}).scalar())


def _day(offset: int) -> datetime:
    """A due date as the Zoho sync and the composer write it: 00:00 UTC."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=offset)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _rows(p: Any, org: str, path: str) -> dict[str, dict[str, Any]]:
    res = await _call(p, org, "GET", path)
    assert res.status_code == 200, res.text
    rows = res.json()["rows"]
    key = "lead_name" if "/leads" in path else (
        "first_name" if "/contacts" in path else "name")
    return {row[key]: row for row in rows}


# ── Done-when 1 to 4: the rule ──────────────────────────────────────────────

async def test_the_rule_picks_the_soonest_open_dated_activity(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    stages = await _native(ns, a)
    lane = stages["Qualification"]
    now = datetime.now(UTC)

    # 1. Yesterday's task beats tomorrow's, and a completed task drops out
    #    even though its due date is the earliest of the three.
    one = _deal(ns, a, "One", lane)
    _activity(ns, a, "deal_id", one, "task", subject="Done already",
              due=_day(-3), completed=now - timedelta(days=2))
    late = _activity(ns, a, "deal_id", one, "task", subject="Send the quote", due=_day(-1))
    _activity(ns, a, "deal_id", one, "task", subject="Chase", due=_day(1))

    # 2. A future meeting before every task wins. A past meeting is history.
    two = _deal(ns, a, "Two", lane)
    _activity(ns, a, "deal_id", two, "meeting", subject="Kick-off was",
              occurred=now - timedelta(days=4))
    meet = _activity(ns, a, "deal_id", two, "meeting", subject="Demo",
                     occurred=now + timedelta(hours=2))
    _activity(ns, a, "deal_id", two, "task", subject="Proposal", due=_day(2))
    _activity(ns, a, "deal_id", two, "call", subject="Old call",
              occurred=now - timedelta(hours=1))

    # 3. A Zoho task and a native task count alike: the sooner one wins,
    #    whichever source wrote it.
    three = _deal(ns, a, "Three", lane)
    zoho = _activity(ns, a, "deal_id", three, "task", subject="From Zoho",
                     due=_day(3), zoho=True)
    _activity(ns, a, "deal_id", three, "task", subject="Native later", due=_day(5))
    four = _deal(ns, a, "Four", lane)
    native = _activity(ns, a, "deal_id", four, "task", subject="Native first", due=_day(1))
    _activity(ns, a, "deal_id", four, "task", subject="Zoho later", due=_day(4), zoho=True)

    # 4. An undated task, a note and a past call give no next activity.
    five = _deal(ns, a, "Five", lane)
    _activity(ns, a, "deal_id", five, "task", subject="Someday")
    _activity(ns, a, "deal_id", five, "note", subject="A note", occurred=now)
    _activity(ns, a, "deal_id", five, "call", subject="Rang",
              occurred=now - timedelta(days=1))
    _deal(ns, a, "Six", lane)

    # A tie on the due date goes to the smaller id.
    seven = _deal(ns, a, "Seven", lane)
    tied = sorted(
        _activity(ns, a, "deal_id", seven, "task", subject=f"Tie {i}", due=_day(6))
        for i in range(3)
    )

    rows = await _rows(ns, a, "/crm/deals?page_size=100")

    got = rows["One"]["next_activity"]
    assert got["id"] == late
    assert got["type"] == "task" and got["subject"] == "Send the quote"
    assert got["all_day"] is True
    assert _parse(got["at"]) == _day(-1)

    got = rows["Two"]["next_activity"]
    assert got["id"] == meet
    assert got["type"] == "meeting" and got["all_day"] is False

    assert rows["Three"]["next_activity"]["id"] == zoho
    assert rows["Four"]["next_activity"]["id"] == native

    assert rows["Five"]["next_activity"] is None
    assert rows["Six"]["next_activity"] is None
    assert rows["Seven"]["next_activity"]["id"] == tied[0]


def test_the_rule_has_no_branch_on_the_source() -> None:
    """Done-when 3, the static half: no ``ext_source`` in the rule or the read."""
    from gateway.routes.crm import core

    assert "ext_source" not in core.NEXT_ACTIVITY_SQL
    assert "ext_source" not in inspect.getsource(core.attach_next_activity)
    assert "zoho" not in inspect.getsource(core.attach_next_activity).lower()


# ── Done-when 7: leads, through lead_id ─────────────────────────────────────

async def test_leads_carry_it_and_contacts_do_not(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    await _native(ns, a)
    hot = _lead(ns, a, "Hot lead")
    _lead(ns, a, "Cold lead")
    call = _activity(ns, a, "lead_id", hot, "call", subject="Call back",
                     occurred=datetime.now(UTC) + timedelta(days=1))
    contact = await _call(ns, a, "POST", "/crm/contacts", {"first_name": "Anitha"})
    assert contact.status_code == 201, contact.text

    leads = await _rows(ns, a, "/crm/leads")
    assert leads["Hot lead"]["next_activity"]["id"] == call
    assert leads["Hot lead"]["next_activity"]["type"] == "call"
    assert leads["Cold lead"]["next_activity"] is None

    contacts = await _rows(ns, a, "/crm/contacts")
    assert "next_activity" not in contacts["Anitha"]


# ── Done-when 8: the tenant fence ───────────────────────────────────────────

async def test_an_activity_of_org_b_never_sets_an_org_a_card(ns, app_engine) -> None:  # noqa: F811
    a, b = ns.org_a, ns.org_b
    stages = await _native(ns, a)
    await _native(ns, b)
    deal = _deal(ns, a, "Ours", stages["Qualification"])
    # Org B's row names org A's deal. The foreign key cannot see the tenant,
    # so only row level security keeps it off org A's card.
    _activity(ns, b, "deal_id", deal, "task", subject="Not yours", due=_day(-1))

    rows = await _rows(ns, a, "/crm/deals")
    assert rows["Ours"]["next_activity"] is None

    board = await _call(ns, a, "GET", "/crm/pipeline")
    assert board.status_code == 200, board.text
    cards = [r for lane in board.json()["lanes"] for r in lane["rows"]]
    assert [c["next_activity"] for c in cards if c["name"] == "Ours"] == [None]


# ── Done-when 5: one statement for the whole board ──────────────────────────

async def test_the_board_reads_the_next_activities_in_one_statement(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    stages = await _native(ns, a)
    lanes = [stages["Qualification"], stages["Proposal"], stages["Negotiation"]]
    expected: dict[str, str] = {}
    for i in range(40):
        deal = _deal(ns, a, f"Card {i:02d}", lanes[i % 3])
        expected[deal] = _activity(ns, a, "deal_id", deal, "task",
                                   subject=f"Task {i}", due=_day(i - 20))

    seen: list[str] = []
    res = await _call(ns, a, "GET", "/crm/pipeline", statements=seen)
    assert res.status_code == 200, res.text

    reads = [s for s in seen if "crm_activities" in s]
    assert len(reads) == 1, reads
    assert "DISTINCT ON" in reads[0]

    cards = [r for lane in res.json()["lanes"] for r in lane["rows"]]
    assert len(cards) == 40
    assert {c["id"]: c["next_activity"]["id"] for c in cards} == expected


# ── Done-when 6: the lane sort ──────────────────────────────────────────────

async def test_card_sort_next_activity_orders_a_lane_soonest_first(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    stages = await _native(ns, a)
    lane = stages["Proposal"]
    now = datetime.now(UTC)
    # The stage age order is the reverse of the creation order below.
    made = {}
    for i, name in enumerate(("Later", "None A", "Overdue", "Soon meeting", "None B")):
        made[name] = _deal(ns, a, name, lane, amount=1000.0 * (i + 1),
                           changed=now - timedelta(days=10 - i))
    _activity(ns, a, "deal_id", made["Later"], "task", subject="Later", due=_day(3))
    _activity(ns, a, "deal_id", made["Overdue"], "task", subject="Late", due=_day(-1))
    _activity(ns, a, "deal_id", made["Soon meeting"], "meeting", subject="Demo",
              occurred=now + timedelta(hours=1))
    # A completed task and an undated task change nothing.
    _activity(ns, a, "deal_id", made["None A"], "task", subject="Done",
              due=_day(-5), completed=now)
    _activity(ns, a, "deal_id", made["None B"], "task", subject="Undated")

    def _lane(payload: dict) -> dict:
        return next(x for x in payload["lanes"] if x["status"]["id"] == lane)

    default = await _call(ns, a, "GET", "/crm/pipeline")
    assert default.status_code == 200, default.text
    by_age = _lane(default.json())
    assert [r["name"] for r in by_age["rows"]] == [
        "None B", "Soon meeting", "Overdue", "None A", "Later"]

    sorted_ = await _call(ns, a, "GET", "/crm/pipeline?card_sort=next_activity")
    assert sorted_.status_code == 200, sorted_.text
    by_next = _lane(sorted_.json())
    nones = sorted((made["None A"], made["None B"]), reverse=True)
    assert [r["id"] for r in by_next["rows"]] == [
        made["Overdue"], made["Soon meeting"], made["Later"], *nones]
    assert "next_at" not in by_next["rows"][0]

    # The totals of every lane do not change with the order.
    def _totals(payload: dict) -> list[tuple]:
        return [(x["status"]["id"], x["count"], x["amount"], x["weighted"])
                for x in payload["lanes"]]

    assert _totals(sorted_.json()) == _totals(default.json())
    assert (await _call(ns, a, "GET", "/crm/pipeline?card_sort=stage_age")).status_code == 200

    refused = await _call(ns, a, "GET", "/crm/pipeline?card_sort=amount")
    assert refused.status_code == 422, refused.text
