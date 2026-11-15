"""WS-STU C23a — the coded field type, with no database (hermetic).

The value cases live in ``test_studio_fields.py`` and in the golden file. This file
holds the parts that no table of values covers: the text of migration 445, the
refusal of a changed ``system`` in a sync, and the PKG-13 choice.
"""

from __future__ import annotations

import re
from pathlib import Path

from acb_common import studio_fields as sf
from gateway.package_host import field_values
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "infra/postgres/445_studio_coded_value.sql"


def test_the_migration_widens_both_checks_to_the_whole_list_and_adds_no_table() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").replace("\r\n", "\n")
    for check in ("studio_object_fields_type", "package_field_value_field_type"):
        body = sql.split(f"ADD CONSTRAINT {check} CHECK", 1)[1].split(";")[0]
        assert re.findall(r"'(\w+)'", body) == list(sf.FIELD_TYPES), check
    assert sql.count("LIKE '%''coded''%'") == 2
    assert "SET LOCAL lock_timeout" in sql
    assert "CREATE TABLE" not in sql.upper()


def test_a_changed_system_is_a_sync_problem() -> None:
    assert "system" in store._FIELD_ATTRS


def test_a_packaged_app_may_declare_a_coded_field() -> None:
    assert "coded" not in field_values.REFUSED_TYPES
    field = field_values.declared_field("dx", {"type": "coded", "system": "X-SYS"})
    assert field.system == "X-SYS"
    checked, text = field_values.value_json(field, {"system": "X-SYS", "code": "X-0001"})
    assert checked == {"system": "X-SYS", "code": "X-0001"}
    assert text == '{"system": "X-SYS", "code": "X-0001"}'
