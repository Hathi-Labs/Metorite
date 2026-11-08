"""WS-PKG PKG-14a-3 — the host-delete triggers, read from the migration.

Spec: ``project-docs/specs/package_host.md`` §8.3, the PKG-14a-3 row of §10
and PKG-Q30. Migration: the file that creates the function
``package_field_value_host_delete`` (372 at build). The R8 suite is
``tests/unit/test_package_field_value_host_delete_r8.py``.

This file needs no database. It holds four rules:

a. Each kind of ``HOST_RECORD_KINDS`` has exactly one AFTER DELETE trigger,
   on its host table, and no trigger names a kind outside the tuple. So a
   new kind without a trigger fails here.
b. Each trigger fires AFTER DELETE, FOR EACH ROW, and on no other event.
c. The function deletes by the org of the old row (the cross-org control),
   and it is not SECURITY DEFINER and swallows no error.
d. The migration is expand-only (R6) and idempotent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from gateway.package_host.field_values import HOST_RECORD_KINDS
from gateway.routes.crm.core import ENTITIES

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"
_FUNCTION = "package_field_value_host_delete"

#: The host table of each record kind (§8.3).
HOST_TABLES: dict[str, str] = {
    "task": "pm_tasks",
    "crm.lead": "crm_leads",
    "crm.contact": "crm_contacts",
    "crm.company": "crm_companies",
    "crm.deal": "crm_deals",
}

_TRIGGER_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(?P<name>\w+)\s+"
    r"(?P<when>BEFORE|AFTER|INSTEAD\s+OF)\s+(?P<events>[\w\s]+?)\s+ON\s+(?P<table>\w+)\s+"
    r"FOR\s+EACH\s+(?P<level>ROW|STATEMENT)\s+EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+"
    r"(?P<fn>\w+)\s*\(\s*'(?P<kind>[^']*)'\s*\)",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _migration() -> tuple[Path, str]:
    """The one migration that creates the function, found by CONTENT."""
    hits = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+{_FUNCTION}\s*\(",
                     p.read_text(encoding="utf-8"))
    ]
    assert len(hits) == 1, f"one migration must create {_FUNCTION}, found {hits}"
    return hits[0], _strip_comments(hits[0].read_text(encoding="utf-8"))


def _triggers(sql: str) -> list[re.Match[str]]:
    return [m for m in _TRIGGER_RE.finditer(sql) if m.group("fn") == _FUNCTION]


def _function_body(sql: str) -> str:
    m = re.search(r"\$pfv_host_delete\$(?P<body>.*?)\$pfv_host_delete\$", sql, re.S)
    assert m, "the function body must sit between $pfv_host_delete$ quotes"
    return m.group("body")


def trigger_kinds(sql: str) -> dict[str, str]:
    """Each record kind that a trigger passes, mapped to its table."""
    out: dict[str, str] = {}
    for m in _triggers(sql):
        kind = m.group("kind")
        assert kind not in out, f"two triggers pass the kind {kind!r}"
        out[kind] = m.group("table")
    return out


def assert_parity(sql: str, kinds: tuple[str, ...]) -> None:
    """The triggers name each kind exactly once, on its host table."""
    got = trigger_kinds(sql)
    assert set(got) == set(kinds), (
        f"the triggers pass {sorted(got)}, and HOST_RECORD_KINDS holds {sorted(kinds)}. "
        "A new kind needs a trigger on its host table in a new migration.")
    for kind, table in got.items():
        assert HOST_TABLES[kind] == table, f"{kind} must fire on {HOST_TABLES[kind]}, not {table}"


# ── a. Parity with HOST_RECORD_KINDS ───────────────────────────────────────


def test_the_triggers_equal_host_record_kinds():
    _, sql = _migration()
    assert set(HOST_TABLES) == set(HOST_RECORD_KINDS), (
        "HOST_TABLES of this test must name each kind of HOST_RECORD_KINDS")
    assert_parity(sql, HOST_RECORD_KINDS)


def test_the_parity_check_fails_for_a_missing_kind():
    """The fence itself: a kind with no trigger fails, and so does an extra one."""
    _, sql = _migration()
    with pytest.raises(AssertionError):
        assert_parity(sql, (*HOST_RECORD_KINDS, "crm.ticket"))
    with pytest.raises(AssertionError):
        assert_parity(sql, HOST_RECORD_KINDS[1:])


def test_the_crm_tables_are_the_tables_of_the_crm_routes():
    _, sql = _migration()
    crm_tables = {e.table for e in ENTITIES.values()}
    for kind, table in trigger_kinds(sql).items():
        if kind.startswith("crm."):
            assert table in crm_tables, f"{table} is not a table of routes/crm/core.py ENTITIES"


# ── b. AFTER DELETE, FOR EACH ROW, and nothing else ─────────────────────────


def test_each_trigger_fires_after_delete_for_each_row_only():
    _, sql = _migration()
    found = _triggers(sql)
    assert len(found) == len(HOST_RECORD_KINDS)
    for m in found:
        assert m.group("when").upper() == "AFTER", m.group("name")
        assert m.group("events").strip().upper() == "DELETE", (
            f"{m.group('name')} must fire on DELETE only, not {m.group('events')!r}")
        assert m.group("level").upper() == "ROW", m.group("name")
    every = re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER", sql, re.I)
    assert len(every) == len(found), "the migration creates a trigger the fence does not read"


# ── c. The function ─────────────────────────────────────────────────────────


def test_the_function_deletes_by_the_org_of_the_old_row():
    _, sql = _migration()
    body = re.sub(r"\s+", " ", _function_body(sql))
    assert "DELETE FROM package_field_value" in body
    assert "organization_id = OLD.organization_id" in body, (
        "the org filter is the cross-org control under a foreign key cascade")
    assert "record_kind = TG_ARGV[0]" in body
    assert "record_id = OLD.id" in body
    assert "RETURN OLD" in body


def test_the_function_adds_no_privilege_and_swallows_no_error():
    _, sql = _migration()
    assert not re.search(r"SECURITY\s+DEFINER", sql, re.I)
    assert not re.search(r"\bEXCEPTION\b", _function_body(sql), re.I)


# ── d. Expand-only and idempotent ──────────────────────────────────────────


def test_the_migration_is_expand_only():
    _, sql = _migration()
    for pattern in (r"DROP\s+TABLE", r"DROP\s+COLUMN", r"\bRENAME\b", r"ALTER\s+TABLE",
                    r"\bTRUNCATE\b", r"\bUPDATE\s+\w+\s+SET\b", r"DROP\s+TRIGGER",
                    r"DROP\s+FUNCTION"):
        assert not re.search(pattern, sql, re.I), f"the migration holds {pattern}"


def test_the_migration_is_idempotent():
    _, sql = _migration()
    assert re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+{_FUNCTION}", sql, re.I)
    plain = re.findall(r"CREATE\s+(?!OR\s+REPLACE)(TRIGGER|FUNCTION)", sql, re.I)
    assert not plain, "each CREATE must take OR REPLACE, so a second run is a no-op"
    assert re.search(r"SET\s+LOCAL\s+lock_timeout", sql, re.I), (
        "a trigger on a hot table must not wait long for its lock")
