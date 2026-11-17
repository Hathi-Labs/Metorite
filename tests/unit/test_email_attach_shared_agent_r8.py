"""R8 twin of ``test_email_attach_shared_agent.py`` (task 3598).

A file that a chat of a shared agent made (``make_document``) attaches to an
email only when the sender may write to that chat. The rule is
``workspace._chat_thread_file``, and its room check is the REAL
``rooms.resolve_room_access``. Here it runs on Postgres with FORCE ROW LEVEL
SECURITY, as the NOSUPERUSER NOBYPASSRLS app role of the H3 rehearsal, with
two orgs. A hermetic fake agrees with any SQL, so only this file proves that
the room read binds the tenant (R8).

The cases:

* Alice's own chat attaches, by ``artifact:orchestrator:<path>`` and by the
  bare path;
* Bob's private chat in the same org does not attach for Alice, nor hers for
  him;
* Carol in org B reads none of org A's chats, and her own chat reads org B's
  tenant dir;
* in a room, a ``member`` attaches and a ``viewer`` does not;
* a member who left the room (the leave route deletes the participant row)
  no longer attaches;
* a reader of an org-visible chat, who has no participant row, does not
  attach;
* a folder whose chat has no row attaches for no one.

Mutation this suite catches (R7): drop the room check in
``_chat_thread_file``. The colleague, viewer, leaver, reader and no-row cases
go red.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<your db> \\
        uv run pytest tests/unit/test_email_attach_shared_agent_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from sqlalchemy import text

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _BOB,
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

pytestmark = _DB_GATE

_DAN = "dan@s15-a.test"
_ORCH = "agent-orchestrator"


def _slug(sid: str) -> str:
    from acb_skills.agent_paths import thread_slug

    return thread_slug(sid)


def _seed_chat(promoted, org: str, owner: str, parts: list[tuple[str, str]],  # noqa: F811
               *, visibility: str = "private") -> str:
    """A ``chat_session`` row and its participant rows, as the superuser."""
    sid = str(uuid.uuid4())
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, visibility, organization_id) "
            "VALUES (:s, :u, 'orchestrator', :v, CAST(:o AS uuid))"),
            {"s": sid, "u": owner, "v": visibility, "o": org})
        for subject, role in parts:
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subj, :r, CAST(:o AS uuid))"),
                {"s": sid, "subj": subject, "r": role, "o": org})
    return sid


class _World:
    """Two orgs of chats on Postgres, and their files in the tenant dirs."""

    def __init__(self, promoted, base: Path) -> None:  # noqa: F811
        from gateway.routes.workspace import _tenant_agent_workspace

        repo = base / "agents" / "repos" / _ORCH
        repo.mkdir(parents=True)
        (repo / "config.json").write_text(
            json.dumps({"sharing": {"instancing": "shared"}}), encoding="utf-8")
        a, b = promoted.org_a, promoted.org_b
        self.promoted = promoted
        self.s_alice = _seed_chat(promoted, a, _ALICE, [(_ALICE, "owner")])
        self.s_bob = _seed_chat(promoted, a, _BOB, [(_BOB, "owner")])
        self.s_room = _seed_chat(promoted, a, _ALICE,
                                 [(_ALICE, "owner"), (_BOB, "member"), (_DAN, "viewer")])
        self.s_org = _seed_chat(promoted, a, _ALICE, [(_ALICE, "owner")], visibility="org")
        self.s_carol = _seed_chat(promoted, b, _CAROL, [(_CAROL, "owner")])
        self.s_norow = str(uuid.uuid4())
        self.dir_a = _tenant_agent_workspace(_ORCH, a)
        self.dir_b = _tenant_agent_workspace(_ORCH, b)
        assert self.dir_a and self.dir_b
        for sid, name in ((self.s_alice, "alice.pdf"), (self.s_bob, "bob.pdf"),
                          (self.s_room, "room.pdf"), (self.s_org, "org.pdf"),
                          (self.s_norow, "norow.pdf"), (self.s_carol, "planted.pdf")):
            self._put(self.dir_a, self.rel(sid, name), f"A:{name}".encode())
        self._put(self.dir_b, self.rel(self.s_carol, "carol.pdf"), b"B:carol.pdf")

    @staticmethod
    def rel(sid: str, name: str) -> str:
        return f"outputs/{_slug(sid)}/{name}"

    @staticmethod
    def _put(root: Path, rel: str, data: bytes) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@pytest.fixture
def short() -> Any:
    """A short temp dir: a chat folder holds two UUIDs, and the 260-character
    limit of a Windows path is near."""
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="e3598")).resolve()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def world(graph_as_app, short, monkeypatch) -> _World:  # noqa: F811
    from acb_common import get_settings

    with graph_as_app.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', :o) ON CONFLICT DO NOTHING"),
            {"e": _DAN, "o": graph_as_app.org_a})
    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(short / "agents"))
    return _World(graph_as_app, short)


def _org_of(email: str, w: _World) -> str:
    return w.promoted.org_b if email == _CAROL else w.promoted.org_a


def _get(w: _World, email: str, path: str, agent: str | None = "orchestrator") -> bytes | None:
    """The bytes that a send attaches, or ``None`` when it answers 422. The
    check before a card must agree."""
    from gateway.routes.email.transport import send

    org = _org_of(email, w)
    ref = send.ArtifactAttachment(agent=agent, path=path)
    checked = send.refused_artifacts([ref], email, organization_id=org)
    try:
        [got] = send.load_artifact_attachments([ref], email, organization_id=org)
    except HTTPException as exc:
        assert exc.status_code == 422, exc.detail
        assert checked == [path]
        return None
    assert checked == []
    return got["content"]


def test_own_chat_attaches_and_a_colleagues_does_not(world: _World) -> None:
    w = world
    mine = w.rel(w.s_alice, "alice.pdf")
    assert _get(w, _ALICE, mine) == b"A:alice.pdf"
    assert _get(w, _ALICE, mine, None) == b"A:alice.pdf"   # the bare path
    assert _get(w, _BOB, mine) is None
    assert _get(w, _BOB, mine, None) is None
    assert _get(w, _ALICE, w.rel(w.s_bob, "bob.pdf")) is None
    assert _get(w, _BOB, w.rel(w.s_bob, "bob.pdf")) == b"A:bob.pdf"


def test_another_orgs_chat_is_refused(world: _World) -> None:
    """Carol is in org B. The read is bound to her tenant, so org A's rows are
    not there, and her own chat reads org B's dir, never org A's."""
    w = world
    assert _get(w, _CAROL, w.rel(w.s_alice, "alice.pdf")) is None
    assert _get(w, _CAROL, w.rel(w.s_carol, "planted.pdf")) is None
    assert _get(w, _CAROL, w.rel(w.s_carol, "carol.pdf")) == b"B:carol.pdf"
    # Org A's member names Carol's chat: it exists in another tenant.
    assert _get(w, _ALICE, w.rel(w.s_carol, "planted.pdf")) is None


def test_a_room_member_attaches_and_a_viewer_does_not(world: _World) -> None:
    w = world
    room = w.rel(w.s_room, "room.pdf")
    assert _get(w, _ALICE, room) == b"A:room.pdf"
    assert _get(w, _BOB, room) == b"A:room.pdf"
    assert _get(w, _DAN, room) is None
    assert _get(w, _DAN, room, None) is None


def test_a_member_who_left_is_refused(world: _World) -> None:
    """The leave route deletes the participant row (``routes/rooms.py``)."""
    w = world
    room = w.rel(w.s_room, "room.pdf")
    assert _get(w, _BOB, room) == b"A:room.pdf"
    with w.promoted.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM chat_session_participant WHERE session_id = :s AND subject = :u"),
            {"s": w.s_room, "u": _BOB})
    assert _get(w, _BOB, room) is None


def test_an_org_chat_reader_and_a_chat_with_no_row_are_refused(world: _World) -> None:
    w = world
    assert _get(w, _ALICE, w.rel(w.s_org, "org.pdf")) == b"A:org.pdf"
    assert _get(w, _BOB, w.rel(w.s_org, "org.pdf")) is None
    for email in (_ALICE, _BOB):
        assert _get(w, email, w.rel(w.s_norow, "norow.pdf")) is None


def test_the_check_route_answers_as_the_member(world: _World) -> None:
    """``POST /email/metorite-files/resolve`` names the files of the card and
    each path that the send refuses, as the member."""
    from gateway.routes.email.transport import metorite_files as mf
    from gateway.routes.email.transport import send

    w = world
    mine, bobs = w.rel(w.s_alice, "alice.pdf"), w.rel(w.s_bob, "bob.pdf")
    req = mf.MetoriteFilesRequest(
        refs=[f"artifact:orchestrator:{mine}"],
        artifacts=[send.ArtifactAttachment(path=mine), send.ArtifactAttachment(path=bobs)])
    out = asyncio.run(mf.resolve_metorite_files_route(req, user=_user(_ALICE, w.promoted.org_a)))
    assert out["refused"] == [bobs]
    [f] = out["files"]
    assert f["name"] == "alice.pdf" and f["size"] == len(b"A:alice.pdf")
    bad = mf.MetoriteFilesRequest(refs=[f"artifact:orchestrator:{bobs}"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mf.resolve_metorite_files_route(bad, user=_user(_ALICE, w.promoted.org_a)))
    assert exc.value.status_code == 422
