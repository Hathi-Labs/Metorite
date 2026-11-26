"""WS-MCP SW-104 — the machine, the kind of machine and the model of a run (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.12.3, §3.12.4 rule 2,
§3.12.7 rules 5 and 6, the slice SW-104 and Q19.

``claim_task`` and ``start_agent_run`` take three optional inputs: ``host``,
``model`` and ``host_kind`` (``computer``, ``server`` or ``cloud``). The
gateway cleans the two names as it cleans ``agent_label``, and caps each at 60
characters. A control character is 422 at the schema of the body. Three
nullable columns of ``pm_agent_runs`` hold them, and the runs wire gives them
back. A box token's connection name wins over ``host`` and sets ``server``.

This file runs on a REAL Postgres, through the real ``/projects`` router,
under the non-privileged role of the phase-4 rehearsal (R8). The structural
fence that the values grant nothing is ``test_agent_label_fence.py``.

Run it on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_run_place_model_r8.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py``.

* P1. ``G`` ``_INSERT_RUN_SQL``: bind NULL for ``:host``.
  ``test_a_claim_with_all_three_values_stores_them``.
* P2. ``G`` ``_set_run_place``: return at once.
  ``test_a_later_claim_replaces_them_and_a_claim_with_none_keeps_them``.
* P3. ``G`` ``StartIn.host``: drop the pattern.
  ``test_a_control_character_is_422_at_the_schema``.
* P4. ``G`` ``agent_place_of``: skip the person check.
  ``test_a_person_body_with_a_place_is_refused``.
* P5. ``G`` ``_box_place``: keep the agent's host.
  ``test_a_box_token_names_its_machine_and_sets_server``.
* P6. ``G`` ``_runs_out``: drop ``agent_model``.
  ``test_a_claim_with_all_three_values_stores_them``.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import agent_oauth
from acb_common import get_settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_agent_run_exclusive import (
    ORIGIN,
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

ALICE = "sw104-alice@place.test"
BOX_NAME = "metorite-home"

_PLACE_SQL = ("SELECT agent_host, agent_model, agent_host_kind FROM pm_agent_runs"
              " WHERE id = CAST(:i AS uuid)")


def _box_connection(p: Any, org: str, email: str, name: str) -> str:
    """A live box token of *email*: ``credential`` ``box``, as
    ``agent_tokens`` mints it (EA-19b)."""
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at, credential) VALUES (CAST(:o AS uuid), :m, :client, :name,"
            " :origin, ARRAY['projects:read', 'projects:write'], CAST('{}' AS uuid[]),"
            " false, now() + interval '1 day', 'box') RETURNING id"),
            {"o": org, "m": email, "client": agent_oauth.BOX_CLIENT_ID, "name": name,
             "origin": ORIGIN},
        ).scalar())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org = promoted.org_a
    ns = SimpleNamespace(p=promoted, org=org)
    with promoted.admin_engine.begin() as c:
        _member_row(c, org, ALICE, "Alice Place")
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.conn_box = _box_connection(promoted, org, ALICE, BOX_NAME)
    box_via = agent_oauth.actor_via_for(
        None, client_id=agent_oauth.BOX_CLIENT_ID, credential=agent_oauth.BOX_CREDENTIAL,
        connection_id=ns.conn_box, client_name=BOX_NAME)
    assert box_via.startswith(agent_oauth.BOX_STAMP_PREFIX), box_via
    ns.box = _agent(ALICE, org, ns.conn_box, box_via)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(90_000, 110_000))


def _fresh(w: Any) -> str:
    return _seed_task(w.p, w.org, ALICE, n=next(_numbers))


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


def _place(w: Any, run_id: str) -> tuple[Any, Any, Any]:
    [row] = _rows(w.p, _PLACE_SQL, i=run_id)
    return row.agent_host, row.agent_model, row.agent_host_kind


def _wire(run: dict[str, Any]) -> tuple[Any, Any, Any]:
    return run["agent_host"], run["agent_model"], run["agent_host_kind"]


FULL = {"host": "VJ laptop", "model": "Sonnet 5.5", "host_kind": "computer"}


@_DB_GATE
class TestThePlaceOnARealDatabase:

    async def test_the_columns_exist_and_their_checks_hold(self, w):
        cols = {r.column_name: (r.data_type, r.is_nullable, r.column_default) for r in _rows(
            w.p, "SELECT column_name, data_type, is_nullable, column_default"
                 " FROM information_schema.columns WHERE table_name = 'pm_agent_runs'"
                 " AND column_name IN ('agent_host', 'agent_model', 'agent_host_kind')")}
        assert cols == {name: ("text", "YES", None)
                        for name in ("agent_host", "agent_model", "agent_host_kind")}
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(w.alice, "POST", f"/projects/tasks/{task}/claim", None)
        run_id = claimed.json()["agent_run_id"]
        for column, bad in (("agent_host", "x" * 61), ("agent_host", ""),
                            ("agent_model", "x" * 61), ("agent_model", ""),
                            ("agent_host_kind", "laptop")):
            with pytest.raises(DBAPIError), w.p.admin_engine.begin() as c:
                c.execute(text(f"UPDATE pm_agent_runs SET {column} = :v"
                               " WHERE id = CAST(:i AS uuid)"), {"v": bad, "i": run_id})
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_agent_runs SET agent_host = :v, agent_host_kind = 'cloud'"
                           " WHERE id = CAST(:i AS uuid)"), {"v": "x" * 60, "i": run_id})

    async def test_a_claim_with_all_three_values_stores_them(self, w):
        """The first "Done when" of SW-104. The claim stores the three values,
        and ``get_agent_run`` and the runs list give them back."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(session, "POST", f"/projects/tasks/{task}/claim", FULL)
            assert claimed.status_code == 200, claimed.text
            run_id = claimed.json()["agent_run_id"]
            one = await call(w.alice_person, "GET", f"/projects/agent-runs/{run_id}", None)
            listed = await call(w.alice_person, "GET", "/projects/agent-runs", None)
        assert _place(w, run_id) == ("VJ laptop", "Sonnet 5.5", "computer")
        assert one.status_code == 200, one.text
        assert _wire(one.json()["run"]) == ("VJ laptop", "Sonnet 5.5", "computer")
        [row] = [r for r in listed.json()["rows"] if r["id"] == run_id]
        assert _wire(row) == ("VJ laptop", "Sonnet 5.5", "computer")

    async def test_a_later_claim_replaces_them_and_a_claim_with_none_keeps_them(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            first = await call(session, "POST", f"/projects/tasks/{task}/claim", FULL)
            run_id = first.json()["agent_run_id"]
            later = await call(session, "POST", f"/projects/tasks/{task}/claim", {
                "host": "VJ desktop", "model": "Opus 5.5", "host_kind": "cloud"})
            assert later.json()["agent_run_id"] == run_id
            assert _place(w, run_id) == ("VJ desktop", "Opus 5.5", "cloud")
            again = await call(session, "POST", f"/projects/tasks/{task}/claim", None)
            assert again.json()["agent_run_id"] == run_id
            assert _place(w, run_id) == ("VJ desktop", "Opus 5.5", "cloud")
            # One value replaces one value, and the others stay.
            started = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key(), "model": "Haiku 5"})
            assert started.status_code == 200, started.text
            assert started.json()["run"]["id"] == run_id
            assert _wire(started.json()["run"]) == ("VJ desktop", "Haiku 5", "cloud")
        assert _place(w, run_id) == ("VJ desktop", "Haiku 5", "cloud")

    async def test_a_new_run_of_a_start_stores_the_cleaned_values(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            started = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/agent-runs", {
                                     "idempotency_key": _key(),
                                     "host": "  VJ   laptop\u202e " + "x" * 70,
                                     "model": "Sonnet\u200b 5.5", "host_kind": "server"})
        assert started.status_code == 201, started.text
        run = started.json()["run"]
        host, model, kind = _place(w, run["id"])
        assert host == ("VJ laptop " + "x" * 70)[:60] and len(host) == 60
        assert (model, kind) == ("Sonnet 5.5", "server")
        assert _wire(run) == (host, model, kind)

    async def test_a_control_character_is_422_at_the_schema(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            answers = [
                await call(session, "POST", f"/projects/tasks/{task}/claim", body)
                for body in ({"host": "VJ\nlaptop"}, {"model": "Sonnet\x005.5"},
                             {"host": "a\tb"}, {"model": "a\x85b"}, {"host": ""})
            ]
            answers.append(await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                      {"idempotency_key": _key(), "host": "VJ\x1blaptop"}))
        assert [a.status_code for a in answers] == [422] * 6, [a.text for a in answers]
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_host_kind_takes_only_the_three_values(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            bad = [await call(session, "POST", f"/projects/tasks/{task}/claim",
                              {"host_kind": kind}) for kind in ("laptop", "Server", "", 1)]
            assert [b.status_code for b in bad] == [422] * 4, [b.text for b in bad]
            for kind in ("computer", "server", "cloud"):
                ok = await call(session, "POST", f"/projects/tasks/{task}/claim",
                                {"host_kind": kind})
                assert ok.status_code == 200, ok.text
                assert _place(w, ok.json()["agent_run_id"])[2] == kind

    async def test_a_run_with_no_value_reads_null(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/claim", {"agent_label": "lane-1"})
            run_id = claimed.json()["agent_run_id"]
            one = await call(w.alice_person, "GET", f"/projects/agent-runs/{run_id}", None)
        assert _place(w, run_id) == (None, None, None)
        assert _wire(one.json()["run"]) == (None, None, None)

    async def test_a_person_body_with_a_place_is_refused(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            answers = [
                await call(w.alice_person, "POST", f"/projects/tasks/{task}/claim", body)
                for body in ({"host": "VJ laptop"}, {"model": "Sonnet 5.5"},
                             {"host_kind": "computer"})
            ]
            answers.append(await call(w.alice_person, "POST",
                                      f"/projects/tasks/{task}/agent-runs", {
                                          "connection_id": w.conn_alice,
                                          "body": "Please take it", "model": "Sonnet 5.5"}))
        assert [a.status_code for a in answers] == [422] * 4, [a.text for a in answers]
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_box_token_names_its_machine_and_sets_server(self, w):
        """SW-50: the connection name of a box token wins over ``host``, and
        the kind is ``server``. The model stays the agent's own."""
        one, two = _fresh(w), _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(_in_session(w.box, _key()), "POST",
                                 f"/projects/tasks/{one}/claim", {
                                     "host": "my laptop", "model": "Sonnet 5.5",
                                     "host_kind": "computer"})
            assert claimed.status_code == 200, claimed.text
            started = await call(_in_session(w.box, _key()), "POST",
                                 f"/projects/tasks/{two}/agent-runs",
                                 {"idempotency_key": _key()})
            assert started.status_code == 201, started.text
        assert _place(w, claimed.json()["agent_run_id"]) == (BOX_NAME, "Sonnet 5.5", "server")
        assert _wire(started.json()["run"]) == (BOX_NAME, None, "server")

    async def test_a_reopen_clears_the_place_with_the_label(self, w):
        """A finished run keeps the place of the session that ran it. A
        member's prompt hands the run to the next session, and the place goes
        with the label (the rule of SW-7b)."""
        task = _fresh(w)
        first = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(first, "POST", f"/projects/tasks/{task}/claim",
                                 {**FULL, "agent_label": "lane-3"})
            run_id = claimed.json()["agent_run_id"]
            fin = await call(first, "POST", f"/projects/agent-runs/{run_id}/finish", {})
            assert fin.status_code == 200, fin.text
            assert _place(w, run_id) == ("VJ laptop", "Sonnet 5.5", "computer")
            prompt = await call(w.alice_person, "POST",
                                f"/projects/agent-runs/{run_id}/activities",
                                {"type": "prompt", "body": "One more thing",
                                 "idempotency_key": _key()})
            assert prompt.status_code == 201, prompt.text
        assert _place(w, run_id) == (None, None, None)

    async def test_the_place_never_opens_the_run_of_another_session(self, w):
        """Session B sends the place of session A. The gate still finds the
        run of session A as a holder: 409, and the run keeps its values."""
        task = _fresh(w)
        first, second = _in_session(w.alice, _key()), _in_session(w.alice, _key())
        async with _session(w.p) as call:
            a = await call(first, "POST", f"/projects/tasks/{task}/claim", FULL)
            b = await call(second, "POST", f"/projects/tasks/{task}/claim",
                           {"host": "other", "model": "other", "host_kind": "cloud"})
        assert a.status_code == 200, a.text
        assert b.status_code == 409 and b.json()["detail"]["error"] == "task_held", b.text
        assert _place(w, a.json()["agent_run_id"]) == ("VJ laptop", "Sonnet 5.5", "computer")
