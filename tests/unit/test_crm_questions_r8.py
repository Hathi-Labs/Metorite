"""R8 — the open questions, through the real router (WS-53 CRM-Q32-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.3 and the CRM-Q32-1
done-when list of §18.14.10.

Against a REAL Postgres, as the non-privileged role of the phase-4
rehearsal. The seed goes in as the superuser, so each read below is the first
thing the app role does.

1. Two orgs with different modules, plans and cursor rows. Each reads only
   its own questions (item 12).
2. A member who is not an admin gets 403, Native mode gets 409, and an org
   outside ``CRM_QUESTIONS_ORGS`` gets 404 ``questions_off`` (item 12).
3. An email, a phone and a name in the records reach the body as their
   pattern class only (item 3).
4. ``all=1`` adds each part in use with ``current`` (item 2).
5. ``count_open`` (item 4), and the ``questions`` of a catalogue entry and
   ``revenue`` of the catalogue (item 8).
6. The ``stage_value`` read through ``field_sql.value_counts``, and the
   ``slot`` read on the empty slots (item 11).
7. 25 modules and 1,300 field definitions answer under 1 s (item 13).
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import time
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
    "crm_sync_cursors", "crm_mapping_plans", "crm_field_fill", "crm_module_records",
    "crm_field_defs", "crm_source_profiles", "integration_connections", "crm_deals",
    "crm_leads", "crm_contacts", "crm_companies", "crm_pipeline_stages", "crm_pipelines",
    "crm_settings", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)

#: Item 13: the bound of one answer, in seconds.
BOUND_S = 1.0

#: Item 3: the values that may reach the body as their pattern class only.
_EMAIL = "ravi.kumar@q32-secret.test"
_PHONE = "+91 98450 12345"
_NAME = "Ravi Kumar"


def _user(org: str, *, admin: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:crm", "admin:access:manage"] if admin else ["feature:crm"]
    return UserContext(
        email="admin@crm-q32-r8.test" if admin else "member@crm-q32-r8.test",
        role=UserRole.EMPLOYEE, access=build_access(grants), organization_id=org,
    )


async def calls(p: Any, user: Any, requests: list[tuple[str, str, Any]],
                ) -> list[tuple[Any, float]]:
    """Each request through ONE app, engine and client, with its own time.
    The tenant comes from the user."""
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
            out = []
            for method, path, body in requests:
                started = time.perf_counter()
                res = await client.request(method, path, json=body)
                out.append((res, time.perf_counter() - started))
            return out
    finally:
        release_tenant(scope)


async def call(p: Any, user: Any, method: str, path: str, body: Any = None) -> Any:
    return (await calls(p, user, [(method, path, body)]))[0][0]


async def bound(p: Any, org: str, fn: Any) -> Any:
    """*fn(db)* in a tenant session of *org*, as the app role."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes.crm.core import _tenant_session

    scope = clear_tenant()
    token = bind_tenant(org)
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            _tenant_session() as db,
        ):
            return await fn(db)
    finally:
        release_tenant(token)
        release_tenant(scope)


# ── The seed, as the superuser ────────────────────────────────────────────────


def module_row(api: str, label: str | None = None, *, custom: bool = True,
               singular: str | None = None, display: str | None = "Name") -> dict:
    return {
        "api_name": api, "tab": api, "label": label or api,
        "singular_label": singular, "custom": custom,
        "generated_type": "custom" if custom else "default", "api_supported": True,
        "visible": True, "mirrored": True, "generic": True,
        "scope": "ZohoCRM.modules.custom.READ", "display_field": display,
    }


def mirror(p: Any, org: str, modules: list[dict], *, mode: str = "mirror") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), :m, 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = :m, access = 'everyone'"),
            {"o": org, "m": mode})
        conn = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm') RETURNING id"),
            {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), :c, 1, "
            "CAST(:p AS jsonb), :h)"),
            {"o": org, "c": conn, "p": json.dumps({"modules": modules}),
             "h": uuid.uuid4().hex})


def defs(p: Any, org: str, module: str, rows: list[tuple]) -> None:
    """``(key, type, lookup target)`` rows of ``crm_field_defs``."""
    with p.admin_engine.begin() as c:
        for position, (key, kind, target, *options) in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                "type, options, source, provider_api_name, position) VALUES "
                "(CAST(:o AS uuid), :e, :k, :l, :t, CAST(:opt AS jsonb), 'zoho', "
                ":k, :pos)"),
                {"o": org, "e": f"module:{module}", "k": key,
                 "l": key.replace("_", " "), "t": kind, "pos": position,
                 "opt": json.dumps({"lookup_module": target}) if target
                 else (json.dumps(options[0]) if options else None)})


def fills(p: Any, org: str, module: str, shares: dict[str, float], total: int = 100) -> None:
    with p.admin_engine.begin() as c:
        for key, share in shares.items():
            c.execute(text(
                "INSERT INTO crm_field_fill (organization_id, entity, key, filled, "
                "total, distinct_count) VALUES (CAST(:o AS uuid), :e, :k, :f, :t, 1)"),
                {"o": org, "e": f"module:{module}", "k": key,
                 "f": int(share * total), "t": total})


def records(p: Any, org: str, module: str, rows: list[dict]) -> None:
    with p.admin_engine.begin() as c:
        for i, fields in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"{module}-{i}",
                 "n": fields.get("Name") or f"{module} {i}", "f": json.dumps(fields)})


def plan(p: Any, org: str, items: list[dict]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, rules, "
            "created_by, approved_by, approved_at) VALUES (CAST(:o AS uuid), 1, "
            "'approved', CAST(:r AS jsonb), 'r8', 'r8', now())"),
            {"o": org, "r": json.dumps(items)})


def cursor(p: Any, org: str, module: str, state: dict) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, last_status, "
            "cursor_state) VALUES (CAST(:o AS uuid), :m, 'ok', CAST(:s AS jsonb)) "
            "ON CONFLICT (organization_id, module) DO UPDATE SET cursor_state = "
            "CAST(:s AS jsonb)"), {"o": org, "m": module, "s": json.dumps(state)})


def cursor_state(p: Any, org: str, module: str) -> Any:
    with p.admin_engine.begin() as c:
        row = c.execute(text(
            "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
            "CAST(:o AS uuid) AND module = :m"), {"o": org, "m": module}).first()
    return row[0] if row else None


@pytest.fixture
def q_env(promoted, monkeypatch):
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    for name, value in {
        "crm_mirror": True, "crm_mirror_orgs": "*", "crm_catalogue_orgs": "*",
        "crm_questions_orgs": "*", "crm_fill_rank_orgs": "*",
        "decide_feature_modes": "", "crm_decide_orgs": "",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


#: Org A: DEWiN's shape, with invented names.
PROFILE_A = [
    module_row("Invoices", custom=False, singular="Invoice"),
    module_row("Fee_Receipts", "Collection"),
    module_row("Buyer_Clients", "Clients"),
    module_row("Towers"),
    module_row("Commercials"),
]


def seed_a(p: Any) -> None:
    a = p.org_a
    mirror(p, a, PROFILE_A)
    defs(p, a, "Invoices", [
        ("Name", "text", None), ("Billed_Value", "currency", None),
        ("Net_Value", "currency", None), ("Invoice_Date", "date", None),
        ("Status", "select", None), ("Buyer", "lookup", "Buyer_Clients"),
    ])
    fills(p, a, "Invoices", {"Billed_Value": 0.45, "Net_Value": 0.4, "Invoice_Date": 1.0,
                             "Status": 0.9})
    defs(p, a, "Fee_Receipts", [
        ("Name", "text", None), ("Paid_On", "date", None), ("Part_A", "currency", None),
        ("Part_B", "currency", None), ("Track", "select", None),
        ("Buyer", "lookup", "Buyer_Clients"),
    ])
    fills(p, a, "Fee_Receipts", {"Paid_On": 0.6, "Part_A": 0.6, "Part_B": 0.55,
                                 "Track": 0.9})
    defs(p, a, "Buyer_Clients", [("Name", "text", None)])
    defs(p, a, "Towers", [("Name", "text", None)])
    defs(p, a, "Commercials", [("Name", "text", None), ("Status", "select", None,
                                                       [{"value": "Won"}, {"value": "Hold"}])])
    records(p, a, "Invoices", [{"Name": f"INV-{i}", "Billed_Value": 4242} for i in range(3)])
    records(p, a, "Fee_Receipts", [
        {"Name": "R-1", "Part_A": 100, "Track": _EMAIL, "Buyer": {"id": "x", "name": _NAME}},
        {"Name": "R-2", "Part_A": 200, "Track": _PHONE},
        {"Name": "R-3", "Part_B": 300, "Track": _NAME},
    ])
    records(p, a, "Buyer_Clients", [{"Name": _NAME}, {"Name": "Asha Rao"}])
    records(p, a, "Towers", [{"Name": "T1"}])
    records(p, a, "Commercials", [{"Name": "C1", "Status": "Won"},
                                  {"Name": "C2", "Status": "On Hold"},
                                  {"Name": "C3", "Status": "On Hold"}])
    plan(p, a, [
        {"kind": "role", "slot": "deal", "module": "Commercials"},
        {"kind": "stage", "slot": "deal", "module": "Commercials", "field_api_name": "Status",
         "values": {"Won": "won"}},
        {"kind": "catalogue", "module": "Invoices", "amount": "Net_Value",
         "by": {"amount": "admin"}},
    ])


def seed_b(p: Any) -> None:
    b = p.org_b
    mirror(p, b, [module_row("Ledgers", "Expense Ledger")])
    defs(p, b, "Ledgers", [("Name", "text", None), ("Spent", "currency", None),
                           ("Spent_Too", "currency", None)])
    fills(p, b, "Ledgers", {"Spent": 0.7, "Spent_Too": 0.7})
    records(p, b, "Ledgers", [{"Name": "L1", "Spent": 9}])


def ids(body: dict) -> list[str]:
    return [q["id"] for q in body["shown"]]


# ── 1. Each org reads only its own questions ──────────────────────────────────


async def test_two_orgs_each_read_only_their_own_questions(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    seed_b(p)
    cursor(p, p.org_b, "zoho:questions",
           {"dismissed": {"purpose:Towers": {"schema": "x"}}})
    a = await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")
    b = await call(p, _user(p.org_b), "GET", "/crm/questions?limit=50")
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    got_a, got_b = ids(a.json()), ids(b.json())
    # Org A: revenue, the money parts, a stage value, the slot and the purpose.
    assert got_a[0] == "revenue"
    assert "key:Fee_Receipts:amount" in got_a and "key:Fee_Receipts:status" in got_a
    assert "key:Invoices:amount" not in got_a  # the admin answered it
    assert "purpose:Towers" in got_a and "slot:company" in got_a
    stage = next(q for q in a.json()["shown"] if q["kind"] == "stage_value")
    assert (stage["value"], stage["count"], stage["slot"]) == ("On Hold", 2, "deal")
    assert "slot:deal" not in got_a  # the approved role plays Deals
    # Org B reads none of org A.
    assert got_b == ["purpose:Ledgers"]
    body_b = json.dumps(b.json())
    for word in ("Collection", "Towers", "Invoices", "Commercials", "On Hold"):
        assert word not in body_b, word
    assert a.json()["open"] == len(got_a) and a.json()["more"] == 0


async def test_the_first_read_shows_five_and_counts_the_rest(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions")).json()
    assert len(body["shown"]) == 5 and body["open"] == 5 + body["more"]
    assert body["plan"] == 1


# ── 2. The guards ────────────────────────────────────────────────────────────


async def test_a_member_gets_403_native_gets_409_and_outside_the_list_404(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    member = await call(p, _user(p.org_a, admin=False), "GET", "/crm/questions")
    assert member.status_code == 403, member.text
    mirror(p, p.org_b, [], mode="native")
    native = await call(p, _user(p.org_b), "GET", "/crm/questions")
    assert native.status_code == 409 and native.json()["detail"]["code"] == "not_mirror"
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", p.org_b, raising=False)
    off = await call(p, _user(p.org_a), "GET", "/crm/questions")
    assert off.status_code == 404 and off.json()["detail"]["code"] == "questions_off"
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "", raising=False)
    no_catalogue = await call(p, _user(p.org_a), "GET", "/crm/questions")
    assert no_catalogue.json()["detail"]["code"] == "questions_off"


# ── 3. The masked samples ───────────────────────────────────────────────────


async def test_an_email_a_phone_and_a_name_reach_the_body_as_their_class_only(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    res = await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")
    body = res.json()
    status = next(q for q in body["shown"] if q["id"] == "key:Fee_Receipts:status")
    (track,) = [c for c in status["choices"] if c["value"] == "Track"]
    # The lowest ids come first, and an id is a uuid, so the order is free.
    assert sorted(track["samples"]) == ["<email>", "<name>", "<phone>"]
    clients = next(q for q in body["shown"] if q["id"] == "slot:company")
    assert clients["choices"][0]["samples"][0] == "<name>"
    for secret in (_EMAIL, _PHONE, _NAME, "Asha Rao"):
        assert secret not in res.text, secret
    amount = next(q for q in body["shown"] if q["id"] == "key:Fee_Receipts:amount")
    assert [c["value"] for c in amount["choices"]] == ["Part_A", "Part_B"]
    assert sorted(amount["choices"][0]["samples"]) == ["100", "200"]
    assert all(len(c["samples"]) <= 3 for q in body["shown"] for c in q["choices"]
               if c.get("samples") is not None)


# ── 4. ``all=1`` ─────────────────────────────────────────────────────────────


async def test_all_adds_each_part_in_use_with_current(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions?all=1")).json()
    answered = {q["id"]: q for q in body["answered"]}
    assert (answered["key:Invoices:amount"]["current"],
            answered["key:Invoices:amount"]["by"]) == ("Net_Value", "admin")
    assert answered["key:Invoices:date"]["by"] == "code"
    assert answered["purpose:Fee_Receipts"]["current"] == "payment_in"
    assert "purpose:Commercials" not in answered  # a role gives it
    plain = (await call(p, _user(p.org_a), "GET", "/crm/questions")).json()
    assert plain["answered"] == []


# ── 5. count_open, and the catalogue ────────────────────────────────────────


async def test_count_open_and_the_questions_of_a_catalogue_entry(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings
    from gateway.routes.crm.questions import count_open

    p = q_env
    seed_a(p)
    full = (await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")).json()
    assert await bound(p, p.org_a, lambda db: count_open(db, p.org_a)) == full["open"]
    member = _user(p.org_a, admin=False)
    cat = (await call(p, member, "GET", "/crm/catalogue")).json()
    entries = {e["module"]: e for e in cat["entries"]}
    assert set(entries["Fee_Receipts"]["questions"]) >= {
        "key:Fee_Receipts:amount", "key:Fee_Receipts:status"}
    assert entries["Towers"]["questions"] == ["purpose:Towers"]
    assert entries["Invoices"]["questions"] == []
    assert cat["revenue"] == {"value": None, "by": None}
    # No sample and no record value reaches the catalogue of a member.
    text_ = json.dumps(cat)
    assert "samples" not in text_ and _EMAIL not in text_ and "4242" not in text_
    # Outside the list the entries carry no `questions`, and count_open is 0.
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    cat = (await call(p, member, "GET", "/crm/catalogue")).json()
    assert {e["questions"] for e in cat["entries"]} == {None}
    assert await bound(p, p.org_a, lambda db: count_open(db, p.org_a)) == 0


async def test_an_admin_answer_of_revenue_reaches_the_catalogue(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_mapping_plans SET rules = rules || CAST(:r AS jsonb) WHERE "
            "organization_id = CAST(:o AS uuid)"),
            {"o": p.org_a, "r": json.dumps([{"kind": "catalogue", "module": "@org",
                                             "revenue": "payment_in",
                                             "by": {"revenue": "admin"}}])})
    cat = (await call(p, _user(p.org_a), "GET", "/crm/catalogue")).json()
    assert cat["revenue"] == {"value": "payment_in", "by": "admin"}
    assert "@org" not in {e["module"] for e in cat["entries"]}
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")).json()
    assert "revenue" not in ids(body)


# ── 6. The stage and slot reads ─────────────────────────────────────────────


async def test_the_stage_values_read_counts_by_value_in_one_tenant(q_env, app_engine) -> None:
    from gateway.routes.crm.field_sql import value_counts

    p = q_env
    seed_a(p)
    seed_b(p)
    records(p, p.org_b, "Commercials", [{"Status": "Lost"}] * 4)
    got = await bound(p, p.org_a, lambda db: value_counts(
        db, p.org_a, "Commercials", "Status", 500))
    assert got == [("On Hold", 2), ("Won", 1)]
    # RLS and the predicate: org A never counts the records of org B.
    leak = await bound(p, p.org_a, lambda db: value_counts(
        db, p.org_b, "Commercials", "Status", 500))
    assert leak == []


async def test_a_dismissal_of_the_slot_hides_it_until_the_schema_moves(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    cursor(p, p.org_a, "zoho:map_pipeline", {"schema": {"slots": "d1"}, "items": {}})
    cursor(p, p.org_a, "zoho:questions", {"dismissed": {"slot:company": {"schema": "d1"}}})
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")).json()
    assert "slot:company" not in ids(body)
    cursor(p, p.org_a, "zoho:map_pipeline", {"schema": {"slots": "d2"}, "items": {}})
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")).json()
    assert "slot:company" in ids(body)


# ── 7. The bound ────────────────────────────────────────────────────────────


async def test_twenty_five_modules_and_1300_fields_answer_under_one_second(
    q_env, app_engine,
) -> None:
    p, a = q_env, q_env.org_a
    mirror(p, a, [module_row(f"Big_{i:02d}", f"Big {i}") for i in range(25)])
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, provider_api_name, position) "
            "SELECT CAST(:o AS uuid), 'module:Big_' || lpad(CAST(m AS text), 2, '0'), "
            "'F_' || f, 'Field ' || f, "
            "CASE f % 4 WHEN 0 THEN 'currency' WHEN 1 THEN 'date' "
            "WHEN 2 THEN 'select' ELSE 'text' END, 'zoho', 'F_' || f, f "
            "FROM generate_series(0, 24) m, generate_series(1, 52) f"), {"o": a})
        c.execute(text(
            "INSERT INTO crm_field_fill (organization_id, entity, key, filled, total, "
            "distinct_count) SELECT organization_id, entity, key, 60, 100, 3 "
            "FROM crm_field_defs WHERE organization_id = CAST(:o AS uuid)"), {"o": a})
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, "
            "name, fields) SELECT CAST(:o AS uuid), 'Big_' || lpad(CAST(m AS text), 2, '0'), "
            "'zoho', 'r' || m || '-' || r, 'Row ' || r, "
            "jsonb_build_object('F_4', r, 'F_8', r * 2) "
            "FROM generate_series(0, 24) m, generate_series(1, 40) r"), {"o": a})
        count = c.execute(text(
            "SELECT count(*) FROM crm_field_defs WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": a}).scalar()
    assert count == 1300
    admin = _user(a)
    _warm, (res, took) = await calls(p, admin, [("GET", "/crm/questions", None),
                                                ("GET", "/crm/questions", None)])
    print(f"CRM-Q32-1 25 modules, 1,300 fields: {took:.3f} s")
    assert res.status_code == 200, res.text
    assert res.json()["open"] >= 25
    assert took < BOUND_S, f"the questions took {took:.3f} s"


# ── CRM-Q32-4: the questions step, My Day and Sync health ───────────────────


async def step(p: Any, org: str, now: Any) -> Any:
    from gateway.routes.crm.questions import questions_after_run

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        return await questions_after_run(org, now=now)


async def test_the_step_runs_with_each_mode_off_and_an_answer_sets_no_new_at(
    q_env, app_engine,
) -> None:
    from datetime import UTC, datetime, timedelta

    from gateway.crm_sync import map_pipeline as mp

    p = q_env
    seed_a(p)
    assert not mp.enabled_for(p.org_a), "each mode of the pipeline is off"
    first = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
    count = await step(p, p.org_a, first)
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert count == len(state["seen"]) > 0
    assert (state["new_at"], state["money"], state["new"]) == (
        first.isoformat(), True, count)
    # An answer closes one question. The next step sets no new_at.
    got = await call(p, _user(p.org_a), "POST", "/crm/questions/purpose:Towers/answer",
                     {"value": "asset"})
    assert got.status_code == 200, got.text
    await step(p, p.org_a, first + timedelta(hours=2))
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert state["new_at"] == first.isoformat() and "purpose:Towers" not in state["seen"]
    assert len(state["seen"]) == count - 1
    # A dismissal written beside the step stays.
    await call(p, _user(p.org_a), "POST", "/crm/questions/key:Fee_Receipts:date/dismiss")
    await step(p, p.org_a, first + timedelta(hours=3))
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert "key:Fee_Receipts:date" in state["dismissed"]


async def test_two_steps_on_one_day_give_one_card_for_an_admin_only(
    q_env, app_engine,
) -> None:
    from datetime import UTC, datetime, timedelta

    from gateway.routes.crm.aggregate import my_day_rows
    from gateway.routes.shell import feed

    p = q_env
    seed_a(p)
    morning = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
    await step(p, p.org_a, morning)
    # A new module with records brings a new question later the same day.
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = jsonb_set(profile, '{modules}', "
            "profile -> 'modules' || CAST(:m AS jsonb)) WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": p.org_a, "m": json.dumps([module_row("Masts")])})
    records(p, p.org_a, "Masts", [{"Name": "M1"}])
    evening = morning + timedelta(hours=12)
    await step(p, p.org_a, evening)
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert (state["new_at"], state["new"]) == (evening.isoformat(), 1)

    async def rows_of(user: Any) -> Any:
        return await bound(p, p.org_a, lambda _db: my_day_rows(
            user, now=evening + timedelta(hours=1), limit=5))

    admin = await rows_of(_user(p.org_a))
    signal = feed._questions_signal(admin["questions"])
    assert signal.signal_id == "crm:questions:2026-10-18"
    assert (signal.facts["count"], signal.facts["money"]) == (1, True)
    member = await rows_of(_user(p.org_a, admin=False))
    assert member["questions"] is None
    # The next day the card is gone: one card a day at most.
    later = await bound(p, p.org_a, lambda _db: my_day_rows(
        _user(p.org_a), now=evening + timedelta(hours=25), limit=5))
    assert later["questions"] is None


async def test_sync_health_carries_the_open_count(q_env, app_engine, monkeypatch) -> None:
    from datetime import UTC, datetime

    from acb_common import get_settings
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    p = q_env
    seed_a(p)
    # Before the first questions step, the count is not known.
    health = await call(p, _user(p.org_a), "GET", "/crm/sync/health")
    assert health.status_code == 200 and health.json()["open_questions"] is None
    await step(p, p.org_a, datetime.now(UTC))
    full = (await call(p, _user(p.org_a), "GET", "/crm/questions")).json()
    # Fix round P2: the card reads ONE cursor row, and builds no question.
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_args) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        health = await call(p, _user(p.org_a), "GET", "/crm/sync/health")
    finally:
        event.remove(Engine, "before_cursor_execute", _record)
    assert health.status_code == 200, health.text
    assert health.json()["open_questions"] == full["open"] > 0
    assert not [s for s in seen if "crm_field_defs" in s or "crm_mapping_plans" in s]
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    health = await call(p, _user(p.org_a), "GET", "/crm/sync/health")
    assert health.json()["open_questions"] is None


async def test_the_aggregate_422_of_an_open_key_part_names_open_questions(
    q_env, app_engine, monkeypatch,
) -> None:
    """CRM-Q32-6 item 2: the gateway puts ``href`` in the 422 detail of
    ``no_key_field`` when the part is an open question, and only then."""
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    path = "/crm/aggregate?entity=module:Fee_Receipts&measure=sum_amount&group_by=owner"
    got = await call(p, _user(p.org_a, admin=False), "GET", path)
    assert got.status_code == 422, got.text
    detail = got.json()["detail"]
    assert (detail["code"], detail["href"]) == (
        "no_key_field", "/crm?tab=settings&section=questions")
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    got = await call(p, _user(p.org_a, admin=False), "GET", path)
    assert got.status_code == 422 and "href" not in got.json()["detail"]


async def test_a_stage_value_with_a_blank_around_it_is_the_value(q_env, app_engine) -> None:
    """Fix round: the values read trims each value as ``mapping.text_value``
    (the executor) does, so " Won " is "Won", which has an entry."""
    from gateway.crm_sync import questions as qs
    from gateway.routes.crm.field_sql import value_counts

    p = q_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        for i, status in enumerate([" Won ", "Won\t", "  ", {"x": 1}]):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Commercials', 'zoho', "
                ":e, :e, CAST(:f AS jsonb))"),
                {"o": p.org_a, "e": f"trim-{i}", "f": json.dumps({"Status": status})})
    got = await bound(p, p.org_a, lambda db: value_counts(
        db, p.org_a, "Commercials", "Status", 500))
    assert got == [("Won", 3), ("On Hold", 2)]
    body = (await call(p, _user(p.org_a), "GET", "/crm/questions?limit=50")).json()
    stages = [q["value"] for q in body["shown"] if q["kind"] == "stage_value"]
    assert stages == ["On Hold"]
    assert qs.stage_id("deal", " Won ") not in ids(body)
