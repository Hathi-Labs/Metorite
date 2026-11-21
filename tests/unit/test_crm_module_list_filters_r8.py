"""R8 — one filter builder for a module list and a module total (CRM-Q34-4).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-4.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal.

1. A list and a total with the SAME ``field.<key>`` filters hold the same
   records: a text value in any case, a lookup by its exact name, a number
   range, two filters, and a filter with ``q`` (extends
   ``test_the_aggregate_reuses_the_list_filter_builder`` to modules).
2. An unknown field is a 422 on both. Org B stays out.
3. The flag: off, a list with ``field.<key>`` is a 422 (never a list that
   dropped the filter), and the list without it keeps its answer, with
   ``custom.<key>`` too.
4. ``custom.<key>`` still works for the UI beside ``field.<key>``.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit.test_crm_module_find_r8 import (  # noqa: F401
    _flag,
    _list,
    _names,
    _total,
    inv,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


async def _same(p: Any, org: str, query: dict[str, str]) -> list[str]:
    """The names of the list, after the total counted the same records."""
    total = await _total(p, org, **query)
    page = await _list(p, org, **query)
    assert total.status_code == 200, total.text
    assert page.status_code == 200, page.text
    names = sorted(r["name"] for r in page.json()["rows"])
    assert total.json()["records"] == page.json()["total"] == len(names), (query, names)
    return names


@pytest.mark.parametrize(("query", "names"), [
    ({"field.Subject": "under_score item 500"}, ["INV-002"]),
    ({"field.Subject": "UNDER_SCORE ITEM 500"}, ["INV-002"]),
    ({"field.Customer": "Acme Ltd"}, ["INV-002", "Ltd bill"]),
    # A lookup matches the exact stored name (the docstring of crm_aggregate).
    ({"field.Customer": "acme ltd"}, []),
    ({"field.Amount.from": "300", "field.Amount.to": "500"},
     ["INV-003", "INV-004", "INV-005"]),
    ({"field.Amount.from": "850"}, ["Meta row"]),
    ({"field.Customer": "Acme Ltd", "field.Amount.from": "300"}, ["Ltd bill"]),
    ({"q": "acme", "field.Amount.to": "200"}, ["INV-002"]),
    ({"field.Customer": "Nobody At All"}, []),
])
async def test_a_list_and_a_total_with_the_same_filters_count_the_same(
        inv, app_engine, query: dict[str, str], names: list[str]) -> None:
    assert await _same(inv, inv.org_a, query) == names


async def test_a_lookup_filter_by_a_metorite_id_counts_the_same(inv, app_engine) -> None:
    """The lookup id read of task 3128 is shared: an id that is no record of
    this org matches none, on both."""
    org_b_id = (await _list(inv, inv.org_b)).json()["rows"][0]["id"]
    assert await _same(inv, inv.org_a, {"field.Customer": org_b_id}) == []


@pytest.mark.parametrize("key", ["field.NoSuchField", "field.Amount.sideways"])
async def test_an_unknown_field_is_a_422_on_both(inv, app_engine, key: str) -> None:
    total = await _total(inv, inv.org_a, **{key: "1"})
    page = await _list(inv, inv.org_a, **{key: "1"})
    assert total.status_code == page.status_code == 422, (total.text, page.text)


async def test_org_b_stays_out(inv, app_engine) -> None:
    assert await _same(inv, inv.org_a, {"field.Customer": "Sharath Secret"}) == []
    assert await _same(inv, inv.org_b, {"field.Customer": "Sharath Secret"}) == [
        "Secret B Name"]


async def test_off_a_list_with_a_field_filter_is_a_422(inv, app_engine, monkeypatch) -> None:
    _flag(monkeypatch, "")
    page = await _list(inv, inv.org_a, **{"field.Subject": "under_score item 500"})
    assert page.status_code == 422
    assert "crm_aggregate" in page.text
    # No filter, and the old filters: the answer of before.
    everything = await _list(inv, inv.org_a)
    assert everything.status_code == 200 and everything.json()["total"] == 9
    custom = await _list(inv, inv.org_a, **{"custom.Subject": "under_score item 500"})
    assert [r["name"] for r in custom.json()["rows"]] == ["INV-002"]


async def test_on_custom_and_field_keep_both_their_answers(inv, app_engine) -> None:
    value = "under_score item 500"
    by_custom = await _list(inv, inv.org_a, **{"custom.Subject": value})
    by_field = await _list(inv, inv.org_a, **{"field.Subject": value})
    assert [r["name"] for r in by_custom.json()["rows"]] == ["INV-002"]
    assert by_custom.json()["rows"] == by_field.json()["rows"]
