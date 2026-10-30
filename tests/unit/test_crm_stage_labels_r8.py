"""R8 — the Zoho Mirror sync names a stage by its Zoho LABEL (WS-53, task #1096).

Spec: ``project-docs/specs/crm_platform.md`` §20.6 items 7 to 9.

The bug (Fracktal, 2026-10-30). A Zoho admin renames a picklist value. Zoho
then changes ``display_value`` (the label on the board) and keeps
``actual_value`` (the first name). A deal record carries the label in
``Stage``. The sync named each picklist value and each pipeline stage by
``actual_value``. So the row with the Zoho id kept the old name and no deal,
and each record pull appended a second row under the label, with no Zoho id
and no place in the pipeline. ``orderLanes`` put those rows after each
pipeline stage.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS):

a. a fresh sync gives ONE row for a renamed value: the Zoho id, the label,
   the place in the pipeline and the deal;
b. an org in the split state heals on its next picklist read with no manual
   step. Each deal, each pipeline place and each saved view lands on the row
   with the Zoho id, no label row without a Zoho id stays, and the overlay
   follows the rule of ``core.merge_status_from_source``;
b2. a pipeline stage finds its row by the Zoho id first, in a run whose
    picklist read failed;
c. the same for ``Lead_Status``;
d. a value whose label equals its internal value behaves as before;
e. two values that trade labels in one read keep their rows.

The fake sends no ``display_value`` while it builds the split state. The sync
then names each value by ``actual_value``, as the old rule did with a real
Zoho. The fake then sends the shape that Zoho sends, with both values.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_stage_overlay_r8 import (  # noqa: F401  (c3 is a fixture)
    _deal,
    _deal_status,
    _picklist_run,
    _sync,
    c3,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401  (ns is a fixture)
    _assert_non_priv,
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

#: (Zoho id, actual_value, display_value, forecast category) of each Stage
#: value, in Zoho order. S1 and S4 keep their first name. S2 and S3 are two
#: of the six values that a Fracktal admin renamed.
_STAGE_VALUES = (
    ("S1", "Re-Evaluate", "Re-Evaluate", "Pipeline"),
    ("S2", "Confirmation and Advance", "New Lead", "Pipeline"),
    ("S3", "Closed Won", "Delivered & Installed", "Closed Won"),
    ("S4", "Closed Lost", "Closed Lost", "Closed Lost"),
)
_FORECAST_TYPES = {"Pipeline": "Open", "Closed Won": "Closed Won",
                   "Closed Lost": "Closed Lost"}

#: The same for the Lead_Status values. L2 was renamed.
_LEAD_VALUES = (
    ("L1", "Fresh", "Fresh"),
    ("L2", "Contacted", "Reached Out"),
)


def _stages(*, labels: bool) -> list[dict[str, Any]]:
    """The Stage picklist. Without *labels*, no value has a ``display_value``."""
    out = []
    for seq, (ident, actual, label, forecast) in enumerate(_STAGE_VALUES, start=1):
        item: dict[str, Any] = {
            "id": ident, "actual_value": actual, "sequence_number": seq,
            "probability": 10 * seq, "forecast_category": {"name": forecast},
        }
        if labels:
            item["display_value"] = label
        out.append(item)
    return out


def _pipelines(*, labels: bool) -> dict[str, Any]:
    """One default pipeline with each stage. Each map entry carries the Zoho id
    of its picklist value, as a Zoho pipeline does."""
    maps = []
    for seq, (ident, actual, label, forecast) in enumerate(_STAGE_VALUES, start=1):
        entry: dict[str, Any] = {
            "id": ident, "actual_value": actual, "sequence_number": seq,
            "forecast_category": {"name": forecast},
            "forecast_type": _FORECAST_TYPES[forecast],
        }
        if labels:
            entry["display_value"] = label
        maps.append(entry)
    return {"pipeline": [
        {"id": "p1", "display_value": "Sales", "default": True, "maps": maps}]}


def _lead_statuses(*, labels: bool) -> list[dict[str, Any]]:
    out = []
    for seq, (ident, actual, label) in enumerate(_LEAD_VALUES, start=1):
        item: dict[str, Any] = {"id": ident, "actual_value": actual, "sequence_number": seq}
        if labels:
            item["display_value"] = label
        out.append(item)
    return out


def _zoho_shape(z: Any, *, labels: bool) -> None:
    z.stages = _stages(labels=labels)
    z.pipelines = _pipelines(labels=labels)
    z.lead_statuses = _lead_statuses(labels=labels)


_DEAL_ROWS_SQL = (
    "SELECT id::text AS id, name, ext_id, position, type, hidden, ui_position, "
    "ui_probability, ui_color, max_dwell_days FROM crm_deal_statuses "
    "WHERE organization_id = CAST(:o AS uuid)"
)
_LEAD_ROWS_SQL = (
    "SELECT id::text AS id, name, ext_id, position, hidden, ui_position, "
    "ui_probability, ui_color FROM crm_lead_statuses "
    "WHERE organization_id = CAST(:o AS uuid)"
)


def _rows(p: Any, sql: str, org: str) -> dict[str, Any]:
    """Each status row, keyed by name."""
    return {r.name: r for r in _q(p, sql, o=org)}


def _by_ext(p: Any, sql: str, org: str) -> dict[str | None, list[Any]]:
    out: dict[str | None, list[Any]] = {}
    for r in _q(p, sql, o=org):
        out.setdefault(r.ext_id, []).append(r)
    return out


def _members(p: Any, org: str) -> list[tuple[str, str, int]]:
    """``(pipeline name, status id, place)`` of each pipeline membership."""
    return sorted(
        (str(r[0]), str(r[1]), int(r[2])) for r in _q(
            p, "SELECT pl.name, ps.status_id::text, ps.position FROM "
               "crm_pipeline_stages ps JOIN crm_pipelines pl ON pl.id = ps.pipeline_id "
               "WHERE ps.organization_id = CAST(:o AS uuid)", o=org)
    )


def _lead_status(p: Any, org: str, ext_id: str) -> str:
    return str(_q(p, "SELECT status_id FROM crm_leads WHERE organization_id = "
                     "CAST(:o AS uuid) AND ext_id = :x", o=org, x=ext_id)[0][0])


def _set(p: Any, table: str, row_id: str, **values: Any) -> None:
    """Write overlay values as the admin door would, straight to the row."""
    columns = ", ".join(f"{k} = :{k}" for k in values)
    with p.admin_engine.begin() as c:
        c.execute(text(f"UPDATE {table} SET {columns} WHERE id = CAST(:id AS uuid)"),
                  {"id": row_id, **values})


def _discovery_due(p: Any, org: str) -> None:
    """Age the stored profile, so the next run reads the picklists and the
    pipelines (the 24-hour discovery cadence of ``_execute``)."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET fetched_at = now() - interval '25 hours' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _split_state(env: Any) -> dict[str, Any]:
    """Build the split state of a live org through the engine, and check it.

    Deals carry the label in ``Stage``. The fake sends no label, so the sync
    names each value by ``actual_value``, as the old rule did.
    """
    _zoho_shape(env.a, labels=False)
    _deal(env.a, "d1", 1, Stage="Re-Evaluate")
    _deal(env.a, "d2", 2, Stage="New Lead")
    _deal(env.a, "d3", 3, Stage="New Lead")
    _deal(env.a, "d4", 4, Stage="Delivered & Installed")
    _deal(env.a, "d5", 5, Stage="Closed Lost")
    await _sync(env, env.org_a, env.a)
    rows = _rows(env.p, _DEAL_ROWS_SQL, env.org_a)
    assert {n: r.ext_id for n, r in rows.items()} == {
        "Re-Evaluate": "S1", "Confirmation and Advance": "S2", "Closed Won": "S3",
        "Closed Lost": "S4", "New Lead": None, "Delivered & Installed": None,
    }
    # The label rows sit after each Zoho stage, outside the pipeline.
    assert rows["New Lead"].position > rows["Closed Lost"].position
    ids = {n: r.id for n, r in rows.items()}
    assert _members(env.p, env.org_a) == sorted(
        ("Sales", ids[n], place) for place, n in enumerate(
            ("Re-Evaluate", "Confirmation and Advance", "Closed Won", "Closed Lost")))
    assert _deal_status(env.p, env.org_a, "d2") == ids["New Lead"]
    assert _deal_status(env.p, env.org_a, "d4") == ids["Delivered & Installed"]
    return ids


# ── a. A fresh sync gives one row for a renamed value ───────────────────────


async def test_a_fresh_sync_names_a_renamed_stage_by_its_label(c3, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _zoho_shape(c3.a, labels=True)
    _deal(c3.a, "d1", 1, Stage="New Lead")
    _deal(c3.a, "d2", 2, Stage="Delivered & Installed")
    await _sync(c3, c3.org_a, c3.a)

    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert None not in by_ext, by_ext  # no row of a record pull
    assert {x: [r.name for r in rs] for x, rs in by_ext.items()} == {
        "S1": ["Re-Evaluate"], "S2": ["New Lead"], "S3": ["Delivered & Installed"],
        "S4": ["Closed Lost"]}
    s2, s3 = by_ext["S2"][0], by_ext["S3"][0]
    assert (s2.position, s3.type) == (1, "won")
    assert _deal_status(c3.p, c3.org_a, "d1") == s2.id
    assert _deal_status(c3.p, c3.org_a, "d2") == s3.id
    assert _members(c3.p, c3.org_a) == sorted(
        ("Sales", by_ext[x][0].id, place) for place, x in enumerate(("S1", "S2", "S3", "S4")))
    # Org B did not change.
    assert _q(c3.p, "SELECT count(*) FROM crm_deal_statuses WHERE organization_id = "
                    "CAST(:o AS uuid)", o=c3.org_b)[0][0] == 0


# ── b. The split state heals on the next picklist read ──────────────────────


async def test_an_org_in_the_split_state_heals_on_its_next_picklist_read(c3) -> None:  # noqa: F811
    ids = await _split_state(c3)
    # The overlay of each row before the heal. S1 has the same label and
    # internal value, so it has no split. It keeps its overlay as before (d).
    _set(c3.p, "crm_deal_statuses", ids["Re-Evaluate"],
         ui_position=0, ui_color="amber", hidden=False)
    # S2: both rows hold an overlay.
    _set(c3.p, "crm_deal_statuses", ids["Confirmation and Advance"],
         ui_position=1, ui_probability=33, hidden=False)
    _set(c3.p, "crm_deal_statuses", ids["New Lead"],
         ui_position=11, ui_probability=50, ui_color="violet", max_dwell_days=7,
         hidden=True)
    # S3: only the label row holds an overlay.
    _set(c3.p, "crm_deal_statuses", ids["Delivered & Installed"],
         ui_position=12, ui_color="teal")
    with c3.p.admin_engine.begin() as c:
        view = str(c.execute(text(
            "INSERT INTO crm_views (organization_id, entity, name, owner_email, filters) "
            "VALUES (CAST(:o AS uuid), 'deals', 'Mine', 'admin@crm-sync.test', "
            "CAST(:f AS jsonb)) RETURNING id"),
            {"o": c3.org_a, "f": f'{{"status": "{ids["New Lead"]}"}}'}).scalar())

    # Zoho as it is: each value has its label. A deal moves in Zoho, so the
    # same run pulls it after the picklist read.
    _zoho_shape(c3.a, labels=True)
    _deal(c3.a, "d6", 9, Stage="New Lead")
    _discovery_due(c3.p, c3.org_a)
    from gateway.crm_sync import engine

    result = await engine.run_org(c3.org_a)
    assert (result.status, result.kind) == ("ok", "incremental"), result

    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert None not in by_ext, by_ext[None]  # no label row is left
    assert {x: [(r.id, r.name) for r in rs] for x, rs in by_ext.items()} == {
        "S1": [(ids["Re-Evaluate"], "Re-Evaluate")],
        "S2": [(ids["Confirmation and Advance"], "New Lead")],
        "S3": [(ids["Closed Won"], "Delivered & Installed")],
        "S4": [(ids["Closed Lost"], "Closed Lost")],
    }
    s1, s2, s3 = by_ext["S1"][0], by_ext["S2"][0], by_ext["S3"][0]
    # Every deal is kept, on the row with the Zoho id.
    for deal, row in (("d1", s1), ("d2", s2), ("d3", s2), ("d6", s2), ("d4", s3),
                      ("d5", by_ext["S4"][0])):
        assert _deal_status(c3.p, c3.org_a, deal) == row.id, deal
    # The Zoho place, the pipeline place and the type.
    assert [r.position for r in (s1, s2, s3)] == [0, 1, 2]
    assert s3.type == "won"
    assert _members(c3.p, c3.org_a) == sorted(
        ("Sales", by_ext[x][0].id, place) for place, x in enumerate(("S1", "S2", "S3", "S4")))
    # The overlay rule: the kept row wins, and a NULL of it takes the value
    # of the folded row. `hidden` stays as the kept row had it.
    assert (s2.ui_position, s2.ui_probability, s2.ui_color, s2.max_dwell_days,
            s2.hidden) == (1, 33, "violet", 7, False)
    assert (s3.ui_position, s3.ui_color, s3.ui_probability) == (12, "teal", None)
    assert (s1.ui_position, s1.ui_color, s1.hidden) == (0, "amber", False)
    assert _q(c3.p, "SELECT filters FROM crm_views WHERE id = CAST(:i AS uuid)",
              i=view)[0][0] == {"status": s2.id}
    # The pipeline step of the same run read the labels and the Zoho ids.
    profile = _q(c3.p, "SELECT profile FROM crm_source_profiles WHERE organization_id = "
                       "CAST(:o AS uuid) ORDER BY fetched_at DESC LIMIT 1", o=c3.org_a)[0][0]
    assert [(s["name"], s["ext_id"]) for s in profile["pipelines"][0]["stages"]] == [
        ("Re-Evaluate", "S1"), ("New Lead", "S2"), ("Delivered & Installed", "S3"),
        ("Closed Lost", "S4")]

    # A second run with both steps due changes nothing.
    before = sorted(tuple(r) for r in _q(c3.p, _DEAL_ROWS_SQL, o=c3.org_a))
    _discovery_due(c3.p, c3.org_a)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert sorted(tuple(r) for r in _q(c3.p, _DEAL_ROWS_SQL, o=c3.org_a)) == before


async def test_a_pipeline_stage_finds_its_row_by_the_zoho_id_first(c3) -> None:  # noqa: F811
    """Item 3. The picklist read fails, and the pipeline step runs. The label
    "New Lead" names the row of a record pull, and the Zoho id names the row
    of the picklist. The place goes to the row with the Zoho id."""
    from gateway.crm_sync import engine

    ids = await _split_state(c3)
    _zoho_shape(c3.a, labels=True)
    c3.a.fields_fail.add("Deals")
    _discovery_due(c3.p, c3.org_a)
    result = await engine.run_org(c3.org_a)
    assert result.status == "ok", result

    rows = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert rows["Confirmation and Advance"].ext_id == "S2"  # no picklist read
    assert rows["New Lead"].ext_id is None
    assert _members(c3.p, c3.org_a) == sorted(
        ("Sales", ids[n], place) for place, n in enumerate(
            ("Re-Evaluate", "Confirmation and Advance", "Closed Won", "Closed Lost")))
    assert len(rows) == 6  # the pipeline step added no row

    # The next picklist read heals it.
    c3.a.fields_fail.clear()
    await _picklist_run(c3, c3.org_a, c3.a)
    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert None not in by_ext
    assert by_ext["S2"][0].id == ids["Confirmation and Advance"]
    assert by_ext["S2"][0].name == "New Lead"
    assert _deal_status(c3.p, c3.org_a, "d2") == ids["Confirmation and Advance"]


# ── c. Lead_Status takes the same path ───────────────────────────────────────


async def test_a_lead_status_split_heals_the_same_way(c3) -> None:  # noqa: F811
    _zoho_shape(c3.a, labels=False)
    c3.a.add("Leads", "l1", 1, Last_Name="One", Lead_Status="Reached Out")
    c3.a.add("Leads", "l2", 2, Last_Name="Two", Lead_Status="Fresh")
    await _sync(c3, c3.org_a, c3.a)
    rows = _rows(c3.p, _LEAD_ROWS_SQL, c3.org_a)
    assert {n: r.ext_id for n, r in rows.items()} == {
        "Fresh": "L1", "Contacted": "L2", "Reached Out": None}
    kept, folded = rows["Contacted"].id, rows["Reached Out"].id
    _set(c3.p, "crm_lead_statuses", kept, ui_position=1)
    _set(c3.p, "crm_lead_statuses", folded, ui_position=9, ui_color="teal",
         ui_probability=40, hidden=True)

    _zoho_shape(c3.a, labels=True)
    await _picklist_run(c3, c3.org_a, c3.a)

    by_ext = _by_ext(c3.p, _LEAD_ROWS_SQL, c3.org_a)
    assert None not in by_ext, by_ext
    assert {x: [(r.id, r.name) for r in rs] for x, rs in by_ext.items()} == {
        "L1": [(rows["Fresh"].id, "Fresh")], "L2": [(kept, "Reached Out")]}
    l2 = by_ext["L2"][0]
    assert (l2.position, l2.ui_position, l2.ui_color, l2.ui_probability, l2.hidden) == (
        1, 1, "teal", 40, False)
    assert _lead_status(c3.p, c3.org_a, "l1") == kept
    assert _lead_status(c3.p, c3.org_a, "l2") == rows["Fresh"].id


# ── d. A label that equals its internal value ────────────────────────────────


async def test_a_value_whose_label_is_its_internal_value_is_unchanged(c3) -> None:  # noqa: F811
    """With ``display_value`` equal to ``actual_value``, the sync gives what it
    gave with ``actual_value`` alone: one row for each value, same id, same
    name, same order, and its deal."""
    plain = [{**s, "display_value": s["actual_value"]} for s in _stages(labels=False)]
    c3.a.stages = [dict(s) for s in plain]
    _deal(c3.a, "d1", 1, Stage="Closed Won")
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert {n: (r.ext_id, r.position) for n, r in before.items()} == {
        "Re-Evaluate": ("S1", 0), "Confirmation and Advance": ("S2", 1),
        "Closed Won": ("S3", 2), "Closed Lost": ("S4", 3)}
    _set(c3.p, "crm_deal_statuses", before["Closed Won"].id, ui_probability=90)

    c3.a.stages = [{k: v for k, v in s.items() if k != "display_value"} for s in plain]
    await _picklist_run(c3, c3.org_a, c3.a)
    after = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert {n: (r.id, r.ext_id, r.position) for n, r in after.items()} == {
        n: (r.id, r.ext_id, r.position) for n, r in before.items()}
    assert after["Closed Won"].ui_probability == 90
    assert _deal_status(c3.p, c3.org_a, "d1") == before["Closed Won"].id


# ── e. Two values trade labels in one read ───────────────────────────────────


@pytest.mark.parametrize("first", ["S1", "S2"])
async def test_two_values_that_trade_labels_keep_their_rows(c3, first) -> None:  # noqa: F811
    """Zoho swaps the labels of two values and keeps each ``actual_value``.
    Each row keeps its id, its overlay and its deals, in either read order."""
    c3.a.stages = [
        {"id": "S1", "actual_value": "Stage A", "display_value": "Qualify",
         "sequence_number": 1},
        {"id": "S2", "actual_value": "Stage B", "display_value": "Propose",
         "sequence_number": 2},
    ]
    _deal(c3.a, "d1", 1, Stage="Qualify")
    _deal(c3.a, "d2", 2, Stage="Propose")
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert set(before) == {"Qualify", "Propose"}
    row1, row2 = before["Qualify"].id, before["Propose"].id
    _set(c3.p, "crm_deal_statuses", row1, ui_probability=33)

    c3.a.stages[0]["display_value"] = "Propose"
    c3.a.stages[1]["display_value"] = "Qualify"
    if first == "S2":
        c3.a.stages[0]["sequence_number"], c3.a.stages[1]["sequence_number"] = 2, 1
    await _picklist_run(c3, c3.org_a, c3.a)

    rows = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert set(rows) == {"Qualify", "Propose"}, rows  # no parked name stays
    assert (rows["Propose"].id, rows["Propose"].ext_id) == (row1, "S1")
    assert (rows["Qualify"].id, rows["Qualify"].ext_id) == (row2, "S2")
    assert rows["Propose"].ui_probability == 33
    assert _deal_status(c3.p, c3.org_a, "d1") == row1
    assert _deal_status(c3.p, c3.org_a, "d2") == row2
