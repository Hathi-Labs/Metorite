"""HRD-18a repair round 2: the plan of a CRM search, with no database.

Pure checks of ``gateway.routes.crm.core``. The R8 suite
``test_crm_near_search_r8.py`` proves the reads on a real database.

- A ``?q=`` over ``MAX_Q_CHARS`` characters is a 422 before any SQL.
- A query of more than ``name_match.NEAR_MAX_WORDS`` words gets no near
  pass, and neither does the one phrase of a query of 9 words or more.
- A word over ``name_match.NEAR_MAX_CHARS`` characters gets no near pass.
- The predicate of a deal holds no sub-select: the related names come in as
  bound values (``related_hits``). A deal search with no hits refuses to build.
- No caller text reaches the SQL text.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.crm import core


def _hits(q: str, **found: tuple[str, ...]) -> core.RelatedHits:
    return core.RelatedHits(tuple(dict(found) for _ in core.search_words(q)))


def test_a_long_query_is_a_422() -> None:
    assert core.search_words("x" * core.MAX_Q_CHARS) == ["x" * core.MAX_Q_CHARS]
    with pytest.raises(HTTPException) as refused:
        core.search_words("x" * (core.MAX_Q_CHARS + 1))
    assert refused.value.status_code == 422
    with pytest.raises(HTTPException):
        core.list_contract(core.CONTACTS, q="x" * 5000, near="also")


def test_a_long_query_gets_no_near_pass() -> None:
    assert core.near_words("Sharath 3D printer") == [(0, "Sharath"), (2, "printer")]
    assert core.near_words("Sharath 3D printer order") == []
    nine = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii"
    assert core.search_words(nine) == [nine]
    assert core.near_words(nine) == []
    query = core.list_contract(core.CONTACTS, q=nine, near="also")
    assert query.near is None and query.also is None


def test_a_long_word_gets_no_near_pass() -> None:
    assert core.near_words("y" * 40) == [(0, "y" * 40)]
    assert core.near_words("y" * 41) == []
    assert core.list_contract(core.CONTACTS, q="y" * 41, near=True).near is None


def test_the_near_pass_is_planned_for_page_one_only() -> None:
    query = core.list_contract(core.CONTACTS, q="Sharath", near="also")
    assert query.near is not None and query.near.limit == 20 and not query.near.also
    assert query.also is not None and query.also.limit == core.NEAR_ALSO_LIMIT
    assert query.also.also
    later = core.list_contract(core.CONTACTS, q="Sharath", near="also", page=2)
    assert later.near is None and later.also is None
    assert core.list_contract(core.CONTACTS, q="Sharath").near is None


def test_a_deal_search_needs_the_related_hits() -> None:
    with pytest.raises(ValueError, match="related_hits"):
        core.search_where(core.DEALS, "Sharath")
    # No q: nothing to read, so no hits are needed.
    assert core.search_where(core.DEALS, "") == (None, {})
    assert core.list_contract(core.DEALS).where == ""


def test_the_deal_predicate_holds_no_sub_select_and_no_caller_text() -> None:
    q = "Sharat; DROP crm_deals"
    hits = core.RelatedHits((
        {"company_id": ("c1",), "id": ("d1", "d2")},
        {"lead_id": ("l1",)},
        {},
    ))
    clause, params = core.search_where(core.DEALS, q, related=hits)
    assert "SELECT" not in clause.upper()
    assert "Sharat" not in clause and "DROP" not in clause
    assert "id = ANY(CAST(:r0_id AS uuid[]))" in clause
    assert "company_id = ANY(CAST(:r0_company_id AS uuid[]))" in clause
    assert "lead_id = ANY(CAST(:r1_lead_id AS uuid[]))" in clause
    assert params["r0_id"] == ["d1", "d2"] and params["q0"] == "%Sharat;%"
    # Three words, three groups joined by AND.
    assert clause.count(" AND ") == 2


def test_a_word_with_no_hit_keeps_its_own_columns() -> None:
    clause, _params = core.search_where(core.DEALS, "Sharath", related=_hits("Sharath"))
    assert clause == "((name ILIKE :q0 OR next_step ILIKE :q0))"
