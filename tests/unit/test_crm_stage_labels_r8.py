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
e. two values that trade labels in one read keep their rows;
f. the chain: the label of one value is the ``actual_value`` of another.
   The heal queues ONE full read, and each deal lands on the row of its own
   value. A plain rename queues no full read. The same holds for
   ``Lead_Status``, and for a label that a retired value held. The label
   read queues a role call only for a deal that changed. A read for a rule
   that stopped starts over when the check fires, and a label read that
   stopped goes on from its page;
f2. the check writes the restart of a stopped read into the cursor of its
    own entity (task #1117). A run that stops after the check, on a stop or
    on its budget, keeps it, and the next run reads from page 1. A cursor
    that is not in progress gets no restart;
g. the merge shows a lane when either row showed (the hidden rule);
h. the merge fills an empty ``required_fields`` from the folded row;
i. a pipeline stage type finds its row by the Zoho id first.

The fake sends no ``display_value`` while it builds the split state. The sync
then names each value by ``actual_value``, as the old rule did with a real
Zoho. The fake then sends the shape that Zoho sends, with both values.
"""

from __future__ import annotations

import json
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
    # of the folded row. The kept row showed, so the merged row shows (g).
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


def _restage(z: Any, ident: str, stage: str) -> None:
    """Give a fake deal a new ``Stage`` and keep its ``Modified_Time``. Zoho
    does this when it relabels the value of the deal: the record carries the
    new label, and it does not count as a change."""
    for record in z.data["Deals"]:
        if record["id"] == ident:
            record["Stage"] = stage


@pytest.mark.parametrize("first", ["S1", "S2"])
async def test_two_values_that_trade_labels_keep_their_rows(c3, first) -> None:  # noqa: F811
    """Zoho swaps the labels of two values and keeps each ``actual_value``.
    Each row keeps its id, its overlay and its deals, in either read order.

    Each deal then carries the new label of its value, as in Zoho. The trade
    queues one full read (f), and that read maps each deal to its row again.
    """
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
    _restage(c3.a, "d1", "Propose")
    _restage(c3.a, "d2", "Qualify")
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


# ── f. The chain: one value's label is another value's actual_value ─────────


def _chain_shape(z: Any, *, labels: bool, s2_first: bool) -> None:
    """S1: ``actual_value`` "Alpha", label "Beta". S2: ``actual_value`` "Beta",
    label "Gamma". Without *labels* the fake sends no ``display_value``.
    *s2_first* puts S2 first in the Zoho order, so the picklist read meets S2
    first. Each pipeline map entry carries the id of its value."""
    values = [("S1", "Alpha", "Beta"), ("S2", "Beta", "Gamma")]
    if s2_first:
        values.reverse()
    items, maps = [], []
    for seq, (ident, actual, label) in enumerate(values, start=1):
        item: dict[str, Any] = {"id": ident, "actual_value": actual, "sequence_number": seq}
        if labels:
            item["display_value"] = label
        items.append(item)
        maps.append({**item, "forecast_category": {"name": "Pipeline"},
                     "forecast_type": "Open"})
    z.stages = items
    z.pipelines = {"pipeline": [
        {"id": "p1", "display_value": "Sales", "default": True, "maps": maps}]}


def _full_reads(z: Any, start: int, module: str) -> int:
    """How many reads of the record list of *module* began with no
    ``If-Modified-Since`` after request *start*: each is a full read."""
    return sum(
        1 for r in z.requests[start:]
        if r.method == "GET" and r.url.path.endswith(f"/crm/v2/{module}")
        and "ids" not in r.url.params and r.url.params.get("page", "1") == "1"
        and "If-Modified-Since" not in r.headers
    )


def _cursor(p: Any, org: str, module: str) -> dict[str, Any]:
    """The state of one sync cursor row, or an empty state."""
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = :m", o=org, m=module)
    return (rows[0][0] if rows else None) or {}


def _rereads(p: Any, org: str) -> set[str]:
    """The entities on the two re-read lists of the field rules cursor."""
    state = _cursor(p, org, "zoho:field_rules")
    return {*(state.get("reread") or ()), *(state.get("label_reread") or ())}


def _role_reads(z: Any, start: int) -> list[str]:
    """The deal of each contact-role call after request *start*."""
    return [r.url.path.split("/")[-2] for r in z.requests[start:]
            if r.url.path.endswith("/Contact_Roles")]


def _reset_role_queue(p: Any, org: str) -> None:
    """Empty the role queue, so a test counts only what a later read queues."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = CAST(:s AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:deal_roles'"),
            {"o": org, "s": json.dumps({"pending": [], "attempts": {}, "seeded": True})})


def _queue_rule_read(p: Any, org: str, entity: str) -> None:
    """Put *entity* on the ``reread`` list, as a rejected field rule does."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, last_status, "
            "initial_done_at, cursor_state) VALUES (CAST(:o AS uuid), "
            "'zoho:field_rules', 'ok', now(), CAST(:s AS jsonb)) "
            "ON CONFLICT (organization_id, module) DO UPDATE SET "
            "cursor_state = EXCLUDED.cursor_state"),
            {"o": org, "s": json.dumps({"applied": [], "reread": [entity]})})


@pytest.mark.parametrize("s2_first", [False, True], ids=["S1-first", "S2-first"])
async def test_the_chain_heals_with_one_full_read_of_the_deals(c3, s2_first) -> None:  # noqa: F811
    """The old rule named S2's row "Beta", the label of S1. So the deal of S1,
    which carries "Beta", sat on S2's row. The heal names each row by its
    label, but a rename moves no deal. So the picklist read queues one full
    read of the deals, and that read puts each deal on its own row. The
    incremental read of the same run does not list the deal of S1, because it
    did not change in Zoho. A second run queues nothing."""
    from gateway.crm_sync import engine

    _chain_shape(c3.a, labels=False, s2_first=s2_first)
    _deal(c3.a, "d1", 1, Stage="Beta")   # a deal of S1
    _deal(c3.a, "d2", 2, Stage="Gamma")  # a deal of S2
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert {n: r.ext_id for n, r in before.items()} == {
        "Alpha": "S1", "Beta": "S2", "Gamma": None}
    s1_row, s2_row = before["Alpha"].id, before["Beta"].id
    assert _deal_status(c3.p, c3.org_a, "d1") == s2_row  # the wrong row

    _chain_shape(c3.a, labels=True, s2_first=s2_first)
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    result = await engine.run_org(c3.org_a)
    assert (result.status, result.kind) == ("ok", "incremental"), result

    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert None not in by_ext, by_ext[None]
    assert {x: [(r.id, r.name) for r in rs] for x, rs in by_ext.items()} == {
        "S1": [(s1_row, "Beta")], "S2": [(s2_row, "Gamma")]}
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    assert _deal_status(c3.p, c3.org_a, "d2") == s2_row
    assert _members(c3.p, c3.org_a) == sorted(
        ("Sales", row, place) for place, row in enumerate(
            (s2_row, s1_row) if s2_first else (s1_row, s2_row)))
    # One full read ran in this run, and it left the re-read list.
    assert _full_reads(c3.a, start, "Deals") == 1
    assert "deal" not in _rereads(c3.p, c3.org_a)

    # A second run with the picklist read due queues nothing and moves nothing.
    snapshot = sorted(tuple(r) for r in _q(c3.p, _DEAL_ROWS_SQL, o=c3.org_a))
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 0
    assert _rereads(c3.p, c3.org_a) == set()
    assert sorted(tuple(r) for r in _q(c3.p, _DEAL_ROWS_SQL, o=c3.org_a)) == snapshot
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row


async def test_a_chain_of_lead_statuses_heals_with_one_full_read(c3) -> None:  # noqa: F811
    """The same chain on ``Lead_Status``: L1 has ``actual_value`` "Alpha" and the
    label "Beta", and L2 has ``actual_value`` "Beta" and the label "Gamma"."""
    from gateway.crm_sync import engine

    c3.a.lead_statuses = [
        {"id": "L1", "actual_value": "Alpha", "sequence_number": 1},
        {"id": "L2", "actual_value": "Beta", "sequence_number": 2},
    ]
    c3.a.add("Leads", "l1", 1, Last_Name="One", Lead_Status="Beta")   # a lead of L1
    c3.a.add("Leads", "l2", 2, Last_Name="Two", Lead_Status="Gamma")  # a lead of L2
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _LEAD_ROWS_SQL, c3.org_a)
    assert {n: r.ext_id for n, r in before.items()} == {
        "Alpha": "L1", "Beta": "L2", "Gamma": None}
    l1_row, l2_row = before["Alpha"].id, before["Beta"].id
    assert _lead_status(c3.p, c3.org_a, "l1") == l2_row  # the wrong row

    c3.a.lead_statuses[0]["display_value"] = "Beta"
    c3.a.lead_statuses[1]["display_value"] = "Gamma"
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    result = await engine.run_org(c3.org_a)
    assert (result.status, result.kind) == ("ok", "incremental"), result

    by_ext = _by_ext(c3.p, _LEAD_ROWS_SQL, c3.org_a)
    assert {x: [(r.id, r.name) for r in rs] for x, rs in by_ext.items()} == {
        "L1": [(l1_row, "Beta")], "L2": [(l2_row, "Gamma")]}
    assert _lead_status(c3.p, c3.org_a, "l1") == l1_row
    assert _lead_status(c3.p, c3.org_a, "l2") == l2_row
    assert _full_reads(c3.a, start, "Leads") == 1
    assert _rereads(c3.p, c3.org_a) == set()


async def test_a_label_that_a_retired_value_held_gets_one_full_read(c3) -> None:  # noqa: F811
    """Zoho retires S2 ("Beta"), and an admin then renames S1 to "Beta". A changed
    deal of S1 carries "Beta" and lands on the retired row. The picklist read
    parks that row, and a relabel leaves ``Modified_Time`` as it was, so no
    incremental read lists the deal again. One full read moves it."""
    from gateway.crm_sync import engine

    c3.a.stages = [
        {"id": "S1", "actual_value": "Alpha", "sequence_number": 1},
        {"id": "S2", "actual_value": "Beta", "sequence_number": 2},
    ]
    _deal(c3.a, "d1", 1, Stage="Alpha")
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    s1_row, s2_row = before["Alpha"].id, before["Beta"].id

    # Zoho retires S2. The next picklist read marks its row.
    c3.a.stages = c3.a.stages[:1]
    _discovery_due(c3.p, c3.org_a)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    # S1 takes the label. No picklist read runs, so a pull finds the old row.
    c3.a.stages[0]["display_value"] = "Beta"
    _restage(c3.a, "d1", "Beta")
    _deal(c3.a, "d2", 5, Stage="Beta")
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _deal_status(c3.p, c3.org_a, "d2") == s2_row  # the retired row

    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert {x: [(r.id, r.name) for r in rs] for x, rs in by_ext.items()} == {
        "S1": [(s1_row, "Beta")], "S2": [(s2_row, "Beta [S2]")]}
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    assert _deal_status(c3.p, c3.org_a, "d2") == s1_row
    assert _full_reads(c3.a, start, "Deals") == 1
    assert _rereads(c3.p, c3.org_a) == set()

    # The park renamed the row, so the next picklist read queues nothing.
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 0


async def test_a_label_read_queues_a_role_call_only_for_a_deal_that_changed(c3) -> None:  # noqa: F811
    """A label read changes no contact role, and no admin chooses its cost. So
    a chain costs no role call for each deal. A deal that did change still owes
    its call, because the read moves the cursor past it. A read for a rule on
    the same deals still queues each one."""
    from gateway.crm_sync import engine

    _chain_shape(c3.a, labels=False, s2_first=False)
    _deal(c3.a, "d1", 1, Stage="Beta")
    _deal(c3.a, "d2", 2, Stage="Gamma")
    await _sync(c3, c3.org_a, c3.a)
    _reset_role_queue(c3.p, c3.org_a)

    _chain_shape(c3.a, labels=True, s2_first=False)
    _deal(c3.a, "d3", 9, Stage="Beta")  # it changed in Zoho
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 1
    # d1 owes no call. d2 sits at the cursor second, where an incremental read
    # still asks, so it costs one call. d3 changed.
    assert _role_reads(c3.a, start) == ["d2", "d3"]

    _reset_role_queue(c3.p, c3.org_a)
    _queue_rule_read(c3.p, c3.org_a, "deal")
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 1
    assert sorted(_role_reads(c3.a, start)) == ["d1", "d2", "d3"]


def _at_page_two(z: Any, start: int) -> Any:
    """Stop a run at page 2 of its full read of the deals. Page 1 arrives, the
    owners load, and page 1 is written. The next call, page 2, stops."""
    return lambda: (
        _full_reads(z, start, "Deals") >= 1 and z.requests[-1].url.path != "/crm/v2/Deals"
    )


def _five_deals_in_the_chain(z: Any) -> None:
    """Five deals on three pages, in the chain with the old names."""
    _chain_shape(z, labels=False, s2_first=False)
    _deal(z, "d1", 1, Stage="Beta")  # a deal of S1, on page 1
    for minute, ident in enumerate(("d2", "d3", "d4", "d5"), start=2):
        _deal(z, ident, minute, Stage="Gamma")


async def test_a_stopped_rule_read_starts_over_when_the_check_fires(c3, monkeypatch) -> None:  # noqa: F811
    """A read for a rule stops on page 1. The next run finds a chain. Page 1 used
    the old names, and the last page would clear the entity. So the read starts
    from page 1 again. That one read counts for the rule and for the label. It
    queues each deal, and ``recovering`` keeps its mark."""
    from gateway.crm_sync import engine

    _five_deals_in_the_chain(c3.a)
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    s1_row, s2_row = before["Alpha"].id, before["Beta"].id
    _reset_role_queue(c3.p, c3.org_a)
    _queue_rule_read(c3.p, c3.org_a, "deal")

    start = len(c3.a.requests)
    stopped = await engine.run_org(c3.org_a, should_stop=_at_page_two(c3.a, start))
    assert stopped.status == "stopped", stopped
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert state["in_progress"] is True and state["rule_reread"] is True, state
    assert engine.LABEL_REREAD_KEY not in state
    with c3.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = cursor_state || "
            "'{\"recovering\": true}'::jsonb WHERE organization_id = CAST(:o AS uuid) "
            "AND module = 'zoho:deal'"), {"o": c3.org_a})
    back: list[str] = []
    original = engine._queue_dependents

    async def spy(ctx: Any, db: Any, entity: str) -> None:
        back.append(entity)
        await original(ctx, db, entity)

    monkeypatch.setattr(engine, "_queue_dependents", spy)

    _chain_shape(c3.a, labels=True, s2_first=False)
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"

    assert _full_reads(c3.a, start, "Deals") == 1  # from page 1 again
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    for ident in ("d2", "d3", "d4", "d5"):
        assert _deal_status(c3.p, c3.org_a, ident) == s2_row, ident
    assert _rereads(c3.p, c3.org_a) == set()
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert "rule_reread" not in state and engine.LABEL_REREAD_KEY not in state, state
    assert sorted(_role_reads(c3.a, start)) == ["d1", "d2", "d3", "d4", "d5"]
    assert back == ["deal"]


async def test_a_label_read_that_stopped_goes_on_from_its_page(c3) -> None:  # noqa: F811
    """The label list and the mark outlive a stop. The next run finds no chain,
    so the read goes on from its page and does not start over."""
    from gateway.crm_sync import engine

    _five_deals_in_the_chain(c3.a)
    await _sync(c3, c3.org_a, c3.a)
    s1_row = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)["Alpha"].id
    _chain_shape(c3.a, labels=True, s2_first=False)
    _discovery_due(c3.p, c3.org_a)

    start = len(c3.a.requests)
    stopped = await engine.run_org(c3.org_a, should_stop=_at_page_two(c3.a, start))
    assert stopped.status == "stopped", stopped
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert state["in_progress"] is True and state[engine.LABEL_REREAD_KEY] is True, state
    assert _rereads(c3.p, c3.org_a) == {"deal"}

    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 0  # it goes on from page 2
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    assert _rereads(c3.p, c3.org_a) == set()
    assert engine.LABEL_REREAD_KEY not in _cursor(c3.p, c3.org_a, "zoho:deal")


# ── f2. The restart of a stopped read outlives the run (task #1117) ─────────


def _record_reads(z: Any, start: int, module: str) -> int:
    """How many reads of the record list of *module* came after request
    *start*. A keyset read asks for each page at ``page=1``, with the time of
    the last page in ``If-Modified-Since``, so this counts pages."""
    return sum(
        1 for r in z.requests[start:]
        if r.method == "GET" and r.url.path == f"/crm/v2/{module}"
        and "ids" not in r.url.params
    )


def _at_next_deal_page(z: Any, start: int, pages: int) -> Any:
    """Stop a run at its next read of the deals, after it read *pages* pages of
    them. The owners load after the first page, so the stop waits for that."""
    return lambda: (
        _record_reads(z, start, "Deals") >= pages
        and z.requests[-1].url.path != "/crm/v2/Deals"
    )


def _after_the_picklist(z: Any, start: int, module: str) -> Any:
    """Stop a run at its first call after the field read of *module*. The page
    of that read, and of its label check, is written first."""
    return lambda: any(
        r.url.path.endswith("/settings/fields") and r.url.params.get("module") == module
        for r in z.requests[start:]
    )


def _trade_the_chain_labels(z: Any) -> None:
    """A Zoho admin trades the labels of S1 and S2. Each deal then carries the
    new label of its value, and its ``Modified_Time`` stays."""
    labels = {"S1": "Gamma", "S2": "Beta"}
    for item in [*z.stages, *z.pipelines["pipeline"][0]["maps"]]:
        item["display_value"] = labels[item["id"]]
    _restage(z, "d1", "Gamma")
    for ident in ("d2", "d3", "d4", "d5"):
        _restage(z, ident, "Beta")


def _queue_label_read(p: Any, org: str, entity: str) -> None:
    """Put *entity* on the label list, as the label check does."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, last_status, "
            "initial_done_at, cursor_state) VALUES (CAST(:o AS uuid), "
            "'zoho:field_rules', 'ok', now(), CAST(:s AS jsonb)) "
            "ON CONFLICT (organization_id, module) DO UPDATE SET "
            "cursor_state = EXCLUDED.cursor_state"),
            {"o": org, "s": json.dumps({"applied": [], "label_reread": [entity]})})


@pytest.mark.parametrize("way", ["stop", "budget"])
async def test_a_stopped_label_read_starts_over_after_a_run_that_stopped_at_the_check(
    c3, way,  # noqa: F811
) -> None:
    """A label read stops at page 2. An admin trades two labels, and the next
    run reads page 2 under the old row names, then stops. The run after that
    renames the rows and stops before it reads a deal page, on a stop or on
    its budget. The cursor keeps the restart, so a clean run reads from page
    1 and puts each deal on its row. A third run does not read in full."""
    from gateway.crm_sync import engine

    _five_deals_in_the_chain(c3.a)
    await _sync(c3, c3.org_a, c3.a)
    s1_row = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)["Alpha"].id
    s2_row = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)["Beta"].id
    _chain_shape(c3.a, labels=True, s2_first=False)
    _discovery_due(c3.p, c3.org_a)

    # The chain fires the check. The cursor is not in progress, so the label
    # read starts from page 1 and no restart is written. It stops at page 2.
    start = len(c3.a.requests)
    assert (await engine.run_org(
        c3.org_a, should_stop=_at_page_two(c3.a, start))).status == "stopped"
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert state["in_progress"] is True and state[engine.LABEL_REREAD_KEY] is True, state
    assert engine.LABEL_RESTART_KEY not in state

    # The trade. The next run reads page 2 with the old row names, so d3 and
    # d4, two deals of S2, land on the row of S1. It stops at page 3.
    _trade_the_chain_labels(c3.a)
    start = len(c3.a.requests)
    assert (await engine.run_org(
        c3.org_a, should_stop=_at_next_deal_page(c3.a, start, 1))).status == "stopped"
    assert (_record_reads(c3.a, start, "Deals"), _full_reads(c3.a, start, "Deals")) == (1, 0)
    assert _deal_status(c3.p, c3.org_a, "d3") == s1_row  # the wrong row
    at_page_three = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert at_page_three["in_progress"] is True, at_page_three
    assert at_page_three["since"] != state["since"]  # it moved on a page

    # The check fires and renames the rows. The run stops before a deal page.
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    if way == "stop":
        stopped = await engine.run_org(
            c3.org_a, should_stop=_after_the_picklist(c3.a, start, "Deals"))
    else:
        stopped = await engine.run_org(c3.org_a, credit_budget=2)
    assert (stopped.status, stopped.error) == (
        "stopped", "shutdown" if way == "stop" else "budget"), stopped
    assert _record_reads(c3.a, start, "Deals") == 0
    rows = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert (rows["Gamma"].id, rows["Beta"].id) == (s1_row, s2_row)
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert state[engine.LABEL_RESTART_KEY] is True, state
    # Every other key stays, so the read keeps its marks and its page.
    assert {k: v for k, v in state.items() if k != engine.LABEL_RESTART_KEY} == at_page_three
    assert _rereads(c3.p, c3.org_a) == {"deal"}

    # A clean run. Its check does not fire, and the read starts from page 1.
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 1
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    for ident in ("d2", "d3", "d4", "d5"):
        assert _deal_status(c3.p, c3.org_a, ident) == s2_row, ident
    assert _rereads(c3.p, c3.org_a) == set()
    state = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert state["in_progress"] is False, state
    assert engine.LABEL_RESTART_KEY not in state and engine.LABEL_REREAD_KEY not in state

    # A third run neither starts over nor reads in full.
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 0
    assert _rereads(c3.p, c3.org_a) == set()
    assert engine.LABEL_RESTART_KEY not in _cursor(c3.p, c3.org_a, "zoho:deal")
    for ident in ("d2", "d3", "d4", "d5"):
        assert _deal_status(c3.p, c3.org_a, ident) == s2_row, ident


async def test_the_check_writes_no_restart_into_a_cursor_that_is_not_in_progress(c3) -> None:  # noqa: F811
    """The chain fires the check after a read that ended. The run stops before
    a deal page. The cursor gets no restart, and the label read of the next
    run starts from page 1 as a new read."""
    from gateway.crm_sync import engine

    _chain_shape(c3.a, labels=False, s2_first=False)
    _deal(c3.a, "d1", 1, Stage="Beta")
    _deal(c3.a, "d2", 2, Stage="Gamma")
    await _sync(c3, c3.org_a, c3.a)
    before = _cursor(c3.p, c3.org_a, "zoho:deal")
    assert before["in_progress"] is False, before
    s1_row = _rows(c3.p, _DEAL_ROWS_SQL, c3.org_a)["Alpha"].id

    _chain_shape(c3.a, labels=True, s2_first=False)
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    stopped = await engine.run_org(
        c3.org_a, should_stop=_after_the_picklist(c3.a, start, "Deals"))
    assert stopped.status == "stopped", stopped
    assert _rereads(c3.p, c3.org_a) == {"deal"}  # the check fired
    assert _cursor(c3.p, c3.org_a, "zoho:deal") == before

    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Deals") == 1
    assert _deal_status(c3.p, c3.org_a, "d1") == s1_row
    assert _rereads(c3.p, c3.org_a) == set()


async def test_the_check_of_a_lead_status_writes_the_restart_into_the_lead_cursor(c3) -> None:  # noqa: F811
    """The check of ``Lead_Status`` fires while a label read of the leads is
    in progress. The restart goes into the lead cursor, and the deal cursor
    gets none. The next clean run reads the leads from page 1."""
    from gateway.crm_sync import engine

    c3.a.lead_statuses = [
        {"id": "L1", "actual_value": "Alpha", "sequence_number": 1},
        {"id": "L2", "actual_value": "Beta", "sequence_number": 2},
    ]
    c3.a.add("Leads", "l1", 1, Last_Name="One", Lead_Status="Beta")   # a lead of L1
    c3.a.add("Leads", "l2", 2, Last_Name="Two", Lead_Status="Gamma")  # a lead of L2
    await _sync(c3, c3.org_a, c3.a)
    before = _rows(c3.p, _LEAD_ROWS_SQL, c3.org_a)
    l1_row, l2_row = before["Alpha"].id, before["Beta"].id
    # A label read of the leads that stopped at page 2.
    _queue_label_read(c3.p, c3.org_a, "lead")
    with c3.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = cursor_state || CAST(:s AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
            {"o": c3.org_a, "s": json.dumps(
                {"in_progress": True, "page": 2, engine.LABEL_REREAD_KEY: True})})
    deal_before = _cursor(c3.p, c3.org_a, "zoho:deal")

    c3.a.lead_statuses[0]["display_value"] = "Beta"
    c3.a.lead_statuses[1]["display_value"] = "Gamma"
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    stopped = await engine.run_org(
        c3.org_a, should_stop=_after_the_picklist(c3.a, start, "Leads"))
    assert stopped.status == "stopped", stopped
    assert _cursor(c3.p, c3.org_a, "zoho:lead")[engine.LABEL_RESTART_KEY] is True
    assert _cursor(c3.p, c3.org_a, "zoho:deal") == deal_before

    start = len(c3.a.requests)
    assert (await engine.run_org(c3.org_a)).status == "ok"
    assert _full_reads(c3.a, start, "Leads") == 1
    assert _lead_status(c3.p, c3.org_a, "l1") == l1_row
    assert _lead_status(c3.p, c3.org_a, "l2") == l2_row
    assert _rereads(c3.p, c3.org_a) == set()
    assert engine.LABEL_RESTART_KEY not in _cursor(c3.p, c3.org_a, "zoho:lead")


async def test_a_plain_rename_heals_with_no_full_read(c3) -> None:  # noqa: F811
    """S2 and S3 were renamed, and no label is another value's old name. The
    fold of item 8 moves each deal, so the heal queues no full read."""
    from gateway.crm_sync import engine

    await _split_state(c3)
    _zoho_shape(c3.a, labels=True)
    _discovery_due(c3.p, c3.org_a)
    start = len(c3.a.requests)
    result = await engine.run_org(c3.org_a)
    assert (result.status, result.kind) == ("ok", "incremental"), result
    assert None not in _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    assert _full_reads(c3.a, start, "Deals") == 0
    assert _rereads(c3.p, c3.org_a) == set()


# ── g and h. The hidden rule and the required fields of the merge ────────────


async def test_the_merge_shows_a_lane_when_either_row_showed(c3) -> None:  # noqa: F811
    """An admin hid the empty Zoho-id lane of S2 while the label row held its
    deals. The merged row holds the deals, so it shows. S3: both rows were
    hidden, so the merged row stays hidden."""
    ids = await _split_state(c3)
    _set(c3.p, "crm_deal_statuses", ids["Confirmation and Advance"], hidden=True)
    _set(c3.p, "crm_deal_statuses", ids["Closed Won"], hidden=True)
    _set(c3.p, "crm_deal_statuses", ids["Delivered & Installed"], hidden=True)
    _zoho_shape(c3.a, labels=True)
    await _picklist_run(c3, c3.org_a, c3.a)

    by_ext = _by_ext(c3.p, _DEAL_ROWS_SQL, c3.org_a)
    s2, s3 = by_ext["S2"][0], by_ext["S3"][0]
    assert (s2.id, s2.hidden) == (ids["Confirmation and Advance"], False)
    assert _deal_status(c3.p, c3.org_a, "d2") == s2.id
    assert (s3.id, s3.hidden) == (ids["Closed Won"], True)


async def test_the_merge_fills_empty_required_fields_from_the_folded_row(c3) -> None:  # noqa: F811
    """S2: the kept row requires nothing, and the label row requires the
    amount, so the merged row requires the amount. S3: the kept row has its
    own list, and it keeps it."""
    ids = await _split_state(c3)
    _set(c3.p, "crm_deal_statuses", ids["New Lead"], required_fields=["amount"])
    _set(c3.p, "crm_deal_statuses", ids["Closed Won"], required_fields=["close_date"])
    _set(c3.p, "crm_deal_statuses", ids["Delivered & Installed"],
         required_fields=["amount"])
    _zoho_shape(c3.a, labels=True)
    await _picklist_run(c3, c3.org_a, c3.a)

    required = {r[0]: list(r[1]) for r in _q(
        c3.p, "SELECT ext_id, required_fields FROM crm_deal_statuses "
              "WHERE organization_id = CAST(:o AS uuid)", o=c3.org_a)}
    assert required["S2"] == ["amount"]
    assert required["S3"] == ["close_date"]
    assert required["S1"] == []


# ── i. A pipeline stage type finds its row by the Zoho id first ──────────────


async def test_a_stage_type_finds_its_row_by_the_zoho_id_first(c3) -> None:  # noqa: F811
    """The rows still carry the names of the old rule (the picklist read did
    not run). The profile names the stages by label, with the Zoho id. A
    stage with an id that a row holds goes to that row. A stage with no such
    row goes by name, as before."""
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine

    with c3.p.admin_engine.begin() as c:
        for name, ext, position in (("Alpha", "S1", 0), ("Beta", "S2", 1),
                                    ("Gamma", None, 2), ("Delta", None, 3)):
            c.execute(text(
                "INSERT INTO crm_deal_statuses (organization_id, name, position, type, "
                "ext_id) VALUES (CAST(:o AS uuid), :n, :p, 'open', :x)"),
                {"o": c3.org_a, "n": name, "p": position, "x": ext})
    pipelines = [{"stages": [
        {"name": "Beta", "ext_id": "S1", "type": "lost"},
        {"name": "Gamma", "ext_id": "S2", "type": "won"},
        {"name": "Delta", "ext_id": "S9", "type": "won"},  # no row holds S9
    ]}]
    async with tenant_session(c3.org_a) as db:
        await engine._apply_stage_types(db, c3.org_a, pipelines)

    types = {(r[0], r[1]): r[2] for r in _q(
        c3.p, "SELECT name, ext_id, type FROM crm_deal_statuses "
              "WHERE organization_id = CAST(:o AS uuid)", o=c3.org_a)}
    assert types == {
        ("Alpha", "S1"): "lost", ("Beta", "S2"): "won",
        ("Gamma", None): "open", ("Delta", None): "won",
    }
