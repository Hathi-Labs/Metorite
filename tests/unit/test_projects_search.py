"""WS-27r — the search surface, and the LIKE defect it uncovered.

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 10, §11.18.

The last row of the parity backlog. `?q=` has existed on the list endpoint
since WS-27a; what was missing was a way to reach it, and — it turned out — an
escape.

The claims worth pinning:

* **`_` and `%` are LITERAL when a human types them.** This was live: searching
  `task_id` also matched `taskXid`, because `_` is LIKE's single-character
  wildcard. In a workspace where people search for identifiers all day that is
  a steady drip of hits nobody asked for, and it looks like fuzzy matching
  rather than like a bug.
* **ranking happens in SQL, before the LIMIT.** Ranked afterwards, the best
  answer is only in the list if it was already inside the arbitrary fifty rows
  the database happened to return — which reads as "search is bad at long
  queries" rather than as a defect.
* **`#42` is a task number.** Getting every task whose description mentions 42
  is a search box that ignored what you typed.
* **a short TEXT query is a 422 that says what to type** (D-PM-31, built
  2026-10-06). The palette gates before it calls, so only a caller that
  skipped the gate sees it. A task number passes at any length, as an exact
  lookup.
* **search can never surface what the list would hide.** Same visibility
  clause, same archived rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import search as pm_search
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views
from gateway.routes.projects.filters import build_task_filters, like_escape
from gateway.routes.projects.search import MAX_HITS, MIN_QUERY, task_number

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    projects_user,
    silence_events,
)

MODULES = (
    pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me,
    pm_search,
)
USER = projects_user()
MEMBER = member_user("colleague@fracktal.in")

SOURCE = Path("apps/services/gateway/gateway/routes/projects/search.py")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


# ── like_escape — the defect ────────────────────────────────────────────────

def test_an_underscore_is_a_literal_not_a_wildcard() -> None:
    """⚠️ Live defect. `task_id` matched `taskXid` and `task-id`."""
    assert like_escape("task_id") == r"task\_id"


def test_a_percent_is_a_literal_too() -> None:
    """`50%` quietly meant `50`, so it matched `500` and `1502`."""
    assert like_escape("50%") == r"50\%"


def test_a_backslash_is_escaped_FIRST() -> None:
    """⚠️ Escaping the backslash last would double every backslash this
    function had just introduced, and `_` would come back out as a wildcard."""
    assert like_escape(r"a\_b") == r"a\\\_b"


def test_a_plain_term_is_left_completely_alone() -> None:
    assert like_escape("parser refactor") == "parser refactor"


@pytest.mark.parametrize("term", ["", "%", "_", "\\", "%%__\\\\"])
def test_escaping_is_total_no_metacharacter_survives_unescaped(term: str) -> None:
    """Every metacharacter in the output is preceded by a backslash. Written as
    a property rather than as four examples, because the failure mode is one
    character somebody forgot."""
    escaped = like_escape(term)
    for index, char in enumerate(escaped):
        if char in "%_":
            assert escaped[index - 1] == "\\", escaped


@pytest.mark.parametrize(
    ("term", "bound_pattern"),
    [
        ("task_id", r"%task\_id%"),
        ("50%", r"%50\%%"),
        ("back\\slash", r"%back\\slash%"),
        ("  padded  ", "%padded%"),
    ],
)
def test_the_list_endpoint_escapes_too_not_only_search(
    term: str, bound_pattern: str,
) -> None:
    """⚠️ The defect was on `build_task_filters`, which the board and every
    saved view read through. Fixing only the new endpoint would leave the bug
    exactly where people meet it.

    ⚠️ **This used to assert on the SOURCE TEXT of that one line** — and WS-27be
    had to reshape the line (a minimum-length branch went in above it), which
    broke a test whose subject had not changed at all. A fence that pins a
    spelling fails on refactors and passes on a rewrite that keeps the spelling
    and drops the behaviour. So it asserts on the BOUND VALUE instead: the
    pattern `build_task_filters` hands the database, which is the thing that was
    wrong.
    """
    _, params = build_task_filters(q=term)
    assert params["q"] == bound_pattern


# ── task_number ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#42", 42), ("42", 42), (" #42 ", 42), ("# 42", 42),
        ("parser", None), ("", None), ("#", None), ("4.2", None),
        ("-1", None), ("42a", None), ("²", None), ("#٧", None),  # noqa: RUF001
    ],
)
def test_a_task_number_is_recognised_only_when_it_is_one(raw, expected) -> None:
    assert task_number(raw) == expected


def test_an_absurdly_long_digit_string_is_a_phrase_not_a_number() -> None:
    """`task_number` is a BIGINT. An unbounded `int()` on user input is a parse
    nobody asked for, and the result could not be compared to the column."""
    assert task_number("9" * 40) is None


# ── The query, structurally ─────────────────────────────────────────────────

def test_ranking_happens_in_SQL_before_the_limit() -> None:
    """⚠️ Ranked in Python over a capped set, the best answer is only present
    if it was already inside the arbitrary fifty rows returned."""
    source = SOURCE.read_text(encoding="utf-8")
    query = re.search(r"_SEARCH_SQL = \"\"\"(.*?)\"\"\"", source, re.S)
    assert query is not None
    body = query.group(1)
    assert "ORDER BY rank" in body
    assert body.index("ORDER BY") < body.index("LIMIT")
    assert "OFFSET" not in body


def test_each_rank_is_paired_with_the_right_test() -> None:
    """⚠️ Asserting only that `THEN 0/1/2` appear in order is not enough: the
    tiers can be swapped by exchanging their WHEN clauses, leaving the THENs
    exactly where they were. A mutant did precisely that and survived. The
    pairing is the claim."""
    source = SOURCE.read_text(encoding="utf-8")
    query = re.search(r"CASE(.*?)END AS rank", source, re.S).group(1)
    assert re.search(r"t\.task_number = CAST\(:number AS bigint\) THEN 0", query)
    assert re.search(r"t\.title ILIKE :prefix THEN 1", query)
    assert re.search(r"t\.title ILIKE :term THEN 2", query)


def test_the_number_parameter_is_CAST_so_its_type_is_inferable() -> None:
    """⚠️ Found by the live run, invisible to every hermetic test.

    `:number IS NOT NULL` names no column, so Postgres has nothing to infer the
    parameter's type from and asyncpg answers `AmbiguousParameterError: could
    not determine data type of parameter $1` — the query never runs. A Python
    fake has no type system to be ambiguous about, so all 43 tests here passed
    with the cast missing. Structural, because that is the only level at which
    this suite can hold it.
    """
    source = SOURCE.read_text(encoding="utf-8")
    query = re.search(r"_SEARCH_SQL = \"\"\"(.*?)\"\"\"", source, re.S).group(1)
    for reference in re.finditer(r":number", query):
        window = query[max(0, reference.start() - 24):reference.end()]
        assert "CAST(" in window, f"bare :number at {reference.start()}"
    assert "AS bigint)" in query


def test_the_search_carries_the_visibility_clause() -> None:
    """⚠️ A new query is a new place to forget it, and search reaches EVERY
    project by design — so a missing clause here leaks the whole workspace."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "{visible}" in source
    assert "task_visibility_clause(vis)" in source


def test_the_search_hides_archived_tasks() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "t.archived_at IS NULL" in source


def test_the_cap_is_probed_by_one_so_truncation_is_a_fact() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"cap": cap + 1' in source


def test_search_writes_nothing() -> None:
    """A read surface. A POST here would be a second write path to something."""
    source = SOURCE.read_text(encoding="utf-8")
    assert not re.search(r"@router\.(post|patch|put|delete)", source)


def test_the_route_is_actually_mounted() -> None:
    from gateway.routes.projects import router

    assert "/projects/search" in {route.path for route in router.routes}


# ── Behaviour ───────────────────────────────────────────────────────────────

def _workspace(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Ops", subject="owner@fracktal.in")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    return project, todo


@pytest.mark.asyncio
async def test_a_title_match_comes_back_with_its_project_named(db, events):
    """A hit is useless without knowing where it lives — the palette has no
    tree to look it up in."""
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="Refactor the parser", task_number=7)

    result = await pm_search.search_tasks(q="parser", user=USER)

    assert [r["title"] for r in result["rows"]] == ["Refactor the parser"]
    assert result["rows"][0]["project_name"] == "Ops"
    assert result["rows"][0]["task_number"] == 7


@pytest.mark.asyncio
async def test_a_title_prefix_outranks_a_title_mention_outranks_a_description(
    db, events,
):
    """⚠️ The whole reason search is not the list endpoint. Somebody typing
    "parser" means the thing CALLED that, and a mutant that merely swapped the
    two title tiers survived until this fixture existed."""
    project, todo = _workspace(db)
    # `updated_at` is set explicitly because it is the SQL's tie-break, and a
    # fixture that left it to chance would assert whatever order the seeding
    # happened to produce.
    db.seed_task(project.id, todo.id, title="Older title mention of parser",
                 updated_at="2026-08-01T00:00:00Z")
    db.seed_task(project.id, todo.id, title="Newer title mention of parser",
                 updated_at="2026-08-05T00:00:00Z")
    db.seed_task(project.id, todo.id, title="Parser rewrite",
                 updated_at="2026-07-01T00:00:00Z")
    db.seed_task(project.id, todo.id, title="Beta unrelated",
                 description="the parser is mentioned here",
                 updated_at="2026-08-09T00:00:00Z")

    result = await pm_search.search_tasks(q="parser", user=USER)

    assert [(r["rank"], r["title"]) for r in result["rows"]] == [
        # Rank beats recency: the prefix hit is the OLDEST row here and still
        # comes first, so a mutant that sorted by date alone cannot pass.
        (1, "Parser rewrite"),
        (2, "Newer title mention of parser"),
        (2, "Older title mention of parser"),
        (3, "Beta unrelated"),
    ]


@pytest.mark.asyncio
async def test_the_exact_task_number_outranks_everything(db, events):
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="42 ways to refactor", task_number=1)
    db.seed_task(project.id, todo.id, title="Unrelated work", task_number=42)

    result = await pm_search.search_tasks(q="#42", user=USER)

    assert result["rows"][0]["title"] == "Unrelated work"
    assert result["rows"][0]["rank"] == 0


@pytest.mark.asyncio
async def test_an_underscore_does_not_match_any_character(db, events):
    """⚠️ The live defect, end to end. Before `like_escape`, searching for the
    identifier `task_id` also returned `taskXid`."""
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="Rename task_id everywhere")
    db.seed_task(project.id, todo.id, title="Rename taskXid everywhere")

    result = await pm_search.search_tasks(q="task_id", user=USER)

    assert [r["title"] for r in result["rows"]] == ["Rename task_id everywhere"]


@pytest.mark.asyncio
async def test_a_percent_sign_is_searched_for_literally(db, events):
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="Cut latency 50% by Friday")
    db.seed_task(project.id, todo.id, title="Ship 500 units")

    result = await pm_search.search_tasks(q="50%", user=USER)

    assert [r["title"] for r in result["rows"]] == ["Cut latency 50% by Friday"]


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["a", "ab", " qa "])
async def test_a_short_text_query_is_a_422_that_says_what_to_type(db, events, term):
    """D-PM-31. A text query under 3 characters cannot use the trigram index,
    so the route refuses it. The palette gates first (``search.ts``), so the
    422 reaches only a caller that skipped the gate. It costs no query."""
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="Anything")

    with pytest.raises(HTTPException) as refused:
        await pm_search.search_tasks(q=term, user=USER)

    assert refused.value.status_code == 422
    assert refused.value.detail == (
        f"Give at least 3 characters to search ('{term.strip()}' is"
        f" {len(term.strip())}). A task number works at any length: #7."
    )
    assert db.statements == []


@pytest.mark.asyncio
async def test_a_whitespace_only_query_is_not_a_search(db, events):
    result = await pm_search.search_tasks(q="    ", user=USER)
    assert result["rows"] == []
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_minimum_is_exactly_three_characters(db, events):
    _workspace(db)
    assert MIN_QUERY == 3
    await pm_search.search_tasks(q="abc", user=USER)
    assert db.statements != [], f"{MIN_QUERY} characters must reach the database"


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["#7", "7", " #7 ", "# 7"])
async def test_a_task_number_passes_at_any_length(db, events, term):
    """The owner's exception (2026-10-06). ``#7`` is two characters and is an
    exact lookup, so the minimum does not apply to it."""
    project, todo = _workspace(db)
    db.seed_task(project.id, todo.id, title="Unrelated work", task_number=7)
    db.seed_task(project.id, todo.id, title="Ship #7 of the batch", task_number=8)

    result = await pm_search.search_tasks(q=term, user=USER)

    assert [(r["task_number"], r["rank"]) for r in result["rows"]] == [(7, 0)]


@pytest.mark.asyncio
async def test_an_archived_task_is_not_findable(db, events):
    project, todo = _workspace(db)
    db.seed_task(
        project.id, todo.id, title="Old parser work",
        archived_at="2026-08-01T00:00:00Z",
    )

    assert (await pm_search.search_tasks(q="parser", user=USER))["rows"] == []


@pytest.mark.asyncio
async def test_search_cannot_reach_a_project_the_caller_lacks(db, events):
    """⚠️ Search spans EVERY project by design, which makes it the endpoint
    where a missing visibility clause leaks the most."""
    db.seed_project(name="Ops", subject="colleague@fracktal.in")
    secret = db.seed_project(name="Secret", subject=None)
    status = db.seed_status(secret.id, name="To do", category="todo")
    db.seed_task(secret.id, status.id, title="Confidential parser rewrite")

    result = await pm_search.search_tasks(q="parser", user=MEMBER)

    assert result["rows"] == []


@pytest.mark.asyncio
async def test_the_limit_is_clamped_to_the_maximum(db, events):
    """A client asking for ten thousand gets fifty, because the cap is the
    endpoint's promise rather than the caller's preference."""
    _workspace(db)
    await pm_search.search_tasks(q="parser", limit=10_000, user=USER)
    bound = next(args for stmt, args in db.calls if "AS rank" in stmt)
    assert bound["cap"] == MAX_HITS + 1


@pytest.mark.asyncio
async def test_a_nonsense_limit_still_asks_for_at_least_one(db, events):
    _workspace(db)
    await pm_search.search_tasks(q="parser", limit=0, user=USER)
    bound = next(args for stmt, args in db.calls if "AS rank" in stmt)
    assert bound["cap"] == 2


@pytest.mark.asyncio
async def test_the_bound_pattern_is_escaped_not_raw(db, events):
    """The end-to-end version of the escaping claim: what actually reaches the
    database has the metacharacter neutralised."""
    _workspace(db)
    await pm_search.search_tasks(q="task_id", user=USER)
    bound = next(args for stmt, args in db.calls if "AS rank" in stmt)
    assert bound["term"] == r"%task\_id%"
    assert bound["prefix"] == r"task\_id%"


@pytest.mark.asyncio
async def test_a_number_query_is_an_exact_lookup_and_never_a_text_scan(db, events):
    """D-PM-31's exception. A query that is only a number runs the EXACT arm.
    Without it, ``#7`` would be a two-character ILIKE, the scan D-PM-31
    exists to refuse."""
    _workspace(db)
    await pm_search.search_tasks(q="#42", user=USER)
    stmt, bound = next((s, a) for s, a in db.calls if "AS rank" in s)
    where = stmt.split("END AS rank", 1)[1]
    assert bound["number"] == 42
    assert "t.task_number = CAST(:number AS bigint)" in where
    assert "ILIKE" not in where


@pytest.mark.asyncio
async def test_a_word_query_runs_the_text_arm_only(db, events):
    _workspace(db)
    await pm_search.search_tasks(q="parser", user=USER)
    stmt = next(s for s, _a in db.calls if "AS rank" in s)
    where = stmt.split("END AS rank", 1)[1]
    assert "t.title ILIKE :term OR t.description ILIKE :term" in where
    assert "task_number" not in where


@pytest.mark.asyncio
async def test_a_word_query_binds_a_null_number(db, events):
    """NULL rather than absent: the statement references `:number` three times
    and a missing bind is a driver error, not a skipped clause."""
    _workspace(db)
    await pm_search.search_tasks(q="parser", user=USER)
    bound = next(args for stmt, args in db.calls if "AS rank" in stmt)
    assert bound["number"] is None
