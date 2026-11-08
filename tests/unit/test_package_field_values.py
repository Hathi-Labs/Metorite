"""WS-PKG PKG-13 — the field-value store, with no database (hermetic).

Spec: ``project-docs/specs/package_host.md`` §5.1, §8.3 and the PKG-13 row
of §10. Code: ``gateway/package_host/field_values.py``. The R8 twin is
``tests/unit/test_package_field_values_r8.py``.

What this suite holds:

a. A bad record kind, record id, field key or install id is refused before
   the first statement.
b. A value of the wrong type raises ``FieldError``, and the message names
   the field key. No write runs.
c. A value larger than the CHECK is refused before the write, also when
   ``check_value`` takes it.
d. A declaration that sets ``required``, ``hot`` or ``to``, or has the type
   ``relation`` or ``file``, is refused, and the message names the key.
e. The order of the statements: the read of the bound org, then the install
   row with ``FOR KEY SHARE``, then the write. Each statement names the
   bound org beside RLS. No INSERT names an org.
f. Only ``field_values.py`` writes ``package_field_value`` (a source scan of
   ``apps/`` and ``packages/``).
g. ``HOST_RECORD_KINDS``, the field key pattern and the types agree with the
   CHECKs of the migration. The suite finds the migration by its content,
   never by its number (R1).
h. The R8 twin is in the ``--must-run`` lists of pr-check and deploy.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields
from acb_common.db import TenantUnbound
from acb_common.studio_fields import FieldError
from gateway.package_host import field_values as fv
from gateway.package_host.field_values import FieldNotDeclared
from gateway.package_host.store import _BOUND_ORG, InstallError, InstallNotFound, StateRefused

_ROOT = Path(__file__).resolve().parents[2]
ORG = "00000000-0000-0000-0000-00000000000a"
INSTALL = str(uuid.uuid4())
RECORD = str(uuid.uuid4())
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS package_field_value (" in p.read_text(encoding="utf-8"))


class _Result:
    def __init__(self, row: tuple[Any, ...] | None = None,
                 rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._row

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeDb:
    """Records each statement. Answers the bound org and the level, and the
    install row: its state and the payload of its declaration."""

    def __init__(self, *, org: str | None = ORG, level: str = "read committed",
                 state: str | None = "active", payload: Any = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.org = org
        self.level = level
        self.state = state
        self.payload = payload

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.startswith("SELECT CAST(CAST(NULLIF"):
            return _Result(row=(self.org, self.level))
        if sql.startswith("SELECT i.state"):
            if self.state is None:
                return _Result()
            text_payload = None if self.payload is None else json.dumps(self.payload)
            return _Result(row=(self.state, text_payload))
        return _Result(rowcount=1)

    def writes(self) -> list[str]:
        return [s for s, _ in self.calls if s.startswith(("INSERT", "DELETE"))]


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _args(**kw: Any) -> dict[str, Any]:
    return {"install_id": INSTALL, "record_kind": "crm.deal", "record_id": RECORD,
            "field_key": "warranty_end", "value": "2027-01-31", **kw}


def _set(db: _FakeDb, **kw: Any) -> Any:
    return _run(fv.set_value(db, **_args(**kw)))


# ── a. The arguments, before the database ──────────────────────────────────


@pytest.mark.parametrize(("kw", "code"), [
    ({"record_kind": "crm.invoice"}, "bad_record_kind"),
    ({"record_kind": "Task"}, "bad_record_kind"),
    ({"record_kind": None}, "bad_record_kind"),
    ({"record_id": "not-a-uuid"}, "bad_record_id"),
    ({"record_id": None}, "bad_record_id"),
    ({"field_key": "Warranty"}, "bad_field_key"),
    ({"field_key": "a" * 49}, "bad_field_key"),
    ({"field_key": "1st"}, "bad_field_key"),
    ({"install_id": "x"}, "bad_install_id"),
])
def test_a_bad_argument_is_refused_before_the_database(kw: dict[str, Any], code: str) -> None:
    db = _FakeDb(payload={"type": "date"})
    with pytest.raises(InstallError) as exc:
        _set(db, **kw)
    assert (exc.value.code, exc.value.status) == (code, 422)
    assert db.calls == [], "a refusal of an argument must run no statement"


@pytest.mark.parametrize("fn", [fv.get_values, fv.delete_values])
def test_the_read_and_the_delete_refuse_a_bad_kind_before_the_database(fn: Any) -> None:
    db = _FakeDb()
    with pytest.raises(InstallError) as exc:
        _run(fn(db, record_kind="pm_tasks", record_id=RECORD))
    assert exc.value.code == "bad_record_kind"
    assert db.calls == []


# ── b. The type check names the field key ──────────────────────────────────


@pytest.mark.parametrize(("payload", "value"), [
    ({"type": "date"}, "31/01/2027"),
    ({"type": "number"}, "twelve"),
    ({"type": "checkbox"}, 1),
    ({"type": "select", "options": ["a", "b"]}, "c"),
    ({"type": "text", "many": True}, "one"),
    ({"type": "email"}, "not an address"),
])
def test_a_value_of_the_wrong_type_names_the_key_and_writes_nothing(
        payload: dict[str, Any], value: Any) -> None:
    db = _FakeDb(payload=payload)
    with pytest.raises(FieldError) as exc:
        _set(db, value=value)
    assert exc.value.field == "warranty_end"
    assert "`warranty_end`" in str(exc.value)
    assert db.writes() == []


def test_a_good_value_is_upserted_with_the_declared_type() -> None:
    db = _FakeDb(payload={"type": "email", "label": "Owner"})
    assert _set(db, value="Ana@X.test") == "ana@x.test"
    sql, params = db.calls[-1]
    assert sql.startswith("INSERT INTO package_field_value")
    assert "ON CONFLICT (organization_id, install_id, record_kind, record_id, field_key)" in sql
    assert params["type"] == "email"
    assert json.loads(params["value"]) == "ana@x.test"
    assert (params["kind"], params["record"], params["key"]) == ("crm.deal", RECORD, "warranty_end")


def test_none_deletes_the_row() -> None:
    db = _FakeDb(payload={"type": "date"})
    assert _set(db, value=None) is None
    assert db.writes() == [fv._DELETE_ONE_SQL]


# ── c. The size, before the write ──────────────────────────────────────────


@pytest.mark.parametrize(("payload", "value"), [
    # 20000 characters of 4 bytes each: check_value takes it, the CHECK not.
    ({"type": "longText"}, "\U0001d11e" * studio_fields.MAX_LONG_TEXT_CHARS),
    ({"type": "text", "many": True}, ["x" * studio_fields.MAX_TEXT_CHARS] * 40),
])
def test_a_value_over_the_size_is_refused_before_the_write(
        payload: dict[str, Any], value: Any) -> None:
    field = fv.declared_field("notes", payload)
    studio_fields.check_value(field, value)  # the value check alone takes it
    db = _FakeDb(payload=payload)
    with pytest.raises(FieldError) as exc:
        _set(db, field_key="notes", value=value)
    assert exc.value.field == "notes" and "`notes`" in str(exc.value)
    assert str(fv.MAX_VALUE_BYTES) in str(exc.value)
    assert db.writes() == []


def test_the_byte_bound_is_the_bound_of_the_check() -> None:
    code = _code(_MIGRATION.read_text(encoding="utf-8"))
    assert f"octet_length(value::text) <= {fv.MAX_VALUE_BYTES}" in code


# ── d. The declaration ─────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    {"type": "text", "required": True},
    {"type": "text", "hot": True},
    {"type": "relation", "to": "crm.deal"},
    {"type": "relation"},
    {"type": "file"},
    {"type": "text", "key": "other"},
    {"type": "nope"},
    {"type": "select"},
    ["text"],
])
def test_a_refused_declaration_names_the_key(payload: Any) -> None:
    db = _FakeDb(payload=payload)
    with pytest.raises(FieldError) as exc:
        _set(db, value="x")
    assert exc.value.field == "warranty_end"
    assert "`warranty_end`" in str(exc.value)
    assert db.writes() == []


def test_a_declaration_may_say_false_for_required_and_hot() -> None:
    field = fv.declared_field("k", {"type": "text", "required": False, "hot": False,
                                    "label": "K", "many": True})
    assert (field.key, field.type, field.many, field.required) == ("k", "text", True, False)


def test_an_undeclared_key_raises_field_not_declared() -> None:
    db = _FakeDb(payload=None)
    with pytest.raises(FieldNotDeclared) as exc:
        _set(db)
    assert (exc.value.code, exc.value.status) == ("field_not_declared", 422)
    assert "`warranty_end`" in str(exc.value)
    assert db.writes() == []


@pytest.mark.parametrize("state", ["paused", "archived"])
def test_an_install_that_is_not_active_takes_no_value(state: str) -> None:
    db = _FakeDb(state=state, payload={"type": "date"})
    with pytest.raises(StateRefused) as exc:
        _set(db)
    assert (exc.value.code, exc.value.status) == ("install_not_active", 409)
    db = _FakeDb(state=state, payload={"type": "date"})
    with pytest.raises(StateRefused):
        _set(db, value=None)
    assert db.writes() == []


def test_a_missing_install_is_install_not_found() -> None:
    db = _FakeDb(state=None)
    with pytest.raises(InstallNotFound) as exc:
        _set(db)
    assert exc.value.status == 404
    assert db.writes() == []


# ── e. The statements ──────────────────────────────────────────────────────


def test_the_order_of_the_statements() -> None:
    db = _FakeDb(payload={"type": "date"})
    _set(db)
    sqls = [s for s, _ in db.calls]
    assert len(sqls) == 3
    assert sqls[0].startswith("SELECT CAST(CAST(NULLIF")
    assert sqls[1] == fv._INSTALL_SQL and sqls[1].endswith("FOR KEY SHARE OF i")
    assert db.calls[1][1]["claim"] == "crm.deal:warranty_end"
    assert sqls[2] == fv._UPSERT_SQL


def test_an_unbound_session_raises_and_writes_nothing() -> None:
    for call in (
        lambda db: fv.set_value(db, **_args()),
        lambda db: fv.get_values(db, record_kind="task", record_id=RECORD),
        lambda db: fv.delete_values(db, record_kind="task", record_id=RECORD),
    ):
        db = _FakeDb(org=None, payload={"type": "date"})
        with pytest.raises(TenantUnbound):
            _run(call(db))
        assert len(db.calls) == 1


def test_the_writer_refuses_a_level_that_is_not_read_committed() -> None:
    db = _FakeDb(level="repeatable read", payload={"type": "date"})
    with pytest.raises(RuntimeError, match="read committed"):
        _set(db)
    assert len(db.calls) == 1


@pytest.mark.parametrize("name", [
    "_INSTALL_SQL", "_UPSERT_SQL", "_DELETE_ONE_SQL", "_DELETE_RECORD_SQL", "_GET_SQL"])
def test_each_statement_names_the_bound_org(name: str) -> None:
    sql = getattr(fv, name)
    assert _BOUND_ORG in sql


def test_the_insert_names_no_org() -> None:
    """The column DEFAULT reads the bound tenant, so no caller names an org."""
    columns = fv._UPSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    assert "organization_id" not in columns


def test_the_read_hides_a_paused_install_and_a_changed_type() -> None:
    assert "i.state = 'active'" in fv._GET_SQL
    assert "c.payload ->> 'type' = v.field_type" in fv._GET_SQL
    assert "c.key = v.record_kind || ':' || v.field_key" in fv._GET_SQL


def test_get_values_decodes_each_row() -> None:
    class _Db(_FakeDb):
        async def execute(self, stmt: Any, params: Any = None) -> _Result:
            if "FROM package_field_value AS v" in str(stmt):
                self.calls.append((str(stmt), params))
                return _Result(rows=[(INSTALL, "app-1", "k", "money",
                                      '{"minor": 5, "currency": "INR"}')])
            return await super().execute(stmt, params)

    got = _run(fv.get_values(_Db(), record_kind="task", record_id=RECORD))
    assert got == [fv.FieldValue(install_id=INSTALL, app_id="app-1", field_key="k",
                                 field_type="money", value={"minor": 5, "currency": "INR"})]


# ── f. The one writer ──────────────────────────────────────────────────────


_WRITE = re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+package_field_value\b", re.I)


def test_only_the_field_store_writes_a_field_value() -> None:
    """A second INSERT, UPDATE or DELETE of ``package_field_value`` under
    ``apps/`` or ``packages/`` fails here. The table names no member, so the
    member purge has no row for it. The FK cascade of a removal is legal and
    names no statement."""
    writers = sorted(
        str(p.relative_to(_ROOT)).replace("\\", "/")
        for folder in ("apps", "packages")
        for p in (_ROOT / folder).rglob("*.py")
        if "node_modules" not in p.parts and _WRITE.search(p.read_text(encoding="utf-8")))
    assert writers == ["apps/services/gateway/gateway/package_host/field_values.py"]
    members = (_ROOT / "apps/services/gateway/gateway/routes/admin/members.py").read_text(
        encoding="utf-8")
    assert "package_field_value" not in members
    assert _WRITE.search(fv._UPSERT_SQL)
    assert _WRITE.search(fv._DELETE_ONE_SQL)
    assert _WRITE.search(fv._DELETE_RECORD_SQL)


# ── g. The migration agrees with the code ──────────────────────────────────


def _code(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def test_host_record_kinds_agree_with_the_check_and_the_spec() -> None:
    code = _code(_MIGRATION.read_text(encoding="utf-8"))
    shape = re.search(r"record_kind ~ '([^']+)'", code)
    assert shape is not None
    pattern = re.compile(shape.group(1), re.ASCII)
    assert "length(record_kind) <= 64" in code
    assert len(set(fv.HOST_RECORD_KINDS)) == len(fv.HOST_RECORD_KINDS) == 5
    for kind in fv.HOST_RECORD_KINDS:
        assert pattern.fullmatch(kind), kind
        assert len(kind) <= 64
    spec = (_ROOT / "project-docs" / "specs" / "package_host.md").read_text(encoding="utf-8")
    section = spec.split("### 8.3 Field extensions", 1)[1].split("\n### 8.4", 1)[0]
    assert set(re.findall(r"`(task|crm\.[a-z]+)`", section)) == set(fv.HOST_RECORD_KINDS)


def test_the_key_and_the_type_checks_agree_with_studio_fields() -> None:
    code = _code(_MIGRATION.read_text(encoding="utf-8"))
    assert f"field_key ~ '^{studio_fields.NAME_PATTERN}$'" in code
    check = code.split("CONSTRAINT package_field_value_field_type CHECK (field_type IN (", 1)[1]
    types = re.findall(r"'([A-Za-z]+)'", check.split("))", 1)[0])
    assert tuple(types) == studio_fields.FIELD_TYPES


def test_the_migration_is_expand_only_and_tenant_scoped() -> None:
    code = _code(_MIGRATION.read_text(encoding="utf-8"))
    assert code.lstrip().startswith("BEGIN;") and code.rstrip().endswith("COMMIT;")
    assert not re.search(r"\b(DROP\s+(TABLE|COLUMN|CONSTRAINT)|RENAME)\b", code, re.I)
    assert set(re.findall(r"ALTER TABLE\s+([a-z_]+)", code)) == {"package_field_value"}
    assert "ALTER TABLE package_field_value ENABLE ROW LEVEL SECURITY" in code
    assert "ALTER TABLE package_field_value FORCE  ROW LEVEL SECURITY" in code
    assert code.count(
        "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)") == 1
    # REFERENCES before DEFAULT, and never the name that phase 3 adds.
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+DEFAULT", code)
    assert "package_field_value_org_fk" not in code
    assert "FOREIGN KEY (organization_id, install_id)" in code
    assert "REFERENCES package_install (organization_id, id) ON DELETE CASCADE" in code
    assert "PRIMARY KEY (organization_id, install_id, record_kind, record_id, field_key)" in code
    assert "ON package_field_value (organization_id, record_kind, record_id)" in code
    # No column names a member.
    assert not re.search(r"\b\w*(email|_by|member)\w*\s+TEXT\b", code, re.I)


# ── h. The R8 twin runs in CI ──────────────────────────────────────────────


def _must_run(workflow: str) -> list[str]:
    lines = (_ROOT / ".github" / "workflows" / workflow).read_text(
        encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "--must-run \\")
    listed: list[str] = []
    for line in lines[start + 1:]:
        listed.append(line.strip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return listed


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    """Without the entry, the R8 twin skips in CI with no database and the
    job stays green."""
    assert "tests/unit/test_package_field_values_r8.py" in _must_run(workflow)
