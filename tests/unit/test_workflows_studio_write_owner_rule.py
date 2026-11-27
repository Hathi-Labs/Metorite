"""WFC-4a-2s: only the owner may add or change a Studio write step (hermetic).

Spec: ``project-docs/specs/ai_studio.md`` row WFC-4a-2s (task 3309). Code:
``engine/graph.studio_write_prints`` and ``crud._refuse_foreign_studio_write``,
called by ``crud.update_workflow``. The R8 half, with two orgs and a role
that cannot bypass RLS, is in ``test_workflows_author_gate_r8.py``.

A ``studio_write`` node runs as ``workflows.owner_email`` after the owner
approves a card. The card hides a value whose key looks like a secret, and it
clips a long value (``park.py``). So a member who is not the owner must not
add a write, or change one, in the owner's draft. A Save that keeps a write as
it is, or removes it, stays open. The owner may do each of them.

The fake session here keeps the draft as JSON text, as jsonb keeps it, and
reads no SQL past the first words of each statement. So the R8 half proves the
statement under RLS.

**The mutations (R7).** Each ran on 2026-11-15, and each test named here went
red.

* M1: drop the owner exemption (``_refuse_foreign_studio_write`` checks the
  owner too). Red: the 6 cases of ``test_the_owner_adds_changes_and_removes``
  that add or change, and ``test_the_owner_may_change_a_write`` (R8).
* M2: compare the ids only, and ignore the config. Red: the 8 cases of
  ``test_another_member_cannot_change_a_write``,
  ``test_a_second_node_with_the_id_of_a_kept_write_is_refused``, the
  ``STUDIO_WRITE`` case of ``test_another_member_cannot_respell_a_write``,
  and ``test_another_member_cannot_add_or_change_a_write`` (R8).
* M3: skip the check for a type change (the stored draft counts each node
  of any type). Red: ``test_another_member_cannot_turn_a_step_into_a_write``,
  the ``STUDIO_WRITE`` case of ``test_another_member_cannot_respell_a_write``,
  and ``test_another_member_cannot_turn_a_stored_step_into_a_write`` (R8).
* M4: drop ``FOR UPDATE`` from the read of the stored draft. Red:
  ``test_the_check_locks_the_draft_after_the_trigger_rows``.
"""

from __future__ import annotations

import copy
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.workflows import crud
from gateway.routes.workflows import router as workflows_router
from gateway.routes.workflows.engine.graph import compile_graph, studio_write_prints

OWNER = "owner@example.test"
OTHER = "other@example.test"
WID = str(uuid.uuid4())
PKG = "7a1c0e55-0000-4000-8000-0000000000c3"
PKG_2 = "7a1c0e55-0000-4000-8000-0000000000c4"
RECORD = "7a1c0e55-0000-4000-8000-0000000000d5"
MEMBER = ["feature:workflows"]


def _node(nid: Any, ntype: str, config: Any = None, *, label: str | None = None,
          x: int = 0) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0},
            "data": {"label": label or str(nid), "config": {} if config is None else config}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in pairs]


def _write_config(**over: Any) -> dict:
    return {"package_id": PKG, "action": "add",
            "data": {"site": "Plant 4", "status": "planned", "weight": 1.5}, **over}


def _graph(write: dict | None = None, *extra: dict) -> dict:
    """trigger -> gate -> write, and *extra* nodes with no edge."""
    nodes = [_node("start", "trigger", {"kind": "schedule"}),
             _node("gate", "approval", label="Approve the write")]
    pairs = [("start", "gate")]
    if write is not None:
        nodes.append(write)
        pairs.append(("gate", str(write.get("id"))))
    return {"nodes": [*nodes, *extra], "edges": _edges(*pairs)}


def _write(config: Any = None, *, nid: Any = "write", ntype: str = "studio_write",
           **kw: Any) -> dict:
    return _node(nid, ntype, _write_config() if config is None else config, **kw)


# ── The fake session ─────────────────────────────────────────────────────────


class _FakeDb:
    """One workflow. The draft is JSON text, so each read gives a new copy."""

    def __init__(self, graph: dict, *, owner: str = OWNER) -> None:
        self.stored = json.dumps(graph)
        self.owner = owner
        self.statements: list[str] = []
        self.params: list[dict] = []

    def row(self, **over: Any) -> SimpleNamespace:
        base = dict(id=WID, name="Nightly", description="", owner_email=self.owner,
                    status="draft", latest_version=None, graph=json.loads(self.stored),
                    variables={}, hook_token="tok", created_at=None, updated_at=None,
                    disabled_reason=None, disabled_at=None)
        return SimpleNamespace(**{**base, **over})

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        sql = " ".join(str(clause).split())
        params = params or {}
        self.statements.append(sql)
        self.params.append(params)
        value: Any = None
        if sql.startswith("SELECT * FROM workflows"):
            value = self.row()
        elif sql.startswith("SELECT graph FROM workflows"):
            value = SimpleNamespace(graph=json.loads(self.stored))
        elif sql.startswith("SELECT") and "FROM workflow_triggers" in sql:
            value = []
        elif sql.startswith("UPDATE workflows SET") and "graph" in params:
            self.stored = params["graph"]
        elif sql.startswith("INSERT INTO workflows"):
            value = self.row(id=str(uuid.uuid4()), owner_email=params["owner"],
                             graph=json.loads(params.get("graph") or "{}"))

        class _Result:
            def fetchone(self) -> Any:
                return value

            def fetchall(self) -> list:
                return value or []

        return _Result()

    def graph(self) -> dict:
        return json.loads(self.stored)


@pytest.fixture
def db_of(monkeypatch: pytest.MonkeyPatch) -> Any:
    """``db_of(graph)`` makes the fake of one test, and the route opens it."""
    made: list[_FakeDb] = []

    @asynccontextmanager
    async def _session() -> Any:
        yield made[-1]

    monkeypatch.setattr(crud, "_tenant_session", _session)

    def make(graph: dict, **kw: Any) -> _FakeDb:
        made.append(_FakeDb(graph, **kw))
        return made[-1]

    return make


def _client(email: str) -> TestClient:
    app = FastAPI()
    app.include_router(workflows_router)

    def _user() -> UserContext:
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=str(uuid.uuid4()), access=build_access(MEMBER))

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def _save(db: _FakeDb, graph: dict, email: str = OTHER, **extra: Any) -> Any:
    return _client(email).put(f"/workflows/{WID}", json={"graph": graph, **extra})


def _refused(r: Any, db: _FakeDb, before: dict) -> None:
    """A 403 in plain words, and the draft as it was, with no write."""
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == crud.STUDIO_WRITE_REFUSAL.format(owner=OWNER)
    assert db.graph() == before
    assert not any(s.startswith(("UPDATE", "INSERT", "DELETE")) for s in db.statements)


# ── The prints ───────────────────────────────────────────────────────────────


def _print(config: dict, nid: str = "write", ntype: str = "studio_write") -> tuple:
    return (nid, ntype, json.dumps(config, sort_keys=True))


def test_the_prints_name_each_write_by_id_type_and_config() -> None:
    graph = _graph(_write(), _node("tidy", "set", {"assignments": {}}))
    assert studio_write_prints(graph) == {_print(_write_config())}


def test_key_order_the_canvas_and_the_label_do_not_change_a_print() -> None:
    moved = _write(dict(reversed(list(_write_config().items()))), label="Renamed", x=400)
    assert studio_write_prints(_graph(moved)) == studio_write_prints(_graph(_write()))


def test_a_missing_config_and_an_empty_config_give_one_print() -> None:
    """The run reads each of these as ``{}`` (``_node_config``)."""
    shapes = [
        {"id": "write", "type": "studio_write", "data": {"label": "w"}},
        {"id": "write", "type": "studio_write"},
        {"id": "write", "type": "studio_write", "data": None},
        {"id": "write", "type": "studio_write", "data": {"config": "text"}},
        _write({}),
    ]
    for node in shapes:
        assert studio_write_prints({"nodes": [node]}) == {_print({})}, node


def test_the_read_finds_each_shape_of_a_write() -> None:
    nodes = [_write(), _write(_write_config(action="edit")),       # one id twice
             _write(nid=None), _write(ntype=" Studio_Write "), _write(ntype="STUDIO_WRITE"),
             _write(nid=7)]
    assert studio_write_prints({"nodes": nodes, "edges": []}) == {
        _print(_write_config()), _print(_write_config(action="edit")),
        _print(_write_config(), nid=""), _print(_write_config(), ntype=" Studio_Write "),
        _print(_write_config(), ntype="STUDIO_WRITE"), _print(_write_config(), nid="7"),
    }


@pytest.mark.parametrize("graph", [None, [], "text", {}, {"nodes": "x"},
                                   {"nodes": {"write": _write()}}, {"nodes": [1, "a", None]}])
def test_a_bad_graph_gives_no_print(graph: Any) -> None:
    assert studio_write_prints(graph) == set()


def test_the_run_reads_the_nodes_only_and_so_does_the_rule() -> None:
    """A write in another key of the draft, or inside the config of a step,
    never reaches the run model. So the rule does not count it."""
    hidden = {"id": "x", "type": "studio_write", "data": {"config": _write_config()}}
    graph = _graph(_write())
    graph["blocks"] = [hidden]
    graph["nodes"].append(_node("tidy", "set", {"assignments": {"w": hidden}}))
    graph["edges"] += _edges(("write", "tidy"))
    compiled = compile_graph(graph)
    assert [b["id"] for b in compiled["blocks"]] == ["start", "gate", "write", "tidy"]
    assert [b["type"] for b in compiled["blocks"]].count("studio_write") == 1
    assert studio_write_prints(graph) == {_print(_write_config())}


# ── The route: another member ────────────────────────────────────────────────


def test_another_member_cannot_add_a_write(db_of: Any) -> None:
    before = _graph()
    db = db_of(before)
    _refused(_save(db, _graph(_write())), db, before)


@pytest.mark.parametrize("config", [
    _write_config(data={"site": "Plant 5", "status": "planned", "weight": 1.5}),
    _write_config(data={"site": "Plant 4", "status": "planned", "weight": 1.5,
                        "api_key": "x" * 200}),
    _write_config(data={"site": "Plant 4", "status": "planned", "weight": 2}),
    _write_config(action="remove"),
    _write_config(package_id=PKG_2),
    _write_config(record_id=RECORD),
    {"package_id": PKG, "action": "add"},
    {},
])
def test_another_member_cannot_change_a_write(db_of: Any, config: dict) -> None:
    before = _graph(_write())
    db = db_of(before)
    _refused(_save(db, _graph(_write(config))), db, before)


def test_another_member_cannot_turn_a_step_into_a_write(db_of: Any) -> None:
    """The same id and the same config, with the type changed to a write."""
    before = _graph(_write(ntype="set"))
    db = db_of(before)
    _refused(_save(db, _graph(_write())), db, before)


@pytest.mark.parametrize("stored,sent", [
    ("STUDIO_WRITE", "studio_write"),     # a new spelling that runs
    ("set", " Studio_Write "),            # a spelling that the read still finds
])
def test_another_member_cannot_respell_a_write(db_of: Any, stored: str, sent: str) -> None:
    before = _graph(_write(ntype=stored))
    db = db_of(before)
    _refused(_save(db, _graph(_write(ntype=sent))), db, before)


def test_a_second_node_with_the_id_of_a_kept_write_is_refused(db_of: Any) -> None:
    before = _graph(_write())
    db = db_of(before)
    sent = _graph(_write(), _write(_write_config(action="remove")))
    _refused(_save(db, sent), db, before)


@pytest.mark.parametrize("nid", [None, ""])
def test_a_write_with_no_id_is_refused(db_of: Any, nid: Any) -> None:
    before = _graph(_write())
    db = db_of(before)
    _refused(_save(db, _graph(_write(), _write(nid=nid))), db, before)


def test_another_member_keeps_a_write(db_of: Any) -> None:
    """The member moves the write, renames it, reorders its keys, and adds a
    step. Each print stays, so the Save goes through."""
    db = db_of(_graph(_write()))
    kept = _write(dict(reversed(list(_write_config().items()))), label="Moved", x=300)
    sent = _graph(kept, _node("tidy", "set", {"assignments": {"a": "b"}}))
    r = _save(db, sent, name="Renamed")
    assert r.status_code == 200, r.text
    assert db.graph() == sent and r.json()["name"] == "Nightly"


def test_another_member_keeps_a_write_with_no_config(db_of: Any) -> None:
    stored = {"id": "write", "type": "studio_write", "data": {"label": "w"}}
    db = db_of(_graph(stored))
    sent = _graph(_write({}))
    r = _save(db, sent)
    assert r.status_code == 200, r.text
    assert db.graph() == sent


@pytest.mark.parametrize("sent", [
    _graph(),                                    # the write is gone
    _graph(_write(ntype="set")),                 # the step is not a write now
    {"nodes": [], "edges": []},
])
def test_another_member_removes_a_write(db_of: Any, sent: dict) -> None:
    db = db_of(_graph(_write()))
    r = _save(db, sent)
    assert r.status_code == 200, r.text
    assert db.graph() == sent
    assert not any("FOR UPDATE" in s for s in db.statements)


def test_a_save_with_no_graph_reads_no_draft(db_of: Any) -> None:
    db = db_of(_graph(_write()))
    r = _client(OTHER).put(f"/workflows/{WID}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert not any(s.startswith("SELECT graph") for s in db.statements)


def test_the_check_locks_the_draft_after_the_trigger_rows(db_of: Any) -> None:
    """A Save takes the trigger rows, then the workflow row, as the trigger
    state route does. The check reads the draft before any write."""
    db = db_of(_graph(_write()))
    r = _save(db, _graph(_write()), triggers=[])
    assert r.status_code == 200, r.text
    order = [s.split(" WHERE")[0] for s in db.statements]
    lock = order.index("SELECT graph FROM workflows")
    assert "FOR UPDATE" in db.statements[lock]
    assert db.statements[lock - 1].endswith("FOR UPDATE")
    assert "FROM workflow_triggers" in db.statements[lock - 1]
    assert db.statements[lock + 1].startswith("UPDATE workflows SET")


# ── The route: the owner ─────────────────────────────────────────────────────


@pytest.mark.parametrize("before,sent", [
    (_graph(), _graph(_write())),                                   # add
    (_graph(_write()), _graph(_write(_write_config(action="edit")))),  # change
    (_graph(_write(ntype="set")), _graph(_write())),                # type change
    (_graph(_write()), _graph()),                                   # remove
])
@pytest.mark.parametrize("email", [OWNER, OWNER.upper()])
def test_the_owner_adds_changes_and_removes(db_of: Any, before: dict, sent: dict,
                                           email: str) -> None:
    db = db_of(before)
    r = _save(db, sent, email=email)
    assert r.status_code == 200, r.text
    assert db.graph() == sent


def test_a_workflow_with_no_owner_takes_a_write_from_nobody(db_of: Any) -> None:
    before = _graph()
    db = db_of(before, owner=" ")
    r = _save(db, _graph(_write()))
    assert r.status_code == 403
    assert r.json()["detail"].endswith("Ask the owner.")
    assert db.graph() == before


def test_the_refusal_is_in_plain_words() -> None:
    words = crud.STUDIO_WRITE_REFUSAL.format(owner=OWNER)
    assert words == ("Only the owner of this workflow may add or change a step that "
                     "writes a record. Ask owner@example.test.")
    assert "studio_write" not in words and "node" not in words


# ── The other doors that write a draft ───────────────────────────────────────


def test_a_copy_belongs_to_the_member_who_copies_it(db_of: Any) -> None:
    """The copy runs as its own owner, the member who made it. So the write
    that it carries needs that member's approval, and the source is unchanged."""
    before = _graph(_write())
    db = db_of(before)
    r = _client(OTHER).post(f"/workflows/{WID}/duplicate")
    assert r.status_code == 201, r.text
    assert r.json()["owner_email"] == OTHER
    insert = next(p for s, p in zip(db.statements, db.params, strict=True)
                  if s.startswith("INSERT INTO workflows"))
    assert insert["owner"] == OTHER and json.loads(insert["graph"]) == before
    assert db.graph() == before


def test_a_new_workflow_belongs_to_its_maker_and_starts_empty(db_of: Any) -> None:
    db = db_of(_graph())
    r = _client(OTHER).post("/workflows", json={"name": "Mine"})
    assert r.status_code == 201, r.text
    insert = next(s for s in db.statements if s.startswith("INSERT INTO workflows"))
    assert "graph" not in insert.split("VALUES")[0]


def test_the_prints_do_not_change_the_graph_they_read() -> None:
    graph = _graph(_write())
    frozen = copy.deepcopy(graph)
    studio_write_prints(graph)
    assert graph == frozen
