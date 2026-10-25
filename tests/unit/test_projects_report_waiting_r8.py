"""WS-27bn RA-3 — "Waiting on people", on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 C, the §5 row
"Waiting on a person", and §8 RA-3.

``test_projects_report_waiting.py`` pins the Python twin of each rule. This
file pins the SQL of ``analytics_waiting.py`` through the real report
preview (``POST /projects/reports/preview``, the path of the Overview), as
the non-privileged role of the phase-4 rehearsal (the ``promoted``
fixture), on a database with the whole ladder and two organizations:

* **Q1** the questions list holds each run in ``awaiting`` with an open
  question, the oldest first. An answered question, a run that works, and a
  run on a done task are not in it.
* **R1** the reviews list holds a task that an agent moved into a review
  lane, and an open pull request on a task that an agent worked. A person
  act after either arm takes the task out, and an automation act does not.
* **H1** the handoffs list holds an open ``blocks`` link where an agent
  holds one task and a person holds the other, with the side of the agent.
* **A1** the answer time: the median and the p90 over the questions of the
  period, with the unanswered question out of both and counted. The Python
  twin agrees, from the rows in the database.
* **V1** §7 rule 1: a member who sees one task reads that task only, and the
  other organization reads nothing.
* **F1** with the runs flag off, the questions and the answer time read
  nothing.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra3 \\
        uv run pytest tests/unit/test_projects_report_waiting_r8.py -v -rs

The suite carries ``_DB_GATE`` through ``pytestmark``, so it skips where no
ladder URL is set, and its fixtures never start there.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

A_EMAIL = "ra3-a@waiting-r8.test"
PERSON = "ra3-p@waiting-r8.test"
M_EMAIL = "ra3-m@waiting-r8.test"
B_EMAIL = "ra3-b@waiting-r8.test"
IN_APP = "agent:coder"
GITHUB = "system:github"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    ns.m = b._principal(M_EMAIL, promoted.org_a, unrestricted=False)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    b.member(promoted, promoted.org_a, PERSON)
    b.member(promoted, promoted.org_a, M_EMAIL)
    b.member(promoted, promoted.org_b, B_EMAIL)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_task_links"))
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    world.root["lanes"]["review"] = _lane(world, "In review", "in_progress")
    world.root["lanes"]["in_progress"] = _lane(world, "In progress", "in_progress")
    # "Preview" holds "review" as a substring and not as a word.
    world.root["lanes"]["preview"] = _lane(world, "Preview", "in_progress")
    return world


# ── Seeds ────────────────────────────────────────────────────────────────────


def _lane(w: Any, name: str, category: str) -> str:
    sid = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
            "category, position) VALUES (:id, :o, :p, :n, :c, 50)"),
            {"id": sid, "o": w.p.org_a, "p": w.root["id"], "n": name, "c": category})
    return sid


def _task(w: Any, lane: str = "todo", *, title: str = "Fix the extruder") -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.root, category=lane, title=title)


def _assign(w: Any, tid: str, who: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
            "assigned_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :a, :e)"),
            {"o": w.p.org_a, "t": tid, "a": who, "e": A_EMAIL})


def _act(
    w: Any, tid: str, by: str, *, kind: str = "comment", via: str | None = None,
    minutes_ago: int = 5, meta: dict[str, Any] | None = None,
) -> None:
    """One timeline row, at a chosen time."""
    body = dict(meta or {})
    if via is not None:
        body["via"] = via
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, "
            "body, meta, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :k, "
            ":by, 'x', CAST(:m AS jsonb), now() - make_interval(mins => :ago))"),
            {"o": w.p.org_a, "t": tid, "k": kind, "by": by,
             "m": json.dumps(body), "ago": minutes_ago})


def _move(w: Any, tid: str, by: str, *, via: str | None = None, minutes_ago: int = 60) -> None:
    """The status move into the lane the task sits in."""
    _act(w, tid, by, kind="status_change", via=via, minutes_ago=minutes_ago,
         meta={"from": "To do", "to": "In review",
               "from_category": "todo", "to_category": "in_progress"})


def _run_activity(
    w: Any, rid: str, tid: str, kind: str, *, minutes_ago: int,
    reply_to: str | None = None, org: str | None = None,
) -> str:
    """One run activity at a chosen time. A prompt may answer a question."""
    aid = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_run_activities (id, organization_id, run_id, task_id, "
            "type, content, actor, actor_via, in_reply_to, created_at) VALUES "
            "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:r AS uuid), CAST(:t AS uuid), "
            ":k, CAST(:c AS jsonb), :e, :via, CAST(:reply AS uuid), "
            "now() - make_interval(mins => :ago))"),
            {"id": aid, "o": org or w.p.org_a, "r": rid, "t": tid, "k": kind,
             "c": json.dumps({"body": kind}), "e": A_EMAIL,
             "via": None if kind == "prompt" else b.VIA, "reply": reply_to,
             "ago": minutes_ago})
    return aid


def _question(
    w: Any, tid: str, *, status: str = "awaiting", asked_ago: int = 120,
    answered_after: int | None = None,
) -> str:
    """A run with a question, and, when given, its answer."""
    rid = b.run(w.p, w.p.org_a, tid, A_EMAIL, status)
    qid = _run_activity(w, rid, tid, "elicitation", minutes_ago=asked_ago)
    if answered_after is not None:
        _run_activity(w, rid, tid, "prompt", minutes_ago=asked_ago - answered_after,
                      reply_to=qid)
    return rid


def _link(w: Any, blocker: str, blocked: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_links (organization_id, source_task_id, target_task_id, "
            "link_type, created_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), "
            "CAST(:t AS uuid), 'blocks', :e)"),
            {"o": w.p.org_a, "s": blocker, "t": blocked, "e": A_EMAIL})


async def _waiting(w: Any, user: Any, *, project: bool = True, weeks: int = 12) -> dict[str, Any]:
    """The `waiting` section, through the preview that the Overview calls."""
    body: dict[str, Any] = {"config": {
        "sections": ["waiting"], "weeks": weeks, "skip_current_week": False,
    }}
    if project:
        body["project_id"] = w.root["id"]
    async with b._session(w.p) as call:
        got = await call(user, "POST", "/projects/reports/preview", body)
    assert got.status_code == 200, got.text
    return got.json()["sections"]["waiting"]


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [r["id"] for r in rows]


# ── Q1 ───────────────────────────────────────────────────────────────────────


class TestQuestions:
    async def test_q1_open_questions_oldest_first(self, w):
        newer = _task(w, title="Newer question")
        older = _task(w, title="Older question")
        answered = _task(w)
        working = _task(w)
        closed = _task(w, "done")
        _question(w, newer, asked_ago=120)
        _question(w, older, asked_ago=3 * 24 * 60)
        # Answered: the run waits no more, as the prompt moved it on.
        _question(w, answered, status="in_progress", answered_after=10)
        # A question on a run that works is not a run that waits.
        _question(w, working, status="in_progress")
        _question(w, closed)
        got = await _waiting(w, w.a)
        assert _ids(got["questions"]) == [older, newer]
        assert got["questions_total"] == 2
        from acb_auth import agent_oauth

        first = got["questions"][0]
        # §6.8 E2: the agent, and the member it works for.
        assert first["member"] == A_EMAIL
        assert first["client_via"] == b.VIA
        assert first["agent"] == agent_oauth.client_label(b.VIA)
        assert 71.5 < first["age_hours"] < 72.5
        assert got["summary"].startswith("2 agent questions wait for an answer.")
        assert "The oldest is 3 days old." in got["summary"]
        assert got["agent_runs_seen"] is True

    async def test_q1_two_members_on_one_task_make_two_rows(self, w):
        """RA-3 review. Each run is its own row, and `run_id` tells the two
        apart, so the client keys each row on it."""
        tid = _task(w)
        for who in (A_EMAIL, PERSON):
            rid = b.run(w.p, w.p.org_a, tid, who, "awaiting")
            _run_activity(w, rid, tid, "elicitation", minutes_ago=60)
        got = await _waiting(w, w.a)
        assert [r["id"] for r in got["questions"]] == [tid, tid]
        assert len({r["run_id"] for r in got["questions"]}) == 2
        assert {r["member"] for r in got["questions"]} == {A_EMAIL, PERSON}

    async def test_q1_an_answered_awaiting_run_has_no_open_question(self, w):
        """The run still reads `awaiting`, and its only question has an
        answer. `_OPEN_ELICITATIONS_SQL` finds none, so it is not listed."""
        tid = _task(w)
        _question(w, tid, answered_after=5)
        got = await _waiting(w, w.a)
        assert got["questions"] == [] and got["questions_total"] == 0


# ── R1 ───────────────────────────────────────────────────────────────────────


REVIEWS = {
    "lane_agent": True,
    "lane_agent_then_github": True,
    "lane_agent_then_person": False,
    "lane_person": False,
    "progress_agent": False,
    # RA-3 review: "Preview" holds "review" and is not a review lane.
    "preview_agent": False,
    "pr_worked": True,
    "pr_not_worked": False,
    "pr_draft": False,
    "pr_worked_then_person": False,
}


def _lane_of(name: str) -> str:
    """The lane each seeded task sits in, from the start of its name."""
    for prefix, lane in (("lane", "review"), ("progress", "in_progress"),
                         ("preview", "preview")):
        if name.startswith(prefix):
            return lane
    return "todo"


def _seed_reviews(w: Any) -> dict[str, str]:
    t = {name: _task(w, _lane_of(name), title=name) for name in REVIEWS}
    _move(w, t["lane_agent"], A_EMAIL, via=b.VIA, minutes_ago=300)
    _move(w, t["lane_agent_then_github"], A_EMAIL, via=b.VIA, minutes_ago=200)
    _act(w, t["lane_agent_then_github"], GITHUB, via="github", minutes_ago=100)
    _move(w, t["lane_agent_then_person"], A_EMAIL, via=b.VIA, minutes_ago=200)
    _act(w, t["lane_agent_then_person"], PERSON, minutes_ago=100)
    _move(w, t["lane_person"], PERSON, minutes_ago=200)
    _move(w, t["progress_agent"], A_EMAIL, via=b.VIA, minutes_ago=200)
    _move(w, t["preview_agent"], A_EMAIL, via=b.VIA, minutes_ago=200)
    for name in ("pr_worked", "pr_draft", "pr_worked_then_person"):
        b.run(w.p, w.p.org_a, t[name], A_EMAIL, "completed",
              created="now() - interval '10 hours'")
    b.ref(w.p, w.p.org_a, t["pr_worked"], 1, "open")
    b.ref(w.p, w.p.org_a, t["pr_not_worked"], 2, "open")
    b.ref(w.p, w.p.org_a, t["pr_draft"], 3, "draft")
    b.ref(w.p, w.p.org_a, t["pr_worked_then_person"], 4, "open")
    # The person acts after the pull request, which `ref` stamps now().
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_task_refs SET created_at = now() - interval '2 hours'"
            " WHERE task_id = CAST(:t AS uuid)"), {"t": t["pr_worked_then_person"]})
    _act(w, t["pr_worked_then_person"], PERSON, minutes_ago=30)
    return t


class TestReviews:
    async def test_r1_agent_work_that_waits_for_a_review(self, w):
        t = _seed_reviews(w)
        got = await _waiting(w, w.a)
        listed = set(_ids(got["reviews"]))
        assert {name: t[name] in listed for name in REVIEWS} == REVIEWS
        assert got["reviews_total"] == sum(REVIEWS.values())
        by_id = {r["id"]: r for r in got["reviews"]}
        assert by_id[t["lane_agent"]]["reasons"] == ["review_lane"]
        assert by_id[t["pr_worked"]]["reasons"] == ["pull_request"]
        assert by_id[t["pr_worked"]]["pr_url"].endswith("/pull/1")
        # The longest wait first: the lane move of five hours ago.
        assert got["reviews"][0]["id"] == t["lane_agent"]
        assert by_id[t["lane_agent"]]["member"] == A_EMAIL

    async def test_r1_the_lane_rule_is_one_rule_in_python_and_in_sql(self, w):
        """The SQL twin and the Python twin answer one table of names."""
        from gateway.routes.projects import analytics_waiting as wt

        names = ["In review", "Code REVIEW", "Reviewing", "In-review", "Preview",
                 "Peer_review", "In progress", "Reviewed and done", "", "review"]
        values = ", ".join(f"(:n{i})" for i in range(len(names)))
        with w.p.admin_engine.begin() as c:
            rows = c.execute(text(
                f"SELECT s.name, {wt.review_lane_sql('s')} AS lane"
                f"  FROM (SELECT CAST(v.n AS text) AS name,"
                f"               CAST(:started_cat AS text) AS category"
                f"          FROM (VALUES {values}) AS v(n)) s"),
                {"started_cat": "in_progress",
                 **{f"n{i}": n for i, n in enumerate(names)}}).fetchall()
        sql = {r.name: bool(r.lane) for r in rows}
        assert sql == {n: wt.is_review_lane("in_progress", n) for n in names}
        assert sql["Preview"] is False and sql["Peer_review"] is False
        assert sql["In review"] and sql["Reviewing"] and sql["In-review"]


# ── H1 ───────────────────────────────────────────────────────────────────────


class TestHandoffs:
    async def test_h1_a_blocks_link_between_a_person_and_an_agent(self, w):
        person_blocker = _task(w, title="person blocker")
        agent_blocked = _task(w, title="agent blocked")
        agent_blocker = _task(w, title="agent blocker")
        person_blocked = _task(w, title="person blocked")
        p1, p2 = _task(w), _task(w)
        done_agent = _task(w, "done")
        p3 = _task(w)
        _assign(w, person_blocker, PERSON)
        _assign(w, agent_blocked, IN_APP)
        _assign(w, agent_blocker, PERSON)
        b.run(w.p, w.p.org_a, agent_blocker, A_EMAIL, "in_progress")
        _assign(w, person_blocked, PERSON)
        _assign(w, p1, PERSON)
        _assign(w, p2, PERSON)
        _assign(w, done_agent, IN_APP)
        _assign(w, p3, PERSON)
        _link(w, person_blocker, agent_blocked)
        _link(w, agent_blocker, person_blocked)
        _link(w, p1, p2)
        # A done blocker blocks nothing.
        _link(w, done_agent, p3)
        got = await _waiting(w, w.a)
        sides = {h["id"]: h["agent_side"] for h in got["handoffs"]}
        assert sides == {agent_blocked: "blocked", person_blocked: "blocker"}
        assert got["handoffs_total"] == 2
        by_id = {h["id"]: h for h in got["handoffs"]}
        assert by_id[agent_blocked]["agent"] == "coder"
        assert by_id[agent_blocked]["person"] == PERSON
        assert by_id[agent_blocked]["blocker_id"] == person_blocker
        assert by_id[person_blocked]["member"] == A_EMAIL


# ── A1 ───────────────────────────────────────────────────────────────────────


class TestAnswerTime:
    async def test_a1_the_median_and_the_p90_of_the_answers(self, w):
        from gateway.routes.projects import analytics_waiting as wt

        tid = _task(w)
        # Answers after 60, 180 and 300 minutes: 1, 3 and 5 hours.
        for asked, after in ((600, 60), (1200, 180), (1800, 300)):
            _question(w, tid, status="completed", asked_ago=asked, answered_after=after)
        # No answer: out of both figures, and counted.
        _question(w, tid, asked_ago=120)
        # Before the 12-week window: not in the period at all.
        _question(w, tid, status="completed", asked_ago=100 * 24 * 60, answered_after=60)
        got = await _waiting(w, w.a)
        assert got["answer_time"] == {
            "asked": 4, "answered": 3, "unanswered": 1,
            "median_hours": 3.0, "p90_hours": 4.6,
        }
        # The Python twin, from the rows in the database.
        with w.p.admin_engine.begin() as c:
            pairs = [
                (r.asked_at, r.answered_at) for r in c.execute(text(
                    "SELECT e.created_at AS asked_at,"
                    " (SELECT min(p.created_at) FROM pm_agent_run_activities p"
                    "   WHERE p.in_reply_to = e.id) AS answered_at"
                    "  FROM pm_agent_run_activities e"
                    " WHERE e.task_id = CAST(:t AS uuid) AND e.type = 'elicitation'"
                    "   AND e.created_at > now() - interval '12 weeks'"), {"t": tid})
            ]
        assert wt.answer_time(pairs) == got["answer_time"]


# ── V1 ───────────────────────────────────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds no grant, so M sees the task M holds and
        nothing else. B is in the other organization and sees nothing."""
        mine = _task(w, title="mine")
        _assign(w, mine, M_EMAIL)
        _question(w, mine)
        other = _task(w, title="other")
        _question(w, other)
        review = _task(w, "review")
        _move(w, review, A_EMAIL, via=b.VIA)
        blocker, blocked = _task(w), _task(w)
        _assign(w, blocker, PERSON)
        _assign(w, blocked, IN_APP)
        _link(w, blocker, blocked)
        # RA-3 review (R7). M can open the blocked task, which an agent holds,
        # and NOT the blocker, which a person holds. The row would print the
        # blocker's title, so M must get no row at all.
        m_blocked = _task(w, title="m blocked")
        _assign(w, m_blocked, M_EMAIL)
        _assign(w, m_blocked, IN_APP)
        hidden_blocker = _task(w, title="hidden blocker")
        _assign(w, hidden_blocker, PERSON)
        _link(w, hidden_blocker, m_blocked)
        admin = await _waiting(w, w.a)
        assert admin["questions_total"] == 2
        assert admin["reviews_total"] == 1 and admin["handoffs_total"] == 2
        m = await _waiting(w, w.m, project=False)
        assert _ids(m["questions"]) == [mine]
        assert m["questions_total"] == 1
        assert m["reviews"] == []
        assert m["handoffs"] == [] and m["handoffs_total"] == 0
        assert m["answer_time"]["asked"] == 1
        other_org = await _waiting(w, w.b, project=False)
        assert other_org["questions"] == [] and other_org["questions_total"] == 0
        assert other_org["reviews"] == [] and other_org["handoffs"] == []
        assert other_org["answer_time"]["asked"] == 0
        assert other_org["agent_runs_seen"] is False


# ── F1 ───────────────────────────────────────────────────────────────────────


class TestRunsFlag:
    async def test_f1_the_flag_off_reads_no_question(self, w, monkeypatch):
        from acb_common import get_settings

        tid = _task(w)
        _question(w, tid, asked_ago=300, answered_after=None)
        _question(w, tid, status="completed", asked_ago=600, answered_after=60)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        got = await _waiting(w, w.a)
        assert got["questions"] == [] and got["questions_total"] == 0
        assert got["answer_time"]["asked"] == 0
        assert got["summary"] == "Nobody is waiting on a person."
