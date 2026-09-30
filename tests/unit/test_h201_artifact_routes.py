"""H-201 part 2 — the global artifact routes serve only the caller's own
workspace (``projects_ai_chat.md`` §21.14).

The four routes are ``GET /agent/artifacts``, ``GET /agent/artifacts/file``,
``PUT /agent/artifacts/file`` and ``POST /agent/artifacts/upload``. Before
this part they resolved a SHARED agent to its one clone, ``repos/<agent>``.
Every tenant runs that agent in that clone, so any member of any org could
list, read, write and upload into the run output of another org.

Now each route resolves through ``workspace._member_agent_workspace``. It
gives the caller's own state dir of a ``personal`` agent, and nothing else.

The fixture builds four agents on a scratch disk:

* ``agent-h201p`` is ``personal``. Alice (org A) and Carol (org B) each have
  a private file in their own state dir.
* ``agent-h201s`` is ``shared``. Its clone holds a run output of org A.
* ``agent-h201t`` is ``team``, keyed by the team name alone.
* ``agent-h201n`` is ``personal`` in the registry, with no clone on disk.

The R8 tests run the blob write-through and the fault-in on the H3
rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS role.

Mutations this suite catches (R7):

* ``_discover_agent_workspaces`` back to ``_agent_workspace_dir(...) or
  _canonical_workspace_dir(...)``: the list, read and write tests fail;
* ``upload_artifact`` back to that resolution: the upload test fails;
* the ``u:<email>`` check in ``_member_agent_workspace`` widened to any
  instance: the team agent is served, and two tests fail;
* the ``state_root`` containment check removed: a planted link reaches the
  shared clone, and the link test fails;
* ``organization_id`` dropped from the write-through: no blob row lands;
* ``organization_id`` dropped from the fault-in: the file is not restored.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_h201_artifact_routes.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_P, _S, _T, _N = "agent-h201p", "agent-h201s", "agent-h201t", "agent-h201n"
_ORG_A_SECRET = "ORG-A RUN OUTPUT"


def _config(instancing: str, team: str | None = None) -> str:
    sharing: dict = {"instancing": instancing, "visibility": "organization"}
    if team:
        sharing["team"] = team
    return json.dumps({"name": "x", "sharing": sharing})


class _Disk:
    """The scratch clone root, state root and the four agents."""

    def __init__(self, base: Path) -> None:
        from acb_skills.agent_paths import ensure_state_dir

        self.repos = base / "agents" / "repos"
        for name, cfg in ((_P, _config("personal")), (_S, _config("shared")),
                          (_T, _config("team", "ops"))):
            (self.repos / name).mkdir(parents=True)
            (self.repos / name / "config.json").write_text(cfg, encoding="utf-8")
        self.shared_out = self.repos / _S / "outputs"
        self.shared_out.mkdir()
        (self.shared_out / "org-a-run.md").write_text(_ORG_A_SECRET, encoding="utf-8")
        (self.repos / _T / "outputs").mkdir()
        (self.repos / _T / "outputs" / "team.md").write_text("TEAM", encoding="utf-8")
        self.alice = ensure_state_dir(_P, f"u:{_ALICE}")
        self.carol = ensure_state_dir(_P, f"u:{_CAROL}")
        for ws, who in ((self.alice, "ALICE"), (self.carol, "CAROL")):
            (ws / "outputs").mkdir()
            (ws / "outputs" / f"{who.lower()}.md").write_text(who, encoding="utf-8")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY",
                        [{"name": n} for n in (_P, _S, _T, _N)])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    return _Disk(tmp_path)


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import workspace

    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _listing(client, **params) -> list[dict]:
    r = client.get("/agent/artifacts", params=params)
    assert r.status_code == 200, r.text
    return r.json()["artifacts"]


def _files(entries: list[dict]) -> set[tuple[str, str]]:
    return {(e["agent_name"], e["path"]) for e in entries if not e["is_dir"]}


# ── The resolver — no database ──────────────────────────────────────────────

def test_the_member_workspace_is_only_the_callers_personal_state_dir(disk) -> None:
    from gateway.routes.workspace import _member_agent_workspace

    assert _member_agent_workspace(_P, _ALICE) == disk.alice
    assert _member_agent_workspace(_P, _CAROL) == disk.carol
    assert _member_agent_workspace(_S, _ALICE) is None
    assert _member_agent_workspace(_T, _ALICE) is None
    assert _member_agent_workspace(_N, _ALICE) is None
    assert not (disk.repos / _N).exists()
    for nobody in (None, "", "default", "internal"):
        assert _member_agent_workspace(_P, nobody) is None
    for bad in ("..", "../agent-h201s", "a/b", ""):
        assert _member_agent_workspace(bad, _ALICE) is None


def test_a_planted_link_in_the_state_root_does_not_reach_the_clone(disk) -> None:
    """A state dir that is a link into ``repos/`` resolves out of the state
    root, so it is not a workspace."""
    import os
    import shutil

    from gateway.routes.workspace import _member_agent_workspace

    shutil.rmtree(disk.alice)
    try:
        os.symlink(disk.repos / _S, disk.alice, target_is_directory=True)
    except OSError:
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(disk.repos / _S), str(disk.alice))
    assert _member_agent_workspace(_P, _ALICE) is None


# ── The four routes — members of two orgs (R8) ──────────────────────────────

@_DB_GATE
def test_each_member_lists_only_their_own_files(graph_as_app, disk) -> None:  # noqa: F811
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    carol = _client(_user(_CAROL, graph_as_app.org_b))

    a_files, c_files = _files(_listing(alice)), _files(_listing(carol))
    assert (_P, "outputs/alice.md") in a_files
    assert (_P, "outputs/carol.md") in c_files
    for got, other in ((a_files, "outputs/carol.md"), (c_files, "outputs/alice.md")):
        assert (_P, other) not in got
        assert not {n for n, _p in got} & {_S, _T, _N}
    # The agent filter does not reopen a shared agent.
    for client in (alice, carol):
        assert _listing(client, agent=_S) == []
        assert _listing(client, agent=_T) == []
        assert _listing(client, agent=_S, category="outputs") == []


@_DB_GATE
def test_no_member_reads_a_shared_clone_or_another_members_file(graph_as_app, disk) -> None:  # noqa: F811
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    carol = _client(_user(_CAROL, graph_as_app.org_b))

    for client in (alice, carol):
        r = client.get("/agent/artifacts/file",
                       params={"agent": _S, "path": "outputs/org-a-run.md"})
        assert r.status_code == 404, r.status_code
        assert _ORG_A_SECRET not in r.text
        r = client.get("/agent/artifacts/file",
                       params={"agent": _T, "path": "outputs/team.md"})
        assert r.status_code == 404
    # Carol asks for Alice's path, and her own folder has no such file.
    r = carol.get("/agent/artifacts/file", params={"agent": _P, "path": "outputs/alice.md"})
    assert r.status_code == 404 and "ALICE" not in r.text
    # A relative path out of Alice's own folder is refused.
    for escape in (f"../{disk.carol.name}/outputs/carol.md",
                   "../../../repos/agent-h201s/outputs/org-a-run.md"):
        r = alice.get("/agent/artifacts/file", params={"agent": _P, "path": escape})
        assert r.status_code in (400, 404), (escape, r.status_code)
        assert "CAROL" not in r.text and _ORG_A_SECRET not in r.text
    # Her own file still reads.
    r = alice.get("/agent/artifacts/file", params={"agent": _P, "path": "outputs/alice.md"})
    assert r.status_code == 200 and r.text == "ALICE"


@_DB_GATE
def test_no_member_writes_a_shared_clone_or_another_members_file(graph_as_app, disk) -> None:  # noqa: F811
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    carol = _client(_user(_CAROL, graph_as_app.org_b))

    for client in (alice, carol):
        for agent in (_S, _T):
            r = client.put("/agent/artifacts/file",
                           params={"agent": agent, "path": "outputs/org-a-run.md"},
                           json={"content": "OVERWRITTEN", "encoding": "utf-8"})
            assert r.status_code == 404, (agent, r.status_code)
    assert (disk.shared_out / "org-a-run.md").read_text(encoding="utf-8") == _ORG_A_SECRET
    assert (disk.repos / _T / "outputs" / "team.md").read_text(encoding="utf-8") == "TEAM"

    # Carol writes Alice's path. It lands in Carol's own folder only.
    r = carol.put("/agent/artifacts/file", params={"agent": _P, "path": "outputs/alice.md"},
                  json={"content": "FROM CAROL", "encoding": "utf-8"})
    assert r.status_code == 200, r.text
    assert (disk.carol / "outputs" / "alice.md").read_text(encoding="utf-8") == "FROM CAROL"
    assert (disk.alice / "outputs" / "alice.md").read_text(encoding="utf-8") == "ALICE"
    # The old rule still holds: writes stay in the three visible folders.
    r = alice.put("/agent/artifacts/file", params={"agent": _P, "path": "config.json"},
                  json={"content": "{}", "encoding": "utf-8"})
    assert r.status_code == 400


@_DB_GATE
def test_the_upload_goes_only_to_the_callers_own_folder(graph_as_app, disk) -> None:  # noqa: F811
    """The legitimate flow is the email rule editor: upload into a personal
    agent, then pick the file from the list, then read it."""
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    carol = _client(_user(_CAROL, graph_as_app.org_b))

    for client in (alice, carol):
        for agent in (_S, _T, _N):
            r = client.post("/agent/artifacts/upload",
                            params={"agent": agent, "category": "inputs"},
                            files={"files": ("x.md", b"# planted")})
            assert r.status_code == 404, (agent, r.status_code)
    assert not (disk.repos / _S / "inputs").exists()
    assert not (disk.repos / _T / "inputs").exists()
    assert not (disk.repos / _N).exists()

    r = alice.post("/agent/artifacts/upload",
                   params={"agent": _P, "category": "agent-data"},
                   files={"files": ("draft.md", b"# alice draft")})
    assert r.status_code == 200, r.text
    assert r.json()[0]["path"] == "agent-data/draft.md"
    assert (disk.alice / "agent-data" / "draft.md").read_bytes() == b"# alice draft"
    assert not (disk.carol / "agent-data" / "draft.md").exists()

    picked = _files(_listing(alice, agent=_P, category="agent-data"))
    assert picked == {(_P, "agent-data/draft.md")}
    assert _files(_listing(carol, agent=_P, category="agent-data")) == set()
    got = alice.get("/agent/artifacts/file", params={"agent": _P, "path": "agent-data/draft.md"})
    assert got.status_code == 200 and got.text == "# alice draft"


@_DB_GATE
def test_a_caller_with_no_member_gets_nothing(graph_as_app, disk) -> None:  # noqa: F811
    for email in ("default", ""):
        c = _client(_user(email, graph_as_app.org_a))
        assert _listing(c) == []
        r = c.get("/agent/artifacts/file", params={"agent": _S, "path": "outputs/org-a-run.md"})
        assert r.status_code == 404


@_DB_GATE
def test_the_write_through_lands_in_the_callers_tenant(graph_as_app, disk) -> None:  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    name = f"r8-{uuid.uuid4().hex[:6]}.md"
    for email, org, body in ((_ALICE, a, "A"), (_CAROL, b, "B")):
        r = _client(_user(email, org)).put(
            "/agent/artifacts/file", params={"agent": _P, "path": f"outputs/{name}"},
            json={"content": body, "encoding": "utf-8"})
        assert r.status_code == 200, r.text

    with graph_as_app.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text AS o, instance, convert_from(content, 'UTF8') AS body "
            "FROM agent_blob WHERE agent_name = :n AND path = :p ORDER BY instance"),
            {"n": _P, "p": f"outputs/{name}"}).fetchall()
    assert [(r.o, r.instance, r.body) for r in rows] == [
        (a, f"u:{_ALICE}", "A"), (b, f"u:{_CAROL}", "B"),
    ]


@_DB_GATE
def test_the_fault_in_reads_only_the_callers_tenant(graph_as_app, disk) -> None:  # noqa: F811
    """A file missing from the disk is restored from the blob store, in the
    caller's tenant. The same email under another tenant finds nothing."""
    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/fi-{uuid.uuid4().hex[:6]}.md"
    alice = _client(_user(_ALICE, a))
    assert alice.put("/agent/artifacts/file", params={"agent": _P, "path": rel},
                     json={"content": "STORED", "encoding": "utf-8"}).status_code == 200

    (disk.alice / rel).unlink()
    other = _client(_user(_ALICE, b)).get(
        "/agent/artifacts/file", params={"agent": _P, "path": rel})
    assert other.status_code == 404 and "STORED" not in other.text
    assert not (disk.alice / rel).exists()

    own = alice.get("/agent/artifacts/file", params={"agent": _P, "path": rel})
    assert own.status_code == 200 and own.text == "STORED"
