"""The fill rank, the rules that need no database (WS-53 CRM-Q17a-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3 and the CRM-Q17a-1
done-when list of §18.9.6. ``test_crm_fill_rank_r8.py`` proves the SQL, the
sync and the routes against a real Postgres.

The fences here:

* ``CRM_FILL_RANK_ORGS`` parses as ``CRM_MIRROR_ORGS`` does, and fails closed;
* each list column and each system field is a standard key, once;
* ``empty_set`` is the one rule of ``empty_columns``, and the layout and the
  export both reach it (the parity fence of done-when item 5);
* a run's counts and its field rules name the changed entities.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateway.crm_sync import fill
from gateway.crm_sync.runs import CursorRow
from gateway.routes.crm import fill_rank, flags
from gateway.routes.crm.core import SYSTEM_FIELDS
from gateway.routes.crm.export import COLUMNS
from gateway.routes.crm.fill_rank import Fill, empty_set, standard_keys

from tests.unit._crm_fakes import crm_switch_on

_ROOT = Path(__file__).resolve().parents[2]
_CRM = _ROOT / "apps/services/gateway/gateway/routes/crm"
ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


# ── The flag ─────────────────────────────────────────────────────────────────


def _fill_orgs(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", value, raising=False)


def test_the_default_list_is_empty_and_allows_no_org(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["crm_fill_rank_orgs"].default == ""
    crm_switch_on(monkeypatch, "*")
    _fill_orgs(monkeypatch, "")
    assert flags.fill_rank_allowed(ORG) is False


def test_a_listed_org_and_star(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _fill_orgs(monkeypatch, f" {ORG.upper()} ")
    assert flags.fill_rank_allowed(ORG) is True
    assert flags.fill_rank_allowed(OTHER) is False
    _fill_orgs(monkeypatch, "*")
    assert flags.fill_rank_allowed(OTHER) is True
    assert flags.fill_rank_allowed(None) is False


def test_the_crm_switch_still_rules(monkeypatch) -> None:
    crm_switch_on(monkeypatch, OTHER)
    _fill_orgs(monkeypatch, "*")
    assert flags.fill_rank_allowed(ORG) is False


def test_the_list_parses_through_the_one_parse_seam() -> None:
    """One grammar for every org list: ``flags._parse_orgs``."""
    tree = ast.parse((_CRM / "flags.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "crm_fill_rank_orgs")
    calls = {c.func.id for c in ast.walk(fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "_parse_orgs" in calls


# ── The keys ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", sorted(COLUMNS))
def test_each_list_column_and_system_field_is_a_standard_key_once(slug: str) -> None:
    keys = standard_keys(slug)
    assert len(keys) == len(set(keys))
    aliases = fill_rank.LIST_ALIASES.get(slug, {})
    for key, _header in COLUMNS[slug]:
        assert aliases.get(key, key) in keys
    for field in SYSTEM_FIELDS[slug]:
        assert field.key in keys


def test_the_joined_list_columns_name_their_real_column() -> None:
    assert fill_rank.LIST_ALIASES["deals"] == {
        "status_name": "status_id", "organization_name": "company_id",
    }
    assert fill_rank.LIST_ALIASES["leads"] == {"status_name": "status_id"}
    assert "organization_name" in standard_keys("leads")  # a real lead column


def test_a_custom_key_has_the_prefix_of_the_custom_filters() -> None:
    assert fill_rank.fill_key("Region", is_system=False) == "custom.Region"
    assert fill_rank.fill_key("email", is_system=True) == "email"
    assert fill_rank.fill_key("Unit_Price", is_system=False, module=True) == "Unit_Price"


# ── The rule of empty_columns ────────────────────────────────────────────────


def test_the_share_and_its_none() -> None:
    assert Fill(1, 3, 1).share == 0.3333
    assert Fill(0, 0, 0).share is None


def test_empty_set_takes_total_above_zero_and_filled_zero() -> None:
    fills = {
        "industry": Fill(0, 4, 0),
        "phone": Fill(1, 4, 1),
        "website": Fill(0, 0, 0),
        "name": Fill(0, 4, 0),
        "last_activity_at": Fill(0, 4, 0),
        "custom.Region": Fill(0, 4, 0),
    }
    assert empty_set("organizations", fills) == ["industry"]


def test_empty_set_names_a_joined_column_in_both_words() -> None:
    fills = {"company_id": Fill(0, 2, 0), "status_id": Fill(2, 2, 1)}
    assert empty_set("deals", fills) == ["company_id", "organization_name"]


def test_the_name_column_and_last_activity_never_show() -> None:
    for slug in COLUMNS:
        fills = {k: Fill(0, 5, 0) for k in standard_keys(slug)}
        empty = set(empty_set(slug, fills))
        assert not empty & fill_rank.NEVER_EMPTY
        assert empty, slug


def test_annotate_leaves_each_definition_alone_when_the_flag_is_off() -> None:
    from gateway.routes.crm.fields import FieldDef

    d = FieldDef(key="Region", label="Region", type="text", position=0,
                 visible=True, source="zoho")
    assert fill_rank.annotate([d], None) == [d]
    got = fill_rank.annotate([d], {"custom.Region": Fill(1, 4, 1)})[0]
    assert (got.fill, got.distinct) == (0.25, 1)
    missing = fill_rank.annotate([d], {})[0]
    assert (missing.fill, missing.distinct) == (None, None)


# ── One function for the layout and the export (done-when items 4 and 5) ─────


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        c.func.id if isinstance(c.func, ast.Name) else c.func.attr
        for c in ast.walk(tree)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name | ast.Attribute)
    }


def test_the_layout_and_the_export_reach_the_one_rule() -> None:
    """R7 fence. The layout calls ``empty_set``, the export calls
    ``empty_columns``, and ``empty_columns`` returns ``empty_set``. No other
    file of the package names the rule."""
    assert "empty_set" in _calls(_CRM / "fields.py")
    assert "empty_columns" in _calls(_CRM / "export.py")
    tree = ast.parse((_CRM / "fill_rank.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "empty_columns")
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "empty_set"
               for c in ast.walk(fn))
    for path in sorted(_CRM.glob("*.py")):
        if path.name == "fill_rank.py":
            continue
        body = path.read_text(encoding="utf-8")
        assert "NEVER_EMPTY" not in body and ".filled == 0" not in body, path.name


def test_the_export_keeps_its_static_columns() -> None:
    """``COLUMNS`` never changes. The export filters it per request."""
    body = (_CRM / "export.py").read_text(encoding="utf-8")
    assert "if c[0] not in empty" in body


# ── Which entity changed ─────────────────────────────────────────────────────


def _ctx(counts: dict, start: dict | None = None, end: dict | None = None) -> SimpleNamespace:
    rules = "zoho:field_rules"
    start_rows = {rules: CursorRow(rules, None, start, None, None)} if start else {}
    end_rows = {rules: CursorRow(rules, None, end, None, None)} if end else {}
    return SimpleNamespace(
        counts=counts, cursors=end_rows, handle=SimpleNamespace(cursors=start_rows),
    )


def test_counts_name_the_changed_entities() -> None:
    ctx = _ctx({
        "organizations": {"upserted": 1, "deleted": 0},
        "contacts": {"upserted": 0, "deleted": 2},
        "leads": {"upserted": 0, "deleted": 0},
        "activities": {"upserted": 9, "deleted": 0},
        "module:Products": {"upserted": 3},
        "module:Quotes": {"seen": 4},
        "tasks": {"upserted": 1},
    })
    assert fill.changed_entities(ctx) == {"company", "contact", "module:Products"}


def test_a_backfilled_or_undone_rule_names_its_entity() -> None:
    rule = {"id": "r1", "entity": "lead", "field": "Email_2", "column": "email"}
    other = {"id": "r2", "entity": "deal", "field": "X", "column": "next_step"}
    # Backfilled in this run: absent at the start, applied at the end.
    assert fill.changed_entities(_ctx({}, None, {"applied": [rule]})) == {"lead"}
    # Undone in this run.
    assert fill.changed_entities(_ctx({}, {"applied": [rule]}, {"applied": []})) == {"lead"}
    # A backfill that finished in this run.
    marked = {**rule, "backfilling": True}
    assert fill.changed_entities(
        _ctx({}, {"applied": [marked, other]}, {"applied": [rule, other]}),
    ) == {"lead"}
    # No change.
    assert fill.changed_entities(_ctx({}, {"applied": [other]}, {"applied": [other]})) == set()


def test_the_statement_names_only_mapped_tables_and_bound_values() -> None:
    from gateway.crm_sources.zoho import mapping

    sql, params = fill.statement("module:Invoices", mapping)
    assert "Invoices" not in sql and params["module"] == "Invoices"
    assert params["std"] == [] and params["prefix"] == ""
    sql, params = fill.statement("company", mapping)
    assert "FROM crm_companies t" in sql
    assert params["prefix"] == "custom." and "name" in params["std"]
    assert "'false'::jsonb" in fill.FILLED_SQL
    assert sql.count("crm_field_fill") == 2  # one upsert, one delete
