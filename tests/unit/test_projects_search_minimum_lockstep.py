"""The search minimum, held equal in the route, the chat tool and the browser.

Spec: ``project-docs/specs/project_management_app.md`` D-PM-31 (owner,
2026-08-13; built 2026-10-06 with the owner's task-number exception).

The rule has three copies, because two of its readers cannot import the
third:

1. ``gateway/routes/projects/filters.py`` ``MIN_QUERY`` and ``task_number``:
   the route, and the one the other two mirror.
2. ``skill_projects/reads.py`` ``MIN_QUERY`` and ``_task_number``: the chat
   tool. The skill depends on httpx and acb-common only.
3. ``workbench/control_plane/src/app/projects/lib/search.ts`` ``MIN_QUERY``:
   the palette and the pickers. TypeScript, so it is read here as TEXT.

The defect this fences (WS-46 P3, PR #671): the tool asked for 3 while the
route took 2, so the tool refused "#7" for a task the route would find. A
copy that drifts fails here, not in front of a member.

The same file pins ``reads.MIN_DRAFT_TITLE`` to ``candidates.MIN_TITLE_CHARS``,
a second copy of the same kind that had no fence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from gateway.routes.projects import candidates, filters
from gateway.routes.projects import search as search_route
from skill_projects import reads

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "workbench" / "control_plane" / "src" / "app" / "projects" / "lib"
SEARCH_TS = LIB / "search.ts"

#: The ONE case table for the rule. ``search.test.ts`` checks the browser's
#: ``isTaskNumberQuery`` and ``isSearchableQuery`` against the same file, so
#: the browser, the route and the tool all answer to one list of inputs.
CASES = json.loads((LIB / "searchMinimumCases.json").read_text(encoding="utf-8"))
NUMBER_CASES = tuple(c["q"] for c in CASES)


def ts_min_query(source: str) -> int:
    """The value of ``export const MIN_QUERY = <n>;`` in a TypeScript source."""
    found = re.findall(r"^export const MIN_QUERY\s*=\s*(\d+)\s*;", source, re.M)
    assert len(found) == 1, f"expected one MIN_QUERY in search.ts, found {found}"
    return int(found[0])


def test_the_three_minimums_are_one_value() -> None:
    browser = ts_min_query(SEARCH_TS.read_text(encoding="utf-8"))
    assert filters.MIN_QUERY == 3, "D-PM-31 sets the text minimum at 3"
    assert reads.MIN_QUERY == filters.MIN_QUERY, "the chat tool drifted from the route"
    assert browser == filters.MIN_QUERY, "search.ts drifted from the route"
    assert search_route.MIN_QUERY is filters.MIN_QUERY


def test_the_reader_finds_a_drifted_browser_copy() -> None:
    """The mutation, kept in the suite: the same reader, handed a drifted
    copy, gives a value the first test would refuse."""
    drifted = "export const MIN_QUERY = 2;\n"
    assert ts_min_query(drifted) != filters.MIN_QUERY
    with pytest.raises(AssertionError):
        ts_min_query("// MIN_QUERY lives elsewhere now\n")


@pytest.mark.parametrize("raw", NUMBER_CASES)
def test_the_tool_and_the_route_agree_on_what_a_task_number_is(raw: str) -> None:
    assert reads._task_number(raw) == filters.task_number(raw)


@pytest.mark.parametrize("raw", NUMBER_CASES)
def test_the_tool_and_the_route_agree_on_what_is_too_short(raw: str) -> None:
    term = raw.strip()
    assert reads._short_text(term) == filters.short_text_query(term)


@pytest.mark.parametrize("case", CASES, ids=[repr(c["q"]) for c in CASES])
def test_the_route_answers_the_shared_case_table(case: dict) -> None:
    """The browser checks the same rows in ``search.test.ts``."""
    term = case["q"].strip()
    assert filters.task_number(case["q"]) == case["number"]
    assert (bool(term) and not filters.short_text_query(term)) is case["searchable"]


def test_the_tool_says_the_routes_sentence() -> None:
    """The route quotes the term and the tool fences it in «guillemets», as it
    fences all member text. Otherwise the two sentences are the same."""
    route_says = filters.short_query_message("ab").replace("'ab'", "«ab»")
    assert reads._short_query("ab") == route_says


def test_the_draft_title_minimum_is_the_routes() -> None:
    assert reads.MIN_DRAFT_TITLE == candidates.MIN_TITLE_CHARS
