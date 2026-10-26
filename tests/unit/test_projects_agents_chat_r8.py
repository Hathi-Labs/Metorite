"""WS-27bn RA-8 — ``GET /projects/analytics/agents``, on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §8 RA-8, §6.8 E rule 7 and E5.

``test_projects_agents_chat.py`` pins the shape of the answer. This file pins
the gateway read behind it, through the real ``/projects`` router as the
non-privileged role of the phase-4 rehearsal (the ``promoted`` fixture):

* **S1** parity. Each section, and each ``agents_now`` figure, equals what the
  Overview path (``POST /projects/reports/preview`` with ``kpis``) returns for
  the same fixture. The read computes no figure of its own.
* **V1** visibility. A reader who cannot open a task does not see its title in
  ``waiting`` or ``quality``, in the payload or in the printed answer.
* **O1** with agent runs off, no section reads "all clear".
* **A1** the printed answer from this fixture, in the shape of E5.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra8 \\
        uv run pytest tests/unit/test_projects_agents_chat_r8.py -v -rs

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

A_EMAIL = "ra8-a@agents-chat-r8.test"
M_EMAIL = "ra8-m@agents-chat-r8.test"
B_EMAIL = "ra8-b@agents-chat-r8.test"

SEEN = "Visible question to Vijay"
HIDDEN = "SECRET question the reader cannot open"
SEEN_PR = "Visible failing pull request task"
HIDDEN_PR = "SECRET failing pull request task"

SECTIONS = ["waiting", "agent_runs", "quality"]


def _admin(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects", "data:org:read", "admin:members:read"]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = _admin(A_EMAIL, promoted.org_a)
    ns.m = b._principal(M_EMAIL, promoted.org_a, unrestricted=False)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    b.member(promoted, promoted.org_a, M_EMAIL)
    b.member(promoted, promoted.org_b, B_EMAIL)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


# ── Seeds ────────────────────────────────────────────────────────────────────


def _task(w: Any, title: str, *, assignee: str | None = None) -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.root, assignee=assignee, title=title)


def _question(w: Any, tid: str, *, asked_ago: int = 180) -> None:
    """A run in `awaiting` with one open question."""
    rid = b.run(w.p, w.p.org_a, tid, A_EMAIL, "awaiting")
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_run_activities (id, organization_id, run_id, task_id, "
            "type, content, actor, actor_via, created_at) VALUES "
            "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:r AS uuid), CAST(:t AS uuid), "
            "'elicitation', CAST(:c AS jsonb), :e, :via, now() - make_interval(mins => :ago))"),
            {"id": str(uuid.uuid4()), "o": w.p.org_a, "r": rid, "t": tid,
             "c": json.dumps({"body": "elicitation"}), "e": A_EMAIL, "via": b.VIA,
             "ago": asked_ago})


def _failing_pr(w: Any, tid: str, number: int) -> None:
    """An agent worked the task long ago, and its open pull request fails."""
    old = "now() - make_interval(mins => 57600)"
    b.run(w.p, w.p.org_a, tid, A_EMAIL, "completed", created=old, last_activity=old,
          last_agent=old)
    b.ref(w.p, w.p.org_a, tid, number, "open", checks="failing")


def _fixture(w: Any) -> None:
    """Two visible tasks (held by M) and two hidden ones: each kind has a
    question and a failing pull request."""
    seen = _task(w, SEEN, assignee=M_EMAIL)
    _question(w, seen)
    hidden = _task(w, HIDDEN)
    _question(w, hidden, asked_ago=600)
    seen_pr = _task(w, SEEN_PR, assignee=M_EMAIL)
    _failing_pr(w, seen_pr, 1)
    hidden_pr = _task(w, HIDDEN_PR)
    _failing_pr(w, hidden_pr, 2)


async def _agents(w: Any, user: Any, *, project: bool = True) -> dict[str, Any]:
    path = "/projects/analytics/agents"
    if project:
        path += f"?project_id={w.root['id']}"
    async with b._session(w.p) as call:
        got = await call(user, "GET", path)
    assert got.status_code == 200, got.text
    return got.json()


async def _overview(w: Any, user: Any, *, project: bool = True) -> dict[str, Any]:
    """The Overview call: a preview of the three sections with the strip."""
    body: dict[str, Any] = {"config": {"sections": SECTIONS}, "kpis": True}
    if project:
        body["project_id"] = w.root["id"]
    async with b._session(w.p) as call:
        got = await call(user, "POST", "/projects/reports/preview", body)
    assert got.status_code == 200, got.text
    return got.json()


# ── S1: parity with the Overview ─────────────────────────────────────────────


class TestParity:
    async def test_s1_each_section_equals_the_overview_section(self, w):
        _fixture(w)
        # M holds tasks and no project, so M reads the portfolio scope.
        for user, project in ((w.a, True), (w.m, False)):
            got = await _agents(w, user, project=project)
            overview = await _overview(w, user, project=project)
            for name in SECTIONS:
                assert got["sections"][name]["summary"] == overview["sections"][name]["summary"]
                assert got["sections"][name] == overview["sections"][name]
            assert got["agents_now"]["runs"] == overview["kpis"]["runs"]
            assert got["agents_now"]["merge"] == overview["kpis"]["merge"]
        assert got["scope"] == "portfolio"

    async def test_s1_the_portfolio_scope_reads_without_a_project(self, w):
        _fixture(w)
        got = await _agents(w, w.a, project=False)
        overview = await _overview(w, w.a, project=False)
        assert got["scope"] == "portfolio"
        assert got["sections"]["waiting"]["summary"] == overview["sections"]["waiting"]["summary"]

    async def test_s1_a_project_the_reader_cannot_open_is_refused(self, w):
        async with b._session(w.p) as call:
            got = await call(w.b, "GET", f"/projects/analytics/agents?project_id={w.root['id']}")
        assert got.status_code in (403, 404)


# ── V1: the reader's own visibility ──────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_reader_never_sees_a_title_they_cannot_open(self, w):
        from skill_projects import reads

        _fixture(w)
        got = await _agents(w, w.m, project=False)
        payload_text = json.dumps(got)
        printed = "\n".join(reads.agents_answer(got))
        for secret in (HIDDEN, HIDDEN_PR):
            assert secret not in payload_text and secret not in printed
        assert SEEN in printed and SEEN_PR in printed
        assert got["sections"]["waiting"]["questions_total"] == 1
        assert got["sections"]["quality"]["checks"]["failing_total"] == 1

        everyone = await _agents(w, w.a)
        both = "\n".join(reads.agents_answer(everyone))
        assert everyone["sections"]["waiting"]["questions_total"] == 2
        assert HIDDEN in both and SEEN in both

    async def test_v1_the_other_organization_reads_nothing(self, w):
        _fixture(w)
        got = await _agents(w, w.b, project=False)
        assert got["sections"]["waiting"]["questions_total"] == 0
        assert got["sections"]["quality"]["checks"]["failing_total"] == 0
        assert HIDDEN not in json.dumps(got)


# ── O1 and A1 ────────────────────────────────────────────────────────────────


class TestAnswer:
    async def test_o1_with_runs_off_no_section_says_all_clear(self, w, monkeypatch):
        from acb_common import get_settings
        from skill_projects import reads

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        got = await _agents(w, w.a)
        printed = "\n".join(reads.agents_answer(got))
        lowered = printed.lower()
        assert "all clear" not in lowered and "nobody is waiting" not in lowered
        assert "no agent run is working now" not in lowered
        assert got["agents_now"]["summary"] in printed
        assert "Agent runs are off for this organization" in printed

    async def test_a1_the_printed_answer(self, w, capsys):
        from skill_projects import reads

        _fixture(w)
        got = await _agents(w, w.a)
        lines = reads.agents_answer(got)
        for heading, name in (("Waiting on people:", "waiting"), ("Agent runs:", "agent_runs"),
                              ("Quality:", "quality")):
            assert lines[lines.index(heading) + 1] == f"  {got['sections'][name]['summary']}"
        assert lines[lines.index("Agents now:") + 1] == f"  {got['agents_now']['summary']}"
        with capsys.disabled():
            print("\n--- A1 printed answer ---\n" + "\n".join(lines) + "\n--- end ---")
