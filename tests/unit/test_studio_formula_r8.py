"""WS-STU STU-C7a: formula fields on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 3.7 (layer 2) and the section 14
C7a row. Code: ``acb_common/studio_expr.py`` and ``studio_rules.py`` (the pure
half, in ``test_studio_expr.py`` and ``test_studio_rules.py``) and the batch
section of ``gateway/studio/objects_store.py`` (``_lock_objects``,
``_apply_write`` and ``set_constraints``).

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_formula_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (C7a).** A create stores the result, and an update recomputes it
from the merged record. A write of a target is refused, also with the same
value. A tie rounds as the named mode, positive and negative. A zero divisor
refuses the batch with 0 rows written and the session usable. A forged stored
formula refuses each write. A hot target filters and sorts. A declaration on an
object with records is refused. A ``range``, a ``unique``, a ``group_sum_min``
and a state-machine guard read the stored result. Org B sees no formula of org A.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``studio_rules.refuse_computed_keys``: answer None.
  ``test_r8_a_write_of_a_target_is_refused_also_with_the_same_value`` goes red.
* M2. ``studio_expr._round_ratio``: ``half_up`` becomes ``twice > den``.
  ``test_r8_a_money_tie_rounds_as_the_named_mode`` goes red.
* M3. ``studio_expr._divide``: skip the rounding at the node.
  ``test_r8_a_tie_rounds_at_the_node_and_at_the_target`` goes red.
* M4. ``objects_store._apply_write``: on an update, read ``mark.before`` in place of
  the merged record. ``test_r8_an_update_recomputes_from_the_merged_record`` goes red.
* M5. ``objects_store._apply_write``: run the formulas on the raw data, before
  ``validate_record``. ``test_r8_the_formula_reads_the_checked_record`` goes red.
* M6. ``studio_expr._apply``: drop the null rule.
  ``test_r8_a_null_input_clears_the_target`` goes red.
* M7. ``studio_rules.touched_keys``: take ``FORMULA`` out of the early return.
  ``test_r8_a_formula_queues_no_key_check`` goes red.
* M8. ``objects_store.set_constraints``: skip ``_check_formulas_first``.
  ``test_r8_a_new_formula_on_an_object_with_records_is_refused`` goes red.
* M9. ``studio_expr._Run._if``: run both branches.
  ``test_r8_if_runs_one_branch_only`` goes red.
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
from acb_common.studio_rules import (
    ConstraintError,
    ConstraintViolation,
    FormulaError,
    MoveViolation,
)
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
ANA = f"ana.{TAG}@stuc7a.test"

_FIELDS: list[dict[str, Any]] = [
    {"key": "qty", "type": "number"},
    {"key": "price", "type": "number"},
    {"key": "total", "type": "number", "hot": True},
    {"key": "fee", "type": "money"},
    {"key": "tax", "type": "money", "hot": True},
    {"key": "mail", "type": "email"},
    {"key": "full", "type": "text"},
    {"key": "tag", "type": "text", "hot": True},
    {"key": "status", "type": "select", "options": ["draft", "approved"]},
    {"key": "need", "type": "text", "required": True},
]
_TOTAL: dict[str, Any] = {
    "name": "line_total", "kind": "formula", "target": "total", "rounding": "half_up",
    "scale": 2, "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]},
}
_TAX: dict[str, Any] = {
    "name": "tax_of", "kind": "formula", "target": "tax", "currency": "INR",
    "expr": {"fn": "mul", "args": [{"ref": "fee"}, {"num": "0.18"}], "rounding": "half_up"},
}
_FULL: dict[str, Any] = {
    "name": "shout", "kind": "formula", "target": "full",
    "expr": {"fn": "concat", "args": [{"ref": "mail"}, {"text": "!"}]},
}
_RATIO: dict[str, Any] = {
    "name": "ratio", "kind": "formula", "target": "total", "rounding": "half_up", "scale": 2,
    "expr": {"fn": "div", "args": [{"ref": "qty"}, {"ref": "price"}],
             "rounding": "half_up", "places": 4},
}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc7a', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc7a", "name": "stuc7a", "purpose": "The C7a R8 suite."})


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
                "s": f"stuc7a-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=ids["a"], pkg_b=ids["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc7a-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _line(org: str, pkg: str, rules: list[dict[str, Any]] | None = None,
                name: str = "line", fields: list[dict[str, Any]] | None = None
                ) -> store.StudioObject:
    """An object with its rules. The default rule is the line total."""
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name,
                                         fields=_FIELDS if fields is None else fields)
        assert made is not None
        done = await store.set_constraints(db, made.id, [_TOTAL] if rules is None else rules,
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
    data.setdefault("need", "x")
    return (await _put(org, obj, Write("create", obj.id, data=data)))[0]


def _set(obj: store.StudioObject, rid: str, **data: Any) -> Write:
    return Write("update", obj.id, rid, data)


def _rows(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


def _data(world: Any, rid: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        raw = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                        {"r": rid}).scalar_one()
    return dict(raw if isinstance(raw, dict) else json.loads(raw))


def _hot_num(world: Any, rid: str, key: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT v_num FROM studio_record_hot WHERE record_id = "
                              "CAST(:r AS uuid) AND field_key = :k"), {"r": rid, "k": key}).scalar()


# -- the declaration ------------------------------------------------------------


def test_r8_a_formula_is_stored_in_the_constraints_and_reads_back_equal(world) -> None:
    async def go() -> store.StudioObject:
        return await _line(world.a, world.pkg_a, [_TOTAL, _TAX])

    obj = _go(world, go)
    assert [r.stored() for r in obj.constraints] == [_TOTAL, _TAX]
    assert obj.constraints[0].kind == sr.FORMULA and obj.computed == ("total", "tax")


def test_r8_a_formula_that_does_not_fit_the_fields_is_refused(world) -> None:
    async def go() -> list[str]:
        out = []
        for bad in ({**_TOTAL, "expr": {"ref": "ghost"}}, {**_TOTAL, "target": "need"},
                    {**_TOTAL, "target": "status"}, {**_TOTAL, "target": "ghost"},
                    {**_TOTAL, "expr": {"ref": "total"}}):
            with pytest.raises(ConstraintError) as refused:
                await _line(world.a, world.pkg_a, [bad], name=f"o{len(out)}")
            out.append(str(refused.value))
        return out

    messages = _go(world, go)
    assert "ghost" in messages[0] and "required" in messages[1]
    assert "must be one number" in messages[2] and "is not a field" in messages[3]
    assert "own target" in messages[4]


def test_r8_a_new_formula_on_an_object_with_records_is_refused(world) -> None:
    async def go() -> tuple[str, str, store.StudioObject]:
        obj = await _line(world.a, world.pkg_a, [])
        made = await _make(world.a, obj, qty=2, price=3)
        async with tenant_session(world.a) as db:
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, obj.id, [_TOTAL], package_id=world.pkg_a)
            after = await store.get_object(db, obj.id)
        assert after is not None and after.constraints == ()
        return str(refused.value), made.id, obj

    message, rid, obj = _go(world, go)
    assert "declare before the first record" in message and "`line_total`" in message
    assert _data(world, rid) == {"qty": 2, "price": 3, "need": "x"}
    assert _rows(world, obj) == 1


def test_r8_an_unchanged_formula_and_a_removed_one_pass_on_an_object_with_records(world) -> None:
    """The same formula stays. A removed one leaves its stored values as plain values,
    so a write may then hold the key."""
    async def go() -> tuple[store.Record, store.Record]:
        obj = await _line(world.a, world.pkg_a, [_TOTAL])
        made = await _make(world.a, obj, qty=2, price=3)
        async with tenant_session(world.a) as db:
            same = await store.set_constraints(db, obj.id, [_TOTAL], package_id=world.pkg_a)
            assert same is not None and same.computed == ("total",)
            changed = {**_TOTAL, "scale": 3}
            with pytest.raises(ConstraintError):
                await store.set_constraints(db, obj.id, [changed], package_id=world.pkg_a)
            gone = await store.set_constraints(db, obj.id, [], package_id=world.pkg_a)
            assert gone is not None and gone.computed == ()
        manual = (await _put(world.a, obj, _set(obj, made.id, total=99)))[0]
        return made, manual

    kept, manual = _go(world, go)
    assert kept.data["total"] == 6 and manual.data["total"] == 99
    assert _data(world, manual.id)["total"] == 99


def test_r8_a_stored_total_is_not_a_declaration_of_another_object(world) -> None:
    """A formula binds its own object. A sibling object in the package takes a plain key."""
    async def go() -> store.Record:
        await _line(world.a, world.pkg_a, [_TOTAL], name="priced")
        plain = await _line(world.a, world.pkg_a, [], name="plain")
        return await _make(world.a, plain, qty=2, price=3, total=500)

    assert _go(world, go).data["total"] == 500


# -- a create and an update -------------------------------------------------------


def test_r8_a_create_computes_and_stores_the_result(world) -> None:
    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a)
        return await _make(world.a, obj, qty=3, price=2.5)

    made = _go(world, go)
    assert made.data["total"] == 7.5
    assert _data(world, made.id)["total"] == 7.5
    assert float(_hot_num(world, made.id, "total")) == 7.5


def test_r8_an_update_recomputes_from_the_merged_record(world) -> None:
    """The update names only `qty`. The price comes from the stored record."""
    async def go() -> tuple[store.Record, store.Record]:
        obj = await _line(world.a, world.pkg_a)
        made = await _make(world.a, obj, qty=3, price=2.5)
        moved = (await _put(world.a, obj, _set(obj, made.id, qty=4)))[0]
        return made, moved

    made, moved = _go(world, go)
    assert moved.data["total"] == 10 and _data(world, made.id)["total"] == 10
    assert float(_hot_num(world, made.id, "total")) == 10.0


def test_r8_a_batch_recomputes_each_write_in_order(world) -> None:
    async def go() -> list[Any]:
        obj = await _line(world.a, world.pkg_a)
        made = await _make(world.a, obj, qty=1, price=10)
        return await _put(world.a, obj, _set(obj, made.id, qty=2), _set(obj, made.id, price=7),
                          Write("create", obj.id, data={"need": "y", "qty": 5, "price": 5}))

    first, second, third = _go(world, go)
    assert first.data["total"] == 20 and second.data["total"] == 14 and third.data["total"] == 25


def test_r8_a_null_input_clears_the_target(world) -> None:
    async def go() -> tuple[store.Record, store.Record]:
        obj = await _line(world.a, world.pkg_a)
        made = await _make(world.a, obj, qty=3, price=2)
        cleared = (await _put(world.a, obj, _set(obj, made.id, price=None)))[0]
        return made, cleared

    made, cleared = _go(world, go)
    assert "total" not in cleared.data and "total" not in _data(world, made.id)
    assert _hot_num(world, made.id, "total") is None


def test_r8_a_stored_number_is_exact_at_its_scale(world) -> None:
    async def go() -> list[store.Record]:
        obj = await _line(world.a, world.pkg_a, [{**_TOTAL, "scale": 4}])
        out = []
        for qty, price in ((0.0001, 1), (12.3456, 1), (-0.0001, 1), (3, 0.3333)):
            out.append(await _make(world.a, obj, qty=qty, price=price))
        return out

    made = _go(world, go)
    assert [m.data["total"] for m in made] == [0.0001, 12.3456, -0.0001, 0.9999]
    with world.p.admin_engine.connect() as c:
        texts = [c.execute(text("SELECT data ->> 'total' FROM studio_records "
                                "WHERE id = CAST(:r AS uuid)"), {"r": m.id}).scalar_one()
                 for m in made]
    assert texts == ["0.0001", "12.3456", "-0.0001", "0.9999"]


def test_r8_a_result_past_15_digits_at_its_scale_is_refused(world) -> None:
    async def go() -> tuple[BatchError, store.Record]:
        obj = await _line(world.a, world.pkg_a)
        too_big = await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "qty": 10**7, "price": 10**7}))
        fits = await _make(world.a, obj, qty=10**6, price=10**6)
        return too_big, fits

    too_big, fits = _go(world, go)
    assert isinstance(too_big.cause, FormulaError) and too_big.cause.code == "result_too_large"
    assert fits.data["total"] == 10**12


# -- a write of a target -----------------------------------------------------------


@pytest.mark.parametrize("value", [None, 7.5, 99, "x"])
def test_r8_a_write_of_a_target_is_refused_also_with_the_same_value(world, value: Any) -> None:
    async def go() -> tuple[store.StudioObject, store.Record, BatchError, BatchError]:
        obj = await _line(world.a, world.pkg_a)
        made = await _make(world.a, obj, qty=3, price=2.5)
        on_create = await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "qty": 1, "total": value}))
        on_update = await _refused(world.a, obj, _set(obj, made.id, total=value))
        return obj, made, on_create, on_update

    obj, made, on_create, on_update = _go(world, go)
    for refused, index in ((on_create, 0), (on_update, 0)):
        assert isinstance(refused.cause, FormulaError) and refused.index == index
        assert refused.cause.code == "computed_field" and refused.cause.rule == "line_total"
        assert refused.cause.object == "line" and "`total` is computed" in str(refused.cause)
    assert _rows(world, obj) == 1 and _data(world, made.id)["total"] == 7.5


def test_r8_a_target_in_a_second_write_leaves_the_first_unwritten(world) -> None:
    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _line(world.a, world.pkg_a)
        refused = await _refused(
            world.a, obj,
            Write("create", obj.id, data={"need": "x", "qty": 1, "price": 1}),
            Write("create", obj.id, data={"need": "x", "total": 3}))
        return obj, refused

    obj, refused = _go(world, go)
    assert refused.index == 1 and _rows(world, obj) == 0


# -- a zero divisor and a failed formula ----------------------------------------------


def test_r8_a_zero_divisor_refuses_the_batch_and_the_session_stays_usable(world) -> None:
    async def go() -> tuple[store.StudioObject, BatchError, Any, Any]:
        obj = await _line(world.a, world.pkg_a, [_RATIO])
        async with tenant_session(world.a) as db:
            first = await store.create_record(db, ANA, obj.id, {"need": "o", "qty": 1, "price": 4},
                                              package_id=obj.package_id)
            with pytest.raises(BatchError) as failed:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    Write("create", obj.id, data={"need": "a", "qty": 1, "price": 2}),
                    Write("create", obj.id, data={"need": "b", "qty": 1, "price": 0})])
            kept = await store.get_record(db, obj.id, first.id, package_id=obj.package_id)
            after = await store.create_record(db, ANA, obj.id, {"need": "z", "qty": 3, "price": 4},
                                              package_id=obj.package_id)
        return obj, failed.value, kept, after

    obj, failed, kept, after = _go(world, go)
    cause = failed.cause
    assert isinstance(cause, FormulaError) and failed.index == 1
    assert (cause.rule, cause.object, cause.code) == ("ratio", "line", "division_by_zero")
    assert kept is not None and kept.data["total"] == 0.25 and after is not None
    assert after.data["total"] == 0.75
    assert _rows(world, obj) == 2, "the outer row and the row after, and none of the batch"


def test_r8_an_update_to_a_zero_divisor_leaves_the_old_result(world) -> None:
    async def go() -> tuple[store.Record, BatchError]:
        obj = await _line(world.a, world.pkg_a, [_RATIO])
        made = await _make(world.a, obj, qty=1, price=4)
        refused = await _refused(world.a, obj, _set(obj, made.id, price=0))
        return made, refused

    made, refused = _go(world, go)
    assert isinstance(refused.cause, FormulaError)
    assert "`0`" not in str(refused.cause)
    assert _data(world, made.id)["total"] == 0.25 and _data(world, made.id)["price"] == 4


def test_r8_if_runs_one_branch_only(world) -> None:
    guarded = {**_RATIO, "expr": {"fn": "if", "args": [
        {"fn": "eq", "args": [{"ref": "price"}, {"num": "0"}]}, {"num": "0"},
        {"fn": "div", "args": [{"ref": "qty"}, {"ref": "price"}],
         "rounding": "half_up", "places": 4}]}}

    async def go() -> tuple[store.Record, store.Record]:
        obj = await _line(world.a, world.pkg_a, [guarded])
        return (await _make(world.a, obj, qty=1, price=0),
                await _make(world.a, obj, qty=1, price=8))

    zero, eighth = _go(world, go)
    assert zero.data["total"] == 0 and eighth.data["total"] == 0.13


# -- rounding ------------------------------------------------------------------------


@pytest.mark.parametrize("mode,fee,minor", [
    ("half_up", 100, 3), ("half_up", -100, -3), ("half_even", 100, 2), ("half_even", -100, -2),
    ("half_even", 300, 8), ("down", 100, 2), ("down", -100, -2), ("up", 100, 3),
    ("up", -100, -3),
])
def test_r8_a_money_tie_rounds_as_the_named_mode(world, mode: str, fee: int, minor: int) -> None:
    """2.5 minor units, a tie. 300 gives 7.5, and half_even takes the even 8."""
    rule = {**_TAX, "expr": {"fn": "mul", "args": [{"ref": "fee"}, {"num": "0.025"}],
                             "rounding": mode}}

    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [rule], name=f"tie_{mode}")
        return await _make(world.a, obj, fee={"minor": fee, "currency": "INR"})

    made = _go(world, go)
    assert made.data["tax"] == {"minor": minor, "currency": "INR"}


@pytest.mark.parametrize("mode,qty,expected", [
    ("half_up", 2.5, 3), ("half_up", -2.5, -3), ("half_even", 2.5, 2), ("half_even", -2.5, -2),
    ("down", 2.5, 2), ("down", -2.5, -2), ("up", 2.5, 3), ("up", -2.5, -3),
])
def test_r8_a_tie_rounds_at_the_node_and_at_the_target(world, mode: str, qty: float,
                                                      expected: int) -> None:
    """The node rounds the tie as *mode*. The target then rounds a whole number, so a
    node that skipped its rounding would leave the tie to the target (half_up)."""
    node = {**_TOTAL, "scale": 0, "rounding": "half_up", "expr": {
        "fn": "div", "args": [{"ref": "qty"}, {"num": "1"}], "rounding": mode, "places": 0}}

    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [node], name=f"node_{mode}")
        return await _make(world.a, obj, qty=qty)

    assert _go(world, go).data["total"] == expected


def test_r8_a_money_in_another_currency_is_refused(world) -> None:
    async def go() -> BatchError:
        obj = await _line(world.a, world.pkg_a, [_TAX])
        return await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "fee": {"minor": 100, "currency": "USD"}}))

    refused = _go(world, go)
    assert isinstance(refused.cause, FormulaError) and refused.cause.code == "currency_mismatch"


# -- the order of the steps ----------------------------------------------------------


def test_r8_the_formula_reads_the_checked_record(world) -> None:
    """`validate_record` lower-cases an email. A formula that ran on the raw data would read the capitals."""
    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [_FULL])
        return await _make(world.a, obj, mail="Ana@Example.COM")

    made = _go(world, go)
    assert made.data["mail"] == "ana@example.com" and made.data["full"] == "ana@example.com!"


def test_r8_a_required_plain_field_is_still_required(world) -> None:
    async def go() -> BatchError:
        obj = await _line(world.a, world.pkg_a)
        return await _refused(world.a, obj, Write("create", obj.id, data={"qty": 1, "price": 1}))

    refused = _go(world, go)
    assert not isinstance(refused.cause, FormulaError) and "`need` is required" in str(refused.cause)


def test_r8_formulas_run_in_order_of_dependency(world) -> None:
    chain = {"name": "label_of", "kind": "formula", "target": "full",
             "expr": {"fn": "concat", "args": [{"text": "T="}, {"ref": "tag"}]}}
    tag = {"name": "tag_of", "kind": "formula", "target": "tag",
           "expr": {"fn": "concat", "args": [{"text": "n"}, {"ref": "need"}]}}

    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [chain, tag])
        return await _make(world.a, obj, need="7")

    assert _go(world, go).data["full"] == "T=n7", "the later formula runs first"


# -- the other rules read the stored result ----------------------------------------------


def test_r8_a_range_rule_on_a_formula_target_reads_the_stored_result(world) -> None:
    cap = {"name": "cap", "kind": "range", "field": "total", "max": 10}

    async def go() -> tuple[store.StudioObject, store.Record, BatchError, BatchError]:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, cap])
        ok = await _make(world.a, obj, qty=2, price=5)
        over_create = await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "qty": 3, "price": 5}))
        over_update = await _refused(world.a, obj, _set(obj, ok.id, qty=3))
        return obj, ok, over_create, over_update

    obj, ok, over_create, over_update = _go(world, go)
    for refused in (over_create, over_update):
        assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "cap"
    assert _rows(world, obj) == 1 and _data(world, ok.id)["total"] == 10


def test_r8_a_unique_rule_on_a_formula_target_reads_the_stored_result(world) -> None:
    one = {"name": "one_total", "kind": "unique", "fields": ["total"]}

    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, one])
        await _make(world.a, obj, qty=2, price=5)
        clash = await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "qty": 5, "price": 2}))
        await _make(world.a, obj, qty=5, price=3)
        return obj, clash

    obj, clash = _go(world, go)
    assert isinstance(clash.cause, ConstraintViolation) and clash.cause.rule == "one_total"
    assert _rows(world, obj) == 2


def test_r8_a_group_sum_min_on_a_formula_target_reads_the_stored_result(world) -> None:
    floor = {"name": "tag_floor", "kind": "group_sum_min", "field": "total",
             "group_by": ["tag"], "min": 0}

    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, floor])
        await _make(world.a, obj, tag="a", qty=5, price=4)
        refused = await _refused(world.a, obj, Write("create", obj.id, data={
            "need": "x", "tag": "a", "qty": 5, "price": -5}))
        await _make(world.a, obj, tag="a", qty=2, price=-5)
        return obj, refused

    obj, refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "tag_floor"
    assert _rows(world, obj) == 2


def test_r8_a_guard_on_a_formula_target_reads_the_stored_result(world) -> None:
    flow = {"name": "po_flow", "kind": "state_machine", "field": "status", "initial": "draft",
            "moves": [{"from": "draft", "to": "approved",
                       "guard": [{"field": "total", "op": "lte", "value": 100}]}]}

    async def go() -> tuple[store.Record, store.Record, BatchError]:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, flow])
        small = await _make(world.a, obj, qty=2, price=5)
        big = await _make(world.a, obj, qty=20, price=50)
        moved = (await _put(world.a, obj, _set(obj, small.id, status="approved")))[0]
        refused = await _refused(world.a, obj, _set(obj, big.id, status="approved"))
        return moved, big, refused

    moved, big, refused = _go(world, go)
    assert moved.data["status"] == "approved" and isinstance(refused.cause, MoveViolation)
    assert _data(world, big.id)["status"] == "draft"


def test_r8_a_guard_sees_the_result_of_the_same_write(world) -> None:
    """The batch holds the move and the price change. The guard reads the new total."""
    flow = {"name": "po_flow", "kind": "state_machine", "field": "status", "initial": "draft",
            "moves": [{"from": "draft", "to": "approved",
                       "guard": [{"field": "total", "op": "lte", "value": 100}]}]}

    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, flow])
        made = await _make(world.a, obj, qty=20, price=50)
        refused = await _refused(world.a, obj, _set(obj, made.id, status="approved"))
        assert isinstance(refused.cause, MoveViolation)
        return (await _put(world.a, obj, _set(obj, made.id, price=1, status="approved")))[0]

    done = _go(world, go)
    assert done.data["status"] == "approved" and done.data["total"] == 20


def test_r8_a_formula_queues_no_key_check(world) -> None:
    """A formula adds no SQL key check: a batch of formula writes runs one statement per
    write and no count or sum."""
    async def go() -> list[str]:
        obj = await _line(world.a, world.pkg_a)
        seen: list[str] = []
        async with tenant_session(world.a) as db:
            real = db.execute

            async def spy(stmt: Any, *a: Any, **k: Any) -> Any:
                seen.append(str(stmt))
                return await real(stmt, *a, **k)

            db.execute = spy  # type: ignore[method-assign]
            await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                Write("create", obj.id, data={"need": "x", "qty": 1, "price": 1})])
        return seen

    seen = _go(world, go)
    assert not [s for s in seen if "count(" in s.lower() or "sum(" in s.lower()], seen


# -- a hot target ---------------------------------------------------------------------


def test_r8_a_hot_target_filters_and_sorts_through_the_query(world) -> None:
    async def go() -> tuple[list[Any], list[Any], list[Any]]:
        obj = await _line(world.a, world.pkg_a)
        for qty in (3, 1, 2, 5):
            await _make(world.a, obj, qty=qty, price=10)
        async with tenant_session(world.a) as db:
            def totals(page: Any) -> list[Any]:
                return [r.data["total"] for r in page.records]

            up = await store.query_records(db, package_id=obj.package_id, object_id=obj.id,
                                           sort={"field": "total", "dir": "asc"})
            down = await store.query_records(db, package_id=obj.package_id, object_id=obj.id,
                                             sort={"field": "total", "dir": "desc"})
            big = await store.query_records(
                db, package_id=obj.package_id, object_id=obj.id,
                filter=[{"field": "total", "op": "gte", "value": 25}],
                sort={"field": "total", "dir": "asc"})
        return totals(up), totals(down), totals(big)

    up, down, big = _go(world, go)
    assert up == [10, 20, 30, 50] and down == [50, 30, 20, 10] and big == [30, 50]


# -- a forged stored formula ------------------------------------------------------------


def _forge(world: Any, obj: store.StudioObject, rules: list[dict[str, Any]]) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_objects SET constraints = CAST(:r AS jsonb) "
                       "WHERE id = CAST(:o AS uuid)"), {"r": json.dumps(rules), "o": obj.id})


def test_r8_a_forged_stored_tree_over_a_cap_refuses_each_write(world) -> None:
    deep: dict[str, Any] = {"num": "1"}
    for _ in range(9):
        deep = {"fn": "neg", "args": [deep]}

    async def go() -> tuple[BatchError, BatchError, store.StudioObject]:
        obj = await _line(world.a, world.pkg_a)
        made = await _make(world.a, obj, qty=1, price=1)
        _forge(world, obj, [{**_TOTAL, "expr": deep}])
        on_create = await _refused(world.a, obj, Write("create", obj.id, data={"need": "x"}))
        on_update = await _refused(world.a, obj, _set(obj, made.id, qty=2))
        return on_create, on_update, obj

    on_create, on_update, obj = _go(world, go)
    for refused in (on_create, on_update):
        assert isinstance(refused.cause, sr.RuleViolation)
        assert refused.cause.rule == "line_total" and "Declare the rules" in str(refused.cause)
    assert _rows(world, obj) == 1


def test_r8_a_forged_stored_formula_that_reads_no_field_refuses_each_write(world) -> None:
    async def go() -> BatchError:
        obj = await _line(world.a, world.pkg_a)
        _forge(world, obj, [{**_TOTAL, "expr": {"ref": "ghost"}}])
        return await _refused(world.a, obj, Write("create", obj.id, data={"need": "x"}))

    refused = _go(world, go)
    assert isinstance(refused.cause, FormulaError) and refused.cause.code == "declaration"
    assert refused.cause.rule == "line_total" and refused.cause.object == "line"


# -- isolation ---------------------------------------------------------------------------


def test_r8_org_b_is_not_bound_by_the_formula_of_org_a(world) -> None:
    """The same object name in org B holds no formula, so it takes a plain `total`. Org B
    cannot declare a formula on the object of org A, and it reads none of its records."""
    async def go() -> tuple[store.Record, store.Record, Any, Any, BatchError]:
        a = await _line(world.a, world.pkg_a)
        b_obj = await _line(world.b, world.pkg_b, [])
        free = await _make(world.b, b_obj, qty=2, price=3, total=500)
        in_a = await _make(world.a, a, qty=2, price=3)
        async with tenant_session(world.b) as db:
            seen = await store.get_record(db, a.id, in_a.id, package_id=world.pkg_a)
            declared = await store.set_constraints(db, a.id, [_TAX], package_id=world.pkg_a)
        refused = await _refused(world.a, a, Write("create", a.id, data={
            "need": "x", "total": 500}))
        return free, in_a, seen, declared, refused

    free, in_a, seen, declared, refused = _go(world, go)
    assert free.data["total"] == 500 and in_a.data["total"] == 6
    assert seen is None and declared is None
    assert isinstance(refused.cause, FormulaError)


def test_r8_the_declared_formula_is_in_one_statement_with_the_record(world) -> None:
    """One write holds the record and its hot rows in one statement, so the stored
    result and the hot row cannot drift."""
    async def go() -> store.Record:
        obj = await _line(world.a, world.pkg_a, [_TOTAL, _TAX])
        return await _make(world.a, obj, qty=2, price=3, fee={"minor": 1000, "currency": "INR"})

    made = _go(world, go)
    assert made.data["total"] == 6 and made.data["tax"] == {"minor": 180, "currency": "INR"}
    assert float(_hot_num(world, made.id, "total")) == 6.0
    assert float(_hot_num(world, made.id, "tax")) == 180.0
