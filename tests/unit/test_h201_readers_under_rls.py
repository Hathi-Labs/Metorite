"""H-201 — the other readers bind the tenant (``projects_ai_chat.md`` §21.13).

S15 bound the chat save path. Some modules still opened the unbound
``acb_graph.get_session()`` on a FORCE-RLS table. In production an unbound
read there finds no row and an unbound write changes nothing. This suite runs
the REAL readers on the H3 rehearsal's phase-4 catalog, as its NOSUPERUSER
NOBYPASSRLS role, with one fresh backend per session. It reuses the S15
fixtures, so it does not patch ``get_session``.

For each bound reader it shows three things:

* the org's own rows come back under the org's tenant;
* another org gets nothing;
* no tenant refuses, and nothing falls back to an unbound session.

Mutations this suite catches (R7):

* ``workspace._get_workspace_path`` back to ``get_session()``: the org's own
  path reads as ``None``;
* ``set_workspace_path`` back to ``get_session()``: the UPDATE changes no row;
* ``history_tools.query_history`` back to ``get_session()``: the org's own
  message is not found;
* ``agent._member_graph_session`` back to ``get_session()``: the pending
  commit and the audit event of the org read as absent;
* the room check removed from ``POST /agent/respond-input``: Bob and Carol
  reach the relay.

The source fence at the end reads the code, so it runs with no database.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5550/acb_tenant \\
        uv run pytest tests/unit/test_h201_readers_under_rls.py -v -rs
"""
from __future__ import annotations

import asyncio
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
    _BOB,
    _CAROL,
    _admin_one,
    _browser_rows,
    _client,
    _new_session,
    _sid,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def _seed_session(promoted, org: str, owner: str, workspace: str | None) -> str:  # noqa: F811
    sid = _sid()
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, workspace_path, "
            "organization_id) VALUES (:s, :u, 'orchestrator', :w, CAST(:o AS uuid))"),
            {"s": sid, "u": owner, "w": workspace, "o": org})
    return sid


# ── routes/workspace.py — chat_session.workspace_path ───────────────────────

class _Roots:
    """The server-derived roots, on a scratch disk, with one app per org."""

    def __init__(self, base: Path, promoted) -> None:  # noqa: F811
        self.base = base
        self.apps = base / "agents" / "custom_apps"
        self.app_a = self.apps / f"app-a-{uuid.uuid4().hex[:6]}"
        self.app_b = self.apps / f"app-b-{uuid.uuid4().hex[:6]}"
        self.outside = base / "outside"
        for d in (self.app_a, self.app_b, self.outside):
            d.mkdir(parents=True)
        (self.outside / "secret.txt").write_text("DATABASE_URL=x", encoding="utf-8")
        with promoted.admin_engine.begin() as c:
            for slug, path, owner, org in (
                (self.app_a.name, self.app_a, _ALICE, promoted.org_a),
                (self.app_b.name, self.app_b, _CAROL, promoted.org_b),
            ):
                c.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, workspace_path, "
                    "organization_id) VALUES (:s, :s, :o, :w, CAST(:g AS uuid))"),
                    {"s": slug, "o": owner, "w": str(path), "g": org})


def _link(link: Path, target: Path) -> None:
    """A directory link. A junction on Windows, where a symlink needs a grant."""
    import os

    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def roots(graph_as_app, tmp_path, monkeypatch):  # noqa: F811
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "custom_apps"))
    return _Roots(tmp_path, graph_as_app)


@_DB_GATE
def test_the_workspace_path_reads_in_the_members_tenant(graph_as_app, roots):  # noqa: F811
    from acb_graph.db import TenantUnbound
    from gateway.routes.workspace import _get_workspace_path

    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid = _seed_session(graph_as_app, a, _ALICE, str(roots.app_a))

    assert _get_workspace_path(sid, _ALICE, a) == roots.app_a.resolve()
    assert _get_workspace_path(sid, _CAROL, b) is None
    with pytest.raises(TenantUnbound):
        _get_workspace_path(sid, _ALICE, None)


def _patch(sid: str, user, path: str):
    from gateway.routes.workspace import WorkspacePatchRequest, set_workspace_path

    return asyncio.run(set_workspace_path(
        sid, WorkspacePatchRequest(workspace_path=path), _user=user))


def _stored(promoted, sid: str) -> str | None:  # noqa: F811
    return _admin_one(promoted, "SELECT workspace_path AS w FROM "
                      "chat_session WHERE id = :s", s=sid).w


@_DB_GATE
def test_the_workspace_patch_writes_in_the_members_tenant(graph_as_app, roots):  # noqa: F811
    from acb_graph.db import TenantUnbound
    from fastapi import HTTPException

    a = graph_as_app.org_a
    sid = _seed_session(graph_as_app, a, _ALICE, None)

    # No tenant: the room lookup fails closed first, so the answer is 403.
    with pytest.raises(HTTPException) as err:
        _patch(sid, _user(_ALICE, None), str(roots.app_a))
    assert err.value.status_code == 403
    assert _stored(graph_as_app, sid) is None
    # The path check itself refuses with no tenant too.
    from gateway.routes.workspace import _allowed_workspace
    with pytest.raises(TenantUnbound):
        _allowed_workspace(str(roots.app_a), session_id=sid, user_email=_ALICE,
                           organization_id=None, write=True)
    _patch(sid, _user(_ALICE, a), str(roots.app_a))
    assert _stored(graph_as_app, sid) == str(roots.app_a.resolve())


@_DB_GATE
def test_the_workspace_patch_refuses_every_path_outside_an_app_it_may_edit(
    graph_as_app, roots,  # noqa: F811
):
    """🔴 H-201 fix round 1, P0. ``/`` made the whole disk readable through
    ``GET /workspace/S/file?path=proc/self/environ``. Each shape below gets
    422 and changes no row."""
    from fastapi import HTTPException

    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid = _seed_session(graph_as_app, a, _ALICE, None)
    link = roots.app_a / "escape"
    _link(link, roots.outside)
    import os

    refused = {
        "the root": os.path.abspath(os.sep),
        "/etc": "/etc",
        "a dir outside every root": str(roots.outside),
        "a .. escape": str(roots.app_a / ".." / ".." / ".." / "outside"),
        "a link that escapes": str(link),
        "another tenant's app": str(roots.app_b),
        "the apps root itself": str(roots.apps),
        "a path that does not exist": str(roots.apps / "no-such-app"),
    }
    for why, path in refused.items():
        with pytest.raises(HTTPException) as err:
            _patch(sid, _user(_ALICE, a), path)
        assert err.value.status_code == 422, why
        assert _stored(graph_as_app, sid) is None, why
    # Bob is in org A, but he is not in Alice's room (403). Given his own
    # session, he still may not bind Alice's app, which he cannot edit (422).
    with pytest.raises(HTTPException) as err:
        _patch(sid, _user(_BOB, a), str(roots.app_a))
    assert err.value.status_code == 403
    bobs = _seed_session(graph_as_app, a, _BOB, None)
    with pytest.raises(HTTPException) as err:
        _patch(bobs, _user(_BOB, a), str(roots.app_a))
    assert err.value.status_code == 422
    assert _stored(graph_as_app, bobs) is None
    # Carol may edit her own app, but Alice's session is in org A.
    with pytest.raises(HTTPException):
        _patch(sid, _user(_CAROL, b), str(roots.app_a))
    assert _stored(graph_as_app, sid) is None
    # A subfolder of Alice's own app is legitimate.
    (roots.app_a / "src").mkdir()
    _patch(sid, _user(_ALICE, a), str(roots.app_a / "src"))
    assert _stored(graph_as_app, sid) == str((roots.app_a / "src").resolve())


@_DB_GATE
def test_a_stored_path_is_checked_again_on_every_read(graph_as_app, roots):  # noqa: F811
    """A value stored before the fix is still attacker input. A value that
    fails the check counts as absent. The session agent is ``orchestrator``,
    so the fallback is no workspace at all."""
    import os

    from gateway.routes.workspace import _get_workspace_path

    a = graph_as_app.org_a
    link = roots.app_a / "escape"
    _link(link, roots.outside)
    for bad in (os.path.abspath(os.sep), str(roots.outside), str(link),
                str(roots.app_b), str(roots.app_a / ".." / ".." / ".." / "outside")):
        sid = _seed_session(graph_as_app, a, _ALICE, bad)
        assert _get_workspace_path(sid, _ALICE, a) is None, bad
    # A legacy write_artifact root, strictly below the clone root, still reads.
    clone = roots.base / "agents" / "repos" / "agent-h201"
    clone.mkdir(parents=True)
    sid = _seed_session(graph_as_app, a, _ALICE, str(clone))
    assert _get_workspace_path(sid, _ALICE, a) == clone.resolve()
    # But the clone root itself does not.
    sid = _seed_session(graph_as_app, a, _ALICE, str(clone.parent))
    assert _get_workspace_path(sid, _ALICE, a) is None


@_DB_GATE
def test_the_file_route_cannot_read_outside_the_workspace(graph_as_app, roots):  # noqa: F811
    """The attack end to end: a stored root outside every allowed root gives
    404, never the file."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.workspace import get_workspace_file

    a = graph_as_app.org_a
    sid = _seed_session(graph_as_app, a, _ALICE, str(roots.outside))
    app = FastAPI()
    app.get("/workspace/{session_id}/file", response_model=None)(get_workspace_file)
    app.dependency_overrides[get_current_user] = lambda: _user(_ALICE, a)
    got = TestClient(app).get(f"/workspace/{sid}/file", params={"path": "secret.txt"})
    assert got.status_code == 404, got.text
    assert "DATABASE_URL" not in got.text


@pytest.fixture
def workspace_client(roots, monkeypatch):
    """The real workspace router. The blob-store mirror is the only stub."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import workspace

    async def _no_store(*_a, **_k):
        return False

    for name in ("_mirror_gateway_write", "_mirror_gateway_delete", "_faultin_from_store"):
        monkeypatch.setattr(workspace, name, _no_store)

    def _as(user):
        app = FastAPI()
        app.include_router(workspace.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _as


@_DB_GATE
def test_every_workspace_route_checks_the_room(graph_as_app, roots, workspace_client):  # noqa: F811
    """🔴 H-201 fix round 1, P1. Bob is in org A and has the id of Alice's
    private session. Carol is in org B. Neither may list, read, write,
    upload, promote, delete or bind. A read that is refused looks like no
    workspace, and a change that is refused is 403, as in chat."""
    from acb_auth import UserContext
    from acb_auth.access import SERVICE_ACCESS
    from acb_auth.roles import UserRole

    a, b = graph_as_app.org_a, graph_as_app.org_b
    (roots.app_a / "outputs").mkdir()
    (roots.app_a / "outputs" / "note.md").write_text("# Alice", encoding="utf-8")
    (roots.app_a / "inputs").mkdir()
    (roots.app_a / "inputs" / "spec.txt").write_text("spec", encoding="utf-8")
    sid = _seed_session(graph_as_app, a, _ALICE, str(roots.app_a))
    base = f"/agent/workspace/{sid}"

    for who in (_user(_BOB, a), _user(_CAROL, b), _user(_ALICE, None)):
        c = workspace_client(who)
        tree = c.get(base)
        assert tree.status_code == 200 and tree.json()["files"] == [], who.email
        assert c.get(f"{base}/file", params={"path": "outputs/note.md"}).status_code == 404
        assert c.get(f"{base}/history", params={"path": "outputs/note.md"}).json() == {"history": []}
        assert c.get(f"{base}/events").status_code == 404
        assert c.put(f"{base}/file", params={"path": "outputs/x.md"},
                     json={"content": "forged"}).status_code == 403
        assert c.delete(f"{base}/file", params={"path": "outputs/note.md"}).status_code == 403
        assert c.post(f"{base}/upload", files={"files": ("f.txt", b"x")}).status_code == 403
        assert c.post(f"{base}/promote", json={"path": "inputs/spec.txt"}).status_code == 403
        assert c.patch(base, json={"workspace_path": str(roots.app_a)}).status_code == 403
        assert c.post(f"{base}/events", json={"name": "artifact_created",
                                              "path": "outputs/x.md"}).status_code == 403
    assert (roots.app_a / "outputs" / "note.md").is_file()
    assert not (roots.app_a / "outputs" / "x.md").exists()

    # Alice owns the room.
    alice = workspace_client(_user(_ALICE, a))
    names = {f["path"] for f in alice.get(base).json()["files"]}
    assert "outputs/note.md" in names
    got = alice.get(f"{base}/file", params={"path": "outputs/note.md"})
    assert got.status_code == 200 and "# Alice" in got.text
    put = alice.put(f"{base}/file", params={"path": "outputs/x.md"}, json={"content": "ok"})
    assert put.status_code == 200, put.text
    assert alice.delete(f"{base}/file", params={"path": "outputs/x.md"}).status_code == 200

    # The write_artifact tool posts events with the internal token and no member.
    service = UserContext(email="system:internal", role=UserRole.AGENT,
                          access=SERVICE_ACCESS)
    assert workspace_client(service).post(
        f"{base}/events", json={"name": "artifact_created", "path": "outputs/x.md"},
    ).status_code == 204


# ── acb_skills/history_tools.py — query_history ─────────────────────────────

def _history(org: str | None, user: str | None = None, *, verified: bool = True,
             **criteria) -> str:
    """``query_history`` on a run frame that carries *org* and *user*, the
    way the executor sets them: ``_RUN_ORG`` keyed by the run's thread id,
    and the run context with the member that the session verified."""
    from acb_common import bind_run_context
    from acb_skills.history_tools import query_history
    from orchestrator import executor

    tid = f"h201-run-{uuid.uuid4().hex[:8]}"

    async def _go() -> str:
        executor._stream_relay_thread_id.set(tid)
        bind_run_context(thread_id=tid, user=user, member_verified=verified)
        if org:
            executor._RUN_ORG[tid] = org
        try:
            return await query_history(**criteria)
        finally:
            executor._RUN_ORG.pop(tid, None)

    return asyncio.run(_go())


def _seed_room(promoted, org: str, owner: str, messages: list[tuple[str, int]], *,  # noqa: F811
               participants: tuple[tuple[str, str, int | None], ...] = (),
               history: str = "full") -> str:
    """A session with its messages, and participant rows when given. The
    owner row is written only when a participant is named, as S14 does."""
    sid = _sid()
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, history_visibility, "
            "organization_id) VALUES (:s, :u, 'orchestrator', :h, CAST(:o AS uuid))"),
            {"s": sid, "u": owner, "h": history, "o": org})
        rows = ((owner, "owner", None), *participants) if participants else ()
        for subject, role, join_ts in rows:
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "join_message_ts, organization_id) VALUES (:s, :p, :r, :j, "
                "CAST(:o AS uuid))"),
                {"s": sid, "p": subject, "r": role, "j": join_ts, "o": org})
        for content, ts in messages:
            c.execute(text(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "organization_id) VALUES (:i, :s, 'user', :c, :t, CAST(:o AS uuid))"),
                {"i": f"m-{uuid.uuid4().hex[:8]}", "s": sid, "c": content, "t": ts,
                 "o": org})
    return sid


@_DB_GATE
def test_query_history_reads_in_the_runs_tenant(graph_as_app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "true")
    a, b = graph_as_app.org_a, graph_as_app.org_b
    term = f"h201-{uuid.uuid4().hex[:8]}"
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    rows = _browser_rows(f"u-{uuid.uuid4().hex[:8]}")
    rows[0]["content"] = f"Recall {term} please"
    assert alice.post(f"/chat/sessions/{sid}/messages", json=rows).json()["saved"] == 1

    mine = json.loads(_history(a, _ALICE, search=term))
    assert [(r["thread_id"], r["role"]) for r in mine] == [(sid, "user")]
    assert term in mine[0]["content"]

    assert _history(b, _CAROL, search=term) == "[]"
    # With the bind ON and no tenant, the opener is None and the tool answers
    # empty. It never opens an unbound session.
    assert _history(None, _ALICE, search=term) == "[]"


@_DB_GATE
def test_query_history_reads_only_rooms_the_member_may_read(graph_as_app, monkeypatch):  # noqa: F811
    """🔴 H-201 fix round 1, P1 (D12). A cron, webhook or email run has no
    member. Before, the member criterion dropped out and the tool gave every
    private chat of the org, and injected email text could ask for it."""
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "true")
    a = graph_as_app.org_a
    term = f"h201-{uuid.uuid4().hex[:8]}"
    own = _seed_room(graph_as_app, a, _ALICE, [(f"alice {term}", 100)])
    bobs = _seed_room(graph_as_app, a, _BOB, [(f"bob private {term}", 200)])
    shared = _seed_room(graph_as_app, a, _BOB, [(f"bob shared {term}", 300)],
                        participants=((_ALICE, "member", None),))
    late = _seed_room(graph_as_app, a, _BOB,
                      [(f"before join {term}", 400), (f"after join {term}", 600)],
                      participants=((_ALICE, "member", 500),), history="since_join")

    def _seen(user, **kw) -> set[tuple[str, str]]:
        out = _history(a, user, search=term, limit=20, **kw)
        return {(r["thread_id"], r["content"]) for r in json.loads(out)}

    assert _seen(_ALICE) == {
        (own, f"alice {term}"), (shared, f"bob shared {term}"),
        (late, f"after join {term}"),
    }
    assert (bobs, f"bob private {term}") not in _seen(_ALICE)
    # Bob sees his own rooms, and not Alice's private one.
    assert own not in {sid for sid, _ in _seen(_BOB)}
    # No member, and a member that no session verified, see nothing.
    assert _history(a, None, search=term) == "[]"
    assert _history(a, _ALICE, verified=False, search=term) == "[]"
    # A thread criterion cannot reach past the rule.
    assert _seen(_ALICE, thread_id=bobs) == set()


# ── routes/agent.py — pending_commit and audit_event ────────────────────────

def _seed_commit(promoted, org: str) -> str:  # noqa: F811
    cid = str(uuid.uuid4())
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pending_commit (id, agent_name, run_id, local_clone_dir, "
            "commit_sha, commit_message, organization_id) VALUES (CAST(:i AS uuid), "
            "'h201-agent', 'run-h201', '/nonexistent', 'deadbeef', 'fix', "
            "CAST(:o AS uuid))"), {"i": cid, "o": org})
    return cid


def _seed_audit(promoted, org: str) -> str:  # noqa: F811
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO audit_event (actor, action, target, payload, organization_id) "
            "VALUES ('system:mutation', 'agent_run_complete', 'agent:h201', "
            "CAST(:p AS jsonb), CAST(:o AS uuid))"),
            {"p": json.dumps({"run_id": run_id}), "o": org})
    return run_id


@_DB_GATE
def test_pending_commits_read_in_the_members_tenant(graph_as_app):  # noqa: F811
    from acb_graph.db import TenantUnbound
    from fastapi import HTTPException
    from gateway.routes.agent import (
        get_pending_commit_diff,
        list_mutations,
        list_pending_commits,
    )

    a, b = graph_as_app.org_a, graph_as_app.org_b
    cid = _seed_commit(graph_as_app, a)
    alice, carol = _user(_ALICE, a), _user(_CAROL, b)

    def _ids(rows) -> set[str]:
        return {r["id"] for r in rows if "id" in r}

    assert cid in _ids(asyncio.run(list_pending_commits(limit=200, user=alice)))
    assert cid in _ids(asyncio.run(list_mutations(limit=200, user=alice)))
    assert asyncio.run(get_pending_commit_diff(cid, user=alice))["id"] == cid

    assert cid not in _ids(asyncio.run(list_pending_commits(limit=200, user=carol)))
    assert cid not in _ids(asyncio.run(list_mutations(limit=200, user=carol)))
    with pytest.raises(HTTPException) as err:
        asyncio.run(get_pending_commit_diff(cid, user=carol))
    assert err.value.status_code == 404

    nobody = _user(_ALICE, None)
    for call in (lambda: list_pending_commits(limit=5, user=nobody),
                 lambda: list_mutations(limit=5, user=nobody),
                 lambda: get_pending_commit_diff(cid, user=nobody)):
        with pytest.raises(TenantUnbound):
            asyncio.run(call())


@_DB_GATE
def test_a_pending_commit_delete_stays_in_the_members_tenant(graph_as_app):  # noqa: F811
    from acb_graph.db import TenantUnbound
    from gateway.routes.agent import delete_pending_commit

    a, b = graph_as_app.org_a, graph_as_app.org_b
    cid = _seed_commit(graph_as_app, a)

    def _there() -> bool:
        return _admin_one(graph_as_app, "SELECT 1 AS x FROM pending_commit "
                          "WHERE id = CAST(:i AS uuid)", i=cid) is not None

    out = asyncio.run(delete_pending_commit(cid, user=_user(_CAROL, b)))
    assert out["rows_deleted"] == 0 and _there()
    with pytest.raises(TenantUnbound):
        asyncio.run(delete_pending_commit(cid, user=_user(_ALICE, None)))
    assert _there()
    out = asyncio.run(delete_pending_commit(cid, user=_user(_ALICE, a)))
    assert out["rows_deleted"] == 1 and not _there()


@_DB_GATE
def test_run_status_and_audit_dismiss_stay_in_the_members_tenant(graph_as_app):  # noqa: F811
    from acb_graph.db import TenantUnbound
    from gateway.routes.agent import dismiss_mutation_event, get_run_status

    a, b = graph_as_app.org_a, graph_as_app.org_b
    run_id = _seed_audit(graph_as_app, a)
    alice, carol = _user(_ALICE, a), _user(_CAROL, b)

    assert asyncio.run(get_run_status(run_id, user=alice))["status"] == "completed"
    assert asyncio.run(get_run_status(run_id, user=carol))["status"] == "not_found"
    with pytest.raises(TenantUnbound):
        asyncio.run(get_run_status(run_id, user=_user(_ALICE, None)))

    assert asyncio.run(dismiss_mutation_event(run_id, user=carol))["rows_deleted"] == 0
    with pytest.raises(TenantUnbound):
        asyncio.run(dismiss_mutation_event(run_id, user=_user(_ALICE, None)))
    assert asyncio.run(dismiss_mutation_event(run_id, user=alice))["rows_deleted"] == 1


# ── POST /agent/respond-input — the room check before the relay ─────────────

@pytest.fixture
def relay_calls(monkeypatch):
    """Both delivery paths, stubbed. The list records every call that got
    past the room check."""
    from orchestrator import executor, stream_relay

    calls: list[str] = []

    def _fast(request_id, *_a, **_k):
        calls.append(f"fast:{request_id}")
        return False

    async def _relay(thread_id, *_a, **_k):
        calls.append(f"relay:{thread_id}")
        return False

    monkeypatch.setattr(executor, "resolve_user_input", _fast)
    monkeypatch.setattr(stream_relay, "dispatch_control", _relay)
    return calls


def _answer(user, thread_id: str | None, request_id: str = "req-h201"):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.agent import respond_user_input

    app = FastAPI()
    app.post("/agent/respond-input")(respond_user_input)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app).post("/agent/respond-input", json={
        "request_id": request_id, "answer": "yes", "thread_id": thread_id,
    })


@_DB_GATE
def test_respond_input_needs_send_in_the_room(graph_as_app, relay_calls):  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    assert alice.post(f"/chat/sessions/{sid}/messages",
                      json=_browser_rows("u-h201")).json()["saved"] == 1

    # Bob is in org A and is not in Alice's private room.
    assert _answer(_user(_BOB, a), sid).status_code == 403
    # Carol is in org B. Alice's session reads as another tenant's.
    assert _answer(_user(_CAROL, b), sid).status_code == 403
    # No tenant is no capability.
    assert _answer(_user(_ALICE, None), sid).status_code == 403
    # A call that names no thread is refused before any delivery (fix round 1).
    assert _answer(_user(_ALICE, a), None).status_code == 422
    assert relay_calls == []

    # Alice passes the check. No run is parked, so both paths say no: 409.
    assert _answer(_user(_ALICE, a), sid).status_code == 409
    assert relay_calls == ["fast:req-h201", f"relay:{sid}"]


@_DB_GATE
def test_respond_input_answers_only_for_the_thread_that_asked(graph_as_app, monkeypatch):  # noqa: F811
    """🔴 H-201 fix round 1, P2. Bob is a VIEWER in Alice's room, so he saw
    her request id in her stream. Before, the fast path resolved by the
    request id alone. With no thread, or with the id of Bob's own room, his
    answer reached Alice's parked run."""
    from orchestrator import executor, stream_relay

    async def _no_relay(*_a, **_k):
        return False

    monkeypatch.setattr(stream_relay, "dispatch_control", _no_relay)
    a = graph_as_app.org_a
    alices = _seed_room(graph_as_app, a, _ALICE, [("q", 1)],
                        participants=((_BOB, "viewer", None),))
    bobs = _seed_room(graph_as_app, a, _BOB, [("mine", 1)])

    async def _go() -> tuple[list[int], bool, dict | None]:
        fut = asyncio.get_running_loop().create_future()
        executor._pending_user_input.park("req-h201-owner", fut, alices)
        try:
            codes = []
            for tid in (None, alices, bobs):
                r = await asyncio.to_thread(_answer, _user(_BOB, a), tid, "req-h201-owner")
                codes.append(r.status_code)
            await asyncio.sleep(0.05)
            stolen = fut.done()
            r = await asyncio.to_thread(_answer, _user(_ALICE, a), alices, "req-h201-owner")
            codes.append(r.status_code)
            await asyncio.sleep(0.05)
            return codes, stolen, (fut.result() if fut.done() else None)
        finally:
            executor._pending_user_input.pop("req-h201-owner", None)

    codes, stolen, answer = asyncio.run(_go())
    assert stolen is False, "Bob's answer reached Alice's parked run"
    # No thread: 422. Bob in Alice's room is a viewer: 403. Bob in his own
    # room passes the room check, but his room does not own the request: 409.
    assert codes[:3] == [422, 403, 409]
    # Alice, in the room that asked, answers it.
    assert codes[3] == 200
    assert answer == {"answer": "yes", "wasFreeform": True}


# ── The source fence. It reads the code, so it cannot skip. ─────────────────

def test_the_h201_readers_open_no_unbound_session() -> None:
    import inspect

    from acb_skills import history_tools
    from gateway.routes import agent, workspace

    for fn in (workspace._get_workspace_path, workspace.set_workspace_path,
               history_tools.query_history,
               agent.get_run_status, agent.list_mutations,
               agent.list_pending_commits, agent.get_pending_commit_diff,
               agent.approve_pending_commit, agent.reject_pending_commit,
               agent.remutate_pending_commit, agent.delete_pending_commit,
               agent.dismiss_mutation_event, agent._member_graph_session):
        src = inspect.getsource(fn)
        assert "get_session" not in src, fn.__name__
    # The workspace read takes the tenant with no default to fall back on.
    param = inspect.signature(workspace._get_workspace_path).parameters["organization_id"]
    assert param.default is inspect.Parameter.empty
    # The history tool takes its tenant from the run, never from an argument.
    assert "organization_id" not in inspect.signature(history_tools.query_history).parameters
    assert "_graph_session_opener_current" in inspect.getsource(history_tools.query_history)
    # The answer path checks the room before it relays.
    src = inspect.getsource(agent.respond_user_input)
    assert src.index("can_send") < src.index("dispatch_control(")
    assert src.index("can_send") < src.index("resolve_user_input(")
    assert "thread_id=req.thread_id" in src
    # The executor answers only for the thread that owns the request.
    from orchestrator import executor
    param = inspect.signature(executor.resolve_user_input).parameters["thread_id"]
    assert param.default is inspect.Parameter.empty
