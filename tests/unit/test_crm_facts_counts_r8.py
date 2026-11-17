"""R8 — the CRM facts count a module from its fill rows (task 3593).

Production (48 h to 2026-11-16, 45 chat turns): the ``crm_facts`` lap of the
run setup had p50 169 ms and p90 1,036 ms. The cause is the exact count of
``crm_module_records`` in ``catalogue._load``. On a seeded org of 480,000
records (8 modules) its plan is a parallel sequential scan: 85 ms warm and
190 ms with cold buffers, and the cost grows with the table. The facts read
``records > 0`` and the order of the modules. They never read the number.

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal:

1. ``_load(count=COUNT_FROM_FILL)`` takes the count of a module from its fill
   rows, and counts exactly ONLY a module that has no fill row. A fill total
   of 0 is not trusted, so the exact count decides.
2. The default ``_load`` still counts every module exactly (the catalogue
   route, whose body shows the number).
3. ``crm_facts`` reads the facts of an org with no exact count of a module
   that has fill rows, and still names a module that has records and no fill.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_catalogue_r8 import (  # noqa: F401
    _DB_GATE,
    _fills,
    _member,
    cat_env,
    org_a,
)
from tests.unit.test_h3_rls_promotion_rehearsal import app_engine, promoted  # noqa: F401

pytestmark = _DB_GATE


class _Spy:
    """A session that keeps the SQL and the bound modules of each count."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self.counted: list[list[str]] = []

    async def execute(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> Any:
        if "FROM crm_module_records" in str(stmt) and "count(*)" in str(stmt):
            self.counted.append(sorted((params or {}).get("modules") or []))
        return await self._db.execute(stmt, params, *a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


async def _as_app(p: Any, org: str, work: Any) -> Any:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes.crm.core import _tenant_session

    scope = clear_tenant()
    token = bind_tenant(org)
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            _tenant_session() as db,
        ):
            spy = _Spy(db)
            return await work(spy), spy
    finally:
        release_tenant(token)
        release_tenant(scope)


def _records_of(sources: list[Any]) -> dict[str, int]:
    return {s.module: s.records for s in sources if s.kind == "module"}


async def test_the_fill_total_is_the_count_and_only_a_module_with_no_fill_is_counted(
    org_a,
) -> None:
    from gateway.routes.crm.catalogue import COUNT_FROM_FILL, _load

    async def work(db: _Spy) -> Any:
        return await _load(db, org_a.org_a, count=COUNT_FROM_FILL)

    (_mode, sources, _roles, _others), spy = await _as_app(org_a, org_a.org_a, work)
    got = _records_of(sources)
    # Invoices has fill rows with a total of 100 (3 records exist): the fill wins.
    assert got["Invoices"] == 100
    # No fill row: counted exactly. Quotes holds none.
    assert (got["Fee_Receipts"], got["Buyer_Clients"], got["Towers"], got["Quotes"]) == (
        2, 1, 4, 0)
    # ONE exact count, and it names no module that has fill rows.
    assert len(spy.counted) == 1
    assert "Invoices" not in spy.counted[0]
    assert {"Fee_Receipts", "Buyer_Clients", "Towers", "Quotes"} <= set(spy.counted[0])


async def test_a_fill_total_of_zero_is_not_trusted(org_a) -> None:
    from gateway.routes.crm.catalogue import COUNT_FROM_FILL, _load

    # Fee_Receipts gets a fill row of total 0 although 2 records exist now.
    _fills(org_a, org_a.org_a, "Fee_Receipts", {"Paid_On": 0.0}, total=0)

    async def work(db: _Spy) -> Any:
        return await _load(db, org_a.org_a, count=COUNT_FROM_FILL)

    (_m, sources, _r, _o), spy = await _as_app(org_a, org_a.org_a, work)
    assert _records_of(sources)["Fee_Receipts"] == 2
    assert "Fee_Receipts" in spy.counted[0]


async def test_the_default_load_still_counts_each_module_exactly(org_a) -> None:
    from gateway.routes.crm.catalogue import _load

    async def work(db: _Spy) -> Any:
        return await _load(db, org_a.org_a)

    (_m, sources, _r, _o), spy = await _as_app(org_a, org_a.org_a, work)
    assert _records_of(sources)["Invoices"] == 3
    assert len(spy.counted) == 1 and "Invoices" in spy.counted[0]


async def test_the_facts_read_no_exact_count_of_a_module_with_fill_rows(
    org_a, monkeypatch,
) -> None:
    from acb_common import get_settings
    from gateway.routes.crm.facts import crm_facts

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)

    async def work(db: _Spy) -> Any:
        return await crm_facts(db, _member(org_a.org_a))

    facts, spy = await _as_app(org_a, org_a.org_a, work)
    assert facts, "the org has a catalogue, so it has facts"
    assert all("Invoices" not in group for group in spy.counted)
    subjects = {f.subject for f in facts}
    # Records and no fill row: the module is still there.
    assert "module:Fee_Receipts" in subjects
