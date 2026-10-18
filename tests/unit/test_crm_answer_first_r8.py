"""R8 — answer first, clarify over time, through the real router (WS-53 CRM-Q33).

Spec: ``project-docs/specs/crm_platform.md`` §18.15. Against a REAL
Postgres, as the non-privileged role of the phase-4 rehearsal, with the seed
of ``test_crm_questions_r8.py``. The seed is DEWiN's Collection with
synthetic values: each record is "IP & Rent" or "EB".

* Part 5: one bounded statement finds which field includes GST and which is
  after TDS. With the rule "net of GST", the card offers the three ex-GST
  fields, and the assumption is their sum, read in one aggregate.
* Part 3: the rules of the org and of each member, their authority and
  their tenancy, the hold and the confirm, and the facts ``rule:<slug>``.
* Part 4: who set an answer and when, and a member's own choice and its
  clear.
* Part 2: the pending ask of WhatsApp, built by the server, and a reply
  that records through the same door as the web pick.
* The migration: found by content, FORCE row level security, its checks.

The Redis of a hold is a fake under the real tenant wrapper, so each key
still carries the tenant prefix.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._crm_redis_fake import RawRedis, tenant_client
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_questions_answer_r8 import item_of, versions
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    call,
    cursor_state,
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

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
QID = "key:Payment_Received:amount"
NET_RULE = "All numbers I want to see should be net of GST."
SUM_KEY = "IP+Rent+EB_Total_Amount"

#: DEWiN's Collection: key, label, type, fill.
FIELDS = [
    ("Name", "Name", "text", 1.0), ("Payment_Type", "Payment Type", "select", 1.0),
    ("IP", "IP Basic Amount", "currency", 0.5), ("Rent", "Rent Basic Amount", "currency", 0.5),
    ("EB_Total_Amount", "EB Basic Amount", "currency", 0.45),
    ("IP_Total_Amount", "IP (Basic + GST)", "currency", 0.5),
    ("Any_other_Deduction", "IP GST", "currency", 0.5),
    ("Rent_GST", "Rent GST", "currency", 0.5), ("Rent_TDS", "Rent TDS", "currency", 0.5),
    ("Billing_Amount", "Rent Net Amount", "currency", 0.5),
    ("EB_GST", "EB GST", "currency", 0.45), ("EB_TDS", "EB TDS", "currency", 0.45),
    ("EB_Net_Amount", "EB Net Amount", "currency", 0.45),
]


def member(org: str, n: int = 1) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=f"member{n}@crm-q33.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:crm"]), organization_id=org)


def _rows() -> list[dict]:
    out = []
    for i in range(10):
        ip, rent = 1000 + 10 * i, 2000 + 10 * i
        r_gst, r_tds = round(rent * 0.18, 2), round(rent * 0.10, 2)
        out.append({"Name": f"P-{i}", "Payment_Type": "IP & Rent", "IP": ip, "Rent": rent,
                    "IP_Total_Amount": round(ip * 1.18, 2),
                    "Any_other_Deduction": round(ip * 0.18, 2), "Rent_GST": r_gst,
                    "Rent_TDS": r_tds, "Billing_Amount": round(rent + r_gst - r_tds, 2)})
    for i in range(9):
        eb = 500 + 5 * i
        e_gst, e_tds = round(eb * 0.18, 2), round(eb * 0.02, 2)
        out.append({"Name": f"E-{i}", "Payment_Type": "EB", "EB_Total_Amount": eb,
                    "EB_GST": e_gst, "EB_TDS": e_tds,
                    "EB_Net_Amount": round(eb + e_gst - e_tds, 2)})
    return out


def seed_dewin(p: Any, org: str) -> None:
    mirror(p, org, [module_row("Payment_Received", "Collection")])
    with p.admin_engine.begin() as c:
        for position, (key, label, kind, _fill) in enumerate(FIELDS):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
                "source, provider_api_name, position) VALUES (CAST(:o AS uuid), "
                "'module:Payment_Received', :k, :l, :t, 'zoho', :k, :pos)"),
                {"o": org, "k": key, "l": label, "t": kind, "pos": position})
    fills(p, org, "Payment_Received", {k: f for k, _l, _t, f in FIELDS})
    records(p, org, "Payment_Received", _rows())
    # The AI was not sure, and ranked Rent Net Amount first (the real case).
    plan(p, org, [{"kind": "catalogue", "module": "Payment_Received", "purpose": "payment_in",
                   "amount": None, "by": {"purpose": "admin", "amount": "ai"},
                   "ranked": {"amount": ["Billing_Amount", "EB_Net_Amount", "IP"]}}])


@pytest.fixture
def af(q_env, monkeypatch):
    """The answer-first list on, the fake Redis of the holds, the rules and
    the WhatsApp asks, and a clean table after."""
    from acb_common import get_settings
    from gateway.routes.crm import questions, rules, text_asks

    monkeypatch.setattr(get_settings(), "crm_answer_first_orgs", "*", raising=False)
    raw = RawRedis()
    for module in (questions, rules, text_asks):
        monkeypatch.setattr(module, "_redis", lambda: tenant_client(raw))
    q_env.redis = raw
    yield q_env
    with q_env.admin_engine.begin() as c:
        for org in (q_env.org_a, q_env.org_b):
            for table in ("crm_business_rules", "people"):
                c.execute(text(f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                          {"o": org})


async def ask(p: Any, user: Any, ids: str = QID, *, everything: bool = False) -> dict:
    got = await call(p, user, "GET", f"/crm/questions/ask?ids={ids}"
                     + ("&all=1" if everything else ""))
    assert got.status_code == 200, got.text
    return got.json()["questions"][0]


async def add_rule(p: Any, user: Any, body: str, scope: str = "me") -> Any:
    return await call(p, user, "POST", "/crm/rules", {"body": body, "scope": scope})


def rule_rows(p: Any, org: str) -> list[tuple]:
    with p.admin_engine.begin() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT scope, member_email, slug, body, origin FROM crm_business_rules "
            "WHERE organization_id = CAST(:o AS uuid) ORDER BY created_at, id"), {"o": org})]


# ── Part 5: the evidence, and the steer of a rule ───────────────────────────


async def test_with_no_rule_each_money_option_carries_its_evidence(af, app_engine) -> None:
    p = af
    seed_dewin(p, p.org_a)
    q = await ask(p, member(p.org_a))
    by = {c["value"]: c for c in q["choices"]}
    assert list(by) == ["Billing_Amount", "EB_Net_Amount", "IP"]
    assert by["Billing_Amount"]["best"] is True
    assert by["Billing_Amount"]["evidence"] == ["Includes GST", "After TDS"]
    assert by["EB_Net_Amount"]["evidence"] == ["Includes GST", "After TDS"]
    assert by["IP"]["evidence"] == ["Excludes GST"]
    assert q["answer_first"] is True and q["suggested_sum"] == []
    assert q["assume"]["fields"] == ["Billing_Amount"]
    assert q["assume"]["how"] == "Rent Net Amount (includes GST, after TDS)"


async def test_dewin_with_net_of_gst_recommends_the_sum_of_the_three_ex_gst_fields(
    af, app_engine,
) -> None:
    """The acceptance example of the owner (2026-10-18): "All numbers I want
    to see should be net of GST". The amount is IP + Rent + EB Basic."""
    p = af
    seed_dewin(p, p.org_a)
    saved = await add_rule(p, _user(p.org_a), NET_RULE, "org")
    assert saved.status_code == 200, saved.text
    q = await ask(p, member(p.org_a))
    assert [c["value"] for c in q["choices"]] == ["IP", "Rent", "EB_Total_Amount"]
    assert [c["best"] for c in q["choices"]] == [True, False, False]
    assert {tuple(c["evidence"]) for c in q["choices"]} == {("Excludes GST",)}
    assert q["suggested_sum"] == ["IP", "Rent", "EB_Total_Amount"]
    assume = q["assume"]
    assert assume["fields"] == ["IP", "Rent", "EB_Total_Amount"]
    assert assume["why"] == f'the business rule "{NET_RULE}"'
    assert assume["alternative"]["field"] == "Billing_Amount"
    # The admin page shows the same steer and evidence.
    page = await call(p, _user(p.org_a), "GET", "/crm/questions")
    shown = next(x for x in page.json()["shown"] if x["id"] == QID)
    assert shown["suggested_sum"] == ["IP", "Rent", "EB_Total_Amount"]
    rent_net = next(o for o in shown["options"] if o["value"] == "Billing_Amount")
    assert rent_net["evidence"] == ["Includes GST", "After TDS"]


async def test_the_assumed_sum_reads_in_one_aggregate_and_is_the_sum_of_the_parts(
    af, app_engine,
) -> None:
    p = af
    seed_dewin(p, p.org_a)
    path = (f"/crm/aggregate?entity=module:Payment_Received&measure=sum&field="
            f"{quote(SUM_KEY, safe='')}")
    got = await call(p, member(p.org_a), "GET", path)
    assert got.status_code == 200, got.text
    want = sum(r.get("IP", 0) + r.get("Rent", 0) + r.get("EB_Total_Amount", 0) for r in _rows())
    assert got.json()["total"] == pytest.approx(want)
    assert got.json()["records"] == len(_rows())
    # A part that is no currency field, or a key word, is no sum.
    for bad in ("IP+Name", "IP+@amount", "IP+IP", "IP"):
        res = await call(p, member(p.org_a), "GET",
                         f"/crm/aggregate?entity=module:Payment_Received&measure=sum&field="
                         f"{quote(bad, safe='')}")
        assert (res.status_code == 422) is (bad != "IP"), (bad, res.text)


async def test_off_the_list_nothing_of_q33_shows(af, app_engine, monkeypatch) -> None:
    from acb_common import get_settings

    p = af
    seed_dewin(p, p.org_a)
    monkeypatch.setattr(get_settings(), "crm_answer_first_orgs", "", raising=False)
    q = await ask(p, member(p.org_a))
    assert q["answer_first"] is False and q["assume"] is None
    assert all(c["evidence"] == [] for c in q["choices"])
    off = await call(p, member(p.org_a), "GET", "/crm/rules")
    assert off.status_code == 404 and off.json()["detail"]["code"] == "rules_off"
    mine = await call(p, member(p.org_a), "GET", "/crm/questions/mine")
    assert mine.status_code == 404


# ── Part 3: the rules ───────────────────────────────────────────────────────


async def test_the_authority_and_the_privacy_of_the_rules(af, app_engine) -> None:
    p = af
    seed_dewin(p, p.org_a)
    seed_dewin(p, p.org_b)
    admin, m1, m2 = _user(p.org_a), member(p.org_a, 1), member(p.org_a, 2)
    # A member cannot set a rule for everyone.
    refused = await add_rule(p, m1, NET_RULE, "org")
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "rule_admin_only"
    org_rule = (await add_rule(p, admin, NET_RULE, "org")).json()
    own = (await add_rule(p, m1, "IP is our real earning.")).json()
    assert own["scope"] == "me" and own["editable"] is True
    # Member 2 reads the org rule, and never member 1's rule.
    seen = (await call(p, m2, "GET", "/crm/rules")).json()
    assert [(r["scope"], r["body"]) for r in seen["rules"]] == [("org", NET_RULE)]
    assert seen["rules"][0]["editable"] is False and seen["can_admin"] is False
    assert (await call(p, m2, "PATCH", f"/crm/rules/{own['id']}", {"body": "x"})).status_code \
        == 404
    assert (await call(p, m2, "DELETE", f"/crm/rules/{own['id']}")).status_code == 404
    assert (await call(p, m1, "PATCH", f"/crm/rules/{org_rule['id']}",
                       {"body": "x"})).status_code == 403
    # Org B reads none of org A, also through the id.
    other = _user(p.org_b)
    assert (await call(p, other, "GET", "/crm/rules")).json()["rules"] == []
    assert (await call(p, other, "DELETE", f"/crm/rules/{org_rule['id']}")).status_code == 404
    # The owner changes and removes; the slug stays.
    changed = await call(p, m1, "PATCH", f"/crm/rules/{own['id']}",
                         {"body": "IP is our only real earning."})
    assert changed.status_code == 200 and changed.json()["slug"] == own["slug"]
    assert (await call(p, m1, "DELETE", f"/crm/rules/{own['id']}")).status_code == 200
    assert [r[0] for r in rule_rows(p, p.org_a)] == ["org"]
    assert rule_rows(p, p.org_b) == []
    # A rule with a hidden mark, or too long, is no rule.
    for bad in ("x‮", "y" * 301, "   "):
        assert (await add_rule(p, m1, bad)).status_code == 422


async def test_a_held_rule_saves_nothing_until_the_member_confirms_it(af, app_engine) -> None:
    p = af
    seed_dewin(p, p.org_a)
    m1, m2 = member(p.org_a, 1), member(p.org_a, 2)
    held = await call(p, m1, "POST", "/crm/rules/propose",
                      {"body": "All amounts net of GST", "scope": "me"})
    assert held.status_code == 200, held.text
    body = held.json()
    assert body["id"] == f"rule:{body['token']}" and rule_rows(p, p.org_a) == []
    assert [o["key"] for o in body["options"]] == ["save", "skip"]
    # Another member cannot confirm it: the hold is keyed by the member.
    other = await call(p, m2, "POST", f"/crm/rules/pending/{body['id']}", {"choice": "save"})
    assert other.status_code == 409 and rule_rows(p, p.org_a) == []
    done = await call(p, m1, "POST", f"/crm/rules/pending/{body['id']}", {"choice": "save"})
    assert done.status_code == 200 and done.json()["result"] == "saved"
    assert rule_rows(p, p.org_a) == [("member", "member1@crm-q33.test", "all-amounts-net-of-gst",
                                      "All amounts net of GST", "chat")]
    # One use: a second confirm finds nothing.
    again = await call(p, m1, "POST", f"/crm/rules/pending/{body['id']}", {"choice": "save"})
    assert again.status_code == 409
    # A member's hold of an org rule is refused before it is held.
    org_try = await call(p, m1, "POST", "/crm/rules/propose",
                         {"body": "All amounts net of GST", "scope": "org"})
    assert org_try.status_code == 403
    # Skip drops the hold and saves nothing.
    skip = (await call(p, m1, "POST", "/crm/rules/propose",
                       {"body": "Show EB apart", "scope": "me"})).json()
    out = await call(p, m1, "POST", f"/crm/rules/pending/{skip['id']}", {"choice": "skip"})
    assert out.json()["result"] == "skipped" and len(rule_rows(p, p.org_a)) == 1


async def test_the_rules_reach_the_org_facts_of_each_member(af, app_engine) -> None:
    from tests.unit.test_org_facts_r8 import facts_of

    p = af
    seed_dewin(p, p.org_a)
    await add_rule(p, _user(p.org_a), NET_RULE, "org")
    await add_rule(p, member(p.org_a, 1), "IP is our real earning.")
    one = (await facts_of(p, member(p.org_a, 1))).json()["facts"]
    two = (await facts_of(p, member(p.org_a, 2))).json()["facts"]
    rules_one = {f["subject"]: f for f in one if f["key"] == "rule"}
    assert set(rules_one) == {"rule:all-numbers-i-want-to-see-should-be-net",
                              "rule:ip-is-our-real-earning"}
    assert rules_one["rule:ip-is-our-real-earning"]["set_by"] == "you"
    assert [f["subject"] for f in two if f["key"] == "rule"] == [
        "rule:all-numbers-i-want-to-see-should-be-net"]
    assert all("@" not in str(f["set_by"]) for f in one)


# ── Part 4: who and when, and a member's own choice ─────────────────────────


async def test_an_admin_answer_shows_who_set_it_and_when(af, app_engine) -> None:
    from tests.unit.test_crm_questions_chat_r8 import people

    p = af
    seed_dewin(p, p.org_a)
    people(p, p.org_a, [("Asha Admin", "admin@crm-q32-r8.test")])
    got = await call(p, _user(p.org_a), "POST", f"/crm/questions/{QID}/answer", {"value": "IP"})
    assert got.status_code == 200, got.text
    page = (await call(p, _user(p.org_a), "GET", "/crm/questions?all=1")).json()
    done = next(x for x in page["answered"] if x["id"] == QID)
    assert done["set_by"] == "Asha Admin" and done["set_at"]
    assert "@" not in done["set_by"]


async def test_a_member_lists_and_clears_only_their_own_choice(af, app_engine) -> None:
    p = af
    seed_dewin(p, p.org_a)
    m1, m2 = member(p.org_a, 1), member(p.org_a, 2)
    before = versions(p, p.org_a)
    for who, value in ((m1, "IP"), (m2, "Rent")):
        got = await call(p, who, "POST", f"/crm/questions/{QID}/reply", {"choice": value})
        assert got.status_code == 200 and got.json()["result"] == "proposal", got.text
    mine = (await call(p, m1, "GET", "/crm/questions/mine")).json()["choices"]
    assert [(c["id"], c["value"]) for c in mine] == [(QID, "IP")]
    cleared = await call(p, m1, "DELETE", f"/crm/questions/{QID}/mine")
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True
    assert (await call(p, m1, "GET", "/crm/questions/mine")).json()["choices"] == []
    left = cursor_state(p, p.org_a, "zoho:questions")["proposals"][QID]
    assert [x["member"] for x in left] == ["member2@crm-q33.test"]
    assert versions(p, p.org_a) == before


# ── Part 2: WhatsApp, with the server's own pending ask ─────────────────────


async def _in_org(p: Any, org: str, fn: Any) -> Any:
    """*fn()* with the tenant of *org* bound, as the WhatsApp run binds the
    org of the phone's link, on the app role."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    scope = clear_tenant()
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await fn()
    finally:
        release_tenant(token)
        release_tenant(scope)


def _as(monkeypatch, user: Any) -> None:
    import acb_auth.deps as deps

    async def member_context(email: str) -> Any:
        assert email == user.email
        return user

    monkeypatch.setattr(deps, "member_context", member_context)


async def test_a_whatsapp_reply_records_a_member_s_proposal_and_writes_no_plan(
    af, app_engine, monkeypatch,
) -> None:
    from gateway.routes.crm import text_asks

    p = af
    seed_dewin(p, p.org_a)
    await add_rule(p, _user(p.org_a), NET_RULE, "org")
    m1 = member(p.org_a)
    _as(monkeypatch, m1)
    before = versions(p, p.org_a)
    offered = await _in_org(p, p.org_a, lambda: text_asks.offer(
        p.org_a, m1.email, "whatsapp", {"kind": "question", "id": QID}))
    assert "1. IP Basic Amount (excludes GST)" in offered
    assert "3. EB Basic Amount (excludes GST)" in offered and "Reply 1, 2, 3 or 4." in offered
    (name, (ttl, stored)), = p.redis.store.items()
    assert name == f"cc:{p.org_a}:crm_text_ask:member1@crm-q33.test:whatsapp"
    assert ttl == 24 * 3600 and json.loads(stored)["options"][1] == [
        "Rent", "Rent Basic Amount (excludes GST)"]
    out = await _in_org(p, p.org_a, lambda: text_asks.settle(
        p.org_a, m1.email, "whatsapp", "2"))
    assert out.startswith("Thanks. This uses Rent Basic Amount, as you chose.")
    assert versions(p, p.org_a) == before
    props = cursor_state(p, p.org_a, "zoho:questions")["proposals"][QID]
    assert [(x["member"], x["value"]) for x in props] == [("member1@crm-q33.test", "Rent")]
    assert p.redis.store == {}


async def test_a_whatsapp_reply_of_an_admin_writes_the_answer(af, app_engine,
                                                              monkeypatch) -> None:
    from gateway.routes.crm import text_asks

    p = af
    seed_dewin(p, p.org_a)
    admin = _user(p.org_a)
    _as(monkeypatch, admin)
    await _in_org(p, p.org_a, lambda: text_asks.offer(
        p.org_a, admin.email, "whatsapp", {"kind": "question", "id": QID}))
    out = await _in_org(p, p.org_a, lambda: text_asks.settle(
        p.org_a, admin.email, "whatsapp", "IP Basic Amount"))
    assert out == "Thanks. Totals of Collection now use IP Basic Amount. Ask me again, and I use it."
    assert item_of(p, p.org_a, "Payment_Received")["amount"] == "IP"


async def test_a_whatsapp_yes_saves_the_held_rule_and_nothing_else_does(
    af, app_engine, monkeypatch,
) -> None:
    from gateway.routes.crm import rules, text_asks

    p = af
    seed_dewin(p, p.org_a)
    m1 = member(p.org_a)
    _as(monkeypatch, m1)

    async def hold() -> Any:
        return await rules.propose(p.org_a, m1, "All amounts net of GST", "me")

    held = await _in_org(p, p.org_a, hold)
    text_ = await _in_org(p, p.org_a, lambda: text_asks.offer(
        p.org_a, m1.email, "whatsapp", {"kind": "rule", "id": held.token}))
    assert text_.endswith("Reply YES to save it, or NO.")
    # A reply that is no YES saves nothing, and goes to the chat.
    assert await _in_org(p, p.org_a, lambda: text_asks.settle(
        p.org_a, m1.email, "whatsapp", "maybe later")) is None
    assert rule_rows(p, p.org_a) == []
    # Hold again, and YES saves it, as WhatsApp.
    held = await _in_org(p, p.org_a, hold)
    await _in_org(p, p.org_a, lambda: text_asks.offer(
        p.org_a, m1.email, "whatsapp", {"kind": "rule", "id": held.token}))
    out = await _in_org(p, p.org_a, lambda: text_asks.settle(
        p.org_a, m1.email, "whatsapp", "YES"))
    assert out == 'Saved for you: "All amounts net of GST"'
    assert [r[-1] for r in rule_rows(p, p.org_a)] == ["whatsapp"]


async def test_a_pending_ask_of_org_a_is_never_settled_in_org_b(af, app_engine,
                                                                monkeypatch) -> None:
    from gateway.routes.crm import text_asks

    p = af
    seed_dewin(p, p.org_a)
    seed_dewin(p, p.org_b)
    m1 = member(p.org_a)
    _as(monkeypatch, m1)
    await _in_org(p, p.org_a, lambda: text_asks.offer(
        p.org_a, m1.email, "whatsapp", {"kind": "question", "id": QID}))
    assert await _in_org(p, p.org_b, lambda: text_asks.settle(
        p.org_b, m1.email, "whatsapp", "2")) is None
    assert len(p.redis.store) == 1
    assert (cursor_state(p, p.org_b, "zoho:questions") or {}).get("proposals") is None


# ── The migration ───────────────────────────────────────────────────────────


def test_the_migration_is_found_by_content_and_forces_row_level_security(af) -> None:
    p = af
    found = [f.name for f in sorted(MIGRATIONS.glob("[0-9]*.sql"))
             if "CREATE TABLE IF NOT EXISTS crm_business_rules" in f.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    with p.admin_engine.begin() as c:
        assert c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass('public.crm_business_rules')")).all() == [(True, True)]
        bad = [
            ("member", None, "a-b", "x", "chat"),          # a member rule with no member
            ("org", "a@x.test", "a-b", "x", "chat"),        # an org rule with a member
            ("org", None, "A B", "x", "chat"),              # a slug of the wrong shape
            ("org", None, "a-b", "", "chat"),               # an empty body
            ("org", None, "a-b", "x", "email"),             # an unknown origin
        ]
        from sqlalchemy.exc import IntegrityError

        for scope, email, slug, body, origin in bad:
            with pytest.raises(IntegrityError), c.begin_nested():
                c.execute(text(
                    "INSERT INTO crm_business_rules (organization_id, scope, member_email, "
                    "slug, body, origin, created_by, updated_by) VALUES (CAST(:o AS uuid), "
                    ":s, :m, :g, :b, :r, 'x', 'x')"),
                    {"o": p.org_a, "s": scope, "m": email, "g": slug, "b": body,
                     "r": origin})


# ── Part 1, end to end: the tool over the real router ───────────────────────


async def _through_the_crm(p: Any, user: Any, monkeypatch: Any, fn: Any) -> Any:
    """Run *fn* with the tool transport pointed at the real ``/crm`` router,
    as *user*, on the app role."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from acb_skills import crm_tools
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> Any:
        return real(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")

    monkeypatch.setattr(crm_tools.httpx, "AsyncClient", client)
    monkeypatch.setattr(crm_tools, "_current_user_email", lambda: user.email)
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await fn()
    finally:
        release_tenant(scope)
        monkeypatch.setattr(crm_tools.httpx, "AsyncClient", real)


async def test_the_chat_answers_dewin_first_with_the_note_and_one_question(
    af, app_engine, monkeypatch,
) -> None:
    """The real case: a member asks for the money collected. The answer
    comes first, on IP + Rent + EB Basic (the rule "net of GST"), with how
    it got the number and the figure of Rent Net Amount, then one card."""
    from acb_skills import crm_tools

    p = af
    seed_dewin(p, p.org_a)
    await add_rule(p, _user(p.org_a), NET_RULE, "org")
    token = crm_tools.open_asks_for_run()
    crm_tools.mark_answer_tool(True)
    try:
        out = await _through_the_crm(p, member(p.org_a), monkeypatch, lambda: (
            crm_tools.crm_aggregate("module:Payment_Received", measure="sum_amount")))
    finally:
        crm_tools.release_asks(token)
    rows = _rows()
    ex_gst = sum(r.get("IP", 0) + r.get("Rent", 0) + r.get("EB_Total_Amount", 0) for r in rows)
    rent_net = sum(r.get("Billing_Amount", 0) for r in rows)
    assert out.splitlines()[0].endswith(
        f"INR {ex_gst:,.0f} across {len(rows)} matching records."), out
    assert f'This assumes the business rule "{NET_RULE}".' in out
    assert (f"With Rent Net Amount (includes GST, after TDS) instead, the total is "
            f"{crm_tools.lakh_crore(rent_net)}.") in out
    assert out.count(crm_tools.FOLLOW_UP) == 1 and crm_tools.CLARIFY not in out
    assert out.index(crm_tools.SHOW_NOTE) < out.index(crm_tools.FOLLOW_UP)


# ── Fix round 1 (P2): the member purge reaches the CRM ──────────────────────


async def test_the_purge_takes_a_member_out_of_the_crm(af, app_engine) -> None:
    """The member's own rules go (the delete list of the purge). The org
    rule that the member wrote stays, with no address. In ``zoho:questions``
    the member's proposals go, and the record of who set an answer keeps
    its time and loses the address. Another member is untouched."""
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params
    from gateway.routes.crm import questions, rules

    from tests.unit.test_crm_questions_r8 import bound

    p = af
    seed_dewin(p, p.org_a)
    admin, m1, m2 = _user(p.org_a), member(p.org_a, 1), member(p.org_a, 2)
    assert (await add_rule(p, m1, NET_RULE, "me")).status_code == 200
    assert (await add_rule(p, m2, "IP is our real earning.", "me")).status_code == 200
    assert (await add_rule(p, admin, "All amounts net of GST", "org")).status_code == 200
    for who, value in ((m1, "IP"), (m2, "Rent")):
        got = await call(p, who, "POST", f"/crm/questions/{QID}/reply", {"choice": value})
        assert got.status_code == 200, got.text
    assert (await call(p, admin, "POST", "/crm/questions/revenue/answer",
                       {"value": "payment_in"})).status_code == 200
    rows = next(r for r in _PURGE_DELETES if r.table == "crm_business_rules")

    async def purge(email: str):
        async def run(db):
            await db.execute(text(rows.delete_sql), _purge_params(rows, uid="", email=email))
            changed = await rules.scrub_member(db, p.org_a, email)
            return changed, await questions.purge_member_state(db, p.org_a, email)
        return await bound(p, p.org_a, run)

    assert await purge(m1.email) == (0, 1)
    assert await purge(admin.email) == (1, 1)
    left = rule_rows(p, p.org_a)
    assert ("member", "member1@crm-q33.test") not in [(r[0], r[1]) for r in left]
    assert ("member", "member2@crm-q33.test") in [(r[0], r[1]) for r in left]
    with p.admin_engine.begin() as c:
        by = c.execute(text(
            "SELECT created_by, updated_by FROM crm_business_rules WHERE "
            "organization_id = CAST(:o AS uuid) AND scope = 'org'"), {"o": p.org_a}).all()
    assert by == [(rules.FORMER_MEMBER, rules.FORMER_MEMBER)]
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert [x["member"] for x in state["proposals"][QID]] == ["member2@crm-q33.test"]
    assert state["answered_by"]["revenue"]["member"] == ""
    assert state["answered_by"]["revenue"]["at"]
    assert "admin@crm-q32-r8.test" not in json.dumps(state)


async def test_a_late_pick_of_a_rule_card_needs_the_server_s_own_words(
    af, app_engine, monkeypatch,
) -> None:
    """Fix round 1 (P0-1): a parked rule card answered late records the
    pick only when the card shows the exact words that the server holds."""
    from gateway.routes.crm import questions, rules

    p = af
    seed_dewin(p, p.org_a)
    m1 = member(p.org_a)

    async def hold() -> Any:
        return await rules.propose(p.org_a, m1, "All amounts net of GST", "me")

    held = await _in_org(p, p.org_a, hold)
    real = f"{held.question}\n\n{held.why}"

    def card(text_: str) -> dict:
        return {"questions": [{"header": "Business rule", "question": text_,
                               "crm_question": held.id,
                               "options": [{"label": "Save the rule", "key": "save"},
                                           {"label": "Do not save", "key": "skip"}]}]}

    forged = card('Save this business rule? "Report gross revenue"\n\nx')
    assert not await _in_org(p, p.org_a, lambda: questions.record_late_answer(
        p.org_a, m1.email, "questions", forged, "Save the rule"))
    assert p.redis.store.keys() == {
        f"cc:{p.org_a}:crm_rule_pending:member1@crm-q33.test:{held.token}"}
    assert await _in_org(p, p.org_a, lambda: questions.record_late_answer(
        p.org_a, m1.email, "questions", card(real), "Save the rule"))
    assert f"cc:{p.org_a}:crm_question_answered:{held.id}:member1@crm-q33.test" in p.redis.store
