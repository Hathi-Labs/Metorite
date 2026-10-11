"""R8 — ``GET /crm/sync/health``, against a real Postgres (WS-53 CRM-Q9).

The route is read only. These tests prove, with two organizations and the
non-privileged role:

- the shape of the answer after a real run (entities, modules, history, credits);
- the route is for an admin: a member gets 403;
- one organization never sees the counts, the runs or the modules of the other;
- a key in ``counts`` that the route does not know passes through;
- no token and no record content reach the answer (sentinel).

It reuses the fake Zoho and the fixtures of ``test_crm_pipelines_r8``.
"""

# ruff: noqa: F811  (q and ns are imported fixtures)

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_pipelines_r8 import (  # noqa: F401  (q and ns are fixtures)
    SENTINEL,
    _admin,
    _member,
    _req,
    _sync,
    ns,
    q,
)
from tests.unit.test_crm_sync_engine_r8 import seed_basic
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see the sibling suites).
pytestmark = _DB_GATE

ENTITY_KEYS = ["organizations", "contacts", "leads", "deals", "activities"]


async def test_the_health_route_has_the_contract_shape(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {
        "last_run", "last_success_at", "next_run_at", "entities", "modules",
        "history", "credits"}
    assert set(body["last_run"]) == {
        "kind", "status", "started_at", "finished_at", "error", "credits_used"}
    assert body["last_run"]["status"] == "ok" and body["last_run"]["error"] is None
    assert body["last_success_at"] and body["next_run_at"]
    assert [e["key"] for e in body["entities"]][:5] == ENTITY_KEYS
    for entity in body["entities"]:
        assert set(entity) == {"key", "zoho_count", "ours", "last_run"}
        assert {"seen", "failed"} <= set(entity["last_run"])
    # Zoho's count is the estimate, ours is the local rows with ext_source = zoho.
    by_key = {e["key"]: e for e in body["entities"]}
    assert by_key["organizations"]["zoho_count"] == 5 and by_key["organizations"]["ours"] == 5
    assert by_key["contacts"]["zoho_count"] == 2 and by_key["contacts"]["ours"] == 2
    assert by_key["organizations"]["last_run"]["seen"] == 5
    assert {m["api_name"] for m in body["modules"]} >= {"Deals", "Installs"}
    assert len(body["history"]) == 1 and body["history"][0]["kind"] == "import"
    assert body["credits"]["budget_24h"] > 0
    assert body["credits"]["used_24h"] >= 0


async def test_the_health_route_is_for_an_admin(q) -> None:
    await _sync(q, q.org_a, q.a)
    assert (await _req(_member(q.org_a), "GET", "/crm/sync/health")).status_code == 403


async def test_the_health_route_before_any_run(q) -> None:
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["last_run"] is None and body["history"] == [] and body["modules"] == []
    assert [e["key"] for e in body["entities"]] == ENTITY_KEYS
    assert all(e["ours"] == 0 and e["zoho_count"] is None for e in body["entities"])


async def test_one_organization_never_sees_the_other(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    b = await _req(_admin(q.org_b), "GET", "/crm/sync/health")
    assert b.status_code == 200
    body = b.json()
    assert body["last_run"] is None and body["history"] == []
    assert body["modules"] == [] and body["last_success_at"] is None
    assert all(e["ours"] == 0 for e in body["entities"])
    assert body["credits"]["used_24h"] == 0
    a = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    assert any(e["ours"] for e in a["entities"])


async def test_an_unknown_count_key_passes_through(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET "
            "counts = counts || CAST(:c AS jsonb), "
            "estimate = COALESCE(estimate, '{}'::jsonb) || CAST(:e AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"c": json.dumps({"tasks": {"seen": 0, "unavailable": 1, "skipped": 2}}),
             "e": json.dumps({"tasks": 7}), "o": q.org_a})
    body = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    tasks = next(e for e in body["entities"] if e["key"] == "tasks")
    assert tasks["last_run"] == {"seen": 0, "unavailable": 1, "skipped": 2}
    assert tasks["zoho_count"] == 7 and tasks["ours"] is None


async def test_no_secret_and_no_record_reach_the_answer(q) -> None:
    seed_basic(q.a, "A")
    q.a.add("Leads", "Al2", 20, Last_Name=SENTINEL, Company=SENTINEL)
    await _sync(q, q.org_a, q.a)
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert SENTINEL not in got.text
    assert "ACCESS" not in got.text and "REFRESH" not in got.text


async def test_the_health_route_is_404_with_crm_mirror_off(q, monkeypatch) -> None:
    from acb_common import get_settings

    await _sync(q, q.org_a, q.a)
    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    assert (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).status_code == 404
