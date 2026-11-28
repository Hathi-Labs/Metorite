"""WS-MCP SW-116 — agents report their external jobs (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.12.11 ("The job") and
the slice SW-116.

A job is a child run of SW-39 with a ``job_kind``: ``github_action``,
``ci_local``, ``merge_queue``, ``release`` or ``review``. The migration
``agent_run_job`` adds the column and two CHECKs.
``POST /projects/agent-runs/{run_id}/jobs`` is its one writer, through the
child writer seam (``agent_runs._INSERT_CHILD_SQL``). The key is
``job:<parent id>:<job id>``. A report moves the row only forward:
``queued`` is ``created``, ``running`` is ``in_progress``, ``passed`` is
``completed`` and ``failed`` is ``failed``. An open job takes ``abandoned``
when its parent ends first, and the map reads it as ``unknown``. A run holds
at most 20 open sub-agents and 20 open jobs, each cap apart.

This file runs on a REAL Postgres, through the real ``/projects`` router,
under the non-privileged role of the phase-4 rehearsal (R8). The structural
fence is ``test_agent_label_fence.py`` (``TestTheJobGrantsNothing``).

Run it on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_run_jobs_r8.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py``, ``M`` is
the migration.

* J1. ``M``: drop the CHECK ``pm_agent_runs_job_on_child``.
  ``test_the_checks_refuse_a_bad_shape``.
* J2. ``G`` ``_MOVE_JOB_SQL``: drop ``status = ANY(:lower)``.
  ``test_a_report_after_the_end_changes_nothing`` and
  ``test_a_queued_after_running_changes_nothing``.
* J3. ``G`` ``_END_CHILDREN_SQL``: give a job the parent's status.
  ``test_a_job_open_when_its_parent_ends_takes_abandoned``.
* J4. ``G`` ``_OPEN_CHILDREN_SQL``: drop ``job_kind IS NULL``.
  ``test_20_open_jobs_do_not_block_a_sub_agent``.
* J5. ``G`` ``report_job``: skip the cap. ``test_the_21st_open_job_is_429``.
* J6. ``G`` ``clean_job_link``: keep the query.
  ``test_the_gateway_drops_the_query_and_the_fragment``.
* J7. ``G`` ``clean_job_link``: skip the user name test.
  ``test_a_link_that_is_not_https_or_names_a_user_is_422``.
* J8. ``G`` ``report_job``: skip ``_check_open_parent`` on a first report.
  ``test_a_closed_parent_takes_no_first_report``.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit.test_agent_run_exclusive import (
    _agent,
    _connection,
    _in_session,
    _member_row,
    _person,
    _seed_task,
)
from tests.unit.test_agent_runs_r8 import CLIENT_ID, _rows, _session
from tests.unit.test_agent_runs_r8 import VIA as R8_VIA
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ALICE = "sw116-alice@job.test"     # the parent's member, org A
BOB = "sw116-bob@job.test"         # another member of org A
OTHER = "sw116-other@job.test"     # a member of org B

_REFUSED = "No open run of yours holds a task with that id."
_OTHER_CONNECTION = "That run belongs to another connection of yours."
_JOB_CAP = ("This run already has 20 open jobs. Report the end of one before you report"
            " another.")
_LINK_REFUSED = "link must be an https link with no user name and no password."
_OPEN = ("created", "in_progress", "awaiting", "stopping")
LINK = "https://github.com/acme/repo/actions/runs/42"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org, org_b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, org=org, org_b=org_b)
    with promoted.admin_engine.begin() as c:
        _member_row(c, org, ALICE, "Alice Job")
        _member_row(c, org, BOB, "Bob Job")
        _member_row(c, org_b, OTHER, "Other Job")
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.conn_alice_2 = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_2 = _agent(ALICE, org, ns.conn_alice_2, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.conn_bob = _connection(promoted, org, BOB, CLIENT_ID, "Claude Code")
    ns.bob = _agent(BOB, org, ns.conn_bob, R8_VIA)
    ns.conn_other = _connection(promoted, org_b, OTHER, CLIENT_ID, "Claude Code")
    ns.other = _agent(OTHER, org_b, ns.conn_other, R8_VIA)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The job writes are dark behind ``AGENT_MAP_ORGS``, as the child writes
    of SW-114, so each test runs with the flag on for every org."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(get_settings(), "agent_map_orgs", "*")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(160_000, 180_000))


def _fresh(w: Any, org: str | None = None, owner: str = ALICE) -> str:
    return _seed_task(w.p, org or w.org, owner, n=next(_numbers))


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


async def _claim(call: Any, user: Any, task: str) -> str:
    answer = await call(user, "POST", f"/projects/tasks/{task}/claim", None)
    assert answer.status_code == 200, answer.text
    run_id = answer.json()["agent_run_id"]
    assert run_id, answer.text
    return str(run_id)


async def _report(call: Any, user: Any, parent: str, job_id: str, state: str, *,
                  kind: str = "github_action", name: str = "pr-check",
                  **extra: Any) -> Any:
    body = {"job_id": job_id, "kind": kind, "name": name, "state": state, **extra}
    return await call(user, "POST", f"/projects/agent-runs/{parent}/jobs", body)


_agent_ids = iter(range(1, 1_000_000))


async def _subagent(call: Any, user: Any, parent: str) -> Any:
    body = {"agent_id": f"sub-{next(_agent_ids)}", "agent_label": "verifier"}
    return await call(user, "POST", f"/projects/agent-runs/{parent}/children", body)


def _child_rows(w: Any, parent: str) -> list[Any]:
    return _rows(w.p, "SELECT id, organization_id, task_id, app, kind, parent_run_id,"
                      " member_email, connection_id, status, session_key, idempotency_key,"
                      " agent_label, job_kind, external_link, started_at, ended_at"
                      " FROM pm_agent_runs WHERE parent_run_id = CAST(:parent AS uuid)"
                      " ORDER BY created_at, id", parent=parent)


def _jobs(w: Any, parent: str) -> list[Any]:
    return [r for r in _child_rows(w, parent) if r.job_kind is not None]


def _status(w: Any, run_id: str) -> str:
    [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                  i=run_id)
    return str(row.status)


def _run_count(w: Any) -> int:
    [row] = _rows(w.p, "SELECT count(*) AS n FROM pm_agent_runs")
    return int(row.n)


@_DB_GATE
class TestTheMigration:

    async def test_the_column_and_the_checks(self, w):
        cols = {r.column_name: (r.data_type, r.is_nullable, r.column_default) for r in _rows(
            w.p, "SELECT column_name, data_type, is_nullable, column_default"
                 " FROM information_schema.columns WHERE table_name = 'pm_agent_runs'"
                 " AND column_name = 'job_kind'")}
        assert cols == {"job_kind": ("text", "YES", None)}
        checks = {r.conname: (r.convalidated, r.definition) for r in _rows(
            w.p, "SELECT conname, convalidated, pg_get_constraintdef(oid) AS definition"
                 " FROM pg_constraint WHERE conrelid = to_regclass('pm_agent_runs')"
                 " AND conname IN ('pm_agent_runs_job_kind_check',"
                 " 'pm_agent_runs_job_on_child')")}
        assert set(checks) == {"pm_agent_runs_job_kind_check", "pm_agent_runs_job_on_child"}
        assert all(valid for valid, _ in checks.values()), checks
        for kind in ("github_action", "ci_local", "merge_queue", "release", "review"):
            assert kind in checks["pm_agent_runs_job_kind_check"][1], kind
        assert "child" in checks["pm_agent_runs_job_on_child"][1]
        index = [r.indexdef for r in _rows(
            w.p, "SELECT indexdef FROM pg_indexes WHERE tablename = 'pm_agent_runs'")]
        assert not [d for d in index if "job_kind" in d], index

    async def test_the_checks_refuse_a_bad_shape(self, w):
        """J1. A plain run with a job kind, and a child with an unknown kind,
        each fail at the database."""
        async with _session(w.p) as call:
            parent = await _claim(call, _in_session(w.alice, _key()), _fresh(w))
        [row] = _rows(w.p, "SELECT task_id, member_email, client_via FROM pm_agent_runs"
                           " WHERE id = CAST(:i AS uuid)", i=parent)
        bad = (("run", None, row.task_id, "ci_local"),
               ("child", parent, None, "deploy"))
        for kind, parent_id, task_id, job_kind in bad:
            with pytest.raises(IntegrityError), w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, kind, parent_run_id,"
                    " member_email, client_via, status, started_by, job_kind)"
                    " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :k, CAST(:p AS uuid), :m,"
                    " :v, 'in_progress', :v, :j)"),
                    {"o": w.org, "t": task_id, "k": kind, "p": parent_id,
                     "m": row.member_email, "v": row.client_via, "j": job_kind})


@_DB_GATE
class TestTheJobReports:

    async def test_a_running_report_writes_one_job(self, w):
        """The first "Done when" of SW-116. One child with ``job_kind`` set
        and status ``in_progress``. It has no task, no app and no session
        key, and its key is ``job:<parent>:<job id>``."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            made = await _report(call, session, parent, "run-42", "running", link=LINK)
        assert made.status_code == 201, made.text
        assert (made.json()["created"], made.json()["moved"]) == (True, False)
        run = made.json()["run"]
        assert (run["kind"], run["parent_run_id"], run["status"]) == (
            "child", parent, "in_progress")
        assert (run["job_kind"], run["agent_label"], run["external_link"]) == (
            "github_action", "pr-check", LINK)
        assert run["started_at"] and run["ended_at"] is None
        [row] = _child_rows(w, parent)
        assert str(row.id) == run["id"]
        assert (row.task_id, row.app, row.session_key) == (None, None, None)
        assert row.idempotency_key == f"job:{parent}:run-42"
        assert (str(row.organization_id), row.member_email, str(row.connection_id)) == (
            w.org, ALICE, w.conn_alice)
        assert _status(w, parent) == "in_progress"

    async def test_a_passed_report_moves_the_same_row_to_completed(self, w):
        """The second "Done when". The same row moves, with ``ended_at``."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            first = await _report(call, session, parent, "run-1", "running")
            done = await _report(call, session, parent, "run-1", "passed", link=LINK)
        assert first.status_code == 201 and done.status_code == 200, done.text
        assert (done.json()["created"], done.json()["moved"]) == (False, True)
        assert done.json()["run"]["id"] == first.json()["run"]["id"]
        [row] = _child_rows(w, parent)
        assert row.status == "completed" and row.ended_at is not None
        assert row.started_at is not None and row.external_link == LINK

    async def test_a_report_after_the_end_changes_nothing(self, w):
        """J2. The third "Done when". Any report after ``passed`` or
        ``failed`` changes nothing and answers the row as it is."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            await _report(call, session, parent, "run-1", "running")
            await _report(call, session, parent, "run-1", "failed")
            [before] = _child_rows(w, parent)
            later = [await _report(call, session, parent, "run-1", state,
                                   link="https://example.com/other")
                     for state in ("passed", "running", "queued", "failed")]
        for answer in later:
            assert answer.status_code == 200, answer.text
            assert (answer.json()["created"], answer.json()["moved"]) == (False, False)
            assert answer.json()["run"]["status"] == "failed"
        [after] = _child_rows(w, parent)
        assert after == before
        assert after.external_link is None

    async def test_a_queued_after_running_changes_nothing(self, w):
        """J2. The fourth "Done when". A job never steps back."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            await _report(call, session, parent, "run-1", "running")
            [before] = _child_rows(w, parent)
            back = await _report(call, session, parent, "run-1", "queued")
            again = await _report(call, session, parent, "run-1", "running")
        for answer in (back, again):
            assert answer.status_code == 200, answer.text
            assert answer.json()["moved"] is False
        [after] = _child_rows(w, parent)
        assert after == before and after.status == "in_progress"

    async def test_a_queued_job_has_no_start_until_it_runs(self, w):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            queued = await _report(call, session, parent, "mq-7", "queued",
                                   kind="merge_queue", name="merge queue")
            [row] = _child_rows(w, parent)
            assert (row.status, row.started_at, row.ended_at) == ("created", None, None)
            running = await _report(call, session, parent, "mq-7", "running",
                                    kind="merge_queue", name="merge queue")
        assert queued.status_code == 201 and running.status_code == 200
        assert running.json()["moved"] is True
        [row] = _child_rows(w, parent)
        assert row.status == "in_progress" and row.started_at is not None
        assert row.job_kind == "merge_queue"

    async def test_a_first_report_of_an_end_writes_an_ended_job(self, w):
        """A lane may miss the start. A first ``passed`` writes the job ended."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            made = await _report(call, session, parent, "ci-1", "passed", kind="ci_local")
        assert made.status_code == 201, made.text
        [row] = _child_rows(w, parent)
        assert row.status == "completed" and row.ended_at is not None

    async def test_a_job_open_when_its_parent_ends_takes_abandoned(self, w, monkeypatch):
        """J3. The fifth "Done when". An open job takes ``abandoned``. An open
        sub-agent takes the parent's status (SW-39). An ended job keeps its
        own status. The one-session map read gives ``unknown`` for the
        abandoned job and the status for the others."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            await _report(call, session, parent, "open-1", "running")
            await _report(call, session, parent, "queued-1", "queued")
            await _report(call, session, parent, "done-1", "passed")
            sub = (await _subagent(call, session, parent)).json()["run"]["id"]
            fin = await call(session, "POST", f"/projects/agent-runs/{parent}/finish", {})
            assert fin.status_code == 200, fin.text
            late = await _report(call, session, parent, "open-1", "passed")
            monkeypatch.setattr(get_settings(), "agents_view_orgs", "*")
            monkeypatch.setattr(get_settings(), "agent_lenses_orgs", "*")
            summary = await call(w.alice_person, "GET",
                                 "/projects/agents/overview?shape=map&detail=summary", None)
            assert summary.status_code == 200, summary.text
            [node] = [s["id"] for s in summary.json()["sessions"]]
            one = await call(w.alice_person, "GET",
                             f"/projects/agents/overview?shape=map&session={node}", None)
        by_key = {r.idempotency_key: r for r in _child_rows(w, parent)}
        assert by_key[f"job:{parent}:open-1"].status == "abandoned"
        assert by_key[f"job:{parent}:queued-1"].status == "abandoned"
        assert by_key[f"job:{parent}:done-1"].status == "completed"
        assert all(r.ended_at is not None for r in by_key.values())
        assert _status(w, parent) == "completed" and _status(w, sub) == "completed"
        assert late.status_code == 200 and late.json()["moved"] is False
        assert late.json()["run"]["status"] == "abandoned"
        assert one.status_code == 200, one.text
        states = {c["child_id"]: (c["state"], c["job_kind"]) for c in one.json()["children"]}
        ids = {k: str(r.id) for k, r in by_key.items()}
        assert states[ids[f"job:{parent}:open-1"]] == ("unknown", "github_action")
        assert states[ids[f"job:{parent}:done-1"]] == ("completed", "github_action")
        assert states[sub] == ("completed", None)

    async def test_a_run_of_another_connection_is_403_and_of_another_member_404(self, w):
        """The sixth "Done when". An own run of another connection is 403, for
        a first report and for a move. A run of another member is the one
        404."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            await _report(call, session, parent, "mine", "running")
            alice_2 = _in_session(w.alice_2, _key())
            bob = _in_session(w.bob, _key())
            other_conn = [await _report(call, alice_2, parent, "new", "running"),
                          await _report(call, alice_2, parent, "mine", "passed")]
            other_member = [await _report(call, bob, parent, "new", "running"),
                            await _report(call, bob, parent, "mine", "passed")]
        for answer in other_conn:
            assert answer.status_code == 403, answer.text
            assert answer.json() == {"detail": _OTHER_CONNECTION}
        for answer in other_member:
            assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert [r.status for r in _child_rows(w, parent)] == ["in_progress"]

    async def test_the_21st_open_job_is_429(self, w):
        """J5. The seventh "Done when", first half. An ended job frees a slot."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            for i in range(20):
                made = await _report(call, session, parent, f"job-{i}", "running")
                assert made.status_code == 201, made.text
            over = await _report(call, session, parent, "job-20", "running")
            await _report(call, session, parent, "job-0", "passed")
            freed = await _report(call, session, parent, "job-20", "queued")
        assert over.status_code == 429 and over.json() == {"detail": _JOB_CAP}
        assert freed.status_code == 201, freed.text
        assert len(_jobs(w, parent)) == 21

    async def test_20_open_jobs_do_not_block_a_sub_agent(self, w):
        """J4. The seventh "Done when", second half. Each cap is its own:
        20 open jobs leave room for a sub-agent, and 20 open sub-agents leave
        room for a job."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            for i in range(20):
                assert (await _report(call, session, parent, f"j{i}", "running")
                        ).status_code == 201
            sub = await _subagent(call, session, parent)
            for _ in range(19):
                assert (await _subagent(call, session, parent)).status_code == 201
            sub_over = await _subagent(call, session, parent)
            job_over = await _report(call, session, parent, "j20", "running")
            job_end = await _report(call, session, parent, "j0", "failed")
        assert sub.status_code == 201, sub.text
        assert sub_over.status_code == 429
        assert job_over.status_code == 429
        assert job_end.status_code == 200 and job_end.json()["moved"] is True

    @pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "é", "a\nb"])
    async def test_a_bad_job_id_is_422(self, w, bad):
        """The eighth "Done when"."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            answer = await _report(call, session, parent, bad, "running")
        assert answer.status_code == 422, answer.text
        assert _child_rows(w, parent) == []

    async def test_a_bad_kind_state_name_or_extra_key_is_422(self, w):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            answers = [
                await _report(call, session, parent, "j1", "running", kind="deploy"),
                await _report(call, session, parent, "j1", "done"),
                await _report(call, session, parent, "j1", "running", name="​\x07"),
                await _report(call, session, parent, "j1", "running", task_id="x"),
                await call(session, "POST", f"/projects/agent-runs/{parent}/jobs",
                           {"job_id": "j1", "state": "running"}),
            ]
        for answer in answers:
            assert answer.status_code == 422, answer.text
        assert _child_rows(w, parent) == []

    @pytest.mark.parametrize("bad", [
        "http://github.com/a", "ftp://x.test/a", "https://user@github.com/a",
        "https://user:pw@github.com/a", "https://:pw@github.com/a", "https:///no-host",
        "https://git hub.com/a", "https://x.test/‮a", "javascript:alert(1)",
    ])
    async def test_a_link_that_is_not_https_or_names_a_user_is_422(self, w, bad):
        """J7. The ninth "Done when"."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            answer = await _report(call, session, parent, "j1", "running", link=bad)
        assert answer.status_code == 422, answer.text
        assert answer.json() == {"detail": _LINK_REFUSED}
        assert _child_rows(w, parent) == []

    async def test_the_gateway_drops_the_query_and_the_fragment(self, w):
        """J6. The tenth "Done when". A token in a query never reaches the row."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            made = await _report(call, session, parent, "j1", "running",
                                 link=LINK + "?token=SECRET123&x=1#frag")
            moved = await _report(call, session, parent, "j1", "passed",
                                  link="https://ci.example.com/runs/9?sig=SECRET456")
        assert made.status_code == 201 and made.json()["run"]["external_link"] == LINK
        assert moved.json()["run"]["external_link"] == "https://ci.example.com/runs/9"
        [row] = _child_rows(w, parent)
        assert row.external_link == "https://ci.example.com/runs/9"
        raw = made.text + moved.text
        assert "SECRET123" not in raw and "SECRET456" not in raw

    async def test_org_b_sees_nothing_of_org_a(self, w):
        """The eleventh "Done when". A run of org A reads as an unknown run
        to org B, for a report and for the read of its children. Org A's
        read of a run of org B is the same 404."""
        alice = _in_session(w.alice, _key())
        other = _in_session(w.other, _key())
        async with _session(w.p) as call:
            mine = await _claim(call, alice, _fresh(w))
            theirs = await _claim(call, other, _fresh(w, w.org_b, OTHER))
            await _report(call, alice, mine, "a-1", "running", link=LINK)
            await _report(call, other, theirs, "b-1", "running",
                          link="https://example.com/b")
            b_report = await _report(call, other, mine, "a-1", "passed")
            b_new = await _report(call, other, mine, "b-2", "running")
            b_read = await call(w.other, "GET", f"/projects/agent-runs/{mine}/children", None)
            a_read = await call(w.alice, "GET", f"/projects/agent-runs/{theirs}/children",
                                None)
            a_own = await call(w.alice, "GET", f"/projects/agent-runs/{mine}/children", None)
        for answer in (b_report, b_new):
            assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert b_read.status_code == 404 and a_read.status_code == 404
        assert LINK not in b_read.text and "example.com/b" not in a_read.text
        assert [r["job_kind"] for r in a_own.json()["rows"]] == ["github_action"]
        assert [r.status for r in _child_rows(w, mine)] == ["in_progress"]
        assert len(_child_rows(w, theirs)) == 1

    async def test_a_person_body_is_refused(self, w):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            person = await _report(call, w.alice_person, parent, "j1", "running")
        assert person.status_code == 422, person.text
        assert person.json() == {"detail": "Only an agent reports a job, under one of its runs."}
        assert _child_rows(w, parent) == []

    async def test_a_closed_parent_takes_no_first_report(self, w):
        """J8. A closed parent takes no new job. The report of a job that it
        already ended answers the row and changes nothing."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            await _report(call, session, parent, "old", "running")
            fin = await call(session, "POST", f"/projects/agent-runs/{parent}/release", {})
            assert fin.status_code == 200, fin.text
            new = await _report(call, session, parent, "new", "running")
            old = await _report(call, session, parent, "old", "passed")
        assert new.status_code == 404 and new.json() == {"detail": _REFUSED}
        assert old.status_code == 200 and old.json()["moved"] is False
        assert [r.status for r in _child_rows(w, parent)] == ["abandoned"]

    async def test_a_job_is_never_a_task_run(self, w):
        """A job holds no task, so the run list and the task's runs never
        name it, and a second claim on the task still meets ``task_held``."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            job = (await _report(call, session, parent, "j1", "running")).json()["run"]["id"]
            mine = await call(w.alice_person, "GET", "/projects/agent-runs", None)
            on_task = await call(w.alice_person, "GET", f"/projects/tasks/{task}/agent-runs",
                                 None)
            bob = await call(_in_session(w.bob, _key()), "POST",
                             f"/projects/tasks/{task}/claim", None)
        assert job not in mine.text and job not in on_task.text
        assert bob.status_code == 409 and bob.json()["detail"]["error"] == "task_held"


@_DB_GATE
class TestTheDarkRoute:

    async def test_the_flag_off_gives_the_unknown_run_404_and_writes_nothing(
            self, w, monkeypatch):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            assert (await _report(call, session, parent, "on-1", "running")).status_code == 201
            unknown = await _report(call, session, str(uuid.uuid4()), "x", "running")
            before = _run_count(w)
            monkeypatch.setattr(get_settings(), "agent_map_orgs", "")
            off = [await _report(call, session, parent, "off-1", "running"),
                   await _report(call, session, parent, "on-1", "passed"),
                   await _report(call, w.alice_person, parent, "p-1", "running"),
                   await _report(call, session, parent, "bad", "running", link="http://x")]
            after = _run_count(w)
        assert unknown.status_code == 404
        for answer in off:
            assert answer.status_code == 404, answer.text
            assert answer.content == unknown.content
        assert after == before
        assert [r.status for r in _child_rows(w, parent)] == ["in_progress"]


class TestTheJobRules:
    """Hermetic: the rules of the writer, read from the code."""

    def test_the_end_of_the_children_gives_a_job_abandoned(self) -> None:
        from gateway.routes.projects import agent_runs

        sql = agent_runs._END_CHILDREN_SQL
        assert "CASE WHEN job_kind IS NULL THEN :status ELSE :abandoned END" in sql
        assert agent_runs.JOB_ABANDONED_STATUS == "abandoned"
        assert agent_runs.JOB_ABANDONED_STATUS in agent_runs.TERMINAL_STATUSES

    def test_the_move_names_the_job_subject_and_moves_only_forward(self) -> None:
        from gateway.routes.projects import agent_runs

        sql = agent_runs._MOVE_JOB_SQL
        for needed in ("kind = 'child'", "job_kind IS NOT NULL",
                       "parent_run_id = CAST(:rid AS uuid)", "idempotency_key = :key",
                       "member_email = :me", "status = ANY(:lower)"):
            assert needed in sql, needed
        assert agent_runs._lower_statuses("created") == []
        assert agent_runs._lower_statuses("in_progress") == ["created"]
        for final in ("completed", "failed"):
            assert agent_runs._lower_statuses(final) == ["created", "in_progress"]

    def test_the_two_caps_read_apart(self) -> None:
        from gateway.routes.projects import agent_runs

        assert "job_kind IS NULL" in agent_runs._OPEN_CHILDREN_SQL
        assert "job_kind IS NOT NULL" in agent_runs._OPEN_JOBS_SQL
        assert agent_runs.MAX_OPEN_JOBS == agent_runs.MAX_OPEN_CHILDREN == 20

    def test_the_states_map_to_the_run_statuses_in_order(self) -> None:
        from gateway.routes.projects import agent_runs

        assert agent_runs.JOB_STATUS == {"queued": "created", "running": "in_progress",
                                         "passed": "completed", "failed": "failed"}
        ranks = [agent_runs.JOB_RANK[agent_runs.JOB_STATUS[s]] for s in agent_runs.JOB_STATES]
        assert ranks == [0, 1, 2, 2]
        assert set(agent_runs.JOB_STATUS.values()) <= set(agent_runs.RUN_STATUSES)

    @pytest.mark.parametrize(("raw", "clean"), [
        (None, None),
        ("", None),
        ("https://github.com/a/b", "https://github.com/a/b"),
        ("HTTPS://github.com/a/b", "https://github.com/a/b"),
        ("https://github.com/a/b?x=1", "https://github.com/a/b"),
        ("https://github.com/a/b#frag", "https://github.com/a/b"),
        ("https://box.local:8443/ci/7?t=1#x", "https://box.local:8443/ci/7"),
    ])
    def test_the_link_clean(self, raw, clean) -> None:
        from gateway.routes.projects import agent_runs

        assert agent_runs.clean_job_link(raw) == clean

    def test_the_key_of_a_job_differs_from_the_key_of_a_sub_agent(self) -> None:
        from gateway.routes.projects import agent_runs

        parent = str(uuid.uuid4())
        assert agent_runs.job_key(parent, "a") == f"job:{parent}:a"
        assert agent_runs.job_key(parent, "a") != agent_runs.child_key(parent, "a")
        assert len(agent_runs.job_key(parent, "x" * agent_runs.JOB_ID_MAX)) <= 128
