"""WS-ALN ALN-1 — the durable trigger of the alignment check (R8).

Spec: ``project-docs/specs/project_alignment.md`` §5, and row ALN-1 of §10.
Code: ``gateway/align/trigger.py``, ``gateway/align/check.py``.

Two halves.

**Hermetic** (no database). The gate, the hash and the dedupe key, the log line
of a failure, the stub, the settings list, and the source fences of the seam.
They run in every environment.

**R8** (a real Postgres, through the real ``/projects`` router, as the
NOBYPASSRLS app role of the phase-4 rehearsal, with two organizations). A
hermetic fake agrees with any SQL it gets, so each acceptance item runs here.
The R8 class carries ``_DB_GATE``. Without the database it SKIPS, and the run
reads green. A skip is not a pass.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_align_trigger.py -v -rs

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``trigger.enqueue_check``: drop ``db.begin_nested()``.
  ``test_r8_a_failed_enqueue_leaves_the_write_intact`` goes red.
* M2. ``trigger.enqueue_check``: drop the ``except``.
  ``test_r8_a_failed_enqueue_leaves_the_write_intact`` goes red.
* M3. ``trigger.jobs_enabled``: always answer True.
  ``test_r8_a_closed_gate_writes_no_row`` and the gate cases go red.
* M4. ``trigger.enqueue_check``: drop the compare with *before*.
  ``test_r8_an_edit_with_the_same_text_enqueues_none`` goes red.
* M5. ``tasks.create_task``: move the enqueue after the session.
  ``test_the_seam_create_and_patch_enqueue_inside_the_tenant_session`` and
  ``test_r8_a_rolled_back_write_leaves_no_job`` go red.
* M6. ``tasks.patch_task``: pass ``known_team=True``.
  ``test_r8_a_personal_task_is_never_enqueued`` goes red.
* M7. ``tasks.move_task_in``: enqueue on each move.
  ``test_r8_a_move_between_two_team_projects_enqueues_none`` goes red.
* M8. ``tasks.create_task``: drop the test of ``personal_owner``.
  ``test_r8_a_personal_task_is_never_enqueued`` goes red.
* M9. ``check.py``: drop ``register_jobs()``.
  ``test_the_stub_registers_at_import`` goes red.
* M10. ``trigger._checked_text``: add the due date.
  ``test_the_hash_reads_only_the_title_and_the_description`` goes red.
* M12. ``intake.capture_intake``: drop the ``enqueue_check`` call.
  ``test_r8_a_capture_into_a_team_project_enqueues_one_job`` goes red.
* M13. ``trigger.jobs_enabled``: drop the test of ``worker.kinds()``.
  ``test_an_unregistered_kind_closes_the_gate_and_opens_no_savepoint`` goes red.
* M11. ``core.assert_move_keeps_privacy``: answer None.
  ``test_r8_a_move_out_of_a_personal_project_enqueues_one_job`` goes red.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import env_guard
from acb_common.db import tenant_session
from acb_common.settings import Settings
from gateway import flag_reload
from gateway.align import check, trigger
from gateway.jobs import queue, worker
from gateway.routes.projects import core, tasks
from sqlalchemy import text

from tests.unit import _agent_board_r8
from tests.unit._agent_board_r8 import member, project, task
from tests.unit.test_agent_runs_r8 import _principal, _session
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_ROUTES = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"

ORG = "11111111-1111-1111-1111-111111111111"
TASK = "22222222-2222-2222-2222-222222222222"
#: A title that a log line must never hold.
SECRET_TITLE = "the secret plan for the customer"


def _gate(monkeypatch: Any, *, worker_on: bool = True, orgs: str = "") -> None:
    """Set the two switches of the gate, and leave every other setting alone."""
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=worker_on))
    monkeypatch.setattr(trigger, "get_settings", lambda: SimpleNamespace(align_jobs_orgs=orgs))


class _NoDatabase:
    """A session that fails on any use, so a test proves that no work reached it."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the gate must not touch the database ({name})")


def _row(title: str | None = "A title", description: str | None = "A body",
         **more: Any) -> SimpleNamespace:
    return SimpleNamespace(title=title, description=description, **more)


# ── The gate ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("worker_on", "orgs", "org", "open_"), [
    (True, ORG, ORG, True),
    (True, f"  {ORG.upper()} ,other", ORG, True),
    (True, "*", ORG, True),
    (False, ORG, ORG, False),
    (False, "*", ORG, False),
    (True, "", ORG, False),
    (True, "another-org", ORG, False),
    (True, ORG, None, False),
    (True, ORG, "", False),
])
def test_the_gate_needs_the_worker_and_the_org(
    monkeypatch: Any, worker_on: bool, orgs: str, org: str | None, open_: bool,
) -> None:
    _gate(monkeypatch, worker_on=worker_on, orgs=orgs)
    assert trigger.jobs_enabled(org) is open_


def test_an_unregistered_kind_closes_the_gate_and_opens_no_savepoint(
        monkeypatch: Any) -> None:
    """If `gateway.align.check` failed to import, no worker claims the row."""
    _gate(monkeypatch, orgs=ORG)
    monkeypatch.setattr(worker, "_HANDLERS", {})
    assert trigger.jobs_enabled(ORG) is False
    answer = asyncio.run(trigger.enqueue_check(
        _NoDatabase(), TASK, ORG, row=_row(), known_team=True))
    assert answer is None


def test_the_default_gate_is_closed() -> None:
    assert Settings.model_fields["align_jobs_orgs"].default == ""
    assert Settings.model_fields["job_queue"].default is False


def test_a_closed_gate_does_no_database_work(monkeypatch: Any) -> None:
    _gate(monkeypatch, orgs="")
    answer = asyncio.run(trigger.enqueue_check(_NoDatabase(), TASK, ORG, row=_row()))
    assert answer is None


def test_an_org_that_is_not_listed_does_no_database_work(monkeypatch: Any) -> None:
    _gate(monkeypatch, orgs="another-org")
    answer = asyncio.run(trigger.enqueue_check(
        _NoDatabase(), TASK, ORG, row=_row(), before=_row("Other text")))
    assert answer is None


def test_the_same_text_does_no_database_work(monkeypatch: Any) -> None:
    _gate(monkeypatch, orgs=ORG)
    answer = asyncio.run(trigger.enqueue_check(
        _NoDatabase(), TASK, ORG, row=_row(due_at="2099-01-01"),
        before=_row(due_at="2098-01-01")))
    assert answer is None


def test_the_flag_is_on_the_operator_lists() -> None:
    assert env_guard.is_platform_env("ALIGN_JOBS_ORGS")
    assert "ALIGN_JOBS_ORGS" in flag_reload.RELOADABLE
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nALIGN_JOBS_ORGS=\n" in example


# ── The hash and the dedupe key ─────────────────────────────────────────────


def test_the_hash_is_sha256_of_the_title_a_nul_and_the_description() -> None:
    expected = hashlib.sha256("Title é\x00Body".encode()).hexdigest()
    assert trigger.content_hash(_row("Title é", "Body")) == expected
    assert len(expected) == 64


def test_the_hash_reads_only_the_title_and_the_description() -> None:
    one = _row(due_at="2099-01-01", tags=["a"], status_id="s1")
    two = _row(due_at="2001-01-01", tags=["b"], status_id="s2")
    assert trigger.content_hash(one) == trigger.content_hash(two)


def test_the_hash_keeps_the_two_fields_apart() -> None:
    assert trigger.content_hash(_row("ab", "c")) != trigger.content_hash(_row("a", "bc"))
    assert trigger.content_hash(_row("ab", "")) != trigger.content_hash(_row("", "ab"))


def test_a_null_and_an_empty_text_are_the_same_text() -> None:
    assert trigger.content_hash(_row("A", None)) == trigger.content_hash(_row("A", ""))


def test_a_text_change_moves_the_hash() -> None:
    assert trigger.content_hash(_row("A", "B")) != trigger.content_hash(_row("A", "B "))


def test_the_dedupe_key_fits_the_queue() -> None:
    key = trigger.dedupe_key(TASK, trigger.content_hash(_row()))
    assert key.startswith(f"align:{TASK}:")
    assert len(key) == 107 and len(key) <= queue.DEDUPE_CHARS


# ── A failed enqueue ────────────────────────────────────────────────────────


class _BrokenSession:
    """A session whose savepoint fails with an error that quotes the title."""

    def begin_nested(self) -> Any:
        raise RuntimeError(f"INSERT failed for {SECRET_TITLE}")


def test_a_failed_enqueue_logs_no_text_and_raises_nothing(monkeypatch: Any) -> None:
    _gate(monkeypatch, orgs=ORG)
    with structlog.testing.capture_logs() as caps:
        answer = asyncio.run(trigger.enqueue_check(
            _BrokenSession(), TASK, ORG, row=_row(SECRET_TITLE), known_team=True))
    assert answer is None
    lines = [c for c in caps if c["event"] == "align.enqueue_failed"]
    assert len(lines) == 1 and lines[0]["log_level"] == "warning"
    assert lines[0]["task_id"] == TASK and lines[0]["error"] == "RuntimeError"
    assert SECRET_TITLE not in repr(caps)


def test_a_cancel_is_not_swallowed(monkeypatch: Any) -> None:
    _gate(monkeypatch, orgs=ORG)

    class Cancelled:
        def begin_nested(self) -> Any:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(trigger.enqueue_check(Cancelled(), TASK, ORG, row=_row(), known_team=True))


# ── The stub ────────────────────────────────────────────────────────────────


def test_the_stub_registers_at_import() -> None:
    assert trigger.KIND == "align.check"
    assert trigger.KIND in worker.kinds()
    entry = worker._HANDLERS[trigger.KIND]
    assert entry.handler is check.check
    assert entry.mode == "session" and entry.idempotent is True


def test_the_stub_reads_nothing_and_logs_nothing() -> None:
    with structlog.testing.capture_logs() as caps:
        assert asyncio.run(check.check(_NoDatabase(), {"task_id": TASK})) is None
    assert caps == []
    source = inspect.getsource(check.check)
    assert "db." not in source and "payload[" not in source and "_log" not in source


def test_the_gateway_imports_the_stub_before_the_worker_starts() -> None:
    source = (_GATEWAY / "main.py").read_text(encoding="utf-8")
    assert source.index("from gateway.align import check") < source.index(
        "from gateway.jobs.worker import start_job_worker")


# ── The fences of the seam ──────────────────────────────────────────────────


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id == name]


def _inside_tenant_session(function: ast.AST, call: ast.Call) -> bool:
    parents = {
        child: parent for parent in ast.walk(function)
        for child in ast.iter_child_nodes(parent)}
    node: ast.AST | None = call
    while node is not None:
        if isinstance(node, ast.AsyncWith) and any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", "") == "_tenant_session"
                for item in node.items):
            return True
        node = parents.get(node)
    return False


@pytest.mark.parametrize("handler", ["create_task", "patch_task"])
def test_the_seam_create_and_patch_enqueue_inside_the_tenant_session(handler: str) -> None:
    """The write and its job share one transaction (§5 item 1). An enqueue
    after the commit could lose the job in a crash."""
    function = _function(_tree(_ROUTES / "tasks.py"), handler)
    calls = _calls(function, "enqueue_check")
    assert len(calls) == 1, f"{handler} must enqueue once"
    assert _inside_tenant_session(function, calls[0]), (
        f"{handler}: the enqueue must sit inside its `_tenant_session` block")


def test_the_seam_move_enqueues_on_the_session_of_its_caller() -> None:
    tree = _tree(_ROUTES / "tasks.py")
    calls = _calls(_function(tree, "move_task_in"), "enqueue_check")
    assert len(calls) == 1
    assert getattr(calls[0].args[0], "id", "") == "db"
    route = _function(tree, "move_task")
    moves = _calls(route, "move_task_in")
    assert len(moves) == 1 and _inside_tenant_session(route, moves[0])


@pytest.mark.parametrize("module", ["tasks.py", "move.py", "personal.py"])
def test_the_seam_each_move_passes_the_session_of_its_write(module: str) -> None:
    moves = _calls(_tree(_ROUTES / module), "move_task_in")
    assert moves, f"{module} no longer calls move_task_in, so update this fence"
    assert all(getattr(call.args[0], "id", "") == "db" for call in moves)


@pytest.mark.parametrize("function", ["insert_row", "update_row"])
def test_the_seam_has_no_hook_on_the_row_helpers(function: str) -> None:
    """§5: the seam is the handlers. A hook here would reach the file import,
    the recurrence and the personal creators."""
    body = _function(_tree(_ROUTES / "core.py"), function)
    assert not _calls(body, "enqueue_check")
    assert "gateway.align" not in (_ROUTES / "core.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("module", [
    "personal.py", "recurrence.py", "import_writer.py",
    "activities.py", "automation.py", "merge.py",
])
def test_the_seam_skips_the_creators_that_the_spec_leaves_out(module: str) -> None:
    assert "enqueue_check" not in (_ROUTES / module).read_text(encoding="utf-8")


def test_the_seam_intake_enqueues_inside_the_tenant_session() -> None:
    function = _function(_tree(_ROUTES / "intake.py"), "capture_intake")
    calls = _calls(function, "enqueue_check")
    assert len(calls) == 1 and _inside_tenant_session(function, calls[0])


def test_the_trigger_opens_no_session_and_names_no_org_in_the_job() -> None:
    """R5. The caller holds the session, and the column DEFAULT stamps the org."""
    source = (_GATEWAY / "align" / "trigger.py").read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]
    for word in ("tenant_session", "get_db", "get_session_factory", "create_async_engine",
                 "organization_id"):
        assert word not in code, word


def test_aln1_stamps_no_org_on_an_event() -> None:
    """§5 item 6: H-328 part (c) owns that stamp, in ``core.emit``."""
    assert "organization_id" not in inspect.getsource(trigger)
    assert "organization_id" not in inspect.getsource(check)


# ── R8 ──────────────────────────────────────────────────────────────────────


def _now_after() -> datetime:
    """A claim time after each job of the test."""
    return datetime.now(UTC) + timedelta(minutes=1)


def _jobs(p: Any, org: str) -> list[Any]:
    """The ``align.check`` rows of *org*, read as the admin."""
    with p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id, organization_id, kind, payload, state, dedupe_key FROM jobs "
            "WHERE kind = :k AND organization_id = CAST(:o AS uuid) ORDER BY created_at"),
            {"k": trigger.KIND, "o": org}))


def _task_text(p: Any, task_id: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT title, description, project_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task_id}).fetchone()


def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
    async def go() -> Any:
        async with _session(p) as call:
            return await call(user, method, path, body)
    return asyncio.run(go())


def _personal(p: Any, org: str, email: str, *, parent: dict | None = None) -> dict[str, Any]:
    """A project of *email*'s personal tree, stamped as ``personal_owner`` says."""
    node = project(p, org, email, grant=email, parent=parent)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET personal_owner = :e WHERE id = CAST(:i AS uuid)"),
                  {"e": email, "i": node["id"]})
    return node


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """A member and a team project in each org, and a personal tree in org A."""
    p = promoted
    # The API takes numbers from a counter that starts at 1, and the seed from
    # its own count. Keep the two ranges apart in one root.
    _agent_board_r8._NUMBER[0] = max(_agent_board_r8._NUMBER[0], 1_000_000)
    a_email, b_email = "aln1-a@align-r8.test", "aln1-b@align-r8.test"
    ns = SimpleNamespace(p=p, a_email=a_email, b_email=b_email)
    ns.a = _principal(a_email, p.org_a)
    ns.b = _principal(b_email, p.org_b)
    # A team root is a space and holds no task, so the work sits one level down.
    space_a = project(p, p.org_a, a_email, grant=a_email)
    ns.team_a = project(p, p.org_a, a_email, parent=space_a)
    ns.other_team_a = project(p, p.org_a, a_email, parent=space_a)
    space_b = project(p, p.org_b, b_email, grant=b_email)
    ns.team_b = project(p, p.org_b, b_email, parent=space_b)
    ns.own = _personal(p, p.org_a, a_email)
    ns.own_child = _personal(p, p.org_a, a_email, parent=ns.own)
    member(p, p.org_a, a_email)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The world with the gate open for both orgs, a registry of the stub
    alone, and no job left from the last test."""
    p = world.p
    _gate(monkeypatch, orgs=f"{p.org_a},{p.org_b}")
    monkeypatch.setattr(worker, "_HANDLERS", {})
    check.register_jobs()

    def wipe() -> None:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM jobs WHERE kind = :k"), {"k": trigger.KIND})

    wipe()
    yield world
    wipe()


@_DB_GATE
class TestTheTriggerOnARealDatabase:
    def test_r8_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, w: Any) -> None:
        async def go() -> Any:
            async with _session(w.p), tenant_session(w.p.org_a) as db:
                return (await db.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles "
                    "WHERE rolname = current_user"))).fetchone()

        bypass, superuser = asyncio.run(go())
        assert bypass is False and superuser is False

    def test_r8_a_create_enqueues_one_job_in_the_bound_org(self, w: Any) -> None:
        p = w.p
        made = _call(p, w.a, "POST", "/projects/tasks", {
            "title": "Build the mount", "description": "Use the alloy",
            "project_id": w.team_a["id"]})
        assert made.status_code == 201, made.text
        tid = made.json()["id"]
        jobs = _jobs(p, p.org_a)
        assert len(jobs) == 1
        job = jobs[0]
        assert str(job.organization_id) == p.org_a and job.kind == "align.check"
        assert job.state == "queued"
        assert job.payload == {"task_id": tid}, "the payload holds the task id only"
        digest = trigger.content_hash(_row("Build the mount", "Use the alloy"))
        assert job.dedupe_key == f"align:{tid}:{digest}"
        assert _jobs(p, p.org_b) == []

    def test_r8_the_stub_completes_the_job(self, w: Any) -> None:
        p = w.p
        made = _call(p, w.a, "POST", "/projects/tasks", {
            "title": "Stub run", "project_id": w.team_a["id"]})
        assert made.status_code == 201

        async def go() -> Any:
            async with _session(p):
                return await worker.run_one(p.org_a, _now_after())

        assert asyncio.run(go()) == "done"
        assert [j.state for j in _jobs(p, p.org_a)] == ["done"]

    def test_r8_org_b_sees_no_job_of_org_a(self, w: Any) -> None:
        p = w.p
        _call(p, w.a, "POST", "/projects/tasks", {
            "title": "Org A work", "project_id": w.team_a["id"]})
        job_id = str(_jobs(p, p.org_a)[0].id)

        async def go() -> tuple[Any, ...]:
            async with _session(p):
                async with tenant_session(p.org_b) as db:
                    seen = (await db.execute(text(
                        "SELECT count(*) FROM jobs WHERE kind = :k"),
                        {"k": trigger.KIND})).scalar_one()
                    got = await queue.get_job(db, job_id)
                claim = await worker.run_one(p.org_b, _now_after())
                async with tenant_session(p.org_a) as db:
                    own = await queue.get_job(db, job_id)
            return seen, got, claim, own

        seen, got, claim, own = asyncio.run(go())
        assert seen == 0 and got is None and claim is None
        assert own is not None and own.state == "queued", "the control read of org A"

    def test_r8_a_text_edit_enqueues_one_job(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="Old title")
        edited = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", {"title": "New title"})
        assert edited.status_code == 200, edited.text
        jobs = _jobs(p, p.org_a)
        assert len(jobs) == 1
        assert jobs[0].dedupe_key == f"align:{tid}:{trigger.content_hash(_row('New title', None))}"
        described = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", {"description": "Body"})
        assert described.status_code == 200
        assert len(_jobs(p, p.org_a)) == 2, "a description edit is a second text"

    def test_r8_an_edit_with_the_same_text_enqueues_none(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="Stays the same")
        for body in (
            {"title": "Stays the same"},
            {"due_at": "2099-01-02T10:00:00+00:00"},
            {"estimate_mins": 45},
            {"description": ""},
        ):
            answer = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", body)
            assert answer.status_code == 200, (body, answer.text)
        assert _jobs(p, p.org_a) == []

    def test_r8_a_status_move_enqueues_none(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="A lane move")
        moved = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}",
                      {"status_id": w.team_a["lanes"]["done"]})
        assert moved.status_code == 200, moved.text
        assert _jobs(p, p.org_a) == []

    def test_r8_a_repeat_of_a_live_key_returns_the_live_job(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="Repeat")
        row = _row("Repeat", None, project_id=w.team_a["id"])

        async def go() -> tuple[Any, Any, Any]:
            async with _session(p):
                async with tenant_session(p.org_a) as db:
                    first = await trigger.enqueue_check(db, tid, p.org_a, row=row)
                    second = await trigger.enqueue_check(db, tid, p.org_a, row=row)
                await worker.run_one(p.org_a, _now_after())
                async with tenant_session(p.org_a) as db:
                    third = await trigger.enqueue_check(db, tid, p.org_a, row=row)
            return first, second, third

        first, second, third = asyncio.run(go())
        assert first.created is True and second.created is False and second.id == first.id
        assert third.created is True and third.id != first.id, "a done job frees the key"

    def test_r8_a_failed_enqueue_leaves_the_write_intact(self, w: Any, monkeypatch: Any) -> None:
        """A real Postgres error, as the member's session would see it. With
        no savepoint it aborts the transaction, and the write is lost."""
        p = w.p

        async def broken(db: Any, *args: Any, **kwargs: Any) -> Any:
            await db.execute(text("SELECT 1 / 0"))

        monkeypatch.setattr(trigger.queue, "enqueue", broken)
        own = task(p, p.org_a, w.a_email, w.own, title="A personal note")
        with structlog.testing.capture_logs() as caps:
            made = _call(p, w.a, "POST", "/projects/tasks", {
                "title": SECRET_TITLE, "project_id": w.team_a["id"]})
            assert made.status_code == 201, made.text
            tid = made.json()["id"]
            # The PATCH reads after the enqueue, so its session must be usable.
            edited = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", {"title": "Second title"})
            assert edited.status_code == 200, edited.text
            moved = _call(p, w.a, "POST", f"/projects/tasks/{own}/move",
                          {"project_id": w.team_a["id"]})
            assert moved.status_code == 200, moved.text

        assert _task_text(p, tid).title == "Second title", "the two writes committed"
        assert str(_task_text(p, own).project_id) == w.team_a["id"], "the move committed"
        assert _jobs(p, p.org_a) == []
        failures = [c for c in caps if c["event"] == "align.enqueue_failed"]
        assert len(failures) == 3
        assert SECRET_TITLE not in repr(failures)

    def test_r8_a_rolled_back_write_leaves_no_job(self, w: Any, monkeypatch: Any) -> None:
        """The handler raises AFTER the enqueue, in the same transaction."""
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="Keeps its title")

        async def fails(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("after the enqueue")

        monkeypatch.setattr(tasks, "attach_agent_state", fails)
        with pytest.raises(RuntimeError):
            _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", {"title": "Lost title"})
        assert _task_text(p, tid).title == "Keeps its title"

        title = f"Rolled back {uuid.uuid4().hex}"

        def no_reply(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("after the enqueue")

        monkeypatch.setattr(tasks, "row_to_dict", no_reply)
        with pytest.raises(RuntimeError):
            _call(p, w.a, "POST", "/projects/tasks", {
                "title": title, "project_id": w.team_a["id"]})
        with p.admin_engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM pm_tasks WHERE title = :t"),
                             {"t": title}).scalar_one() == 0
        assert _jobs(p, p.org_a) == []

    def test_r8_a_closed_gate_writes_no_row(self, w: Any, monkeypatch: Any) -> None:
        p = w.p
        for worker_on, orgs in ((True, ""), (True, p.org_b), (False, p.org_a)):
            _gate(monkeypatch, worker_on=worker_on, orgs=orgs)
            made = _call(p, w.a, "POST", "/projects/tasks", {
                "title": "No row", "project_id": w.team_a["id"]})
            assert made.status_code == 201, made.text
            edited = _call(p, w.a, "PATCH", f"/projects/tasks/{made.json()['id']}",
                           {"title": "Still no row"})
            assert edited.status_code == 200
        assert _jobs(p, p.org_a) == []

    def test_r8_a_capture_into_a_team_project_enqueues_one_job(self, w: Any) -> None:
        p = w.p
        made = _call(p, w.a, "POST", "/projects/intake", {
            "title": "Captured idea", "description": "From a call",
            "project_id": w.team_a["id"]})
        assert made.status_code == 201, made.text
        tid = made.json()["task"]["id"]
        jobs = _jobs(p, p.org_a)
        assert len(jobs) == 1
        assert str(jobs[0].organization_id) == p.org_a and jobs[0].payload == {"task_id": tid}
        digest = trigger.content_hash(_row("Captured idea", "From a call"))
        assert jobs[0].dedupe_key == f"align:{tid}:{digest}"
        assert _jobs(p, p.org_b) == []

    def test_r8_a_capture_into_a_personal_project_enqueues_none(self, w: Any) -> None:
        p = w.p
        made = _call(p, w.a, "POST", "/projects/intake", {
            "title": "A private capture", "project_id": w.own["id"]})
        assert made.status_code == 201, made.text
        assert _jobs(p, p.org_a) == []

    def test_r8_a_personal_task_is_never_enqueued(self, w: Any) -> None:
        p = w.p
        made = _call(p, w.a, "POST", "/projects/tasks", {
            "title": "A note to me", "project_id": w.own["id"]})
        assert made.status_code == 201, made.text
        tid = made.json()["id"]
        edited = _call(p, w.a, "PATCH", f"/projects/tasks/{tid}", {"title": "A new note"})
        assert edited.status_code == 200, edited.text
        moved = _call(p, w.a, "POST", f"/projects/tasks/{tid}/move",
                      {"project_id": w.own_child["id"]})
        assert moved.status_code == 200, moved.text
        assert str(_task_text(p, tid).project_id) == w.own_child["id"]
        assert _jobs(p, p.org_a) == []

    def test_r8_a_move_out_of_a_personal_project_enqueues_one_job(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.own, title="A note to promote")
        moved = _call(p, w.a, "POST", f"/projects/tasks/{tid}/move",
                      {"project_id": w.team_a["id"]})
        assert moved.status_code == 200, moved.text
        jobs = _jobs(p, p.org_a)
        assert len(jobs) == 1 and jobs[0].payload == {"task_id": tid}
        digest = trigger.content_hash(_row("A note to promote", None))
        assert jobs[0].dedupe_key == f"align:{tid}:{digest}"

    def test_r8_a_promoted_subtree_enqueues_one_job_for_each_task(self, w: Any) -> None:
        p = w.p
        parent = task(p, p.org_a, w.a_email, w.own, title="A parent note")
        child = task(p, p.org_a, w.a_email, w.own, title="A child note")
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET parent_task_id = CAST(:p AS uuid) "
                           "WHERE id = CAST(:c AS uuid)"), {"p": parent, "c": child})
        moved = _call(p, w.a, "POST", f"/projects/tasks/{parent}/move",
                      {"project_id": w.team_a["id"], "include_subtasks": True})
        assert moved.status_code == 200, moved.text
        assert {j.payload["task_id"] for j in _jobs(p, p.org_a)} == {parent, child}

    def test_r8_a_move_between_two_team_projects_enqueues_none(self, w: Any) -> None:
        p = w.p
        tid = task(p, p.org_a, w.a_email, w.team_a, title="A team task")
        moved = _call(p, w.a, "POST", f"/projects/tasks/{tid}/move",
                      {"project_id": w.other_team_a["id"]})
        assert moved.status_code == 200, moved.text
        assert str(_task_text(p, tid).project_id) == w.other_team_a["id"]
        assert _jobs(p, p.org_a) == []

    def test_r8_core_answers_the_owner_of_the_project_that_the_task_leaves(self, w: Any) -> None:
        """The privacy guard refuses as before, and adds one answer."""
        p = w.p

        async def go() -> tuple[Any, ...]:
            async with _session(p), tenant_session(p.org_a) as db:
                own = SimpleNamespace(project_id=w.own["id"])
                team = SimpleNamespace(project_id=w.team_a["id"])
                leaves = await core.assert_move_keeps_privacy(db, own, w.team_a["id"])
                stays = await core.assert_move_keeps_privacy(db, own, w.own_child["id"])
                team_move = await core.assert_move_keeps_privacy(db, team, w.other_team_a["id"])
                try:
                    await core.assert_move_keeps_privacy(db, team, w.own["id"])
                except core.HTTPException as exc:
                    return leaves, stays, team_move, exc.status_code
            return leaves, stays, team_move, None

        leaves, stays, team_move, refused = asyncio.run(go())
        assert leaves == w.a_email and stays == w.a_email and team_move is None
        assert refused == 422
