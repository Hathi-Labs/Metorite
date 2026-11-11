"""R5 + R6 + R7 + R8 fence — lead pipelines on the one pipeline seam (WS-CRM CRM-M4a).

Spec: ``project-docs/specs/crm_conversational.md`` §8 CRM-M4. Migration 386
(``infra/postgres/386_crm_lead_pipelines.sql``, found here by CONTENT, R1).
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


#: A read of ``crm_pipelines``: ``FROM`` or ``JOIN``, with its alias, if any.
_PIPELINES_READ = re.compile(
    r"(?:FROM|JOIN)\s+crm_pipelines\b"
    r"(?:\s+(?:AS\s+)?(?!(?:WHERE|ON|ORDER|JOIN|LEFT|INNER|GROUP|LIMIT)\b)(\w+))?",
    re.IGNORECASE,
)


def _kind_misses(source: str) -> list[int]:
    """The line of each read of ``crm_pipelines`` in *source* that names no
    kind. A ``FROM`` read by id passes, and its caller checks the kind. A
    ``JOIN`` passes only with a kind term, on its alias or bare."""
    misses: list[int] = []
    for match in _PIPELINES_READ.finditer(source):
        # The statement: this line and the few lines after it.
        window = source[match.start():match.start() + 260]
        alias = match.group(1)
        joined = match.group(0).upper().startswith("JOIN")
        named = bool(re.search(r"kind = :kind|kind = 'deal'|kind = 'lead'|DEAL_PIPELINE_SQL",
                               window))
        if alias:
            named = named or bool(re.search(rf"\b{re.escape(alias)}\.kind\s*=", window))
        by_id = not joined and "WHERE id = CAST" in window
        if not (named or by_id):
            misses.append(source.count("\n", 0, match.start()) + 1)
    return misses


def test_the_reader_scan_finds_a_join_with_no_kind() -> None:
    """Repair 1 (P3): the scan reads a ``JOIN`` with an alias too. Mutation:
    a JOIN reader with no kind term is a miss."""
    bare = "SELECT d.id FROM crm_deals d JOIN crm_pipelines p ON p.id = d.pipeline_id"
    assert _kind_misses(bare) == [1]
    assert _kind_misses(bare + " AND p.kind = 'deal'") == []
    assert _kind_misses("SELECT 1 FROM crm_deals d LEFT JOIN crm_pipelines AS pl "
                        "ON pl.id = d.pipeline_id WHERE pl.kind = :kind") == []
    assert _kind_misses("SELECT name FROM crm_pipelines WHERE id = CAST(:p AS uuid)") == []
    assert _kind_misses("SELECT name FROM crm_pipelines ORDER BY position") == [1]


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
        misses += [f"{where}:{line}" for line in _kind_misses(source)]
        by_id += [where] * len(re.findall(
            r"(?:require|load)_row\(\s*db,\s*\"crm_pipelines\"", source))
    assert misses == [], misses
    # The id reads: the one deal check of core, and the two Undos of this seam.
    assert sorted(by_id) == ["routes/crm/core.py", "routes/crm/pipelines.py",
                             "routes/crm/pipelines.py"], by_id


def test_the_undo_of_a_pipeline_takes_the_create_lock_first() -> None:
    """Repair 1 (P3): a copy create takes the create lock, then reads its
    source ``FOR SHARE``. The Undo takes the create lock before it locks any
    stage, so the two never wait on each other in a cycle."""
    import inspect

    from gateway.routes.crm import pipelines

    source = inspect.getsource(pipelines._undo_pipeline)
    assert source.index("lock_pipeline_creates(") < source.index("_lock_new_stages(")
    create = inspect.getsource(pipelines.create_pipeline_in)
    assert create.index("lock_pipeline_creates(") < create.index("plan_pipeline(")


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
            "'crm_pipelines'::regclass AND contype = 'c' "
            "AND conname IN ('crm_pipelines_kind', 'crm_pipelines_feeds_lead')"))}
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


# ── Repair rounds 1 and 2: a status that a pipeline holds ───────────────────

DEAL_A = {"name": "AMC contracts", "stages": [{"name": "Qualify"}, {"name": "Site visit"}]}
DEAL_B = {"name": "Service", "stages": [{"name": "Qualify"}, {"name": "Site visit"}]}
LEAD_B = {"name": "Expo", "kind": "lead", "stages": [{"name": "New"}, {"name": "Booth visit"}]}


def _area(kind: str) -> str:
    return "lead_status" if kind == "lead" else "deal_stage"


def _status_table(kind: str) -> str:
    return "crm_lead_statuses" if kind == "lead" else "crm_deal_statuses"


def _created(p: Any, kind: str, status_id: str) -> Any:
    """The create row of one status in the Change history."""
    (row,) = [x for x in _rows(p, p.org_a) if x.area == _area(kind)
              and x.action == "create" and str(x.entity_id) == status_id]
    return row


def _pipeline_made(p: Any, pipeline_id: str) -> Any:
    """The create row of one pipeline in the Change history."""
    (row,) = [x for x in _rows(p, p.org_a) if x.area == "pipeline"
              and x.action == "create" and str(x.entity_id) == pipeline_id]
    return row


async def _undo(p: Any, admin: Any, row: Any) -> Any:
    return await _call(p, admin, "POST", f"/crm/change-history/{row.id}/undo")


def _exists(p: Any, table: str, row_id: str) -> bool:
    return _one(p, f"SELECT count(*) FROM {table} WHERE id = CAST(:i AS uuid)", i=row_id) == 1


def _held(kind: str, pipeline: str, status: str, *, undo: bool = True) -> str:
    """The whole refusal, with the name of the pipeline now (repair 4)."""
    noun = "lead pipeline" if kind == "lead" else "deal pipeline"
    status_noun = "lead status" if kind == "lead" else "deal stage"
    outcome = "nothing was undone" if undo else "it was not deleted"
    then = "undo this" if undo else f"delete the {status_noun}"
    return (f'The {noun} "{pipeline}" uses the {status_noun} "{status}", so {outcome}. '
            f'Undo the changes to the {noun} "{pipeline}", newest first, and then {then}.')


def _no_orphan(p: Any, kind: str) -> None:
    """No membership row names a pipeline that is gone."""
    stages = "crm_lead_pipeline_stages" if kind == "lead" else "crm_pipeline_stages"
    assert _one(p, f"SELECT count(*) FROM {stages} ps WHERE ps.organization_id = "
                   "CAST(:o AS uuid) AND NOT EXISTS (SELECT 1 FROM crm_pipelines x "
                   "WHERE x.id = ps.pipeline_id)", o=p.org_a) == 0


def test_the_status_undo_and_delete_take_the_create_lock_first() -> None:
    """Repairs 2 and 3. One lock order: the pipeline create lock, then a
    change-log row, then a status or pipeline row. Each Undo takes the lock
    before it locks its own change-log row (``undo_change``). The status Undo
    and the delete helper take it before the status row, and the routes that
    put a stage in a pipeline or take it out take it first. Mutation: move
    any of these locks after the row read -> red."""
    import inspect

    from gateway.routes.crm import admin, change_log, pipelines

    undo_change = inspect.getsource(change_log.undo_change)
    assert undo_change.index("lock_pipeline_creates(") < undo_change.index("FOR UPDATE")
    for write in (pipelines.add_stage_in, pipelines.remove_stage_in):
        source = inspect.getsource(write)
        assert source.index("lock_pipeline_creates(") < source.index("_native_pipeline("), write

    undo = inspect.getsource(admin._undo_status)
    assert undo.index("lock_pipeline_creates(") < undo.index("_current(")
    delete = inspect.getsource(admin.delete_status_in)
    assert delete.index("lock_pipeline_creates(") < delete.index("load_row(")
    assert delete.index("load_row(") < delete.index("pipelines_holding(")
    assert delete.index("pipelines_holding(") < delete.index("DELETE FROM")


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_the_undo_of_a_status_that_a_pipeline_made_refuses_and_names_it(
    pns, kind: str,  # noqa: F811
) -> None:
    """Round 1 P1, now under the round 2 rule. The status Undo would take the
    status out of the pipeline, and then the pipeline Undo answers 409 for
    ever. It refuses, names the pipeline and says to undo that one. The
    pipeline Undo then works, and the status goes with it."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    body = TRADE_SHOW if kind == "lead" else DEAL_A
    made = (await _call(pns, admin, "POST", "/crm/pipelines", body)).json()
    new = made["stages"][1]
    status_row = _created(pns, kind, new["id"])
    undo = await _undo(pns, admin, status_row)
    assert undo.status_code == 409 and _code(undo) == "in_use", undo.text
    words = _words(undo)
    assert words == _held(kind, body["name"], new["name"]), words
    # Repair 3: no step that has no door. Change history has no Undo button,
    # and only the API takes a stage out.
    assert "Change history" not in words and "take the stage out" not in words, words
    assert _exists(pns, _status_table(kind), new["id"])
    assert new["id"] in [s["id"] for s in (await _call(
        pns, admin, "GET", "/crm/pipelines",
        params={"kind": kind})).json()[-1]["stages"]], "the status stays in the pipeline"
    again = await _undo(pns, admin, _pipeline_made(pns, made["id"]))
    assert again.status_code == 200, again.text
    assert not _exists(pns, _status_table(kind), new["id"])
    gone = await _undo(pns, admin, status_row)
    assert gone.status_code == 409, gone.text
    _no_orphan(pns, kind)


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_bypass_a_a_status_that_one_pipeline_made_and_another_reuses(
    pns, kind: str,  # noqa: F811
) -> None:
    """Round 2 P1, bypass A. Pipeline A makes S, and pipeline B reuses S. The
    Undo of A leaves S, because B holds it. The Undo of S then refuses and
    names B, so B never strands. Undo B, then undo S: 200 twice, and no row
    is left over."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    a = (await _call(pns, admin, "POST", "/crm/pipelines",
                     TRADE_SHOW if kind == "lead" else DEAL_A)).json()
    b = await _call(pns, admin, "POST", "/crm/pipelines", LEAD_B if kind == "lead" else DEAL_B)
    assert b.status_code == 201, b.text
    b = b.json()
    s_name = "Booth visit" if kind == "lead" else "Site visit"
    (s,) = [x for x in a["stages"] if x["name"] == s_name]
    assert s["id"] in [x["id"] for x in b["stages"]], "B reuses S"
    assert (await _undo(pns, admin, _pipeline_made(pns, a["id"]))).status_code == 200
    assert _exists(pns, _status_table(kind), s["id"]), "B holds S, so the Undo of A leaves it"
    s_row = _created(pns, kind, s["id"])
    refused = await _undo(pns, admin, s_row)
    assert refused.status_code == 409 and _code(refused) == "in_use", refused.text
    assert _words(refused).startswith(_held(kind, b["name"], s_name)), _words(refused)
    assert s_name in [x["name"] for x in (await _call(
        pns, admin, "GET", "/crm/pipelines", params={"kind": kind})).json()[-1]["stages"]]
    # The full happy path: undo B, then undo S.
    first = await _undo(pns, admin, _pipeline_made(pns, b["id"]))
    assert first.status_code == 200, first.text
    second = await _undo(pns, admin, s_row)
    assert second.status_code == 200, second.text
    assert not _exists(pns, _status_table(kind), s["id"])
    assert not _exists(pns, "crm_pipelines", b["id"])
    _no_orphan(pns, kind)


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_bypass_b_a_status_that_an_admin_added_and_a_pipeline_reuses(
    pns, kind: str,  # noqa: F811
) -> None:
    """Round 2 P1, bypass B. An admin adds S in Settings, and pipeline B reuses
    S. The Undo of S refuses and names B. A delete of S refuses too. Undo B,
    then undo S: 200 twice, and no row is left over."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = await _call(pns, admin, "POST", f"/crm/statuses/{kind}", {"name": "Walk-in"})
    assert added.status_code == 201, added.text
    s_id = added.json()["id"]
    first = "New" if kind == "lead" else "Qualify"
    b = await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "Expo", **({"kind": "lead"} if kind == "lead" else {}),
        "stages": [{"name": first}, {"name": "Walk-in"}]})
    assert b.status_code == 201, b.text
    b = b.json()
    s_row = _created(pns, kind, s_id)
    refused = await _undo(pns, admin, s_row)
    assert refused.status_code == 409 and _code(refused) == "in_use", refused.text
    assert _words(refused).startswith(_held(kind, "Expo", "Walk-in")), _words(refused)
    deleted = await _call(pns, admin, "DELETE", f"/crm/statuses/{kind}/{s_id}")
    assert deleted.status_code == 409 and _code(deleted) == "in_use", deleted.text
    assert _words(deleted).startswith(_held(kind, "Expo", "Walk-in", undo=False))
    assert _exists(pns, _status_table(kind), s_id)
    one = await _undo(pns, admin, _pipeline_made(pns, b["id"]))
    assert one.status_code == 200, one.text
    two = await _undo(pns, admin, s_row)
    assert two.status_code == 200, two.text
    assert not _exists(pns, _status_table(kind), s_id)
    _no_orphan(pns, kind)


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_a_status_that_only_the_default_holds_still_undoes(
    pns, kind: str,  # noqa: F811
) -> None:
    """As on main. A deal stage that an admin added sits in the default deal
    pipeline only, and a lead status in the default lead list (no pipeline
    row). Its Undo answers 200, and so does a delete."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = await _call(pns, admin, "POST", f"/crm/statuses/{kind}", {"name": "Walk-in"})
    assert added.status_code == 201, added.text
    if kind == "deal":
        assert _one(pns, "SELECT count(*) FROM crm_pipeline_stages WHERE pipeline_id = "
                         "CAST(:pl AS uuid) AND status_id = CAST(:s AS uuid)",
                    pl=deal["pipeline"], s=added.json()["id"]) == 1
    undo = await _undo(pns, admin, _created(pns, kind, added.json()["id"]))
    assert undo.status_code == 200, undo.text
    again = await _call(pns, admin, "POST", f"/crm/statuses/{kind}", {"name": "Walk-in"})
    assert again.status_code == 201, again.text
    gone = await _call(pns, admin, "DELETE", f"/crm/statuses/{kind}/{again.json()['id']}")
    assert gone.status_code == 200, gone.text


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_the_refusal_names_the_pipeline_by_its_name_now(
    pns, kind: str,  # noqa: F811
) -> None:
    """Round 2 P3. After a rename, the refusal names the new name, not the
    name in the log row. A deal pipeline is renamed through its route. A lead
    pipeline has no rename route yet, so the test renames it in SQL."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines",
                        TRADE_SHOW if kind == "lead" else DEAL_A)).json()
    if kind == "deal":
        renamed = await _call(pns, admin, "PATCH", f"/crm/pipelines/{made['id']}",
                              {"name": "Renamed one"})
        assert renamed.status_code == 200, renamed.text
    else:
        _one(pns, "UPDATE crm_pipelines SET name = 'Renamed one' WHERE id = CAST(:pl AS uuid) "
                  "RETURNING id", pl=made["id"])
    new = made["stages"][1]
    refused = await _undo(pns, admin, _created(pns, kind, new["id"]))
    assert refused.status_code == 409, refused.text
    assert _words(refused).startswith(_held(kind, "Renamed one", new["name"])), _words(refused)


async def test_the_refusal_names_each_pipeline_that_holds_the_status(pns) -> None:  # noqa: F811
    """Two lead pipelines hold one status: the words name both, in order."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = (await _call(pns, admin, "POST", "/crm/statuses/lead", {"name": "Walk-in"})).json()
    for name in ("Expo", "Fair"):
        made = await _call(pns, admin, "POST", "/crm/pipelines", {
            "name": name, "kind": "lead", "stages": [{"name": "New"}, {"name": "Walk-in"}]})
        assert made.status_code == 201, made.text
    refused = await _undo(pns, admin, _created(pns, "lead", added["id"]))
    assert refused.status_code == 409, refused.text
    assert _words(refused) == (
        'The lead pipelines "Expo" and "Fair" use the lead status "Walk-in", so nothing '
        'was undone. Undo the changes to the lead pipelines "Expo" and "Fair", newest '
        "first, and then undo this.")


async def test_a_pipeline_that_is_the_default_holds_the_stages_it_made(pns) -> None:  # noqa: F811
    """An org with no other deal pipeline: the first pipeline that a create
    makes is the default. It still holds the stage that it made, so the Undo
    of that stage refuses. A stage that an admin adds then joins it as the
    default, and its Undo answers 200. Then the pipeline undoes."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    _one(pns, "DELETE FROM crm_pipelines WHERE id = CAST(:pl AS uuid) RETURNING id",
         pl=deal["pipeline"])
    admin = _admin(pns.org_a)
    only = (await _call(pns, admin, "POST", "/crm/pipelines", DEAL_A)).json()
    site = only["stages"][1]
    refused = await _undo(pns, admin, _created(pns, "deal", site["id"]))
    assert refused.status_code == 409, refused.text
    assert _words(refused).startswith(_held("deal", "AMC contracts", "Site visit"))
    added = await _call(pns, admin, "POST", "/crm/statuses/deal", {"name": "Walk-in"})
    assert added.status_code == 201, added.text
    assert "Walk-in" in [s["name"] for s in (await _call(
        pns, admin, "GET", "/crm/pipelines")).json()[0]["stages"]], "it joins the default"
    walk = await _undo(pns, admin, _created(pns, "deal", added.json()["id"]))
    assert walk.status_code == 200, walk.text
    whole = await _undo(pns, admin, _pipeline_made(pns, only["id"]))
    assert whole.status_code == 200, whole.text
    _no_orphan(pns, "deal")


async def test_an_undo_of_a_status_waits_for_a_create_that_reuses_it(pns) -> None:  # noqa: F811
    """The race of round 2. A create holds the create lock and puts S in a new
    lead pipeline, and has not committed. The Undo of S waits for the lock.
    After the commit it sees the pipeline, and refuses with its name."""
    import asyncio

    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = (await _call(pns, admin, "POST", "/crm/statuses/lead", {"name": "Walk-in"})).json()
    s_row = _created(pns, "lead", added["id"])
    with pns.admin_engine.connect() as c:
        tx = c.begin()
        c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                  {"k": f"crm_pipeline_create:{pns.org_a}"})
        pipe = c.execute(text(
            "INSERT INTO crm_pipelines (organization_id, name, position, is_default, kind) "
            "VALUES (CAST(:o AS uuid), 'Late fair', 9, false, 'lead') RETURNING id"),
            {"o": pns.org_a}).scalar()
        c.execute(text(
            "INSERT INTO crm_lead_pipeline_stages (organization_id, pipeline_id, status_id, "
            "position) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), 0)"),
            {"o": pns.org_a, "p": str(pipe), "s": added["id"]})
        # Repair 4: a pipeline holds S through an open change, so the create
        # writes its row in the same transaction, as log_change does.
        c.execute(text(
            "INSERT INTO crm_change_log (organization_id, actor_email, via, ladder_level, "
            "area, action, entity_id, summary, after, mode, reversible) VALUES ("
            "CAST(:o AS uuid), 'race@example.test', 'ui', 'company', 'pipeline', 'create', "
            ":p, 'Created the lead pipeline \"Late fair\"', CAST(:after AS jsonb), "
            "'native', true)"),
            {"o": pns.org_a, "p": str(pipe), "after": json.dumps({
                "name": "Late fair", "kind": "lead",
                "stages": [{"id": added["id"], "name": "Walk-in", "type": "open",
                            "source": "existing"}]})})
        undo = asyncio.ensure_future(_undo(pns, admin, s_row))
        await asyncio.sleep(1.5)
        assert not undo.done(), "the Undo waits for the create lock"
        tx.commit()
    refused = await undo
    assert refused.status_code == 409 and _code(refused) == "in_use", refused.text
    assert _words(refused) == _held("lead", "Late fair", "Walk-in"), _words(refused)
    assert _exists(pns, "crm_lead_statuses", added["id"])


# ── Repair round 3: the take-out, the case of an id, the lock order ─────────


def _take_out_row(p: Any, pipeline_id: str) -> Any:
    """The newest open take-out of a stage from one pipeline."""
    rows = [x for x in _rows(p, p.org_a) if x.area == "pipeline_stage" and x.action == "delete"
            and str(x.entity_id) == pipeline_id and x.undone_by is None]
    return rows[-1]


async def test_a_take_out_does_not_free_a_stage_that_the_create_made(pns) -> None:  # noqa: F811
    """Repair 3 (P1), the steps of the re-verifier. P makes S. The Undo of S
    refuses. A take-out of S from P answers 200, and the Undo of S still
    refuses and names P, and so does a delete of S. Then the legal order: the
    Undo of the take-out, then the Undo of P, 200 each. S goes with P."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", DEAL_A)).json()
    (s,) = [x for x in made["stages"] if x["name"] == "Site visit"]
    s_row = _created(pns, "deal", s["id"])
    first = await _undo(pns, admin, s_row)
    assert first.status_code == 409 and _words(first) == _held("deal", "AMC contracts", "Site visit")
    out = await _call(pns, admin, "DELETE", f"/crm/pipelines/{made['id']}/stages/{s['id']}")
    assert out.status_code == 200, out.text
    again = await _undo(pns, admin, s_row)
    assert again.status_code == 409 and _code(again) == "in_use", again.text
    assert _words(again) == _held("deal", "AMC contracts", "Site visit"), _words(again)
    gone = await _call(pns, admin, "DELETE", f"/crm/statuses/deal/{s['id']}")
    assert gone.status_code == 409, gone.text
    assert _words(gone) == _held("deal", "AMC contracts", "Site visit", undo=False)
    assert _exists(pns, "crm_deal_statuses", s["id"])
    back = await _undo(pns, admin, _take_out_row(pns, made["id"]))
    assert back.status_code == 200, back.text
    whole = await _undo(pns, admin, _pipeline_made(pns, made["id"]))
    assert whole.status_code == 200, whole.text
    assert not _exists(pns, "crm_pipelines", made["id"])
    assert not _exists(pns, "crm_deal_statuses", s["id"]), "S went with the Undo of P"
    late = await _undo(pns, admin, s_row)
    assert late.status_code == 409 and _code(late) == "already_undone", late.text
    _no_orphan(pns, "deal")


async def test_a_take_out_does_not_free_a_reused_stage(pns) -> None:  # noqa: F811
    """Repair 3 (P1), bypass B with a take-out. An admin adds S, and P reuses
    S. The take-out of S from P leaves the Undo of S refused. The legal order:
    undo the take-out, then P, then S, 200 each time."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = (await _call(pns, admin, "POST", "/crm/statuses/deal", {"name": "Walk-in"})).json()
    p = (await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "Expo", "stages": [{"name": "Qualify"}, {"name": "Walk-in"}]})).json()
    out = await _call(pns, admin, "DELETE", f"/crm/pipelines/{p['id']}/stages/{added['id']}")
    assert out.status_code == 200, out.text
    s_row = _created(pns, "deal", added["id"])
    refused = await _undo(pns, admin, s_row)
    assert refused.status_code == 409 and _words(refused) == _held("deal", "Expo", "Walk-in")
    for row in (_take_out_row(pns, p["id"]), _pipeline_made(pns, p["id"]), s_row):
        done = await _undo(pns, admin, row)
        assert done.status_code == 200, (row.summary, done.text)
    assert not _exists(pns, "crm_deal_statuses", added["id"])
    _no_orphan(pns, "deal")


async def test_a_lead_pipeline_has_no_take_out_route(pns) -> None:  # noqa: F811
    """The take-out route acts on a deal pipeline only, so a lead pipeline
    answers 404, and the Undo of its status still refuses."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", TRADE_SHOW)).json()
    s = made["stages"][1]
    out = await _call(pns, admin, "DELETE", f"/crm/pipelines/{made['id']}/stages/{s['id']}")
    assert out.status_code == 404, out.text
    refused = await _undo(pns, admin, _created(pns, "lead", s["id"]))
    assert _words(refused) == _held("lead", "Trade show", "Booth visit"), _words(refused)


async def test_the_undo_of_a_pipeline_leaves_a_stage_that_another_create_lists(
    pns,  # noqa: F811
) -> None:
    """A makes S, B reuses S, and S is taken out of B. The Undo of A answers
    200 and leaves S, because the open create of B lists it. S keeps its open
    create row. Then undo the take-out, B and S: 200 each."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    a = (await _call(pns, admin, "POST", "/crm/pipelines", DEAL_A)).json()
    b = (await _call(pns, admin, "POST", "/crm/pipelines", DEAL_B)).json()
    (s,) = [x for x in a["stages"] if x["name"] == "Site visit"]
    out = await _call(pns, admin, "DELETE", f"/crm/pipelines/{b['id']}/stages/{s['id']}")
    assert out.status_code == 200, out.text
    first = await _undo(pns, admin, _pipeline_made(pns, a["id"]))
    assert first.status_code == 200, first.text
    assert _exists(pns, "crm_deal_statuses", s["id"])
    s_row = _created(pns, "deal", s["id"])
    assert s_row.undone_by is None
    refused = await _undo(pns, admin, s_row)
    assert _words(refused) == _held("deal", "Service", "Site visit"), _words(refused)
    for row in (_take_out_row(pns, b["id"]), _pipeline_made(pns, b["id"]), s_row):
        done = await _undo(pns, admin, row)
        assert done.status_code == 200, (row.summary, done.text)
    _no_orphan(pns, "deal")


@pytest.mark.parametrize("kind", ["deal", "lead"])
async def test_a_pipeline_with_no_open_change_holds_nothing(
    pns, kind: str,  # noqa: F811
) -> None:
    """Repair 4 (P2), as on main. A pipeline with no change row (a Zoho
    pipeline that stays after the switch) holds a stage. The Settings delete
    of that stage answers 200, and so does the Undo of a status create."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    table = _status_table(kind)
    zoho = str(_one(
        pns, f"INSERT INTO {table} (organization_id, name, type, position) "
             "VALUES (CAST(:o AS uuid), 'Zoho only', 'open', 90) RETURNING id", o=pns.org_a))
    added = (await _call(pns, admin, "POST", f"/crm/statuses/{kind}", {"name": "Walk-in"})).json()
    imported = str(_one(
        pns, "INSERT INTO crm_pipelines (organization_id, name, position, is_default, kind) "
             "VALUES (CAST(:o AS uuid), 'Imported', 7, false, :k) RETURNING id",
        o=pns.org_a, k=kind))
    stages = "crm_lead_pipeline_stages" if kind == "lead" else "crm_pipeline_stages"
    for place, sid in enumerate((zoho, added["id"])):
        _one(pns, f"INSERT INTO {stages} (organization_id, pipeline_id, status_id, position) "
                  "VALUES (CAST(:o AS uuid), CAST(:pl AS uuid), CAST(:s AS uuid), :pos) "
                  "RETURNING position", o=pns.org_a, pl=imported, s=sid, pos=place)
    gone = await _call(pns, admin, "DELETE", f"/crm/statuses/{kind}/{zoho}")
    assert gone.status_code == 200, gone.text
    undone = await _undo(pns, admin, _created(pns, kind, added["id"]))
    assert undone.status_code == 200, undone.text
    assert not _exists(pns, table, zoho) and not _exists(pns, table, added["id"])
    assert _exists(pns, "crm_pipelines", imported)


async def test_a_stage_add_to_a_pipeline_with_no_create_names_its_undo(pns) -> None:  # noqa: F811
    """Repair 4. An admin adds S, then puts S in a pipeline that has no
    create row through the stage route, a logged change. The Undo of S and
    the delete of S refuse, and name the step that works: the Undo of the
    changes to that pipeline. Then the Undo of the add and the Undo of S
    answer 200."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    added = (await _call(pns, admin, "POST", "/crm/statuses/deal", {"name": "Walk-in"})).json()
    imported = str(_one(
        pns, "INSERT INTO crm_pipelines (organization_id, name, position, is_default) "
             "VALUES (CAST(:o AS uuid), 'Imported', 7, false) RETURNING id", o=pns.org_a))
    put = await _call(pns, admin, "POST", f"/crm/pipelines/{imported}/stages",
                      {"status_id": added["id"]})
    assert put.status_code == 201, put.text
    s_row = _created(pns, "deal", added["id"])
    refused = await _undo(pns, admin, s_row)
    assert refused.status_code == 409 and _code(refused) == "in_use", refused.text
    assert _words(refused) == _held("deal", "Imported", "Walk-in"), _words(refused)
    gone = await _call(pns, admin, "DELETE", f"/crm/statuses/deal/{added['id']}")
    assert _words(gone) == _held("deal", "Imported", "Walk-in", undo=False), _words(gone)
    (add_row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline_stage"
                  and x.action == "create" and str(x.entity_id) == imported]
    for row in (add_row, s_row):
        done = await _undo(pns, admin, row)
        assert done.status_code == 200, (row.summary, done.text)
    assert not _exists(pns, "crm_deal_statuses", added["id"])


async def test_an_upper_case_id_reads_as_the_same_status(pns) -> None:  # noqa: F811
    """Repair 3 (P2). The default deal pipeline came from a create, so its
    open create holds the stage that it made. A delete with the id in upper
    case refuses as the lower case one does, and so does an Undo whose
    change id is in upper case. Before, the upper case id missed the JSONB
    match and stranded the Undo of the pipeline."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    _one(pns, "DELETE FROM crm_pipelines WHERE id = CAST(:pl AS uuid) RETURNING id",
         pl=deal["pipeline"])
    admin = _admin(pns.org_a)
    only = (await _call(pns, admin, "POST", "/crm/pipelines", DEAL_A)).json()
    site = only["stages"][1]["id"]
    for sid in (site, site.upper()):
        r = await _call(pns, admin, "DELETE", f"/crm/statuses/deal/{sid}")
        assert r.status_code == 409 and _code(r) == "in_use", (sid, r.text)
        assert _words(r) == _held("deal", "AMC contracts", "Site visit", undo=False)
    row = _created(pns, "deal", site)
    upper = await _call(pns, admin, "POST", f"/crm/change-history/{str(row.id).upper()}/undo")
    assert upper.status_code == 409 and _words(upper) == _held("deal", "AMC contracts", "Site visit")
    assert _exists(pns, "crm_deal_statuses", site)
    whole = await _undo(pns, admin, _pipeline_made(pns, only["id"]))
    assert whole.status_code == 200, whole.text


async def test_a_status_undo_and_a_pipeline_undo_at_once_never_deadlock(pns) -> None:  # noqa: F811
    """Repair 3 (P3), the race of the re-verifier. A held create lock, then
    the Undo of S (a new status of P) and the Undo of P together, four times.
    Each Undo takes the create lock before its change-log row, so neither
    answers 409 ``undo_conflict``. The Undo of P always answers 200."""
    import asyncio

    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    for turn in range(4):
        made = (await _call(pns, admin, "POST", "/crm/pipelines", {
            "name": f"Race {turn}", "kind": "lead",
            "stages": [{"name": "New"}, {"name": f"Race S{turn}"}]})).json()
        s_row = _created(pns, "lead", made["stages"][1]["id"])
        p_row = _pipeline_made(pns, made["id"])
        with pns.admin_engine.connect() as c:
            tx = c.begin()
            c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                      {"k": f"crm_pipeline_create:{pns.org_a}"})
            first = asyncio.ensure_future(_undo(pns, admin, s_row))
            second = asyncio.ensure_future(_undo(pns, admin, p_row))
            await asyncio.sleep(1.5)
            tx.commit()
        status, pipeline = await first, await second
        assert pipeline.status_code == 200, (turn, pipeline.text)
        assert status.status_code == 409, (turn, status.text)
        assert _code(status) in ("in_use", "already_undone"), (turn, status.text)


# The orders of repair 3. Each order acts on one status S and one pipeline P,
# then drains: it undoes the open changes of the order, newest first, until
# no Undo moves. After the drain P is gone, so no Undo of P ended in a 409 for
# ever. "made": P makes S. "reused": an admin adds S, and P reuses it.
_DEAL_ORDERS: tuple[tuple[str, ...], ...] = (
    ("take_out", "undo_s", "undo_p"),
    ("take_out", "delete_s", "undo_p"),
    ("take_out", "undo_p", "undo_s"),
    ("undo_s", "take_out", "undo_take_out", "undo_p"),
    ("rename", "undo_s", "undo_p"),
    ("rename", "take_out", "undo_s", "undo_rename", "undo_p"),
    ("hide", "undo_s", "undo_p"),
    ("hide", "take_out", "delete_s", "undo_hide"),
    ("undo_p", "undo_s", "delete_s"),
    ("take_out", "rename", "hide", "undo_s", "delete_s", "undo_p"),
    ("undo_take_out", "undo_s"),
    ("delete_s", "take_out", "undo_take_out", "undo_s"),
)
_LEAD_ORDERS: tuple[tuple[str, ...], ...] = (
    ("undo_s", "undo_p"),
    ("delete_s", "undo_p"),
    ("hide", "undo_s", "delete_s", "undo_hide", "undo_p"),
    ("undo_p", "undo_s"),
)


async def _drain(p: Any, admin: Any, seen: set[str], tag: str) -> None:
    """Undo the open changes that are not in *seen*, newest first, until no
    Undo answers 200."""
    for _ in range(20):
        rows = [x for x in reversed(_rows(p, p.org_a)) if str(x.id) not in seen
                and x.undone_by is None and x.undoes_id is None and x.reversible]
        moved = False
        for row in rows:
            r = await _undo(p, admin, row)
            assert r.status_code in (200, 409), (tag, row.summary, r.text)
            if r.status_code == 200:
                moved = True
                break
        if not moved:
            return


async def _run_order(p: Any, admin: Any, kind: str, how: str, ops: tuple[str, ...],
                     tag: str) -> None:
    """One order, then the drain. Each answer is 2xx, 404, 409 or 422, and
    never 409 ``undo_conflict``."""
    seen = {str(x.id) for x in _rows(p, p.org_a)}
    s_name, p_name = f"S {tag}", f"P {tag}"
    first = "New" if kind == "lead" else "Qualify"
    body: dict[str, Any] = {"name": p_name, "stages": [{"name": first}, {"name": s_name}]}
    if kind == "lead":
        body["kind"] = "lead"
    if how == "reused":
        added = await _call(p, admin, "POST", f"/crm/statuses/{kind}", {"name": s_name})
        assert added.status_code == 201, added.text
    made = await _call(p, admin, "POST", "/crm/pipelines", body)
    assert made.status_code == 201, made.text
    pid = made.json()["id"]
    (sid,) = [x["id"] for x in made.json()["stages"] if x["name"] == s_name]

    def last(area: str, action: str) -> Any:
        rows = [x for x in _rows(p, p.org_a) if x.area == area and x.action == action
                and str(x.entity_id) in (pid, sid) and x.undone_by is None
                and x.undoes_id is None]
        return rows[-1] if rows else None

    #: The Undo of each kind of op: the area and the action of its row.
    undoes = {"undo_s": (_area(kind), "create"), "undo_p": ("pipeline", "create"),
              "undo_take_out": ("pipeline_stage", "delete"),
              "undo_rename": ("pipeline", "change"), "undo_hide": (_area(kind), "change")}
    writes = {"delete_s": ("DELETE", f"/crm/statuses/{kind}/{sid}", None),
              "take_out": ("DELETE", f"/crm/pipelines/{pid}/stages/{sid}", None),
              "rename": ("PATCH", f"/crm/pipelines/{pid}", {"name": f"{p_name} 2"}),
              "hide": ("PATCH", f"/crm/statuses/{kind}/{sid}", {"hidden": True})}
    for op in ops:
        if op in writes:
            method, path, payload = writes[op]
            r = await _call(p, admin, method, path, payload)
        else:
            row = last(*undoes[op])
            if row is None:
                continue  # Nothing of that kind is open, for example S went with P.
            r = await _undo(p, admin, row)
        assert r.status_code in (200, 201, 404, 409, 422), (tag, op, r.text)
        assert r.status_code != 409 or _code(r) != "undo_conflict", (tag, op, r.text)
    await _drain(p, admin, seen, tag)
    assert not _exists(p, "crm_pipelines", pid), (tag, "the Undo of P is stuck")
    assert not _exists(p, _status_table(kind), sid), (tag, "S stays")
    _no_orphan(p, kind)


#: Each case: the kind, how S came, and the order. One case for each, so a
#: slow box never meets the timeout of one test.
_ORDER_CASES = [
    (kind, how, ops)
    for kind, orders in (("deal", _DEAL_ORDERS), ("lead", _LEAD_ORDERS))
    for how in ("made", "reused")
    for ops in orders
]


@pytest.mark.parametrize(("kind", "how", "ops"), _ORDER_CASES,
                         ids=[f"{k}-{h}-{'-'.join(o)}" for k, h, o in _ORDER_CASES])
async def test_no_order_leaves_the_undo_of_a_pipeline_stuck(
    pns, kind: str, how: str, ops: tuple[str, ...],  # noqa: F811
) -> None:
    """Repair 3 (P1). Each order of status create, status Undo, status
    delete, pipeline create, pipeline Undo, take-out, Undo of the take-out,
    rename and hide ends with a drain that removes the pipeline. The lead
    orders leave out the take-out and the rename, which have no lead route."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    await _run_order(pns, _admin(pns.org_a), kind, how, ops, how)


async def test_a_feed_renamed_after_the_card_refuses_with_plan_changed(pns) -> None:  # noqa: F811
    """P3. The card pins the feed by id. A rename between the card and Apply
    makes the name find another deal pipeline, or none, and the create
    refuses with 409 ``plan_changed``."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    body = {**WEB, "feeds": "Sales pipeline"}
    plan = (await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "Web", "kind": "lead", "feeds": "Sales pipeline",
        "stages": json.dumps(WEB["stages"])})).json()
    assert plan["feeds_id"] == deal["pipeline"]
    assert plan["default_deal_pipeline"] == "Sales pipeline"
    planned = {"planned": [s["id"] for s in plan["stages"]], "planned_feeds": plan["feeds_id"]}
    # Another deal pipeline takes the name, and the old one is renamed.
    other = await _call(pns, admin, "POST", "/crm/pipelines", {
        "name": "Partner deals", "stages": [{"name": "Qualify"}]})
    _one(pns, "UPDATE crm_pipelines SET name = 'Old sales' WHERE id = CAST(:pl AS uuid) "
              "RETURNING id", pl=deal["pipeline"])
    _one(pns, "UPDATE crm_pipelines SET name = 'Sales pipeline' WHERE id = CAST(:pl AS uuid) "
              "RETURNING id", pl=other.json()["id"])
    stale = await _call(pns, admin, "POST", "/crm/pipelines", {**body, **planned})
    assert stale.status_code == 409 and _code(stale) == "plan_changed", stale.text
    # Repair 2 (P3): the words say that the feed changed, not the statuses.
    assert stale.json()["detail"]["changed"] == "feeds"
    assert _words(stale).startswith(
        "The deal pipeline that this lead pipeline feeds changed after the card showed it"
    ), _words(stale)
    assert _lead_pipelines(pns, pns.org_a) == []
    # A stage that changed says so, in the nouns of the kind.
    statuses = {**planned, "planned": [None, *planned["planned"][1:]]}
    moved = await _call(pns, admin, "POST", "/crm/pipelines",
                        {**body, **statuses, "planned_feeds": other.json()["id"]})
    assert moved.status_code == 409 and moved.json()["detail"]["changed"] == "stages", moved.text
    assert _words(moved).startswith("The statuses changed after the card showed them")
    fresh = await _call(pns, admin, "POST", "/crm/pipelines",
                        {**body, **planned, "planned_feeds": other.json()["id"]})
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["feeds_pipeline_id"] == other.json()["id"]


async def test_the_kind_of_a_pipeline_cannot_change(pns, app_engine) -> None:  # noqa: F811
    """P3. A fed deal pipeline never becomes a lead one, and a lead pipeline
    never keeps its statuses as a deal one, for each role."""
    _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    web = (await _call(pns, admin, "POST", "/crm/pipelines", {**WEB, "feeds": "Sales pipeline"})).json()
    for target, kind in ((web["feeds_pipeline_id"], "lead"), (web["id"], "deal")):
        with pytest.raises(Exception, match="the kind of a pipeline cannot change"), \
                app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_a})
            c.execute(text("UPDATE crm_pipelines SET kind = :k WHERE id = CAST(:p AS uuid)"),
                      {"k": kind, "p": target})
        with pytest.raises(Exception, match="the kind of a pipeline cannot change"), \
                pns.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_pipelines SET kind = :k WHERE id = CAST(:p AS uuid)"),
                      {"k": kind, "p": target})
    # A rename leaves the kind alone, and passes.
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_a})
        c.execute(text("UPDATE crm_pipelines SET name = 'Web 2' WHERE id = CAST(:p AS uuid)"),
                  {"p": web["id"]})


async def test_a_merge_of_two_lead_statuses_keeps_the_lead_pipeline_place(pns) -> None:  # noqa: F811
    """P3. The Mirror merge folds a lead status into another. Each lead
    pipeline keeps the kept status at the place of the folded one, and the
    cascade takes none."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes.crm import core

    from tests.unit._tenant_ladder import tenant_engine_scope

    _seed(pns, pns.org_a)
    statuses = _leads(pns, pns.org_a)
    trade = (await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", TRADE_SHOW)).json()
    booth = trade["stages"][1]["id"]
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(pns.app_url.render_as_string(hide_password=False)):
            bind_tenant(pns.org_a)
            async with core._tenant_session() as db:
                await core.merge_status_from_source(
                    db, "crm_lead_statuses", statuses["contacted"], booth, "Contacted")
    finally:
        release_tenant(scope)
    assert _members(pns, trade["id"]) == ["New", "Contacted", "Demo booked", "Converted", "Junk"]


async def test_a_deals_view_refuses_a_lead_pipeline(pns) -> None:  # noqa: F811
    """P3. A deals view that names a lead pipeline would open a board that
    answers 404. The save refuses it, on a create and on a change."""
    deal = _seed(pns, pns.org_a)
    _leads(pns, pns.org_a)
    admin = _admin(pns.org_a)
    trade = (await _call(pns, admin, "POST", "/crm/pipelines", TRADE_SHOW)).json()
    bad = await _call(pns, admin, "POST", "/crm/views", {
        "entity": "deals", "name": "Trade board", "layout": "board", "pipeline_id": trade["id"]})
    assert bad.status_code == 404, bad.text
    good = await _call(pns, admin, "POST", "/crm/views", {
        "entity": "deals", "name": "Sales board", "layout": "board",
        "pipeline_id": deal["pipeline"]})
    assert good.status_code == 201, good.text
    changed = await _call(pns, admin, "PATCH", f"/crm/views/{good.json()['id']}",
                          {"pipeline_id": trade["id"]})
    assert changed.status_code == 404, changed.text
