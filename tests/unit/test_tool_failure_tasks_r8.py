"""TOF-2e — one task for each fingerprint, on a real database (R8).

Spec: ``project-docs/specs/tool_failure_log.md`` §5.4, §7 and §11 (D225).
Code: ``gateway/failures/sync.py``, ``gateway/build_info.build_committed_at``
and the job queue (``gateway/jobs/worker.py``).

The R8 tests run on a phase-4 promoted scratch catalog as the role
``acb_app_h3rls``: not the owner, not a superuser and not BYPASSRLS. The
fixture runs the failure store migration again with that role in place of
``acb_app``, so the role holds the grants of production. Org A is the internal
org, with the project "Tool failures" and its four lanes. Org B is a customer.
The tests pass the clock and the running release, and a fake
``build_committed_at`` gives the commit times. No test runs git or a model.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_tool_failure_tasks_r8.py -v -rs

Without the database the R8 tests SKIP, and the run reads green. A skip is
not a pass. The hermetic tests at the top run with no database.

**Acceptance (TOF-2e, §13.2), one test or more each.**

a. One task for each fingerprint.
b. A repeat updates the counts, at most once an hour.
c. A merge, a live build and 7 clean days close the task.
d. A repeat of a release that holds the fix reopens it. A repeat of an old
   release does not.
e. A move to Done goes back to Review.
f. A second try of the same job, after a try that failed, makes no second
   task.
g. When the deletes of the prune hook fail, the ``jobs`` rows of that prune
   stay deleted.
h. A cancelled task mutes the fingerprint, and a move back to Open writes
   again. A renamed lane moves no task.

GH-6 skips the project: ``test_github_merge_r8.py``. The retention of each org
(P9): ``test_failure_store_r8.py``. No tenant text in a task (P1):
``test_failure_no_tenant_text.py``.

**Mutations (R7).** Each one turns a named test red. Run on 2026-11-19, all
red. ``failure_set`` itself refuses a second task id, so a mutation that only
tries a second task stays green: the database is the last guard.

* M1. ``_sync_new`` skips ``_set`` after the create -> ``test_a_one_task_for_each_fingerprint``.
* M2. ``COUNTS_EVERY = timedelta(0)`` -> ``test_b_a_repeat_updates_the_counts_once_an_hour``.
* M3. ``holds_fix`` drops ``FIX_TOLERANCE`` and compares with ``>`` to the
  merge -> ``test_holds_fix_has_a_tolerance_of_10_seconds``.
* M4. ``repeat_after_fix`` drops the ``holds_fix`` check (any count reopens)
  -> ``test_d_a_repeat_of_the_fix_release_reopens_and_an_old_one_does_not``.
* M5. The one-closer branch is removed -> ``test_e_a_move_to_done_goes_back_to_review``.
* M6. ``register(..., idempotent=False)`` -> ``test_f_a_second_try_of_the_job_makes_no_second_task``.
* M7. ``_sync_new`` swallows a failure of ``_set`` -> ``test_f_a_failed_set_leaves_no_task``.
* M8. ``prune_hook`` drops ``db.begin_nested()`` -> ``test_g_a_failed_prune_hook_keeps_the_jobs_rows_deleted``.
* M9. ``_gone`` reads no cancelled lane -> ``test_h_a_cancelled_task_mutes_and_open_writes_again``.
* M10. ``seed`` drops the org check -> ``test_the_seed_returns_for_each_org_but_the_internal_org``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import tenant_session
from gateway import build_info
from gateway.failures import sync
from gateway.jobs import queue, worker
from sqlalchemy import text

from tests.unit import _github_r8 as g
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_failure_store_r8 import _BIND, _TALLY, _fp, _migration
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

RUN_AS = "tool-failures@tof2e.test"
TOOL = "crm_get_record"
SITE = "acb_skills.crm_tools:crm_get_record"
#: Two releases. The new one holds the fix, the old one does not.
R_NEW = "a" * 40
R_OLD = "b" * 40


# ══ Hermetic: the rules, the text and the registration ══════════════════════


def _cfg(**kw: Any) -> sync.Config:
    base = {"org": str(uuid.uuid4()), "project": str(uuid.uuid4()), "run_as": RUN_AS,
            "external_min": 20, "close_days": 7}
    base.update(kw)
    return sync.Config(**base)


def _due(**kw: Any) -> sync.Due:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    base: dict[str, Any] = {
        "fingerprint": _fp(TOOL, "exception", "builtins.KeyError", SITE), "tool": TOOL,
        "error_code": "exception", "exc_type": "builtins.KeyError", "code_site": SITE,
        "sources": ("chat_tool",), "surfaces": ("chat",), "first_seen_at": now,
        "last_seen_at": now, "first_release": R_OLD, "last_release": R_OLD, "total": 3,
        "state": "new", "task_id": None, "fix_merged_at": None, "fix_live_at": None,
        "closed_at": None, "fix_release": None, "reopened": 0, "shown_total": 0,
        "n_1d": 3, "n_7d": 3, "n_30d": 3, "days": ()}
    base.update(kw)
    return sync.Due(**base)


def test_the_kind_is_a_detached_idempotent_write_with_a_policy() -> None:
    entry = worker._HANDLERS[sync.KIND]
    assert (entry.mode, entry.idempotent, entry.effect) == ("detached", True, "write")
    assert "D225" in entry.policy and "internal org" in entry.policy
    assert sync.seed in worker.seed_hooks
    assert sync.prune_hook in worker.prune_hooks


def test_the_settings_ship_dark() -> None:
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["tool_failure_tasks"].default is False
    for name in ("tool_failure_org_id", "tool_failure_project_id", "tool_failure_run_as"):
        assert fields[name].default == ""
    assert fields["tool_failure_external_min"].default == 20
    assert fields["tool_failure_close_days"].default == 7


def test_with_an_id_missing_the_job_has_no_config(monkeypatch) -> None:
    s = get_settings()
    org, project = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(s, "tool_failure_org_id", org)
    monkeypatch.setattr(s, "tool_failure_project_id", project)
    monkeypatch.setattr(s, "tool_failure_run_as", RUN_AS)
    assert sync.config() == sync.Config(org, project, RUN_AS, 20, 7)
    for name, bad in (("tool_failure_org_id", ""), ("tool_failure_project_id", "x"),
                      ("tool_failure_run_as", "")):
        with monkeypatch.context() as m:
            m.setattr(s, name, bad)
            assert sync.config() is None, name


def test_the_seed_returns_for_each_org_but_the_internal_org(monkeypatch) -> None:
    cfg = _cfg()
    monkeypatch.setattr(sync, "config", lambda: cfg)
    monkeypatch.setattr(sync, "tasks_enabled", lambda: True)
    seen: list[tuple[str, str, str]] = []

    @contextlib.asynccontextmanager
    async def session(org: str):
        yield org

    async def enqueue(db: Any, kind: str, payload: Any, **kw: Any) -> None:
        seen.append((db, kind, kw["dedupe_key"]))

    monkeypatch.setattr(sync, "tenant_session", session)
    monkeypatch.setattr(sync.queue, "enqueue", enqueue)
    at = datetime(2026, 11, 19, 7, 40, tzinfo=UTC)
    asyncio.run(sync.seed(str(uuid.uuid4()), at))
    assert seen == []
    asyncio.run(sync.seed(cfg.org, at))
    assert seen == [(cfg.org, sync.KIND, "failure_sync:2026-11-19T07:30")]
    monkeypatch.setattr(sync, "tasks_enabled", lambda: False)
    asyncio.run(sync.seed(cfg.org, at))
    assert len(seen) == 1


def test_holds_fix_has_a_tolerance_of_10_seconds() -> None:
    merged = datetime(2026, 11, 19, 8, 0, 0, tzinfo=UTC)
    times = {R_NEW: merged - timedelta(seconds=10), R_OLD: merged - timedelta(seconds=11)}
    assert sync.holds_fix(R_NEW, merged, times)
    assert not sync.holds_fix(R_OLD, merged, times)
    assert not sync.holds_fix(None, merged, times)
    assert not sync.holds_fix("c" * 40, merged, times)  # git gave no answer
    assert not sync.holds_fix(R_NEW, None, times)


def test_a_code_that_we_do_not_own_waits_for_two_days_or_three_orgs() -> None:
    from datetime import date

    cfg = _cfg()
    code = {"error_code": "mcp_timeout", "exc_type": "-", "code_site": "-", "tool": "mcp"}
    d1, d2 = date(2026, 11, 18), date(2026, 11, 19)
    assert sync.wants_task(_due(), None, cfg)  # an owned code: at once
    assert not sync.wants_task(_due(**code, days=(sync.Day(d2, 500, ()),)), "<3", cfg)
    assert not sync.wants_task(_due(**code, days=(
        sync.Day(d2, 20, ()), sync.Day(d1, 19, ()))), None, cfg)
    assert sync.wants_task(_due(**code, days=(
        sync.Day(d2, 20, ()), sync.Day(d1, 20, ()))), None, cfg)
    assert sync.wants_task(_due(**code), "3", cfg)
    assert not sync.wants_task(_due(**code), None, cfg)  # NULL: cannot tell


def test_the_text_holds_platform_fields_in_code_spans() -> None:
    due = _due(total=7, n_1d=2, n_7d=5, n_30d=7)
    assert sync.title(due) == f"Tool failure · {TOOL} · exception · {due.fingerprint}"
    body = sync.description(due, "<3", 7)
    for value in (f"`{TOOL}`", "`exception`", "`builtins.KeyError`", f"`{SITE}`",
                  "`chat_tool`", "`7`", "`<3`", f"`{R_OLD}`"):
        assert value in body, value
    assert "Surfaces" not in body  # only at a band of 3 or more
    assert "`chat`" in sync.description(due, "4", 7)
    assert "`unknown`" in sync.description(due, None, 7)
    assert "Do not move it to Done" in body
    seen = body.split("**Seen**")[1].split("**The rule**")[0]
    assert f"`{due.first_seen_at.date().isoformat()}`" in seen
    assert "T" not in seen.replace("`", "").split("release")[0]  # a date, never an hour


def test_build_committed_at_reads_git_and_refuses_a_value_that_is_not_a_sha(monkeypatch) -> None:
    import subprocess

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True, cwd=g.ROOT).stdout.strip()
    stamp = subprocess.run(["git", "show", "-s", "--format=%ct", head], capture_output=True,
                           text=True, check=True, cwd=g.ROOT).stdout.strip()
    assert build_info.build_committed_at(head) == datetime.fromtimestamp(int(stamp), tz=UTC)
    calls: list[Any] = []
    monkeypatch.setattr(build_info.subprocess, "run", lambda *a, **k: calls.append(a))
    for bad in ("--output=/tmp/x", "HEAD", "ABCDEF1", "abc", "a" * 41, None):
        assert build_info.build_committed_at(bad) is None
    assert calls == []
    assert build_info.build_committed_at(head) is not None  # kept: no second spawn
    assert calls == []


# ══ R8: the world ═══════════════════════════════════════════════════════════


class W:
    """The promoted catalog, the project and its lanes, the config."""

    p: Any
    app: Any
    a: str
    b: str
    project: str
    lanes: dict[str, str]
    cfg: sync.Config
    times: dict[str, datetime]


@pytest.fixture
def world(promoted, app_engine, monkeypatch):  # noqa: F811
    p = promoted
    body = _migration().replace("acb_app", _APP_ROLE)
    with p.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(body)
    w = W()
    w.p, w.a, w.b, w.app = p, p.org_a, p.org_b, app_engine
    w.project, w.lanes = seed_failure_project(p, p.org_a)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM jobs"))
    s = get_settings()
    for name, value in (("tool_failure_tasks", True), ("tool_failure_org_id", p.org_a),
                        ("tool_failure_project_id", w.project),
                        ("tool_failure_run_as", RUN_AS), ("tool_failure_external_min", 20),
                        ("tool_failure_close_days", 7)):
        monkeypatch.setattr(s, name, value)
    w.cfg = sync.config()
    assert w.cfg is not None and w.cfg.org == p.org_a
    w.times = {}
    monkeypatch.setattr(build_info, "build_committed_at", lambda sha: w.times.get(sha))
    yield w
    with p.admin_engine.begin() as c:
        for table in ("failure_config", "failure_events", "failure_org_days",
                      "failure_daily", "failure_fingerprints", "jobs"):
            c.execute(text(f"DELETE FROM {table}"))


def seed_failure_project(p: Any, org: str) -> tuple[str, dict[str, str]]:
    """The project "Tool failures" of the internal *org*: a child of a space,
    with its four lanes by name, a grant to ``RUN_AS``, and the
    ``failure_config`` row. A space holds no task, so the project is a child,
    as on production (§7.1)."""
    space, _ = g.seed_project(p, org, RUN_AS, lanes=(("To do", "todo"),))
    project = str(uuid.uuid4())
    lanes: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
            "parent_project_id) VALUES (:id, :o, 'Tool failures', :e, true, :parent)"),
            {"id": project, "o": org, "e": RUN_AS, "parent": space})
        for position, (name, category) in enumerate((
                ("Open", "todo"), ("Review", "in_progress"), ("Done", "done"),
                ("Cancelled", "cancelled"))):
            lanes[name] = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": lanes[name], "o": org, "p": project, "n": name, "c": category,
                 "pos": (position + 1) * 10})
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by) "
            "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :e, :e)"),
            {"o": org, "p": project, "e": RUN_AS})
        c.execute(text("DELETE FROM failure_config"))
        c.execute(text("INSERT INTO failure_config (internal_org) VALUES (:o)"), {"o": org})
    return project, lanes


def _run(w: W, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(g.app_dsn(w.p)):
            return await fn()
    return asyncio.run(go())


def _tally(w: W, n: int = 1, *, release: str | None = R_OLD, tool: str = TOOL,
           code: str = "exception", exc: str = "builtins.KeyError", site: str = SITE,
           org: str | None = None) -> str:
    """One count of org B (a customer), as the app role, through the one
    tally function."""
    fp = _fp(tool, code, exc, site)
    with w.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": org or w.b})
        c.execute(_TALLY, {"fp": fp, "tool": tool, "code": code, "exc": exc,
                           "site": site, "source": "chat_tool", "surface": "chat",
                           "release": release, "n": n})
    return fp


def _sync(w: W, now: datetime, running: str | None = None) -> dict[str, int]:
    return _run(w, lambda: sync.sync(w.cfg, now=now, running=running))


def _fprow(w: W, fp: str) -> Any:
    return g.rows(w.p, "SELECT * FROM failure_fingerprints WHERE fingerprint = :f", f=fp)[0]


def _tasks(w: W) -> list[Any]:
    return g.rows(w.p, "SELECT t.*, s.name AS lane FROM pm_tasks t JOIN pm_task_statuses s "
                       "ON s.id = t.status_id WHERE t.project_id = CAST(:proj AS uuid) "
                       "ORDER BY t.created_at", proj=w.project)


def _task(w: W, task_id: Any) -> Any:
    return g.rows(w.p, "SELECT t.*, s.name AS lane FROM pm_tasks t JOIN pm_task_statuses s "
                       "ON s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=str(task_id))[0]


def _comments(w: W, task_id: Any) -> list[str]:
    return [r[0] for r in g.rows(
        w.p, "SELECT body FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
             "AND type = 'comment' AND created_by = :a ORDER BY created_at, id",
        t=str(task_id), a=sync.ACTOR)]


def _lane(w: W, task_id: Any, name: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) "
                       "WHERE id = CAST(:t AS uuid)"), {"s": w.lanes[name], "t": str(task_id)})


def _merge(w: W, task_id: Any, merged_at: datetime, number: int = 7) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "state, via, created_by, merged_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            "'pull_request', 'github', :ref, :url, 'merged', 'ui', :e, :m)"),
            {"o": w.a, "t": str(task_id), "ref": f"acme/metorite#{number}",
             "url": f"https://github.com/acme/metorite/pull/{number}", "e": RUN_AS,
             "m": merged_at})


def _now() -> datetime:
    return datetime.now(UTC)


def _to_fix_live(w: W, fix_live_at: datetime) -> tuple[str, Any, datetime]:
    """One fingerprint with its task in ``fix_live`` at *fix_live_at*."""
    fp = _tally(w, release=R_OLD)
    _sync(w, fix_live_at - timedelta(hours=1))
    task_id = _fprow(w, fp).task_id
    merged = fix_live_at - timedelta(minutes=30)
    _merge(w, task_id, merged)
    w.times.update({R_NEW: merged - timedelta(seconds=1), R_OLD: merged - timedelta(days=1)})
    _sync(w, fix_live_at, running=R_NEW)
    row = _fprow(w, fp)
    assert (row.state, row.fix_release) == ("fix_live", R_NEW)
    return fp, task_id, merged


# ── a. One task for each fingerprint ─────────────────────────────────────────


@_DB_GATE
def test_a_one_task_for_each_fingerprint(world) -> None:
    w = world
    fp1 = _tally(w)
    fp2 = _tally(w, tool="crm_list_records", site="acb_skills.crm_tools:crm_list_records")
    # A code that we do not own, under its limit: counts and no task.
    fp3 = _tally(w, tool="mcp", code="mcp_timeout", exc="-", site="-")
    now = _now()
    _sync(w, now)
    _sync(w, now + timedelta(minutes=15))
    tasks = _tasks(w)
    assert sorted(t.title for t in tasks) == sorted(
        f"Tool failure · {tool} · exception · {fp}"
        for tool, fp in ((TOOL, fp1), ("crm_list_records", fp2)))
    for t in tasks:
        assert (t.source, t.created_by, t.lane) == ("automation", RUN_AS, "Open")
    rows = {fp: _fprow(w, fp) for fp in (fp1, fp2, fp3)}
    assert {str(rows[fp1].task_id), str(rows[fp2].task_id)} == {str(t.id) for t in tasks}
    assert (rows[fp1].state, rows[fp1].shown_total) == ("open", 1)
    assert (rows[fp3].state, rows[fp3].task_id) == ("new", None)


# ── b. A repeat updates the counts ──────────────────────────────────────────


@_DB_GATE
def test_b_a_repeat_updates_the_counts_once_an_hour(world) -> None:
    w = world
    fp = _tally(w)
    now = _now()
    _sync(w, now)
    task_id = _fprow(w, fp).task_id
    assert "- Total: `1`" in _task(w, task_id).description
    _tally(w, 4)
    _sync(w, now + timedelta(minutes=10))
    assert "- Total: `1`" in _task(w, task_id).description
    assert _fprow(w, fp).shown_total == 1
    _sync(w, now + timedelta(hours=2))
    assert "- Total: `5`" in _task(w, task_id).description
    assert _fprow(w, fp).shown_total == 5
    assert len(_tasks(w)) == 1


# ── c. A merge, a live build and 7 clean days ───────────────────────────────


@_DB_GATE
def test_c_a_merge_a_live_build_and_7_clean_days_close_it(world) -> None:
    w = world
    base = _now()
    fp = _tally(w, release=R_OLD)
    _sync(w, base - timedelta(days=9))
    task_id = _fprow(w, fp).task_id
    merged = base - timedelta(days=8, hours=1)
    _merge(w, task_id, merged)
    w.times.update({R_NEW: merged - timedelta(seconds=1), R_OLD: merged - timedelta(days=1)})
    # The running build is old: the fix is merged, not live.
    _sync(w, base - timedelta(days=8), running=R_OLD)
    row = _fprow(w, fp)
    assert row.state == "fix_merged" and row.fix_merged_at == merged
    # With no answer from git, the state does not move.
    _sync(w, base - timedelta(days=8), running="c" * 40)
    assert _fprow(w, fp).state == "fix_merged"
    live_at = base - timedelta(days=8) + timedelta(minutes=1)
    _sync(w, live_at, running=R_NEW)
    row = _fprow(w, fp)
    assert (row.state, row.fix_live_at, row.fix_release) == ("fix_live", live_at, R_NEW)
    close_on = (live_at + timedelta(days=7)).date().isoformat()
    assert any(f"`{close_on}`" in c and f"`{R_NEW}`" in c for c in _comments(w, task_id))
    # 6 days: still watched.
    _sync(w, live_at + timedelta(days=6), running=R_NEW)
    assert _fprow(w, fp).state == "fix_live"
    _sync(w, base - timedelta(minutes=5), running=R_NEW)
    row = _fprow(w, fp)
    task = _task(w, task_id)
    assert (row.state, task.lane) == ("closed", "Done")
    assert row.closed_at is not None and task.completed_at is not None
    assert any(c.startswith("Closed.") for c in _comments(w, task_id))
    moves = g.rows(w.p, "SELECT created_by, meta FROM pm_activities WHERE task_id = "
                        "CAST(:t AS uuid) AND type = 'status_change'", t=str(task_id))
    assert moves and all(m.created_by == sync.ACTOR and m.meta.get("automation")
                         for m in moves)


# ── d. A repeat after the fix ───────────────────────────────────────────────


@_DB_GATE
def test_d_a_repeat_of_the_fix_release_reopens_and_an_old_one_does_not(world) -> None:
    w = world
    base = _now()
    fp, task_id, _ = _to_fix_live(w, base - timedelta(hours=2))
    _tally(w, release=R_OLD)  # an old process on an old release
    _sync(w, base, running=R_NEW)
    assert (_fprow(w, fp).state, _fprow(w, fp).reopened) == ("fix_live", 0)
    _tally(w, release=None)  # a NULL release never holds the fix
    _sync(w, base, running=R_NEW)
    assert _fprow(w, fp).state == "fix_live"
    _tally(w, release=R_NEW)
    _sync(w, base, running=R_NEW)
    row = _fprow(w, fp)
    assert (row.state, row.reopened, row.fix_live_at) == ("open", 1, None)
    assert str(row.task_id) == str(task_id) and len(_tasks(w)) == 1
    assert _task(w, task_id).lane == "Open"
    assert _comments(w, task_id)[-1] == sync.REPEAT
    # The old merge does not count again: a new merge must come.
    _sync(w, base + timedelta(minutes=1), running=R_NEW)
    assert _fprow(w, fp).state == "open"


@_DB_GATE
def test_d_a_closed_task_reopens_on_the_fix_release_only(world) -> None:
    w = world
    base = _now()
    fp, task_id, _ = _to_fix_live(w, base - timedelta(days=8))
    _sync(w, base - timedelta(minutes=5), running=R_NEW)
    assert _fprow(w, fp).state == "closed"
    _tally(w, release=R_OLD)
    _sync(w, base, running=R_NEW)
    row = _fprow(w, fp)
    assert (row.state, row.reopened) == ("closed", 0)
    assert _task(w, task_id).lane == "Done"
    _tally(w, release=R_NEW)
    _sync(w, base, running=R_NEW)
    row = _fprow(w, fp)
    assert (row.state, row.reopened, row.closed_at) == ("open", 1, None)
    assert _task(w, task_id).lane == "Open" and len(_tasks(w)) == 1


# ── e. One closer ───────────────────────────────────────────────────────────


@_DB_GATE
def test_e_a_move_to_done_goes_back_to_review(world) -> None:
    w = world
    fp = _tally(w)
    now = _now()
    _sync(w, now)
    task_id = _fprow(w, fp).task_id
    for step in range(2):  # each time
        _lane(w, task_id, "Done")
        _sync(w, now + timedelta(minutes=1 + step))
        assert _task(w, task_id).lane == "Review"
        assert _fprow(w, fp).state == "open"
        assert _comments(w, task_id)[-1] == sync.ONE_CLOSER
    assert _comments(w, task_id).count(sync.ONE_CLOSER) == 2


# ── f. A second try ─────────────────────────────────────────────────────────


@_DB_GATE
def test_f_a_second_try_of_the_job_makes_no_second_task(world, monkeypatch) -> None:
    w = world
    _tally(w)
    _tally(w, tool="crm_list_records", site="acb_skills.crm_tools:crm_list_records")
    monkeypatch.setattr(sync, "running_release", lambda: None)
    real = queue.complete
    calls = {"n": 0}

    async def complete_once_failing(db: Any, claim: Any, *, now: datetime) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the settle of the first try failed")
        return await real(db, claim, now=now)

    monkeypatch.setattr(queue, "complete", complete_once_failing)
    t0 = _now()

    async def go() -> tuple[Any, Any]:
        async with tenant_session(w.a) as db:
            await queue.enqueue(db, sync.KIND, {}, dedupe_key="failure_sync:test", now=t0)
        first = await worker.run_one(w.a, t0)
        second = await worker.run_one(w.a, t0 + timedelta(minutes=5))
        return first, second

    assert _run(w, go) == ("retry", "done")
    assert len(_tasks(w)) == 2
    [job] = g.rows(w.p, "SELECT state, attempts FROM jobs WHERE kind = :k", k=sync.KIND)
    assert (job.state, job.attempts) == ("done", 2)


@_DB_GATE
def test_f_a_failed_set_leaves_no_task(world, monkeypatch) -> None:
    w = world
    fp = _tally(w)
    real = sync._set

    async def failing(db: Any, fp_: str, st: Any) -> None:
        raise RuntimeError("failure_set failed")

    monkeypatch.setattr(sync, "_set", failing)
    assert _sync(w, _now()) == {"failed": 1}
    assert _tasks(w) == [] and _fprow(w, fp).task_id is None
    monkeypatch.setattr(sync, "_set", real)
    _sync(w, _now())
    assert len(_tasks(w)) == 1 and _fprow(w, fp).task_id is not None


# ── g. The prune hook never rolls back the jobs prune ───────────────────────


@_DB_GATE
def test_g_a_failed_prune_hook_keeps_the_jobs_rows_deleted(world, monkeypatch) -> None:
    w = world
    fp = _tally(w)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO jobs (organization_id, kind, state, attempts, finished_at) "
            "VALUES (CAST(:o AS uuid), 'jobs.prune', 'done', 1, now() - interval '40 days')"),
            {"o": w.b})
        c.execute(text(
            "INSERT INTO failure_events (organization_id, occurred_at, fingerprint, source, "
            "surface, error_code, tool, exc_type, code_site) VALUES (CAST(:o AS uuid), "
            "now() - interval '40 days', :fp, 'chat_tool', 'chat', 'exception', :t, "
            "'builtins.KeyError', :s)"), {"o": w.b, "fp": fp, "t": TOOL, "s": SITE})
    monkeypatch.setattr(worker, "prune_hooks", [sync.prune_hook])
    monkeypatch.setattr(sync, "_PRUNE_ORG_DAYS_SQL", "SELECT 1 / 0")

    async def go() -> None:
        async with tenant_session(w.b) as db:
            await worker.prune_job(db, {})

    _run(w, go)
    assert g.rows(w.p, "SELECT id FROM jobs WHERE organization_id = CAST(:o AS uuid)",
                  o=w.b) == []
    # The hook failed, so its delete rolled back to the savepoint.
    assert len(g.rows(w.p, "SELECT id FROM failure_events WHERE organization_id = "
                           "CAST(:o AS uuid) AND occurred_at < now() - interval '30 days'",
                      o=w.b)) == 1


# ── h. Muted, and a renamed lane ────────────────────────────────────────────


@_DB_GATE
def test_h_a_cancelled_task_mutes_and_open_writes_again(world) -> None:
    w = world
    fp = _tally(w)
    now = _now()
    _sync(w, now)
    task_id = _fprow(w, fp).task_id
    _lane(w, task_id, "Cancelled")
    _sync(w, now + timedelta(minutes=1))
    assert _fprow(w, fp).state == "muted"
    _tally(w, 9)
    _sync(w, now + timedelta(hours=2))
    assert "- Total: `1`" in _task(w, task_id).description  # no write while muted
    assert _fprow(w, fp).total == 10  # the counts go on
    _lane(w, task_id, "Open")
    _sync(w, now + timedelta(hours=3))
    assert _fprow(w, fp).state == "open"
    assert "- Total: `10`" in _task(w, task_id).description
    assert len(_tasks(w)) == 1


@_DB_GATE
def test_h_a_renamed_lane_moves_no_task_and_names_the_lane(world, monkeypatch) -> None:
    w = world
    fp = _tally(w)
    now = _now()
    _sync(w, now)
    task_id = _fprow(w, fp).task_id
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_statuses SET name = 'In review' "
                       "WHERE id = CAST(:s AS uuid)"), {"s": w.lanes["Review"]})
    logged: list[tuple[str, dict[str, Any]]] = []

    class Log:
        def warning(self, event: str, **kw: Any) -> None:
            logged.append((event, kw))

        def info(self, event: str, **kw: Any) -> None:
            pass

    monkeypatch.setattr(sync, "_log", Log())
    _lane(w, task_id, "Done")
    assert _sync(w, now + timedelta(minutes=1)) == {"lane_missing": 1}
    assert _task(w, task_id).lane == "Done"
    assert ("failure_sync.lane_missing", {"name": "Review"}) in logged
