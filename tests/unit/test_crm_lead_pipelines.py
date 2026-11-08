"""R5 + R6 + R7 + R8 fence — lead pipelines on the one pipeline seam (WS-CRM CRM-M4a).

Spec: ``project-docs/specs/crm_conversational.md`` §8 CRM-M4. Migration 360
(``infra/postgres/360_crm_lead_pipelines.sql``, found here by CONTENT, R1).
Route: ``POST /crm/pipelines`` with ``kind=lead``, its preview and
``GET /crm/pipelines?kind=lead`` in
``apps/services/gateway/gateway/routes/crm/pipelines.py``.

**What this file holds.**

- The migration is expand only. Old code that names no kind meets the new
  schema and gets a deal pipeline. The CHECKs hold the kind and keep a feed
  off a deal pipeline. A second run of the file changes nothing.
- Through the real ``/crm`` router, against a real Postgres, as the
  NON-privileged app role (R8): a lead pipeline from a list and from a copy,
  "Trade show", "Web" and "Outbound" each with its own statuses, the org's
  own won and lost statuses, ``feeds`` and its refusals, the Undo, and a
  second org that reads zero rows.
- A lead pipeline never reaches a deal reader: the deal list, the board, a
  new deal, a convert, the default, the reports and the stage routes.
- A lead with no pipeline behaves as before.

⚠️ Every database test here runs in the R8 lane (``pytestmark``). Without a
ladder database they skip, and ``ci_unit_guard.py --must-run`` fails CI on that
skip.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_change_history import (  # noqa: F401 — ns is a fixture
    _admin,
    _member,
    _one,
    _rows,
    _settings,
    ns,
)
from tests.unit.test_crm_pipelines_native import (  # noqa: F401 — pns is a fixture
    STAMP,
    TOKEN,
    _call,
    _code,
    _count,
    _seed,
    _words,
    pns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"


# ── The migration, as text (R1, R6) ─────────────────────────────────────────


def _migration() -> Path:
    found = [
        p for p in sorted(_MIGRATIONS.glob("[0-9]*.sql"))
        if "CREATE TABLE IF NOT EXISTS crm_lead_pipeline_stages" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _bare(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def test_the_migration_takes_a_free_number_and_says_why() -> None:
    path = _migration()
    numbers = [int(p.name.split("_", 1)[0]) for p in _MIGRATIONS.glob("[0-9]*.sql")]
    mine = int(path.name.split("_", 1)[0])
    assert numbers.count(mine) == 1, f"two migrations share number {mine}"
    header = path.read_text(encoding="utf-8").split("\nBEGIN;", 1)[0]
    for required in ("What:", "Why:", "Depends on:", "Expand only (R6)", "R5"):
        assert required in header, required


def test_the_migration_is_expand_only() -> None:
    """R6: no drop, no rename, no row of data. ``kind`` has a constant
    default, so old code that names none gets a deal pipeline."""
    bare = _bare(_migration().read_text(encoding="utf-8"))
    for forbidden in ("DROP TABLE", "DROP COLUMN", "RENAME", "TRUNCATE", "DELETE FROM",
                      "UPDATE crm_", "INSERT INTO crm_"):
        assert forbidden not in bare, forbidden
    assert "ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'deal'" in bare
    assert re.search(r"feeds_pipeline_id UUID NULL\s+REFERENCES crm_pipelines \(id\) "
                     r"ON DELETE SET NULL", bare)
    assert re.search(r"ALTER TABLE crm_leads\s+ADD COLUMN IF NOT EXISTS pipeline_id UUID NULL",
                     bare)
    assert "CREATE INDEX IF NOT EXISTS crm_leads_pipeline_idx ON crm_leads (pipeline_id)" in bare
    # Each CHECK goes in NOT VALID, and a guarded block validates it.
    for check in ("crm_pipelines_kind", "crm_pipelines_feeds_lead"):
        assert re.search(rf"ADD CONSTRAINT {check}\s+CHECK \([^;]*\) NOT VALID", bare), check
        assert f"VALIDATE CONSTRAINT {check}" in bare, check
    assert "CHECK (kind IN ('deal', 'lead'))" in bare
    assert "SET LOCAL lock_timeout" in bare


def test_the_new_table_is_tenant_scoped_with_force_rls() -> None:
    """R5: the tenant key with REFERENCES before DEFAULT, ENABLE and FORCE, and
    one policy with USING and WITH CHECK, as ``crm_pipeline_stages`` has."""
    bare = _bare(_migration().read_text(encoding="utf-8"))
    table = bare[bare.index("CREATE TABLE IF NOT EXISTS crm_lead_pipeline_stages"):]
    table = table[:table.index(");")]
    assert re.search(r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) "
                     r"ON DELETE CASCADE\s+DEFAULT current_setting\('app\.tenant_id', true\)"
                     r"::uuid", table)
    assert "REFERENCES crm_pipelines (id) ON DELETE CASCADE" in table
    assert "REFERENCES crm_lead_statuses (id) ON DELETE CASCADE" in table
    assert "PRIMARY KEY (pipeline_id, status_id)" in table
    assert "ALTER TABLE crm_lead_pipeline_stages ENABLE ROW LEVEL SECURITY" in bare
    assert "ALTER TABLE crm_lead_pipeline_stages FORCE  ROW LEVEL SECURITY" in bare
    assert "USING      (organization_id = current_setting('app.tenant_id', true)::uuid)" in bare
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in bare


def test_the_new_table_is_in_every_list_that_names_the_crm_tables() -> None:
    """The start over empties it, a Mirror org writes none, and the generated
    phases carry it."""
    from gateway.routes.crm import core, settings

    assert "crm_lead_pipeline_stages" in core.RESET_TABLES
    assert "crm_lead_pipeline_stages" in settings._RESET_DELETES
    assert "crm_lead_pipeline_stages" in core.MIRROR_SETTINGS_TABLES
    policies = (_MIGRATIONS / "generated" / "04_policies.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE crm_lead_pipeline_stages FORCE  ROW LEVEL SECURITY" in policies


def test_the_seed_keeps_the_deal_term_of_core() -> None:
    """``seed.py`` is a leaf, so it keeps its own copy of the term."""
    from gateway.routes.crm import core, seed

    assert seed.DEAL_PIPELINE == core.DEAL_PIPELINE_SQL == "kind = 'deal'"


#: The terms that let a read of ``crm_pipelines`` pass: it names the kind, or
#: it reads one row by its id and leaves the kind to its caller.
_KIND_OR_ID = re.compile(
    r"kind = :kind|kind = 'deal'|kind = 'lead'|DEAL_PIPELINE_SQL|WHERE id = CAST"
)


def test_each_read_of_crm_pipelines_names_the_kind_or_an_id() -> None:
    """Acceptance 2. A deal reader names ``kind = 'deal'``, so a lead pipeline
    never shows on a deal surface. A read by id names the id, and its caller
    checks the kind (``core.require_deal_pipeline``, or the kind of the Undo
    row). Mutation: drop the term from ``default_pipeline_id`` -> red here and
    in the database test below."""
    misses: list[str] = []
    by_id: list[str] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        where = str(path.relative_to(_GATEWAY)).replace("\\", "/")
        for match in re.finditer(r"FROM crm_pipelines\b", source):
            # The statement: this line and the few lines after it.
            if not _KIND_OR_ID.search(source[match.start():match.start() + 260]):
                misses.append(f"{where}:{source.count(chr(10), 0, match.start()) + 1}")
        by_id += [where] * len(re.findall(
            r"(?:require|load)_row\(\s*db,\s*\"crm_pipelines\"", source))
    assert misses == [], misses
    # The id reads: the one deal check of core, and the two Undos of this seam.
    assert sorted(by_id) == ["routes/crm/core.py", "routes/crm/pipelines.py",
                             "routes/crm/pipelines.py"], by_id


def test_this_suite_is_named_in_both_must_run_lists() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_crm_lead_pipelines\.py \\?$", workflow, re.M), name


def test_the_body_takes_a_kind_and_feeds_and_no_tenant() -> None:
    from gateway.routes.crm.pipelines import PIPELINE_KINDS, PipelineCreateIn
    from pydantic import ValidationError

    assert set(PIPELINE_KINDS) == {"deal", "lead"}
    body = PipelineCreateIn.model_validate(
        {"name": "Web", "kind": "lead", "stages": [{"name": "New"}], "feeds": "Sales"})
    assert (body.kind, body.feeds) == ("lead", "Sales")
    with pytest.raises(ValidationError):
        PipelineCreateIn.model_validate({"name": "Web", "kind": "lead", "stages": [],
                                         "organization_id": "x"})


# ── The database tests ──────────────────────────────────────────────────────


def _leads(p: Any, org: str) -> dict[str, str]:
    """The org's lead statuses: New, Contacted, Qualified (won), Lost."""
    ids: dict[str, str] = {}
    for key, name, kind, pos in (("new", "New", "open", 10), ("contacted", "Contacted", "ongoing", 20),
                                 ("qualified", "Qualified", "won", 40), ("lost", "Lost", "lost", 50)):
        ids[key] = str(_one(
            p, "INSERT INTO crm_lead_statuses (organization_id, name, type, position) "
               "VALUES (CAST(:o AS uuid), :n, :t, :pos) RETURNING id",
            o=org, n=name, t=kind, pos=pos))
    return ids


def _lead(p: Any, org: str, status: str, name: str = "Ravi Menon",
          pipeline: str | None = None) -> str:
    return str(_one(
        p, "INSERT INTO crm_leads (organization_id, lead_name, status_id, pipeline_id) "
           "VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid), CAST(:pl AS uuid)) RETURNING id",
        o=org, n=name, s=status, pl=pipeline))


TRADE_SHOW = {"name": "Trade show", "kind": "lead", "stages": [
    {"name": "New"}, {"name": "Booth visit"}, {"name": "Demo booked"},
    {"name": "Converted"}, {"name": "Junk"}]}
WEB = {"name": "Web", "kind": "lead", "stages": [
    {"name": "New"}, {"name": "Form filled"}, {"name": "Contacted"}]}
OUTBOUND = {"name": "Outbound", "kind": "lead", "stages": [
    {"name": "Cold list"}, {"name": "Replied", "type": "open"},
    {"name": "Meeting set", "type": "won"}, {"name": "No answer", "type": "lost"}]}


def _lead_pipelines(p: Any, org: str) -> list[Any]:
    with p.admin_engine.begin() as c:
        return list(c.execute(text(
            "SELECT id, name, kind, feeds_pipeline_id, is_default FROM crm_pipelines "
            "WHERE organization_id = CAST(:o AS uuid) AND kind = 'lead' ORDER BY position"),
            {"o": org}).fetchall())


def _members(p: Any, pipeline: str) -> list[str]:
    with p.admin_engine.begin() as c:
        return [r.name for r in c.execute(text(
            "SELECT s.name FROM crm_lead_pipeline_stages ps JOIN crm_lead_statuses s "
            "ON s.id = ps.status_id WHERE ps.pipeline_id = CAST(:p AS uuid) "
            "ORDER BY ps.position"), {"p": pipeline})]


async def test_an_admin_makes_trade_show_web_and_outbound_each_with_its_own_statuses(
    pns, app_engine,  # noqa: F811
) -> None:
    """Done when 1. Three lead pipelines from three lists. A name that the CRM
    holds reuses that status, a new name makes a lead status, and "Converted"
    and "Junk" close as they read. The deal list does not change."""
    deal = _seed(pns, pns.org_a)
    statuses = _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = {}
    for body in (TRADE_SHOW, WEB, OUTBOUND):
        r = await _call(pns, admin, "POST", "/crm/pipelines", body)
        assert r.status_code == 201, r.text
        made[body["name"]] = r.json()
        assert made[body["name"]]["kind"] == "lead"
    trade = made["Trade show"]
    assert [(s["name"], s["type"]) for s in trade["stages"]] == [
        ("New", "open"), ("Booth visit", "open"), ("Demo booked", "open"),
        ("Converted", "won"), ("Junk", "lost")]
    assert trade["stages"][0]["id"] == statuses["new"], "New is the one row of the CRM"
    # Web lists no closing status, so the org's own won and lost join it.
    assert [s["name"] for s in made["Web"]["stages"]] == [
        "New", "Form filled", "Contacted", "Qualified", "Lost"]
    assert [s["name"] for s in made["Outbound"]["stages"]] == [
        "Cold list", "Replied", "Meeting set", "No answer"]
    # Each pipeline holds its own statuses, in order, in the new table.
    for name, body in made.items():
        assert _members(pns, body["id"]) == [s["name"] for s in body["stages"]], name
    # One "New" row, in two lead pipelines.
    assert _one(pns, "SELECT count(*) FROM crm_lead_pipeline_stages WHERE status_id = "
                     "CAST(:s AS uuid)", s=statuses["new"]) == 2
    # New lead statuses are org-wide rows; won ones forecast 100.
    assert _one(pns, "SELECT probability FROM crm_lead_statuses WHERE organization_id = "
                     "CAST(:o AS uuid) AND name = 'Meeting set'", o=pns.org_a) == 100
    # The lead list reads the three, and the deal list reads the deal one only.
    leads = (await _call(pns, admin, "GET", "/crm/pipelines", params={"kind": "lead"})).json()
    assert [p["name"] for p in leads] == ["Trade show", "Web", "Outbound"]
    assert all(p["kind"] == "lead" and not p["is_default"] for p in leads)
    deals = (await _call(pns, admin, "GET", "/crm/pipelines")).json()
    assert [(p["name"], p["kind"]) for p in deals] == [("Sales pipeline", "deal")]
    assert [s["id"] for s in deals[0]["stages"]] == [
        deal["qualify"], deal["proposal"], deal["won"], deal["lost"]]
    # Change history: one pipeline row each, with the kind in "after".
    rows = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    assert len(rows) == 3 and {x.after["kind"] for x in rows} == {"lead"}
    trade_row = next(x for x in rows if x.entity_id == trade["id"])
    assert trade_row.summary == 'Created the lead pipeline "Trade show" with 5 statuses'
    assert trade_row.ladder_level == "company" and trade_row.via == "ui"
    made_statuses = [x for x in _rows(pns, pns.org_a) if x.area == "lead_status"]
    assert {x.after["name"] for x in made_statuses} == {
        "Booth visit", "Demo booked", "Converted", "Junk", "Form filled", "Cold list",
        "Replied", "Meeting set", "No answer"}


async def test_a_lead_pipeline_from_a_copy_takes_the_statuses_in_order(pns) -> None:  # noqa: F811
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    web = (await _call(pns, admin, "POST", "/crm/pipelines", WEB)).json()
    r = await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "Web retarget", "kind": "lead", "copy_from": web["id"]})
    assert r.status_code == 201, r.text
    assert [s["id"] for s in r.json()["stages"]] == [s["id"] for s in web["stages"]]
    (row,) = [x for x in _rows(pns, pns.org_a)
              if x.area == "pipeline" and x.entity_id == r.json()["id"]]
    assert row.after["copy_of"] == "Web" and row.after["kind"] == "lead"
    assert row.summary == 'Created the lead pipeline "Web retarget", a copy of "Web", with 5 statuses'


async def test_a_copy_never_crosses_the_kinds(pns) -> None:  # noqa: F811
    """A lead pipeline copies a lead pipeline, and a deal one a deal one."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    web = (await _call(pns, admin, "POST", "/crm/pipelines", WEB)).json()
    for body in ({"name": "X", "kind": "lead", "copy_from": deal["pipeline"]},
                 {"name": "X", "copy_from": web["id"]}):
        r = await _call(pns, admin, "POST", "/crm/pipelines", body)
        assert r.status_code == 404 and _code(r) == "no_pipeline", r.text
        assert "to copy with that id" in _words(r)


async def test_a_lead_pipeline_holds_an_open_status_and_the_closing_ones(pns) -> None:  # noqa: F811
    """The working default of §8 CRM-M4: one open status, and the org's own
    won and lost statuses when the list has none."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    r = await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "X", "kind": "lead", "stages": [{"name": "Converted"}, {"name": "Junk"}]})
    assert r.status_code == 422, r.text
    assert "A lead pipeline needs at least one open status" in _words(r)
    plan = await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "Events", "kind": "lead", "stages": json.dumps([{"name": "Met at booth"}])})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["kind"] == "lead" and body["feeds"] is None
    assert [(s["name"], s["type"], s["source"]) for s in body["stages"]] == [
        ("Met at booth", "open", "new"), ("Qualified", "won", "added"), ("Lost", "lost", "added")]
    assert _count(pns, "crm_lead_statuses", pns.org_a) == 4, "the preview writes nothing"


async def test_an_org_with_no_won_status_is_told_to_add_one(pns) -> None:  # noqa: F811
    _seed(pns, pns.org_a)
    _one(pns, "INSERT INTO crm_lead_statuses (organization_id, name, type, position) "
              "VALUES (CAST(:o AS uuid), 'New', 'open', 10) RETURNING id", o=pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", {
        "name": "X", "kind": "lead", "stages": [{"name": "New"}, {"name": "Junk"}]})
    assert r.status_code == 422, r.text
    assert "This CRM has no won status yet" in _words(r)
    assert _count(pns, "crm_pipelines", pns.org_a) == 1


async def test_feeds_names_a_deal_pipeline_of_the_org(pns, app_engine) -> None:  # noqa: F811
    """The lead pipeline names the deal pipeline it feeds. The preview shows
    it, the row keeps it, and the list answers its id."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    body = {**TRADE_SHOW, "feeds": "  sales PIPELINE "}
    plan = await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": body["name"], "kind": "lead", "feeds": body["feeds"],
        "stages": json.dumps(body["stages"])})
    assert plan.status_code == 200 and plan.json()["feeds"] == "Sales pipeline", plan.text
    r = await _call(pns, admin, "POST", "/crm/pipelines", body)
    assert r.status_code == 201, r.text
    assert r.json()["feeds_pipeline_id"] == deal["pipeline"]
    (row,) = _lead_pipelines(pns, pns.org_a)
    assert str(row.feeds_pipeline_id) == deal["pipeline"]
    (logged,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    assert logged.after["feeds"] == {"id": deal["pipeline"], "name": "Sales pipeline"}
    assert logged.summary.endswith('It feeds deals into "Sales pipeline"')
    # A lead pipeline with no feed leaves the key out: the default deal pipeline.
    web = (await _call(pns, admin, "POST", "/crm/pipelines", WEB)).json()
    assert "feeds_pipeline_id" not in web
    # A deal pipeline has no feed, so the deal list is as before.
    assert "feeds_pipeline_id" not in (await _call(pns, admin, "GET", "/crm/pipelines")).json()[0]


async def test_feeds_is_refused_across_orgs_and_for_a_deal_pipeline(pns, app_engine) -> None:  # noqa: F811
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    other = _seed(pns, pns.org_b)
    _one(pns, "UPDATE crm_pipelines SET name = 'Partner deals' WHERE id = CAST(:pl AS uuid) "
              "RETURNING id", pl=other["pipeline"])
    admin = _admin(pns.org_a)
    cases = [
        # Org B holds "Partner deals". Under RLS org A cannot see it.
        ({**WEB, "feeds": "Partner deals"}, 404, "no_pipeline"),
        ({**WEB, "feeds": "Nowhere"}, 404, "no_pipeline"),
        ({"name": "AMC", "stages": [{"name": "Site visit"}], "feeds": "Sales pipeline"}, 422, None),
        ({"name": "AMC", "kind": "contact", "stages": [{"name": "A"}]}, 422, None),
    ]
    for body, status, code in cases:
        r = await _call(pns, admin, "POST", "/crm/pipelines", body)
        assert r.status_code == status, (body, r.text)
        assert _code(r) == code, (body, r.text)
        assert _words(r).strip(), (body, "a refusal with no words")
    assert "There is no deal pipeline called" in _words(
        await _call(pns, admin, "POST", "/crm/pipelines", cases[0][0]))
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_lead_statuses", pns.org_a) == 4
    # The database refuses a cross-org feed too, for the app role.
    web = (await _call(pns, admin, "POST", "/crm/pipelines", WEB)).json()
    for target in (other["pipeline"], web["id"]):
        with pytest.raises(Exception, match="feeds a deal pipeline of its own organization"), \
                app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_a})
            c.execute(text("UPDATE crm_pipelines SET feeds_pipeline_id = CAST(:f AS uuid) "
                           "WHERE id = CAST(:p AS uuid)"), {"f": target, "p": web["id"]})


async def test_a_second_org_reads_zero_lead_pipeline_rows(pns, app_engine) -> None:  # noqa: F811
    """Done when 4 (R5, R8). Org B reads none of org A's lead pipelines or
    their statuses, through the route and through a bare SELECT as the app
    role, and it writes no row that names them."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    trade = (await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", TRADE_SHOW)).json()
    assert _one(pns, "SELECT count(*) FROM crm_lead_pipeline_stages WHERE organization_id = "
                     "CAST(:o AS uuid)", o=pns.org_a) == 5
    other = _admin(pns.org_b)
    assert (await _call(pns, other, "GET", "/crm/pipelines", params={"kind": "lead"})).json() == []
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_b})
        assert c.execute(text("SELECT count(*) FROM crm_lead_pipeline_stages")).scalar() == 0
        assert c.execute(text(
            "SELECT count(*) FROM crm_pipelines WHERE kind = 'lead'")).scalar() == 0
    # Org B cannot put its own status into org A's pipeline: RLS hides the
    # pipeline, so the guard of the table refuses.
    b_status = _leads(pns, pns.org_b)["new"]
    with pytest.raises(Exception, match="not a lead pipeline of this organization"), \
            app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_b})
        c.execute(text(
            "INSERT INTO crm_lead_pipeline_stages (pipeline_id, status_id, position) "
            "VALUES (CAST(:p AS uuid), CAST(:s AS uuid), 9)"), {"p": trade["id"], "s": b_status})
    # Org B may use the same name: the name check is inside the org and the kind.
    _seed(pns, pns.org_b)
    mine = await _call(pns, other, "POST", "/crm/pipelines", TRADE_SHOW)
    assert mine.status_code == 201, mine.text


async def test_a_deal_pipeline_and_a_lead_pipeline_may_share_a_name(pns) -> None:  # noqa: F811
    """Trade show leads can feed Trade show deals. Two lead pipelines of one
    name are refused, as two deal pipelines are."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    deal = await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "Trade show", "stages": [{"name": "Booth demo"}]})
    assert deal.status_code == 201, deal.text
    lead = await _call(pns, admin, "POST", "/crm/pipelines", {**TRADE_SHOW, "feeds": "Trade show"})
    assert lead.status_code == 201, lead.text
    assert lead.json()["feeds_pipeline_id"] == deal.json()["id"]
    again = await _call(pns, admin, "POST", "/crm/pipelines", {**WEB, "name": " trade SHOW"})
    assert again.status_code == 409 and _code(again) == "name_taken", again.text
    assert "A lead pipeline named" in _words(again)


async def test_a_lead_pipeline_never_reaches_a_deal_reader(pns) -> None:  # noqa: F811
    """Acceptance 2. The deal list, the board, a new deal, a moved deal, a
    convert, the default, the reports and the stage routes read deal
    pipelines only. The lead pipeline sorts first by position, so a reader that
    drops the kind picks it."""
    deal = _seed(pns, pns.org_a)
    statuses = _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    trade = (await _call(pns, admin, "POST", "/crm/pipelines", TRADE_SHOW)).json()
    _one(pns, "UPDATE crm_pipelines SET position = -5 WHERE id = CAST(:pl AS uuid) RETURNING id",
         pl=trade["id"])
    lead_id = trade["id"]
    assert [p["name"] for p in (await _call(pns, admin, "GET", "/crm/pipelines")).json()] == [
        "Sales pipeline"]
    board = await _call(pns, admin, "GET", "/crm/pipeline", params={"pipeline_id": lead_id})
    assert board.status_code == 404, board.text
    made = await _call(pns, admin, "POST", "/crm/deals", {
        "name": "Acme", "pipeline_id": lead_id, "status_id": deal["qualify"]})
    assert made.status_code == 404, made.text
    # A deal with no stated pipeline joins the deal default, never the lead one.
    plain = await _call(pns, admin, "POST", "/crm/deals", {"name": "Plain", "status_id": deal["qualify"]})
    assert plain.status_code == 201, plain.text
    assert plain.json()["pipeline_id"] == deal["pipeline"]
    moved = await _call(pns, admin, "PATCH", f"/crm/deals/{plain.json()['id']}",
                        {"pipeline_id": lead_id})
    assert moved.status_code == 404, moved.text
    # The deal stage routes and the rename act on deal pipelines only.
    for method, path, body in (
        ("PATCH", f"/crm/pipelines/{lead_id}", {"name": "Renamed"}),
        ("POST", f"/crm/pipelines/{lead_id}/stages", {"status_id": deal["proposal"]}),
        ("DELETE", f"/crm/pipelines/{lead_id}/stages/{statuses['new']}", None),
    ):
        r = await _call(pns, admin, method, path, body)
        assert r.status_code == 404, (method, path, r.text)
    # The reports group deals by deal pipelines only.
    grouped = await _call(pns, admin, "GET", "/crm/aggregate",
                          params={"entity": "deals", "group_by": "pipeline"})
    assert grouped.status_code == 200, grouped.text
    assert "Trade show" not in grouped.text
    # A convert lands its deal in the deal default, and refuses the lead pipeline.
    lead = _lead(pns, pns.org_a, statuses["new"])
    refused = await _call(pns, admin, "POST", f"/crm/leads/{lead}/convert",
                          {"pipeline_id": lead_id, "deal": {"name": "Acme AMC"}})
    assert refused.status_code == 404, refused.text
    converted = await _call(pns, admin, "POST", f"/crm/leads/{lead}/convert",
                            {"deal": {"name": "Acme AMC"}})
    assert converted.status_code == 200, converted.text
    assert converted.json()["deal"]["pipeline_id"] == deal["pipeline"]
    # The deal statuses are as they were: a lead create makes no deal stage.
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4
    assert _one(pns, "SELECT count(*) FROM crm_pipeline_stages WHERE pipeline_id = "
                     "CAST(:pl AS uuid)", pl=lead_id) == 0


async def test_a_lead_with_no_pipeline_behaves_as_before(pns) -> None:  # noqa: F811
    """Done when 5. A lead made before the lead pipelines keeps a NULL pipeline,
    reads and moves as before, and the lead statuses list every status."""
    _seed(pns, pns.org_a)
    statuses = _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    lead = _lead(pns, pns.org_a, statuses["new"], name="Old lead")
    before = (await _call(pns, admin, "GET", f"/crm/leads/{lead}")).json()
    for body in (TRADE_SHOW, WEB):
        assert (await _call(pns, admin, "POST", "/crm/pipelines", body)).status_code == 201
    after = (await _call(pns, admin, "GET", f"/crm/leads/{lead}")).json()
    assert {k: v for k, v in after.items() if k != "updated_at"} == {
        k: v for k, v in before.items() if k != "updated_at"}
    assert "pipeline_id" not in after
    listed = (await _call(pns, admin, "GET", "/crm/leads")).json()
    assert [r["id"] for r in listed["rows"]] == [lead]
    moved = await _call(pns, admin, "PATCH", f"/crm/leads/{lead}",
                        {"status_id": statuses["contacted"]})
    assert moved.status_code == 200, moved.text
    assert _one(pns, "SELECT pipeline_id FROM crm_leads WHERE id = CAST(:l AS uuid)", l=lead) is None
    # The lead statuses list holds every status, the new ones too.
    names = [s["name"] for s in (await _call(pns, admin, "GET", "/crm/statuses/lead")).json()]
    assert {"New", "Contacted", "Qualified", "Lost", "Booth visit", "Form filled"} <= set(names)
    # A new lead names no pipeline, as before.
    made = await _call(pns, admin, "POST", "/crm/leads", {"lead_name": "New lead"})
    assert made.status_code == 201, made.text
    assert _one(pns, "SELECT pipeline_id FROM crm_leads WHERE id = CAST(:l AS uuid)",
                l=made.json()["id"]) is None


async def test_the_undo_of_a_lead_pipeline_removes_it_and_the_statuses_it_made(pns) -> None:  # noqa: F811
    """The Undo deletes the pipeline and each lead status that the create made,
    with one row each. "New", which it reused, stays. It refuses while a lead
    names the pipeline, and while a new status holds a lead."""
    _seed(pns, pns.org_a)
    statuses = _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    trade = (await _call(pns, admin, "POST", "/crm/pipelines", TRADE_SHOW)).json()
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    booth = trade["stages"][1]["id"]
    # A lead of the pipeline holds it.
    held = _lead(pns, pns.org_a, statuses["new"], pipeline=trade["id"])
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "in_use", undo.text
    assert 'The lead pipeline "Trade show" holds 1 lead now' in _words(undo)
    _one(pns, "DELETE FROM crm_leads WHERE id = CAST(:l AS uuid) RETURNING id", l=held)
    # A lead with no pipeline in "Booth visit", which the create made.
    loose = _lead(pns, pns.org_a, booth, name="Loose")
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "in_use", undo.text
    assert 'The status "Booth visit", which this change made, holds 1 lead now' in _words(undo)
    _one(pns, "DELETE FROM crm_leads WHERE id = CAST(:l AS uuid) RETURNING id", l=loose)
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    out = undo.json()["change"]
    assert out["action"] == "delete" and out["before"]["kind"] == "lead"
    assert _lead_pipelines(pns, pns.org_a) == []
    assert _one(pns, "SELECT count(*) FROM crm_lead_pipeline_stages WHERE organization_id = "
                     "CAST(:o AS uuid)", o=pns.org_a) == 0
    names = {s["name"] for s in (await _call(pns, admin, "GET", "/crm/statuses/lead")).json()}
    assert names == {"New", "Contacted", "Qualified", "Lost"}
    gone = [x for x in _rows(pns, pns.org_a) if x.area == "lead_status" and x.action == "delete"]
    assert len(gone) == 4
    assert any('Removed the lead status "Booth visit" with the Undo of the lead pipeline '
               '"Trade show"' in x.summary for x in gone)


async def test_the_undo_of_a_deal_pipeline_that_a_lead_pipeline_feeds_refuses(pns) -> None:  # noqa: F811
    """The feed of the lead pipeline would change with no row, so the Undo of
    the deal pipeline names the lead pipeline and waits for its Undo."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "AMC contracts", "stages": [{"name": "Site visit"}]})).json()
    lead = await _call(pns, admin, "POST", "/crm/pipelines", {**WEB, "feeds": "AMC contracts"})
    assert lead.status_code == 201, lead.text
    (row,) = [x for x in _rows(pns, pns.org_a)
              if x.area == "pipeline" and x.entity_id == amc["id"]]
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "in_use", undo.text
    assert 'The lead pipeline "Web" feeds deals into the pipeline "AMC contracts"' in _words(undo)
    (web_row,) = [x for x in _rows(pns, pns.org_a)
                  if x.area == "pipeline" and x.entity_id == lead.json()["id"]]
    assert (await _call(pns, admin, "POST",
                        f"/crm/change-history/{web_row.id}/undo")).status_code == 200
    assert (await _call(pns, admin, "POST",
                        f"/crm/change-history/{row.id}/undo")).status_code == 200


async def test_a_mirror_org_off_the_flag_or_a_member_makes_no_lead_pipeline(
    pns, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    _settings(pns, pns.org_a, "native", access="everyone")
    member = await _call(pns, _member(pns.org_a), "POST", "/crm/pipelines", WEB)
    assert member.status_code == 403, member.text
    _settings(pns, pns.org_a, "mirror")
    mirror = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", WEB)
    assert mirror.status_code == 409 and _code(mirror) == "mirror_mode_settings", mirror.text
    _settings(pns, pns.org_a, "native")
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", pns.org_b, raising=False)
    dark = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", WEB)
    assert dark.status_code == 404, dark.text
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_change_log", pns.org_a) == 0


async def test_via_is_chat_for_the_agent_lead_pipeline(pns) -> None:  # noqa: F811
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", OUTBOUND,
                    headers={"X-Actor-Via": STAMP, "Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 201, r.text
    by = {(x.area, x.entity_id): x.via for x in _rows(pns, pns.org_a)}
    assert by[("pipeline", r.json()["id"])] == "chat"
    assert by[("lead_status", r.json()["stages"][0]["id"])] == "chat"


# ── The migration against the database (R6, R8) ─────────────────────────────


def test_old_code_on_the_new_schema_gets_a_deal_pipeline(pns, app_engine) -> None:  # noqa: F811
    """An INSERT that names no kind, as code before CRM-M4a writes it, makes a
    deal pipeline. The CHECKs refuse an unknown kind and a feed on a deal
    pipeline. A second run of the file changes nothing."""
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_a})
        made = c.execute(text(
            "INSERT INTO crm_pipelines (name, position, is_default) VALUES ('Old', 0, true) "
            "RETURNING kind, feeds_pipeline_id")).first()
        assert (made.kind, made.feeds_pipeline_id) == ("deal", None)
    for sql, check in (
        ("INSERT INTO crm_pipelines (name, kind) VALUES ('Bad', 'contact')", "crm_pipelines_kind"),
        ("INSERT INTO crm_pipelines (name, kind, feeds_pipeline_id) SELECT 'Bad', 'deal', id "
         "FROM crm_pipelines LIMIT 1", "crm_pipelines_feeds_lead"),
    ):
        with pytest.raises(Exception, match=check), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_a})
            c.execute(text(sql))
    with pns.admin_engine.begin() as c:
        validated = {r.conname: r.convalidated for r in c.execute(text(
            "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = "
            "'crm_pipelines'::regclass AND conname LIKE 'crm_pipelines_%'"))}
        assert validated == {"crm_pipelines_kind": True, "crm_pipelines_feeds_lead": True}
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = 'crm_lead_pipeline_stages'::regclass")).scalar()
        assert forced is True
    sql = _migration().read_text(encoding="utf-8")
    for _ in range(2):
        with pns.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    assert _one(pns, "SELECT count(*) FROM crm_pipelines WHERE organization_id = "
                     "CAST(:o AS uuid)", o=pns.org_a) == 1
