"""SW-43c: two sessions of one member, the chat change and the agent read (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.9 (the window rule, the
read stamp and the protocol) and slice SW-43c.

The real gateway app, the real MCP edge and the real chat tool, on a promoted
catalog of its own, as a NON-privileged role. An outside agent asks over the
edge. The member answers on the task card: that is session 1, a person request
to the gateway. The member then opens the chat: that is session 2, the tool
``change_agent_answer`` through the skill's own client. The agent reads over
the edge.

What a fake cannot prove, and this file does:

* a chat change BEFORE the agent read swaps the answer, and the agent reads the
  new answer and never the old one;
* AFTER the agent read, neither session of the member can change the answer:
  the chat shows no card, the card route answers 409, and the answer stays;
* a chat withdraw before the read opens the question again, and the agent sees
  the marker of the withdraw.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_answer_chat_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red. Commit first.

* C1. ``agent_runs.py`` ``_read_run``: never stamp the answer (``own = False``).
  ``test_r8_after_the_agent_read_no_session_of_the_member_changes_the_answer``.
* C2. ``guarded.py`` ``_live_answer``: drop the ``changeable`` test, AND
  ``agent_runs.py`` ``replace_agent_answer``: drop the ``changeable`` 409.
  The tool and the route each hold the rule, so ONE of them removed leaves the
  test green on purpose.
  ``test_r8_after_the_agent_read_no_session_of_the_member_changes_the_answer``.
* C3. ``guarded.py`` ``_replace_card``: send the old option ids.
  ``test_r8_a_chat_change_before_the_read_swaps_the_answer_the_agent_reads``.
"""

from __future__ import annotations

import json
import types
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_common import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import approve

pytestmark = w._DB_GATE

MEMBER = w.MEMBER
_APP_ROLE = "acb_app_sw43chat"
_APP_PW = "sw43chat_scratch_pw"

OPTIONS = [
    {"id": "go", "label": "Restart it now", "confirm": True},
    {"id": "no", "label": "Not now"},
]


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one member, one agent connection, one project."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_sw43chat"
    w._clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'sw43chat', 'sw43chat')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 430_000
    a.member(MEMBER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    doing = a.status(team, "Doing", "in_progress", 1)
    a.status(team, "To do", "todo", 0)
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, doing=doing,
                             c={}, tok={})
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live


@pytest.fixture
def on(live, monkeypatch):
    """The real gateway and the real edge, with agent runs and SW-43 on."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.setattr(settings, "agent_answer_change_enabled", True)
    return live


# ── Helpers ─────────────────────────────────────────────────────────────────


def _key() -> str:
    return uuid.uuid4().hex


async def _agent(world, tool: str, **arguments: Any) -> dict[str, Any]:
    """One tool call of the member's agent over the real edge."""
    result = await tw._mcp(world, "write", tool, arguments)
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _card(world, method: str, path: str, body: dict[str, Any] | None = None
                ) -> httpx.Response:
    """Session 1: the member on the task card, a person request to the gateway."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    headers = {"Authorization": f"Bearer {tw.INTERNAL}", "X-User-Email": MEMBER}
    if body is not None:
        headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, content=raw)
    return await tw._in_scope(world, go)


async def _chat(world, monkeypatch, act) -> Any:
    """Session 2: the member in the chat, the tool through the skill's client."""
    import skill_projects.client as client
    from gateway.main import app as gateway_app

    real = httpx.AsyncClient
    monkeypatch.setattr(client, "httpx", types.SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=httpx.ASGITransport(app=gateway_app), **kw),
        TransportError=httpx.TransportError, Response=httpx.Response))
    monkeypatch.setattr(client, "gateway_url", lambda: "http://gateway.test")
    monkeypatch.setattr(client, "current_user_email", lambda: MEMBER)
    monkeypatch.setattr(client, "internal_token", lambda: tw.INTERNAL)
    return await tw._in_scope(world, act)


def _rows(world, sql: str, **params: Any) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(sql), params))


async def _answered(world, title: str) -> tuple[str, str, str]:
    """A task, the agent's question with a costly option, and the member's
    answer on the card. Returns ``(task, run, answer id)``."""
    task = world.seeder.task(world.team, world.doing, title)
    asked = await _agent(world, "ask_on_task", task_id=task, question="Restart the box?",
                         options=OPTIONS, idempotency_key=_key())
    run = asked["run"]["id"]
    picked = await _card(world, "POST", f"/projects/agent-runs/{run}/activities",
                         {"type": "prompt", "selected": ["go"], "idempotency_key": _key()})
    assert picked.status_code == 201, picked.text
    return task, run, picked.json()["activity"]["id"]


def _answer_row(world, answer: str) -> Any:
    [row] = _rows(world, "SELECT in_reply_to, content FROM pm_agent_run_activities"
                         " WHERE id = CAST(:a AS uuid)", a=answer)
    return row


# ── The acceptance ──────────────────────────────────────────────────────────


async def test_r8_a_chat_change_before_the_read_swaps_the_answer_the_agent_reads(
    on, monkeypatch,
) -> None:
    from skill_projects import guarded

    task, run, old = await _answered(on, "SW43C swap")
    asked = approve(monkeypatch)
    said = await _chat(on, monkeypatch,
                       lambda: guarded.change_agent_answer(task, option="Not now"))
    assert said.startswith("Changed your answer"), said
    assert len(asked) == 1 and "«Restart it now» → «Not now»" in asked[0]["context"]
    # The old answer answers nothing now, and it carries the withdraw.
    row = _answer_row(on, old)
    assert row.in_reply_to is None and row.content["withdrawn"]["by"] == MEMBER
    # The agent reads the run: the new answer, never the old one.
    read = await _agent(on, "get_agent_run", run_id=run)
    ids = [a["id"] for a in read["activities"]]
    assert old not in ids
    answers = [a for a in read["activities"] if a["type"] == "prompt" and a["in_reply_to"]]
    assert [a["selected"] for a in answers] == [["no"]], answers
    # The read closed the window, so the chat cannot change it again.
    later = await _chat(on, monkeypatch,
                        lambda: guarded.change_agent_answer(task, option="Restart it now"))
    assert guarded.ANSWER_READ in later and len(asked) == 1


async def test_r8_after_the_agent_read_no_session_of_the_member_changes_the_answer(
    on, monkeypatch,
) -> None:
    from skill_projects import guarded

    task, run, answer = await _answered(on, "SW43C too late")
    read = await _agent(on, "get_agent_run", run_id=run)
    assert answer in [a["id"] for a in read["activities"]]
    asked = approve(monkeypatch)
    # Session 2, the chat: no card, and one plain sentence.
    for act in (lambda: guarded.change_agent_answer(task, withdraw=True),
                lambda: guarded.change_agent_answer(task, option="Not now")):
        said = await _chat(on, monkeypatch, act)
        assert said.startswith("Refused:") and guarded.ANSWER_READ in said, said
    assert asked == []
    # Session 1, the card: the route answers 409 for both acts.
    for tail, body in (("withdraw", {}), ("replace", {"selected": ["no"]})):
        late = await _card(on, "POST", f"/projects/agent-runs/{run}/answer/{tail}",
                           {"answer_id": answer, "idempotency_key": _key(), **body})
        assert late.status_code == 409, late.text
        assert "already has your answer" in late.json()["detail"]
    row = _answer_row(on, answer)
    assert row.in_reply_to is not None and "withdrawn" not in row.content
    assert row.content["selected"] == ["go"]


async def test_r8_a_chat_withdraw_before_the_read_opens_the_question_again(
    on, monkeypatch,
) -> None:
    from skill_projects import guarded

    task, run, answer = await _answered(on, "SW43C withdraw")
    approve(monkeypatch)
    said = await _chat(on, monkeypatch,
                       lambda: guarded.change_agent_answer(task, withdraw=True))
    assert said.startswith("Took back your answer"), said
    wire = await _card(on, "GET", f"/projects/agent-runs/{run}")
    assert wire.status_code == 200, wire.text
    assert wire.json()["run"]["status"] == "awaiting"
    assert wire.json()["run"]["open_elicitation"] is not None
    read = await _agent(on, "get_agent_run", run_id=run)
    assert answer not in [a["id"] for a in read["activities"]]
    assert read["run"]["status"] == "awaiting"
    assert read["activities"][-1]["type"] == "prompt"
    assert read["activities"][-1]["in_reply_to"] is None
