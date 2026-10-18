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
* D-M9 ``apply_pull_request`` runs the rule for ``merged`` only, not for
  ``closed`` -> ``test_d5b_*``.

Fix round 1 (2026-10-18). Each new rule and its mutation, each one red when run:

* **Item 1** a lazy read of ``GET /refs`` that finds ``merged`` moves the task,
  after a ``closed`` delivery was lost. 1-M1 ``_refresh_pulls_as_app`` calls
  ``write_pull_request`` and not ``apply_pull_request``; 1-M2
  ``_app_installation`` answers ``None`` (the old TR-1 write) ->
  ``test_item1_a_lazy_read_that_finds_merged_moves_the_task``.
* **Item 5** an open and a merge in one second. 5-M1 the write needs ``<``
  again -> ``test_item5_an_open_and_a_merge_in_one_second_move_the_task``.
  5-M2 the same-second tie-break dropped ->
  ``test_item5_a_late_open_at_the_same_second_does_not_undo_a_merge``.
* **Item 6** an unreadable reference. 6-M1 ``_blocking`` ignores
  ``unreadable_since`` -> ``test_item6_an_unreadable_ref_stops_blocking_after_the_bound``.
  6-M2 no ``github.merge.blocked_by`` log line -> the same test.
  6-M3 ``UNREADABLE_AFTER = timedelta(0)`` -> the same test (a fresh refusal
  must still block).
* **F1** ``row.created_at > work.latest_at`` replaced by ``True`` ->
  ``test_f1_a_persons_older_move_does_not_keep_the_task``.
* **F2** ``created_at < :aw_before`` dropped from ``_MCP_WHERE`` ->
  ``test_f2_an_mcp_act_after_the_merge_does_not_count``.
* **F3** ``AGENT_WORKED_SQL`` built from its own copy with no MCP half ->
  ``test_f3_the_boolean_and_the_reason_agree``.

Fix round 2 (2026-10-18), each one red when run:

* **R2-1** the rule runs only on a move INTO a terminal state. R2-1-M1
  ``moved_into_terminal`` ignores ``old_state`` -> ``test_r2_1_a_terminal_reread_never_runs_the_rule_again``.
  R2-1-M2 ``record_refusal`` runs ``on_unblocked`` on every refused read past
  the bound (``crossed`` read as true) -> the same test.
* **R2-3** the order inside one second. R2-3-M1 ``event_rank`` gives 0 to a
  ``reopened`` event -> ``test_r2_3_a_close_and_a_reopen_in_one_second_end_open``.
  R2-3-M2 an equal stamp writes with no rank compare -> the same test (the
  late ``closed`` of the second case).

Fix round 3 (2026-10-18), red when run:

* **R3-1** a merge after a lost reopen runs the rule. R3-1-M1
  ``moved_into_terminal`` goes back to the TERMINAL check ->
  ``test_r3_1_a_merge_after_a_lost_reopen_moves_the_task``.
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
    from gateway import github_sync

    # Item 7: a short join window, so a trailing loop ends inside the test.
    monkeypatch.setattr(github_sync, "JOIN_WINDOW_S", 0.05)
    g.use_redis(monkeypatch)
    g.clear_github(p)
    g.seed_github_row(p, p.org_a, INSTALL)
    events: list[tuple[str, dict]] = []

    async def _emit(name: str, payload: dict) -> None:
        from acb_common.db import current_tenant

        # The org that the event goes out under, after the commit.
        events.append((name, {**payload, "_bound_org": current_tenant()}))

    monkeypatch.setattr(github_merge, "emit", _emit)

    async def _no_github(request: httpx.Request) -> httpx.Response:
        # A checks job after an `opened` event must never reach the internet.
        return httpx.Response(404, json={})

    g.patch_github(monkeypatch, _no_github, REAL_CLIENT)

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


def _ref(
    w: Any, task: str, number: int, state: str = "open", *, updated_at: str | None = None,
    unreadable_ago: str | None = None,
) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "state, via, created_by, pr_updated_at, unreadable_since) VALUES "
            "(CAST(:o AS uuid), CAST(:t AS uuid), 'pull_request', 'github', :ref, :url, :s, "
            "'ui', :e, CAST(:u AS timestamptz), "
            "CASE WHEN CAST(:ua AS text) IS NULL THEN NULL "
            "     ELSE now() - CAST(:ua AS interval) END)"),
            {"o": w.p.org_a, "t": task, "ref": f"{REPO}#{number}",
             "url": f"https://github.com/{REPO}/pull/{number}", "s": state, "e": EMAIL,
             "u": updated_at, "ua": unreadable_ago})


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
    return await _event(w, "closed", number, merged=merged, delivery=delivery,
                        updated_at=updated_at)


async def _event(
    w: Any, action: str, number: int, *, merged: bool = True, delivery: str | None = None,
    updated_at: str | None = None, closed_state: bool | None = None,
) -> httpx.Response:
    from fastapi import FastAPI
    from gateway.routes.github import webhook

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    closed = action == "closed" or (closed_state is True)
    payload = {
        "action": action, "number": number, "installation": {"id": INSTALL},
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": number, "title": f"PR {number}",
            "state": "closed" if closed else "open", "merged": closed and merged,
            "merged_at": now if closed and merged else None, "merged_by": {"login": "octo"},
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
    # The events go out after the answer, under the org of the merge.
    assert {p["_bound_org"] for _, p in w.events} == {w.p.org_a}


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


# ── Fix round 1 ─────────────────────────────────────────────────────────────


async def test_f1_a_persons_older_move_does_not_keep_the_task(world) -> None:
    """A person moved the task BEFORE the agent worked it: the agent's run is
    newer, so the merge moves the task (§12.3 step 3)."""
    w = world
    task = _task(w)
    _act(w, task, "status_change", EMAIL, None, "3 hours")
    _run(w, task, "2 hours")
    _ref(w, task, 401)
    await _close(w, 401)
    assert _category(w, task) == "done"


async def test_f2_an_mcp_act_after_the_merge_does_not_count(world) -> None:
    """§12.2 counts a row made BEFORE the merge. An agent's comment one hour
    after the merge time makes no agent-worked task."""
    w = world
    task = _task(w)
    _act(w, task, "comment", EMAIL, "mcp:claude-code", "-1 hour")
    _ref(w, task, 402)
    await _close(w, 402)
    assert _category(w, task) == "in_progress"
    assert _moves(w, task) == []


async def test_f3_the_boolean_and_the_reason_agree(world) -> None:
    """``AGENT_WORKED_SQL`` (the import of EA-13e) and ``reason`` give the
    same answer on every case, the edge of ``:aw_before`` included."""
    from gateway.db import tenant_session
    from gateway.routes.projects import agent_worked

    w = world
    now = datetime.now(UTC)
    cases: dict[str, str] = {}
    cases["nothing"] = _task(w)
    cases["run"] = _task(w)
    _run(w, cases["run"], "2 hours")
    cases["run_later"] = _task(w)
    _run(w, cases["run_later"], "-2 hours")
    cases["mcp"] = _task(w)
    _act(w, cases["mcp"], "comment", EMAIL, "mcp:cursor", "1 hour")
    cases["mcp_later"] = _task(w)
    _act(w, cases["mcp_later"], "comment", EMAIL, "mcp:cursor", "-1 hour")
    cases["chat"] = _task(w)
    _act(w, cases["chat"], "comment", EMAIL, "chat:projects-assistant", "1 hour")
    cases["lookalike"] = _task(w)
    _act(w, cases["lookalike"], "comment", EMAIL, "mcpx:cursor", "1 hour")
    cases["edge"] = _task(w)
    edge_id = _act(w, cases["edge"], "comment", EMAIL, "mcp:cursor", "30 minutes")
    edge_at = g.scalar(w.p, "SELECT created_at FROM pm_activities WHERE id = CAST(:i AS uuid)",
                       i=edge_id)
    expected = {"nothing": False, "run": True, "run_later": False, "mcp": True,
                "mcp_later": False, "chat": False, "lookalike": False, "edge": False}
    got: dict[str, tuple[bool, bool]] = {}
    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_a) as db:
        for name, task in cases.items():
            before = edge_at if name == "edge" else now
            boolean = (await db.execute(text(f"SELECT {agent_worked.AGENT_WORKED_SQL} AS v"),
                                        agent_worked.params(task, before))).scalar_one()
            why = await agent_worked.reason(db, task, before)
            got[name] = (bool(boolean), why is not None)
    assert {k: v[0] for k, v in got.items()} == expected
    assert all(a == b for a, b in got.values()), got


async def test_item1_a_lazy_read_that_finds_merged_moves_the_task(world, monkeypatch) -> None:
    """The ``closed`` delivery was lost in a restart. A member opens the task,
    the lazy read finds ``merged``, and the agent's task moves (one write path)."""
    from acb_auth import UserContext, UserRole, build_access
    from gateway.routes import projects as projects_package

    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 403)
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_lazy",
                                             "expires_at": "2099-01-01T00:00:00Z"})
        if request.url.path == f"/repos/{REPO}/pulls/403":
            return httpx.Response(200, json={
                "number": 403, "title": "PR 403", "state": "closed", "merged": True,
                "merged_at": stamp, "updated_at": stamp, "draft": False,
                "merged_by": {"login": "octo"}, "head": {"ref": "agent/x", "sha": "e" * 40}})
        return httpx.Response(404, json={})

    g.patch_github(monkeypatch, handler, REAL_CLIENT)
    member = UserContext(email=EMAIL, role=UserRole.EMPLOYEE, organization_id=w.p.org_a,
                         access=build_access(["feature:projects", "data:org:read"]))
    resp = await g.call(w.p, member, [projects_package.router], "GET",
                        f"/projects/tasks/{task}/refs", real_client=REAL_CLIENT)
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["rows"]
    assert row["state"] == "merged" and row["head_sha"] == "e" * 40
    assert _category(w, task) == "done"
    assert [n for n, p in w.events if p.get("task_id") == task] == [
        "pm.task.updated", "pm.task.status_changed"]


async def test_item5_an_open_and_a_merge_in_one_second_move_the_task(world) -> None:
    """GitHub stamps whole seconds. The ``opened`` and the ``closed`` of one
    second carry one ``updated_at``, and the close still writes."""
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 404)
    same = "2026-10-18T12:00:00Z"
    await _event(w, "opened", 404, updated_at=same)
    assert g.scalar(w.p, "SELECT state FROM pm_task_refs WHERE ref = :r",
                    r=f"{REPO}#404") == "open"
    await _close(w, 404, updated_at=same)
    assert _category(w, task) == "done"


async def test_item5_a_late_open_at_the_same_second_does_not_undo_a_merge(world) -> None:
    w = world
    task = _task(w, "done")
    same = "2026-10-18T12:00:00Z"
    _ref(w, task, 405, "merged", updated_at=same)
    await _event(w, "synchronize", 405, updated_at=same)
    assert g.scalar(w.p, "SELECT state FROM pm_task_refs WHERE ref = :r",
                    r=f"{REPO}#405") == "merged"


async def test_item6_an_unreadable_ref_stops_blocking_after_the_bound(world, monkeypatch) -> None:
    """A pull request that the App cannot read (a token-era one, or one outside
    the installation) blocks for a short refusal, names itself in the log,
    and stops blocking after ``UNREADABLE_AFTER``."""
    from gateway.routes.projects import github_merge

    lines: list[tuple[str, dict]] = []

    class _Log:
        def info(self, event: str, **kw: Any) -> None:
            lines.append((event, kw))

        warning = info

    monkeypatch.setattr(github_merge, "_log", _Log())
    w = world
    fresh = _task(w)
    _run(w, fresh)
    _ref(w, fresh, 406, "open", unreadable_ago="10 minutes")
    _ref(w, fresh, 407)
    await _close(w, 407)
    assert _category(w, fresh) == "in_progress"
    blocked = [kw for e, kw in lines if e == "github.merge.blocked_by"]
    assert blocked and blocked[0]["refs"] == [f"{REPO}#406"]
    old = _task(w)
    _run(w, old)
    _ref(w, old, 408, "open", unreadable_ago="2 hours")
    _ref(w, old, 409)
    await _close(w, 409)
    assert _category(w, old) == "done"


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def _successors(w: Any, task: str) -> int:
    return int(g.scalar(w.p, "SELECT count(*) FROM pm_tasks WHERE recurrence_id = ("
                             "SELECT recurrence_id FROM pm_tasks WHERE id = CAST(:t AS uuid))",
                        t=task))


def _move_back(w: Any, task: str) -> None:
    """The agent moves the task back to In progress after the merge."""
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid), completed_at = NULL "
                       "WHERE id = CAST(:t AS uuid)"),
                  {"s": w.lanes["in_progress"], "t": task})
    _act(w, task, "status_change", EMAIL, "mcp:claude-code", "0 seconds")


async def test_r2_1_a_terminal_reread_never_runs_the_rule_again(world, monkeypatch) -> None:
    """Merged, Done, then the agent moves the task back. A lazy re-read, an
    ``edited`` event and a refused read past the bound each leave it In
    progress, and no new successor appears."""
    from acb_auth import UserContext, UserRole, build_access
    from gateway import github_sync
    from gateway.db import tenant_session
    from gateway.routes import projects as projects_package

    w = world
    task = _task(w, recurring=True)
    _run(w, task)
    _ref(w, task, 801)
    _ref(w, task, 802, "open", unreadable_ago="3 hours")
    with w.p.admin_engine.begin() as c:  # 802 crossed the bound on an earlier read
        c.execute(text("UPDATE pm_task_refs SET unblocked_at = now() - interval '1 hour' "
                       "WHERE ref = :r"), {"r": f"{REPO}#802"})
    await _close(w, 801, updated_at="2026-10-18T09:00:00Z")
    assert _category(w, task) == "done"
    successors = _successors(w, task)
    _move_back(w, task)

    # 1. The lazy read of GET /refs reads the merged pull request again.
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET fetched_at = now() - interval '1 hour' "
                       "WHERE ref = :r"), {"r": f"{REPO}#801"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_r2", "expires_at": "2099-01-01T00:00:00Z"})
        if request.url.path == f"/repos/{REPO}/pulls/801":
            return httpx.Response(200, json={
                "number": 801, "title": "PR 801", "state": "closed", "merged": True,
                "merged_at": "2026-10-18T09:00:00Z", "updated_at": "2026-10-18T09:30:00Z",
                "draft": False, "merged_by": {"login": "octo"},
                "head": {"ref": "agent/x", "sha": "d" * 40}})
        return httpx.Response(404, json={})

    g.patch_github(monkeypatch, handler, REAL_CLIENT)
    member = UserContext(email=EMAIL, role=UserRole.EMPLOYEE, organization_id=w.p.org_a,
                         access=build_access(["feature:projects", "data:org:read"]))
    resp = await g.call(w.p, member, [projects_package.router], "GET",
                        f"/projects/tasks/{task}/refs", real_client=REAL_CLIENT)
    assert resp.status_code == 200, resp.text
    assert g.scalar(w.p, "SELECT pr_updated_at = TIMESTAMPTZ '2026-10-18 09:30:00+00' "
                         "FROM pm_task_refs WHERE ref = :r", r=f"{REPO}#801") is True,         "the re-read must WRITE, or the test proves nothing"
    assert _category(w, task) == "in_progress", "the lazy re-read ran the rule again"

    # 2. An `edited` event of the merged pull request.
    await _event(w, "edited", 801, updated_at="2026-10-18T10:00:00Z", closed_state=True)
    assert _category(w, task) == "in_progress", "an edited event ran the rule again"

    # 3. A refused read past the bound, after the crossing was handled.
    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_a) as db:
        outcome = await github_sync.record_refusal(db, w.p.org_a, f"{REPO}#802")
    assert outcome is None
    assert _category(w, task) == "in_progress", "a refused read ran the rule again"
    assert _successors(w, task) == successors
    assert len(_moves(w, task)) == 1


async def test_r2_3_a_close_and_a_reopen_in_one_second_end_open(world) -> None:
    w = world
    task = _task(w)
    same = "2026-10-18T12:00:00Z"
    _ref(w, task, 811)
    await _event(w, "opened", 811, updated_at=same)
    await _close(w, 811, merged=False, updated_at=same)
    await _event(w, "reopened", 811, updated_at=same)
    state = "SELECT state FROM pm_task_refs WHERE ref = :r"
    assert g.scalar(w.p, state, r=f"{REPO}#811") == "open", "close then reopen ends open"
    # The other order of delivery: the reopen comes first, the close late.
    other = _task(w)
    _ref(w, other, 812)
    await _event(w, "opened", 812, updated_at=same)
    await _event(w, "reopened", 812, updated_at=same)
    await _close(w, 812, merged=False, updated_at=same)
    assert g.scalar(w.p, state, r=f"{REPO}#812") == "open", "a late close cannot undo a reopen"
    # A merge, then a late `opened` of the same second, stays merged.
    merged = _task(w)
    _ref(w, merged, 813)
    await _close(w, 813, updated_at=same)
    await _event(w, "opened", 813, updated_at=same)
    assert g.scalar(w.p, state, r=f"{REPO}#813") == "merged"


# ── Fix round 3 ─────────────────────────────────────────────────────────────


async def test_r3_1_a_merge_after_a_lost_reopen_moves_the_task(world) -> None:
    """Open, then closed with no merge. The ``reopened`` delivery is lost.
    Then the pull request merges: the row goes closed -> merged, and the
    agent's task moves to Done."""
    w = world
    task = _task(w)
    _run(w, task)
    _ref(w, task, 821)
    await _event(w, "opened", 821, updated_at="2026-10-18T08:00:00Z")
    await _close(w, 821, merged=False, updated_at="2026-10-18T08:10:00Z")
    assert _category(w, task) == "in_progress", "a lone close moves nothing"
    # (the `reopened` of 08:20 never arrives)
    await _close(w, 821, merged=True, updated_at="2026-10-18T08:30:00Z")
    assert g.scalar(w.p, "SELECT state FROM pm_task_refs WHERE ref = :r",
                    r=f"{REPO}#821") == "merged"
    assert _category(w, task) == "done"
