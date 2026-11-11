"""WS-STU STU-C19: lookup tables on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row C19. Code: the
section "the lookup tables (C19)" of ``acb_common/studio_rules.py`` (the pure
half is ``test_studio_lookup.py``) and of ``gateway/studio/objects_store.py``
(``_fill_lookups``, ``build_lookup`` and ``_check_no_lookup_reads``).

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_lookup_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (C19).** A freight quote reads the rate of its lane and weight on
the quote date. A missing key gives a clear error, never zero. A formula reads
the rate. Org B never reads a row of org A.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``studio_rules.lookup_result``: answer 0 minor units for no row.
  ``test_r8_a_missing_key_is_a_clear_error_and_never_zero`` goes red.
* M2. ``objects_store.build_lookup``: drop ``DESC``.
  ``test_r8_a_freight_quote_reads_the_rate_of_its_lane_and_weight_on_its_date`` goes red.
* M3. ``objects_store._fill_lookups``: skip the ``lookup_stale`` test.
  ``test_r8_an_update_keeps_the_quoted_rate_until_a_key_or_the_date_changes`` goes red.
* M4. ``objects_store.set_field_hot``: skip ``_check_no_lookup_reads``.
  ``test_r8_a_key_of_the_table_cannot_turn_cold`` goes red.
* M5. ``objects_store._apply_write``: drop ``batch.lookups.forget(oid)`` after
  the create. ``test_r8_a_kept_read_is_dropped_when_the_batch_writes_the_source``
  goes red.
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
from acb_common.studio_rules import ConstraintError, FormulaError, LookupRefused
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
ANA = f"ana.{TAG}@stuc19.test"

_CARD_FIELDS: list[dict[str, Any]] = [
    {"key": "lane", "type": "text", "hot": True},
    {"key": "slab", "type": "number", "hot": True},
    {"key": "starts", "type": "date", "hot": True},
    {"key": "rate", "type": "money"},
]
_QUOTE_FIELDS: list[dict[str, Any]] = [
    {"key": "lane", "type": "text"},
    {"key": "slab", "type": "number"},
    {"key": "on", "type": "date"},
    {"key": "kg", "type": "number"},
    {"key": "rate", "type": "money"},
    {"key": "freight", "type": "money"},
]
_RATE: dict[str, Any] = {
    "name": "rate_of_lane", "kind": "lookup", "target": "rate", "source": "card",
    "field": "rate", "match": [{"field": "lane", "ref": "lane"}, {"field": "slab", "ref": "slab"}],
    "as_of": "on", "valid_from": "starts",
}
_FREIGHT: dict[str, Any] = {
    "name": "freight_of", "kind": "formula", "target": "freight", "currency": "INR",
    "expr": {"fn": "mul", "args": [{"ref": "rate"}, {"ref": "kg"}], "rounding": "half_up"},
}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc19', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc19", "name": "stuc19", "purpose": "The C19 R8 suite."})


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
                "s": f"stuc19-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=ids["a"], pkg_b=ids["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc19-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _inr(minor: int) -> dict[str, Any]:
    return {"minor": minor, "currency": "INR"}


async def _object(org: str, pkg: str, name: str, fields: list[dict[str, Any]],
                  rules: list[dict[str, Any]] | None = None) -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name, fields=fields)
        assert made is not None
        if rules is None:
            return made
        done = await store.set_constraints(db, made.id, rules, package_id=pkg)
    assert done is not None
    return done


async def _put(org: str, pkg: str, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=pkg, writes=list(writes))


async def _refused(org: str, pkg: str, *writes: Write) -> BatchError:
    with pytest.raises(BatchError) as caught:
        await _put(org, pkg, *writes)
    return caught.value


def _row(card: store.StudioObject, lane: str, slab: int, starts: str, minor: int) -> Write:
    return Write("create", card.id, data={"lane": lane, "slab": slab, "starts": starts,
                                          "rate": _inr(minor)})


async def _freight(org: str, pkg: str) -> tuple[store.StudioObject, store.StudioObject]:
    """A rate card with two versions of one lane, and a quote that reads it."""
    card = await _object(org, pkg, "card", _CARD_FIELDS)
    await _put(org, pkg,
               _row(card, "BLR-DEL", 500, "2026-01-01", 1000),
               _row(card, "BLR-DEL", 500, "2026-06-01", 1200),
               _row(card, "BLR-DEL", 1000, "2026-01-01", 1800),
               _row(card, "BLR-BOM", 500, "2026-01-01", 700))
    quote = await _object(org, pkg, "quote", _QUOTE_FIELDS, [_RATE, _FREIGHT])
    return card, quote


def _quote(quote: store.StudioObject, **data: Any) -> Write:
    return Write("create", quote.id, data=data)


def _rows(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


# -- the acceptance ----------------------------------------------------------


def test_r8_a_freight_quote_reads_the_rate_of_its_lane_and_weight_on_its_date(world) -> None:
    async def go() -> list[Any]:
        _, quote = await _freight(world.a, world.pkg_a)
        return await _put(world.a, world.pkg_a,
                          _quote(quote, lane="BLR-DEL", slab=500, on="2026-03-15", kg=4),
                          _quote(quote, lane="BLR-DEL", slab=500, on="2026-06-01", kg=4),
                          _quote(quote, lane="BLR-DEL", slab=1000, on="2026-07-01", kg=2),
                          _quote(quote, lane="BLR-BOM", slab=500, on="2026-12-31", kg=3))

    made = _go(world, go)
    assert [r.data["rate"]["minor"] for r in made] == [1000, 1200, 1800, 700]
    assert [r.data["freight"]["minor"] for r in made] == [4000, 4800, 3600, 2100]


def test_r8_a_missing_key_is_a_clear_error_and_never_zero(world) -> None:
    async def go() -> tuple[list[BatchError], store.StudioObject]:
        _, quote = await _freight(world.a, world.pkg_a)
        out = []
        for data in ({"lane": "BLR-XYZ-SECRET", "slab": 500, "on": "2026-03-15"},
                     {"lane": "BLR-DEL", "slab": 750, "on": "2026-03-15"},
                     {"lane": "BLR-DEL", "slab": 500, "on": "2025-12-31"}):
            ok = _quote(quote, lane="BLR-BOM", slab=500, on="2026-03-15", kg=1)
            out.append(await _refused(world.a, world.pkg_a, ok, _quote(quote, kg=1, **data)))
        return out, quote

    refusals, quote = _go(world, go)
    for refused in refusals:
        assert refused.index == 1
        cause = refused.cause
        assert isinstance(cause, LookupRefused) and cause.code == sr.LOOKUP_MISSING
        assert cause.field == "rate" and "no row of `card` matches" in cause.message
        assert "SECRET" not in cause.message and "SECRET" not in str(refused)
    assert _rows(world, quote) == 0


def test_r8_an_empty_key_clears_the_rate_and_the_formula_gives_no_value(world) -> None:
    async def go() -> list[Any]:
        _, quote = await _freight(world.a, world.pkg_a)
        return await _put(world.a, world.pkg_a, _quote(quote, lane="BLR-DEL", slab=500, kg=4))

    (made,) = _go(world, go)
    assert "rate" not in made.data and "freight" not in made.data


def test_r8_a_caller_cannot_write_the_rate(world) -> None:
    async def go() -> BatchError:
        _, quote = await _freight(world.a, world.pkg_a)
        return await _refused(world.a, world.pkg_a, _quote(
            quote, lane="BLR-DEL", slab=500, on="2026-03-15", kg=4, rate=_inr(1)))

    cause = _go(world, go).cause
    assert isinstance(cause, FormulaError) and cause.code == sr.COMPUTED_FIELD


def test_r8_an_update_keeps_the_quoted_rate_until_a_key_or_the_date_changes(world) -> None:
    async def go() -> tuple[Any, Any, Any]:
        card, quote = await _freight(world.a, world.pkg_a)
        (made,) = await _put(world.a, world.pkg_a,
                             _quote(quote, lane="BLR-DEL", slab=500, on="2026-03-15", kg=4))
        # The card gets a new row for the same day: an old quote keeps its rate.
        await _put(world.a, world.pkg_a, _row(card, "BLR-DEL", 500, "2026-03-01", 1100))
        (kept,) = await _put(world.a, world.pkg_a, Write("update", quote.id, made.id, {"kg": 5}))
        (moved,) = await _put(world.a, world.pkg_a,
                              Write("update", quote.id, made.id, {"on": "2026-06-02"}))
        return made, kept, moved

    made, kept, moved = _go(world, go)
    assert made.data["rate"] == _inr(1000)
    assert kept.data["rate"] == _inr(1000) and kept.data["freight"] == _inr(5000)
    assert moved.data["rate"] == _inr(1200) and moved.data["freight"] == _inr(6000)


def test_r8_a_row_of_the_same_batch_is_read(world) -> None:
    async def go() -> list[Any]:
        card, quote = await _freight(world.a, world.pkg_a)
        return await _put(world.a, world.pkg_a, _row(card, "DEL-CCU", 500, "2026-01-01", 900),
                          _quote(quote, lane="DEL-CCU", slab=500, on="2026-02-01", kg=1))

    assert _go(world, go)[1].data["rate"] == _inr(900)


def test_r8_a_kept_read_is_dropped_when_the_batch_writes_the_source(world) -> None:
    """The batch keeps the rows of each read. A row of the card that a later
    write of the same batch makes is still read: the write drops the kept rows."""
    async def go() -> list[Any]:
        card, quote = await _freight(world.a, world.pkg_a)
        keys = {"lane": "BLR-DEL", "slab": 500, "on": "2026-03-15", "kg": 1}
        return await _put(world.a, world.pkg_a, _quote(quote, **keys), _quote(quote, **keys),
                          _row(card, "BLR-DEL", 500, "2026-03-01", 1100),
                          _quote(quote, **keys))

    made = _go(world, go)
    assert [made[i].data["rate"] for i in (0, 1, 3)] == [_inr(1000), _inr(1000), _inr(1100)]


def test_r8_two_rows_on_one_date_are_ambiguous(world) -> None:
    async def go() -> BatchError:
        card, quote = await _freight(world.a, world.pkg_a)
        await _put(world.a, world.pkg_a, _row(card, "BLR-DEL", 500, "2026-06-01", 1300))
        return await _refused(world.a, world.pkg_a,
                              _quote(quote, lane="BLR-DEL", slab=500, on="2026-07-01", kg=1))

    cause = _go(world, go).cause
    assert isinstance(cause, LookupRefused) and cause.code == sr.LOOKUP_AMBIGUOUS


def test_r8_org_b_never_reads_a_row_of_org_a(world) -> None:
    """Org B has the same package shape and no row. RLS and the bound org of
    each statement keep the rows of org A out, so the quote of org B is refused."""
    async def go() -> BatchError:
        await _freight(world.a, world.pkg_a)
        await _object(world.b, world.pkg_b, "card", _CARD_FIELDS)
        quote_b = await _object(world.b, world.pkg_b, "quote", _QUOTE_FIELDS, [_RATE, _FREIGHT])
        return await _refused(world.b, world.pkg_b,
                              _quote(quote_b, lane="BLR-DEL", slab=500, on="2026-03-15", kg=4))

    cause = _go(world, go).cause
    assert isinstance(cause, LookupRefused) and cause.code == sr.LOOKUP_MISSING


def test_r8_a_table_of_another_package_is_not_read(world) -> None:
    """The source is a bare name of THIS package. A card of another package of
    the same org is not found, so the declaration is refused."""
    async def go() -> str:
        with world.p.admin_engine.begin() as c:
            other = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc19-{TAG}-other-{uuid.uuid4().hex[:6]}", "oe": ANA, "org": world.a,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
        try:
            await _object(world.a, other, "card", _CARD_FIELDS)
            with pytest.raises(ConstraintError) as refused:
                await _object(world.a, world.pkg_a, "quote", _QUOTE_FIELDS, [_RATE])
            return str(refused.value)
        finally:
            with world.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM studio_objects WHERE package_id = CAST(:p AS uuid)"),
                          {"p": other})
                c.execute(text("DELETE FROM apps WHERE id = CAST(:p AS uuid)"), {"p": other})

    assert "another object of this package" in _go(world, go)


# -- the declaration and the fences ------------------------------------------


def test_r8_a_new_lookup_on_an_object_with_records_is_refused(world) -> None:
    async def go() -> str:
        await _object(world.a, world.pkg_a, "card", _CARD_FIELDS)
        quote = await _object(world.a, world.pkg_a, "quote", _QUOTE_FIELDS, [])
        await _put(world.a, world.pkg_a, _quote(quote, lane="BLR-DEL", kg=1))
        async with tenant_session(world.a) as db:
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, quote.id, [_RATE], package_id=world.pkg_a)
        return str(refused.value)

    assert "declare before the first record" in _go(world, go)


def test_r8_a_key_of_the_table_cannot_turn_cold(world) -> None:
    async def go() -> str:
        card, _ = await _freight(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as refused:
                await store.set_field_hot(db, card.id, "starts", False)
            assert await store.set_field_hot(db, card.id, "rate", False)
        return str(refused.value)

    message = _go(world, go)
    assert "must stay hot" in message and "rate_of_lane" in message


def test_r8_a_table_that_no_longer_fits_refuses_the_write(world) -> None:
    """A key that turned cold by raw SQL fails closed with `declaration`, and
    never reads a wrong row."""
    async def go() -> BatchError:
        card, quote = await _freight(world.a, world.pkg_a)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_object_fields SET hot = false "
                           "WHERE object_id = CAST(:o AS uuid) AND key = 'lane'"), {"o": card.id})
        return await _refused(world.a, world.pkg_a,
                              _quote(quote, lane="BLR-DEL", slab=500, on="2026-03-15", kg=1))

    cause = _go(world, go).cause
    assert isinstance(cause, LookupRefused) and cause.code == "declaration"


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_studio_lookup_r8\.py \\?$", workflow, re.M), name
