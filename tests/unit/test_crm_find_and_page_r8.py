"""R8 — CRM-X1, "Find and page" (WS-53, ``crm_platform.md`` §19.5 and §19.10).

Done-when items 1 to 6, against a REAL Postgres, through the real ``/crm``
router, as the non-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS):

1. ``GET /crm/deals?q=<part of a company name>`` returns the deals of that
   company. The match is a sub-select on ``crm_companies``.
2. ``GET /crm/contacts?q=<part of the phone as the record holds it>`` returns
   that contact. Leads match on phone and mobile, companies on phone.
3. A search in organization B returns none of organization A's rows, also
   through the company sub-select. The deal of B points at a company of A on
   purpose, so only row level security inside the sub-select keeps it out.
4. Page 2 at ``page_size=50`` holds rows 51 to 100 in the same order, and no id
   of page 1.
5. ``GET /crm/owners`` holds each active member and each owner who is not a
   member, for the caller's tenant only. A member that the access rule
   refuses gets 403.
6. ``status_name`` holds the stage name on deals and leads, in the list, on
   the board and in the CSV.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_TABLES = (
    "crm_deal_contacts", "crm_activities", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_settings", "crm_deal_statuses",
    "crm_lead_statuses", "crm_lost_reasons",
)


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-x1-r8.test", org, "feature:crm", "admin:access:manage")


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

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
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    made: list[str] = []
    promoted.made_users = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        for email in made:
            c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"),
                      {"e": email.lower()})


async def _native(p: Any, org: str) -> None:
    res = await _call(p, _admin(org), "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text


async def _make(p: Any, org: str, path: str, body: dict) -> str:
    res = await _call(p, _admin(org), "POST", path, body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _rows(p: Any, org: str, path: str) -> list[dict]:
    res = await _call(p, _admin(org), "GET", path)
    assert res.status_code == 200, res.text
    return res.json()["rows"]


#: The first deal stage of the organization ``:o``, for a row the admin writes.
_FIRST_STAGE = (
    "(SELECT id FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid) "
    "ORDER BY position LIMIT 1)"
)


def _add_user(p: Any, org: str, email: str, name: str | None, status: str = "active") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :n, 'employee', :s, CAST(:o AS uuid))"),
            {"e": email, "n": name, "s": status, "o": org})
    p.made_users.append(email)


# ── 1 and 3: the company name of a deal ────────────────────────────────────

async def test_a_deal_matches_the_name_of_its_company(ns, app_engine) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    zephyr = await _make(ns, a, "/crm/organizations", {"name": f"Zephyr{tag} Robotics"})
    await _make(ns, a, "/crm/deals", {"name": "Spare parts", "company_id": zephyr})
    await _make(ns, a, "/crm/deals", {"name": "Unrelated"})

    found = await _rows(ns, a, f"/crm/deals?q=zephyr{tag}")
    assert [r["name"] for r in found] == ["Spare parts"]

    # Organization B holds a deal that points at the company of A. The FK
    # check bypasses row level security, so the admin can write it. Only the
    # sub-select's own tenant binding keeps the company name out of B's search.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, company_id, status_id) "
            "VALUES (CAST(:o AS uuid), 'Bee deal', CAST(:c AS uuid), "
            f"{_FIRST_STAGE})"),
            {"o": b, "c": zephyr})
    assert await _rows(ns, b, f"/crm/deals?q=zephyr{tag}") == []
    # B still finds its own deal by its own name, so the search runs.
    assert [r["name"] for r in await _rows(ns, b, "/crm/deals?q=bee deal")] == ["Bee deal"]
    # And A never sees B's deal.
    assert all(r["name"] != "Bee deal" for r in await _rows(ns, a, "/crm/deals?q=deal"))


# ── 2 and 3: a phone number ─────────────────────────────────────────────────

async def test_a_record_matches_its_phone_as_the_record_holds_it(ns, app_engine) -> None:  # noqa: F811
    tag = str(uuid.uuid4().int)[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    await _make(ns, a, "/crm/contacts", {"first_name": "Anitha", "phone": f"+91 98{tag} 0001"})
    await _make(ns, a, "/crm/contacts", {"first_name": "Mobile", "mobile": f"77{tag}55"})
    await _make(ns, a, "/crm/contacts", {"first_name": "Nobody", "phone": "+1 555 0100"})
    await _make(ns, a, "/crm/leads", {"lead_name": "Lead phone", "phone": f"66{tag}11"})
    await _make(ns, a, "/crm/leads", {"lead_name": "Lead mobile", "mobile": f"55{tag}22"})
    await _make(ns, a, "/crm/organizations", {"name": "Phone Co", "phone": f"44{tag}33"})
    await _make(ns, b, "/crm/contacts", {"first_name": "Bee", "phone": f"+91 98{tag} 0001"})

    def names(rows: list[dict], key: str) -> list[str]:
        return sorted(r[key] for r in rows)

    assert names(await _rows(ns, a, f"/crm/contacts?q=98{tag}"), "first_name") == ["Anitha"]
    assert names(await _rows(ns, a, f"/crm/contacts?q=77{tag}"), "first_name") == ["Mobile"]
    assert names(await _rows(ns, a, f"/crm/leads?q=66{tag}"), "lead_name") == ["Lead phone"]
    assert names(await _rows(ns, a, f"/crm/leads?q=55{tag}"), "lead_name") == ["Lead mobile"]
    assert names(await _rows(ns, a, f"/crm/organizations?q=44{tag}"), "name") == ["Phone Co"]
    # The same number in B finds B's contact only.
    assert names(await _rows(ns, b, f"/crm/contacts?q=98{tag}"), "first_name") == ["Bee"]


# ── 4: the next page ────────────────────────────────────────────────────────

async def test_page_two_holds_rows_51_to_100_and_no_id_of_page_one(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_contacts (organization_id, first_name, last_activity_at) "
            "SELECT CAST(:o AS uuid), 'P' || g, now() - (g % 7) * interval '1 hour' "
            "FROM generate_series(1, 120) g"),
            {"o": a})
    whole = await _call(ns, _admin(a), "GET", "/crm/contacts?page=1&page_size=100")
    first = await _call(ns, _admin(a), "GET", "/crm/contacts?page=1&page_size=50")
    second = await _call(ns, _admin(a), "GET", "/crm/contacts?page=2&page_size=50")
    for res in (whole, first, second):
        assert res.status_code == 200, res.text
    ids_whole = [r["id"] for r in whole.json()["rows"]]
    ids_first = [r["id"] for r in first.json()["rows"]]
    ids_second = [r["id"] for r in second.json()["rows"]]
    assert len(ids_first) == 50 and len(ids_second) == 50
    assert ids_first == ids_whole[:50]
    assert ids_second == ids_whole[50:100]
    assert not set(ids_first) & set(ids_second)
    assert second.json()["total"] == 120


# ── 5: the owners ───────────────────────────────────────────────────────────

async def test_the_owners_are_the_members_and_the_record_owners_of_the_tenant(
    ns, app_engine,  # noqa: F811
) -> None:
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    ann = f"ann.{tag}@crm-x1.test"
    sue = f"sue.{tag}@crm-x1.test"
    bob = f"bob.{tag}@crm-x1.test"
    _add_user(ns, a, ann, f"Ann {tag}")
    _add_user(ns, a, sue, f"Sue {tag}", status="suspended")
    _add_user(ns, b, bob, f"Bob {tag}")
    ghost = f"ghost.{tag}@zoho.test"
    await _native(ns, a)
    await _native(ns, b)
    with ns.admin_engine.begin() as c:
        # A Zoho owner who is not a member, in mixed case and twice.
        for table, column, value in (
            ("crm_deals", "name", "D1"), ("crm_contacts", "first_name", "C1"),
        ):
            stage = (", status_id", f", {_FIRST_STAGE}") if table == "crm_deals" else ("", "")
            c.execute(text(
                f"INSERT INTO {table} (organization_id, {column}, owner_email{stage[0]}) "
                f"VALUES (CAST(:o AS uuid), :v, :e{stage[1]})"),
                {"o": a, "v": value, "e": ghost.upper() if table == "crm_deals" else ghost})
        # A member who owns a record is listed once, as a member.
        c.execute(text(
            "INSERT INTO crm_companies (organization_id, name, owner_email) "
            "VALUES (CAST(:o AS uuid), 'Ann Co', :e)"), {"o": a, "e": ann.upper()})
        # B's own record owner must never reach A.
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, owner_email, status_id) "
            "VALUES (CAST(:o AS uuid), 'L1', :e, (SELECT id FROM crm_lead_statuses "
            "WHERE organization_id = CAST(:o AS uuid) ORDER BY position LIMIT 1))"),
            {"o": b, "e": f"bsowner.{tag}@x.test"})

    res = await _call(ns, _admin(a), "GET", "/crm/owners")
    assert res.status_code == 200, res.text
    owners = res.json()["owners"]
    assert all(set(o) == {"email", "name", "member"} for o in owners), owners
    by_email = {o["email"].lower(): o for o in owners}
    assert by_email[ann] == {"email": ann, "name": f"Ann {tag}", "member": True}
    assert by_email[ghost] == {"email": ghost, "name": None, "member": False}
    assert [o["email"].lower() for o in owners].count(ann) == 1
    assert [o["email"].lower() for o in owners].count(ghost) == 1
    # A suspended member is not an active member, and owns nothing here.
    assert sue not in by_email
    # Nothing of B.
    assert bob not in by_email
    assert f"bsowner.{tag}@x.test" not in by_email
    # By name, then by email. A row with no name sorts after every name.
    assert owners == sorted(owners, key=lambda o: (
        o["name"] is None, (o["name"] or "").lower(), o["email"].lower()))

    # B sees its own member and owner, and none of A's.
    res_b = await _call(ns, _admin(b), "GET", "/crm/owners")
    assert res_b.status_code == 200, res_b.text
    emails_b = {o["email"].lower() for o in res_b.json()["owners"]}
    assert bob in emails_b and f"bsowner.{tag}@x.test" in emails_b
    assert ann not in emails_b and ghost not in emails_b

    # With no settings row the access rule admits admins only (CRM-T5).
    member = _principal(f"m.{tag}@crm-x1.test", a, "feature:crm")
    refused = await _call(ns, member, "GET", "/crm/owners")
    assert refused.status_code == 403, refused.text


# ── 6: the stage name ───────────────────────────────────────────────────────

async def test_the_stage_name_reaches_the_list_the_board_and_the_csv(ns, app_engine) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a = ns.org_a
    await _native(ns, a)
    stages = (await _call(ns, _admin(a), "GET", "/crm/statuses/deal")).json()
    lead_stages = (await _call(ns, _admin(a), "GET", "/crm/statuses/lead")).json()
    stage, lead_stage = stages[1], lead_stages[1]
    await _make(ns, a, "/crm/deals", {"name": f"Deal {tag}", "status_id": stage["id"]})
    await _make(ns, a, "/crm/leads", {"lead_name": f"Lead {tag}", "status_id": lead_stage["id"]})

    deals = await _rows(ns, a, f"/crm/deals?q={tag}")
    assert [r["status_name"] for r in deals] == [stage["name"]]
    leads = await _rows(ns, a, f"/crm/leads?q={tag}")
    assert [r["status_name"] for r in leads] == [lead_stage["name"]]

    board = await _call(ns, _admin(a), "GET", "/crm/pipeline")
    assert board.status_code == 200, board.text
    carded = [
        (lane["status"]["name"], row["status_name"])
        for lane in board.json()["lanes"] for row in lane["rows"]
        if row["name"] == f"Deal {tag}"
    ]
    assert carded == [(stage["name"], stage["name"])]

    for slug, header, name in (
        ("deals", "Stage", stage["name"]), ("leads", "Status", lead_stage["name"]),
    ):
        res = await _call(ns, _admin(a), "GET", f"/crm/export/{slug}.csv?q={tag}")
        assert res.status_code == 200, res.text
        table = list(csv.reader(io.StringIO(res.text.lstrip("﻿"))))
        assert header in table[0], table[0]
        assert table[1][table[0].index(header)] == name


# ── Review round 1: a phone matches on its digits ──────────────────────────

async def test_a_phone_matches_on_its_digits_whatever_the_separators(ns, app_engine) -> None:  # noqa: F811
    """"98450 12345" finds "+91-98450-12345". The text match of done-when 2
    still works, the digits are a second bound parameter, and a query with
    fewer than four digits does not use them."""
    tag = str(uuid.uuid4().int)[:4]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    stored = f"+91-98{tag}-12345"
    typed = f"98{tag} 12345"
    await _make(ns, a, "/crm/contacts", {"first_name": "Dashes", "phone": stored})
    await _make(ns, a, "/crm/contacts", {"first_name": "Mob", "mobile": f"(0) 77{tag} 99"})
    await _make(ns, a, "/crm/leads", {"lead_name": "Dotted", "mobile": f"98.{tag}.12345"})
    await _make(ns, a, "/crm/organizations", {"name": "Slash Co", "phone": f"98/{tag}/12345"})
    await _make(ns, b, "/crm/contacts", {"first_name": "Bee", "phone": stored})

    def names(rows: list[dict], key: str) -> list[str]:
        return sorted(r[key] for r in rows)

    q = typed.replace(" ", "%20")
    assert names(await _rows(ns, a, f"/crm/contacts?q={q}"), "first_name") == ["Dashes"]
    assert names(await _rows(ns, a, f"/crm/contacts?q=77-{tag}-99"), "first_name") == ["Mob"]
    assert names(await _rows(ns, a, f"/crm/leads?q={q}"), "lead_name") == ["Dotted"]
    assert names(await _rows(ns, a, f"/crm/organizations?q={q}"), "name") == ["Slash Co"]
    # The other tenant holds the same number, and finds only its own row.
    assert names(await _rows(ns, b, f"/crm/contacts?q={q}"), "first_name") == ["Bee"]
    # Three digits are too few to match on digits alone.
    assert await _rows(ns, a, "/crm/contacts?q=9-1-2") == []
