"""WS-KG KG-4 — the writers of ``kb_links``, with no database.

Spec: ``project-docs/specs/knowledge_graph.md`` §3.2, §4.1, §4.3, §4.4 and
the KG-4 row of §9. Code: ``gateway/kb_entities/graph.py``. The R8 suite is
``tests/unit/test_kg_links_r8.py``.

Fences (R7):

* ``kg-one-writer-each-form``: one module writes the page rows
  (``routes/knowledge/backlinks.py``) and one writes the graph rows
  (``kb_entities/graph.py``), each with one INSERT.
* ``kg-no-graph-row-with-page``: the graph INSERT writes ``page_id`` NULL,
  and the removal touches a row with ``page_id`` NULL only.
* ``kg-evidence-end``: each relation names the end that holds its link.
* ``kg-closed-relations``: the writer refuses a relation outside
  ``RELATIONS``, and ``RELATIONS`` equals the rows of §4.1 that are built.
* ``kg-switch``: ``GRAPH_SWEEP_ORGS`` is off by default, and the seed hook
  opens no session for an org off the switch.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.kb_entities import graph

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "project-docs/specs/knowledge_graph.md"
_GATEWAY = _ROOT / "apps/services/gateway/gateway"
_INSERT = re.compile(r"\bINSERT\s+INTO\s+kb_links\b", re.IGNORECASE)

#: The relations of §4.1 that a writer of today writes. KG-5 adds
#: `participant` and `domain_company`, and KG-10 adds `mentions` and
#: `relates_to`. Each slice adds its row here and in `RELATIONS`.
PINNED = ("links_to", "deal_contact", "deal_company", "contact_company",
          "activity_on", "origin", "task_link", "attendee")


def _migration() -> str:
    for path in sorted((_ROOT / "infra/postgres").glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        if "CREATE UNIQUE INDEX IF NOT EXISTS kb_links_graph_key" in body:
            return body
    raise AssertionError("no migration makes kb_links_graph_key")


def _strings(source: str) -> list[str]:
    """Each string literal of *source*, with the parts of an implicit join
    joined, as the parser sees them."""
    return [n.value for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _spec_rows() -> dict[str, str]:
    """The relation and the slice of each row of the table of §4.1."""
    body = _SPEC.read_text(encoding="utf-8")
    start = body.index("### 4.1 The relations")
    end = body.index("### 4.2", start)
    rows: dict[str, str] = {}
    for line in body[start:end].splitlines():
        m = re.match(r"\|\s*`([a-z_]+)`\s*\|[^|]*\|[^|]*\|([^|]*)\|", line)
        if m:
            rows[m.group(1)] = m.group(2).strip()
    return rows


# ── The closed list ─────────────────────────────────────────────────────────


def test_relations_equal_the_pinned_list() -> None:
    assert tuple(graph.RELATIONS) == PINNED


def test_relations_equal_the_built_and_kg4_rows_of_the_spec() -> None:
    rows = _spec_rows()
    assert len(rows) >= 12, "the table of §4.1 did not parse"
    built = {name for name, slice_ in rows.items()
             if slice_.startswith("Built") or "KG-4" in slice_}
    assert built == set(graph.RELATIONS)
    assert "participant" not in graph.RELATIONS  # KG-5 (§9, the KG-4 row)


def test_every_relation_is_extracted_until_a_later_slice() -> None:
    for rel in graph.RELATIONS.values():
        assert rel.provenance in graph.PROVENANCES
        assert rel.provenance == "EXTRACTED"


def test_every_relation_names_its_evidence_end() -> None:
    for rel in graph.RELATIONS.values():
        assert rel.evidence in ("source", "app"), rel.name
        assert set(rel.sources) <= set(graph.NODE_KINDS), rel.name
        assert set(rel.targets) <= set(graph.NODE_KINDS), rel.name
        if rel.evidence == "app":
            kinds = graph.APP_KINDS[rel.app or ""]
            assert set(rel.sources) | set(rel.targets) <= kinds, rel.name
        else:
            assert rel.app is None, rel.name


# ── The writer refuses what the list does not hold ─────────────────────────


def test_each_sweep_form_is_a_kg4_relation_and_passes_the_check() -> None:
    seen = set()
    for form in graph.SWEEP_FORMS:
        assert graph.check_form(form).name == form.relation
        seen.add(form.relation)
    assert seen == set(PINNED) - {"links_to"}


@pytest.mark.parametrize("form", [
    graph.EdgeForm("participant", "email", "person", "SELECT 1 AS s, 1 AS t"),
    graph.EdgeForm("made_up", "task", "task", "SELECT 1 AS s, 1 AS t"),
    graph.EdgeForm("links_to", "page", "page", "SELECT 1 AS s, 1 AS t"),
    graph.EdgeForm("deal_company", "deal", "person", "SELECT 1 AS s, 1 AS t"),
    graph.EdgeForm("attendee", "task", "person", "SELECT 1 AS s, 1 AS t"),
])
async def test_the_writer_refuses_a_form_outside_the_list(form: Any) -> None:
    class _Db:
        calls = 0

        async def execute(self, *a: Any, **k: Any) -> Any:
            self.calls += 1
            raise AssertionError("no statement may run")

    db = _Db()
    with pytest.raises(ValueError):
        await graph._write_edges(db, "11111111-1111-4111-8111-111111111111", form)
    assert db.calls == 0


def test_each_form_names_the_org_in_its_own_where() -> None:
    for form in graph.SWEEP_FORMS:
        assert "organization_id = CAST(:org AS uuid)" in form.select, form
        assert re.search(r"\bAS s\b", form.select) and re.search(r"\bAS t\b", form.select)


def test_the_attendee_form_checks_each_id_against_people() -> None:
    (form,) = [f for f in graph.SWEEP_FORMS if f.relation == "attendee"]
    assert "JOIN people p ON p.id = a.pid" in form.select
    assert "p.organization_id = m.organization_id" in form.select


# ── One writer for each form, and no graph row with a page ─────────────────


def _inserts() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for root in (_ROOT / "apps", _ROOT / "packages", _ROOT / "scripts"):
        for path in sorted(root.rglob("*.py")):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            src = path.read_text(encoding="utf-8-sig")
            if "kb_links" not in src:
                continue
            hits = [s for s in _strings(src) if _INSERT.search(s)]
            if hits:
                found[path.relative_to(_ROOT).as_posix()] = hits
    return found


def test_one_writer_for_each_form() -> None:
    found = _inserts()
    page = "apps/services/gateway/gateway/routes/knowledge/backlinks.py"
    edge = "apps/services/gateway/gateway/kb_entities/graph.py"
    assert set(found) == {page, edge}, found
    assert len(found[page]) == 1 and len(found[edge]) == 1
    assert "source_kind" not in found[page][0]
    assert "page_id" in found[page][0]


def test_the_graph_insert_writes_page_id_null_on_the_graph_key() -> None:
    sql = graph._INSERT_EDGES_SQL
    cols = re.search(r"INSERT INTO kb_links \(([^)]*)\)", sql).group(1)
    values = re.search(r"SELECT DISTINCT (.*?) FROM \(", sql).group(1)
    assert [c.strip() for c in cols.split(",")][1] == "page_id"
    assert values.split(", ")[1] == "CAST(NULL AS uuid)"
    assert "WHERE page_id IS NULL DO NOTHING" in sql


def test_the_removal_touches_a_graph_row_only() -> None:
    assert "page_id IS NULL" in graph._REMOVE_SQL
    assert "organization_id = CAST(:org AS uuid)" in graph._REMOVE_SQL
    assert "DELETE" not in graph._REMOVE_SQL.upper()


def test_the_page_link_reads_still_key_on_page_id() -> None:
    """A graph row has page_id NULL, so the three page reads never meet it."""
    backlinks = (_GATEWAY / "routes/knowledge/backlinks.py").read_text(encoding="utf-8")
    core = (_GATEWAY / "routes/knowledge/core.py").read_text(encoding="utf-8")
    expiry = (_GATEWAY / "routes/knowledge/proposal_expiry.py").read_text(encoding="utf-8")
    assert "ON p.id = l.page_id" in backlinks
    assert "AND page_id = CAST(:pid AS uuid) ORDER BY created_at" in core
    assert "AND page_id = CAST(:pid AS uuid) AND target_kind = 'page'" in expiry


# ── The migration mirrors the code ──────────────────────────────────────────


def _check_values(body: str, name: str) -> set[str]:
    start = body.index(f"ADD CONSTRAINT {name}")
    clause = body[start:body.index(";", start)]
    return set(re.findall(r"'([a-zA-Z_]+)'", clause))


def test_the_migration_mirrors_the_node_kinds_and_provenance() -> None:
    body = _migration()
    old = {"page", "task", "project", "crm_record", "email_thread", "meeting",
           "artifact", "url"}
    assert _check_values(body, "kb_links_target_kind_check") == old | set(graph.NODE_KINDS)
    assert _check_values(body, "kb_links_source_kind_check") == set(graph.NODE_KINDS)
    assert _check_values(body, "kb_links_provenance_check") == set(graph.PROVENANCES)
    assert "relation_check" not in body  # the code list is the list (§3.2)


def test_the_migration_is_expand_only() -> None:
    body = re.sub(r"--[^\n]*", "", _migration())
    assert "DROP COLUMN" not in body.upper()
    assert "RENAME" not in body.upper()
    assert "SET NOT NULL" not in body.upper()
    for col in ("source_kind", "source_id", "relation", "provenance", "confidence",
                "removed_at", "removed_by"):
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {col}\s+\w+\s*[,;]", body), col


# ── The switch and the seed hook ────────────────────────────────────────────


@pytest.fixture
def orgs(monkeypatch):
    from acb_common import get_settings

    def set_(value: str) -> None:
        monkeypatch.setattr(get_settings(), "graph_sweep_orgs", value, raising=False)

    return set_


def test_the_switch_is_off_by_default() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["graph_sweep_orgs"].default == ""


def test_the_switch_reads_a_list_and_a_star(orgs) -> None:
    a, b = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    orgs("")
    assert not graph.sweep_allowed(a)
    orgs(f" {a.upper()} ")
    assert graph.sweep_allowed(a) and not graph.sweep_allowed(b)
    orgs("*")
    assert graph.sweep_allowed(a) and graph.sweep_allowed(b)
    assert not graph.sweep_allowed(None) and not graph.sweep_allowed("")


async def test_the_seed_opens_no_session_off_the_switch_or_before_night(
        orgs, monkeypatch) -> None:
    opened: list[str] = []

    def _no_session(org: str) -> Any:
        opened.append(org)
        raise AssertionError("no session may open")

    monkeypatch.setattr(graph, "tenant_session", _no_session)
    org = "11111111-1111-4111-8111-111111111111"
    orgs("")
    await graph.seed(org, datetime(2026, 11, 5, 22, 0, tzinfo=UTC))
    orgs(org)
    await graph.seed(org, datetime(2026, 11, 5, graph.SWEEP_HOUR_UTC - 1, 59, tzinfo=UTC))
    assert opened == []


def test_the_kind_and_the_hook_are_registered() -> None:
    from gateway.jobs import worker

    assert worker.effects()[graph.SWEEP_KIND] == ("own_rows", None)
    assert graph.seed in worker.seed_hooks
