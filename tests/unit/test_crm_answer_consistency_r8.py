"""R8 — CRM answers are consistent and fast, through the real router (WS-53 §18.16).

Spec: ``project-docs/specs/crm_platform.md`` §18.16. Against a REAL
Postgres, as the non-privileged role of the phase-4 rehearsal.

The fixture is the shape of the incident of 2026-10-19, with synthetic
values: an Invoices module whose word for a fee can mean three fields, a
region field with three spellings of one value, and the totals of one
month in two years. The tool transport points at the real ``/crm`` router,
so the term route, the aggregate, the question store, the rules and the
facts all run as in production.

The acceptance replay is a fake model's script: the tool calls that a
model makes for "the total of the fee for the region for September", and
nothing else. It must take at most 4 CRM tool calls.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_answer_first_r8 import (  # noqa: F401
    _through_the_crm,
    add_rule,
    af,
    member,
)
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    bound,
    call,
    fills,
    mirror,
    module_row,
    plan,
    q_env,
    records,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

TERM = "IP billing"
QID = "term:Invoices:ip-billing"
RULE = "IP billing means IPF charges"
SEPTEMBER = {"Circle": "Mumbai", "Invoice_Date.from": "2026-09-01",
             "Invoice_Date.to": "2026-09-30"}

#: key, label, type, fill.
FIELDS = [
    ("Name", "Name", "text", 1.0), ("Circle", "Circle", "text", 0.96),
    ("Invoice_Date", "Invoice Date", "date", 1.0),
    ("Item_Type", "Item Type", "select", 0.05),
    ("Total_Product_Amount", "Total Invoice Amount.", "currency", 0.999),
    ("Total_Invoice_Amount", "Total Tax Invoice Amount", "currency", 0.565),
    ("IPF_Charges", "IPF Charges", "currency", 0.5417),
    ("Total_IP_Infra", "Total (IP + Infra)", "number", 0.4954),
    ("Base_IP_Fees", "Base  IP", "currency", 0.4784),
    ("Total_SGST", "Total SGST", "currency", 0.979),
]


def _row(i: int, circle: str, day: str, ipf: int, infra: int, base: int,
         product: int, **more: Any) -> dict[str, Any]:
    return {"Name": f"INV-{i}", "Circle": circle, "Invoice_Date": day, "IPF_Charges": ipf,
            "Total_IP_Infra": infra, "Base_IP_Fees": base, "Total_Product_Amount": product,
            "Total_Invoice_Amount": round(product * 1.18, 2),
            "Total_SGST": round(product * 0.09, 2), **more}


def _rows() -> list[dict[str, Any]]:
    out = [_row(i, "Mumbai", "2026-09-10", 650000, 360000, 300000, 2000000)
           for i in range(10)]
    out[0]["Item_Type"] = "Ip_Rent"
    out.append(_row(10, "MUM", "2026-09-12", 800000, 400000, 100000, 1500000))
    out.append(_row(11, "MUMBAI", "2026-09-20", 642000, 371000, 100000, 1200000))
    # The same month a year before, and another region.
    out.append(_row(12, "Mumbai", "2025-09-15", 5586000, 3202000, 900000, 9000000))
    out.append(_row(13, "Pune", "2026-09-15", 1000000, 500000, 400000, 3000000))
    return out


def seed(p: Any, org: str) -> None:
    mirror(p, org, [module_row("Invoices", custom=False, singular="Invoice")])
    with p.admin_engine.begin() as c:
        for position, (key, label, kind, _fill) in enumerate(FIELDS):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
                "source, provider_api_name, position) VALUES (CAST(:o AS uuid), "
                "'module:Invoices', :k, :l, :t, 'zoho', :k, :pos)"),
                {"o": org, "k": key, "l": label, "t": kind, "pos": position})
    fills(p, org, "Invoices", {k: f for k, _l, _t, f in FIELDS})
    records(p, org, "Invoices", _rows())
    # The mapping as the AI left it: a sparse amount, and the date.
    plan(p, org, [{"kind": "catalogue", "module": "Invoices", "amount": "Base_IP_Fees",
                   "date": "Invoice_Date", "by": {"amount": "ai", "date": "ai"}}])


def _crm_calls(script: list[tuple[str, dict[str, Any]]]) -> int:
    return sum(1 for name, _args in script if name.startswith("crm_") or name == "org_facts")


async def _replay(p: Any, user: Any, monkeypatch: Any,
                  script: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Run a fake model's script of tool calls through the real router, in
    one run with a card channel, as the web chat holds it."""
    from acb_skills import ask_tools, crm_tools

    monkeypatch.setattr(ask_tools, "cards_refused", lambda: False)
    token = crm_tools.open_asks_for_run()
    crm_tools.mark_answer_tool(True)
    outs: list[str] = []
    try:
        for name, args in script:
            fn = getattr(crm_tools, name)
            outs.append(await _through_the_crm(p, user, monkeypatch, lambda f=fn, a=args: f(**a)))
    finally:
        crm_tools.release_asks(token)
    return outs


SCRIPT = [
    ("crm_list_modules", {}),
    ("crm_aggregate", {"entity": "module:Invoices", "term": TERM,
                       "field_filters": SEPTEMBER}),
]


async def test_the_replay_with_no_rule_asks_first_with_the_two_totals(
    af, app_engine, monkeypatch,
) -> None:
    """The acceptance case, unsure: the word means two fields whose totals
    differ, so the chat asks ONE question first, with each total, for
    September of the current year and all three spellings of the region."""
    from acb_common.today import today_line
    from acb_skills import crm_tools

    p = af
    seed(p, p.org_a)
    # The run knows the date, so "September" is September 2026.
    assert "October 2026" in today_line(datetime(2026, 10, 19, 6, tzinfo=UTC))
    outs = await _replay(p, member(p.org_a), monkeypatch, SCRIPT)
    assert _crm_calls(SCRIPT) <= 4
    out = outs[-1]
    assert out.startswith(crm_tools.ASK_FIRST), out
    assert "IPF Charges" in out and "₹79.4 lakh" in out
    assert "Total (IP + Infra)" in out and "₹43.7 lakh" in out
    payload = json.loads(next(ln for ln in out.splitlines() if ln.startswith("{")))
    card = payload["questions"][0]
    assert card["crm_question"] == QID
    assert [o["key"] for o in card["options"]][:2] == ["IPF_Charges", "Total_IP_Infra"]
    # The question is in the store for the admin, as a term.
    listed = await call(p, _user(p.org_a), "GET", "/crm/questions")
    assert listed.status_code == 200, listed.text
    term = next(q for q in listed.json()["shown"] if q["id"] == QID)
    assert term["kind"] == "term" and term["value"] == TERM


async def test_the_replay_with_the_rule_answers_79_42_lakh_for_sep_2026(
    af, app_engine, monkeypatch,
) -> None:
    """The acceptance case, clear: a business rule names the field, so the
    answer comes first: September 2026, IPF Charges, the three spellings."""
    from acb_skills import crm_tools

    p = af
    seed(p, p.org_a)
    saved = await add_rule(p, _user(p.org_a), RULE, "org")
    assert saved.status_code == 200, saved.text
    outs = await _replay(p, member(p.org_a), monkeypatch, SCRIPT)
    out = outs[-1]
    first = out.splitlines()[0]
    assert first.endswith("INR 7,942,000 across 12 matching records."), out
    assert crm_tools.ASK_FIRST not in out
    note = next(ln for ln in out.splitlines() if ln.startswith(crm_tools.SHOW_NOTE))
    assert '"IP billing" reads IPF Charges (IPF_Charges), because a business rule names it.' \
        in note
    assert "Circle is Mumbai (counted Mumbai, MUM and MUMBAI)" in note
    assert "Invoice_Date from 2026-09-01 to 2026-09-30" in note
    assert "₹43.7 lakh" in note  # the one other total
    assert _crm_calls(SCRIPT) <= 4


async def test_a_member_s_pick_answers_them_and_the_next_member_is_not_asked(
    af, app_engine, monkeypatch,
) -> None:
    from acb_skills import crm_tools

    p = af
    seed(p, p.org_a)
    first, second = member(p.org_a, 1), member(p.org_a, 2)
    await _replay(p, first, monkeypatch, SCRIPT)
    picked = await call(p, first, "POST", f"/crm/questions/{QID}/reply",
                        {"choice": "IPF_Charges"})
    assert picked.status_code == 200 and picked.json()["result"] == "proposal", picked.text
    for who, by in ((first, "me"), (second, "member")):
        got = await call(p, who, "GET",
                         "/crm/terms/resolve?entity=module:Invoices&term=IP%20billing")
        assert got.status_code == 200, got.text
        assert (got.json()["by"], got.json()["field"]) == (by, "IPF_Charges")
    outs = await _replay(p, second, monkeypatch, SCRIPT)
    assert outs[-1].splitlines()[0].endswith("INR 7,942,000 across 12 matching records.")
    assert crm_tools.ASK_FIRST not in outs[-1]


async def test_an_admin_answer_decides_for_everyone_and_another_org_sees_nothing(
    af, app_engine, monkeypatch,
) -> None:
    p = af
    seed(p, p.org_a)
    seed(p, p.org_b)
    await _replay(p, member(p.org_a), monkeypatch, SCRIPT)
    done = await call(p, _user(p.org_a), "POST", f"/crm/questions/{QID}/answer",
                      {"value": "Total_IP_Infra"})
    assert done.status_code == 200, done.text
    got = await call(p, member(p.org_a, 3), "GET",
                     "/crm/terms/resolve?entity=module:Invoices&term=ip%20BILLING")
    assert (got.json()["by"], got.json()["field"]) == ("admin", "Total_IP_Infra")
    other = await call(p, member(p.org_b), "GET",
                       "/crm/terms/resolve?entity=module:Invoices&term=IP%20billing")
    assert other.json()["by"] is None
    # A bad value of a term is refused, and writes nothing.
    bad = await call(p, _user(p.org_a), "POST", f"/crm/questions/{QID}/reply",
                     {"choice": "Total_SGST"})
    assert bad.status_code == 422


async def test_a_text_filter_counts_each_spelling_and_seeds_the_alias_fact(
    af, app_engine,
) -> None:
    p = af
    seed(p, p.org_a)
    got = await call(p, member(p.org_a), "GET",
                     "/crm/aggregate?entity=module:Invoices&measure=sum&field=IPF_Charges"
                     "&field.Circle=mumbai&field.Invoice_Date.from=2026-09-01"
                     "&field.Invoice_Date.to=2026-09-30")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["total"] == 7942000 and body["records"] == 12
    assert body["matched"] == {"Circle": ["Mumbai", "MUM", "MUMBAI"]}
    # The year before stays out, and so does another region.
    year_before = await call(p, member(p.org_a), "GET",
                             "/crm/aggregate?entity=module:Invoices&measure=sum"
                             "&field=IPF_Charges&field.Circle=Mumbai"
                             "&field.Invoice_Date.from=2025-09-01"
                             "&field.Invoice_Date.to=2025-09-30")
    assert year_before.json()["total"] == 5586000
    facts = await call(p, member(p.org_a), "GET", "/org/facts?app=crm")
    if facts.status_code == 404:  # the org-facts route is not on the CRM router
        from gateway.routes.crm.facts import crm_facts

        rows = await bound(p, p.org_a, lambda db: crm_facts(db, member(p.org_a)))
        meanings = [r.meaning for r in rows]
    else:
        meanings = [r["meaning"] for r in facts.json()["facts"]]
    assert any('Circle "Mumbai" also reads as MUM and MUMBAI' in m for m in meanings), meanings


async def test_the_term_fact_reaches_the_facts(af, app_engine, monkeypatch) -> None:
    from gateway.routes.crm.facts import crm_facts

    p = af
    seed(p, p.org_a)
    await _replay(p, member(p.org_a), monkeypatch, SCRIPT)
    rows = await bound(p, p.org_a, lambda db: crm_facts(db, member(p.org_a)))
    term = next(r for r in rows if r.subject == f"term:{TERM}")
    assert "may mean IPF Charges, Total (IP + Infra) and Base  IP" in term.meaning


async def test_the_evidence_moves_the_sparse_amount_to_the_ex_gst_field(af, app_engine) -> None:
    """Part 5: the amount pick of the pipeline reads the arithmetic (the
    tax total is the amount times 1.18), and prefers the most filled field
    that excludes GST. The admin's answer would stay: ``merge_item``."""
    from gateway.crm_sync import catalogue as cat
    from gateway.crm_sync.map_pipeline import DbSource
    from gateway.routes.crm.questions import load

    p = af
    seed(p, p.org_a)
    inputs = await bound(p, p.org_a, lambda db: load(db, p.org_a, full=False))
    src = inputs.sources["Invoices"]

    async def evidence(db: Any) -> Any:
        from gateway.routes.crm.questions import module_evidence

        return await module_evidence(db, p.org_a, src, cat.amount_evidence_fields(src))

    found = await bound(p, p.org_a, evidence)
    assert "excludes_gst" in found["Total_Product_Amount"]
    assert "includes_gst" in found["Total_Invoice_Amount"]
    assert cat.code_amount_ex_gst(src, found).key == "Total_Product_Amount"
    # The pipeline's own read gives the same, in its own tenant session.
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    scope = clear_tenant()
    token = bind_tenant(p.org_a)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            again = await DbSource(p.org_a).amount_evidence(src)
    finally:
        release_tenant(token)
        release_tenant(scope)
    assert again == found
