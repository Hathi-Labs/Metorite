"""R8 — one plan for the Field map, against a real Postgres (WS-53 CRM-Q18-1,
``crm_platform.md`` §18.9.6, "CRM-Q18 · One plan").

Migration 276 adds ``crm_mapping_plans``: the versions of the plan of an org.
``crm_field_rules`` stays the live set that the sync reads. The routes are in
``gateway/routes/crm/mapping_plans.py``, and ``commit`` there is the one
approve.

What this proves, with two organizations and the non-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), one test for each "Done when"
item of CRM-Q18-1:

1. the migration is found by content, replays, has FORCE RLS, and allows one
   approved version for each org;
2. the first ``GET`` builds version 1 from the approved rules, once, also when
   two first reads race;
3. a draft changes no row of ``crm_field_rules``, and nothing in the sync;
4. an approve writes the snapshot in one transaction: a kept rule keeps its id,
   a rule that leaves is ``rejected``, and two rules can swap columns;
5. a restore approves a new version with an old content, and the sync undoes
   the rule that left through the path it has today;
6. a per-rule ``PATCH`` or ``POST`` writes a new approved version;
7. each route is admin only, Mirror only and dark behind ``CRM_MIRROR``, and
   the org comes from the server;
8. orgs stay apart.

``tests/unit/test_crm_field_rules_r8.py`` holds the fixture and the fake Zoho.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _leads,
    _member_of,
    _patch,
    _post,
    _req,
    _rule_status,
    _run,
    fr,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _count,
    _q,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

ADMIN = "admin@crm-sync.test"
PLANS = "/crm/mapping/plans"


async def _plans(org: str) -> Any:
    got = await _req(_admin_of(org), "GET", PLANS)
    assert got.status_code == 200, got.text
    return got.json()


async def _draft(org: str, *changes: dict[str, Any]) -> Any:
    return await _req(_admin_of(org), "POST", PLANS, {"changes": list(changes)})


async def _approve(org: str, version: int) -> Any:
    return await _req(_admin_of(org), "POST", f"{PLANS}/{version}/approve")


async def _restore(org: str, version: int) -> Any:
    return await _req(_admin_of(org), "POST", f"{PLANS}/{version}/restore")


def _put(field: str, column: str, entity: str = "leads") -> dict[str, Any]:
    return {"op": "put", "entity": entity, "field_api_name": field, "target_column": column}


def _remove(field: str, entity: str = "leads") -> dict[str, Any]:
    return {"op": "remove", "entity": entity, "field_api_name": field}


def _rules(p: Any, org: str) -> dict[str, tuple[Any, ...]]:
    """Every rule row of *org*, by field: id, status, column, updated_at."""
    rows = _q(p, "SELECT field_api_name, id, status, target_column, updated_at, "
                 "proposed_by FROM crm_field_rules "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: tuple(r[1:]) for r in rows}


def _versions(p: Any, org: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _q(
        p, "SELECT version, state, created_by, approved_by, based_on "
           "FROM crm_mapping_plans WHERE organization_id = CAST(:o AS uuid) "
           "ORDER BY version", o=org)]


def _identity(plan: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(r["entity"], r["field_api_name"], r["target_column"]) for r in plan["rules"]}


async def _approved(org: str, body: dict[str, Any]) -> dict[str, Any]:
    """The approved version of the list, with its snapshot from
    ``GET /crm/mapping/plans/{version}``."""
    (summary,) = [v for v in body["versions"] if v["state"] == "approved"]
    assert summary["version"] == body["approved"]
    got = await _req(_admin_of(org), "GET", f"{PLANS}/{summary['version']}")
    assert got.status_code == 200, got.text
    plan = got.json()
    assert len(plan["rules"]) == summary["rule_count"]
    return plan


def _cursor_state(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = 'zoho:field_rules'", o=org)
    return rows[0][0] if rows else None


# ── 1. The migration ─────────────────────────────────────────────────────────


def test_1_the_migration_is_found_by_content_replays_and_allows_one_approved(fr) -> None:
    p, _a, _b = fr
    sql = re.compile(r"CREATE TABLE IF NOT EXISTS crm_mapping_plans")
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and sql.search(f.read_text(encoding="utf-8"))]
    assert len(found) == 1
    body = found[0].read_text(encoding="utf-8")
    # Expand only (R6): no drop, no rename, no change to another table, no data.
    for word in ("DROP TABLE", "RENAME", "ALTER TABLE crm_field_rules", "INSERT INTO",
                 "UPDATE "):
        assert word not in body, word
    with p.admin_engine.begin() as c:
        _exec_file(c, str(found[0]))
    assert _q(p, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                 "WHERE oid = to_regclass('crm_mapping_plans')") == [(True, True)]
    policy = _q(p, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_mapping_plans'")
    assert policy and all(row == (True, True) for row in policy)
    indexes = {r[0] for r in _q(p, "SELECT indexname FROM pg_indexes "
                                   "WHERE tablename = 'crm_mapping_plans'")}
    assert {"crm_mapping_plans_version_key", "crm_mapping_plans_one_approved"} <= indexes
    # The indexes of crm_field_rules do not change.
    rule_indexes = {r[0] for r in _q(p, "SELECT indexname FROM pg_indexes "
                                        "WHERE tablename = 'crm_field_rules'")}
    assert {"crm_field_rules_field_key", "crm_field_rules_one_approved"} <= rule_indexes

    insert = (
        "INSERT INTO crm_mapping_plans (organization_id, version, state, created_by) "
        "VALUES (CAST(:o AS uuid), :v, 'approved', 'admin')")
    with p.admin_engine.begin() as c:
        c.execute(text(insert), {"o": p.org_a, "v": 1})
        c.execute(text(insert), {"o": p.org_b, "v": 1})  # another org may
    with pytest.raises(DBAPIError), p.admin_engine.begin() as c:
        c.execute(text(insert), {"o": p.org_a, "v": 2})
    with pytest.raises(DBAPIError), p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, created_by) "
            "VALUES (CAST(:o AS uuid), 1, 'draft', 'admin')"), {"o": p.org_a})
    with pytest.raises(DBAPIError), p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, created_by) "
            "VALUES (CAST(:o AS uuid), 3, 'live', 'admin')"), {"o": p.org_a})


# ── 2. The first read builds version 1 ───────────────────────────────────────


async def test_2_the_first_read_builds_version_1_from_the_approved_rules(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    # Rules from before this slice: one approved, one proposed, one rejected.
    with p.admin_engine.begin() as c:
        for field, column, status, by in (
            ("Custom_Industry", "industry", "approved", "admin"),
            ("Why_Lost", "lost_note", "proposed", "ai"),
            ("Headcount", "no_of_employees", "rejected", "admin"),
        ):
            c.execute(text(
                "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
                "field_api_name, field_type, profile_version, target_column, status, "
                "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', :f, 'select', "
                "1, :c, :s, :b)"), {"o": p.org_a, "f": field, "c": column, "s": status,
                                    "b": by})
    before = _rules(p, p.org_a)
    assert _versions(p, p.org_a) == [], "the migration writes no data"

    body = await _plans(p.org_a)
    assert [v["version"] for v in body["versions"]] == [1]
    first = await _approved(p.org_a, body)
    assert (first["state"], first["created_by"], first["approved_by"]) == (
        "approved", ADMIN, ADMIN)
    assert first["based_on"] is None
    assert _identity(first) == {("lead", "Custom_Industry", "industry")}
    assert {r["kind"] for r in first["rules"]} == {"field"}
    assert _rules(p, p.org_a) == before, "a read changes no rule"

    again = await _plans(p.org_a)
    assert [v["version"] for v in again["versions"]] == [1], "one version 1"


async def test_2_two_first_reads_that_race_write_one_version(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    results = await asyncio.gather(*(
        _req(_admin_of(p.org_a), "GET", PLANS) for _ in range(4)))
    assert [r.status_code for r in results] == [200] * 4, [r.text for r in results]
    assert [v[0] for v in _versions(p, p.org_a)] == [1]


# ── 3. A draft changes nothing ───────────────────────────────────────────────


async def test_3_a_draft_changes_no_rule_and_nothing_in_the_sync(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    rules = _rules(p, p.org_a)
    leads = _leads(p, p.org_a)
    state = _cursor_state(p, p.org_a)
    approved = (await _plans(p.org_a))["approved"]

    made = await _draft(p.org_a, _put("Why_Lost", "lost_note"), _remove("Custom_Industry"))
    assert made.status_code == 201, made.text
    draft = made.json()
    assert (draft["state"], draft["based_on"], draft["created_by"]) == (
        "draft", approved, ADMIN)
    assert draft["approved_by"] is None and draft["approved_at"] is None
    assert _identity(draft) == {("lead", "Why_Lost", "lost_note")}
    (rule,) = draft["rules"]
    assert (rule["field_type"], rule["kind"]) == ("textarea", "field")
    assert rule["profile_version"] >= 1

    assert _rules(p, p.org_a) == rules, "a draft writes no crm_field_rules row"
    assert (await _plans(p.org_a))["approved"] == approved
    a.add("Leads", "l3", 30, Last_Name="Three", Custom_Industry="Space", Why_Lost="Slow")
    assert (await _run(p.org_a)).status == "ok"
    after = _leads(p, p.org_a)
    assert {k: after[k] for k in leads} == leads, "the sync changed a row"
    assert after["l3"][1] == "Space", "the approved rule still applies"
    assert after["l3"][3] is None, "the rule of the draft does not apply"
    assert _cursor_state(p, p.org_a)["applied"] == state["applied"]


async def test_3_a_draft_has_the_checks_of_a_new_rule(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    count = len(_versions(p, p.org_a))

    mismatch = await _draft(p.org_a, _put("Next_Call", "industry"))
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["detail"]["code"] == "type_mismatch"
    assert mismatch.json()["detail"]["field_api_name"] == "Next_Call"
    on_layout = await _draft(p.org_a, _put("Why_Lost", "email"))
    assert on_layout.status_code == 422
    assert on_layout.json()["detail"]["code"] == "standard_field_on_layout"
    unknown = await _draft(p.org_a, _put("Not_A_Field", "lost_note"))
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_field"
    gone = await _draft(p.org_a, _remove("Why_Lost"))
    assert gone.status_code == 422 and gone.json()["detail"]["code"] == "not_in_plan"
    no_column = await _draft(p.org_a, {"op": "put", "entity": "leads",
                                       "field_api_name": "Why_Lost"})
    assert no_column.status_code == 422
    twice = await _draft(p.org_a, _put("Why_Lost", "industry"))
    assert twice.status_code == 409 and twice.json()["detail"]["code"] == "rule_taken"
    empty = await _req(_admin_of(p.org_a), "POST", PLANS, {"changes": []})
    assert empty.status_code == 422
    assert len(_versions(p, p.org_a)) == count, "a refused draft writes no version"


# ── 4. The approve ───────────────────────────────────────────────────────────


async def test_4_an_approve_keeps_ids_rejects_what_leaves_and_the_sync_follows(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "industry"), ("Headcount", "no_of_employees")):
        assert (await _post(p.org_a, field, column)).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    assert _leads(p, p.org_a)["l1"][1] == "Robotics"
    before = _rules(p, p.org_a)
    old = (await _plans(p.org_a))["approved"]

    draft = (await _draft(
        p.org_a, _put("Why_Lost", "lost_note"), _remove("Custom_Industry"),
    )).json()
    done = await _approve(p.org_a, draft["version"])
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["version"], body["state"], body["approved_by"]) == (
        draft["version"], "approved", ADMIN)
    assert body["approved_at"]
    states = {v[0]: v[1] for v in _versions(p, p.org_a)}
    assert states[old] == "superseded" and states[draft["version"]] == "approved"
    assert list(states.values()).count("approved") == 1

    after = _rules(p, p.org_a)
    assert after["Headcount"] == before["Headcount"], "a kept rule is not touched"
    assert after["Custom_Industry"][0] == before["Custom_Industry"][0], "same id"
    assert after["Custom_Industry"][1] == "rejected"
    assert (after["Why_Lost"][1], after["Why_Lost"][2], after["Why_Lost"][4]) == (
        "approved", "lost_note", "admin")
    approved_live = {(f, r[2]) for f, r in after.items() if r[1] == "approved"}
    assert approved_live == {(r["field_api_name"], r["target_column"])
                             for r in body["rules"]}

    reads = len(a.lead_reads())
    assert (await _run(p.org_a)).status == "ok"
    leads = _leads(p, p.org_a)
    assert leads["l1"][1] is None, "the rule that left is undone (a full read)"
    assert leads["l1"][3] == "Price", "the new rule is backfilled"
    assert leads["l2"][2] == 40, "the kept rule still holds its value"
    full = [r for r in a.lead_reads()[reads:] if r.headers.get("If-Modified-Since") is None]
    assert len(full) == 1
    applied = {r["field"]: r["id"] for r in _cursor_state(p, p.org_a)["applied"]}
    assert applied["Headcount"] == str(before["Headcount"][0])

    # A second approve of the same version is refused.
    again = await _approve(p.org_a, draft["version"])
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_draft"
    assert (await _approve(p.org_a, 999)).status_code == 404


async def test_4_two_rules_swap_their_columns_in_one_approve(fr) -> None:
    """The writes keep ``crm_field_rules_one_approved`` whole at each step."""
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "description"), ("Why_Lost", "lost_note")):
        made = await _post(p.org_a, field, column)
        assert made.status_code == 201, made.text
    before = _rules(p, p.org_a)

    draft = await _draft(p.org_a, _put("Custom_Industry", "lost_note"),
                         _put("Why_Lost", "description"))
    assert draft.status_code == 201, draft.text
    done = await _approve(p.org_a, draft.json()["version"])
    assert done.status_code == 200, done.text
    after = _rules(p, p.org_a)
    assert after["Custom_Industry"][:3] == (before["Custom_Industry"][0], "approved",
                                            "lost_note")
    assert after["Why_Lost"][:3] == (before["Why_Lost"][0], "approved", "description")


async def test_4_a_draft_built_before_a_change_is_refused(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    stale = (await _draft(p.org_a, _put("Why_Lost", "lost_note"))).json()
    # A per-rule change approves a new version after the draft.
    assert (await _post(p.org_a, "Headcount", "no_of_employees")).status_code == 201
    refused = await _approve(p.org_a, stale["version"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "plan_moved"
    assert _rules(p, p.org_a).get("Why_Lost") is None, "the refused approve wrote nothing"


# ── 5. The restore ───────────────────────────────────────────────────────────


async def test_5_a_restore_undoes_a_rule_through_the_existing_path(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    before_link = (await _plans(p.org_a))["approved"]
    made = await _post(p.org_a, "LinkedIn", "linkedin_url", entity="contacts")
    assert made.status_code == 201, made.text
    assert (await _run(p.org_a)).status == "ok"

    def contact() -> Any:
        return _q(p, "SELECT linkedin_url, custom FROM crm_contacts WHERE "
                     "organization_id = CAST(:o AS uuid) AND ext_id = 'c1'", o=p.org_a)[0]

    assert contact()[0] == "https://linkedin.example/A-ann"
    kept = _rules(p, p.org_a)["Custom_Industry"]

    back = await _restore(p.org_a, before_link)
    assert back.status_code == 201, back.text
    body = back.json()
    assert (body["state"], body["based_on"]) == ("approved", before_link)
    assert body["version"] == max(v[0] for v in _versions(p, p.org_a))
    assert _identity(body) == {("lead", "Custom_Industry", "industry")}
    assert _rule_status(p, made.json()["id"]) == "rejected"
    assert _rules(p, p.org_a)["Custom_Industry"] == kept, "the kept rule is not touched"

    reads = len(a.contact_reads())
    assert (await _run(p.org_a)).status == "ok"
    assert contact()[0] is None, "the sync cleared the free column"
    assert contact()[1]["LinkedIn"] == "https://linkedin.example/A-ann"
    assert all(r.headers.get("If-Modified-Since") for r in a.contact_reads()[reads:])
    assert _leads(p, p.org_a)["l1"][1] == "Robotics", "the kept rule still applies"

    # Forward again: restore the version that held the LinkedIn rule.
    with_link = body["version"] - 1
    again = await _restore(p.org_a, with_link)
    assert again.status_code == 201, again.text
    assert _rules(p, p.org_a)["LinkedIn"][1] == "approved"
    assert str(_rules(p, p.org_a)["LinkedIn"][0]) == made.json()["id"], "same id"
    assert (await _run(p.org_a)).status == "ok"
    assert contact()[0] == "https://linkedin.example/A-ann", "backfilled again"

    draft = (await _draft(p.org_a, _remove("LinkedIn", entity="contacts"))).json()
    refused = await _restore(p.org_a, draft["version"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "not_restorable"
    assert (await _restore(p.org_a, 999)).status_code == 404


async def test_5_a_restore_checks_again_a_rule_that_is_not_legal_now(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Why_Lost", "lost_note")).status_code == 201
    with_rule = (await _plans(p.org_a))["approved"]
    rule_id = _rules(p, p.org_a)["Why_Lost"][0]
    assert (await _patch(p.org_a, str(rule_id), "rejected")).status_code == 200
    # Zoho drops the field, so the rule is not legal now.
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_field_defs WHERE organization_id = "
                       "CAST(:o AS uuid) AND key = 'Why_Lost'"), {"o": p.org_a})
    count = len(_versions(p, p.org_a))
    refused = await _restore(p.org_a, with_rule)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["field_api_name"] == "Why_Lost"
    assert len(_versions(p, p.org_a)) == count, "the refused restore wrote nothing"
    assert _rule_status(p, str(rule_id)) == "rejected"


# ── 6. A per-rule change writes a new approved version ───────────────────────


async def test_6_a_per_rule_post_or_patch_writes_a_new_approved_version(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    made = await _post(p.org_a, "Custom_Industry", "industry")
    assert made.status_code == 201, made.text
    assert made.json()["proposed_by"] == "admin"
    body = await _plans(p.org_a)
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert _identity(await _approved(p.org_a, body)) == {("lead", "Custom_Industry", "industry")}
    assert _versions(p, p.org_a)[1][1:] == ("approved", ADMIN, ADMIN, 1)

    rejected = await _patch(p.org_a, made.json()["id"], "rejected")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    body = await _plans(p.org_a)
    assert body["approved"] == 3 and _identity(await _approved(p.org_a, body)) == set()

    # An AI proposal: the approve writes a version and keeps who proposed it.
    with p.admin_engine.begin() as c:
        proposed = str(c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by, confidence, reason) VALUES (CAST(:o AS uuid), 'zoho', 'lead', "
            "'Why_Lost', 'textarea', 1, 'lost_note', 'proposed', 'ai', 0.8, "
            "'A guess.') RETURNING id"), {"o": p.org_a}).scalar())
        other = str(c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', 'Headcount', "
            "'integer', 1, 'no_of_employees', 'proposed', 'ai') RETURNING id"),
            {"o": p.org_a}).scalar())
    assert (await _plans(p.org_a))["approved"] == 3, "a proposal writes no version"
    ok = await _patch(p.org_a, proposed, "approved")
    assert ok.status_code == 200, ok.text
    assert (ok.json()["status"], ok.json()["proposed_by"], ok.json()["confidence"]) == (
        "approved", "ai", 0.8)
    body = await _plans(p.org_a)
    assert body["approved"] == 4
    assert _identity(await _approved(p.org_a, body)) == {("lead", "Why_Lost", "lost_note")}
    # A reject of a rule that is not approved writes no version.
    assert (await _patch(p.org_a, other, "rejected")).status_code == 200
    assert (await _plans(p.org_a))["approved"] == 4
    assert len(_versions(p, p.org_a)) == 4


async def test_6_the_plan_follows_a_rule_that_the_sync_made_stale(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "industry"), ("Why_Lost", "lost_note")):
        assert (await _post(p.org_a, field, column)).status_code == 201
    # Zoho drops the field, so the next discovery makes the rule stale.
    a.fields["Leads"] = [f for f in a.fields["Leads"] if f["api_name"] != "Why_Lost"]
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_source_profiles SET fetched_at = now() - "
                       "interval '25 hours' WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": p.org_a})
    assert (await _run(p.org_a)).status == "ok"
    assert _rules(p, p.org_a)["Why_Lost"][1] == "stale"

    body = await _plans(p.org_a)
    plan = await _approved(p.org_a, body)
    assert plan["created_by"] == "system"
    assert _identity(plan) == {("lead", "Custom_Industry", "industry")}
    # An unrelated change still works, and keeps the stale row as it is.
    assert (await _post(p.org_a, "Headcount", "no_of_employees")).status_code == 201
    assert _rules(p, p.org_a)["Why_Lost"][1] == "stale"


# ── 7. Admin only, Mirror only, dark, and the org from the server ────────────


async def test_7_each_route_is_admin_only_mirror_only_and_dark(fr, monkeypatch) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    draft = (await _draft(p.org_a, _put("Why_Lost", "lost_note"))).json()
    count = len(_versions(p, p.org_a))
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    member = _member_of(p.org_a)
    assert (await _req(member, "GET", "/crm/fields/leads")).status_code == 200
    calls = (
        ("GET", PLANS, None),
        ("POST", PLANS, {"changes": [_put("Headcount", "no_of_employees")]}),
        ("POST", f"{PLANS}/{draft['version']}/approve", None),
        ("POST", f"{PLANS}/1/restore", None),
    )
    for method, path, body in calls:
        assert (await _req(member, method, path, body)).status_code == 403, path
    assert len(_versions(p, p.org_a)) == count

    # A body that names an organization is refused (R11).
    forged = await _req(_admin_of(p.org_a), "POST", PLANS, {
        "changes": [_put("Headcount", "no_of_employees")], "organization_id": p.org_b})
    assert forged.status_code == 422
    forged_change = await _req(_admin_of(p.org_a), "POST", PLANS, {"changes": [
        {**_put("Headcount", "no_of_employees"), "organization_id": p.org_b}]})
    assert forged_change.status_code == 422

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    for method, path, body in calls:
        got = await _req(_admin_of(p.org_a), method, path, body)
        assert got.status_code == 409, path
        assert got.json()["detail"]["code"] == "not_mirror"

    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    for method, path, body in calls:
        assert (await _req(_admin_of(p.org_a), method, path, body)).status_code == 404
    assert len(_versions(p, p.org_a)) == count


# ── 8. Orgs stay apart ───────────────────────────────────────────────────────


async def test_8_orgs_stay_apart(fr, app_engine) -> None:
    p, a, b = fr
    _assert_non_priv(app_engine)
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    draft = (await _draft(p.org_a, _put("Why_Lost", "lost_note"))).json()

    seen_b = await _plans(p.org_b)
    assert [v["version"] for v in seen_b["versions"]] == [1]
    assert _identity(await _approved(p.org_b, seen_b)) == set()
    assert (await _approve(p.org_b, draft["version"])).status_code == 404
    assert (await _restore(p.org_b, 2)).status_code == 404
    assert _versions(p, p.org_a)[-1][1] == "draft", "B moved nothing of A"
    assert _count(p, "crm_field_rules", p.org_b) == 0

    def versions_as(org: str | None) -> list[int]:
        fresh = create_engine(p.app_url, poolclass=NullPool)
        try:
            with fresh.begin() as c:
                if org is not None:
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": org})
                return sorted(int(r[0]) for r in c.execute(text(
                    "SELECT version FROM crm_mapping_plans")).all())
        finally:
            fresh.dispose()

    assert versions_as(p.org_a) == [1, 2, 3]
    assert versions_as(p.org_b) == [1]
    assert versions_as(None) == [], "no tenant bound reads nothing"
    with app_engine.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": p.org_b})
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, created_by) "
            "VALUES (CAST(:o AS uuid), 9, 'draft', 'x')"), {"o": p.org_a})
    # B's approve and B's sync apply none of A's rules.
    assert (await _run(p.org_b)).status == "ok"
    assert _leads(p, p.org_b)["l1"][1] is None


# ── Fix round: the list, the lock order, and the lock before the profile ────


@contextmanager
def _recorded() -> Iterator[list[str]]:
    """Every SQL statement that the shared engine sends while the block runs,
    with its white space folded."""
    from acb_common import db as shared
    from sqlalchemy import event

    seen: list[str] = []
    engine = shared.get_engine().sync_engine

    def _record(_conn: Any, _cur: Any, statement: str, *_rest: Any) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _record)


#: The ordered lock of every rule row (``core.FIELD_RULE_LOCK_SQL``).
LOCK_ALL = "FROM crm_field_rules WHERE ext_source = "
LOCK_TAIL = "ORDER BY entity, field_api_name FOR UPDATE"


def _first(seen: list[str], *parts: str) -> int:
    hits = [i for i, s in enumerate(seen) if all(p in s for p in parts)]
    assert hits, f"no statement holds {parts}"
    return hits[0]


async def test_fix_the_list_carries_no_snapshot_and_one_version_does(fr) -> None:
    p, a, b = fr
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    for field, column in (("Custom_Industry", "industry"), ("Why_Lost", "lost_note")):
        assert (await _post(p.org_a, field, column)).status_code == 201

    body = await _plans(p.org_a)
    assert [v["version"] for v in body["versions"]] == [3, 2, 1]
    for item in body["versions"]:
        assert "rules" not in item, "the list carries no snapshot"
    assert [v["rule_count"] for v in body["versions"]] == [2, 1, 0]
    one = await _req(_admin_of(p.org_a), "GET", f"{PLANS}/2")
    assert one.status_code == 200, one.text
    assert _identity(one.json()) == {("lead", "Custom_Industry", "industry")}
    assert (await _req(_admin_of(p.org_a), "GET", f"{PLANS}/99")).status_code == 404
    assert (await _req(_member_of(p.org_a), "GET", f"{PLANS}/2")).status_code == 403
    # Org B has its own version 1 only. A's version 2 is not there.
    await _plans(p.org_b)
    assert (await _req(_admin_of(p.org_b), "GET", f"{PLANS}/2")).status_code == 404


async def test_fix_mark_stale_locks_every_rule_row_in_the_one_order_first(fr) -> None:
    """The sync and the approve lock the rule rows in ONE order, so neither
    waits on the other in a cycle (reviewer P2, a 40P01 deadlock)."""
    from gateway.routes.crm import core, mapping_plans

    assert core.FIELD_RULE_LOCK_SQL.endswith(LOCK_TAIL)
    assert mapping_plans._LIVE_LOCK_SQL is core.FIELD_RULE_LOCK_SQL

    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "description"), ("Why_Lost", "lost_note")):
        assert (await _post(p.org_a, field, column)).status_code == 201
    a.fields["Leads"] = [
        f for f in a.fields["Leads"] if f["api_name"] not in ("Why_Lost", "Custom_Industry")]
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_source_profiles SET fetched_at = now() - "
                       "interval '25 hours' WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": p.org_a})
    with _recorded() as seen:
        assert (await _run(p.org_a)).status == "ok"
    stale = _first(seen, "UPDATE crm_field_rules SET status = 'stale'")
    locked = [i for i, s in enumerate(seen) if LOCK_ALL in s and s.endswith(LOCK_TAIL)]
    assert locked and locked[0] < stale, "mark_stale wrote before the ordered lock"
    assert {r[1] for r in _rules(p, p.org_a).values()} == {"stale"}


async def test_fix_a_patch_takes_the_ordered_lock_before_its_one_row_hold(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        proposed = str(c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', 'Why_Lost', "
            "'textarea', 1, 'lost_note', 'proposed', 'ai') RETURNING id"),
            {"o": p.org_a}).scalar())
    with _recorded() as seen:
        ok = await _patch(p.org_a, proposed, "approved")
    assert ok.status_code == 200, ok.text
    hold = _first(seen, "FROM crm_field_rules WHERE id = ", "IS NOT DISTINCT FROM",
                  "FOR UPDATE")
    lock = _first(seen, LOCK_ALL, LOCK_TAIL)
    assert lock < hold, "the one-row hold came before the ordered lock"


async def test_fix_an_approve_locks_the_rule_rows_before_it_reads_the_profile(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    draft = (await _draft(p.org_a, _put("Why_Lost", "lost_note"))).json()
    with _recorded() as seen:
        done = await _approve(p.org_a, draft["version"])
    assert done.status_code == 200, done.text
    lock = _first(seen, LOCK_ALL, LOCK_TAIL)
    profile = _first(seen, "FROM crm_source_profiles")
    assert lock < profile, "commit read the profile before it locked the rule rows"
