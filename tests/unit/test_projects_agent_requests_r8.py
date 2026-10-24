"""WS-64 OP-6e: the chat lists the agent requests of the member against the real gateway (R8).

Spec: ``project-docs/specs/one_projects_app.md`` D-OP-13 and §9 OP-6e, and
``external_agent_access.md`` EA3-40.

``waiting_agent_requests`` runs through the skill's own client against the REAL
gateway app, on a promoted catalog of its own as a NON-privileged role. The
world is the one of ``test_projects_write_authority.py``. The runs are rows the
test seeds as the admin, in the shape the routes write.

What a fake cannot prove, and this file does:

* the member sees their own waiting request, with the task and the card link,
  and not the request of another member of the same organization;
* a run that finished, and a run that works, do not show;
* the list is newest first;
* the read writes nothing: the runs, the activities and the task keep their rows.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_agent_requests_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red. Commit before each.

* R1. ``agent_runs.py`` ``list_my_agent_runs``: drop the ``r.member_email = :me`` clause.
  ``test_r8_a_member_sees_their_own_request_and_not_another_members``.
* R2. ``agent_runs.py`` ``list_my_agent_runs``: drop the ``r.status = :status`` clause.
  ``test_r8_a_finished_run_and_a_working_run_do_not_show``.
* R3. ``reads.py`` ``waiting_agent_requests``: reverse the rows.
  ``test_r8_the_list_is_newest_first``.
* R4. ``reads.py`` ``waiting_agent_requests``: call ``post`` on the approval route.
  ``test_r8_the_read_writes_nothing``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_common import get_settings
from sqlalchemy import text

from tests.unit import test_mcp_edge_writes as w
from tests.unit.test_projects_write_authority import (  # noqa: F401 — the fixtures
    CREATOR,
    LEAD,
    _chat,
    _task,
    live,
    live_app,
    redis_fake,
    world,
)

pytestmark = w._DB_GATE

VIA = "mcp:claude.ai:claude-code"


@pytest.fixture(autouse=True)
def _runs_on(monkeypatch, live):  # noqa: F811
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with live.admin.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return live


def _seed(wd: Any, task: str, member: str, *, status: str = "awaiting", minutes: int = 5,
          body: str = "May I archive this?", archive: bool = True) -> str:
    """A run of *member* on *task*, with an open question when it awaits."""
    run = str(uuid.uuid4())
    with wd.admin.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (id, organization_id, task_id, member_email, client_via,"
            " status, started_by, created_at, last_activity_at)"
            " VALUES (CAST(:r AS uuid), CAST(:o AS uuid), CAST(:t AS uuid), :m, :via, :s, :via,"
            " now() - make_interval(mins => :age), now() - make_interval(mins => :age))"),
            {"r": run, "o": wd.org, "t": task, "m": member, "via": VIA, "s": status,
             "age": minutes})
        if status == "awaiting":
            meta = {"request": {"kind": "archive_task",
                                "expires_at": "2099-01-01T00:00:00+00:00"}} if archive else None
            c.execute(text(
                "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, type,"
                " content, content_metadata, actor, actor_via, created_at)"
                " VALUES (CAST(:o AS uuid), CAST(:r AS uuid), CAST(:t AS uuid), 'elicitation',"
                " CAST(:c AS jsonb), CAST(:m AS jsonb), :me, :via,"
                " now() - make_interval(mins => :age))"),
                {"o": wd.org, "r": run, "t": task, "c": json.dumps({"body": body}),
                 "m": json.dumps(meta) if meta else None, "me": member, "via": VIA,
                 "age": minutes})
    return run


def _counts(wd: Any) -> tuple[int, int, str]:
    with wd.admin.begin() as c:
        runs = c.execute(text("SELECT count(*) FROM pm_agent_runs")).scalar()
        acts = c.execute(text("SELECT count(*) FROM pm_agent_run_activities")).scalar()
        stamp = c.execute(text(
            "SELECT string_agg(id::text || coalesce(archived_at::text, '-')"
            " || updated_at::text, ',' ORDER BY id) FROM pm_tasks")).scalar()
    return int(runs), int(acts), str(stamp)


async def _list(wd: Any, monkeypatch, who: str) -> str:
    from skill_projects import reads

    return await _chat(wd, monkeypatch, who, reads.waiting_agent_requests)


async def test_r8_a_member_sees_their_own_request_and_not_another_members(
    _runs_on, monkeypatch,
) -> None:
    wd = _runs_on
    mine = _task(wd, wd.team, CREATOR, "OP6E mine")
    theirs = _task(wd, wd.team, LEAD, "OP6E theirs")
    _seed(wd, mine, CREATOR)
    _seed(wd, theirs, LEAD, body="Delete the lot?")
    out = await _list(wd, monkeypatch, CREATOR)
    assert "1 agent requests wait" in out, out
    assert "OP6E mine" in out and f"link: /projects?task={mine}" in out
    assert "asks to archive this task" in out
    assert "OP6E theirs" not in out and theirs not in out and "Delete the lot" not in out
    lead = await _list(wd, monkeypatch, LEAD)
    assert "OP6E theirs" in lead and "OP6E mine" not in lead, lead


async def test_r8_a_finished_run_and_a_working_run_do_not_show(_runs_on, monkeypatch) -> None:
    wd = _runs_on
    waiting = _task(wd, wd.team, CREATOR, "OP6E waits")
    done = _task(wd, wd.team, CREATOR, "OP6E done")
    busy = _task(wd, wd.team, CREATOR, "OP6E busy")
    _seed(wd, waiting, CREATOR)
    _seed(wd, done, CREATOR, status="completed")
    _seed(wd, busy, CREATOR, status="in_progress")
    out = await _list(wd, monkeypatch, CREATOR)
    assert "OP6E waits" in out
    assert "OP6E done" not in out and "OP6E busy" not in out, out


async def test_r8_the_list_is_newest_first(_runs_on, monkeypatch) -> None:
    wd = _runs_on
    old = _task(wd, wd.team, CREATOR, "OP6E old")
    new = _task(wd, wd.team, CREATOR, "OP6E new")
    _seed(wd, old, CREATOR, minutes=300, archive=False, body="Which lane?")
    _seed(wd, new, CREATOR, minutes=2)
    out = await _list(wd, monkeypatch, CREATOR)
    assert out.index("OP6E new") < out.index("OP6E old"), out
    assert "asks a question" in out and "5 hours ago" in out and "just now" in out


async def test_r8_the_read_writes_nothing(_runs_on, monkeypatch) -> None:
    wd = _runs_on
    task = _task(wd, wd.team, CREATOR, "OP6E quiet")
    run = _seed(wd, task, CREATOR)
    before = _counts(wd)
    out = await _list(wd, monkeypatch, CREATOR)
    assert "OP6E quiet" in out
    assert _counts(wd) == before
    with wd.admin.begin() as c:
        assert c.execute(text("SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)"),
                         {"r": run}).scalar() == "awaiting"
