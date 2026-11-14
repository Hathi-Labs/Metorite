"""WS-STU C15a — the autonumber field type, with no database (hermetic).

Spec: ``project-docs/specs/ai_studio.md`` §14 C15a. Code:
``acb_common/studio_fields.py``, ``gateway/studio/objects_store.py``,
``gateway/package_host/field_values.py`` and the migration that adds
``studio_object_counters`` (444 at build). The R8 twin is
``test_studio_autonumber_r8.py``.

What this suite holds:

a. The spec of the type: ``prefix``, ``pad`` and ``reset`` and their limits.
b. ``required``, ``many`` and ``hot`` are refused, and the parts belong to the type.
c. The stored form, its check and the text of a number.
d. PKG-13 refuses the type in a declaration of an extension field.
e. The text of the migration: FORCE RLS, the pair keys, the primary key, both
   type CHECKs. The promoted scratch database applies ``generated/04_policies.sql``,
   which forces RLS again, so this source test is the fence of the migration file.
f. The statement of the counter is one atomic upsert, and the allocation runs
   before the value check.
g. The TypeScript twin knows the type.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common.studio_fields import FieldError

pytest.importorskip("sqlalchemy")

from gateway.package_host import field_values as fv
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS studio_object_counters (" in p.read_text(encoding="utf-8"))
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_TS = _ROOT / "workbench/control_plane/src/lib/studio"


def _spec(**extra: Any) -> dict[str, Any]:
    return {"key": "no", "type": "autonumber", **extra}


def _field(**extra: Any) -> sf.FieldSpec:
    return sf.parse_field_spec(_spec(**extra))


# ── a. The spec ────────────────────────────────────────────────────────────


def test_the_type_is_in_the_one_list_with_its_parts() -> None:
    assert sf.FIELD_TYPES[-1] == "autonumber" and len(sf.FIELD_TYPES) == 23
    assert sf.SPEC_PARTS["autonumber"] == ("prefix", "pad", "reset")
    assert "autonumber" not in sf.HOT_TYPES
    assert "autonumber" in sf.VALUE_SHAPES


@pytest.mark.parametrize("extra", [
    {}, {"prefix": ""}, {"prefix": "FV-"}, {"prefix": "a" * 12}, {"prefix": "A_b./-9"},
    {"pad": 1}, {"pad": 9}, {"reset": "never"}, {"reset": "day"},
    {"prefix": "INV/", "pad": 6, "reset": "day", "label": "Number"},
])
def test_a_good_spec_passes(extra: dict[str, Any]) -> None:
    assert sf.validate_field_spec(_spec(**extra)) == []


@pytest.mark.parametrize(("extra", "path"), [
    ({"prefix": "a" * 13}, "prefix"), ({"prefix": "F V"}, "prefix"), ({"prefix": "é"}, "prefix"),
    ({"prefix": 5}, "prefix"),
    ({"pad": 0}, "pad"), ({"pad": 10}, "pad"), ({"pad": "4"}, "pad"), ({"pad": True}, "pad"),
    ({"pad": 2.5}, "pad"),
    ({"reset": "week"}, "reset"), ({"reset": ""}, "reset"), ({"reset": 1}, "reset"),
])
def test_a_bad_part_is_refused_with_its_path(extra: dict[str, Any], path: str) -> None:
    assert [p.path for p in sf.validate_field_spec(_spec(**extra))] == [path]


@pytest.mark.parametrize("flag", ["required", "many", "hot"])
def test_required_many_and_hot_are_refused(flag: str) -> None:
    assert [p.path for p in sf.validate_field_spec(_spec(**{flag: True}))] == [flag]
    with pytest.raises(FieldError) as exc:
        sf.parse_field_spec(_spec(**{flag: True}))
    assert exc.value.field == "no" and "`no`" in exc.value.message
    assert sf.validate_field_spec(_spec(**{flag: False})) == []


@pytest.mark.parametrize("part", ["prefix", "pad", "reset"])
def test_the_parts_belong_to_the_type(part: str) -> None:
    value = {"prefix": "A", "pad": 4, "reset": "day"}[part]
    problems = sf.validate_field_spec({"key": "f_key", "type": "text", part: value})
    assert [p.path for p in problems] == [part]


def test_the_spec_survives_the_store() -> None:
    field = _field(prefix="FV-", pad=6, reset="day")
    assert field.stored_spec() == {
        "required": False, "many": False, "prefix": "FV-", "pad": 6, "reset": "day"}
    back = sf.stored_field("no", "autonumber", field.stored_spec(), False)
    assert back == field
    plain = _field()
    assert sf.stored_field("no", "autonumber", plain.stored_spec(), False) == plain


# ── c. The stored form ─────────────────────────────────────────────────────


def test_the_text_of_a_number() -> None:
    f = _field(prefix="FV-", pad=4)
    assert sf.autonumber_text(f, 7) == "FV-0007"
    assert sf.autonumber_text(f, 12345) == "FV-12345"  # a wider number keeps its digits
    assert sf.autonumber_text(_field(), 3) == "0003"  # the defaults: no prefix, 4 digits
    assert sf.autonumber_text(_field(pad=1, prefix="N"), 10) == "N10"
    assert sf.autonumber_pad(_field()) == 4 and sf.autonumber_reset(_field()) == "never"
    assert sf.autonumber_reset(_field(reset="day")) == "day"


def test_the_check_accepts_the_stored_form_and_a_form_of_an_older_prefix() -> None:
    f = _field(prefix="FV-", pad=4)
    for good in ("FV-0042", "0042", "INV/000002", "FV-999999999999999"):
        assert sf.check_value(f, good) == good
    for bad in ("", "FV-", "FV-00 42", "FV-0042x", 42, None, ["FV-0001"], "a" * 13 + "1"):
        with pytest.raises(FieldError):
            sf.check_value(f, bad)


def test_an_autonumber_is_never_required_by_a_record() -> None:
    f = _field()
    assert sf.validate_record([f], {}) == {}
    assert sf.validate_record([f], {"no": "0001"}) == {"no": "0001"}
    assert sf.max_entry_bytes(f) < sf.MAX_RECORD_BYTES


def test_the_largest_value_fits_the_byte_bound() -> None:
    f = _field(prefix="a" * 12, pad=9)
    longest = sf.autonumber_text(f, sf.MAX_AUTONUMBER)
    assert len(longest.encode()) + 2 <= sf._max_value_bytes(f)
    assert sf.check_value(f, longest) == longest


# ── d. PKG-13 ──────────────────────────────────────────────────────────────


def test_pkg13_refuses_the_type_in_a_declaration() -> None:
    assert "autonumber" in fv.REFUSED_TYPES
    with pytest.raises(FieldError) as exc:
        fv.declared_field("warranty_no", {"type": "autonumber", "prefix": "W-"})
    assert exc.value.field == "warranty_no" and "`warranty_no`" in exc.value.message


# ── e. The migration ───────────────────────────────────────────────────────


def test_the_migration_forces_rls_and_keys_by_the_pair() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "ALTER TABLE %I FORCE  ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE %I ENABLE ROW LEVEL SECURITY" in sql
    assert "ARRAY['studio_object_counters']" in sql
    assert "WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)" in sql
    assert "PRIMARY KEY (organization_id, object_id, field_key, period)" in sql
    assert ("FOREIGN KEY (organization_id, object_id)\n"
            "        REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE") in sql
    assert f"BETWEEN 1 AND {sf.MAX_AUTONUMBER}" in sql
    assert "period = '' OR period ~ '^\\d{4}-\\d{2}-\\d{2}$'" in sql
    assert "SET LOCAL lock_timeout" in sql
    assert "REFERENCES organization (id) ON DELETE CASCADE\n                     DEFAULT" in sql
    # Both type CHECKs hold the whole list, in order.
    for check in ("studio_object_fields_type", "package_field_value_field_type"):
        body = sql.split(f"ADD CONSTRAINT {check} CHECK", 1)[1].split(";")[0]
        assert re.findall(r"'(\w+)'", body) == list(sf.FIELD_TYPES), check
    policies = (_ROOT / "infra/postgres/generated/04_policies.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE studio_object_counters FORCE  ROW LEVEL SECURITY" in policies


# ── f. The statement and the order ─────────────────────────────────────────


def test_the_next_number_is_one_atomic_statement() -> None:
    """A read then a write of the counter would give two writers one number, if
    the org lock ever went. The statement is one upsert that increments in the
    database, and the store reads the counter nowhere else."""
    sql = store._NEXT_NUMBER_SQL
    assert sql.startswith("INSERT INTO studio_object_counters")
    assert "ON CONFLICT (organization_id, object_id, field_key, period)" in sql
    assert "last_value = studio_object_counters.last_value + 1" in sql
    assert sql.rstrip().endswith("RETURNING last_value")
    assert "SELECT" not in sql.upper()
    src = _STORE.read_text(encoding="utf-8")
    # No other text of the store names the table: not a constant, not an inline statement.
    tree = ast.parse(src)
    own = next(n for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "_NEXT_NUMBER_SQL" for t in n.targets))
    inside = {id(c) for c in ast.walk(own)}
    others = [c.lineno for c in ast.walk(tree) if isinstance(c, ast.Constant)
              and isinstance(c.value, str) and "studio_object_counters" in c.value
              and id(c) not in inside]
    assert others == [], others
    fill = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fill_autonumbers")
    assert [n.id for n in ast.walk(fill) if isinstance(n, ast.Name) and n.id.endswith("_SQL")] == [
        "_NEXT_NUMBER_SQL"]
    assert "nextval" not in src and "CREATE SEQUENCE" not in src


def test_the_period_comes_from_the_injectable_clock_never_from_sql() -> None:
    src = _STORE.read_text(encoding="utf-8")
    body = src.split("async def _batch_day", 1)[1].split("\nasync def ", 1)[0]
    assert "_wall_now()" in body and "now()" not in body.replace("_wall_now()", "")


def _calls_in_order(fn: str) -> list[str]:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn)
    calls = [(c.lineno, c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
             for c in ast.walk(node) if isinstance(c, ast.Call)]
    return [name for _, name in sorted(calls)]


def test_the_allocation_runs_before_the_value_check_and_each_write_refuses_the_key() -> None:
    order = _calls_in_order("_apply_write")
    assert order.index("_fill_autonumbers") < order.index("validate_record")
    assert order.count("_refuse_autonumber_keys") == 2  # a create and an update


# ── g. The TypeScript twin ─────────────────────────────────────────────────


def test_the_ts_twin_knows_the_type() -> None:
    manifest = (_TS / "manifest.ts").read_text(encoding="utf-8")
    assert '| "autonumber"' in manifest
    for part in ("prefix", "pad", "reset"):
        assert f"{part}?:" in manifest
    check = (_TS / "fieldCheck.ts").read_text(encoding="utf-8")
    assert re.search(r"^\s+autonumber,$", check, re.M)
    assert f"[0-9]{{1,{len(str(sf.MAX_AUTONUMBER))}}}" in check
    assert '"autonumber"' in (_TS / "blocks.ts").read_text(encoding="utf-8")
    assert 'f.type !== "autonumber"' in (_TS / "packagePreview.ts").read_text(encoding="utf-8")
    assert 'f.type === "autonumber"' in (_TS / "inlineEdit.ts").read_text(encoding="utf-8")
