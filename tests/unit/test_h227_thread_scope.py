"""H-227 — a shared agent's uploads and S8 documents are thread-scoped (D12).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3 ("Uploads and S8
documents are thread-scoped") and ``project-docs/specs/projects_ai_chat.md``
§21.15 and §22. HANDOFF H-227.

Every session of a shared agent in one organization opens the same tenant
dir, ``state/<agent>/<slug of o:<org>>``. Before H-227:

* an S8 document that ``write_artifact`` wrote landed in the shared
  ``outputs/``, so any member of the org could read it from a session;
* an upload landed in ``inputs/<thread slug>/`` (H-229), but the workspace
  routes, ``TenantFileStore`` and the container still reached the
  ``inputs/`` folder of another thread.

Now ONE slug rule (``agent_paths.thread_slug``) names both folders of a
thread, and ONE rule (``agent_paths.is_other_thread_rel``) hides the folders
of another thread from every reader. A loose file, one in ``inputs/`` or
``outputs/`` but in no thread folder, comes from before the thread folders.
The routes serve it only to the session that the blob history shows wrote
those bytes (``workspace._session_wrote``).

Four halves:

* the pure rules, ``write_artifact`` and ``share_artifact``, with no database;
* ``TenantFileStore`` and the broker's mounts, on the fake Docker;
* the routes on the R8 database as the NOBYPASSRLS app role, with the real
  workspace router (``TENANT_LADDER_DATABASE_URL``);
* the ``sandbox_docker`` half, on the coding image, which
  ``.github/workflows/sandbox-docker.yml`` runs and which fails on a skip.

Mutations this suite catches (R7). The spec's table carries the counts.

* ``is_other_thread_rel`` checks ``outputs/`` only again;
* ``write_artifact`` drops ``_thread_scoped`` (the flat ``outputs/`` again);
* ``share_artifact`` drops ``_thread_scoped``, or shows a loose file;
* ``TenantFileStore`` maps ``inputs/`` to the shared ``inputs/`` again;
* ``projects_mounts`` drops the ``inputs/<thread slug>/`` cover, or makes it
  writable;
* the tree, file, history, delete, PUT or promote route drops its loose rule;
* ``_session_wrote`` ignores the session, or the sha256;
* the fault-in writes a loose file before the history check;
* ``_session_history`` reads the ``.cc-instance`` marker for the store key.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_h227_thread_scope.py -v -rs
"""
from __future__ import annotations

import asyncio
import importlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import bound_run, configure_env, mounts_of
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    PA,
    new_thread,
    sandbox,
    short_tmp,
)

wa = importlib.import_module("acb_skills.write_artifact")


# ═════════════════════════ the pure rules ═══════════════════════════════════


def test_one_slug_rule_names_both_folders_of_a_thread() -> None:
    from acb_skills.agent_paths import (
        tenant_instance,
        thread_inputs_rel,
        thread_outputs_rel,
        thread_slug,
        upload_dir_rel,
    )

    t = new_thread()
    slug = thread_slug(t)
    assert thread_outputs_rel(t) == f"outputs/{slug}"
    assert thread_inputs_rel(t) == f"inputs/{slug}"
    assert upload_dir_rel(tenant_instance(ORG_A), t) == thread_inputs_rel(t)
    assert upload_dir_rel("u:alice@x.io", t) == "inputs", "a personal agent keeps inputs/"


def test_another_threads_upload_folder_is_another_chats() -> None:
    from acb_skills.agent_paths import is_loose_rel, is_other_thread_rel, thread_slug

    mine, other = thread_slug(new_thread()), thread_slug(new_thread())
    for head in ("inputs", "outputs"):
        assert is_other_thread_rel(f"{head}/{other}/x.docx", mine), head
        assert not is_other_thread_rel(f"{head}/{mine}/x.docx", mine), head
        assert is_loose_rel(f"{head}/x.docx"), head
        assert not is_loose_rel(f"{head}/{mine}/x.docx"), head
    # A member's own folder that only looks like a slug is loose, not a thread's.
    assert is_loose_rel("outputs/q3-20240101/x.md")
    assert not is_loose_rel("agent-data/NOTES.md") and not is_loose_rel("outputs")


def test_a_path_moves_into_the_threads_own_folder() -> None:
    from acb_skills.agent_paths import thread_scoped_rel, thread_slug

    t = new_thread()
    slug, other = thread_slug(t), thread_slug(new_thread())
    assert thread_scoped_rel("outputs/report.md", t) == f"outputs/{slug}/report.md"
    assert thread_scoped_rel("outputs/q2/report.md", t) == f"outputs/{slug}/q2/report.md"
    assert thread_scoped_rel("inputs/data.csv", t) == f"inputs/{slug}/data.csv"
    assert thread_scoped_rel("outputs", t) == f"outputs/{slug}"
    # Already in a thread folder: it stays, so the caller still refuses another's.
    assert thread_scoped_rel(f"outputs/{other}/x", t) == f"outputs/{other}/x"
    assert thread_scoped_rel("agent-data/NOTES.md", t) == "agent-data/NOTES.md"
    with pytest.raises(ValueError):
        thread_scoped_rel("outputs/x", "Thread With Spaces!")


def test_the_history_rule_needs_this_session_and_these_bytes() -> None:
    from gateway.routes.workspace import _session_wrote

    row = {"path": "outputs/plan.md", "session_id": "s-alice", "sha256": "a" * 64,
           "action": "create", "actor": "agent"}
    assert _session_wrote([row], "s-alice", "a" * 64)
    assert _session_wrote([{**row, "action": "modify", "actor": "user"}], "s-alice", "a" * 64)
    assert not _session_wrote([row], "s-bob", "a" * 64), "another session's row"
    assert not _session_wrote([row], "s-alice", "b" * 64), "other bytes at the path"
    assert not _session_wrote([{**row, "action": "delete"}], "s-alice", "a" * 64)
    assert not _session_wrote([], "s-alice", "a" * 64)


# ═════════════════════════ write_artifact and share_artifact ═══════════════


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> dict[str, list]:  # noqa: F811
    """A scratch clone root, and the blob mirror and the card recorded."""
    configure_env(monkeypatch, short_tmp)
    seen: dict[str, list] = {"mirrored": [], "cards": []}

    async def mirror(rel: str, data: bytes, **_kw: Any) -> None:
        seen["mirrored"].append(rel)

    async def notify(*, artifact: dict, **_kw: Any) -> None:
        seen["cards"].append(artifact["path"])

    monkeypatch.setattr(wa, "mirror_to_blob_store", mirror)
    monkeypatch.setattr(wa, "_notify", notify)
    return seen


async def _write(path: str, body: str = "BODY", **kw: Any) -> dict:
    out = await wa.write_artifact(path, body, **kw)
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)
    return out


async def test_an_s8_document_of_a_shared_agent_lands_in_the_thread_folder(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug = thread_slug(t)
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        doc = await _write("report.md", "# The plan")
        data = await _write("inputs/data.csv", "a,b")
    assert doc["path"] == f"outputs/{slug}/report.md"
    assert doc["download_url"] == f"/api/agent/workspace/{t}/file?path=outputs/{slug}/report.md"
    assert (ws / doc["path"]).read_text(encoding="utf-8") == "# The plan"
    assert not (ws / "outputs" / "report.md").exists(), "the shared outputs/ again"
    assert data["path"] == f"inputs/{slug}/data.csv"
    # The card and the blob row carry the same path as the link.
    assert runs["cards"] == [doc["path"], data["path"]]
    assert runs["mirrored"] == [doc["path"], data["path"]]


async def test_a_document_with_no_thread_folder_writes_nothing(runs) -> None:
    with bound_run(ORG_A, agent=PA, thread="Thread With Spaces!") as ws:
        out = await _write("report.md")
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        bare = await _write("outputs")
    assert "no thread folder" in out.get("error", ""), out
    assert "names no file" in bare.get("error", ""), bare
    assert not [p for p in ws.rglob("*") if p.is_file() and p.name != ".cc-instance"]
    assert runs["cards"] == [] and runs["mirrored"] == []


async def test_a_personal_agent_writes_where_it_always_did(runs) -> None:
    from acb_skills.agent_paths import ensure_state_dir

    key = "u:alice@example.com"
    ws = ensure_state_dir(PA, key)
    with bound_run(ORG_A, agent=PA, thread=new_thread(), instance=key, workspace=str(ws)):
        doc = await _write("report.md")
        shared = await wa.share_artifact("outputs")
    assert doc["path"] == "outputs/report.md"
    assert [a["path"] for a in shared["artifacts"]] == ["outputs/report.md"]


async def test_share_artifact_shows_only_this_chats_folders(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug, other = thread_slug(t), thread_slug(new_thread())
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        for rel, body in (
            ("outputs/old-plan.md", "A COLLEAGUE'S OLD DOC"),
            (f"outputs/{other}/theirs.md", "ANOTHER THREAD"),
            ("inputs/old-upload.txt", "AN OLD UPLOAD"),
            (f"inputs/{other}/brief.txt", "A COLLEAGUE'S UPLOAD"),
            (f"outputs/{slug}/mine.md", "MINE"),
            (f"inputs/{slug}/attached.txt", "ATTACHED HERE"),
        ):
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(body, encoding="utf-8")
        outputs = await wa.share_artifact("outputs")
        everything = await wa.share_artifact(".")
        loose = await wa.share_artifact("outputs/old-plan.md")
    assert [a["path"] for a in outputs["artifacts"]] == [f"outputs/{slug}/mine.md"]
    shown = {a["path"] for a in everything["artifacts"]}
    assert {f"outputs/{slug}/mine.md", f"inputs/{slug}/attached.txt"} <= shown, shown
    assert not shown & {"outputs/old-plan.md", f"outputs/{other}/theirs.md",
                        "inputs/old-upload.txt", f"inputs/{other}/brief.txt"}, shown
    assert loose["artifacts"] == [] and "File not found" in loose["error"]


# ═════════════════════════ the file store and the mounts ═══════════════════


def _store(box: Any, b: sb.RunBinding, *, inputs: bool = True) -> Any:
    from acb_skills import sandbox_tools as st
    from acb_skills.tenant_file_store import TenantFileStore

    return TenantFileStore(
        workspace=b.workspace, outputs_rel=b.outputs_rel, run_data=b.run_data,
        guard=st._BrokerGuard(box.broker, b), member="member@example.com",
        **({"inputs_rel": b.inputs_rel} if inputs else {}),
    )


@pytest.mark.parametrize("inputs", [True, False], ids=["from-binding", "derived"])
async def test_the_file_tools_see_only_this_threads_uploads(sandbox, inputs) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    t, other = new_thread(), thread_slug(new_thread())
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        b = sb.read_run_binding()
        for rel, body in ((f"inputs/{thread_slug(t)}/mine.txt", "MINE"),
                          (f"inputs/{other}/alice.txt", "ALICE UPLOAD"),
                          ("inputs/flat.txt", "AN OLD UPLOAD")):
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(body, encoding="utf-8")
        store = _store(sandbox, b, inputs=inputs)
        assert await store.read("inputs/mine.txt") == "MINE"
        assert await store.read(f"inputs/{other}/alice.txt") is None
        assert await store.read("inputs/flat.txt") is None
        assert [e.name for e in await store.list_children("inputs")] == ["mine.txt"]
        await store.write("inputs/new.txt", "NEW")
    assert (ws / b.inputs_rel / "new.txt").read_text(encoding="utf-8") == "NEW"
    assert not (ws / "inputs" / "new.txt").exists()
    assert (f"{b.inputs_rel}/new.txt", b"NEW") in sandbox.mirrored
    assert sandbox.cards == [], "an upload folder write is no card"


async def test_a_projects_container_mounts_only_its_own_uploads_read_only(sandbox) -> None:  # noqa: F811
    t1, t2 = new_thread(), new_thread()
    for t in (t1, t2):
        with bound_run(ORG_A, agent=PA, thread=t):
            await sandbox.broker.acquire()
    with bound_run(ORG_A, agent=PA, thread=t1):
        b1 = sb.read_run_binding()
    with bound_run(ORG_A, agent=PA, thread=t2):
        b2 = sb.read_run_binding()
    run1, run2 = (mounts_of(r) for r in sandbox.docker.runs())
    assert f"type=bind,source={b1.workspace / b1.inputs_rel},target=/workspace/inputs,readonly" in run1
    assert f"type=bind,source={b2.workspace / b2.inputs_rel},target=/workspace/inputs,readonly" in run2
    assert str(b1.workspace / b1.inputs_rel) not in " ".join(run2)
    assert not any(f"source={b1.workspace / 'inputs'}," in m for m in run1 + run2), (
        "the shared inputs/ is never a mount source"
    )
    assert (b1.workspace / b1.inputs_rel).is_dir(), "the host made no folder, so Docker would"


async def test_another_target_gets_no_upload_cover(sandbox, monkeypatch) -> None:  # noqa: F811
    sandbox.set_scope(monkeypatch, "code_task:*")
    with bound_run(ORG_A, agent="agent-x", thread=new_thread()):
        await sandbox.broker.acquire()
    assert not any("/workspace/inputs" in m for m in mounts_of(sandbox.docker.runs()[0]))


# ═════════════════════════ the routes (R8) ══════════════════════════════════

from tests.unit.test_chat_write_under_rls import (  # noqa: E402,F401 — fixtures by name
    _ALICE,
    _BOB,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402,F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session  # noqa: E402
from tests.unit.test_h201_tenant_workdirs import (  # noqa: E402,F401 — fixtures by name
    _P,
    _S,
    _client,
    _run_write,
    _tenant_dir,
    disk,
)


def _files(client: Any, sid: str) -> set[str]:
    return {f["path"] for f in client.get(f"/agent/workspace/{sid}").json()["files"]}


def _history(client: Any, sid: str, path: str | None = None) -> list[dict]:
    params = {"path": path} if path else {}
    return client.get(f"/agent/workspace/{sid}/history", params=params).json()["history"]


def _absent_for(client: Any, sid: str, path: str) -> None:
    """*path* is absent for *client*'s session through every route."""
    base = f"/agent/workspace/{sid}"
    assert path not in _files(client, sid), path
    assert client.get(f"{base}/file", params={"path": path}).status_code == 404, path
    assert _history(client, sid, path) == [], path
    assert not any(h["path"] == path for h in _history(client, sid)), path
    assert client.delete(f"{base}/file", params={"path": path}).status_code == 404, path
    assert client.put(f"{base}/file", params={"path": path},
                      json={"content": "OVERWRITTEN"}).status_code == 404, path
    if path.startswith("inputs/"):
        assert client.post(f"{base}/promote", json={"path": path}).status_code == 404, path


def _seed_blob(org: str, path: str, data: bytes, *, session_id: str, actor: str,
               instance: str | None = None) -> None:
    from acb_memory import put_file
    from acb_skills.agent_paths import tenant_instance

    meta = asyncio.run(put_file(
        _S, path, data, action="create", session_id=session_id, actor=actor,
        instance=tenant_instance(org) if instance is None else instance,
        organization_id=org,
    ))
    assert meta is not None, path


@_DB_GATE
def test_alices_upload_and_document_are_invisible_to_bob(graph_as_app, disk) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    a, b = graph_as_app.org_a, graph_as_app.org_b
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    sc = _seed_session(graph_as_app, b, _CAROL, None, agent=_S)
    alice, bob, carol = (_client(_user(e, o)) for e, o in ((_ALICE, a), (_BOB, a), (_CAROL, b)))
    slug = thread_slug(sa)

    # Alice attaches a file, and her run writes an S8 document.
    name = f"brief-{uuid.uuid4().hex[:6]}.txt"
    up = alice.post(f"/agent/workspace/{sa}/upload", files={"files": (name, b"ALICE UPLOAD")})
    assert up.status_code == 200, up.text
    upload = up.json()[0]["path"]
    res, _ws, _key = _run_write(disk, a, sa, f"plan-{uuid.uuid4().hex[:6]}.md", "ALICE PLAN")
    doc = res["path"]
    assert upload == f"inputs/{slug}/{name}" and doc.startswith(f"outputs/{slug}/")
    assert res["download_url"] == f"/api/agent/workspace/{sa}/file?path={doc}"

    # Alice lists and reads both, and the card's link opens.
    assert {upload, doc} <= _files(alice, sa)
    assert alice.get(res["download_url"][len("/api"):]).text == "ALICE PLAN"
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": upload}).text == "ALICE UPLOAD"

    # Bob, same org, his own session of the same agent: absent everywhere.
    for path in (upload, doc):
        _absent_for(bob, sbob, path)
    assert not any(p.startswith((f"inputs/{slug}", f"outputs/{slug}")) for p in _files(bob, sbob))
    # The card's link names Alice's session, and Bob is not in her room.
    assert bob.get(res["download_url"][len("/api"):]).status_code == 404
    # Another org reaches nothing.
    assert carol.get(f"/agent/workspace/{sa}").json()["files"] == []
    assert carol.get(f"/agent/workspace/{sc}/file", params={"path": doc}).status_code == 404

    ws = _tenant_dir(a)
    assert (ws / upload).read_bytes() == b"ALICE UPLOAD", "a refused write changed the file"
    assert (ws / doc).read_text(encoding="utf-8") == "ALICE PLAN"
    # The fault-in never restores Alice's document for Bob.
    (ws / doc).unlink()
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": doc}).status_code == 404
    assert not (ws / doc).exists()
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": doc}).text == "ALICE PLAN"


@_DB_GATE
@pytest.mark.parametrize("instance", ["tenant", "older"])
def test_an_old_flat_document_opens_for_the_thread_that_wrote_it_only(
    graph_as_app, disk, instance,  # noqa: F811
) -> None:
    """Item 4 of H-227: an S8 document from before this change lies in the
    flat ``outputs/``. Its blob row names the session of the run that wrote
    it, under the tenant key or under the older ``''`` key (§21.15). Its old
    link opens for that session, and for no other session of the org."""
    a = graph_as_app.org_a
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    alice, bob = _client(_user(_ALICE, a)), _client(_user(_BOB, a))
    assert alice.get(f"/agent/workspace/{sa}").status_code == 200  # makes the tenant dir
    old = f"outputs/plan-{uuid.uuid4().hex[:6]}.md"
    ws = _tenant_dir(a)
    (ws / old).parent.mkdir(parents=True, exist_ok=True)
    (ws / old).write_text("ALICE OLD PLAN", encoding="utf-8")
    _seed_blob(a, old, b"ALICE OLD PLAN", session_id=sa, actor="agent",
               instance=None if instance == "tenant" else "")

    # Bob: not listed, not served, no history, and no write or delete.
    _absent_for(bob, sbob, old)
    assert (ws / old).read_text(encoding="utf-8") == "ALICE OLD PLAN"
    # Alice: the old link still opens, and the file manager lists it.
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": old}).text == "ALICE OLD PLAN"
    assert old in _files(alice, sa)
    assert [h["session_id"] for h in _history(alice, sa, old)] == (
        [sa] if instance == "tenant" else []
    ), "the history route reads the tenant key, as before"

    # With the disk copy gone, the fault-in restores it for Alice only.
    (ws / old).unlink()
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": old}).status_code == 404
    assert not (ws / old).exists(), "the fault-in wrote the file before the history check"
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": old}).text == "ALICE OLD PLAN"
    # Other bytes at the path now: the row proves nothing, also for Alice.
    (ws / old).write_text("SOMETHING ELSE", encoding="utf-8")
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": old}).status_code == 404


@_DB_GATE
def test_an_old_flat_upload_opens_for_its_thread_only(graph_as_app, disk) -> None:  # noqa: F811
    """An upload from before H-229 lies in the flat ``inputs/``, with the row
    that the old route wrote. Bob cannot read, list or promote it."""
    a = graph_as_app.org_a
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    alice, bob = _client(_user(_ALICE, a)), _client(_user(_BOB, a))
    assert alice.get(f"/agent/workspace/{sa}").status_code == 200
    old = f"inputs/brief-{uuid.uuid4().hex[:6]}.txt"
    ws = _tenant_dir(a)
    (ws / old).parent.mkdir(parents=True, exist_ok=True)
    (ws / old).write_bytes(b"ALICE OLD UPLOAD")
    _seed_blob(a, old, b"ALICE OLD UPLOAD", session_id=sa, actor="user")

    _absent_for(bob, sbob, old)
    assert (ws / old).read_bytes() == b"ALICE OLD UPLOAD"
    assert old in _files(alice, sa)
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": old}).text == "ALICE OLD UPLOAD"
    assert alice.post(f"/agent/workspace/{sa}/promote", json={"path": old}).status_code == 200


@_DB_GATE
def test_a_rewritten_marker_does_not_switch_the_loose_rule_off(graph_as_app, disk) -> None:  # noqa: F811
    """A container writes the dir it mounts. The rule takes the store key from
    the path and the caller's tenant, never from the ``.cc-instance`` marker."""
    a = graph_as_app.org_a
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    alice, bob = _client(_user(_ALICE, a)), _client(_user(_BOB, a))
    assert alice.get(f"/agent/workspace/{sa}").status_code == 200
    old = f"outputs/plan-{uuid.uuid4().hex[:6]}.md"
    ws = _tenant_dir(a)
    (ws / old).parent.mkdir(parents=True, exist_ok=True)
    (ws / old).write_text("ALICE OLD PLAN", encoding="utf-8")
    _seed_blob(a, old, b"ALICE OLD PLAN", session_id=sa, actor="agent")
    (ws / ".cc-instance").write_text(f"u:{_BOB}", encoding="utf-8")
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": old}).status_code == 404
    assert old not in _files(bob, sbob)
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": old}).text == "ALICE OLD PLAN"


@_DB_GATE
def test_a_personal_agent_keeps_its_flat_folders(graph_as_app, disk) -> None:  # noqa: F811
    """A personal agent's dir holds only its member's files, so no loose rule
    applies: a flat ``outputs/`` file is listed and served with no history."""
    from acb_skills.agent_paths import agent_state_dir

    a = graph_as_app.org_a
    sid = _seed_session(graph_as_app, a, _ALICE, None, agent=_P)
    alice = _client(_user(_ALICE, a))
    assert alice.get(f"/agent/workspace/{sid}").status_code == 200
    ws = agent_state_dir(_P, f"u:{_ALICE}")
    (ws / "outputs").mkdir(parents=True, exist_ok=True)
    (ws / "outputs" / "mine.md").write_text("MINE", encoding="utf-8")
    assert "outputs/mine.md" in _files(alice, sid)
    assert alice.get(f"/agent/workspace/{sid}/file", params={"path": "outputs/mine.md"}).text == "MINE"
    put = alice.put(f"/agent/workspace/{sid}/file", params={"path": "outputs/new.md"},
                    json={"content": "NEW"})
    assert put.status_code == 200, put.text


# ═════════════════════════ the sandbox_docker half ══════════════════════════

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_run_data_hygiene import DOCKER_ORG, real_projects  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401
    bind_mounts_work,
    docker_image,
)


@pytest.mark.sandbox_docker
async def test_docker_a_thread_sees_only_its_own_uploads(real_projects) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    broker = real_projects["broker"]
    t1, t2 = new_thread(), new_thread()
    with bound_run(DOCKER_ORG, agent=PA, thread=t1) as ws:
        # Before either container starts: Alice's upload in her thread's
        # folder, an older upload and an older S8 document in the flat folders.
        for rel, body in ((f"inputs/{thread_slug(t1)}/alice.txt", "ALICE UPLOAD"),
                          ("inputs/old-upload.txt", "AN OLD UPLOAD"),
                          ("outputs/old-plan.md", "AN OLD DOC")):
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(body, encoding="utf-8")
        h1 = await broker.acquire()
    own = await broker.exec(h1, "cat /workspace/inputs/alice.txt", 30)
    assert own.exit_code == 0 and "ALICE UPLOAD" in own.output, own.output
    wrote = await broker.exec(h1, "echo x > /workspace/inputs/planted.txt", 30)
    assert wrote.exit_code != 0, "the upload folder is writable in the container"

    with bound_run(DOCKER_ORG, agent=PA, thread=t2):
        h2 = await broker.acquire()
    listed = await broker.exec(h2, "ls -A /workspace/inputs | wc -l", 30)
    assert listed.output.strip().splitlines()[-1] == "0", listed.output
    for path in (f"/workspace/inputs/{thread_slug(t1)}/alice.txt", "/workspace/inputs/old-upload.txt",
                 "/workspace/outputs/old-plan.md"):
        read = await broker.exec(h2, f"cat {path}", 30)
        assert read.exit_code != 0 and "ALICE UPLOAD" not in read.output, (path, read.output)
    found = await broker.exec(
        h2, "find /workspace \\( -name alice.txt -o -name old-upload.txt -o -name old-plan.md \\)"
            " -print 2>/dev/null", 30,
    )
    assert found.output.strip() == "", found.output
