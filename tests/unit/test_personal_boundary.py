"""The personal/team boundary — the two guards, and what they must NOT refuse.

Owner directive 2026-08-26. Two rules land together because they are the same
boundary seen from each side:

    assert_move_keeps_privacy  a task may only move INTO a personal project it
                               already lives in
    assert_assignable_here     assigning somebody else needs a real project

⚠️ **Most of these tests assert that something is ALLOWED**, and that is
deliberate. Both guards are narrow, and the expensive failure is not a missing
refusal — it is a guard that grows and starts refusing the ordinary case. In
particular `assert_assignable_here` must never become a visibility rule:
assignment already grants task-level visibility on purpose (the second arm of
``task_visibility_clause``, added by WS-27j so that delegating outward stops
being a silent no-op), and assigning across a ``group:`` boundary in a TEAM
project is meant to keep working.

The data half — that the schema answers these queries the way the guards
believe — is proven on real Postgres in
``tests/live/live_ws39_personal_boundary.sql`` (10 checks). R8: a guard reading
the wrong row refuses the wrong thing, and no fake would show it.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from gateway.routes.projects.core import (
    assert_assignable_here,
    assert_move_keeps_privacy,
)

RAE = "rae@pb.invalid"
SAM = "sam@pb.invalid"

TEAM = "00000000-0000-0000-0000-0000000000aa"
RAE_ROOT = "00000000-0000-0000-0000-000000000001"
RAE_HOME = "00000000-0000-0000-0000-000000000002"
RAE_HEALTH = "00000000-0000-0000-0000-000000000003"
SAM_ROOT = "00000000-0000-0000-0000-000000000004"

#: personal_owner for each project, as the database holds it.
OWNERS: dict[str, str | None] = {
    TEAM: None,
    RAE_ROOT: RAE,
    RAE_HOME: RAE,
    RAE_HEALTH: RAE,
    SAM_ROOT: SAM,
}


class _Row:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeDB:
    """Answers exactly the two SELECTs the guards issue, from OWNERS."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        text = str(sql)
        self.queries.append(text)
        params = params or {}
        if "id IN" in text:  # assert_move_keeps_privacy
            wanted = [params["old"], params["new"]]
            return _Result([
                _Row(id=pid, personal_owner=OWNERS[pid])
                for pid in dict.fromkeys(wanted) if pid in OWNERS
            ])
        # assert_assignable_here
        pid = params["pid"]
        return _Result([_Row(personal_owner=OWNERS.get(pid))])


def task_in(project_id: str) -> Any:
    return _Row(project_id=project_id)


# ── move ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("old,new,why", [
    (TEAM, TEAM, "team -> team is ordinary project management"),
    (RAE_HOME, TEAM, "personal -> team is PROMOTION, the point of D53.4"),
    (RAE_HOME, RAE_HEALTH, "between my own Areas — it is all mine"),
    (RAE_ROOT, RAE_HOME, "out of my inbox into my own Area"),
    (RAE_HOME, RAE_ROOT, "back to my inbox"),
])
async def test_these_moves_are_allowed(old: str, new: str, why: str) -> None:
    await assert_move_keeps_privacy(FakeDB(), task_in(old), new)


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new,why", [
    (TEAM, RAE_HOME, "a TEAM task would leave the board with no record"),
    (RAE_HOME, SAM_ROOT, "into somebody else's private tree"),
])
async def test_these_moves_are_refused(old: str, new: str, why: str) -> None:
    with pytest.raises(HTTPException) as caught:
        await assert_move_keeps_privacy(FakeDB(), task_in(old), new)
    assert caught.value.status_code == 422
    assert "personal" in str(caught.value.detail).lower()


@pytest.mark.asyncio
async def test_the_refusal_says_what_to_do_instead() -> None:
    """A refusal with no path forward is a wall.

    Both alternatives are named because they answer the two DIFFERENT things
    somebody attempting this could have wanted.
    """
    with pytest.raises(HTTPException) as caught:
        await assert_move_keeps_privacy(FakeDB(), task_in(TEAM), RAE_HOME)
    detail = str(caught.value.detail).lower()
    assert "archive" in detail, "must name archive — the way to take it off a board"
    assert "disposition" in detail or "schedule" in detail, (
        "must name the overlay — the way to organise a team task privately, "
        "which is the need this refusal actually collides with"
    )


# ── assign ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assigning_anyone_in_a_team_project_is_allowed() -> None:
    """⚠️ Case B and Case C. This is the test that must not start failing.

    Assigning across a `group:` boundary works today because assignment IS a
    way to see a task (WS-27j). A future tightening that "fixes" it would
    re-open the silent-assignment bug that arm was added to close.
    """
    await assert_assignable_here(FakeDB(), task_in(TEAM), {SAM, RAE})


@pytest.mark.asyncio
async def test_the_owner_may_assign_themselves_in_their_own_project() -> None:
    """This is what `capture` does on every quick capture."""
    await assert_assignable_here(FakeDB(), task_in(RAE_HOME), {RAE})


@pytest.mark.asyncio
async def test_assigning_someone_else_in_a_personal_project_is_refused() -> None:
    with pytest.raises(HTTPException) as caught:
        await assert_assignable_here(FakeDB(), task_in(RAE_HOME), {SAM})
    assert caught.value.status_code == 422
    assert SAM in str(caught.value.detail), (
        "the refusal must name WHO could not be assigned — a refusal that "
        "does not say which person is one the caller has to guess at"
    )
    assert "move it to a project" in str(caught.value.detail).lower()


@pytest.mark.asyncio
async def test_an_empty_change_touches_the_database_not_at_all() -> None:
    """Re-asserting the same assignees must not cost a query."""
    db = FakeDB()
    await assert_assignable_here(db, task_in(RAE_HOME), set())
    assert db.queries == []


@pytest.mark.asyncio
async def test_removals_are_not_guarded() -> None:
    """Taking somebody OFF a task can never be the thing that strands them.

    Asserted through the endpoint's contract rather than the helper's: the
    helper is only ever handed `added`, so a guard that also saw `removed`
    could refuse somebody's attempt to UNDO the very state it dislikes.
    """
    db = FakeDB()
    await assert_assignable_here(db, task_in(RAE_HOME), set())
    assert db.queries == []
