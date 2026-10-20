"""R8 — stages and statuses an admin can shape, in both modes (WS-53 CRM-C3).

Spec: ``project-docs/specs/crm_platform.md`` §20.6. Migration 257 adds the
Metorite-only overlay (``hidden``, ``ui_position``, ``ui_probability``,
``ui_color``), the Zoho id of a picklist value (``ext_id``) and
``source_gone_at``. D95: one store, two writers, fenced doors. The sync writes
the source columns. Only the admin door writes the overlay.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

- a Mirror admin sets each overlay column (200), and a source field is 409;
  a Native admin gets 422 for a ``ui_*`` field (done when 1);
- a full sync keeps the overlay, the source values follow the picklist, and
  org B does not change (2); a record pull never reorders (3);
- a Zoho rename keeps the row (4); a rename that collides with a row of a
  record pull merges through the fenced door (5);
- a value that leaves Zoho is marked and never deleted, and comes back (6);
  a run with no picklist read marks nothing (7);
- the order route, and the order key after a new Zoho stage (8, 9);
- the two resets (10); the effective probability on each reader (11);
- hide in Native mode (12); a hidden stage still counts and still takes a
  move (13).

``tests/unit/test_crm_mirror_readonly.py`` and
``tests/unit/test_crm_upsert_from_source_fence.py`` hold the static fences.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_pipelines_r8 import Q1Zoho
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401  (ns is a fixture)
    _assert_non_priv,
    _connect,
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


def _stages() -> list[dict[str, Any]]:
    return [
        {"id": "S1", "actual_value": "Qualify", "sequence_number": 1, "probability": 10,
         "forecast_category": {"name": "Pipeline"}},
        {"id": "S2", "actual_value": "Propose", "sequence_number": 2, "probability": 40,
         "forecast_category": {"name": "Pipeline"}},
        {"id": "S3", "actual_value": "Won It", "sequence_number": 3, "probability": 100,
         "forecast_category": {"name": "Closed Won"}},
        {"id": "S4", "actual_value": "Gone", "sequence_number": 4,
         "forecast_category": {"name": "Closed Lost"}},
    ]


class C3Zoho(Q1Zoho):
    """The fake Zoho of ``test_crm_pipelines_r8``, with picklist ids.

    It answers the discovery reads, so a run stores a source profile and the
    24-hour discovery cadence applies. It lists no pipeline, so no stage
    arrives from a pipeline.
    """

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        super().__init__(tag, api_host, accounts_host)
        self.pipelines = {"pipeline": []}
        self.stages = _stages()
        self.lead_statuses: list[dict[str, Any]] = [
            {"id": "L1", "actual_value": "Fresh", "sequence_number": 1},
            {"id": "L2", "actual_value": "Contacted", "sequence_number": 2},
        ]
        #: Modules whose field read answers 500.
        self.fields_fail: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        module = request.url.params.get("module", "")
        if request.url.path.endswith("/settings/fields") and module in self.fields_fail:
            return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
        return super().handle(request)

    def _fields(self, module: str) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = [{
            "api_name": "Region", "field_label": "Region", "data_type": "text",
            "sequence_number": 90, "visible": True}]
        if module == "Deals":
            fields.append({"api_name": "Stage", "data_type": "picklist",
                           "pick_list_values": self.stages})
        if module == "Leads":
            fields.append({"api_name": "Lead_Status", "data_type": "picklist",
                           "pick_list_values": self.lead_statuses})
        return fields


@pytest.fixture
async def c3(ns, monkeypatch):  # noqa: F811 — a fixture
    """Two fake Zoho tenants wired under the engine, and the extra tables cleaned."""
    from gateway.crm_sync import engine

    a = C3Zoho("A", "www.zohoapis.in", "accounts.zoho.in")
    b = C3Zoho("B", "www.zohoapis.eu", "accounts.zoho.eu")

    def transport(request: httpx.Request) -> httpx.Response:
        for z in (a, b):
            if request.url.host in (z.api_host, z.accounts_host):
                return z.handle(request)
        raise AssertionError(f"a call to an unknown host {request.url.host}")

    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(transport))
    yield SimpleNamespace(p=ns, a=a, b=b, org_a=ns.org_a, org_b=ns.org_b)
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            for table in (
                "crm_views", "crm_status_changes", "crm_pipeline_stages",
                "crm_pipelines", "crm_source_profiles", "crm_lost_reasons",
            ):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-sync.test", org, "feature:crm", "admin:access:manage")


def _member(org: str) -> Any:
    return _principal("member@crm-sync.test", org, "feature:crm")


async def _req(user: Any, method: str, path: str, body: Any = None) -> Any:
    """One request through the real ``/crm`` router, as *user*."""
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
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
        ) as client:
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def _sync(c3: Any, org: str, z: C3Zoho, *, connect: bool = True) -> Any:
    from gateway.crm_sync import engine

    if connect:
        await _connect(c3.p, org, z)
    result = await engine.run_org(org)
    assert result.status == "ok", result
    return result


async def _picklist_run(c3: Any, org: str, z: C3Zoho) -> Any:
    """A run that reads the picklist again: the deal cursor starts its import over."""
    with c3.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET initial_done_at = NULL "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:deal'"),
            {"o": org})
    result = await _sync(c3, org, z, connect=False)
    assert result.kind == "import", result
    return result


_ROW_SQL = (
    "SELECT id, name, position, probability, color, hidden, ui_position, "
    "ui_probability, ui_color, max_dwell_days, ext_id, source_gone_at "
    "FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid)"
)


def _deal_rows(p: Any, org: str) -> dict[str, Any]:
    return {r.name: r for r in _q(p, _ROW_SQL, o=org)}


def _lead_rows(p: Any, org: str) -> dict[str, Any]:
    return {r.name: r for r in _q(
        p, "SELECT id, name, position, probability, hidden, ui_position, "
           "ui_probability, ui_color, ext_id, "
           "source_gone_at FROM crm_lead_statuses "
           "WHERE organization_id = CAST(:o AS uuid)", o=org)}


def _deal(z: C3Zoho, ident: str, minute: int, **fields: Any) -> None:
    z.add("Deals", ident, minute, **{"Deal_Name": f"Deal {ident}", **fields})


def _deal_status(p: Any, org: str, ext_id: str) -> str:
    return str(_q(p, "SELECT status_id FROM crm_deals WHERE organization_id = "
                     "CAST(:o AS uuid) AND ext_id = :x", o=org, x=ext_id)[0][0])


def _native(p: Any, org: str) -> dict[str, str]:
    """A Native org with four deal stages, two lead statuses and one deal."""
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode) VALUES "
            "(CAST(:o AS uuid), 'native') ON CONFLICT (organization_id) "
            "DO UPDATE SET mode = EXCLUDED.mode"), {"o": org})
        for key, name, kind, pos, default, prob in (
            ("open", "Open", "open", 0, True, 10), ("next", "Next", "open", 10, False, 50),
            ("won", "Won", "won", 20, False, 100), ("lost", "Lost", "lost", 30, False, 0),
        ):
            ids[key] = str(c.execute(text(
                "INSERT INTO crm_deal_statuses (organization_id, name, type, position, "
                "is_default, probability) VALUES (CAST(:o AS uuid), :n, :t, :p, :d, :pr) "
                "RETURNING id"), {"o": org, "n": name, "t": kind, "p": pos, "d": default,
                                  "pr": prob}).scalar())
        for key, name, pos, default in (("new", "New", 0, True), ("working", "Working", 10, False)):
            ids[key] = str(c.execute(text(
                "INSERT INTO crm_lead_statuses (organization_id, name, type, position, "
                "is_default) VALUES (CAST(:o AS uuid), :n, 'open', :p, :d) RETURNING id"),
                {"o": org, "n": name, "p": pos, "d": default}).scalar())
        ids["deal"] = str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, amount) VALUES "
            "(CAST(:o AS uuid), 'Native deal', CAST(:s AS uuid), 500) RETURNING id"),
            {"o": org, "s": ids["open"]}).scalar())
    return ids


# ── 1. The overlay door ──────────────────────────────────────────────────────


async def test_a_mirror_admin_sets_each_overlay_column_and_a_source_field_is_409(
    c3, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    _deal(c3.a, "d1", 1, Stage="Qualify")
    c3.a.add("Leads", "l1", 2, Last_Name="Lead", Lead_Status="Fresh")
    await _sync(c3, c3.org_a, c3.a)
    with c3.p.admin_engine.begin() as c:
        lost = str(c.execute(text(
            "INSERT INTO crm_lost_reasons (organization_id, label, position) VALUES "
            "(CAST(:o AS uuid), 'Price', 0) RETURNING id"), {"o": c3.org_a}).scalar())
    deal = _deal_rows(c3.p, c3.org_a)["Qualify"]
    lead = _lead_rows(c3.p, c3.org_a)["Fresh"]
    admin = _admin(c3.org_a)

    for body in ({"hidden": True}, {"ui_position": 5}, {"ui_probability": 33},
                 {"ui_color": "violet"}, {"max_dwell_days": 7}):
        r = await _req(admin, "PATCH", f"/crm/statuses/deal/{deal.id}", body)
        assert r.status_code == 200, (body, r.text)
    for body in ({"hidden": True}, {"ui_position": 2}, {"ui_probability": 25},
                 {"ui_color": "teal"}):
        r = await _req(admin, "PATCH", f"/crm/statuses/lead/{lead.id}", body)
        assert r.status_code == 200, (body, r.text)
    r = await _req(admin, "PATCH", f"/crm/lost-reasons/{lost}", {"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True, r.text

    row = _deal_rows(c3.p, c3.org_a)["Qualify"]
    assert (row.hidden, row.ui_position, row.ui_probability, row.ui_color,
            row.max_dwell_days) == (True, 5, 33, "violet", 7)
    assert (row.probability, row.color) == (10, "gray")  # the source is untouched
    lead_row = _lead_rows(c3.p, c3.org_a)["Fresh"]
    assert (lead_row.hidden, lead_row.ui_position, lead_row.ui_color) == (True, 2, "teal")
    # CRM-U6, migration 285: a lead status takes the probability overlay too.
    assert lead_row.ui_probability == 25 and lead_row.probability == 0

    # The wire carries the effective value and the source value.
    wire = (await _req(admin, "PATCH", f"/crm/statuses/deal/{deal.id}", {})).json()
    assert (wire["probability"], wire["source_probability"], wire["ui_probability"]) == (
        33, 10, 33)
    assert (wire["color"], wire["source_color"], wire["ui_color"]) == ("violet", "gray",
                                                                     "violet")
    assert wire["hidden"] is True and wire["ext_id"] == "S1"

    for path, body in (
        (f"/crm/statuses/deal/{deal.id}", {"name": "X"}),
        (f"/crm/statuses/deal/{deal.id}", {"probability": 50}),
        (f"/crm/statuses/deal/{deal.id}", {"ui_probability": 20, "color": "red"}),
        (f"/crm/statuses/lead/{lead.id}", {"probability": 5}),
        (f"/crm/lost-reasons/{lost}", {"label": "Z"}),
    ):
        r = await _req(admin, "PATCH", path, body)
        assert r.status_code == 409, (body, r.text)
        assert r.json()["detail"]["code"] == "mirror_mode_settings"
    assert _deal_rows(c3.p, c3.org_a)["Qualify"].ui_probability == 33  # nothing moved
    member = await _req(_member(c3.org_a), "PATCH", f"/crm/statuses/deal/{deal.id}",
                        {"hidden": False})
    assert member.status_code == 403


async def test_a_native_admin_gets_422_for_a_ui_field(c3) -> None:
    ids = _native(c3.p, c3.org_a)
    admin = _admin(c3.org_a)
    for body in ({"ui_probability": 5}, {"ui_color": "red"}, {"ui_position": 1}):
        r = await _req(admin, "PATCH", f"/crm/statuses/deal/{ids['next']}", body)
        assert r.status_code == 422, (body, r.text)
    r = await _req(admin, "PATCH", f"/crm/statuses/lead/{ids['working']}",
                   {"ui_color": "red"})
    assert r.status_code == 422, r.text
    # The source fields stay editable, as before.
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{ids['next']}",
                   {"probability": 60, "color": "blue"})
    assert r.status_code == 200 and r.json()["probability"] == 60, r.text


# ── 2 and 3. The sync keeps the source values and never the overlay ──────────


async def test_a_full_sync_keeps_the_overlay_and_follows_the_picklist(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Qualify")
    _deal(c3.b, "d1", 1, Stage="Qualify")
    await _sync(c3, c3.org_a, c3.a)
    await _sync(c3, c3.org_b, c3.b)
    before = _deal_rows(c3.p, c3.org_a)
    assert [before[n].position for n in ("Qualify", "Propose", "Won It", "Gone")] == [
        0, 1, 2, 3]
    assert [before[n].ext_id for n in ("Qualify", "Propose", "Won It", "Gone")] == [
        "S1", "S2", "S3", "S4"]
    b_before = sorted(tuple(r) for r in _q(c3.p, _ROW_SQL, o=c3.org_b))
    admin = _admin(c3.org_a)
    qualify = str(before["Qualify"].id)
    overlay = {"hidden": True, "ui_position": 70, "ui_probability": 33,
               "ui_color": "violet", "max_dwell_days": 9}
    for column, value in overlay.items():
        r = await _req(admin, "PATCH", f"/crm/statuses/deal/{qualify}", {column: value})
        assert r.status_code == 200, r.text

    # Zoho reorders the stages and changes a probability.
    c3.a.stages[0].update(sequence_number=2, probability=15)
    c3.a.stages[1].update(sequence_number=1)
    _deal(c3.a, "d2", 5, Stage="Qualify")  # a record pull AFTER the picklist
    await _picklist_run(c3, c3.org_a, c3.a)

    after = _deal_rows(c3.p, c3.org_a)
    q = after["Qualify"]
    assert str(q.id) == qualify
    assert (q.position, q.probability) == (1, 15)
    assert after["Propose"].position == 0
    assert (q.hidden, q.ui_position, q.ui_probability, q.ui_color, q.max_dwell_days) == (
        True, 70, 33, "violet", 9)
    assert after["Propose"].probability == 40
    # Org B did not change.
    assert sorted(tuple(r) for r in _q(c3.p, _ROW_SQL, o=c3.org_b)) == b_before


async def test_record_pulls_do_not_reorder(c3) -> None:
    from gateway.crm_sync import engine

    _deal(c3.a, "d1", 1, Stage="Propose")
    await _sync(c3, c3.org_a, c3.a)
    picklist = {n: (r.position, r.probability) for n, r in _deal_rows(c3.p, c3.org_a).items()}
    # A later picklist that Zoho changed is NOT read by an incremental run, and
    # the deals it pulls name a known stage and a new one.
    c3.a.stages[0].update(probability=77)
    _deal(c3.a, "d2", 5, Stage="Qualify")
    _deal(c3.a, "d3", 6, Stage="Surprise")
    result = await engine.run_org(c3.org_a)
    assert (result.status, result.kind) == ("ok", "incremental")
    rows = _deal_rows(c3.p, c3.org_a)
    for name, (position, probability) in picklist.items():
        assert (rows[name].position, rows[name].probability) == (position, probability), name
    assert rows["Surprise"].position == max(p for p, _ in picklist.values()) + 1
    assert rows["Surprise"].ext_id is None


# ── 4 and 5. A Zoho rename, and a rename that collides ───────────────────────


async def test_a_zoho_rename_keeps_the_row_its_overlay_and_its_deals(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Qualify")
    await _sync(c3, c3.org_a, c3.a)
    qualify = str(_deal_rows(c3.p, c3.org_a)["Qualify"].id)
    r = await _req(_admin(c3.org_a), "PATCH", f"/crm/statuses/deal/{qualify}",
                   {"ui_probability": 33})
    assert r.status_code == 200, r.text
    c3.a.stages[0]["actual_value"] = "Qualified"
    await _picklist_run(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    assert "Qualify" not in rows
    assert (str(rows["Qualified"].id), rows["Qualified"].ui_probability) == (qualify, 33)
    assert _deal_status(c3.p, c3.org_a, "d1") == qualify


async def test_a_rename_that_collides_merges_into_the_row_with_the_zoho_id(c3) -> None:
    from gateway.crm_sync import engine

    _deal(c3.a, "d1", 1, Stage="Qualify")
    await _sync(c3, c3.org_a, c3.a)
    qualify = str(_deal_rows(c3.p, c3.org_a)["Qualify"].id)
    r = await _req(_admin(c3.org_a), "PATCH", f"/crm/statuses/deal/{qualify}",
                   {"ui_probability": 33, "ui_color": "violet"})
    assert r.status_code == 200, r.text
    # Zoho renames the value. A record pull reads the new name first.
    c3.a.stages[0]["actual_value"] = "Qualified"
    _deal(c3.a, "d2", 5, Stage="Qualified")
    assert (await engine.run_org(c3.org_a)).kind == "incremental"
    named = _deal_rows(c3.p, c3.org_a)["Qualified"]
    assert named.ext_id is None and str(named.id) != qualify
    assert _deal_status(c3.p, c3.org_a, "d2") == str(named.id)
    # Two pipelines and a saved view name the row of the record pull.
    with c3.p.admin_engine.begin() as c:
        both, only = (str(c.execute(text(
            "INSERT INTO crm_pipelines (organization_id, name, position) VALUES "
            "(CAST(:o AS uuid), :n, :p) RETURNING id"),
            {"o": c3.org_a, "n": n, "p": i}).scalar()) for i, n in enumerate(("Both", "Only")))
        for pipeline, status, position in (
            (both, qualify, 0), (both, str(named.id), 3), (only, str(named.id), 1),
        ):
            c.execute(text(
                "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, "
                "position) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), "
                ":pos)"), {"o": c3.org_a, "p": pipeline, "s": status, "pos": position})
        view = str(c.execute(text(
            "INSERT INTO crm_views (organization_id, entity, name, owner_email, filters) "
            "VALUES (CAST(:o AS uuid), 'deals', 'Mine', 'admin@crm-sync.test', "
            "CAST(:f AS jsonb)) RETURNING id"),
            {"o": c3.org_a, "f": f'{{"status": "{named.id}", "q": "x"}}'}).scalar())

    await _picklist_run(c3, c3.org_a, c3.a)

    rows = _deal_rows(c3.p, c3.org_a)
    kept = rows["Qualified"]
    assert str(kept.id) == qualify and kept.ext_id == "S1"
    assert (kept.ui_probability, kept.ui_color) == (33, "violet")
    assert _q(c3.p, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
              i=str(named.id))[0][0] == 0
    assert _deal_status(c3.p, c3.org_a, "d1") == qualify
    assert _deal_status(c3.p, c3.org_a, "d2") == qualify
    members = {
        (str(r[0]), str(r[1]), r[2]) for r in _q(
            c3.p, "SELECT pipeline_id, status_id, position FROM crm_pipeline_stages "
                  "WHERE organization_id = CAST(:o AS uuid)", o=c3.org_a)}
    assert members == {(both, qualify, 0), (only, qualify, 1)}
    filters = _q(c3.p, "SELECT filters FROM crm_views WHERE id = CAST(:i AS uuid)",
                 i=view)[0][0]
    assert filters == {"status": qualify, "q": "x"}


async def test_a_merge_that_fails_is_never_swallowed(c3, monkeypatch) -> None:
    """Done when 5: a failure fails the picklist step, and the run says so."""
    from gateway.crm_sync import engine

    _deal(c3.a, "d1", 1, Stage="Qualify")
    await _sync(c3, c3.org_a, c3.a)
    c3.a.stages[0]["actual_value"] = "Qualified"
    _deal(c3.a, "d2", 5, Stage="Qualified")
    await engine.run_org(c3.org_a)

    async def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("merge broke")

    monkeypatch.setattr(engine, "merge_status_from_source", broken)
    with c3.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET initial_done_at = NULL "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:deal'"),
            {"o": c3.org_a})
    result = await engine.run_org(c3.org_a)
    assert result.status == "failed", result
    rows = _deal_rows(c3.p, c3.org_a)
    assert {"Qualify", "Qualified"} <= set(rows)  # the whole step rolled back


# ── 6 and 7. A value that leaves Zoho ────────────────────────────────────────


async def test_a_value_that_leaves_zoho_is_marked_and_comes_back(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Gone")
    await _sync(c3, c3.org_a, c3.a)
    gone = str(_deal_rows(c3.p, c3.org_a)["Gone"].id)
    c3.a.stages = [s for s in c3.a.stages if s["id"] != "S4"]
    await _picklist_run(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    assert rows["Gone"].source_gone_at is not None
    assert all(rows[n].source_gone_at is None for n in ("Qualify", "Propose", "Won It"))
    assert _deal_status(c3.p, c3.org_a, "d1") == gone
    listed = (await _req(_admin(c3.org_a), "GET", "/crm/statuses/deal")).json()
    assert next(s for s in listed if s["id"] == gone)["source_gone_at"]

    c3.a.stages = _stages()
    await _picklist_run(c3, c3.org_a, c3.a)
    assert _deal_rows(c3.p, c3.org_a)["Gone"].source_gone_at is None


async def test_a_run_with_no_picklist_read_marks_nothing(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Qualify")
    _deal(c3.a, "d2", 2, Stage="Pipeline Only")  # a row with no Zoho id
    _deal(c3.a, "d3", 3)  # no stage: UNKNOWN_STATUS
    await _sync(c3, c3.org_a, c3.a)
    assert {"Pipeline Only", "Unknown"} <= set(_deal_rows(c3.p, c3.org_a))
    c3.a.stages = [s for s in c3.a.stages if s["id"] != "S2"]
    c3.a.fields_fail.add("Deals")
    await _picklist_run(c3, c3.org_a, c3.a)
    assert all(r.source_gone_at is None for r in _deal_rows(c3.p, c3.org_a).values())

    c3.a.fields_fail.clear()
    await _picklist_run(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    assert rows["Propose"].source_gone_at is not None
    for name in ("Pipeline Only", "Unknown", "Qualify"):
        assert rows[name].source_gone_at is None, name


# ── 8 and 9. The order route and the order key ───────────────────────────────


async def test_the_order_route_in_mirror_and_native_mode(c3) -> None:
    await _sync(c3, c3.org_a, c3.a)
    ids_b = _native(c3.p, c3.org_b)
    rows = _deal_rows(c3.p, c3.org_a)
    order = [str(rows[n].id) for n in ("Gone", "Won It", "Propose", "Qualify")]
    admin = _admin(c3.org_a)
    r = await _req(admin, "PUT", "/crm/statuses/deal/order", {"ids": order})
    assert r.status_code == 200, r.text
    assert [s["id"] for s in r.json()] == order
    after = _deal_rows(c3.p, c3.org_a)
    assert all(row.ui_position is not None for row in after.values())
    assert [after[n].position for n in ("Qualify", "Propose", "Won It", "Gone")] == [
        0, 1, 2, 3]  # the source order is untouched

    for bad in (order[:-1], [*order, str(uuid.uuid4())], [*order[:-1], ids_b["open"]],
                [*order[:-1], order[0]], [*order, "not-a-uuid"]):
        r = await _req(admin, "PUT", "/crm/statuses/deal/order", {"ids": bad})
        assert r.status_code == 422, (bad, r.text)
    assert (await _req(_member(c3.org_a), "PUT", "/crm/statuses/deal/order",
                       {"ids": order})).status_code == 403

    native = [ids_b[k] for k in ("lost", "won", "next", "open")]
    r = await _req(_admin(c3.org_b), "PUT", "/crm/statuses/deal/order", {"ids": native})
    assert r.status_code == 200, r.text
    b_rows = _deal_rows(c3.p, c3.org_b)
    assert [b_rows[n].position for n in ("Lost", "Won", "Next", "Open")] == [0, 10, 20, 30]
    assert all(row.ui_position is None for row in b_rows.values())


async def test_a_new_zoho_stage_sorts_after_the_admin_order(c3) -> None:
    await _sync(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    order = [str(rows[n].id) for n in ("Propose", "Qualify", "Gone", "Won It")]
    admin = _admin(c3.org_a)
    assert (await _req(admin, "PUT", "/crm/statuses/deal/order",
                       {"ids": order})).status_code == 200
    c3.a.stages.insert(0, {"id": "S0", "actual_value": "Discover", "sequence_number": 0,
                           "probability": 5})
    await _picklist_run(c3, c3.org_a, c3.a)
    listed = (await _req(admin, "GET", "/crm/statuses/deal")).json()
    assert [s["name"] for s in listed] == ["Propose", "Qualify", "Gone", "Won It", "Discover"]
    assert all(s["admin_order"] for s in listed)
    new = listed[-1]
    assert new["ui_position"] is None and new["source_position"] == 0


# ── 10. The resets ───────────────────────────────────────────────────────────


async def test_reset_to_zoho_and_reset_order(c3) -> None:
    await _sync(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    qualify = str(rows["Qualify"].id)
    admin = _admin(c3.org_a)
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{qualify}", {
        "ui_probability": 33, "ui_color": "violet", "hidden": True, "max_dwell_days": 9})
    assert r.status_code == 200, r.text
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{qualify}",
                   {"ui_probability": None, "ui_color": None})
    assert r.status_code == 200, r.text
    assert (r.json()["probability"], r.json()["color"]) == (10, "gray")
    row = _deal_rows(c3.p, c3.org_a)["Qualify"]
    assert (row.ui_probability, row.ui_color, row.hidden, row.max_dwell_days) == (
        None, None, True, 9)

    order = [str(rows[n].id) for n in ("Gone", "Won It", "Propose", "Qualify")]
    assert (await _req(admin, "PUT", "/crm/statuses/deal/order",
                       {"ids": order})).status_code == 200
    r = await _req(admin, "POST", "/crm/statuses/deal/order/reset")
    assert r.status_code == 200, r.text
    assert [s["name"] for s in r.json()] == ["Qualify", "Propose", "Won It", "Gone"]
    assert all(row.ui_position is None for row in _deal_rows(c3.p, c3.org_a).values())
    assert not any(s["admin_order"] for s in r.json())

    _native(c3.p, c3.org_b)
    r = await _req(_admin(c3.org_b), "POST", "/crm/statuses/deal/order/reset")
    assert r.status_code == 422, r.text


# ── 11. The effective probability on each reader ─────────────────────────────


async def test_the_readers_carry_the_effective_probability(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Qualify", Amount=1000)
    _deal(c3.a, "d2", 2, Stage="Qualify", Amount=1000, Probability=50)
    await _sync(c3, c3.org_a, c3.a)
    qualify = str(_deal_rows(c3.p, c3.org_a)["Qualify"].id)
    with c3.p.admin_engine.begin() as c:  # the import mapped no probability for d1
        c.execute(text("UPDATE crm_deals SET probability = NULL WHERE organization_id = "
                       "CAST(:o AS uuid) AND ext_id = 'd1'"), {"o": c3.org_a})
        pipeline = str(c.execute(text(
            "INSERT INTO crm_pipelines (organization_id, name, position, is_default) "
            "VALUES (CAST(:o AS uuid), 'Main', 0, true) RETURNING id"),
            {"o": c3.org_a}).scalar())
        c.execute(text(
            "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, "
            "position) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), 0)"),
            {"o": c3.org_a, "p": pipeline, "s": qualify})
    admin = _admin(c3.org_a)
    assert (await _req(admin, "PATCH", f"/crm/statuses/deal/{qualify}",
                       {"ui_probability": 30})).status_code == 200

    board = (await _req(admin, "GET", "/crm/pipeline")).json()
    lane = next(x for x in board["lanes"] if x["status"]["id"] == qualify)
    assert lane["status"]["probability"] == 30
    assert lane["weighted"] == pytest.approx(1000 * 30 / 100 + 1000 * 50 / 100)
    report = (await _req(admin, "GET", "/crm/reports/pipeline")).json()
    stage = next(x for x in report["stages"] if x["status"]["id"] == qualify)
    assert stage["status"]["probability"] == 30
    assert stage["weighted"] == pytest.approx(800)
    pipelines = (await _req(admin, "GET", "/crm/pipelines")).json()
    main = next(p for p in pipelines if p["id"] == pipeline)
    assert [(s["id"], s["probability"]) for s in main["stages"]] == [(qualify, 30)]
    listed = (await _req(admin, "GET", "/crm/statuses/deal")).json()
    assert next(s for s in listed if s["id"] == qualify)["probability"] == 30


# ── 12 and 13. Hide ──────────────────────────────────────────────────────────


async def test_a_native_hide_and_the_rows_that_must_stay(c3) -> None:
    ids = _native(c3.p, c3.org_a)
    admin = _admin(c3.org_a)
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{ids['next']}", {"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True, r.text
    for kind, key in (("deal", "open"), ("deal", "won"), ("deal", "lost"), ("lead", "new")):
        r = await _req(admin, "PATCH", f"/crm/statuses/{kind}/{ids[key]}", {"hidden": True})
        assert r.status_code == 422, (key, r.text)
    r = await _req(admin, "PATCH", f"/crm/statuses/lead/{ids['working']}", {"hidden": True})
    assert r.status_code == 200, r.text


async def test_a_hidden_stage_still_counts_and_still_takes_a_move(c3) -> None:
    _deal(c3.a, "d1", 1, Stage="Qualify", Amount=100)
    await _sync(c3, c3.org_a, c3.a)
    qualify = str(_deal_rows(c3.p, c3.org_a)["Qualify"].id)
    admin = _admin(c3.org_a)
    assert (await _req(admin, "PATCH", f"/crm/statuses/deal/{qualify}",
                       {"hidden": True})).status_code == 200
    report = (await _req(admin, "GET", "/crm/reports/pipeline")).json()
    assert next(x for x in report["stages"] if x["status"]["id"] == qualify)["count"] == 1

    ids = _native(c3.p, c3.org_b)
    admin_b = _admin(c3.org_b)
    assert (await _req(admin_b, "PATCH", f"/crm/statuses/deal/{ids['next']}",
                       {"hidden": True})).status_code == 200
    r = await _req(admin_b, "PATCH", f"/crm/deals/{ids['deal']}", {"status_id": ids["next"]})
    assert r.status_code == 200, r.text
    assert r.json()["status_id"] == ids["next"]


def test_the_overlay_migration_is_found_by_content() -> None:
    found = [
        p.name for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "ADD COLUMN IF NOT EXISTS ui_probability" in p.read_text(encoding="utf-8")
        # Migration 285 adds the same column to the lead table. It is not the overlay.
        and "ADD COLUMN IF NOT EXISTS ext_id" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found


# ── Review round 1 ───────────────────────────────────────────────────────────


async def test_an_incremental_run_reads_the_picklists_on_the_discovery_cadence(c3) -> None:
    """P1: most runs are incremental, so the picklist read rides on discovery.

    Fracktal's stage rows have no Zoho id yet. Inside 24 hours an incremental
    run reads no picklist. Past `DISCOVERY_EVERY` it reads both, sets the
    Zoho id, and takes the Zoho order and probability.
    """
    from gateway.crm_sync import engine

    _deal(c3.a, "d1", 1, Stage="Qualify")
    c3.a.add("Leads", "l1", 2, Last_Name="Lead", Lead_Status="Fresh")
    await _sync(c3, c3.org_a, c3.a)
    with c3.p.admin_engine.begin() as c:
        for table in ("crm_deal_statuses", "crm_lead_statuses"):
            c.execute(text(f"UPDATE {table} SET ext_id = NULL "
                           "WHERE organization_id = CAST(:o AS uuid)"), {"o": c3.org_a})
    c3.a.stages[0].update(sequence_number=2, probability=15)
    c3.a.stages[1].update(sequence_number=1)

    quiet = await engine.run_org(c3.org_a)
    assert (quiet.status, quiet.kind) == ("ok", "incremental")
    rows = _deal_rows(c3.p, c3.org_a)
    assert rows["Qualify"].ext_id is None and rows["Qualify"].position == 0

    with c3.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET fetched_at = now() - interval '25 hours' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": c3.org_a})
    due = await engine.run_org(c3.org_a)
    assert (due.status, due.kind) == ("ok", "incremental")
    rows = _deal_rows(c3.p, c3.org_a)
    assert {n: rows[n].ext_id for n in ("Qualify", "Propose", "Won It", "Gone")} == {
        "Qualify": "S1", "Propose": "S2", "Won It": "S3", "Gone": "S4"}
    assert (rows["Qualify"].position, rows["Qualify"].probability) == (1, 15)
    assert rows["Propose"].position == 0
    leads = _lead_rows(c3.p, c3.org_a)
    assert (leads["Fresh"].ext_id, leads["Contacted"].ext_id) == ("L1", "L2")


@pytest.mark.parametrize("first", ["S1", "S2"])
async def test_two_values_that_trade_names_keep_their_rows(c3, first) -> None:
    """Zoho swaps the names of two values. Each row keeps its id, its overlay
    and its deals, whichever value the picklist lists first."""
    _deal(c3.a, "d1", 1, Stage="Qualify")
    _deal(c3.a, "d2", 2, Stage="Propose")
    await _sync(c3, c3.org_a, c3.a)
    before = _deal_rows(c3.p, c3.org_a)
    row1, row2 = str(before["Qualify"].id), str(before["Propose"].id)
    assert (await _req(_admin(c3.org_a), "PATCH", f"/crm/statuses/deal/{row1}",
                       {"ui_probability": 33})).status_code == 200
    c3.a.stages[0]["actual_value"] = "Propose"
    c3.a.stages[1]["actual_value"] = "Qualify"
    if first == "S2":
        c3.a.stages[0]["sequence_number"], c3.a.stages[1]["sequence_number"] = 2, 1
    await _picklist_run(c3, c3.org_a, c3.a)
    rows = _deal_rows(c3.p, c3.org_a)
    assert (str(rows["Propose"].id), rows["Propose"].ext_id) == (row1, "S1")
    assert (str(rows["Qualify"].id), rows["Qualify"].ext_id) == (row2, "S2")
    assert rows["Propose"].ui_probability == 33
    assert not [n for n in rows if "[" in n], rows  # no row keeps a parked name
    assert _deal_status(c3.p, c3.org_a, "d1") == row1
    assert _deal_status(c3.p, c3.org_a, "d2") == row2


async def test_the_visibility_rules_judge_the_state_after_the_write(c3) -> None:
    """P2: the payload over the stored row, on a create and on a PATCH."""
    ids = _native(c3.p, c3.org_a)
    admin = _admin(c3.org_a)

    async def patch(key: str, body: dict, kind: str = "deal") -> int:
        return (await _req(admin, "PATCH", f"/crm/statuses/{kind}/{ids[key]}",
                           body)).status_code

    assert await patch("next", {"hidden": True}) == 200
    assert await patch("next", {"is_default": True}) == 422  # a hidden default
    assert await patch("working", {"hidden": True}, "lead") == 200
    assert await patch("working", {"is_default": True}, "lead") == 422
    assert await patch("won", {"type": "open"}) == 422  # the only visible won
    assert await patch("lost", {"type": "open", "probability": 0}) == 422
    assert await patch("won", {"hidden": True}) == 422
    created = await _req(admin, "POST", "/crm/statuses/deal",
                         {"name": "Hidden default", "hidden": True, "is_default": True})
    assert created.status_code == 422, created.text
    # A second visible won stage frees the first one.
    second = await _req(admin, "POST", "/crm/statuses/deal",
                        {"name": "Won too", "type": "won", "probability": 100})
    assert second.status_code == 201, second.text
    assert await patch("won", {"type": "open", "probability": 100}) == 200
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{second.json()['id']}",
                   {"hidden": True})
    assert r.status_code == 422, r.text  # it is now the only visible won stage
    # A hidden won stage does not count as the visible one.
    assert await patch("won", {"type": "won", "probability": 100, "hidden": True}) == 200
    r = await _req(admin, "PATCH", f"/crm/statuses/deal/{second.json()['id']}",
                   {"hidden": True})
    assert r.status_code == 422, r.text
