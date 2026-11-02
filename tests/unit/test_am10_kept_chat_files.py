"""WS-60 AM-10 — a chat's files outlive the chat (D162.8).

Spec: ``project-docs/specs/artifact_management.md`` §7.3 and §11 AM-10.
Owner, 2026-11-02: "we should be able to delete a session chat without
deleting its generated files".

With the artifact registry on, a chat delete MOVES the chat's files to the
kept folder ``outputs/.kept/<kept id>/`` and keeps their rows. Each row keeps
``session_id`` and gets ``source_title`` and ``source_deleted_at``. The one
reader of a kept file is ``GET /artifacts/{id}/content``. With the registry
off, the delete purges as before (H-227).

Two halves.

**Hermetic** (no database). No thread slug can name the kept folder, the two
copies of the kept prefix agree, every path rule refuses a kept path, the
kept id is stable for one chat row, the safe move follows no link, the
migration is found by CONTENT and is expand-only, and the content route
answers 404 with the flag off.

**R8** (the phase-4 rehearsal's catalog, as the NOBYPASSRLS ``acb_app`` role,
with the real chat, workspace and artifact routes). The done-when of AM-10:

* a chat with two outputs, one upload and one zip is deleted. Its owner reads
  all four through the content route, with the same bytes and SHA-256;
* each moved row keeps ``session_id`` and has ``source_title`` and
  ``source_deleted_at``;
* a new chat with the same id lists none of them, reads none of them and
  deletes none of them, through ``prepare_new_session`` and the run door too;
* another member and a member of another organization get 404;
* an injected failure on the disk, and one in the store, answers 503 and
  keeps the chat. A second delete finishes the move, with no second row;
* a late file of the same name moves into the same kept folder and never
  collides on ``artifacts_store_key``;
* with the registry off the delete purges as today, and the purge leaves no
  ``artifacts`` row with no bytes.

Mutations this suite catches (R7):

* ``is_other_thread_rel`` drops the kept rule: a new chat with the same id
  deletes or overwrites a kept file;
* ``session_paths`` or the purge drops ``_NOT_KEPT``: ``prepare_new_session``
  deletes the kept files;
* ``list_artifacts`` drops ``source_deleted_at IS NULL``: the new chat lists
  the old files;
* ``_sync_purge`` drops ``purge_in_session``: a row with no bytes stays;
* ``move_in_session`` drops its merge: the late file fails on the store key;
* the content route drops ``can_read``: Bob reads Alice's kept file.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_am10_kept_chat_files.py -v -rs
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"

wa = importlib.import_module("acb_skills.write_artifact")


# ═════════════════════════ hermetic ═════════════════════════════════════════


def test_no_thread_slug_can_name_the_kept_folder() -> None:
    """``instance_slug`` strips a leading dot, and the slug grammar needs a
    letter or a digit first. So no chat id gives the kept folder's name."""
    from acb_skills.agent_paths import KEPT_DIR, instance_slug, is_thread_slug, thread_slug

    assert KEPT_DIR == ".kept"
    assert not is_thread_slug(KEPT_DIR)
    assert not is_thread_slug(KEPT_DIR.upper())
    ids = [".kept", "..kept", ".kept-0000abcd", "kept", ".KEPT", "-.kept", "_.kept",
           " .kept", ".kept.", str(uuid.uuid4())]
    for raw in ids:
        assert not instance_slug(raw).startswith("."), raw
        try:
            slug = thread_slug(raw)
        except ValueError:
            continue
        assert slug.lower() != KEPT_DIR and not slug.startswith("."), raw


def test_the_two_copies_of_the_kept_prefix_agree() -> None:
    from acb_memory import blob_store
    from acb_skills import agent_paths

    assert blob_store.KEPT_PREFIX == agent_paths.KEPT_PREFIX == "outputs/.kept/"


def test_every_path_rule_refuses_a_kept_path() -> None:
    from acb_skills.agent_paths import (
        is_blocked_rel,
        is_kept_rel,
        is_other_thread_rel,
        kept_rel,
        refused_write,
        thread_slug,
    )

    kid = str(uuid.uuid4())
    own = thread_slug(str(uuid.uuid4()))
    rel = kept_rel(kid, f"outputs/{own}/plan.md", own)
    # The one layout: the slug drops out, and a loose file keeps its head.
    assert rel == f"outputs/.kept/{kid}/outputs/plan.md"
    assert kept_rel(kid, f"inputs/{own}/a/b.pdf", own) == f"outputs/.kept/{kid}/inputs/a/b.pdf"
    assert kept_rel(kid, f"outputs/{own}", own) == f"outputs/.kept/{kid}/outputs"
    assert kept_rel(kid, "outputs/plan.md", own) == f"outputs/.kept/{kid}/loose/outputs/plan.md"
    assert kept_rel(kid) == f"outputs/.kept/{kid}"
    other = thread_slug(str(uuid.uuid4()))
    assert kept_rel(kid, f"outputs/{other}/x", own) == f"outputs/.kept/{kid}/loose/outputs/{other}/x"
    assert is_kept_rel(rel) and is_kept_rel("OUTPUTS/.KEPT/x")
    assert not is_kept_rel("outputs/kept/x") and not is_kept_rel("inputs/.kept/x")
    assert is_blocked_rel(rel), "a dot dir is refused by every reader"
    for slug in (own, None):
        assert is_other_thread_rel(rel, slug), slug
    assert refused_write(Path("."), rel, member="a@x.test", own_slug=own)
    for bad in ("", "x", kid.upper(), "../" + kid, kid + "/x"):
        with pytest.raises(ValueError):
            kept_rel(bad, "outputs/a.md")
    with pytest.raises(ValueError):
        kept_rel(kid, rel)


def test_the_kept_id_is_stable_for_one_chat_row() -> None:
    from gateway.routes.workspace import kept_folder_id

    one = kept_folder_id("org-1", "chat-1", "2026-11-02T10:00:00+00:00")
    assert one == kept_folder_id("org-1", "chat-1", "2026-11-02T10:00:00+00:00")
    assert uuid.UUID(one).version == 5
    # A new chat with the same id has a new row time, so a new folder.
    assert one != kept_folder_id("org-1", "chat-1", "2026-11-02T10:00:00.000001+00:00")
    assert one != kept_folder_id("org-2", "chat-1", "2026-11-02T10:00:00+00:00")


def test_the_safe_move_replaces_makes_parents_and_follows_no_link(tmp_path: Path) -> None:
    from acb_skills import safe_open

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_bytes(b"ONE")
    assert safe_open.move_file(tmp_path, "a/f.txt", "outputs/.kept/k/a/f.txt") is True
    assert (tmp_path / "outputs/.kept/k/a/f.txt").read_bytes() == b"ONE"
    assert not (tmp_path / "a" / "f.txt").exists()
    assert safe_open.move_file(tmp_path, "a/f.txt", "outputs/.kept/k/a/f.txt") is False
    (tmp_path / "a" / "f.txt").write_bytes(b"TWO")
    assert safe_open.move_file(tmp_path, "a/f.txt", "outputs/.kept/k/a/f.txt") is True
    assert (tmp_path / "outputs/.kept/k/a/f.txt").read_bytes() == b"TWO", "a retry replaces"
    with pytest.raises(safe_open.UnsafePath):
        safe_open.move_file(tmp_path, "../x", "y")
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex[:6]}"
    outside.mkdir()
    try:
        os.symlink(outside, tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # this box cannot make a link (a Windows dev box)
    (outside / "s.txt").write_bytes(b"HOST")
    with pytest.raises(safe_open.UnsafePath):
        safe_open.move_file(tmp_path, "link/s.txt", "outputs/s.txt")
    with pytest.raises(safe_open.UnsafePath):
        safe_open.move_file(tmp_path, "outputs/.kept/k/a/f.txt", "link/f.txt")
    assert (outside / "s.txt").read_bytes() == b"HOST"
    assert not (outside / "f.txt").exists()


def _migration() -> Path:
    found = [p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
             if "ADD COLUMN IF NOT EXISTS source_deleted_at" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0]


def test_the_migration_is_found_by_content_and_is_expand_only() -> None:
    body = _migration().read_text(encoding="utf-8")
    sql = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("--"))
    assert re.search(r"ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source_title TEXT NULL;", sql)
    assert re.search(
        r"ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source_deleted_at TIMESTAMPTZ NULL;", sql)
    for word in ("DEFAULT", "DROP", "RENAME", "NOT NULL", "UPDATE ", "CREATE TABLE"):
        assert word not in sql.upper(), word
    assert "lock_timeout" in sql


async def test_the_content_route_is_dark_with_the_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_auth import UserContext
    from acb_auth.roles import UserRole
    from acb_common import get_settings
    from fastapi import HTTPException
    from gateway.routes import artifacts as routes

    monkeypatch.setattr(get_settings(), "artifact_registry_enabled", False)
    user = UserContext(email="a@x.test", role=UserRole.EMPLOYEE, organization_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as off:
        await routes.get_artifact_content(str(uuid.uuid4()), _user=user)
    assert off.value.status_code == 404


def test_the_header_name_holds_no_quote_or_line_break() -> None:
    from gateway.routes.artifacts import _header_name

    assert _header_name('a"b\r\nc\\.md') == "abc.md"
    assert _header_name('"') == "file"


# ═════════════════════════ R8 ═══════════════════════════════════════════════

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
from tests.unit.test_h201_tenant_workdirs import (  # noqa: E402
    _P,
    _S,
    _cfg,
    _Disk,
    _rows,
    _tenant_dir,
)
from tests.unit.test_h227_thread_scope import (  # noqa: E402
    _absent_for,
    _chat_and_files,
    _files,
)


@pytest.fixture
def disk(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The scratch clone root of ``test_h201_tenant_workdirs.disk``, on a SHORT
    path. A kept file sits seven parts below the state root, with three ids in
    its path. Under pytest's long temp names that passes the 260 characters of
    a Windows path. Production is Linux, where the limit is 4096."""
    import shutil
    import tempfile

    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    base = Path(tempfile.mkdtemp(prefix="am10")).resolve()
    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(base / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(base / "agents" / "custom_apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [{"name": n} for n in (_S, _P)])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    try:
        yield _Disk(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _sid() -> str:
    return f"c-{uuid.uuid4().hex[:10]}"


def _kept_path(promoted_: Any, org: str, sid: str, rel: str, created_at: str) -> str:
    """Where the delete of the chat *sid* keeps *rel* (``kept_rel``, the one layout)."""
    from acb_skills.agent_paths import kept_rel, thread_slug
    from gateway.routes.workspace import kept_folder_id

    return kept_rel(kept_folder_id(org, sid, created_at), rel, thread_slug(sid))


def _run(disk_: Any, org: str, sid: str, member: str, call: Any) -> Any:
    """Await *call()* in a run of the shared agent, bound as the executor and
    the chat route bind it: the tenant dir, the run's tenant, and the run's
    VERIFIED member (H-73), so the write registers its row."""
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator.executor import _resolve_run_workspace

    ws, key = _resolve_run_workspace(disk_.shared, _cfg(None), organization_id=org)
    run_id = f"r-{uuid.uuid4().hex[:6]}"

    async def _go() -> Any:
        token = bind_tenant(org)
        bind_run_context(run_id=run_id, thread_id=sid, agent=_S, user=member,
                         member_verified=True)
        wa.bind_artifact_context(
            session_id=sid, agent_name=_S, run_id=run_id, workspace_root=ws,
            instance=key, member=member, gateway_url="http://127.0.0.1:9",
            gateway_token="x",
        )
        try:
            out = await call()
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*rest, return_exceptions=True)
            return out
        finally:
            release_tenant(token)

    return asyncio.run(_go())


def _scoped(promoted_: Any, org: str, call: Any) -> Any:
    """Await *call()* with the async seam on the R8 catalog and *org* bound."""
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _go() -> Any:
        token = bind_tenant(org)
        try:
            async with tenant_engine_scope(promoted_.app_url.render_as_string(hide_password=False)):
                return await call()
        finally:
            release_tenant(token)

    return asyncio.run(_go())


def _content(promoted_: Any, user: Any, artifact_id: str) -> tuple[int, bytes]:
    """``GET /artifacts/{id}/content`` as *user*: the status and the bytes."""
    from fastapi import HTTPException
    from gateway.routes import artifacts as routes

    async def _call() -> tuple[int, bytes]:
        try:
            resp = await routes.get_artifact_content(artifact_id, _user=user)
        except HTTPException as exc:
            return exc.status_code, b""
        return resp.status_code, bytes(resp.body)

    return _scoped(promoted_, user.organization_id, _call)


def _listed(promoted_: Any, member: str, org: str, **kw: Any) -> set[str]:
    from acb_memory import artifacts as reg

    rows = _scoped(promoted_, org, lambda: reg.list_artifacts(member, limit=200, **kw))
    return {r["id"] for r in rows}


def _registry(promoted_: Any, org: str, session_id: str) -> list[dict]:
    with promoted_.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text AS id, path, session_id, owner_email, source_title, "
            "source_deleted_at, sha256, instance FROM artifacts "
            "WHERE organization_id = CAST(:o AS uuid) AND session_id = :s ORDER BY path"),
            {"o": org, "s": session_id}).fetchall()
    return [dict(r._mapping) for r in rows]


def _registry_at(promoted_: Any, path: str) -> list[dict]:
    with promoted_.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text AS id, sha256, version FROM artifacts WHERE path = :p"),
            {"p": path}).fetchall()
    return [dict(r._mapping) for r in rows]


def _history_at(promoted_: Any, path: str) -> list[str]:
    """The session of each history row at *path*."""
    with promoted_.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT session_id FROM agent_file_history WHERE path = :p"), {"p": path}).fetchall()
    return [str(r[0]) for r in rows]


def _chat_exists(promoted_: Any, sid: str) -> bool:
    with promoted_.admin_engine.connect() as c:
        return c.execute(text("SELECT 1 FROM chat_session WHERE id = :s"), {"s": sid}).first() is not None


def _created_at(promoted_: Any, sid: str) -> str:
    with promoted_.admin_engine.connect() as c:
        return c.execute(text("SELECT created_at FROM chat_session WHERE id = :s"),
                         {"s": sid}).scalar().isoformat()


def _chat_with_files(promoted_: Any, disk_: Any, client: Any, org: str, member: str,
                     sid: str, title: str) -> dict[str, tuple[str, bytes]]:
    """A chat of the shared agent with two outputs, one upload and one zip.

    Returns ``{label: (path, bytes)}``, the bytes read from the disk."""
    from acb_skills.package_artifacts import package_artifacts

    made = client.post("/chat/sessions", json={
        "id": sid, "agent_name": _S, "title": title, "last_preview": None,
        "message_count": 0,
    })
    assert made.status_code == 200, made.text
    up = client.post(f"/agent/workspace/{sid}/upload",
                     files={"files": (f"brief-{uuid.uuid4().hex[:6]}.txt", b"THE UPLOAD")})
    assert up.status_code == 200, up.text
    files = {"upload": up.json()[0]["path"]}
    files["plan"] = _run(disk_, org, sid, member, lambda: wa.write_artifact(
        "plan.md", "# Q3 plan" + chr(10) + "the pelican flew"))["path"]
    files["data"] = _run(disk_, org, sid, member, lambda: wa.write_artifact(
        "rows.csv", "a,b" + chr(10) + "1,2"))["path"]
    zipped = _run(disk_, org, sid, member, lambda: package_artifacts(name="bundle"))
    assert "error" not in zipped, zipped
    files["zip"] = zipped["path"]
    ws = _tenant_dir(org)
    return {k: (p, (ws / p).read_bytes()) for k, p in files.items()}


@pytest.fixture
def registry_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "artifact_registry_enabled", True)


@_DB_GATE
def test_a_deleted_chat_keeps_its_files_for_its_owner_and_for_nobody_else(  # noqa: F811
    graph_as_app, disk, registry_on,
) -> None:
    from gateway.routes.agent import _guard_run_thread

    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid = _sid()
    alice = _chat_and_files(_user(_ALICE, a))
    files = _chat_with_files(graph_as_app, disk, alice, a, _ALICE, sid, "Q3 plan")
    before = {r["path"]: r["id"] for r in _registry(graph_as_app, a, sid)}
    for label in ("upload", "plan", "data"):
        assert files[label][0] in before, (label, before)
    assert files["zip"][0] not in before, "a zip is on disk only before the delete"
    made_at = _created_at(graph_as_app, sid)

    gone = alice.delete(f"/chat/sessions/{sid}")
    assert gone.status_code == 204, gone.text
    assert not _chat_exists(graph_as_app, sid)
    ws = _tenant_dir(a)
    kept = _registry(graph_as_app, a, sid)
    assert len(kept) == 4, kept
    by_path = {r["path"]: r for r in kept}
    for row in kept:
        assert row["path"].startswith("outputs/.kept/"), row
        assert row["session_id"] == sid and row["owner_email"] == _ALICE, row
        assert row["source_title"] == "Q3 plan" and row["source_deleted_at"] is not None, row
    by_tail = {path: by_path[_kept_path(graph_as_app, a, sid, path, made_at)]
               for path, _data in files.values()}
    for label, (path, data) in files.items():
        row = by_tail[path]
        assert not (ws / path).exists(), path
        assert _rows(graph_as_app, path) == [] and _registry_at(graph_as_app, path) == [], path
        assert (ws / row["path"]).read_bytes() == data, path
        if label != "zip":
            assert row["id"] == before[path], "a moved row keeps its id"
        status, body = _content(graph_as_app, _user(_ALICE, a), row["id"])
        assert status == 200 and body == data, (label, status)
        assert hashlib.sha256(body).hexdigest() == hashlib.sha256(data).hexdigest() == row["sha256"]
        # Another member, and a member of another organization, read nothing.
        assert _content(graph_as_app, _user(_BOB, a), row["id"]) == (404, b""), label
        assert _content(graph_as_app, _user(_CAROL, b), row["id"]) == (404, b""), label
    ids = {r["id"] for r in kept}
    assert ids <= _listed(graph_as_app, _ALICE, a), "the library lists the kept files"
    assert not ids & _listed(graph_as_app, _ALICE, a, session_id=sid)

    # The run door prepares the id of a chat with no row. It deletes nothing kept.
    asyncio.run(_guard_run_thread(_user(_BOB, a), sid))
    # Alice makes a new chat with the same id. It sees none of the kept files.
    again = alice.post("/chat/sessions", json={
        "id": sid, "agent_name": _S, "title": "Again", "last_preview": None,
        "message_count": 0,
    })
    assert again.status_code == 200, again.text
    for row in kept:
        assert (ws / row["path"]).exists(), "prepare_new_session deleted a kept file"
        _absent_for(alice, sid, row["path"])
        assert (ws / row["path"]).exists(), "a session route deleted a kept file"
    listed = _files(alice, sid)
    for path, _data in files.values():
        # The old path is the new chat's own folder now, and it is empty.
        assert path not in listed, path
        got = alice.get(f"/agent/workspace/{sid}/file", params={"path": path})
        assert got.status_code in (404, 410), (path, got.status_code)
    assert not ids & _listed(graph_as_app, _ALICE, a, session_id=sid)
    assert not any(p.startswith("outputs/.kept") for p in listed)

    # Alice deletes the new chat too. Its folder is a new one, so nothing
    # collides, and the old files keep the title of their own chat.
    assert alice.delete(f"/chat/sessions/{sid}").status_code == 204
    assert all(r["source_title"] == "Q3 plan" for r in _registry(graph_as_app, a, sid))
    # Bob makes a chat with the id: he lists, reads and deletes nothing kept.
    bob = _chat_and_files(_user(_BOB, a))
    made = bob.post("/chat/sessions", json={
        "id": sid, "agent_name": _S, "title": "Bob", "last_preview": None,
        "message_count": 0,
    })
    assert made.status_code == 200, made.text
    for path, data in files.values():
        row = by_tail[path]
        _absent_for(bob, sid, row["path"])
        assert (ws / row["path"]).read_bytes() == data, path
        assert _content(graph_as_app, _user(_ALICE, a), row["id"]) == (200, data), path
        assert _content(graph_as_app, _user(_BOB, a), row["id"]) == (404, b""), path
    assert not ids & _listed(graph_as_app, _BOB, a)
    assert not ids & _listed(graph_as_app, _BOB, a, session_id=sid)
    # Each purge of the id (three doors ran) left the kept history alone too.
    for row in kept:
        assert _history_at(graph_as_app, row["path"]), row["path"]


@_DB_GATE
def test_a_failed_move_keeps_the_chat_and_a_second_delete_finishes_it(  # noqa: F811
    graph_as_app, disk, registry_on, monkeypatch,
) -> None:
    """Disk first, then the store in one transaction. A failure on either
    side answers 503 and keeps the chat with every row where it was. A second
    delete finishes the move into the same folder, with no second row."""
    from acb_memory import artifacts as reg
    from acb_skills import safe_open

    a = graph_as_app.org_a
    sid = _sid()
    alice = _chat_and_files(_user(_ALICE, a))
    files = _chat_with_files(graph_as_app, disk, alice, a, _ALICE, sid, "Board pack")
    before = {r["path"]: r["id"] for r in _registry(graph_as_app, a, sid)}
    made_at = _created_at(graph_as_app, sid)

    real_move = safe_open.move_file
    calls = {"n": 0}

    def _second_fails(root: Path, src: str, dst: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("the disk is full")
        return real_move(root, src, dst)

    def _store_fails(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the database is gone")

    for target, name, fake in ((safe_open, "move_file", _second_fails),
                               (reg, "keep_in_session", _store_fails)):
        calls["n"] = 0
        with monkeypatch.context() as m:
            m.setattr(target, name, fake)
            failed = alice.delete(f"/chat/sessions/{sid}")
        assert failed.status_code == 503, (name, failed.text)
        assert "kept" in failed.json()["detail"]
        assert _chat_exists(graph_as_app, sid), name
        rows = _registry(graph_as_app, a, sid)
        assert {r["path"]: r["id"] for r in rows} == before, (name, rows)
        assert all(r["source_deleted_at"] is None for r in rows), name
        for label in ("upload", "plan", "data"):
            assert _rows(graph_as_app, files[label][0]), (name, label)

    assert alice.delete(f"/chat/sessions/{sid}").status_code == 204
    kept = _registry(graph_as_app, a, sid)
    assert len(kept) == 4, kept
    folders = {r["path"].split("/")[2] for r in kept}
    assert len(folders) == 1, "every try moved into one kept folder"
    by_path = {r["path"]: r for r in kept}
    ws = _tenant_dir(a)
    for path, data in files.values():
        row = by_path[_kept_path(graph_as_app, a, sid, path, made_at)]
        assert (ws / row["path"]).read_bytes() == data, path
        assert len(_registry_at(graph_as_app, row["path"])) == 1
        assert _registry_at(graph_as_app, path) == [] and _rows(graph_as_app, path) == []
        if path in before:
            assert row["id"] == before[path]
        assert _content(graph_as_app, _user(_ALICE, a), row["id"]) == (200, data), path


@_DB_GATE
def test_a_late_file_of_the_same_name_joins_the_kept_folder(  # noqa: F811
    graph_as_app, disk, registry_on,
) -> None:
    """The pass after the row delete moves a late file into the SAME kept
    folder. A kept file of that name takes the newer bytes and keeps its id,
    so the move never collides on ``artifacts_store_key``."""
    from acb_memory import put_file
    from acb_memory.artifacts import Provenance
    from acb_skills.agent_paths import kept_rel, tenant_instance, thread_slug
    from gateway.routes.workspace import keep_thread_files, kept_folder_id

    a = graph_as_app.org_a
    sid = _sid()
    alice = _chat_and_files(_user(_ALICE, a))
    files = _chat_with_files(graph_as_app, disk, alice, a, _ALICE, sid, "Plans")
    kid = kept_folder_id(a, sid, _created_at(graph_as_app, sid))
    assert alice.delete(f"/chat/sessions/{sid}").status_code == 204
    plan = files["plan"][0]
    slug = thread_slug(sid)
    kept_plan = kept_rel(kid, plan, slug)
    first = _registry_at(graph_as_app, kept_plan)
    assert len(first) == 1, first

    # A run of the deleted chat writes plan.md again, and one new file.
    ws = _tenant_dir(a)
    late = f"outputs/{slug}/late.md"
    for rel, body in ((plan, b"# LATE PLAN"), (late, b"LATE")):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_bytes(body)
        meta = asyncio.run(put_file(
            _S, rel, body, action="modify", session_id=sid, actor="agent",
            instance=tenant_instance(a), organization_id=a,
            provenance=Provenance(owner_email=_ALICE, made_by=_S, session_id=sid),
        ))
        assert meta is not None and meta.artifact_id, rel
    asyncio.run(keep_thread_files(
        sid, a, owner=_ALICE, kept_id=kid, source_title="Plans", best_effort=False,
    ))
    after = _registry_at(graph_as_app, kept_plan)
    assert len(after) == 1 and after[0]["id"] == first[0]["id"], after
    assert after[0]["version"] == first[0]["version"] + 1
    assert after[0]["sha256"] == hashlib.sha256(b"# LATE PLAN").hexdigest()
    assert _content(graph_as_app, _user(_ALICE, a), first[0]["id"]) == (200, b"# LATE PLAN")
    assert (ws / kept_plan).read_bytes() == b"# LATE PLAN"
    assert len(_registry_at(graph_as_app, kept_rel(kid, late, slug))) == 1
    assert _registry_at(graph_as_app, plan) == [] and _registry_at(graph_as_app, late) == []
    assert all(r["source_title"] == "Plans" for r in _registry(graph_as_app, a, sid))


@_DB_GATE
def test_with_the_registry_off_the_delete_purges_and_leaves_no_row_with_no_bytes(  # noqa: F811
    graph_as_app, disk, registry_on, monkeypatch,
) -> None:
    """The files were registered while the flag was on. With it off the
    delete purges as today, and the purge takes their registry rows with
    their bytes (AM-10 item 5)."""
    from acb_common import get_settings

    a = graph_as_app.org_a
    sid = _sid()
    alice = _chat_and_files(_user(_ALICE, a))
    files = _chat_with_files(graph_as_app, disk, alice, a, _ALICE, sid, "Old way")
    assert len(_registry(graph_as_app, a, sid)) == 3
    monkeypatch.setattr(get_settings(), "artifact_registry_enabled", False)
    assert alice.delete(f"/chat/sessions/{sid}").status_code == 204
    ws = _tenant_dir(a)
    assert not (ws / "outputs" / ".kept").exists(), "the flag off keeps nothing"
    for path, _data in files.values():
        assert not (ws / path).exists(), path
        assert _rows(graph_as_app, path) == [], path
    assert _registry(graph_as_app, a, sid) == [], "a purge left a row with no bytes"
    with graph_as_app.admin_engine.connect() as c:
        orphans = c.execute(text(
            "SELECT count(*) FROM artifacts r WHERE r.organization_id = CAST(:o AS uuid) "
            "AND r.session_id = :s AND NOT EXISTS (SELECT 1 FROM agent_blob b "
            "WHERE b.agent_name = r.agent_name AND b.instance = r.instance "
            "AND b.path = r.path)"),
            {"o": a, "s": sid}).scalar()
    assert orphans == 0
