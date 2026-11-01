"""WS-STU C2a — the object query on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C2a row. Code:
``gateway/studio/objects_store.query_records`` and
``acb_common/studio_query.py``. ``test_studio_query.py`` holds the hermetic
half.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement and each plan. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_query_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (C2a).**

a. Each op of each type gives the same records on a hot field and on a field
   that is not hot. ``ne`` matches only a record that holds a value.
b. A text of 256 characters or more matches against ``data`` too.
c. A sort reads the hot rows in order, with the record id as the tie-break,
   and the records with no value come last, in the order of their id. Each
   page holds each record once.
d. A query of one package or org reads nothing of another, and a query of
   one object reads no record of another object.
e. ``hot_or_small``: a filter on a field that is not hot is refused over
   5,000 records.
f. A timeout stops the query with ``query_timeout``. The session stays usable,
   and the old ``statement_timeout`` comes back.
g. On 50,000 records, EXPLAIN of the SQL of ``build_query`` shows the hot
   indexes, no Seq Scan, and no Sort for a hot sort.

**The mutations (R7).** Commit before each, and restore with git after.

* Q1. ``objects_store._QUERY_BASE``: drop ``AND {_IN_PACKAGE}``.
  ``test_r8_the_built_sql_of_another_package_reads_nothing`` goes red.
* Q2. ``objects_store._QUERY_BASE``: drop ``r.object_id = CAST(:oid AS uuid)``.
  ``test_r8_a_query_reads_its_own_object_only`` goes red.
* Q3 is hermetic: ``test_studio_query.py``.
* Q4. ``objects_store._q_text_eq``: for a value of 256 or more, drop the
  check ``AND r.data ->> ... = ...`` and keep the test of ``v_text``.
  ``test_r8_a_long_text_match_is_checked_against_data`` goes red. In
  ``_q_contains``, drop the second branch: the same test goes red.
* Q5. ``objects_store._time_limit``: drop the ``set_config`` of the timeout.
  ``test_r8_a_query_past_the_timeout_stops_with_query_timeout`` goes red.
* Q6. ``objects_store._time_limit``: drop ``begin_nested()``, or drop the
  restore in ``finally``. ``test_r8_a_query_past_the_timeout_stops_with_query_timeout``
  or ``test_r8_the_old_timeout_is_back_after_a_query`` goes red.
* Q7. ``objects_store._check_hot_or_small``: ``return`` before the ``raise``.
  ``test_r8_a_cold_filter_on_more_than_5000_records_is_refused`` goes red.
* Q8. ``objects_store.build_query``: drop ``, r.id{dirn}`` from the ORDER BY.
  ``test_r8_a_sort_pages_each_record_once_in_order`` goes red.
* Q9. ``objects_store._CMP``: swap ``<`` and ``<=``.
  ``test_r8_lt_and_lte_differ_at_the_boundary`` goes red.
* Q10. ``objects_store._q_hot``: return ``NOT EXISTS ({row} AND NOT ({frag}))``
  for ``ne``. ``test_r8_ne_matches_only_records_that_hold_a_value`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields as sf
from acb_common import studio_query
from acb_common.db import tenant_session
from acb_common.studio_query import QueryError
from gateway.studio import objects_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc2.test"
_TABLES = ("studio_records", "studio_record_hot")

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc2', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc2", "name": "stuc2", "purpose": "The C2a R8 suite."})

#: The eight types of the op table. Each has a hot field and a cold twin
#: (`c_<key>`) that holds the same value.
_TYPED: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "mail", "type": "email"},
    {"key": "status", "type": "select", "options": ["planned", "on site", "done"]},
    {"key": "hours", "type": "number"},
    {"key": "fee", "type": "money"},
    {"key": "day", "type": "date"},
    {"key": "at", "type": "datetime"},
    {"key": "paid", "type": "checkbox"},
]
_FIELDS = [{**f, "hot": True} for f in _TYPED] + [{**f, "key": "c_" + f["key"]} for f in _TYPED]

_PREFIX = "x" * 250 + "abcdef"  # 256 characters
L1 = _PREFIX + "-tail-one"
L2 = _PREFIX + "-tail-two"

#: The records of the object `visit`, by label. Each value is in the hot field
#: and in its cold twin.
_RECORDS: dict[str, dict[str, Any]] = {
    "r1": {"name": "Alpha", "mail": "a@x.test", "status": "planned", "hours": 1,
           "fee": {"minor": 100, "currency": "INR"}, "day": "2026-01-01",
           "at": "2026-01-01T10:00:00Z", "paid": True},
    "r2": {"name": "beta 50% off_now", "mail": "B@Y.test", "status": "done", "hours": 5,
           "fee": {"minor": 500, "currency": "INR"}, "day": "2026-01-05",
           "at": "2026-01-05T00:00:00Z", "paid": False},
    "r3": {"name": "Gamma", "status": "on site", "hours": 5.5,
           "fee": {"minor": 500, "currency": "USD"}, "day": "2026-01-10",
           "at": "2026-01-05T23:59:59Z", "paid": True},
    "r4": {"name": "back\\slash", "hours": 10, "day": "2026-02-01",
           "at": "2026-02-01T00:00:00+05:30"},
    "r5": {},
    "r6": {"name": L1},
    "r7": {"name": L2},
    "r8": {"name": _PREFIX},
}

_ALL = set(_RECORDS)
_NONE: set[str] = set()


def _twin(data: dict[str, Any]) -> dict[str, Any]:
    return {**data, **{"c_" + k: v for k, v in data.items()}}


#: (field, op, value, the labels that match). Each case runs on the hot
#: field and on its cold twin.
_CASES: dict[str, list[tuple[str, Any, set[str]]]] = {
    "text": [
        ("eq", "Alpha", {"r1"}),
        ("eq", "alpha", _NONE),
        ("eq", L1, {"r6"}),
        ("eq", _PREFIX, {"r8"}),
        ("ne", "Alpha", {"r2", "r3", "r4", "r6", "r7", "r8"}),
        ("ne", L1, {"r1", "r2", "r3", "r4", "r7", "r8"}),
        ("in", ["Alpha", "Gamma", L2], {"r1", "r3", "r7"}),
        ("contains", "ph", {"r1"}),
        ("contains", "GAMMA", {"r3"}),
        ("contains", "%", {"r2"}),
        ("contains", "_", {"r2"}),
        ("contains", "\\", {"r4"}),
        ("contains", "tail-two", {"r7"}),
        ("contains", "ABCDEF", {"r6", "r7", "r8"}),
        ("is_empty", True, {"r5"}),
        ("is_empty", False, _ALL - {"r5"}),
    ],
    "email": [
        ("eq", "B@Y.test", {"r2"}),
        ("ne", "a@x.test", {"r2"}),
        ("in", ["a@x.test", "b@y.test"], {"r1", "r2"}),
        ("contains", "X.TEST", {"r1"}),
        ("is_empty", True, _ALL - {"r1", "r2"}),
    ],
    "select": [
        ("eq", "done", {"r2"}),
        ("ne", "done", {"r1", "r3"}),
        ("in", ["planned", "on site"], {"r1", "r3"}),
        ("is_empty", True, _ALL - {"r1", "r2", "r3"}),
    ],
    "number": [
        ("eq", 5, {"r2"}),
        ("eq", 5.5, {"r3"}),
        ("ne", 5, {"r1", "r3", "r4"}),
        ("lt", 5, {"r1"}),
        ("lte", 5, {"r1", "r2"}),
        ("gt", 5, {"r3", "r4"}),
        ("gte", 5, {"r2", "r3", "r4"}),
        ("in", [1, 10], {"r1", "r4"}),
        ("is_empty", True, _ALL - {"r1", "r2", "r3", "r4"}),
    ],
    "money": [
        ("eq", 500, {"r2", "r3"}),
        ("ne", 500, {"r1"}),
        ("lt", 500, {"r1"}),
        ("lte", 500, {"r1", "r2", "r3"}),
        ("gte", 500, {"r2", "r3"}),
        ("in", [100], {"r1"}),
        ("is_empty", True, _ALL - {"r1", "r2", "r3"}),
    ],
    "date": [
        ("eq", "2026-01-05", {"r2"}),
        ("lt", "2026-01-05", {"r1"}),
        ("lte", "2026-01-05", {"r1", "r2"}),
        ("gt", "2026-01-05", {"r3", "r4"}),
        ("gte", "2026-01-05", {"r2", "r3", "r4"}),
        ("is_empty", True, _ALL - {"r1", "r2", "r3", "r4"}),
    ],
    "datetime": [
        ("eq", "2026-01-05", {"r2", "r3"}),
        ("eq", "2026-01-05T00:00:00Z", {"r2"}),
        ("eq", "2026-01-05T05:30:00+05:30", {"r2"}),
        ("lt", "2026-01-05", {"r1"}),
        ("lte", "2026-01-05", {"r1", "r2", "r3"}),
        ("gt", "2026-01-05", {"r4"}),
        ("gte", "2026-01-05", {"r2", "r3", "r4"}),
        ("lt", "2026-01-05T00:00:00Z", {"r1"}),
        ("lte", "2026-01-05T00:00:00Z", {"r1", "r2"}),
        ("gt", "2026-01-05T00:00:00Z", {"r3", "r4"}),
        ("is_empty", True, _ALL - {"r1", "r2", "r3", "r4"}),
    ],
    "checkbox": [
        ("eq", True, {"r1", "r3"}),
        ("eq", False, {"r2"}),
        ("ne", True, {"r2"}),
        ("ne", False, {"r1", "r3"}),
    ],
}
_KEY_OF = {f["type"]: f["key"] for f in _TYPED}
assert set(_CASES) == set(studio_query.OPS_BY_TYPE)

#: The big object: 50,000 records. `n` is missing on each tenth record.
_BIG_FIELDS: list[dict[str, Any]] = [
    {"key": "n", "type": "number", "hot": True},
    {"key": "t", "type": "text", "hot": True},
    {"key": "d", "type": "datetime", "hot": True},
    {"key": "b", "type": "checkbox", "hot": True},
    {"key": "cold", "type": "text"},
]
BIG = 50_000
#: Record g is at 2026-01-01T00:00Z + 3g minutes, so one day holds 480 records (1%).
_BIG_RECORDS_SQL = (
    "INSERT INTO studio_records (object_id, id, data, created_by, updated_by) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), jsonb_strip_nulls(jsonb_build_object("
    "'n', CASE WHEN g % 10 <> 0 THEN g END, "
    "'t', 'name ' || lpad(g::text, 6, '0'), "
    "'d', to_char((timestamptz '2026-01-01T00:00:00Z' + g * interval '3 minutes') "
    "AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
    "'b', g % 2 = 0, 'cold', 'c' || (g % 100))), :a, :a "
    "FROM generate_series(1, :big) g"
)
_BIG_HOT_SQL = (
    "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_text, v_num, v_ts, v_bool) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), f.k, "
    "CASE f.k WHEN 't' THEN 'name ' || lpad(g::text, 6, '0') END, "
    "CASE f.k WHEN 'n' THEN g END, "
    "CASE f.k WHEN 'd' THEN timestamptz '2026-01-01T00:00:00Z' + g * interval '3 minutes' END, "
    "CASE f.k WHEN 'b' THEN g % 2 = 0 END "
    "FROM generate_series(1, :big) g CROSS JOIN (VALUES ('n'), ('t'), ('d'), ('b')) f(k) "
    "WHERE NOT (f.k = 'n' AND g % 10 = 0)"
)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _run(url: Any, fn: Callable[[], Any]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(url)):
            return await fn()
    return asyncio.run(go())


async def _define(org: str, pkg: str, name: str, fields: list[dict[str, Any]]) -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name, fields=fields)
    assert made is not None
    return made


async def _create(org: str, obj: store.StudioObject, rows: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Create the records *rows*, and answer the id of each label."""
    out: dict[str, str] = {}
    async with tenant_session(org) as db:
        for label, data in rows.items():
            rec = await store.create_record(db, ANA, obj.id, data, package_id=obj.package_id)
            assert rec is not None
            out[label] = rec.id
    return out


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """Read-only data for the whole module.

    Org A: `visit` in package a (the operator table), `visit` in package a2
    and `trip` in package a (the same keys and values), and `tie` in package a
    (the sort). Org B: `visit` in package b, and `big` with 50,000 records.
    """
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("a2", p.org_a), ("b", p.org_b)):
            pkgs[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc2-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def seed() -> SimpleNamespace:
        visit = await _define(p.org_a, pkgs["a"], "visit", _FIELDS)
        ids = await _create(p.org_a, visit, {k: _twin(v) for k, v in _RECORDS.items()})
        other_pkg = await _define(p.org_a, pkgs["a2"], "visit", _FIELDS)
        await _create(p.org_a, other_pkg, {k: _twin(_RECORDS[k]) for k in ("r1", "r2", "r3")})
        trip = await _define(p.org_a, pkgs["a"], "trip", [
            {"key": "name", "type": "text", "hot": True},
            {"key": "hours", "type": "number", "hot": True}])
        await _create(p.org_a, trip, {"t1": {"name": "Alpha", "hours": 5}})
        tie = await _define(p.org_a, pkgs["a"], "tie", [
            {"key": "hours", "type": "number", "hot": True}])
        tie_rows = {f"t{n}": {"hours": 1 + n % 3} for n in range(30)}
        tie_rows.update({f"e{n}": {} for n in range(5)})
        tie_ids = await _create(p.org_a, tie, tie_rows)
        visit_b = await _define(p.org_b, pkgs["b"], "visit", _FIELDS)
        b_ids = await _create(p.org_b, visit_b, {k: _twin(_RECORDS[k]) for k in ("r1", "r2")})
        big = await _define(p.org_b, pkgs["b"], "big", _BIG_FIELDS)
        async with tenant_session(p.org_b) as db:
            await db.execute(text(_BIG_RECORDS_SQL), {"oid": big.id, "a": ANA, "big": BIG})
            await db.execute(text(_BIG_HOT_SQL), {"oid": big.id, "big": BIG})
        return SimpleNamespace(visit=visit, ids=ids, other_pkg=other_pkg, trip=trip, tie=tie,
                               tie_ids=tie_ids, tie_rows=tie_rows, visit_b=visit_b,
                               b_ids=b_ids, big=big)

    ns = _run(p.app_url, seed)
    with p.admin_engine.begin() as c:
        c.execute(text("ANALYZE studio_records"))
        c.execute(text("ANALYZE studio_record_hot"))
    ns.p, ns.a, ns.b = p, p.org_a, p.org_b
    ns.pkg_a, ns.pkg_a2, ns.pkg_b = pkgs["a"], pkgs["a2"], pkgs["b"]
    ns.label = {v: k for k, v in ns.ids.items()}
    yield ns
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE organization_id IN "
                       "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc2-{TAG}-{run}-%"})


async def _query(org: str, obj: store.StudioObject, **kw: Any) -> store.RecordPage | None:
    async with tenant_session(org) as db:
        return await store.query_records(db, package_id=obj.package_id, object_id=obj.id, **kw)


def _labels(world: Any, page: store.RecordPage | None) -> set[str]:
    assert page is not None
    return {world.label[r.id] for r in page.records}


# ── a. Each op of each type, hot and cold ───────────────────────────────────


@pytest.mark.parametrize("kind", sorted(_CASES))
def test_r8_each_op_of_each_type_on_a_hot_and_a_cold_field(world, kind: str) -> None:
    """The hot rows and `data` give the same answer, and each matches the table."""
    key = _KEY_OF[kind]

    async def go() -> list[str]:
        wrong: list[str] = []
        for field in (key, "c_" + key):
            for op, value, want in _CASES[kind]:
                page = await _query(world.a, world.visit, filter=[
                    {"field": field, "op": op, "value": value}], limit=100)
                got = _labels(world, page)
                if got != want or page.total != len(want):
                    wrong.append(f"{field} {op} {value!r:.40}: got {sorted(got)}, "
                                 f"total {page.total}, want {sorted(want)}")
        return wrong

    assert _run(world.p.app_url, go) == []


def test_r8_ne_matches_only_records_that_hold_a_value(world) -> None:
    async def go() -> tuple[set[str], set[str]]:
        hot = await _query(world.a, world.visit, filter=[{"field": "hours", "op": "ne", "value": 5}])
        cold = await _query(world.a, world.visit, filter=[{"field": "c_hours", "op": "ne", "value": 5}])
        return _labels(world, hot), _labels(world, cold)

    assert _run(world.p.app_url, go) == ({"r1", "r3", "r4"}, {"r1", "r3", "r4"})


def test_r8_lt_and_lte_differ_at_the_boundary(world) -> None:
    async def go() -> list[set[str]]:
        out = []
        for field, op, value in (("hours", "lt", 5), ("hours", "lte", 5),
                                 ("at", "lt", "2026-01-05T00:00:00Z"),
                                 ("at", "lte", "2026-01-05T00:00:00Z")):
            out.append(_labels(world, await _query(world.a, world.visit, filter=[
                {"field": field, "op": op, "value": value}])))
        return out

    assert _run(world.p.app_url, go) == [{"r1"}, {"r1", "r2"}, {"r1"}, {"r1", "r2"}]


# ── b. A long text ──────────────────────────────────────────────────────────


def test_r8_a_long_text_match_is_checked_against_data(world) -> None:
    """r6, r7 and r8 have the same 256 characters in `v_text`. Only `data`
    tells them apart."""
    async def go() -> list[set[str]]:
        out = []
        for op, value in (("eq", L1), ("eq", _PREFIX), ("in", [L2]),
                          ("contains", "tail-one"), ("ne", L2)):
            out.append(_labels(world, await _query(world.a, world.visit, filter=[
                {"field": "name", "op": op, "value": value}])))
        return out

    assert _run(world.p.app_url, go) == [
        {"r6"}, {"r8"}, {"r7"}, {"r6"}, {"r1", "r2", "r3", "r4", "r6", "r8"}]


# ── c. The sort ─────────────────────────────────────────────────────────────


def _pages(world: Any, size: int, desc: bool) -> list[str]:
    async def go() -> list[str]:
        seen: list[str] = []
        offset = 0
        while True:
            page = await _query(world.a, world.tie, sort={
                "field": "hours", "dir": "desc" if desc else "asc"}, limit=size, offset=offset)
            assert page is not None and page.total == 35
            seen += [r.id for r in page.records]
            offset += size
            if offset >= 35:
                return seen

    return _run(world.p.app_url, go)


@pytest.mark.parametrize(("size", "desc"), [(4, False), (7, True)])
def test_r8_a_sort_pages_each_record_once_in_order(world, size: int, desc: bool) -> None:
    """Ties go by the record id, and the 5 records with no value come last,
    in the order of their id. A page of 4 starts the tail after an empty head."""
    hours = {world.tie_ids[k]: v.get("hours") for k, v in world.tie_rows.items()}
    valued = sorted((i for i in hours if hours[i] is not None),
                    key=lambda i: (hours[i], uuid.UUID(i)), reverse=desc)
    empty = sorted((i for i in hours if hours[i] is None), key=uuid.UUID)
    assert _pages(world, size, desc) == valued + empty


def test_r8_a_page_past_the_end_is_empty_and_says_so(world) -> None:
    async def go() -> store.RecordPage | None:
        return await _query(world.a, world.tie, sort={"field": "hours"}, offset=40)

    page = _run(world.p.app_url, go)
    assert page is not None and page.records == () and page.total == 35
    assert page.line == "Nothing at offset=40. There are 35 records, so start below offset=35."


# ── d. Tenancy: package, object and org ─────────────────────────────────────


def test_r8_a_query_names_its_package(world) -> None:
    async def go() -> list[Any]:
        async with tenant_session(world.a) as db:
            return [
                await store.query_records(db, package_id=world.pkg_a2, object_id=world.visit.id),
                await store.query_records(db, package_id=world.pkg_a, object_id=world.other_pkg.id),
            ]

    assert _run(world.p.app_url, go) == [None, None]


def test_r8_the_built_sql_of_another_package_reads_nothing(world) -> None:
    """The statement itself checks the package, also with a true object id."""
    query = studio_query.parse_query((), None)

    async def go() -> tuple[int, int, int, int]:
        out = []
        async with tenant_session(world.a) as db:
            for pid in (world.pkg_a, world.pkg_a2):
                sql, params = store.build_query(query, object_id=world.visit.id, package_id=pid,
                                                limit=100, offset=0)
                out.append(len((await db.execute(text(sql), params)).all()))
                sql, params = store.build_count((), object_id=world.visit.id, package_id=pid,
                                                cap=100)
                out.append(int((await db.execute(text(sql), params)).scalar_one()))
        return tuple(out)

    assert _run(world.p.app_url, go) == (8, 8, 0, 0)


def test_r8_a_query_reads_its_own_object_only(world) -> None:
    """`trip` and `tie` are in the same package, and `trip` holds an Alpha too."""
    async def go() -> tuple[Any, Any]:
        whole = await _query(world.a, world.visit, limit=100)
        alpha = await _query(world.a, world.visit, filter=[
            {"field": "name", "op": "eq", "value": "Alpha"}])
        return whole, alpha

    whole, alpha = _run(world.p.app_url, go)
    assert {r.object_id for r in whole.records} == {world.visit.id}
    assert _labels(world, whole) == _ALL and whole.total == 8
    assert _labels(world, alpha) == {"r1"} and alpha.total == 1


def test_r8_org_b_reads_nothing_of_org_a(world) -> None:
    async def go() -> tuple[Any, Any]:
        async with tenant_session(world.b) as db:
            theirs = await store.query_records(db, package_id=world.pkg_a, object_id=world.visit.id)
            mine = await store.query_records(db, package_id=world.pkg_b,
                                             object_id=world.visit_b.id, filter=[
                {"field": "name", "op": "eq", "value": "Alpha"}])
        return theirs, mine

    theirs, mine = _run(world.p.app_url, go)
    assert theirs is None
    assert [r.id for r in mine.records] == [world.b_ids["r1"]] and mine.total == 1


# ── e. hot_or_small ─────────────────────────────────────────────────────────


def test_r8_a_cold_filter_on_more_than_5000_records_is_refused(world) -> None:
    async def go() -> QueryError:
        with pytest.raises(QueryError) as refused:
            await _query(world.b, world.big, filter=[{"field": "cold", "op": "eq", "value": "c5"}])
        return refused.value

    err = _run(world.p.app_url, go)
    assert err.code == "query_not_indexed" and err.field == "cold"
    assert "`cold`" in err.message and "Turn it hot" in err.message


def test_r8_a_hot_filter_that_leaves_5000_or_fewer_lets_a_cold_filter_run(world) -> None:
    """`n` lte 1000 leaves 900 records, and 10 of them hold `cold` = c5."""
    async def go() -> Any:
        return await _query(world.b, world.big, filter=[
            {"field": "n", "op": "lte", "value": 1000},
            {"field": "cold", "op": "eq", "value": "c5"}], limit=100)

    page = _run(world.p.app_url, go)
    assert page.total == 10 and len(page.records) == 10
    assert all(r.data["cold"] == "c5" and r.data["n"] <= 1000 for r in page.records)


def test_r8_the_session_stays_usable_after_a_refusal(world) -> None:
    async def go() -> Any:
        async with tenant_session(world.b) as db:
            with pytest.raises(QueryError):
                await store.query_records(db, package_id=world.pkg_b, object_id=world.big.id,
                                          filter=[{"field": "cold", "op": "eq", "value": "c5"}])
            return await store.get_record(db, world.visit_b.id, world.b_ids["r2"],
                                          package_id=world.pkg_b)

    rec = _run(world.p.app_url, go)
    assert rec is not None and rec.data["name"] == "beta 50% off_now"


def test_r8_a_big_object_says_at_least_and_stops_at_the_offset_cap(world) -> None:
    async def go() -> tuple[Any, Any]:
        first = await _query(world.b, world.big)
        last = await _query(world.b, world.big, offset=studio_query.MAX_OFFSET, limit=100)
        with pytest.raises(QueryError) as past:
            await _query(world.b, world.big, offset=studio_query.MAX_OFFSET + 1)
        assert past.value.code == "query_too_big"
        return first, last

    first, last = _run(world.p.app_url, go)
    assert first.total is None and first.total_at_least == studio_query.TOTAL_CAP
    assert len(first.records) == studio_query.DEFAULT_LIMIT
    assert first.line == "Showing 1-25. More records exist. Call again with offset=25 for the rest."
    assert len(last.records) == 100
    assert last.line == ("Showing 10001-10100. More records exist. A page starts at "
                         "offset=10000 or before. Narrow the filter to see the rest.")


# ── f. The timeout ──────────────────────────────────────────────────────────


async def _timeout(db: Any) -> str:
    return str((await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar_one())


def test_r8_a_query_past_the_timeout_stops_with_query_timeout(world, monkeypatch) -> None:
    """At 1 ms, a scan of 50,000 hot rows stops. The caller's transaction
    stays usable, and its own timeout is back."""
    monkeypatch.setattr(studio_query, "TIMEOUT_MS", 1)

    async def go() -> tuple[str, Any, str]:
        async with tenant_session(world.b) as db:
            await db.execute(text("SELECT set_config('statement_timeout', '42s', true)"))
            with pytest.raises(QueryError) as stopped:
                await store.query_records(db, package_id=world.pkg_b, object_id=world.big.id,
                                          filter=[{"field": "t", "op": "contains", "value": "zzz"}])
            rec = await store.get_record(db, world.visit_b.id, world.b_ids["r1"],
                                         package_id=world.pkg_b)
            return stopped.value.code, rec, await _timeout(db)

    code, rec, after = _run(world.p.app_url, go)
    assert code == "query_timeout"
    assert rec is not None and rec.data["name"] == "Alpha"
    assert after == "42s"


def test_r8_the_old_timeout_is_back_after_a_query(world) -> None:
    """A success releases the SAVEPOINT, and a LOCAL setting survives that.
    So the store sets the old value back itself."""
    async def go() -> tuple[str, str, str]:
        async with tenant_session(world.a) as db:
            await db.execute(text("SELECT set_config('statement_timeout', '42s', true)"))
            page = await store.query_records(db, package_id=world.pkg_a, object_id=world.visit.id)
            assert page is not None and page.total == 8
            set_before = await _timeout(db)
        async with tenant_session(world.a) as db:
            plain = await _timeout(db)
            await store.query_records(db, package_id=world.pkg_a, object_id=world.visit.id)
            return set_before, plain, await _timeout(db)

    set_before, plain, plain_after = _run(world.p.app_url, go)
    assert set_before == "42s"
    # Postgres shows the 5,000 ms limit as "5s".
    assert plain != "5s" and plain_after == plain


# ── g. The plans on 50,000 records ──────────────────────────────────────────


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


def _explain(world: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async def go() -> Any:
        async with tenant_session(world.b) as db:
            return (await db.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params)).scalar_one()

    raw = _run(world.p.app_url, go)
    doc = json.loads(raw) if isinstance(raw, str) else raw
    return _nodes(doc[0]["Plan"])


def _page_plan(world: Any, filter: Any = None, sort: Any = None) -> list[dict[str, Any]]:
    fields = [sf.parse_field_spec(f) for f in _BIG_FIELDS]
    query = studio_query.parse_query(fields, filter, sort)
    sql, params = store.build_query(query, object_id=world.big.id, package_id=world.pkg_b,
                                    limit=25, offset=0)
    return _explain(world, sql, params)


def _hot_scans(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if n.get("Relation Name") == "studio_record_hot"
            or str(n.get("Index Name", "")).startswith("studio_record_hot_")]


def _rows_read(nodes: list[dict[str, Any]]) -> int:
    """The rows that the scans of `studio_record_hot` examined: the rows they
    gave and the rows that a filter removed. A Bitmap Index Scan feeds its
    heap scan, so only the heap scan counts."""
    return sum((int(n["Actual Rows"]) + int(n.get("Rows Removed by Filter", 0))
                + int(n.get("Rows Removed by Index Recheck", 0))) * int(n["Actual Loops"])
               for n in _hot_scans(nodes) if n["Node Type"] != "Bitmap Index Scan")


def _no_seq_scan(nodes: list[dict[str, Any]]) -> None:
    seq = [n["Relation Name"] for n in nodes
           if n["Node Type"] == "Seq Scan" and n.get("Relation Name") in _TABLES]
    assert seq == [], seq


def _uses(nodes: list[dict[str, Any]], index: str, column: str | None = None) -> bool:
    return any(n["Node Type"] in ("Index Scan", "Index Only Scan", "Bitmap Index Scan")
               and n.get("Index Name") == index
               and (column is None or column in str(n.get("Index Cond", "")))
               for n in nodes)


@pytest.mark.parametrize(("key", "index", "desc"), [
    ("n", "studio_record_hot_num_idx", False),
    ("t", "studio_record_hot_text_idx", False),
    ("d", "studio_record_hot_ts_idx", True),
])
def test_r8_a_hot_sort_reads_its_index_in_order_with_no_sort_node(world, key: str, index: str,
                                                                  desc: bool) -> None:
    nodes = _page_plan(world, sort={"field": key, "dir": "desc" if desc else "asc"})
    assert _uses(nodes, index), [n["Node Type"] for n in nodes]
    _no_seq_scan(nodes)
    assert [n for n in nodes if n["Node Type"] == "Sort"] == []
    assert _rows_read(nodes) < 1_000


def test_r8_a_one_percent_time_filter_reads_a_small_share(world) -> None:
    """One day holds 480 of the 50,000 records. `timestamptz` comparisons are
    LEAKPROOF, so the bound is an index condition for the app role."""
    nodes = _page_plan(world, filter=[{"field": "d", "op": "eq", "value": "2026-01-05"}])
    assert _uses(nodes, "studio_record_hot_ts_idx", "v_ts")
    _no_seq_scan(nodes)
    assert _rows_read(nodes) <= 2_500


def test_r8_a_text_eq_reads_the_text_index(world) -> None:
    nodes = _page_plan(world, filter=[{"field": "t", "op": "eq", "value": "name 000123"}])
    assert _uses(nodes, "studio_record_hot_text_idx", "v_text")
    _no_seq_scan(nodes)
    assert _rows_read(nodes) <= 10


@pytest.mark.parametrize(("key", "op", "value"), [
    ("n", "lte", 500),
    ("b", "eq", True),
])
def test_r8_a_number_or_checkbox_filter_is_bounded_to_its_field(world, key: str, op: str,
                                                                 value: Any) -> None:
    """The known gap. No `numeric` comparison is LEAKPROOF, and `v_bool` has
    no index. So the app role reads the hot rows of the one field, through
    the field prefix of a hot index, and no more."""
    nodes = _page_plan(world, filter=[{"field": key, "op": op, "value": value}])
    _no_seq_scan(nodes)
    scans = _hot_scans(nodes)
    assert scans and all("field_key" in str(n.get("Index Cond", "")) for n in scans
                         if n["Node Type"] != "Bitmap Heap Scan"), scans
    assert _rows_read(nodes) <= BIG
