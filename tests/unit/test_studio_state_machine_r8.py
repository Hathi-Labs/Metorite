"""WS-STU STU-C6 — state machines on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.7 and the §14 C6 row. Code:
``acb_common/studio_rules.py`` (``check_move``, ``with_initial``) and the
batch section of ``gateway/studio/objects_store.py`` (``_apply_write`` and
``_check_records``). ``test_studio_rules.py`` holds the hermetic half and
``test_studio_objects_batch_r8.py`` the batch it extends.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_state_machine_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (C6).** A create takes ``initial``, and another state is refused.
A change needs a declared move and a guard that holds on the merged record. A
batch counts the net move of a record. A cleared state and a stored state that
is not declared are refused. A delete is not a move. A refused batch writes
nothing. A machine adds no SQL key check. Org B sees no machine of org A.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``studio_rules.check_move``: drop the loop over the moves, so any change
  passes. ``test_r8_an_illegal_move_is_refused_and_the_row_stays`` goes red.
* M2. ``check_move``: the guard reads ``before``.
  ``test_r8_the_guard_reads_the_merged_record`` goes red.
* M3. ``check_move``: skip the create check.
  ``test_r8_a_create_in_another_state_is_refused`` goes red.
* M4. ``studio_rules.touched_keys``: take ``STATE_MACHINE`` out of the early
  return. ``test_r8_a_machine_queues_no_key_check`` goes red.
* M5. ``studio_rules._role_refused``: answer None.
  ``test_r8_a_role_guard_is_refused_at_declaration`` goes red.
* M6. ``objects_store._apply_write``: set ``mark.before`` to the data before
  each update, so each write counts alone.
  ``test_r8_a_batch_judges_the_net_move`` goes red.
* M7. ``studio_rules.with_initial``: answer its input.
  ``test_r8_a_create_takes_the_initial_state`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_rules as sr
from acb_common.db import tenant_session
from acb_common.studio_rules import ConstraintError, MoveViolation
from gateway.studio import objects_store as store
from gateway.studio.objects_store import BatchError, Write
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc6.test"

_FIELDS: list[dict[str, Any]] = [
    {"key": "status", "type": "select", "options": ["draft", "approved", "sent", "void", "archived"]},
    {"key": "total", "type": "number"},
    {"key": "note", "type": "text"},
]
_FLOW: dict[str, Any] = {
    "name": "po_flow", "kind": "state_machine", "field": "status", "initial": "draft",
    "moves": [
        {"from": "draft", "to": "approved"},
        {"from": "draft", "to": "sent"},
        {"from": "approved", "to": "sent",
         "guard": [{"field": "total", "op": "lte", "value": 1000}]},
        {"from": "approved", "to": "void"},
    ],
}
#: No direct draft -> sent. A batch of draft, approved, sent is refused as a net move.
_STEPS: dict[str, Any] = {
    "name": "po_flow", "kind": "state_machine", "field": "status", "initial": "draft",
    "moves": [{"from": "draft", "to": "approved"}, {"from": "approved", "to": "sent"}],
}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc6', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc6", "name": "stuc6", "purpose": "The C6 R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    """Two packages: one of org A and one of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc6-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=ids["a"], pkg_b=ids["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc6-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _po(org: str, pkg: str, rules: list[dict[str, Any]] | None = None,
              name: str = "po") -> store.StudioObject:
    """An object with its rules. The default rule is the PO flow."""
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name, fields=_FIELDS)
        assert made is not None
        done = await store.set_constraints(db, made.id, [_FLOW] if rules is None else rules,
                                           package_id=pkg)
    assert done is not None
    return done


async def _put(org: str, obj: store.StudioObject, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=obj.package_id, writes=list(writes))


async def _refused(org: str, obj: store.StudioObject, *writes: Write) -> BatchError:
    with pytest.raises(BatchError) as caught:
        await _put(org, obj, *writes)
    return caught.value


async def _make(org: str, obj: store.StudioObject, **data: Any) -> store.Record:
    return (await _put(org, obj, Write("create", obj.id, data=data)))[0]


def _set(obj: store.StudioObject, rid: str, **data: Any) -> Write:
    return Write("update", obj.id, rid, data)


def _rows(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


def _status(world: Any, rid: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT data ->> 'status' FROM studio_records "
                              "WHERE id = CAST(:r AS uuid)"), {"r": rid}).scalar_one()


# ── The declaration ─────────────────────────────────────────────────────────


def test_r8_a_machine_is_stored_in_the_constraints_and_reads_back_equal(world) -> None:
    async def go() -> store.StudioObject:
        return await _po(world.a, world.pkg_a)

    obj = _go(world, go)
    assert [r.stored() for r in obj.constraints] == [_FLOW]
    assert obj.constraints[0].kind == sr.STATE_MACHINE


def test_r8_a_role_guard_is_refused_at_declaration(world) -> None:
    async def go() -> str:
        flow = {**_FLOW, "moves": [{"from": "draft", "to": "sent", "role": "director"}]}
        with pytest.raises(ConstraintError) as bad:
            await _po(world.a, world.pkg_a, [flow])
        return str(bad.value)

    assert "C8" in _go(world, go)


def test_r8_a_machine_that_does_not_fit_the_fields_is_refused(world) -> None:
    async def go() -> str:
        flow = {**_FLOW, "initial": "ghost"}
        with pytest.raises(ConstraintError) as bad:
            await _po(world.a, world.pkg_a, [flow])
        return str(bad.value)

    assert "not an option" in _go(world, go)


# ── Create ──────────────────────────────────────────────────────────────────


def test_r8_a_create_takes_the_initial_state(world) -> None:
    async def go() -> store.Record:
        obj = await _po(world.a, world.pkg_a)
        return await _make(world.a, obj, total=5)

    made = _go(world, go)
    assert made.data["status"] == "draft"


def test_r8_a_create_in_another_state_is_refused(world) -> None:
    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        return obj, await _refused(world.a, obj, Write("create", obj.id, data={"status": "sent"}))

    obj, refused = _go(world, go)
    assert isinstance(refused.cause, MoveViolation)
    assert refused.cause.rule == "po_flow" and refused.cause.object == "po"
    assert refused.cause.to_state == "sent"
    assert _rows(world, obj) == 0


def test_r8_a_create_in_the_initial_state_passes(world) -> None:
    async def go() -> store.Record:
        obj = await _po(world.a, world.pkg_a)
        return await _make(world.a, obj, status="draft")

    assert _go(world, go).data["status"] == "draft"


# ── Update ──────────────────────────────────────────────────────────────────


def test_r8_a_legal_move_passes(world) -> None:
    async def go() -> tuple[store.Record, Any]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        moved = (await _put(world.a, obj, _set(obj, made.id, status="approved")))[0]
        return made, moved

    made, moved = _go(world, go)
    assert moved.data["status"] == "approved" and _status(world, made.id) == "approved"


def test_r8_an_illegal_move_is_refused_and_the_row_stays(world) -> None:
    async def go() -> tuple[store.Record, BatchError, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        await _put(world.a, obj, _set(obj, made.id, status="sent"))
        back = await _refused(world.a, obj, _set(obj, made.id, status="draft"))
        # draft -> void is two declared states and no declared move.
        other = await _make(world.a, obj)
        skip = await _refused(world.a, obj, _set(obj, other.id, status="void"))
        return made, back, skip

    made, back, skip = _go(world, go)
    assert isinstance(back.cause, MoveViolation)
    assert (back.cause.from_state, back.cause.to_state) == ("sent", "draft")
    assert isinstance(skip.cause, MoveViolation) and skip.cause.to_state == "void"
    assert _status(world, made.id) == "sent"


def test_r8_a_guard_that_holds_passes_and_one_that_fails_refuses(world) -> None:
    async def go() -> tuple[store.Record, store.Record, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        cheap = await _make(world.a, obj, total=900)
        dear = await _make(world.a, obj, total=5000)
        await _put(world.a, obj, _set(obj, cheap.id, status="approved"),
                   _set(obj, dear.id, status="approved"))
        await _put(world.a, obj, _set(obj, cheap.id, status="sent"))
        return cheap, dear, await _refused(world.a, obj, _set(obj, dear.id, status="sent"))

    cheap, dear, refused = _go(world, go)
    assert _status(world, cheap.id) == "sent" and _status(world, dear.id) == "approved"
    assert isinstance(refused.cause, MoveViolation)
    assert "5000" not in str(refused) and "1000" not in str(refused), "no guard value"


def test_r8_the_guard_reads_the_merged_record(world) -> None:
    """One update moves the state and the total. The guard reads the total after
    the change, so a high total lowered in the same write passes."""
    async def go() -> tuple[store.Record, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj, total=5000)
        await _put(world.a, obj, _set(obj, made.id, status="approved"))
        refused = await _refused(world.a, obj, _set(obj, made.id, status="sent", total=2000))
        await _put(world.a, obj, _set(obj, made.id, status="sent", total=500))
        return made, refused

    made, refused = _go(world, go)
    assert isinstance(refused.cause, MoveViolation)
    assert _status(world, made.id) == "sent"


def test_r8_an_unchanged_state_passes_with_other_changes(world) -> None:
    async def go() -> store.Record:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        await _put(world.a, obj, _set(obj, made.id, status="approved"))
        return (await _put(world.a, obj, _set(obj, made.id, note="n", total=3)))[0]

    assert _go(world, go).data["status"] == "approved"


def test_r8_a_cleared_state_is_refused(world) -> None:
    async def go() -> tuple[store.Record, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        return made, await _refused(world.a, obj, _set(obj, made.id, status=None))

    made, refused = _go(world, go)
    assert isinstance(refused.cause, MoveViolation) and "cleared" in str(refused.cause)
    assert _status(world, made.id) == "draft"


def test_r8_a_stored_state_that_is_not_declared_is_refused(world) -> None:
    """Raw SQL puts a state in the row. A move from it fails closed, and a
    change that leaves the state alone still passes."""
    async def go() -> tuple[store.Record, store.StudioObject]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        return made, obj

    made, obj = _go(world, go)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_records SET data = jsonb_set(data, '{status}', '\"archived\"') "
                       "WHERE id = CAST(:r AS uuid)"), {"r": made.id})

    async def again() -> BatchError:
        await _put(world.a, obj, _set(obj, made.id, note="still fine"))
        return await _refused(world.a, obj, _set(obj, made.id, status="approved"))

    refused = _go(world, again)
    assert isinstance(refused.cause, MoveViolation) and refused.cause.from_state is None
    assert "archived" not in str(refused)
    assert _status(world, made.id) == "archived"


def test_r8_update_record_runs_the_machine(world) -> None:
    """A single call is a batch of one, so it cannot skip the machine."""
    async def go() -> tuple[MoveViolation, Any]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        async with tenant_session(world.a) as db:
            with pytest.raises(MoveViolation) as bad:
                await store.update_record(db, ANA, obj.id, made.id, {"status": "void"},
                                          package_id=obj.package_id)
            ok = await store.update_record(db, ANA, obj.id, made.id, {"status": "approved"},
                                           package_id=obj.package_id)
        return bad.value, ok

    bad, ok = _go(world, go)
    assert bad.rule == "po_flow" and ok is not None


# ── The batch ───────────────────────────────────────────────────────────────


def test_r8_a_batch_judges_the_net_move(world) -> None:
    """With the PO flow, approved -> sent needs total <= 1000, and draft -> sent
    needs nothing. A batch of draft, approved, sent counts as draft -> sent, so
    a total of 5000 passes. With the steps flow there is no draft -> sent, so
    the same batch is refused though each hop is legal."""
    async def go() -> tuple[store.Record, store.Record, BatchError]:
        flow = await _po(world.a, world.pkg_a, name="flow")
        made = await _make(world.a, flow, total=5000)
        await _put(world.a, flow, _set(flow, made.id, status="approved"),
                   _set(flow, made.id, status="sent"))
        steps = await _po(world.a, world.pkg_a, [_STEPS], name="steps")
        other = await _make(world.a, steps)
        refused = await _refused(world.a, steps, _set(steps, other.id, status="approved"),
                                 _set(steps, other.id, status="sent"))
        return made, other, refused

    made, other, refused = _go(world, go)
    assert _status(world, made.id) == "sent"
    assert isinstance(refused.cause, MoveViolation)
    assert (refused.cause.from_state, refused.cause.to_state) == ("draft", "sent")
    assert _status(world, other.id) == "draft"


def test_r8_a_batch_that_returns_to_the_start_passes(world) -> None:
    """Net move: none. draft -> approved -> draft is the same state at the end."""
    async def go() -> store.Record:
        obj = await _po(world.a, world.pkg_a, [{**_STEPS, "moves": [
            *_STEPS["moves"], {"from": "approved", "to": "draft"}]}])
        made = await _make(world.a, obj)
        return (await _put(world.a, obj, _set(obj, made.id, status="approved"),
                           _set(obj, made.id, status="draft")))[1]

    assert _go(world, go).data["status"] == "draft"


def test_r8_a_mixed_batch_that_breaks_the_machine_writes_nothing(world) -> None:
    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        async with tenant_session(world.a) as db:
            await store.create_record(db, ANA, obj.id, {"note": "outer"}, package_id=obj.package_id)
            with pytest.raises(BatchError) as bad:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    Write("create", obj.id, data={"note": "inside"}),
                    _set(obj, made.id, status="void")])
            more = await store.create_record(db, ANA, obj.id, {"note": "after"},
                                             package_id=obj.package_id)
        assert more is not None
        return obj, bad.value

    obj, bad = _go(world, go)
    assert bad.index == 1 and isinstance(bad.cause, MoveViolation)
    assert _rows(world, obj) == 3, "the first record, the outer row and the row after"


def test_r8_a_delete_is_not_a_move(world) -> None:
    async def go() -> store.StudioObject:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        await _put(world.a, obj, _set(obj, made.id, status="sent"))
        await _put(world.a, obj, Write("delete", obj.id, made.id))
        return obj

    assert _rows(world, _go(world, go)) == 0


# ── The machine and the checks around it ───────────────────────────────────


def test_r8_a_machine_queues_no_key_check(world, monkeypatch) -> None:
    """`touched_keys` answers no key for a machine. Without that, the batch
    would read the hot rows of an empty key."""
    calls: list[Any] = []

    async def spy(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    async def go() -> None:
        obj = await _po(world.a, world.pkg_a)
        made = await _make(world.a, obj)
        monkeypatch.setattr(store, "_check_key", spy)
        await _put(world.a, obj, _set(obj, made.id, status="approved"))
        await _put(world.a, obj, Write("delete", obj.id, made.id))

    _go(world, go)
    assert calls == []


def test_r8_a_machine_runs_beside_a_range_rule(world) -> None:
    async def go() -> tuple[BatchError, BatchError]:
        rng = {"name": "total_ok", "kind": "range", "field": "total", "min": 0}
        obj = await _po(world.a, world.pkg_a, [_FLOW, rng])
        made = await _make(world.a, obj, total=1)
        low = await _refused(world.a, obj, _set(obj, made.id, total=-1))
        move = await _refused(world.a, obj, _set(obj, made.id, status="void"))
        return low, move

    low, move = _go(world, go)
    assert isinstance(low.cause, sr.ConstraintViolation) and isinstance(move.cause, MoveViolation)


def test_r8_a_stored_machine_that_the_parser_cannot_read_refuses_each_write(world) -> None:
    """Only raw SQL can store a machine with a role. Each write is refused until
    `set_constraints` replaces the list."""
    async def go() -> store.StudioObject:
        return await _po(world.a, world.pkg_a)

    obj = _go(world, go)
    bad = [{**_FLOW, "moves": [{"from": "draft", "to": "sent", "role": "director"}]}]
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_objects SET constraints = CAST(:r AS jsonb) "
                       "WHERE id = CAST(:o AS uuid)"), {"r": json.dumps(bad), "o": obj.id})

    async def again() -> BatchError:
        return await _refused(world.a, obj, Write("create", obj.id, data={"status": "draft"}))

    refused = _go(world, again)
    assert isinstance(refused.cause, sr.RuleViolation)
    assert refused.cause.rule == "po_flow" and "Declare the rules" in str(refused.cause)


# ── Isolation ───────────────────────────────────────────────────────────────


def test_r8_org_b_is_not_bound_by_the_machine_of_org_a(world) -> None:
    """The same object name in org B holds no machine, so it takes any state. Org
    B cannot declare rules on the object of org A, and it reads none of its
    records."""
    async def go() -> tuple[store.Record, store.Record, Any, Any, BatchError]:
        a = await _po(world.a, world.pkg_a)
        b_obj = await _po(world.b, world.pkg_b, [])
        free = await _make(world.b, b_obj, status="sent")
        in_a = await _make(world.a, a)
        async with tenant_session(world.b) as db:
            seen = await store.get_record(db, a.id, in_a.id, package_id=world.pkg_a)
            declared = await store.set_constraints(db, a.id, [], package_id=world.pkg_a)
        refused = await _refused(world.a, a, Write("create", a.id, data={"status": "sent"}))
        return free, in_a, seen, declared, refused

    free, _, seen, declared, refused = _go(world, go)
    assert free.data["status"] == "sent"
    assert seen is None and declared is None
    assert isinstance(refused.cause, MoveViolation)


def test_r8_a_checkbox_guard_passes_for_a_box_never_ticked(world) -> None:
    """A form sends no value for an unticked box. `eq false` matches it, as C2a does."""
    async def go() -> tuple[store.Record, store.Record]:
        fields = [*_FIELDS, {"key": "urgent", "type": "checkbox"}]
        flow = {**_FLOW, "moves": [{"from": "draft", "to": "approved", "guard": [
            {"field": "urgent", "op": "eq", "value": False}]}]}
        async with tenant_session(world.a) as db:
            made = await store.define_object(db, ANA, package_id=world.pkg_a, name="chk",
                                             fields=fields)
            assert made is not None
            obj = await store.set_constraints(db, made.id, [flow], package_id=world.pkg_a)
        assert obj is not None
        never = await _make(world.a, obj)
        ticked = await _make(world.a, obj, urgent=True)
        await _put(world.a, obj, _set(obj, never.id, status="approved"))
        refused = await _refused(world.a, obj, _set(obj, ticked.id, status="approved"))
        assert isinstance(refused.cause, MoveViolation)
        return never, ticked

    never, _ = _go(world, go)
    assert _status(world, never.id) == "approved"


def test_r8_a_create_with_a_null_state_takes_the_initial_state(world) -> None:
    async def go() -> store.Record:
        obj = await _po(world.a, world.pkg_a)
        return await _make(world.a, obj, status=None)

    assert _go(world, go).data["status"] == "draft"
