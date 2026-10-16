"""R8 — an activity on a module record, against a real Postgres (WS-53
CRM-Q23a, ``crm_platform.md`` §18.10.2).

Migration 275 adds ``crm_activities.module_record_id`` and widens the CHECK
``crm_activities_target_required``. The sync links a Zoho activity whose
``$se_module`` names a mirrored generic module to that record in
``crm_module_records``, and counts each activity it drops under
``activity_drops``. ``GET /crm/modules/{module}/records/{id}/timeline`` serves
the activities of one module record.

What this proves, one group for each "Done when" item of CRM-Q23a, as the
non-privileged role of the phase-4 rehearsal where a route or the engine runs:

1. and 2. the column, the partial index, the CHECK on a real database, the
   cascade, and a replay that changes nothing, also from the old CHECK;
3. a note, a task and a call on a mirrored module record land with
   ``module_record_id``; a custom ``$se_module`` never falls back to a core
   record; a module that the profile does not mirror stays unlinked; each org
   links only to its own record;
4. the drops count by ``$se_module``, as numbers, and Sync health still serves
   with no ``activity_drops`` row;
5. the timeline route: read only, the 404s, paging, and tenant isolation;
6. the readers that assume a core parent: PATCH and DELETE of an activity,
   the next-activity reads, the export ZIP and ``sync_zoho.push_activities``.

⚠️ The Zoho fixtures follow the v2 shape of a core parent (``What_Id: {id,
name}``, ``$se_module: "<ApiName>"``). No captured payload proves that Zoho
names a custom module that way. The production count ``activity_drops``
confirms it or refutes it.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_crm_module_records_r8 import (  # noqa: F401
    _connect_all,
    _records,
    _run,
    _seed,
    mz,
)
from tests.unit.test_crm_modules_routes_r8 import _module as _profile_module
from tests.unit.test_crm_modules_routes_r8 import _seed_org
from tests.unit.test_crm_next_activity_r8 import _call, _native
from tests.unit.test_crm_pipelines_r8 import _admin as _admin_of
from tests.unit.test_crm_pipelines_r8 import _req
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _connect,
    _q,
    _the_loop_is_not_stopping,
    ns,
    stamp,
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

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_activities", "crm_deal_contacts", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_pipeline_stages", "crm_pipelines",
    "crm_module_records", "crm_field_defs", "crm_source_profiles", "crm_settings",
    "integration_connections", "crm_deal_statuses", "crm_lead_statuses",
    "crm_lost_reasons",
)


@pytest.fixture
def rt(promoted, monkeypatch):
    """The promoted catalog with the CRM switches on, for the route tests."""
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


# ── Seeds, as the superuser ──────────────────────────────────────────────────


def _module_record(p: Any, org: str, module: str, ext_id: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n) RETURNING id"),
            {"o": org, "m": module, "e": ext_id, "n": name}).scalar())


def _activity(
    p: Any, org: str, *, module_record: str | None = None, deal: str | None = None,
    kind: str = "note", subject: str = "x", occurred: datetime | None = None,
    due: datetime | None = None, zoho: bool = False,
) -> str:
    """One activity row. ``zoho`` makes it a mirrored row (``zoho_id = ext_id``,
    as ``core.upsert_from_source`` writes it)."""
    ext = uuid.uuid4().hex if zoho else None
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, occurred_at, "
            "due_at, module_record_id, deal_id, created_by, ext_source, ext_id, zoho_id) "
            "VALUES (CAST(:o AS uuid), :t, :s, :occ, :due, CAST(:r AS uuid), "
            "CAST(:d AS uuid), 'seed@crm-q23a.test', :src, :ext, :ext) RETURNING id"),
            {"o": org, "t": kind, "s": subject, "occ": occurred, "due": due,
             "r": module_record, "d": deal, "src": "zoho" if zoho else None,
             "ext": ext}).scalar())


def _activities(p: Any, org: str) -> dict[str, Any]:
    """The org's activities by ``ext_id``."""
    rows = _q(p, "SELECT ext_id, module_record_id, lead_id, deal_id, contact_id, "
                 "company_id, type FROM crm_activities "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: r for r in rows}


def _migration() -> Path:
    found = [
        f for f in _MIGRATIONS.glob("*.sql")
        if f.name != "schema.generated.sql"
        and re.search(r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+module_record_id",
                      f.read_text(encoding="utf-8"))
    ]
    assert len(found) == 1, [f.name for f in found]
    return found[0]


def _check(p: Any) -> tuple[int, str, bool]:
    """The CHECK's oid, its definition and whether it is validated."""
    rows = _q(p, "SELECT oid, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
                 "WHERE conrelid = to_regclass('public.crm_activities') "
                 "AND conname = 'crm_activities_target_required'")
    assert len(rows) == 1, rows
    return int(rows[0][0]), str(rows[0][1]), bool(rows[0][2])


# ── 1 and 2. The migration on a real database ────────────────────────────────


def _module_fks(rt) -> list:
    return _q(rt, "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
                  "WHERE conrelid = to_regclass('public.crm_activities') AND contype = 'f' "
                  "AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = "
                  "to_regclass('public.crm_activities') AND attname = 'module_record_id')]"
                  "::smallint[]")


def _module_indexes(rt) -> list:
    return _q(rt, "SELECT indexdef FROM pg_indexes WHERE tablename = 'crm_activities' "
                  "AND indexdef LIKE '%module_record_id%'")


def test_the_column_and_the_partial_index_exist(rt) -> None:
    col = _q(rt, "SELECT data_type, is_nullable, column_default FROM "
                 "information_schema.columns WHERE table_name = 'crm_activities' "
                 "AND column_name = 'module_record_id'")
    assert col == [("uuid", "YES", None)]
    assert _module_fks(rt) == [("crm_module_records", "c")], "a foreign key that cascades"
    index = _module_indexes(rt)
    assert len(index) == 1
    assert "WHERE (module_record_id IS NOT NULL)" in index[0][0]


def test_the_check_takes_a_module_record_and_still_refuses_no_target(rt) -> None:
    a = rt.org_a
    _oid, definition, valid = _check(rt)
    assert "module_record_id IS NOT NULL" in definition and valid
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    kept = _activity(rt, a, module_record=record)
    with pytest.raises(IntegrityError, match="crm_activities_target_required"):
        _activity(rt, a)
    # The cascade: the record goes, and its activity goes with it.
    with rt.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_module_records WHERE id = CAST(:r AS uuid)"),
                  {"r": record})
    assert _q(rt, "SELECT id FROM crm_activities WHERE id = CAST(:i AS uuid)", i=kept) == []


def test_a_replay_changes_nothing_and_an_old_check_is_widened_once(rt) -> None:
    path = str(_migration())
    before = _check(rt)
    with rt.admin_engine.begin() as c:
        _exec_file(c, path)
    assert _check(rt) == before, "a replay keeps the same CHECK (the same oid)"

    # A database that still holds the CHECK of 144 is widened, and validated.
    with rt.admin_engine.begin() as c:
        c.execute(text(
            "ALTER TABLE crm_activities DROP CONSTRAINT crm_activities_target_required"))
        c.execute(text(
            "ALTER TABLE crm_activities ADD CONSTRAINT crm_activities_target_required "
            "CHECK (lead_id IS NOT NULL OR deal_id IS NOT NULL OR contact_id IS NOT "
            "NULL OR company_id IS NOT NULL)"))
    assert "module_record_id" not in _check(rt)[1]
    with rt.admin_engine.begin() as c:
        _exec_file(c, path)
    widened = _check(rt)
    assert "module_record_id IS NOT NULL" in widened[1] and widened[2]
    with rt.admin_engine.begin() as c:
        _exec_file(c, path)
    assert _check(rt) == widened, "the second run changes nothing"
    # Review: a replay must not add a second foreign key or a second index.
    assert _module_fks(rt) == [("crm_module_records", "c")]
    assert len(_module_indexes(rt)) == 1


# ── 3. The sync links an activity to a module record ─────────────────────────

NOTE_PARENT = {"id": "i1", "name": "A-Install"}


async def test_a_note_a_task_and_a_call_on_a_module_record_link_to_it(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    first = await _run(p.org_a)
    assert first.status == "ok"
    record = _records(p, p.org_a)["Install_Base", "i1"][10]

    # The module pass of the first run read the record. Now the activities.
    a.add("Notes", "n1", 60, Note_Title="On the install", Note_Content="Body",
          Parent_Id=NOTE_PARENT, **{"$se_module": "Install_Base"})
    a.add("Tasks", "t1", 61, Subject="Service it", Due_Date="2026-11-01",
          What_Id=NOTE_PARENT, **{"$se_module": "Install_Base"})
    a.add("Calls", "c1", 62, Subject="Rang them", Call_Start_Time=stamp(62),
          Who_Id={"id": "w-missing", "name": "x"}, What_Id=NOTE_PARENT,
          **{"$se_module": "Install_Base"})
    second = await _run(p.org_a)
    assert (second.status, second.error) == ("ok", None)
    rows = _activities(p, p.org_a)
    for ext_id, kind in (("n1", "note"), ("t1", "task"), ("c1", "call")):
        row = rows[ext_id]
        assert str(row[1]) == str(record), ext_id
        assert row[2:6] == (None, None, None, None), "no core column"
        assert row[6] == kind
    assert second.counts.get("activity_drops", {}) == {}
    assert second.counts["activities"]["failed"] == 0, "no bump on crm_module_records"


async def test_drops_count_by_module_and_a_custom_module_never_falls_back(mz) -> None:
    """Done when 4, and the no-fallback rule of done when 3. The activity pass
    runs before the module pass, so on the first import a note on a module
    record finds no row yet and is dropped (CRM-Q23b recovers it)."""
    p, a, _b = mz
    _seed(a)  # an Account a1, and the Install_Base record i1
    a.add("Notes", "n1", 30, Note_Title="Early", Parent_Id=NOTE_PARENT,
          **{"$se_module": "Install_Base"})
    # The id of an existing Account, under a custom module: no fallback.
    a.add("Notes", "n2", 31, Note_Title="Not a company",
          Parent_Id={"id": "a1", "name": "x"}, **{"$se_module": "Install_Base"})
    a.add("Notes", "n3", 32, Note_Title="Ghost", Parent_Id={"id": "g1", "name": "x"},
          **{"$se_module": "Ghost_Module"})
    a.add("Tasks", "t1", 33, Subject="Nowhere", What_Id={"id": "zz", "name": "x"})
    a.add("Notes", "n4", 34, Note_Title="Kept", Parent_Id={"id": "a1", "name": "x"},
          **{"$se_module": "Accounts"})
    await _connect_all(p, p.org_a, a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert result.counts["activity_drops"] == {
        "Install_Base": 2, "Ghost_Module": 1, "(none)": 1,
    }
    rows = _activities(p, p.org_a)
    assert set(rows) == {"n4"}
    assert rows["n4"][5] is not None, "a core se_module still links"
    stored = _q(p, "SELECT counts -> 'activity_drops' FROM crm_sync_runs WHERE "
                   "organization_id = CAST(:o AS uuid) ORDER BY started_at DESC LIMIT 1",
                o=p.org_a)
    assert stored[0][0] == {"Install_Base": 2, "Ghost_Module": 1, "(none)": 1}
    assert all(isinstance(v, int) for v in stored[0][0].values()), "numbers only"

    health = await _req(_admin_of(p.org_a), "GET", "/crm/sync/health")
    assert health.status_code == 200, health.text
    keys = [e["key"] for e in health.json()["entities"]]
    assert "activity_drops" not in keys
    assert "module:Install_Base" in keys and keys[:5] == [
        "organizations", "contacts", "leads", "deals", "activities"]
    status = await _req(_admin_of(p.org_a), "GET", "/crm/sync/status")
    assert status.status_code == 200, status.text
    assert status.json()["failed"] == 0


async def test_a_module_that_the_profile_does_not_mirror_stays_unlinked(mz) -> None:
    """A row left in the store from an earlier mirror does not count. The
    connection lacks the module scopes, so the profile mirrors no module."""
    p, a, _b = mz
    _seed(a)
    _module_record(p, p.org_a, "Install_Base", "i1", "Left over")
    a.add("Notes", "n1", 30, Note_Title="x", Parent_Id=NOTE_PARENT,
          **{"$se_module": "Install_Base"})
    await _connect(p, p.org_a, a)  # scopes from before CRM-Q6a
    result = await _run(p.org_a)
    assert result.status == "ok"
    assert result.counts["module:Install_Base"]["unavailable"] == 1
    assert _activities(p, p.org_a) == {}
    assert result.counts["activity_drops"] == {"Install_Base": 1}


async def test_each_org_links_only_to_its_own_module_record(mz) -> None:
    p, a, b = mz
    for z in (a, b):
        _seed(z)  # the SAME Zoho ids in both tenants
    await _connect_all(p, p.org_a, a)
    await _connect_all(p, p.org_b, b)
    for org in (p.org_a, p.org_b):
        assert (await _run(org)).status == "ok"
    for z in (a, b):
        z.add("Notes", "n1", 60, Note_Title=f"{z.tag}-note", Parent_Id=NOTE_PARENT,
              **{"$se_module": "Install_Base"})
    for org in (p.org_a, p.org_b):
        assert (await _run(org)).status == "ok"
    for org in (p.org_a, p.org_b):
        own = _records(p, org)["Install_Base", "i1"][10]
        assert str(_activities(p, org)["n1"][1]) == str(own)
    assert _records(p, p.org_a)["Install_Base", "i1"][10] != \
        _records(p, p.org_b)["Install_Base", "i1"][10]


# ── 5. The timeline of a module record ───────────────────────────────────────

PROFILE = [
    _profile_module("Install_Base", custom=True, label="Install Base"),
    _profile_module("Products"),
]


async def test_the_timeline_serves_the_activities_newest_first(rt) -> None:
    a = rt.org_a
    _seed_org(rt, a, mode="mirror", modules=PROFILE)
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    other = _module_record(rt, a, "Install_Base", "i2", "Pump 8")
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    older = _activity(rt, a, module_record=record, subject="Older", occurred=t0, zoho=True)
    newer = _activity(rt, a, module_record=record, kind="call", subject="Newer",
                      occurred=t0 + timedelta(days=2), zoho=True)
    _activity(rt, a, module_record=other, subject="Not this record", occurred=t0)

    seen: list[str] = []
    res = await _call(rt, a, "GET",
                      f"/crm/modules/Install_Base/records/{record}/timeline",
                      statements=seen)
    assert res.status_code == 200, res.text
    entries = res.json()["entries"]
    assert [e["activity"]["id"] for e in entries] == [newer, older]
    assert {e["kind"] for e in entries} == {"activity"}
    assert {e["activity"]["module_record_id"] for e in entries} == {record}
    assert entries[0]["activity"]["deal_id"] is None
    # Read only: no statement writes.
    assert not [s for s in seen if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", s, re.I)]

    page = await _call(rt, a, "GET", f"/crm/modules/Install_Base/records/{record}/"
                                     "timeline?offset=0&limit=1")
    assert [e["activity"]["id"] for e in page.json()["entries"]] == [newer]
    assert page.json()["has_more"] is True
    last = await _call(rt, a, "GET", f"/crm/modules/Install_Base/records/{record}/"
                                     "timeline?offset=1&limit=1")
    assert [e["activity"]["id"] for e in last.json()["entries"]] == [older]
    assert last.json()["has_more"] is False


async def test_the_timeline_answers_404_for_an_unknown_module_or_record(rt) -> None:
    a = rt.org_a
    _seed_org(rt, a, mode="mirror", modules=PROFILE)
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    product = _module_record(rt, a, "Products", "p1", "Widget")
    for module, rid in (
        ("Nope", record),                         # not in the profile
        ("Leads", record),                        # a core module
        ("Install_Base' OR '1'='1", record),      # never reaches SQL text
        ("Install_Base", product),                # a record of another module
        ("Install_Base", str(uuid.uuid4())),      # no such record
    ):
        res = await _call(rt, a, "GET", f"/crm/modules/{module}/records/{rid}/timeline")
        assert res.status_code == 404, (module, res.text)
    bad = await _call(rt, a, "GET", "/crm/modules/Install_Base/records/not-a-uuid/timeline")
    assert bad.status_code == 422


async def test_the_timeline_keeps_tenant_isolation(rt) -> None:
    a, b = rt.org_a, rt.org_b
    _seed_org(rt, a, mode="mirror", modules=PROFILE)
    _seed_org(rt, b, mode="mirror", modules=PROFILE)
    a_rec = _module_record(rt, a, "Install_Base", "i1", "A-Pump")
    b_rec = _module_record(rt, b, "Install_Base", "i1", "B-Pump")  # the same Zoho id
    _activity(rt, a, module_record=a_rec, subject="A-note", zoho=True)
    b_note = _activity(rt, b, module_record=b_rec, subject="B-note", zoho=True)

    stolen = await _call(rt, b, "GET", f"/crm/modules/Install_Base/records/{a_rec}/timeline")
    assert stolen.status_code == 404
    own = await _call(rt, b, "GET", f"/crm/modules/Install_Base/records/{b_rec}/timeline")
    assert [e["activity"]["id"] for e in own.json()["entries"]] == [b_note]
    assert "A-note" not in own.text


async def test_a_native_org_has_no_module_timeline(rt) -> None:
    a = rt.org_a
    _seed_org(rt, a, mode="native", modules=PROFILE)
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    res = await _call(rt, a, "GET", f"/crm/modules/Install_Base/records/{record}/timeline")
    assert res.status_code == 404


# ── 6. The readers that assume a core parent ─────────────────────────────────


async def test_edit_and_delete_of_a_module_only_activity(rt) -> None:
    a, b = rt.org_a, rt.org_b
    # Mirror mode: a mirrored row is Zoho's, the same 409 as any other.
    _seed_org(rt, a, mode="mirror", modules=PROFILE)
    a_rec = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    mirrored = _activity(rt, a, module_record=a_rec, zoho=True)
    edit = await _call(rt, a, "PATCH", f"/crm/activities/{mirrored}", {"subject": "y"})
    assert edit.status_code == 409 and edit.json()["detail"]["code"] == "mirrored_record"
    gone = await _call(rt, a, "DELETE", f"/crm/activities/{mirrored}")
    assert gone.status_code == 409

    # Native mode (a row left from an earlier mirror): the edit keeps the parent.
    await _native(rt, b)
    b_rec = _module_record(rt, b, "Install_Base", "i1", "Pump 7")
    native = _activity(rt, b, module_record=b_rec, kind="task", subject="Old")
    edit = await _call(rt, b, "PATCH", f"/crm/activities/{native}",
                       {"subject": "New", "completed_at": "2026-10-16T09:00:00+00:00"})
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert (body["subject"], body["module_record_id"]) == ("New", b_rec)
    assert body["deal_id"] is None and body["completed_at"] is not None
    gone = await _call(rt, b, "DELETE", f"/crm/activities/{native}")
    assert gone.status_code == 200, gone.text
    assert _q(rt, "SELECT id FROM crm_activities WHERE id = CAST(:i AS uuid)", i=native) == []
    assert _q(rt, "SELECT id FROM crm_module_records WHERE id = CAST(:i AS uuid)",
              i=b_rec), "the record stays"


async def test_the_next_activity_reads_ignore_a_module_only_task(rt) -> None:
    a = rt.org_a
    stages = await _native(rt, a)
    stage = next(iter(stages.values()))
    with rt.admin_engine.begin() as c:
        deal = str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id) VALUES "
            "(CAST(:o AS uuid), 'Bare deal', CAST(:s AS uuid)) RETURNING id"),
            {"o": a, "s": stage}).scalar())
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    tomorrow = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) \
        + timedelta(days=1)
    _activity(rt, a, module_record=record, kind="task", due=tomorrow)

    listed = await _call(rt, a, "GET", "/crm/deals")
    assert listed.status_code == 200, listed.text
    row = next(r for r in listed.json()["rows"] if r["id"] == deal)
    assert row["next_activity"] is None
    for path in ("/crm/pipeline", "/crm/pipeline?card_sort=next_activity", "/crm/leads"):
        res = await _call(rt, a, "GET", path)
        assert res.status_code == 200, (path, res.text)
    board = (await _call(rt, a, "GET", "/crm/pipeline?card_sort=next_activity")).json()
    cards = [card for lane in board["lanes"] for card in lane["rows"]]
    assert [c["next_activity"] for c in cards if c["id"] == deal] == [None]


async def test_the_export_zip_carries_the_module_record_of_an_activity(rt) -> None:
    a = rt.org_a
    _seed_org(rt, a, mode="mirror", modules=PROFILE)
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    activity = _activity(rt, a, module_record=record, subject="Zipped", zoho=True)
    res = await _call(rt, a, "GET", "/crm/export/all.zip")
    assert res.status_code == 200, res.text
    with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
        rows = list(csv.DictReader(io.StringIO(
            archive.read("activities.csv").decode("utf-8-sig"))))
        records = list(csv.DictReader(io.StringIO(
            archive.read("module_records.csv").decode("utf-8-sig"))))
    row = next(r for r in rows if r["id"] == activity)
    assert row["module_record_id"] == record
    assert row["deal_id"] == "" and row["subject"] == "Zipped"
    assert [r["id"] for r in records] == [record]
    deals = await _call(rt, a, "GET", "/crm/export/deals.csv")
    assert deals.status_code == 200, deals.text


async def test_push_activities_never_pushes_a_module_only_activity(rt, monkeypatch) -> None:
    from acb_common.db import tenant_session
    from gateway.routes.crm import sync_zoho

    a = rt.org_a
    stages = await _native(rt, a)
    stage = next(iter(stages.values()))
    with rt.admin_engine.begin() as c:
        deal = str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, zoho_id) VALUES "
            "(CAST(:o AS uuid), 'Pushed deal', CAST(:s AS uuid), 'zd1') RETURNING id"),
            {"o": a, "s": stage}).scalar())
    record = _module_record(rt, a, "Install_Base", "i1", "Pump 7")
    _activity(rt, a, module_record=record, subject="Mirrored", zoho=True)
    _activity(rt, a, module_record=record, subject="Native, no zoho_id")
    control = _activity(rt, a, deal=deal, subject="A native note on a deal")

    calls: list[dict[str, Any]] = []

    async def _push_one(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"pending": True}

    monkeypatch.setattr(sync_zoho, "push_one", _push_one)
    report = sync_zoho.SyncCycleReport(started_at=datetime.now(UTC).isoformat())
    async with (
        tenant_engine_scope(rt.app_url.render_as_string(hide_password=False)),
        tenant_session(a) as db,
    ):
        await sync_zoho.push_activities(db, report)
    assert [c["native_id"] for c in calls] == [control], "only the core-parent note"
    assert calls[0]["body"]["Parent_Id"] == "zd1"
    assert report.pushed.queued == 1
