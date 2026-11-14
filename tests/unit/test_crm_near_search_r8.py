"""R8 — HRD-18a, a CRM search finds a name as people say it (``chat_failure_modes.md`` F1).

Against a REAL Postgres with ``pg_trgm``, through the real ``/crm`` router, as
the non-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS):

2. ``?q=`` of two or more words: each word must match one search column or
   one related name. AND across the words, OR across the columns.
3. ``?near=true``: when the exact pass gives no row, a word of 4 letters or
   more also matches by ``pg_trgm`` ``strict_word_similarity`` over the same
   columns. The rows of that pass carry ``near: true``. A query with an
   exact hit, a query of more than 3 words, and a list without ``near``
   stay exact.
4. A deal matches the names of its contacts and of its source lead.

The production case (2026-11-14): "Sharath 3D printer" finds the deal
«Neuropeak - Snowflake 3D printer», whose contact is «Sharat Satyanarayana».
On a table of 5,000 leads, "Sharath" finds «Sharat Satyanarayana» first, and
the near pass holds a small share of the table. The test prints the time of
the near read.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_find_and_page_r8 import (  # noqa: F401
    _FIRST_STAGE,
    _TABLES,
    _admin,
    _call,
    _make,
    _native,
    _rows,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


async def _list(p: Any, org: str, path: str) -> dict:
    res = await _call(p, _admin(org), "GET", path)
    assert res.status_code == 200, res.text
    return res.json()


def _link_contact(p: Any, org: str, deal: str, contact: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id, is_primary) "
            "VALUES (CAST(:o AS uuid), CAST(:d AS uuid), CAST(:c AS uuid), true)"),
            {"o": org, "d": deal, "c": contact})


async def _production_case(p: Any, org: str) -> dict[str, str]:
    """The records of the 2026-11-14 case, in one organization."""
    neuropeak = await _make(p, org, "/crm/organizations", {"name": "Bitnib Technologies"})
    sharat = await _make(p, org, "/crm/contacts", {
        "first_name": "Sharat", "last_name": "Satyanarayana", "company_id": neuropeak,
    })
    deal = await _make(p, org, "/crm/deals", {
        "name": "Neuropeak - Snowflake 3D printer", "company_id": neuropeak,
    })
    _link_contact(p, org, deal, sharat)
    # The other Sharath of the CRM, on another deal, as in production.
    bms = await _make(p, org, "/crm/contacts", {
        "first_name": "Sharath", "last_name": "BMS Purchase Dept",
    })
    twin = await _make(p, org, "/crm/deals", {"name": "Twin Dragon 300"})
    _link_contact(p, org, twin, bms)
    await _make(p, org, "/crm/deals", {"name": "Custom Clay 3D Printer"})
    return {"deal": deal, "twin": twin, "sharat": sharat}


# ── 2: each word must match ────────────────────────────────────────────────

async def test_each_word_must_match_one_column(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    await _native(ns, a)
    zephyr = await _make(ns, a, "/crm/organizations", {"name": "Zephyr Robotics"})
    await _make(ns, a, "/crm/deals", {"name": "Spare parts", "company_id": zephyr})
    await _make(ns, a, "/crm/deals", {"name": "Spare motors"})
    await _make(ns, a, "/crm/contacts", {"first_name": "Asha", "last_name": "Rao"})
    await _make(ns, a, "/crm/contacts", {"first_name": "Asha", "last_name": "Iyer"})
    await _make(ns, a, "/crm/leads", {"lead_name": "Kiran Kumar", "organization_name": "Acme"})

    # Two words, two columns: no single column holds "asha rao".
    rows = await _rows(ns, a, "/crm/contacts?q=asha%20rao")
    assert [(r["first_name"], r["last_name"]) for r in rows] == [("Asha", "Rao")]
    # One word in the deal's name, one in its company's name.
    rows = await _rows(ns, a, "/crm/deals?q=zephyr%20spare")
    assert [r["name"] for r in rows] == ["Spare parts"]
    # Each word must match: "spare" alone finds both, "spare zephyr" one.
    assert sorted(r["name"] for r in await _rows(ns, a, "/crm/deals?q=spare")) == [
        "Spare motors", "Spare parts"]
    rows = await _rows(ns, a, "/crm/leads?q=acme%20kiran")
    assert [r["lead_name"] for r in rows] == ["Kiran Kumar"]
    # A word of 2 letters never matches nearly, so "zz" empties the answer,
    # near pass or not.
    assert await _rows(ns, a, "/crm/contacts?q=asha%20zz&near=true") == []


# ── 4: a deal matches its contacts and its source lead ─────────────────────

async def test_a_deal_matches_its_contacts_and_its_source_lead(ns, app_engine) -> None:  # noqa: F811
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    case = await _production_case(ns, a)
    clay = await _make(ns, a, "/crm/deals", {"name": "Clay extruder"})
    lead = await _make(ns, a, "/crm/leads", {"lead_name": "Kavitha Menon"})
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_leads SET converted_deal_id = CAST(:d AS uuid), converted_at = now() "
            "WHERE id = CAST(:l AS uuid)"), {"d": clay, "l": lead})
    named = await _make(ns, a, "/crm/leads", {"lead_name": "Ravindra Joshi"})
    joshi = await _make(ns, a, "/crm/deals", {"name": "Resin order"})
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET lead_id = CAST(:l AS uuid) WHERE id = CAST(:d AS uuid)"),
            {"l": named, "d": joshi})

    rows = await _rows(ns, a, "/crm/deals?q=satyanarayana")
    assert [r["id"] for r in rows] == [case["deal"]]
    # The converted lead's name finds its deal, and the deal's lead_id too.
    assert [r["name"] for r in await _rows(ns, a, "/crm/deals?q=kavitha")] == ["Clay extruder"]
    assert [r["name"] for r in await _rows(ns, a, "/crm/deals?q=joshi")] == ["Resin order"]
    # The contact and the deal name, in one query.
    rows = await _rows(ns, a, "/crm/deals?q=sharat%203D")
    assert [r["id"] for r in rows] == [case["deal"]]

    # The board reads the same predicate.
    board = await _call(ns, _admin(a), "GET", "/crm/pipeline?q=satyanarayana")
    assert board.status_code == 200, board.text
    names = sorted(row["name"] for lane in board.json()["lanes"] for row in lane["rows"])
    assert names == ["Neuropeak - Snowflake 3D printer"]

    # Organization B holds a deal that points at the contact of A. Only row
    # level security inside the sub-select keeps the name out of B's search.
    with ns.admin_engine.begin() as c:
        bee = str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id) "
            f"VALUES (CAST(:o AS uuid), 'Bee deal', {_FIRST_STAGE}) RETURNING id"),
            {"o": b}).scalar_one())
    _link_contact(ns, b, bee, case["sharat"])
    assert await _rows(ns, b, "/crm/deals?q=satyanarayana") == []
    assert await _rows(ns, b, "/crm/deals?q=satyanarayana&near=true") == []


# ── 3: the near pass ────────────────────────────────────────────────────────

async def test_a_near_word_finds_the_record_that_holds_no_exact_word(
    ns, app_engine,  # noqa: F811
) -> None:
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    case = await _production_case(ns, a)
    await _make(ns, a, "/crm/organizations", {"name": "Magnetech Ltd"})
    await _make(ns, a, "/crm/leads", {"lead_name": "Ravi", "organization_name": "Neuropeak"})
    await _make(ns, a, "/crm/contacts", {"first_name": "Anitha", "phone": "+91-98450-12345"})

    # Production: "Sharath 3D printer" holds no deal exactly.
    assert await _rows(ns, a, "/crm/deals?q=Sharath%203D%20printer") == []
    found = await _list(ns, a, "/crm/deals?q=Sharath%203D%20printer&near=true")
    assert [r["id"] for r in found["rows"]] == [case["deal"]]
    assert found["rows"][0]["near"] is True and found["total"] == 1

    # The fixed pairs that SQL reads on the raw text.
    found = await _list(ns, a, "/crm/deals?q=nuropeak&near=true")
    assert [r["id"] for r in found["rows"]] == [case["deal"]]
    assert all(r["near"] is True for r in found["rows"])
    found = await _list(ns, a, "/crm/organizations?q=Magnatech&near=true")
    assert [r["name"] for r in found["rows"]] == ["Magnetech Ltd"]
    found = await _list(ns, a, "/crm/leads?q=neuropeek&near=true")
    assert [r["lead_name"] for r in found["rows"]] == ["Ravi"]

    # A single word with an exact hit behaves as today: exact, no mark.
    found = await _list(ns, a, "/crm/contacts?q=Sharath&near=true")
    assert [r["first_name"] for r in found["rows"]] == ["Sharath"]
    assert "near" not in found["rows"][0]
    # Phone digits still match, near pass or not.
    rows = await _rows(ns, a, "/crm/contacts?q=98450%2012345&near=true")
    assert [r["first_name"] for r in rows] == ["Anitha"]
    assert "near" not in rows[0]
    # More than three words: no near pass.
    assert await _rows(ns, a, "/crm/deals?q=Sharath%203D%20printer%20order&near=true") == []
    # Page 2 gets no near pass.
    assert await _rows(ns, a, "/crm/deals?q=nuropeak&near=true&page=2") == []
    # Another organization never sees a near row of A.
    assert await _rows(ns, b, "/crm/deals?q=Sharath%203D%20printer&near=true") == []
    assert await _rows(ns, b, "/crm/deals?q=nuropeak&near=true") == []


# ── The floor on a table of 5,000 leads ────────────────────────────────────

#: The first lead status of the organization ``:o``.
_FIRST_LEAD_STATUS = (
    "(SELECT id FROM crm_lead_statuses WHERE organization_id = CAST(:o AS uuid) "
    "ORDER BY position LIMIT 1)"
)

_FIRST_TEXT = (
    "Aarav Aditya Akash Amit Anand Anil Anita Anjali Ankit Arjun Arun Asha Ashok "
    "Bharath Chandra Deepak Deepa Dinesh Divya Gaurav Geeta Girish Gopal Hari Harish "
    "Hema Jaya Jyoti Kamal Karan Kavya Kiran Krishna Lakshmi Lalit Madhu Mahesh Manish "
    "Manoj Meena Mohan Mukesh Nandini Naresh Neha Nikhil Nisha Pallavi Pankaj Pooja "
    "Prakash Pradeep Pramod Prasad Praveen Priya Rahul Rajesh Rakesh Ramesh Ravi Rekha "
    "Rohan Rohit Sachin Sagar Sanjay Santosh Sarath Satish Shalini Shweta Sita Sneha "
    "Sudha Sunil Suresh Tarun Uma Usha Varun Veena Venu Vijay Vikas Vinay Vinod Vivek "
    "Yogesh Sohan Impa Dhanasree Prasanna Sruthi Suhas Shreyas Swati Sandeep Sanjeev"
)
_LAST_TEXT = (
    "Rao Iyer Nair Menon Reddy Sharma Verma Gupta Patel Shah Mehta Joshi Kulkarni "
    "Deshpande Pillai Naidu Gowda Hegde Shetty Kamath Bhat Acharya Banerjee Chatterjee "
    "Das Ghosh Bose Sen Mukherjee Dutta Singh Yadav Mishra Pandey Tiwari Dubey Saxena "
    "Agarwal Jain Kapoor Malhotra Khanna Chopra Arora Sethi Bajaj Goyal Mittal Bansal "
    "Garg"
)
_FIRST = _FIRST_TEXT.split()
_LAST = _LAST_TEXT.split()


async def test_sharath_finds_sharat_first_and_floods_no_table(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    await _native(ns, a)
    # 5,000 distinct leads: each first name with a stride of the last names.
    names = [
        f"{_FIRST[i % len(_FIRST)]} {_LAST[(i * 7 + i // len(_FIRST)) % len(_LAST)]}"
        for i in range(5000)
    ]
    assert not any("sharath" in n.lower() for n in names)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, first_name, last_name, "
            "email, organization_name, status_id) "
            "SELECT CAST(:o AS uuid), n, split_part(n, ' ', 1), split_part(n, ' ', 2), "
            "lower(replace(n, ' ', '.')) || '@lead' || g || '.test', 'Company ' || g, "
            f"{_FIRST_LEAD_STATUS} "
            "FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS t(n, g)"),
            {"o": a, "names": names})
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, first_name, last_name, "
            "organization_name, status_id) VALUES (CAST(:o AS uuid), "
            "'Sharat Satyanarayana', 'Sharat', 'Satyanarayana', 'Neuropeak', "
            f"{_FIRST_LEAD_STATUS})"), {"o": a})
        c.execute(text("ANALYZE crm_leads"))

    exact = await _list(ns, a, "/crm/leads?q=Sharath")
    assert exact["rows"] == [] and exact["total"] == 0
    started = time.perf_counter()
    found = await _list(ns, a, "/crm/leads?q=Sharath&near=true&page_size=10")
    elapsed = time.perf_counter() - started
    rows = found["rows"]
    assert rows[0]["lead_name"] == "Sharat Satyanarayana", [r["lead_name"] for r in rows]
    assert len(rows) <= 10 and all(r["near"] is True for r in rows)
    # The share of the table that is near "Sharath" at the floor: the
    # Bharaths, the Saraths and the Sharmas. Each is a near spelling by its
    # trigrams, and the order puts the nearest first.
    share = found["total"] / 5001
    print(
        f"\nHRD-18a near pass: {found['total']} of 5001 leads near 'Sharath' "
        f"({share:.1%}), first «{rows[0]['lead_name']}», "
        f"read in {elapsed * 1000:.0f} ms through the router"
    )
    assert share < 0.05, found["total"]
    assert elapsed < 5.0, elapsed
