"""WS-AP AP-8a: a member asks a member (the person ask), on a real Postgres.

Spec: ``project-docs/specs/waiting_on_me.md`` §6, §7.1, §7.2 and §8 AP-8a.

The person ask is a ``pm_decisions`` row of source ``person``. The route
``POST /projects/tasks/{task_id}/asks`` opens it. ``POST
/projects/asks/{id}/withdraw`` closes it. The answer stays
``answer_decision``. This suite proves, on the real ``/projects`` router that
runs as a role that cannot bypass RLS, with two organizations:

* guard 1: no person present, or an agent connection, is refused before any
  read (a stub database fails on a read);
* guard 2 (V4): an addressee who cannot open the task gets no row and no bell,
  and the refusal names the next step;
* guard 3: the caller does not ask themselves;
* guard 4: one open ask for each task and pair, and two concurrent asks end
  as one ask and one refusal;
* guard 5: the 31st ask of one caller in one day is refused;
* each flag off (``DECISIONS_ORGS`` or ``WAITING_ORGS``) answers 404;
* org B cannot ask about a task of org A;
* V5: only the addressee answers, once, and two concurrent answers end as one;
* the withdraw: only the asker, and a withdrawn ask takes no answer, in the
  service and in the trigger;
* the trigger and the CHECKs of the migration, called in SQL.

**Mutations this suite catches.** Commit first, and restore with
``git checkout -- <file>``.

* M1. ``ask_person``: drop ``_refuse_no_person()``.
* M2. ``ask_person``: drop ``_refuse_an_agent``.
* M3. ``ask_person``: drop the ``deliverable`` check of guard 2 AND the one in
  ``ask_decision``.
* M4. ``ask_person``: drop the self check (guard 3) AND the one in
  ``ask_decision``.
* M5. ``ask_person``: drop the open-ask check (guard 4).
* M6. ``ask_person``: drop ``FOR UPDATE``.
* M7. ``ask_person``: drop the limit check (guard 5).
* M8. ``answer_decision``: drop ``closed_at IS NULL`` from the UPDATE.
* M9. ``withdraw_ask``: drop the asker check.
* M10. ``require_waiting``: do nothing.

Run::

    uv run pytest tests/unit/test_person_asks_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import decisions
from pydantic import ValidationError
from sqlalchemy import text

from tests.unit.test_decisions import (
    _WAIT,
    _count,
    _principal,
    _row,
    _seed_member,
    _seed_task,
    _session,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"


def _migration() -> Path:
    """The migration that adds the person ask, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "('align', 'auto', 'person')" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


# ── Hermetic ────────────────────────────────────────────────────────────────


def test_the_migration_is_expand_only_and_scores_no_person() -> None:
    raw = _migration().read_text(encoding="utf-8")
    sql = re.sub(r"--.*", "", raw)
    # test_decisions.py finds 332 by this text. A second file breaks it.
    assert "CREATE TABLE IF NOT EXISTS pm_decisions" not in raw
    assert "SET LOCAL lock_timeout" in sql
    for name in ("pm_decisions_source", "pm_decisions_closed_reason",
                 "pm_decisions_close_whole"):
        assert re.search(rf"ADD CONSTRAINT {name}\s+CHECK[^;]*NOT VALID;", sql), name
        assert f"VALIDATE CONSTRAINT {name};" in sql, name
    for column in ("due_at", "closed_at", "closed_reason"):
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {column} \w+ NULL;", sql), column
    assert "DEFAULT" not in sql.upper().split("CREATE OR REPLACE FUNCTION")[0]
    assert not re.search(r"\bINDEX\b", sql, re.IGNORECASE)
    assert not re.search(r"\bGRANT\b", sql)
    assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b|\bRENAME\b", sql, re.IGNORECASE)


def test_the_ask_body_names_no_asker() -> None:
    assert set(decisions.AskIn.model_fields) == {"addressee", "question", "choices", "due_at"}
    with pytest.raises(ValidationError):
        decisions.AskIn(addressee="a@x.test", question="Q?", asked_by="b@x.test")  # type: ignore[call-arg]


def test_the_options_of_a_person_ask() -> None:
    assert decisions.ask_options(None) == [
        {"key": "reply", "label": "Reply"}, {"key": "not_me", "label": "Not me"}]
    two = [{"key": "x", "label": "Supplier X"}, {"key": "y", "label": "Supplier Y"}]
    assert decisions.ask_options(two) == [*two, {"key": "not_me", "label": "Not me"}]
    for bad in ([], two[:1], [*two, {"key": "z", "label": "Z"}, {"key": "w", "label": "W"}],
                [two[0], {"key": "reply", "label": "R"}],
                [two[0], {"key": "not_me", "label": "N"}]):
        with pytest.raises(HTTPException) as refused:
            decisions.ask_options(bad)
        assert refused.value.status_code == 422, bad


def test_the_life_of_a_person_ask() -> None:
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    assert decisions.ask_life(None, now) == timedelta(days=14)
    assert decisions.ask_life(now + timedelta(days=2), now) == timedelta(days=9)
    assert decisions.ask_life(now + timedelta(days=23), now) == timedelta(days=30)
    for bad in (now - timedelta(minutes=1), now, now + timedelta(days=23, seconds=1),
                datetime(2026, 11, 9, 12)):
        with pytest.raises(HTTPException) as refused:
            decisions.ask_life(bad, now)
        assert refused.value.status_code == 422, bad


class _Untouchable:
    """A database that fails on any call. A read or a write goes red."""

    calls = 0

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        type(self).calls += 1
        raise AssertionError("the guard read or wrote")


async def test_guard_1_refuses_before_any_read() -> None:
    from acb_common.no_person import NO_PERSON_PRESENT, no_person_scope

    body = decisions.AskIn(addressee="d@x.test", question="Q?")
    person = SimpleNamespace(email="a@x.test", agent=None, organization_id=str(uuid.uuid4()))
    agent = SimpleNamespace(email="a@x.test", agent=object(), organization_id="o")
    with no_person_scope(), pytest.raises(HTTPException) as refused:
        await asyncio.wait_for(
            decisions.ask_person(_Untouchable(), person, str(uuid.uuid4()), body), _WAIT)
    assert refused.value.status_code == 403
    assert refused.value.detail["error"] == NO_PERSON_PRESENT
    with pytest.raises(HTTPException) as refused:
        await asyncio.wait_for(
            decisions.ask_person(_Untouchable(), agent, str(uuid.uuid4()), body), _WAIT)
    assert refused.value.status_code == 403
    for user in (agent,):
        with pytest.raises(HTTPException) as refused:
            await asyncio.wait_for(
                decisions.withdraw_ask(_Untouchable(), user, str(uuid.uuid4())), _WAIT)
        assert refused.value.status_code == 403
    with no_person_scope(), pytest.raises(HTTPException) as refused:
        await asyncio.wait_for(
            decisions.withdraw_ask(_Untouchable(), person, str(uuid.uuid4())), _WAIT)
    assert refused.value.status_code == 403
    assert _Untouchable.calls == 0


def test_the_ask_routes_stay_out_of_the_mcp_door() -> None:
    """AP-Q4: an agent does not ask. The route is in no list of the door."""
    for path in ("apps/services/gateway/gateway", "packages/acb_auth/acb_auth"):
        for f in (_REPO / path).rglob("*.py"):
            src = f.read_text(encoding="utf-8")
            if "MCP_ROUTES" in src:
                assert "/asks" not in src, f


# ── R8 ──────────────────────────────────────────────────────────────────────


ASK = "Confirm supplier X for the gasket?"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = "asker@asks-r8.test"
    ns.d = "addressee@asks-r8.test"
    ns.o = "other@asks-r8.test"      # opens the task, is neither party
    ns.x = "outsider@asks-r8.test"   # a member with no grant
    ns.b = "member@asks-r8-b.test"   # a member of org B
    for email in (ns.d, ns.o, ns.x):
        _seed_member(promoted, promoted.org_a, email)
    ns.task = _seed_task(promoted, promoted.org_a, ns.a, grants=(ns.a, ns.d, ns.o))
    ns.task2 = _seed_task(promoted, promoted.org_a, ns.a, grants=(ns.a, ns.d, ns.o))
    ns.hidden = _seed_task(promoted, promoted.org_a, ns.a, grants=(ns.a,))
    ns.task_b = _seed_task(promoted, promoted.org_b, ns.b, grants=(ns.b,))
    ns.u_a = _principal(ns.a, promoted.org_a)
    ns.u_d = _principal(ns.d, promoted.org_a)
    ns.u_o = _principal(ns.o, promoted.org_a)
    ns.u_x = _principal(ns.x, promoted.org_a)
    ns.u_b = _principal(ns.b, promoted.org_b)
    ns.u_agent = _principal(ns.a, promoted.org_a, agent=SimpleNamespace(connection_id="c"))
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """Both flags on for org A only. No decision, bell or comment left."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "decisions_orgs", world.p.org_a)
    monkeypatch.setattr(get_settings(), "waiting_orgs", world.p.org_a)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_decisions"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'comment'"))
    return world


def _rows(p: Any) -> int:
    return _count(p, "SELECT count(*) FROM pm_decisions")


def _bells(p: Any) -> int:
    return _count(p, "SELECT count(*) FROM pm_notifications")


def _unread_bells(p: Any, who: str) -> int:
    return _count(p, "SELECT count(*) FROM pm_notifications WHERE recipient = :w"
                     " AND read_at IS NULL", w=who)


def _asks_path(task: str) -> str:
    return f"/projects/tasks/{task}/asks"


@_DB_GATE
class TestPersonAsksOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )).first()
        assert role is not None and not role[0] and not role[1]

    # ── The ask ─────────────────────────────────────────────────────────

    async def test_an_ask_reaches_its_addressee_with_reply_and_not_me(self, w):
        async with _session(w.p) as (call, _):
            made = await call(w.u_a, "POST", _asks_path(w.task),
                              {"addressee": w.d.upper(), "question": ASK})
        assert made.status_code == 201, made.text
        out = made.json()["decision"]
        assert out["source"] == "person" and out["state"] == "open" and out["heard"] is True
        assert out["asked_by"] == w.a and out["decider"] == w.d
        assert [o["key"] for o in out["options"]] == ["reply", "not_me"]
        assert out["due_at"] is None and out["closed_at"] is None
        row = _row(w.p, out["id"])
        assert str(row.organization_id) == w.p.org_a
        life = (row.expires_at - row.created_at).total_seconds()
        assert abs(life - 14 * 86400) < 5
        with w.p.admin_engine.connect() as c:
            bells = list(c.execute(text("SELECT recipient, kind, actor FROM pm_notifications")))
        assert [(b.recipient, b.kind, b.actor) for b in bells] == [(w.d, "decision", w.a)]

    async def test_choices_and_a_due_date(self, w):
        due = datetime.now(UTC) + timedelta(days=3)
        choices = [{"key": "x", "label": "Supplier X"}, {"key": "y", "label": "Supplier Y"}]
        async with _session(w.p) as (call, _):
            made = await call(w.u_a, "POST", _asks_path(w.task),
                              {"addressee": w.d, "question": ASK, "choices": choices,
                               "due_at": due.isoformat()})
            assert made.status_code == 201, made.text
            for bad in ({"due_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
                        {"due_at": (datetime.now(UTC) + timedelta(days=24)).isoformat()},
                        {"choices": choices[:1]},
                        {"choices": [*choices, {"key": "reply", "label": "R"}]},
                        {"question": "q" * 141},
                        {"question": "   "}):
                refused = await call(w.u_a, "POST", _asks_path(w.task2),
                                     {"addressee": w.d, "question": ASK} | bad)
                assert refused.status_code == 422, (bad, refused.text)
        out = made.json()["decision"]
        assert [o["key"] for o in out["options"]] == ["x", "y", "not_me"]
        row = _row(w.p, out["id"])
        assert abs((row.due_at - due).total_seconds()) < 1
        assert abs((row.expires_at - (due + timedelta(days=7))).total_seconds()) < 5
        assert _rows(w.p) == 1 and _bells(w.p) == 1

    async def test_the_asker_comes_from_the_session_never_the_body(self, w):
        async with _session(w.p) as (call, _):
            sneaky = await call(w.u_o, "POST", _asks_path(w.task),
                                {"addressee": w.d, "question": ASK, "asked_by": w.a})
        assert sneaky.status_code == 422
        assert _rows(w.p) == 0

    async def test_guard_1_an_agent_and_no_person_present_write_nothing(self, w):
        from acb_common.no_person import NO_PERSON_PRESENT, no_person_scope

        async with _session(w.p) as (call, _):
            agent = await call(w.u_agent, "POST", _asks_path(w.task),
                               {"addressee": w.d, "question": ASK})
            assert agent.status_code == 403
            with no_person_scope():
                marked = await call(w.u_a, "POST", _asks_path(w.task),
                                    {"addressee": w.d, "question": ASK})
            assert marked.status_code == 403
            assert marked.json()["detail"]["error"] == NO_PERSON_PRESENT
        assert _rows(w.p) == 0 and _bells(w.p) == 0

    async def test_guard_2_v4_an_addressee_who_cannot_open_the_task(self, w):
        async with _session(w.p) as (call, _):
            for who in (w.x, "nobody@asks-r8.test", w.b, "agent:bot@x.test", "not-an-email"):
                refused = await call(w.u_a, "POST", _asks_path(w.task),
                                     {"addressee": who, "question": ASK})
                assert refused.status_code == 422, who
                detail = refused.json()["detail"]
                assert detail["error"] == "addressee_cannot_open", who
                assert "Share the project" in detail["message"]
            # D opens `task` and not `hidden`.
            refused = await call(w.u_a, "POST", _asks_path(w.hidden),
                                 {"addressee": w.d, "question": ASK})
            assert refused.json()["detail"]["error"] == "addressee_cannot_open"
        assert _rows(w.p) == 0 and _bells(w.p) == 0

    async def test_the_caller_must_open_the_task(self, w):
        async with _session(w.p) as (call, _):
            for task in (w.hidden, str(uuid.uuid4()), "not-a-uuid"):
                refused = await call(w.u_d, "POST", _asks_path(task),
                                     {"addressee": w.a, "question": ASK})
                assert refused.status_code == 404, task
        assert _rows(w.p) == 0 and _bells(w.p) == 0

    async def test_guard_3_the_caller_does_not_ask_themselves(self, w):
        async with _session(w.p) as (call, _):
            refused = await call(w.u_a, "POST", _asks_path(w.task),
                                 {"addressee": f" {w.a.upper()} ", "question": ASK})
        assert refused.status_code == 422
        assert refused.json()["detail"]["error"] == "self_ask"
        assert _rows(w.p) == 0 and _bells(w.p) == 0

    async def test_guard_4_one_open_ask_for_a_task_and_a_pair(self, w):
        async with _session(w.p) as (call, _):
            first = await call(w.u_a, "POST", _asks_path(w.task),
                               {"addressee": w.d, "question": ASK})
            assert first.status_code == 201
            again = await call(w.u_a, "POST", _asks_path(w.task),
                               {"addressee": w.d, "question": "Another?"})
            assert again.status_code == 409
            assert again.json()["detail"]["error"] == "ask_open"
            # Another addressee, another task, or the other direction: allowed.
            for user, task, who in ((w.u_a, w.task, w.o), (w.u_a, w.task2, w.d),
                                    (w.u_d, w.task, w.a)):
                ok = await call(user, "POST", _asks_path(task),
                                {"addressee": who, "question": ASK})
                assert ok.status_code == 201, (user.email, who, ok.text)
            # After a withdraw, the pair may ask again.
            gone = await call(w.u_a, "POST",
                              f"/projects/asks/{first.json()['decision']['id']}/withdraw")
            assert gone.status_code == 200
            assert (await call(w.u_a, "POST", _asks_path(w.task),
                               {"addressee": w.d, "question": ASK})).status_code == 201
        assert _rows(w.p) == 5

    async def test_guard_4_two_concurrent_asks_end_as_one_ask(self, w):
        async with _session(w.p) as (call, _):
            results = await asyncio.wait_for(asyncio.gather(*(
                call(w.u_a, "POST", _asks_path(w.task),
                     {"addressee": w.d, "question": f"{ASK} {i}"})
                for i in range(3))), _WAIT)
        codes = sorted(r.status_code for r in results)
        assert codes == [201, 409, 409], [r.text for r in results]
        assert _rows(w.p) == 1 and _bells(w.p) == 1

    async def test_guard_5_the_31st_ask_in_a_day_is_refused(self, w):
        opts = '[{"key":"reply","label":"Reply"},{"key":"not_me","label":"Not me"}]'

        def seed(n: int, *, age: str = "1 hour") -> None:
            with w.p.admin_engine.begin() as c:
                for _ in range(n):
                    c.execute(text(
                        "INSERT INTO pm_decisions (organization_id, subject_id, decider,"
                        " options, question, asked_by, source, created_at, expires_at) "
                        "VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :d, CAST(:opts AS jsonb),"
                        " 'Old?', :a, 'person', now() - CAST(:age AS interval),"
                        " now() + interval '7 days')"),
                        {"o": w.p.org_a, "t": str(uuid.uuid4()), "d": w.o, "a": w.a,
                         "opts": opts, "age": age})

        # Asks older than a day do not weigh.
        seed(5, age="25 hours")
        seed(29)
        async with _session(w.p) as (call, _):
            ok = await call(w.u_a, "POST", _asks_path(w.task),
                            {"addressee": w.d, "question": ASK})
            assert ok.status_code == 201, ok.text
            refused = await call(w.u_a, "POST", _asks_path(w.task2),
                                 {"addressee": w.d, "question": ASK})
            assert refused.status_code == 429
            assert refused.json()["detail"]["error"] == "ask_limit"
            assert set(refused.json()["detail"]) == {"error", "message"}
            assert not re.search(r"\d", refused.json()["detail"]["message"])
            # A withdraw does not give an ask back.
            gone = await call(w.u_a, "POST",
                              f"/projects/asks/{ok.json()['decision']['id']}/withdraw")
            assert gone.status_code == 200
            assert (await call(w.u_a, "POST", _asks_path(w.task2),
                               {"addressee": w.d, "question": ASK})).status_code == 429
            # Another caller is not held by the limit of this one.
            assert (await call(w.u_d, "POST", _asks_path(w.task2),
                               {"addressee": w.a, "question": ASK})).status_code == 201
        assert _rows(w.p) == 5 + 29 + 2 and _bells(w.p) == 2

    # ── The flags and the tenant ────────────────────────────────────────

    async def test_each_flag_off_answers_404(self, w, monkeypatch):
        from acb_common import get_settings

        settings = get_settings()
        async with _session(w.p) as (call, _):
            made = await call(w.u_a, "POST", _asks_path(w.task),
                              {"addressee": w.d, "question": ASK})
            ask_id = made.json()["decision"]["id"]
            for flag in ("decisions_orgs", "waiting_orgs"):
                for value in ("", w.p.org_b):
                    monkeypatch.setattr(settings, flag, value)
                    asked = await call(w.u_a, "POST", _asks_path(w.task2),
                                       {"addressee": w.d, "question": ASK})
                    gone = await call(w.u_a, "POST", f"/projects/asks/{ask_id}/withdraw")
                    assert asked.status_code == gone.status_code == 404, (flag, value)
                    monkeypatch.setattr(settings, flag, w.p.org_a)
        assert _rows(w.p) == 1 and _bells(w.p) == 1
        assert _row(w.p, ask_id).closed_at is None

    async def test_org_b_cannot_ask_about_a_task_of_org_a(self, w, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "decisions_orgs", "*")
        monkeypatch.setattr(get_settings(), "waiting_orgs", "*")
        async with _session(w.p) as (call, _):
            for who in (w.d, w.b):
                refused = await call(w.u_b, "POST", _asks_path(w.task),
                                     {"addressee": who, "question": ASK})
                assert refused.status_code == 404, who
            # Org A cannot name a member of org B either.
            refused = await call(w.u_a, "POST", _asks_path(w.task_b),
                                 {"addressee": w.b, "question": ASK})
            assert refused.status_code == 404
        assert _rows(w.p) == 0 and _bells(w.p) == 0

    # ── The answer (V5) ─────────────────────────────────────────────────

    async def _open(self, w: Any, call: Any, **body: Any) -> str:
        made = await call(w.u_a, "POST", _asks_path(w.task),
                          {"addressee": w.d, "question": ASK} | body)
        assert made.status_code == 201, made.text
        return made.json()["decision"]["id"]

    async def test_v5_only_the_addressee_answers_and_a_reply_needs_words(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            path = f"/projects/decisions/{ask_id}/answer"
            for user in (w.u_a, w.u_o, w.u_x, w.u_b):
                refused = await call(user, "POST", path,
                                     {"answer_key": "reply", "reason": "Yes"})
                assert refused.status_code == 404, user.email
            for blank in (None, "", "   "):
                empty = await call(w.u_d, "POST", path,
                                   {"answer_key": "reply", "reason": blank})
                assert empty.status_code == 422, blank
            assert _row(w.p, ask_id).answered_at is None
            done = await call(w.u_d, "POST", path,
                              {"answer_key": "reply", "reason": "Yes, supplier X."})
            assert done.status_code == 200, done.text
            again = await call(w.u_d, "POST", path, {"answer_key": "not_me"})
            assert again.status_code == 409
            assert again.json()["detail"] == "decision_answered"
        row = _row(w.p, ask_id)
        assert (row.answered_by, row.answer_key, row.answer_reason) == (
            w.d, "reply", "Yes, supplier X.")
        assert _unread_bells(w.p, w.d) == 0

    async def test_not_me_takes_no_words(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            done = await call(w.u_d, "POST", f"/projects/decisions/{ask_id}/answer",
                              {"answer_key": "not_me"})
        assert done.status_code == 200, done.text

    async def test_two_concurrent_answers_end_as_one(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            path = f"/projects/decisions/{ask_id}/answer"
            results = await asyncio.wait_for(asyncio.gather(
                call(w.u_d, "POST", path, {"answer_key": "reply", "reason": "one"}),
                call(w.u_d, "POST", path, {"answer_key": "not_me"}),
            ), _WAIT)
        assert sorted(r.status_code for r in results) == [200, 409]
        winner = next(r for r in results if r.status_code == 200).json()["decision"]
        assert _row(w.p, ask_id).answer_key == winner["answer_key"]

    # ── The withdraw ────────────────────────────────────────────────────

    async def test_only_the_asker_withdraws_and_then_no_answer_comes(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            path = f"/projects/asks/{ask_id}/withdraw"
            for user in (w.u_d, w.u_o, w.u_x, w.u_b):
                refused = await call(user, "POST", path)
                assert refused.status_code == 404, user.email
            assert (await call(w.u_a, "POST", "/projects/asks/not-a-uuid/withdraw")
                    ).status_code == 404
            assert _row(w.p, ask_id).closed_at is None
            assert _unread_bells(w.p, w.d) == 1
            gone = await call(w.u_a, "POST", path)
            assert gone.status_code == 200, gone.text
            out = gone.json()["decision"]
            assert out["state"] == "closed" and out["closed_reason"] == "withdrawn"
            # The bell row of the addressee is read.
            assert _unread_bells(w.p, w.d) == 0
            # A second withdraw and a late answer are refused.
            twice = await call(w.u_a, "POST", path)
            assert twice.status_code == 409 and twice.json()["detail"] == "ask_closed"
            late = await call(w.u_d, "POST", f"/projects/decisions/{ask_id}/answer",
                              {"answer_key": "not_me"})
            assert late.status_code == 409 and late.json()["detail"] == "ask_closed"
            # The open lists drop it. The task list shows it as closed.
            assert (await call(w.u_d, "GET", "/projects/decisions")).json()["decisions"] == []
            seen = (await call(w.u_o, "GET", f"/projects/decisions?task_id={w.task}")).json()
            assert seen["waiting_on"] == []
            own = (await call(w.u_d, "GET", f"/projects/decisions?task_id={w.task}")).json()
            assert [d["state"] for d in own["decisions"]] == ["closed"]
        row = _row(w.p, ask_id)
        assert row.answered_at is None and row.closed_reason == "withdrawn"

    async def test_a_decision_that_is_not_a_person_ask_is_not_withdrawn(self, w):
        from tests.unit.test_decisions import _ask

        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d, asked_by=w.a)
            refused = await call(w.u_a, "POST", f"/projects/asks/{made['id']}/withdraw")
            assert refused.status_code == 404
        assert _row(w.p, made["id"]).closed_at is None

    async def test_an_answered_ask_is_not_withdrawn(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            done = await call(w.u_d, "POST", f"/projects/decisions/{ask_id}/answer",
                              {"answer_key": "not_me"})
            assert done.status_code == 200
            refused = await call(w.u_a, "POST", f"/projects/asks/{ask_id}/withdraw")
            assert refused.status_code == 409
            assert refused.json()["detail"] == "decision_answered"
        assert _row(w.p, ask_id).closed_at is None

    async def test_a_withdraw_and_an_answer_at_once_end_as_one(self, w):
        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            results = await asyncio.wait_for(asyncio.gather(
                call(w.u_a, "POST", f"/projects/asks/{ask_id}/withdraw"),
                call(w.u_d, "POST", f"/projects/decisions/{ask_id}/answer",
                     {"answer_key": "not_me"}),
            ), _WAIT)
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        row = _row(w.p, ask_id)
        assert (row.answered_at is None) != (row.closed_at is None)

    # ── The database ────────────────────────────────────────────────────

    async def test_the_trigger_and_the_checks_hold_in_sql(self, w):
        from sqlalchemy.exc import DBAPIError, IntegrityError

        wall = (IntegrityError, DBAPIError)
        opts = '[{"key":"reply","label":"Reply"},{"key":"not_me","label":"Not me"}]'

        def insert(source: str = "person", extra: str = "", values: str = "") -> str:
            with w.p.admin_engine.begin() as c:
                return str(c.execute(text(
                    "INSERT INTO pm_decisions (organization_id, subject_id, decider, options,"
                    f" question, asked_by, source, due_at{extra}) "
                    "VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :d, CAST(:opts AS jsonb),"
                    f" 'Q?', :a, :s, now() + interval '1 day'{values}) RETURNING id"),
                    {"o": w.p.org_a, "t": w.task, "d": w.d, "a": w.a, "s": source,
                     "opts": opts}).scalar())

        def update(did: str, sets: str) -> None:
            with w.p.admin_engine.begin() as c:
                c.execute(text(f"UPDATE pm_decisions SET {sets} WHERE id = CAST(:i AS uuid)"),
                          {"i": did})

        # The source CHECK takes person and refuses another value.
        insert("person")
        insert("align")
        with pytest.raises(wall):
            insert("human")
        # The close is one of two reasons, and whole.
        for extra, values in ((", closed_at, closed_reason", ", now(), 'bored'"),
                              (", closed_at", ", now()"),
                              (", closed_reason", ", 'withdrawn'")):
            with pytest.raises(wall):
                insert(extra=extra, values=values)
        # due_at does not change.
        did = insert()
        for sets in ("due_at = due_at + interval '1 day'", "due_at = NULL"):
            with pytest.raises(wall):
                update(did, sets)
        # One UPDATE does not both answer and close.
        with pytest.raises(wall):
            update(did, "closed_at = now(), closed_reason = 'withdrawn',"
                        " answered_by = decider, answer_key = 'not_me', answered_at = now()")
        # A close happens once, and a closed ask takes no answer.
        update(did, "closed_at = now(), closed_reason = 'withdrawn'")
        for sets, says in (
            ("closed_reason = 'task_closed'", "an ask closes once"),
            ("closed_at = now() + interval '1 second'", "an ask closes once"),
            ("closed_at = NULL, closed_reason = NULL", "an ask closes once"),
            ("answered_by = decider, answer_key = 'not_me', answered_at = now()",
             "a closed ask takes no answer"),
        ):
            with pytest.raises(wall) as refused:
                update(did, sets)
            assert says in str(refused.value), sets
        # An answered ask takes no close.
        other = insert()
        update(other, "answered_by = decider, answer_key = 'not_me', answered_at = now()")
        with pytest.raises(wall):
            update(other, "closed_at = now(), closed_reason = 'withdrawn'")
        # The refusals of 332 stay.
        third = insert()
        for sets in ("question = 'Else?'", "asked_by = 'x@y.test'",
                     "expires_at = expires_at + interval '1 day'"):
            with pytest.raises(wall):
                update(third, sets)
        assert _row(w.p, did).closed_reason == "withdrawn"
        assert _row(w.p, did).answered_at is None

    async def test_the_app_role_closes_through_the_trigger_too(self, w, app_engine):  # noqa: F811
        """The trigger binds the app role, not only the owner."""
        from sqlalchemy.exc import DBAPIError

        async with _session(w.p) as (call, _):
            ask_id = await self._open(w, call)
            assert (await call(w.u_a, "POST", f"/projects/asks/{ask_id}/withdraw")
                    ).status_code == 200
        with pytest.raises(DBAPIError), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.p.org_a})
            c.execute(text(
                "UPDATE pm_decisions SET answered_by = decider, answer_key = 'not_me',"
                " answered_at = now() WHERE id = CAST(:i AS uuid)"), {"i": ask_id})
        assert _row(w.p, ask_id).answered_at is None
