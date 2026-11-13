"""First-party logic modules of AI Studio, the hermetic half (STU-B4a).

Spec: ``project-docs/specs/ai_studio.md`` §3.7 layer 3 and §14 B4a. Code:
``gateway/studio/logic/`` and the door ``gateway/routes/studio/invoke.py``.
The R8 half, on a real Postgres, is ``test_studio_logic_r8.py``.

A fake store records each call, so a test can say that a refusal ran no write
statement. The real ``apply.py`` runs on it.

This file proves:

- the registry ships empty, and the contract types are frozen;
- each refusal of the proposal comes before ``apply_writes``: a non-list, a
  malformed item, an undeclared (object, op), 51 writes, an object that the
  package lacks;
- a module that raises applies nothing, and the log and the refusal name no
  text of the exception;
- the module reads only through the reader, with the viewer of the caller, and
  only an object of its ``reads``;
- a good batch is ONE ``apply_writes`` call and one audit row for each write,
  and a preview rolls the batch back and says ``simulated``;
- the grammar ``logic:<name>`` and the forms that did not change;
- the door switch: a dark org gets ``source_not_open``, a call that is not
  direct gets ``approval_required``, a viewer gets ``read_only``;
- the catalog row shows only for an org on the flag.

**The mutations (R7). Named here, and not run in this slice.**

* M1. ``apply._shape``: drop the cap. ``test_each_bad_proposal_is_refused_before_a_write``
  (51 writes) goes red.
* M2. ``apply._shape``: drop the declared-writes loop. The same test (undeclared) goes red.
* M3. ``apply.run_module``: let a module error out as it is. The raising test goes red.
* M4. ``invoke.refusal_for``: drop the ``logic`` switch. ``test_a_dark_org_gets_source_not_open`` goes red.
* M5. ``invoke.refusal_for``: let a delegated call pass. ``test_a_call_that_is_not_direct_...`` goes red.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import get_settings
from acb_common.studio_access import Viewer
from acb_common.studio_manifest import parse_uses
from acb_common.studio_rules import MAX_WRITES_PER_BATCH
from gateway.routes.studio import actions, flags, invoke
from gateway.studio import logic
from gateway.studio.logic import apply as logic_apply
from pydantic import BaseModel, ConfigDict

ANA = "ana@b4a.test"
SECRET = "SECRET-TEXT-OF-THE-ERROR"


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    writes: list[dict[str, Any]] = []
    read: str | None = None
    boom: bool = False


SEEN: list[Any] = []


async def _echo(inp: EchoInput, reader: logic.Reader) -> list[logic.ProposedWrite]:
    if inp.boom:
        raise RuntimeError(SECRET)
    if inp.read is not None:
        SEEN.append(await reader(inp.read))
    return [logic.ProposedWrite(**w) for w in inp.writes]


def _module(reads: tuple[str, ...] = ("visit",),
            writes: tuple[tuple[str, str], ...] = (("visit", "create"), ("visit", "update"),
                                                  ("visit", "delete"))) -> logic.LogicModule:
    return logic.LogicModule(name="test_echo", input_model=EchoInput, reads=frozenset(reads),
                             writes=frozenset(writes), run=_echo, title="Echo")


class FakeStore:
    """The three calls that ``apply.py`` makes. It records each one."""

    def __init__(self, objects: tuple[str, ...] = ("visit",)) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.objects = objects
        self.viewers: list[Viewer] = []

    async def get_object_by_name(self, db: Any, name: str, *, package_id: str) -> Any:
        self.calls.append(("get_object", name, package_id))
        return SimpleNamespace(id=f"oid-{name}", name=name) if name in self.objects else None

    async def apply_writes(self, db: Any, actor: str, *, package_id: str, writes: list[Any],
                           viewer: Viewer, via: Any = None) -> list[Any]:
        self.calls.append(("apply_writes", len(writes), via))
        self.viewers.append(viewer)
        return [SimpleNamespace(id=f"rid-{i}") if w.op != "delete" else True
                for i, w in enumerate(writes)]

    async def query_records(self, db: Any, *, package_id: str, object_id: str, viewer: Viewer,
                            **kw: Any) -> Any:
        self.calls.append(("query_records", object_id))
        self.viewers.append(viewer)
        return SimpleNamespace(records=[SimpleNamespace(id="r1", data={"site": "x"})])


class FakeDb:
    def __init__(self) -> None:
        self.nested = 0
        self.rolled_back = 0

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        self.nested += 1
        try:
            yield
        except BaseException:
            self.rolled_back += 1
            raise


def _w(op: str = "create", obj: str = "visit", id: str | None = None,
       data: Any = None) -> logic.ProposedWrite:
    if data is None and op != "delete":
        data = {"site": "Hosur"}
    if id is None and op != "create":
        id = "11111111-1111-1111-1111-111111111111"
    return logic.ProposedWrite(object=obj, op=op, id=id, data=data)


def _apply(proposed: Any, *, store: FakeStore | None = None, preview: bool = False,
           module: logic.LogicModule | None = None) -> tuple[Any, FakeStore, list[dict[str, Any]]]:
    store = store or FakeStore()
    rows: list[dict[str, Any]] = []

    async def audit(row: dict[str, Any]) -> None:
        rows.append(row)

    async def go() -> Any:
        return await logic_apply.apply_proposed_writes(
            FakeDb(), member=ANA, package_id="pkg-1", module=module or _module(),
            proposed=proposed, viewer=Viewer(ANA), audit=audit,
            detail={"action": "echo", "uses": "logic:test_echo", "class": "B"},
            preview=preview, store=store)

    return asyncio.run(go()), store, rows


# ── the registry and the contract ──────────────────────────────────────────


def test_the_registry_ships_empty() -> None:
    assert logic.MODULES == {}
    assert logic.LOGIC_CLASS == "B"


def test_the_contract_types_are_frozen() -> None:
    module, write = _module(), _w()
    with pytest.raises(dataclasses.FrozenInstanceError):
        module.name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        write.op = "delete"  # type: ignore[misc]
    assert isinstance(module.reads, frozenset) and isinstance(module.writes, frozenset)


def test_the_module_package_touches_no_table_and_no_network() -> None:
    """The fence: the contract file imports no engine, no session, no SQL and
    no HTTP client. ``apply.py`` imports the exception base of the driver only."""
    for path in (logic.__file__, logic_apply.__file__):
        text = Path(path).read_text(encoding="utf-8")
        for word in ("create_async_engine", "tenant_session", "httpx", "requests", "import text",
                     "from sqlalchemy import", "psycopg", "redis"):
            assert word not in text, (path, word)


# ── a refusal comes before a write ─────────────────────────────────────────


@pytest.mark.parametrize("proposed", [
    "not a list",
    {"object": "visit"},
    [("visit", "create")],
    [_w(), {"object": "visit", "op": "create", "data": {}}],
    [logic.ProposedWrite(object="visit", op="create", id="x", data={"site": "a"})],
    [logic.ProposedWrite(object="visit", op="create", data=None)],
    [logic.ProposedWrite(object="visit", op="update", id=None, data={"site": "a"})],
    [logic.ProposedWrite(object="visit", op="delete", id="x", data={"site": "a"})],
    [logic.ProposedWrite(object="visit", op="merge", id="x", data={})],
    [logic.ProposedWrite(object=7, op="create", data={})],  # type: ignore[arg-type]
], ids=["str", "dict", "tuple", "dict-item", "create-with-id", "create-no-data",
        "update-no-id", "delete-with-data", "bad-op", "bad-object"])
def test_each_bad_proposal_is_refused_before_a_write(proposed: Any) -> None:
    store = FakeStore()
    with pytest.raises(logic_apply.LogicRefused) as err:
        _apply(proposed, store=store)
    assert err.value.code == logic_apply.BAD_OUTPUT
    assert store.calls == []


def test_an_undeclared_op_is_refused_before_a_statement() -> None:
    store = FakeStore()
    with pytest.raises(logic_apply.LogicRefused) as err:
        _apply([_w("create"), _w("update")], store=store,
               module=_module(writes=(("visit", "create"),)))
    assert err.value.code == logic_apply.UNDECLARED_WRITE and store.calls == []


def test_more_than_the_cap_is_refused_before_a_statement() -> None:
    store = FakeStore()
    with pytest.raises(logic_apply.LogicRefused) as err:
        _apply([_w() for _ in range(MAX_WRITES_PER_BATCH + 1)], store=store)
    assert err.value.code == logic_apply.TOO_MANY_WRITES and store.calls == []
    assert MAX_WRITES_PER_BATCH == 50
    done, store2, rows = _apply([_w() for _ in range(MAX_WRITES_PER_BATCH)])
    assert len(done) == 50 and len(rows) == 50
    assert [c for c in store2.calls if c[0] == "apply_writes"] == [("apply_writes", 50, None)]


def test_an_object_that_the_package_lacks_is_refused_and_applies_nothing() -> None:
    store = FakeStore(objects=("visit",))
    module = _module(writes=(("visit", "create"), ("ghost", "create")))
    with pytest.raises(logic_apply.LogicRefused) as err:
        _apply([_w(), _w(obj="ghost")], store=store, module=module)
    assert err.value.code == logic_apply.OBJECT_MISSING
    assert not [c for c in store.calls if c[0] == "apply_writes"]
    # The lookup carries the package id of the door, never one of the proposal.
    assert {c[2] for c in store.calls} == {"pkg-1"}


def test_a_module_that_raises_applies_nothing_and_leaks_no_text(caplog) -> None:
    store = FakeStore()

    async def go() -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=_module(), body={"boom": True}, package_id="pkg-1",
            viewer=Viewer(ANA), store=store)

    with pytest.raises(logic_apply.LogicRefused) as err:
        asyncio.run(go())
    assert err.value.code == logic_apply.LOGIC_FAILED
    assert SECRET not in err.value.message and SECRET not in str(err.value)
    assert err.value.__cause__ is None and store.calls == []


def test_a_body_that_the_input_model_refuses_is_bad_input() -> None:
    async def go() -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=_module(), body={"nope": 1}, package_id="pkg-1",
            viewer=Viewer(ANA), store=FakeStore())

    with pytest.raises(logic_apply.LogicRefused) as err:
        asyncio.run(go())
    assert err.value.code == logic_apply.BAD_INPUT


# ── the reader ─────────────────────────────────────────────────────────────


def test_the_module_reads_through_the_reader_as_the_caller() -> None:
    store, viewer = FakeStore(), Viewer("caller@b4a.test")
    SEEN.clear()

    async def go() -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=_module(), body={"read": "visit"}, package_id="pkg-1",
            viewer=viewer, store=store)

    assert asyncio.run(go()) == []
    assert SEEN == [[{"id": "r1", "data": {"site": "x"}}]]
    assert store.viewers == [viewer]


def test_a_read_outside_the_declared_reads_is_refused() -> None:
    store = FakeStore(objects=("visit", "secret"))

    async def go() -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=_module(), body={"read": "secret"}, package_id="pkg-1",
            viewer=Viewer(ANA), store=store)

    with pytest.raises(logic_apply.LogicRefused) as err:
        asyncio.run(go())
    assert err.value.code == logic_apply.UNDECLARED_READ and store.calls == []


def test_the_module_gets_two_arguments_and_no_session() -> None:
    got: list[Any] = []

    async def run(inp: Any, reader: Any) -> list[logic.ProposedWrite]:
        got.extend([inp, reader])
        return []

    module = dataclasses.replace(_module(), run=run)
    asyncio.run(logic_apply.run_module(
        FakeDb(), module=module, body={}, package_id="pkg-1", viewer=Viewer(ANA),
        store=FakeStore()))
    assert isinstance(got[0], EchoInput) and callable(got[1])
    assert not isinstance(got[1], FakeDb) and not hasattr(got[1], "execute")
    assert list(inspect.signature(logic.LogicModule).parameters) == [
        "name", "input_model", "reads", "writes", "run", "title"]


# ── the batch ──────────────────────────────────────────────────────────────


def test_a_good_batch_is_one_call_and_one_audit_row_for_each_write() -> None:
    done, store, rows = _apply([_w("create"), _w("update"), _w("delete")])
    assert [c for c in store.calls if c[0] == "apply_writes"] == [("apply_writes", 3, None)]
    assert [(d.op, d.object_name) for d in done] == [
        ("create", "visit"), ("update", "visit"), ("delete", "visit")]
    assert [r["outcome"] for r in rows] == ["ran"] * 3
    assert [r["op"] for r in rows] == ["create", "update", "delete"]
    assert all(r["uses"] == "logic:test_echo" and r["class"] == "B" for r in rows)
    assert "Hosur" not in repr(rows)


def test_an_empty_proposal_writes_nothing_and_calls_no_store() -> None:
    done, store, rows = _apply([])
    assert done == [] and rows == [] and store.calls == []


def test_a_preview_rolls_the_batch_back_and_says_simulated() -> None:
    store, rows_out = FakeStore(), []
    db = FakeDb()

    async def audit(row: dict[str, Any]) -> None:
        rows_out.append(row)

    done = asyncio.run(logic_apply.apply_proposed_writes(
        db, member=ANA, package_id="pkg-1", module=_module(), proposed=[_w("create"), _w("update")],
        viewer=Viewer(ANA), audit=audit, detail={"action": "echo"}, preview=True, store=store))
    assert db.nested == 1 and db.rolled_back == 1
    assert [r["outcome"] for r in rows_out] == ["simulated", "simulated"]
    assert all(r["preview"] is True for r in rows_out)
    # The id of a preview create was never stored, so no row names it.
    assert "record_id" not in rows_out[0] and "record_id" in rows_out[1]
    assert len(done) == 2


def test_a_batch_that_the_store_refuses_is_one_refusal_with_its_cause() -> None:
    from gateway.studio import objects_store

    class Refusing(FakeStore):
        async def apply_writes(self, *a: Any, **kw: Any) -> list[Any]:
            raise objects_store.BatchError(1, objects_store.RecordNotFound("x"))

    with pytest.raises(logic_apply.LogicRefused) as err:
        _apply([_w("create"), _w("create")], store=Refusing())
    assert err.value.code == logic_apply.RECORD_REFUSED
    assert isinstance(err.value.cause, objects_store.RecordNotFound)


# ── the grammar ────────────────────────────────────────────────────────────


def test_logic_is_a_uses_form() -> None:
    ref = parse_uses("logic:test_echo")
    assert ref is not None and (ref.source, ref.logic) == ("logic", "test_echo")
    for bad in ("logic:", "logic:Bad Name", "logic:1abc", "logic:a.b", "logic:test_echo ",
                "logic:test_echo\n", "Logic:test_echo"):
        assert parse_uses(bad) is None, bad


def test_the_other_uses_forms_did_not_change() -> None:
    wf = "123e4567-e89b-12d3-a456-426614174000"
    assert parse_uses("object:items.list") == parse_uses("object:items.list")
    got = parse_uses("object:items.list")
    assert got is not None and (got.source, got.object, got.op, got.logic) == (
        "object", "items", "list", None)
    assert parse_uses("integration:notify.email").source == "integration"  # type: ignore[union-attr]
    assert parse_uses(f"workflow:{wf}").source == "workflow"  # type: ignore[union-attr]
    door = parse_uses("knowledge.list_pages")
    assert door is not None and (door.source, door.app, door.op) == (
        "manifest", "knowledge", "list_pages")
    assert parse_uses(None) is None and parse_uses("nothing") is None


# ── the switch and the door ────────────────────────────────────────────────


@pytest.fixture
def one_module(monkeypatch):
    monkeypatch.setitem(logic.MODULES, "test_echo", _module())


def test_a_dark_org_gets_source_not_open(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_logic_orgs", "")
    assert not flags.logic_allowed("acme") and not flags.logic_allowed(None)
    ref = parse_uses("logic:test_echo")
    for direct in (False, True):
        assert invoke.refusal_for(ref, "B", direct=direct, logic=False) == "source_not_open"
    monkeypatch.setattr(get_settings(), "studio_logic_orgs", "acme")
    assert flags.logic_allowed("acme") and flags.logic_allowed("ACME")
    assert not flags.logic_allowed("globex")
    monkeypatch.setattr(get_settings(), "studio_logic_orgs", "*")
    assert flags.logic_allowed("globex")


def test_a_call_that_is_not_direct_gets_approval_required_and_no_card() -> None:
    ref = parse_uses("logic:test_echo")
    assert invoke.refusal_for(ref, "B", logic=True) == "approval_required"
    # A delegated call (an ask for a card) never passes: B4b builds that card.
    assert invoke.refusal_for(ref, "B", delegated=True, logic=True) == "approval_required"
    assert invoke.refusal_for(ref, "B", direct=True, logic=True) is None
    assert invoke.refusal_for(ref, "B", preview=True, logic=True) is None
    assert ("logic", None) not in invoke.DELEGATED_EXECUTORS
    assert invoke._mode(ref, False, True, None) == "none"
    assert invoke._mode(ref, True, False, None) == "direct"
    assert invoke._mode(ref, True, False, None, True) == "preview"


def test_a_viewer_gets_read_only_also_for_a_preview() -> None:
    ref = parse_uses("logic:test_echo")
    for kw in ({"direct": True}, {"preview": True}, {}):
        assert invoke.refusal_for(ref, "B", viewer=True, logic=True, **kw) == "read_only"


def test_the_catalog_row_shows_only_for_an_org_on_the_flag(monkeypatch, one_module) -> None:
    from acb_common.db import bind_tenant, release_tenant

    monkeypatch.setattr(get_settings(), "studio_logic_orgs", "acme")
    token = bind_tenant("acme")
    try:
        (row,) = actions._logic_rows()
    finally:
        release_tenant(token)
    assert (row["id"], row["source"], row["class"], row["title"]) == (
        "logic:test_echo", "logic", "B", "Echo")
    assert "writes" in row["input_schema"]["properties"]
    token = bind_tenant("globex")
    try:
        assert actions._logic_rows() == []
    finally:
        release_tenant(token)


def test_the_catalog_has_no_logic_row_in_production() -> None:
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant("acme")
    try:
        assert actions._logic_rows() == []
    finally:
        release_tenant(token)


# ── a module cannot choose the text of an answer ───────────────────────────


def _raising(make: Any) -> logic.LogicModule:
    async def run(inp: Any, reader: Any) -> Any:
        raise make()

    return dataclasses.replace(_module(), run=run)


@pytest.mark.parametrize("shape", ["logic_refused", "sqlalchemy", "record_refused_cause"])
def test_a_module_raised_error_of_any_shape_is_the_fixed_logic_failed(shape, caplog) -> None:
    import logging

    from sqlalchemy.exc import SQLAlchemyError

    makers = {
        "logic_refused": lambda: logic_apply.LogicRefused(SECRET),
        "sqlalchemy": lambda: SQLAlchemyError(SECRET),
        "record_refused_cause": lambda: logic_apply.LogicRefused(
            logic_apply.RECORD_REFUSED, ValueError(SECRET)),
    }
    caplog.set_level(logging.DEBUG)
    store = FakeStore()

    async def go() -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=_raising(makers[shape]), body={}, package_id="pkg-1",
            viewer=Viewer(ANA), store=store)

    with pytest.raises(logic_apply.LogicRefused) as err:
        asyncio.run(go())
    assert err.value.code == logic_apply.LOGIC_FAILED and err.value.cause is None
    assert SECRET not in err.value.message and SECRET not in repr(err.value.args)
    assert SECRET not in caplog.text
    assert all(SECRET not in r.getMessage() and SECRET not in str(r.__dict__)
               for r in caplog.records)
    assert store.calls == []


def test_an_error_that_the_reader_raised_passes_as_it_is() -> None:
    """The reader's own refusal and a store fault keep their type, so the door
    maps them. The module cannot forge one: it never holds the list."""
    from sqlalchemy.exc import SQLAlchemyError

    class Down(FakeStore):
        async def query_records(self, *a: Any, **kw: Any) -> Any:
            raise SQLAlchemyError("store down")

    async def run(inp: Any, reader: Any) -> Any:
        return await reader(inp.read)

    module = dataclasses.replace(_module(), run=run)

    async def go(read: str, store: FakeStore) -> Any:
        return await logic_apply.run_module(
            FakeDb(), module=module, body={"read": read}, package_id="pkg-1",
            viewer=Viewer(ANA), store=store)

    with pytest.raises(logic_apply.LogicRefused) as err:
        asyncio.run(go("secret", FakeStore()))
    assert err.value.code == logic_apply.UNDECLARED_READ
    with pytest.raises(SQLAlchemyError):
        asyncio.run(go("visit", Down()))
