"""SW-45 — ``claim_scope.check_claim_scope`` with no database.

Spec: ``project-docs/specs/agent_coordination.md`` §3.10.1 and SW-45.

The R8 file ``test_agent_claim_scope_r8.py`` proves the passes on a real
database. This file pins the order and the modes of the one check, with a
fake ``app_owner`` and a fake session that answers the two reads.

Mutations (R7). ``C`` is ``apps/services/gateway/gateway/routes/projects/claim_scope.py``.

* K1. ``C`` ``check_claim_scope`` drops the ``off`` test.
  ``test_off_passes_a_task_outside_the_apps``.
* K2. ``C`` ``check_claim_scope`` drops the ``holds_task`` pass.
  ``test_a_task_that_holds_the_member_passes_in_refuse``.
* K3. ``C`` ``check_claim_scope`` treats ``refuse`` as ``warn``.
  ``test_refuse_raises_403_outside_the_apps``.
* K4. ``C`` ``check_claim_scope`` drops the ``made_by_member`` pass.
  ``test_a_task_the_member_created_passes_with_no_read``.
* K5. ``C`` ``queued_by_member`` binds a connection or a member that is not
  the session's. ``test_the_queued_read_binds_the_session``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.projects import claim_scope

ORG = str(uuid.uuid4())
CONNECTION = str(uuid.uuid4())
MEMBER = "agent-member@claimcheck.test"
LEAD = "lead@claimcheck.test"
TASK_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Db:
    """A session that answers the queued run read and the personal tree walk.
    Each call is kept, so a test can count the reads."""

    def __init__(self, *, queued: bool = False, personal_owner: str | None = None) -> None:
        self.queued = queued
        self.personal_owner = personal_owner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(clause)
        self.calls.append((sql, dict(params or {})))
        if "pm_agent_runs" in sql:
            return _Result(SimpleNamespace(x=1) if self.queued else None)
        if "personal_owner" in sql:
            return _Result(SimpleNamespace(personal_owner=self.personal_owner)
                           if self.personal_owner else None)
        return _Result(None)


def _user(*, agent: bool = True) -> Any:
    scope = SimpleNamespace(connection_id=CONNECTION) if agent else None
    return SimpleNamespace(email=MEMBER, organization_id=ORG, agent=scope)


def _task(created_by: str = "owner@claimcheck.test") -> Any:
    return SimpleNamespace(id=TASK_ID, project_id=PROJECT_ID, created_by=created_by)


@pytest.fixture
def mode(monkeypatch: pytest.MonkeyPatch):
    def set_(value: str) -> None:
        monkeypatch.setattr(get_settings(), "agent_claim_scope_modes",
                            f"{ORG}={value}" if value else "")
    return set_


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake ``app_owner``. ``own`` False and the lead ``LEAD`` by default."""
    state: dict[str, Any] = {"own": False, "lead": LEAD, "calls": 0}

    async def fake(db: Any, user: Any, project_id: Any, vis: Any = None):
        state["calls"] += 1
        return state["lead"], state["own"]

    monkeypatch.setattr(claim_scope, "app_owner", fake)
    return state


async def _check(db: _Db, *, user: Any = None, task: Any = None,
                 current: set[str] | None = None) -> Any:
    return await claim_scope.check_claim_scope(
        db, user or _user(), task or _task(), current or set(), vis=None)


async def test_off_passes_a_task_outside_the_apps(mode, owner) -> None:
    mode("off")
    db = _Db()
    assert await _check(db) is None
    assert db.calls == [] and owner["calls"] == 0


async def test_warn_gives_the_lead_outside_the_apps(mode, owner) -> None:
    mode("warn")
    assert await _check(_Db()) == {"lead": LEAD}


async def test_refuse_raises_403_outside_the_apps(mode, owner) -> None:
    mode("refuse")
    with pytest.raises(HTTPException) as refused:
        await _check(_Db())
    assert refused.value.status_code == 403
    assert refused.value.detail["error"] == claim_scope.OUTSIDE_APPS
    assert refused.value.detail["lead"] == LEAD


async def test_a_task_that_holds_the_member_passes_in_refuse(mode, owner) -> None:
    mode("refuse")
    db = _Db()
    assert await _check(db, current={MEMBER.upper()}) is None
    assert db.calls == [] and owner["calls"] == 0
    with pytest.raises(HTTPException):
        await _check(_Db(), current={"agent:x", "other@claimcheck.test"})


async def test_a_member_hold_is_never_checked(mode, owner) -> None:
    mode("refuse")
    assert await _check(_Db(), user=_user(agent=False)) is None
    assert owner["calls"] == 0


async def test_a_task_the_member_created_passes_with_no_read(mode, owner) -> None:
    mode("refuse")
    db = _Db()
    assert await _check(db, task=_task(created_by=f"  {MEMBER.upper()} ")) is None
    assert db.calls == [] and owner["calls"] == 0


async def test_a_queued_run_passes_before_the_app_walk(mode, owner) -> None:
    mode("refuse")
    db = _Db(queued=True)
    assert await _check(db) is None
    assert owner["calls"] == 0 and len(db.calls) == 1


async def test_the_queued_read_binds_the_session(mode, owner) -> None:
    mode("warn")
    db = _Db()
    await _check(db)
    [(sql, binds)] = [c for c in db.calls if "pm_agent_runs" in c[0]]
    assert binds["tid"] == TASK_ID and binds["me"] == MEMBER
    assert binds["cid"] == CONNECTION
    assert set(binds["queued"]) == {"created", "in_progress", "awaiting"}
    assert "started_by" in sql and "connection_id" in sql


async def test_the_own_personal_tree_passes_in_refuse(mode, owner) -> None:
    mode("refuse")
    assert await _check(_Db(personal_owner=MEMBER)) is None
    with pytest.raises(HTTPException):
        await _check(_Db(personal_owner="other@claimcheck.test"))


async def test_an_own_app_passes_with_no_tree_walk(mode, owner) -> None:
    mode("refuse")
    owner["own"] = True
    db = _Db()
    assert await _check(db) is None
    assert not [c for c in db.calls if "personal_owner" in c[0]]
