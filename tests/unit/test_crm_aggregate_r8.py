"""R8 — the CRM aggregate routes and ``GET /crm/mode`` (WS-53 CRM-C7).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 items 2 and 3, done-when 2.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal:

1. Each measure (``count``, ``sum_amount``, ``weighted``) and each group, and
   a 422 for an unknown value.
2. ``weighted`` reads the effective probability: the deal's own value, else
   the probability of its stage.
3. The Q8 filters bind the aggregate too, because it reuses the list builder.
4. The dwell numbers (median, 90th percentile) and the overdue deals.
5. No row of organization B reaches organization A.
6. Mirror mode answers the dwell route with an empty result and a reason.
7. ``GET /crm/mode`` answers a member that ``GET /crm/settings`` refuses.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

from __future__ import annotations

import json
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
    "crm_status_changes", "crm_deal_contacts", "crm_activities", "crm_deals",
    "crm_leads", "crm_contacts", "crm_companies", "crm_field_defs",
    "crm_pipeline_stages", "crm_pipelines", "crm_settings",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-c7-r8.test", org, "feature:crm", "admin:access:manage")


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


async def _get(p: Any, org: str, path: str) -> Any:
    res = await _call(p, _admin(org), "GET", path)
    assert res.status_code == 200, res.text
    return res.json()


def _stage(c: Any, org: str, name: str) -> str:
    return str(c.execute(text(
        "SELECT id FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid) "
        "AND name = :n"), {"o": org, "n": name}).scalar())


def _deal(c: Any, org: str, name: str, stage: str, amount: float, *,
          owner: str, probability: int | None = None, clock_days: int | None = None,
          custom: dict | None = None) -> None:
    c.execute(text(
        "INSERT INTO crm_deals (organization_id, name, status_id, amount, "
        "probability, owner_email, status_changed_at, custom) VALUES "
        "(CAST(:o AS uuid), :n, CAST(:s AS uuid), :a, :p, :w, "
        "CASE WHEN CAST(:d AS int) IS NULL THEN NULL "
        "ELSE now() - make_interval(days => CAST(:d AS int)) END, "
        "CAST(:c AS jsonb))"),
        {"o": org, "n": name, "s": _stage(c, org, stage), "a": amount,
         "p": probability, "w": owner, "d": clock_days,
         "c": json.dumps(custom or {})})


def _change(c: Any, org: str, from_status: str, days: float) -> None:
    c.execute(text(
        "INSERT INTO crm_status_changes (organization_id, entity_type, entity_id, "
        "from_status, to_status, changed_by, dwell_seconds) VALUES "
        "(CAST(:o AS uuid), 'deal', gen_random_uuid(), :f, 'Next', 'r8', :s)"),
        {"o": org, "f": from_status, "s": int(days * 86400)})


@pytest.fixture
async def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    for org in (promoted.org_a, promoted.org_b):
        res = await _call(promoted, _admin(org), "PATCH", "/crm/settings",
                          {"mode": "native"})
        assert res.status_code == 200, res.text
    a, b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        _deal(c, a, "A1", "Proposal", 1000, owner="a1@c7.test", clock_days=10,
              custom={"region": "south"})
        _deal(c, a, "A2", "Proposal", 2000, owner="a2@c7.test", probability=10,
              clock_days=2)
        _deal(c, a, "A3", "Negotiation", 4000, owner="A1@c7.test", clock_days=0)
        _deal(c, a, "A4", "Closed Won", 8000, owner="a1@c7.test", clock_days=30)
        _deal(c, b, "B1", "Proposal", 100000, owner="b@c7.test", clock_days=40)
        c.execute(text(
            "UPDATE crm_deal_statuses SET max_dwell_days = 5 "
            "WHERE organization_id = CAST(:o AS uuid) AND name = 'Proposal'"),
            {"o": a})
        for days in (1, 3, 5, 7):
            _change(c, a, "Proposal", days)
        _change(c, a, "Qualification", 2)
        _change(c, b, "Proposal", 100)
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
            "type, source, provider_api_name, position) VALUES "
            "(CAST(:o AS uuid), 'deal', 'region', 'Region', 'text', 'native', "
            "'region', 0)"), {"o": a})
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _by_label(body: dict) -> dict[str, float]:
    return {g["label"]: g["value"] for g in body["groups"]}


async def test_count_by_status_names_each_stage_in_board_order(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&group_by=status")
    assert [(g["label"], g["value"]) for g in body["groups"]] == [
        ("Proposal", 2), ("Negotiation", 1), ("Closed Won", 1),
    ]
    assert body["total"] == 4


async def test_sum_amount_by_owner_folds_the_case(ns, app_engine) -> None:  # noqa: F811
    body = await _get(
        ns, ns.org_a, "/crm/aggregate?entity=deals&measure=sum_amount&group_by=owner")
    assert _by_label(body) == {"a1@c7.test": 13000, "a2@c7.test": 2000}


async def test_weighted_reads_the_effective_probability(ns, app_engine) -> None:  # noqa: F811
    """A1 has no probability, so it takes Proposal's 50. A2 keeps its own 10.
    A3 takes Negotiation's 75. A4 is won, so no forecast counts it."""
    body = await _get(
        ns, ns.org_a, "/crm/aggregate?entity=deals&measure=weighted&group_by=owner")
    assert _by_label(body) == {"a1@c7.test": 500 + 3000, "a2@c7.test": 200}
    assert body["total"] == 3700


async def test_no_group_is_one_total_over_every_record(ns, app_engine) -> None:  # noqa: F811
    """No group_by and no group_field: one total and the count of records.
    The statement then has no GROUP BY, so the weighted lanes add up too."""
    count = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals")
    assert (count["groups"], count["total"], count["records"]) == ([], 4, 4)
    assert count["group_by"] is None and count["group_field"] is None
    amount = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&measure=sum_amount")
    assert (amount["total"], amount["records"]) == (15000, 4)
    weighted = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&measure=weighted")
    assert weighted["total"] == 3700 and weighted["records"] is None
    # The list filters still bind, and no row of org B is counted.
    owner = await _get(
        ns, ns.org_a, "/crm/aggregate?entity=deals&measure=sum_amount&owner=a2@c7.test")
    assert (owner["total"], owner["records"]) == (2000, 1)
    b = await _get(ns, ns.org_b, "/crm/aggregate?entity=deals&measure=sum_amount")
    assert (b["total"], b["records"]) == (100000, 1)
    # A filter that matches nothing still gives one row: a count of 0.
    none = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&owner=nobody@c7.test")
    assert (none["total"], none["records"]) == (0, 0)
    # A grouped answer carries the count of records too.
    grouped = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&measure=sum_amount"
                                       "&group_by=owner")
    assert grouped["records"] == 4


async def test_each_other_group_answers(ns, app_engine) -> None:  # noqa: F811
    for group in ("pipeline", "source", "created_month", "updated_month", "close_month"):
        body = await _get(ns, ns.org_a, f"/crm/aggregate?entity=deals&group_by={group}")
        assert body["total"] == 4, (group, body)
    source = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&group_by=source")
    assert _by_label(source) == {"manual": 4}


async def test_the_list_filters_bind_the_aggregate(ns, app_engine) -> None:  # noqa: F811
    custom = await _get(
        ns, ns.org_a, "/crm/aggregate?entity=deals&group_by=owner&custom.region=south")
    assert _by_label(custom) == {"a1@c7.test": 1}
    owner = await _get(
        ns, ns.org_a, "/crm/aggregate?entity=deals&group_by=status&owner=a2@c7.test")
    assert _by_label(owner) == {"Proposal": 1}
    bad = await _call(ns, _admin(ns.org_a), "GET",
                      "/crm/aggregate?entity=deals&group_by=status&custom.nope=1")
    assert bad.status_code == 422


@pytest.mark.parametrize("query", [
    "entity=deals&group_by=status&measure=median",
    "entity=deals&group_by=colour",
    "entity=tickets&group_by=status",
    "entity=leads&group_by=status&measure=sum_amount",
    "entity=contacts&group_by=status",
    "entity=leads&group_by=close_month",
])
async def test_an_unknown_or_unfit_value_is_a_422(ns, app_engine, query: str) -> None:  # noqa: F811
    res = await _call(ns, _admin(ns.org_a), "GET", f"/crm/aggregate?{query}")
    assert res.status_code == 422, res.text


async def test_no_row_of_org_b_reaches_org_a(ns, app_engine) -> None:  # noqa: F811
    a = await _get(ns, ns.org_a, "/crm/aggregate?entity=deals&measure=sum_amount&group_by=owner")
    assert "b@c7.test" not in _by_label(a)
    b = await _get(ns, ns.org_b, "/crm/aggregate?entity=deals&measure=sum_amount&group_by=owner")
    assert _by_label(b) == {"b@c7.test": 100000}
    dwell = await _get(ns, ns.org_a, "/crm/aggregate/stage-dwell?days=1")
    assert "B1" not in {d["name"] for d in dwell["overdue"]}
    proposal = next(s for s in dwell["stages"] if s["name"] == "Proposal")
    assert proposal["samples"] == 4


async def test_the_dwell_numbers(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/aggregate/stage-dwell")
    stages = {s["name"]: s for s in body["stages"]}
    assert stages["Proposal"]["median_days"] == 4.0
    assert stages["Proposal"]["p90_days"] == 6.4
    assert stages["Proposal"]["max_dwell_days"] == 5
    assert stages["Qualification"]["samples"] == 1
    assert stages["Negotiation"]["samples"] == 0
    assert stages["Negotiation"]["median_days"] is None
    # Past its own limit of 5 days: A1 (10 days). A2 (2 days) is not.
    assert [d["name"] for d in body["overdue"]] == ["A1"]
    assert body["overdue"][0]["limit_days"] == 5
    assert body["overdue_total"] == 1
    assert body["reason"] is None


async def test_a_days_argument_replaces_each_limit(ns, app_engine) -> None:  # noqa: F811
    """A3 entered its stage today and A4 is won, so neither is overdue."""
    body = await _get(ns, ns.org_a, "/crm/aggregate/stage-dwell?days=1")
    assert [d["name"] for d in body["overdue"]] == ["A1", "A2"]
    page = await _get(ns, ns.org_a, "/crm/aggregate/stage-dwell?days=1&page=2&page_size=1")
    assert [d["name"] for d in page["overdue"]] == ["A2"]
    assert page["overdue_total"] == 2


async def test_mirror_mode_answers_dwell_with_a_reason(ns, app_engine) -> None:  # noqa: F811
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET mode = 'mirror' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_b})
    body = await _get(ns, ns.org_b, "/crm/aggregate/stage-dwell?days=1")
    assert body["stages"] == [] and body["overdue"] == []
    assert "Zoho" in body["reason"]
    mode = await _get(ns, ns.org_b, "/crm/mode")
    assert mode == {"mode": "mirror"}


async def test_a_member_reads_the_mode_and_not_the_settings(ns, app_engine) -> None:  # noqa: F811
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET access = 'everyone' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})
    member = _principal("m@c7.test", ns.org_a, "feature:crm")
    mode = await _call(ns, member, "GET", "/crm/mode")
    assert mode.status_code == 200, mode.text
    assert mode.json() == {"mode": "native"}
    settings = await _call(ns, member, "GET", "/crm/settings")
    assert settings.status_code == 403
    agg = await _call(ns, member, "GET", "/crm/aggregate?entity=deals&group_by=status")
    assert agg.status_code == 200, agg.text


async def test_a_member_the_access_rule_refuses_gets_403(ns, app_engine) -> None:  # noqa: F811
    member = _principal("m@c7.test", ns.org_a, "feature:crm")
    for path in ("/crm/mode", "/crm/aggregate?entity=deals&group_by=status",
                 "/crm/aggregate/stage-dwell"):
        res = await _call(ns, member, "GET", path)
        assert res.status_code == 403, (path, res.text)
