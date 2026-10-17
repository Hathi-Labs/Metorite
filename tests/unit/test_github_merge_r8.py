"""R8 — merge-to-Done for a task that an agent worked (WS-54 GH-6, G-4).

Spec: ``project-docs/specs/github_integration.md`` §12. A real Postgres (the
promoted phase-4 catalog, as the NON-privileged role), the real webhook, the
real ``apply_status_transition``. Each item below is a test:

* **D1** an agent-run task in review moves to the done lane of its own
  project, with ``completed_at``.
* **D2** a task with no run moves when an MCP act exists: a claim, or only a
  comment through MCP.
* **D3** a task with no run and no MCP act does not move.
* **D4** a person's later status move keeps the task.
* **D5** an open second pull request keeps the task, and its merge then moves it.
* **D5b** when the first pull request merged and the second closes without a
  merge, the close moves the task. A task whose only pull request closes
  without a merge does not move.
* **D6** a cancelled, done, triage or archived task does not move.
* **D7** a recurring task makes exactly one successor.
* **D8** the ``status_change`` row holds ``created_by = system:github`` and
  ``meta.via = github``, and one ``system`` row holds the reason.
* **D9** a second delivery moves nothing.
* **D10** open subtasks stay open.
* **D11** ``core.py`` has no diff.
* **D12** a person's later move through the chat (``chat:projects-assistant``)
  keeps the task.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* D-M1 skip step 2 (``work is None`` moves anyway with a stub) -> ``test_d3_*``.
* D-M2 ``AGENT_WORKED_SQL`` and the reason read drop the MCP half -> ``test_d2_*``.
* D-M3 ``_person_took_it_back`` returns False -> ``test_d4_*`` and ``test_d12_*``.
* D-M4 the chat via counts as a machine via -> ``test_d12_*``.
* D-M5 step 4 drops the open check -> ``test_d5_*``.
* D-M6 step 4 drops the ``merged`` check -> ``test_d5b_*``.
* D-M7 step 1 drops the closing categories -> ``test_d6_*``.
* D-M8 no ``ACTOR_VIA`` set -> ``test_d8_*``.
* D-M9 the webhook skips the merge rule on a close with no merge -> ``test_d5b_*``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit import _github_r8 as g
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient
SECRET = "r8-webhook-secret"
INSTALL = 8101
REPO = "acme/widgets"
EMAIL = "dev@gh6-r8.test"
_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def p(promoted):  # noqa: F811
    g.grant_lookup(promoted)
    return promoted


@pytest.fixture
def world(p, monkeypatch, tmp_path):
    from gateway.routes.projects import github_merge

    g.configure_app(monkeypatch, g.make_private_key(tmp_path))
    g.use_redis(monkeypatch)
    g.clear_github(p)
    g.seed_github_row(p, p.org_a, INSTALL)
    events: list[tuple[str, dict]] = []

    async def _emit(name: str, payload: dict) -> None:
        events.append((name, payload))

    monkeypatch.setattr(github_merge, "emit", _emit)

    class W:
        pass

    w = W()
    w.p, w.events = p, events
    w.project, w.lanes = g.seed_project(p, p.org_a, EMAIL)
    yield w
    g.clear_github(p)


# ── Seeds ───────────────────────────────────────────────────────────────────


def _task(w: Any, category: str = "in_progress", **kwargs: Any) -> str:
    return g.seed_task(w.p, w.p.org_a, EMAIL, project=w.project, lanes=w.lanes,
                       category=category, **kwargs)


def _run(w: Any, task: str, ago: str = "2 hours") -> str:
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            ":e, 'mcp:claude-code', 'completed', :e, now() - CAST(:ago AS interval)) "
            "RETURNING id"), {"o": w.p.org_a, "t": task, "e": EMAIL, "ago": ago}).scalar_one())


def _act(w: Any, task: str, kind: str, by: str, via: str | None, ago: str) -> str:
    meta = {"via": via} if via else {}
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, meta, "
            "body, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :k, :b, "
            "CAST(:m AS jsonb), 'seed', now() - CAST(:ago AS interval)) RETURNING id"),
            {"o": w.p.org_a, "t": task, "k": kind, "b": by, "m": json.dumps(meta),
             "ago": ago}).scalar_one())


def _ref(w: Any, task: str, number: int, state: str = "open") -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "state, via, created_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            "'pull_request', 'github', :ref, :url, :s, 'ui', :e)"),
            {"o": w.p.org_a, "t": task, "ref": f"{REPO}#{number}",
             "url": f"https://github.com/{REPO}/pull/{number}", "s": state, "e": EMAIL})


def _category(w: Any, task: str) -> str:
    return str(g.scalar(w.p, "SELECT s.category FROM pm_tasks t JOIN pm_task_statuses s "
                             "ON s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=task))


def _moves(w: Any, task: str) -> list[Any]:
    return g.rows(w.p, "SELECT created_by, meta FROM pm_activities WHERE task_id = "
                       "CAST(:t AS uuid) AND type = 'status_change' AND created_by = "
                       "'system:github'", t=task)


async def _close(
    w: Any, number: int, *, merged: bool = True, delivery: str | None = None,
    updated_at: str | None = None,
) -> httpx.Response:
    from fastapi import FastAPI
    from gateway.routes.github import webhook

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = {
        "action": "closed", "number": number, "installation": {"id": INSTALL},
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": number, "title": f"PR {number}", "state": "closed", "merged": merged,
            "merged_at": now if merged else None, "merged_by": {"login": "octo"},
            "draft": False, "updated_at": updated_at or now, "body": "",
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "author_association": "MEMBER",
            "head": {"ref": "agent/x", "sha": "d" * 40, "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        },
    }
    raw = json.dumps(payload).encode()
    app = FastAPI()
    app.include_router(webhook.router)
    async with tenant_engine_scope(g.app_dsn(w.p)), REAL_CLIENT(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
    ) as client:
        resp = await client.post("/webhooks/github", content=raw, headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": "sha256=" + hmac.new(
                SECRET.encode(), raw, hashlib.sha256).hexdigest(),
        })
    assert resp.status_code == 200, resp.text
    return resp


# ── D1, D2, D3 ──────────────────────────────────────────────────────────────


async def test_d1_an_agent_run_task_moves_to_its_done_lane(world) -> None:
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 21)
    await _close(w, 21)
    assert _category(w, task) == "done"
    assert g.scalar(w.p, "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                    t=task) == uuid.UUID(w.lanes["done"])
    assert g.scalar(w.p, "SELECT completed_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                    t=task) is not None
    names = [n for n, p in w.events if p.get("task_id") == task]
    assert names == ["pm.task.updated", "pm.task.status_changed"]


async def test_d2_an_mcp_act_with_no_run_moves_the_task(world) -> None:
    w = world
    claimed = _task(w)
    _act(w, claimed, "assignment", EMAIL, "mcp:claude-code", "1 hour")
    _ref(w, claimed, 22)
    commented = _task(w)
    _act(w, commented, "comment", EMAIL, "mcp:cursor", "1 hour")
    _ref(w, commented, 23)
    await _close(w, 22)
    await _close(w, 23)
    assert _category(w, claimed) == "done"
    assert _category(w, commented) == "done"


async def test_d3_a_task_that_people_run_does_not_move(world) -> None:
    w = world
    task = _task(w)
    _act(w, task, "comment", EMAIL, None, "1 hour")
    _act(w, task, "comment", EMAIL, "chat:projects-assistant", "1 hour")
    _ref(w, task, 24)
    await _close(w, 24)
    assert _category(w, task) == "in_progress"
    assert _moves(w, task) == [] and w.events == []


# ── D4, D12: a person took it back ──────────────────────────────────────────


async def test_d4_a_persons_later_move_keeps_the_task(world) -> None:
    w = world
    task = _task(w)
    _run(w, task, "2 hours")
    _act(w, task, "status_change", EMAIL, None, "10 minutes")
    _ref(w, task, 25)
    await _close(w, 25)
    assert _category(w, task) == "in_progress"


async def test_d12_a_persons_move_through_the_chat_keeps_the_task(world) -> None:
    w = world
    task = _task(w)
    _run(w, task, "2 hours")
    _act(w, task, "status_change", EMAIL, "chat:projects-assistant", "10 minutes")
    _ref(w, task, 26)
    await _close(w, 26)
    assert _category(w, task) == "in_progress"


async def test_an_agents_own_later_move_does_not_keep_the_task(world) -> None:
    w = world
    task = _task(w)
    _run(w, task, "2 hours")
    _act(w, task, "status_change", EMAIL, "mcp:claude-code", "10 minutes")
    _act(w, task, "status_change", "system:workflow:7", None, "5 minutes")
    _ref(w, task, 27)
    await _close(w, 27)
    assert _category(w, task) == "done"


# ── D5, D5b: the other pull requests ────────────────────────────────────────


async def test_d5_an_open_second_pull_request_keeps_the_task(world) -> None:
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 31, "open")
    _ref(w, task, 32, "open")
    await _close(w, 32)
    assert _category(w, task) == "in_progress"
    await _close(w, 31)
    assert _category(w, task) == "done"


async def test_d5b_a_close_after_a_merge_moves_and_a_lone_close_does_not(world) -> None:
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 41, "merged")
    _ref(w, task, 42, "open")
    await _close(w, 42, merged=False)
    assert _category(w, task) == "done"
    lone = _task(w)
    _run(w, lone)
    _ref(w, lone, 43, "open")
    await _close(w, 43, merged=False)
    assert _category(w, lone) == "in_progress"


# ── D6 ──────────────────────────────────────────────────────────────────────


async def test_d6_a_closed_held_or_archived_task_does_not_move(world) -> None:
    w = world
    tasks = {
        "cancelled": _task(w, "cancelled"),
        "done": _task(w, "done"),
        "triage": _task(w, "triage"),
        "archived": _task(w, archived=True),
    }
    for number, task in enumerate(tasks.values(), start=51):
        _run(w, task)
        _ref(w, task, number)
        await _close(w, number)
    assert _category(w, tasks["cancelled"]) == "cancelled"
    assert _category(w, tasks["triage"]) == "triage"
    assert _category(w, tasks["archived"]) == "in_progress"
    assert all(_moves(w, t) == [] for t in tasks.values())


# ── D7, D8, D9, D10 ─────────────────────────────────────────────────────────


async def test_d7_a_recurring_task_makes_exactly_one_successor(world) -> None:
    w = world
    task = _task(w, recurring=True)
    _run(w, task)
    _ref(w, task, 61)
    rule = g.scalar(w.p, "SELECT recurrence_id FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=task)
    await _close(w, 61)
    await _close(w, 61, updated_at="2099-01-01T00:00:00Z")
    count = g.scalar(w.p, "SELECT count(*) FROM pm_tasks WHERE recurrence_id = CAST(:r AS uuid)",
                     r=str(rule))
    assert count == 2


async def test_d8_the_timeline_names_github_and_the_reason(world) -> None:
    w = world
    task = _task(w)
    run = _run(w, task)
    _ref(w, task, 71)
    guid = str(uuid.uuid4())
    await _close(w, 71, delivery=guid)
    (move,) = _moves(w, task)
    assert move.meta["via"] == "github" and move.meta["automation"] is True
    assert (move.meta["from_category"], move.meta["to_category"]) == ("in_progress", "done")
    (system,) = g.rows(w.p, "SELECT body, meta FROM pm_activities WHERE task_id = "
                            "CAST(:t AS uuid) AND type = 'system' AND created_by = "
                            "'system:github'", t=task)
    assert system.body == f"GitHub moved this to Done after {REPO}#71 merged"
    assert system.meta["reason"] == "agent_run" and system.meta["agent_run_id"] == run
    assert system.meta["github"] == {"repo": REPO, "number": 71, "merged_by": "octo",
                                     "delivery": guid}
    assert system.meta["via"] == "github"


async def test_d9_a_second_delivery_moves_nothing(world) -> None:
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 72)
    await _close(w, 72)
    await _close(w, 72, updated_at="2099-01-01T00:00:00Z")  # a new GUID, a newer event
    assert len(_moves(w, task)) == 1


async def test_d10_open_subtasks_stay_open(world) -> None:
    w = world
    parent = _task(w)
    child = _task(w, "todo", parent=parent)
    _run(w, parent)
    _ref(w, parent, 73)
    await _close(w, 73)
    assert _category(w, parent) == "done"
    assert _category(w, child) == "todo"


async def test_an_open_agent_run_stays_open(world) -> None:
    w = world
    task = _task(w)
    with w.p.admin_engine.begin() as c:
        run = str(c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            ":e, 'mcp:claude-code', 'in_progress', :e, now() - interval '1 hour') "
            "RETURNING id"), {"o": w.p.org_a, "t": task, "e": EMAIL}).scalar_one())
    _ref(w, task, 74)
    await _close(w, 74)
    assert _category(w, task) == "done"
    assert g.scalar(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)",
                    r=run) == "in_progress"


# ── D11 ─────────────────────────────────────────────────────────────────────


def test_d11_core_py_has_no_diff() -> None:
    """The merge rule leaves ``core.py`` alone. With git and ``origin/main`` at
    hand the diff must be empty. Without them, the rule still reaches the core
    only through its public helpers."""
    from gateway.routes.projects import github_merge

    core = "apps/services/gateway/gateway/routes/projects/core.py"
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=_ROOT,
                          capture_output=True, text=True)
    if base.returncode == 0 and base.stdout.strip():
        diff = subprocess.run(["git", "diff", "--name-only", base.stdout.strip(), "--", core],
                              cwd=_ROOT, capture_output=True, text=True)
        assert diff.returncode == 0 and diff.stdout.strip() == "", diff.stdout
    source = Path(github_merge.__file__).read_text(encoding="utf-8")
    assert "apply_status_transition" in source and "load_default_status" in source
    # Nothing here patches the core at run time.
    assert not re.search(r"core\.\w+\s*=", source)
