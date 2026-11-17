"""A file that a chat made attaches to an email (task 3598).

The owner asked on 2026-11-17: a PDF report that the chat made must go with
an email. In a real turn ``make_document`` gave
``{"path": "outputs/<chat slug>/x.pdf"}``, and ``send_email`` named it as
``artifact:orchestrator:x.pdf`` and as
``artifact:orchestrator:outputs/<chat slug>/x.pdf``. The gateway refused both
with 422 "is not a file in your own workspace". The resolver read only a
``personal`` agent's own folder (H-201 part 2). The orchestrator is a
``shared`` agent, and it writes into the TENANT dir that every member of the
org shares, one folder for each chat.

The rule now (``workspace._chat_thread_file``, called by
``send.resolve_artifact_path`` when the member's own folder has no such
file):

* the path is ``<inputs|outputs>/<thread slug>/<file>`` in the tenant dir of
  the shared agent, for the CALLER's org;
* the slug names one chat, and ``resolve_room_access`` must give the caller
  ``can_send`` in it: an owner or a member of the room;
* the file opens with no link at any depth;
* a held send takes no such file, because a held run can write its folder.

This file is hermetic: the room rows come from a fake ``rooms._load_room``,
so the REAL ``resolve_room_access`` decides each role. The R8 twin
``test_email_attach_shared_agent_r8.py`` runs the same rule on Postgres
under FORCE RLS.

Mutations this suite catches (R7):

* the room check in ``_chat_thread_file`` dropped:
  ``test_another_members_chat_is_refused`` and the role tests go red;
* the tenant root taken from the request (a field of the ref) and not from
  the verified org: ``test_another_org_is_refused_and_the_root_is_the_callers``
  goes red;
* ``chat_files`` ignored in a held send: ``test_a_held_send_takes_no_chat_file``;
* the head rule dropped (a slug folder under any head):
  ``agent-data/<own slug>/x.md`` attaches, and
  ``test_a_path_out_of_a_chat_folder_is_refused`` goes red;
* a route that drops ``organization_id=user.organization_id``:
  ``test_each_route_passes_the_verified_tenant`` goes red.

Run::

    uv run pytest tests/unit/test_email_attach_shared_agent.py -v -rs
"""
from __future__ import annotations

import ast
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway import rooms
from gateway.routes.email.transport import metorite_files as mf
from gateway.routes.email.transport import send
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F

ORG_A, ORG_B = "3598aaaa-0000-4000-8000-00000000000a", "3598bbbb-0000-4000-8000-00000000000b"
ALICE, BOB, DAN = "alice@a3598.test", "bob@a3598.test", "dan@a3598.test"
CAROL = "carol@b3598.test"
_ORCH = "agent-orchestrator"
_OTHER_SHARED = "agent-h3598s"
_EA = "email-assistant"
_REPO = Path(__file__).resolve().parents[2]


def _user(email: str, org: str | None) -> Any:
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _sid() -> str:
    return str(uuid.uuid4())


def _slug(sid: str) -> str:
    from acb_skills.agent_paths import thread_slug

    return thread_slug(sid)


class _Rooms:
    """The ``chat_session`` and ``chat_session_participant`` rows, by org.

    ``load`` stands in for ``rooms._load_room``: a row of another org reads
    as ``_ELSEWHERE``, and an id with no row as ``None``, as the bound read
    and ``chat_session_exists`` give them."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.members = {ORG_A: {ALICE, BOB, DAN}, ORG_B: {CAROL}}
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail = False

    def add(self, org: str, owner: str, parts: list[tuple[str, str]], *,
            agent: str = "orchestrator", visibility: str = "private") -> str:
        sid = _sid()
        self.rows[sid] = {"org": org, "user_id": owner, "agent": agent,
                          "visibility": visibility, "parts": parts}
        return sid

    def load(self, session_id: str, email: str, organization_id: str | None) -> Any:
        self.calls.append((session_id, email, organization_id))
        if self.fail:
            raise RuntimeError("pool is gone")
        if not organization_id:
            raise RuntimeError("TenantUnbound")
        row = self.rows.get(session_id)
        if row is None:
            return None
        if row["org"] != organization_id:
            return rooms._ELSEWHERE
        session = SimpleNamespace(
            user_id=row["user_id"], agent_name=row["agent"], visibility=row["visibility"],
            floor_mode="open", history_visibility="full")
        parts = [SimpleNamespace(subject=s, role=r, join_message_ts=None, join_stream_id=None)
                 for s, r in row["parts"]]
        return {"session": session, "participants": parts, "my_groups": set(),
                "in_org": email in self.members.get(organization_id, set())}


class _Disk:
    """A clone root with the orchestrator (shared), one more shared agent and
    the email assistant (personal), and the chats of two orgs."""

    def __init__(self, base: Path, rooms_: _Rooms) -> None:
        from gateway.routes.workspace import _tenant_agent_workspace

        self.base = base
        repos = base / "agents" / "repos"
        for name, inst in ((_ORCH, "shared"), (_OTHER_SHARED, "shared"), (_EA, "personal")):
            (repos / name).mkdir(parents=True)
            (repos / name / "config.json").write_text(
                json.dumps({"sharing": {"instancing": inst}}), encoding="utf-8")
        r = rooms_
        self.s_alice = r.add(ORG_A, ALICE, [(ALICE, "owner")])
        self.s_bob = r.add(ORG_A, BOB, [(BOB, "owner")])
        self.s_room = r.add(ORG_A, ALICE, [(ALICE, "owner"), (BOB, "member"), (DAN, "viewer")])
        self.s_org = r.add(ORG_A, ALICE, [(ALICE, "owner")], visibility="org")
        # Bob made this chat and then left it: his participant row is gone.
        self.s_left = r.add(ORG_A, BOB, [(ALICE, "owner")])
        self.s_other_agent = r.add(ORG_A, ALICE, [(ALICE, "owner")], agent="h3598s")
        self.s_norow = _sid()  # a folder whose chat has no row anywhere
        self.s_carol = r.add(ORG_B, CAROL, [(CAROL, "owner")])

        self.a = _tenant_agent_workspace(_ORCH, ORG_A)
        self.b = _tenant_agent_workspace(_ORCH, ORG_B)
        assert self.a and self.b and self.a != self.b
        self.files: dict[str, bytes] = {}
        for sid, name, data in (
            (self.s_alice, "report.pdf", b"%PDF-ALICE"),
            (self.s_bob, "bob.pdf", b"%PDF-BOB-PRIVATE"),
            (self.s_room, "room.pdf", b"%PDF-ROOM"),
            (self.s_org, "org.pdf", b"%PDF-ORG-VISIBLE"),
            (self.s_left, "left.pdf", b"%PDF-LEFT"),
            (self.s_norow, "norow.pdf", b"%PDF-NO-ROW"),
            # Planted in org A's dir under the slug of Carol's org-B chat.
            (self.s_carol, "planted.pdf", b"%PDF-ORG-A-PLANTED"),
        ):
            self._put(self.a, f"outputs/{_slug(sid)}/{name}", data)
        self._put(self.a, f"outputs/{_slug(self.s_alice)}/q3/deep.pdf", b"%PDF-DEEP")
        self._put(self.a, f"inputs/{_slug(self.s_alice)}/upload.png", b"PNG-UPLOAD")
        self._put(self.a, f"outputs/{_slug(self.s_alice)}/.env", b"SECRET=1")
        self._put(self.a, f"outputs/{_slug(self.s_alice)}/client_secret.json", b"{}")
        self._put(self.a, "outputs/loose.pdf", b"%PDF-LOOSE")
        self._put(self.a, "x.pdf", b"%PDF-ROOT")
        self._put(self.a, "agent-data/notes.md", b"NOTES")
        self._put(self.a, f"agent-data/{_slug(self.s_alice)}/x.md", b"NOT A CHAT FOLDER")
        self._put(self.a, "outputs/q3-20240101/x.pdf", b"%PDF-FAKE-SLUG")
        self._put(self.b, f"outputs/{_slug(self.s_carol)}/carol.pdf", b"%PDF-CAROL")
        other = _tenant_agent_workspace(_OTHER_SHARED, ORG_A)
        assert other is not None
        self._put(other, f"outputs/{_slug(self.s_other_agent)}/plan.pdf", b"%PDF-OTHER-AGENT")
        # The email assistant's own folder of Alice: the personal rule.
        from acb_skills.agent_paths import ensure_state_dir

        self.alice_ea = ensure_state_dir(_EA, f"u:{ALICE}")
        self._put(self.alice_ea, "outputs/quote.md", b"QUOTE")
        self._put(self.alice_ea, f"outputs/{_slug(self.s_alice)}/report.pdf", b"%PDF-PERSONAL")

    def _put(self, root: Path, rel: str, data: bytes) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.files[f"{root.name}:{rel}"] = data

    def rel(self, sid: str, name: str, head: str = "outputs") -> str:
        return f"{head}/{_slug(sid)}/{name}"


@pytest.fixture
def room_rows(monkeypatch) -> _Rooms:
    r = _Rooms()
    monkeypatch.setattr(rooms, "_load_room", r.load)
    return r


@pytest.fixture
def short() -> Any:
    """A temp dir with a short path. A chat folder holds two UUIDs, and
    pytest's own temp names pass the 260-character limit of a Windows path.
    Production is Linux."""
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="e3598")).resolve()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def disk(short, monkeypatch, room_rows) -> _Disk:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(short / "agents"))
    return _Disk(short, room_rows)


def _attach(email: str, org: str | None, path: str, agent: str | None = None,
            **kw: Any) -> bytes:
    [got] = send.load_artifact_attachments(
        [send.ArtifactAttachment(agent=agent, path=path)], email,
        organization_id=org, **kw)
    return got["content"]


def _refused(email: str, org: str | None, path: str, agent: str | None = None) -> bool:
    """True when the send refuses the file with 422, and the check agrees."""
    ref = send.ArtifactAttachment(agent=agent, path=path)
    try:
        send.load_artifact_attachments([ref], email, organization_id=org)
        sent = False
    except HTTPException as exc:
        assert exc.status_code == 422, exc.detail
        sent = True
    checked = send.refused_artifacts([ref], email, organization_id=org) == [path]
    assert sent is checked, (path, agent, email)
    return sent


async def _metorite(email: str, org: str | None, ref: str, *, held: bool = False) -> bytes:
    [f] = await mf.resolve_metorite_files(_user(email, org), [ref], held=held)
    return f.content


# ── (a) the owner of the chat attaches its file ─────────────────────────────


async def test_a_make_document_path_attaches_for_the_owner_of_the_chat(disk) -> None:
    """Both forms of the failing turn's full path, and the aliases of the
    default agent. Before the fix each one was a 422."""
    rel = disk.rel(disk.s_alice, "report.pdf")
    assert await _metorite(ALICE, ORG_A, f"artifact:orchestrator:{rel}") == b"%PDF-ALICE"
    for alias in ("orchestrator", "metorite", "default", _ORCH):
        assert _attach(ALICE, ORG_A, rel, alias) == b"%PDF-ALICE", alias
    # The bare form: Alice's own email-assistant folder has no such file at
    # this path... it has one at the report's path, so use another file.
    deep = disk.rel(disk.s_alice, "q3/deep.pdf")
    assert _attach(ALICE, ORG_A, deep) == b"%PDF-DEEP"
    assert _attach(ALICE, ORG_A, disk.rel(disk.s_alice, "upload.png", "inputs")) == b"PNG-UPLOAD"
    assert send.refused_artifacts(
        [send.ArtifactAttachment(path=deep),
         send.ArtifactAttachment(agent="orchestrator", path=rel)],
        ALICE, organization_id=ORG_A) == []


async def test_a_bare_path_takes_the_chats_own_agent(disk) -> None:
    """A ref with no agent takes the agent of the chat that its slug names,
    never a fixed one. A chat of another shared agent reads that agent's
    tenant dir."""
    rel = disk.rel(disk.s_other_agent, "plan.pdf")
    assert _attach(ALICE, ORG_A, rel) == b"%PDF-OTHER-AGENT"
    assert await _metorite(ALICE, ORG_A, f"artifact:h3598s:{rel}") == b"%PDF-OTHER-AGENT"
    # The orchestrator's tenant dir has no such file.
    assert _refused(ALICE, ORG_A, rel, "orchestrator")


# ── (b) another member's chat in the same org ───────────────────────────────


async def test_another_members_chat_is_refused(disk) -> None:
    """Mutation: drop the room check in ``_chat_thread_file`` → red."""
    alices = disk.rel(disk.s_alice, "report.pdf")
    bobs = disk.rel(disk.s_bob, "bob.pdf")
    assert _refused(BOB, ORG_A, alices, "orchestrator")
    assert _refused(BOB, ORG_A, alices)
    assert _refused(ALICE, ORG_A, bobs, "orchestrator")
    assert _refused(ALICE, ORG_A, bobs)
    with pytest.raises(HTTPException) as exc:
        await _metorite(BOB, ORG_A, f"artifact:orchestrator:{alices}")
    assert exc.value.status_code == 422
    # Each one still attaches the file of the own chat.
    assert _attach(BOB, ORG_A, bobs, "orchestrator") == b"%PDF-BOB-PRIVATE"


# ── (c) another org: the root is the caller's own ───────────────────────────


async def test_another_org_is_refused_and_the_root_is_the_callers(
    disk, monkeypatch,
) -> None:
    """Carol is in org B. Org A's chats are not hers, and her own chat reads
    org B's tenant dir, never org A's, whatever the request names.
    Mutation: take the root from a field of the ref → red."""
    from gateway.routes import workspace

    seen: list[str | None] = []
    real = workspace._tenant_agent_workspace

    def spy(agent: str, organization_id: str | None) -> Path | None:
        seen.append(organization_id)
        return real(agent, organization_id)

    monkeypatch.setattr(workspace, "_tenant_agent_workspace", spy)
    assert _refused(CAROL, ORG_B, disk.rel(disk.s_alice, "report.pdf"), "orchestrator")
    # Her own chat: org B's file, and never the file planted in org A's dir.
    planted = disk.rel(disk.s_carol, "planted.pdf")
    assert _refused(CAROL, ORG_B, planted, "orchestrator")
    assert _attach(CAROL, ORG_B, disk.rel(disk.s_carol, "carol.pdf")) == b"%PDF-CAROL"
    # A request that names a tenant changes nothing: the model has no such
    # field, and the root comes from the session's org.
    ref = send.ArtifactAttachment.model_validate(
        {"agent": "orchestrator", "path": planted, "organization_id": ORG_A,
         "org": ORG_A, "tenant": ORG_A})
    assert not any(k in send.ArtifactAttachment.model_fields
                   for k in ("organization_id", "org", "tenant"))
    with pytest.raises(HTTPException) as exc:
        send.load_artifact_attachments([ref], CAROL, organization_id=ORG_B)
    assert exc.value.status_code == 422
    assert seen and set(seen) == {ORG_B}, seen
    with pytest.raises(ValidationError):
        mf.MetoriteFilesRequest(refs=[], artifacts=[], organization_id=ORG_A)
    # Org A's member reads org A's planted file only through a chat of org A,
    # and that slug is Carol's chat of org B: no row in org A, so refused.
    assert _refused(ALICE, ORG_A, planted, "orchestrator")


def test_no_tenant_reads_no_chat_file(disk, room_rows) -> None:
    """A caller with no org, or with no email, gets no chat file, and no
    room lookup runs."""
    rel = disk.rel(disk.s_alice, "report.pdf")
    assert _refused(ALICE, None, rel, "orchestrator")
    assert _refused("", ORG_A, rel, "orchestrator")
    assert _refused("default", ORG_A, rel, "orchestrator")
    assert room_rows.calls == []


# ── (d) the role in the room ────────────────────────────────────────────────


def test_a_room_member_attaches_and_a_viewer_does_not(disk) -> None:
    room = disk.rel(disk.s_room, "room.pdf")
    assert _attach(ALICE, ORG_A, room, "orchestrator") == b"%PDF-ROOM"   # owner
    assert _attach(BOB, ORG_A, room, "orchestrator") == b"%PDF-ROOM"     # member
    assert _refused(DAN, ORG_A, room, "orchestrator")                    # viewer
    assert _refused(DAN, ORG_A, room)


def test_a_reader_a_leaver_and_a_chat_with_no_row_are_refused(disk) -> None:
    """An org chat gives Bob read and no send. Bob left the chat that he
    made. A folder whose chat has no row belongs to no one."""
    assert _refused(BOB, ORG_A, disk.rel(disk.s_org, "org.pdf"), "orchestrator")
    assert _refused(BOB, ORG_A, disk.rel(disk.s_left, "left.pdf"), "orchestrator")
    assert _attach(ALICE, ORG_A, disk.rel(disk.s_left, "left.pdf")) == b"%PDF-LEFT"
    for email in (ALICE, BOB):
        assert _refused(email, ORG_A, disk.rel(disk.s_norow, "norow.pdf"), "orchestrator")


def test_a_failed_room_lookup_answers_503(disk, room_rows) -> None:
    """An outage is not a refusal: 503, so the tool says "try again" and no
    card shows. The check raises it too."""
    room_rows.fail = True
    ref = send.ArtifactAttachment(agent="orchestrator", path=disk.rel(disk.s_alice, "report.pdf"))
    for call in (lambda: send.load_artifact_attachments([ref], ALICE, organization_id=ORG_A),
                 lambda: send.refused_artifacts([ref], ALICE, organization_id=ORG_A)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503


# ── (e) paths that leave a chat folder ──────────────────────────────────────


def _bad_paths(disk: _Disk) -> list[str]:
    """Each file here is only in the tenant dir, never in Alice's own
    folder, so only the chat rule can decide it."""
    a, b = _slug(disk.s_alice), _slug(disk.s_bob)
    return [
        f"outputs/{a}/../{b}/bob.pdf",                 # climbs into Bob's folder
        "\\".join(["outputs", a, "..", b, "bob.pdf"]),  # the same, with backslashes
        f"outputs/{a}/./q3/deep.pdf",
        f"/outputs/{a}/q3/deep.pdf",                   # absolute
        (disk.a / "outputs" / a / "q3" / "deep.pdf").as_posix(),
        "outputs/loose.pdf",                           # in no chat folder
        "x.pdf",                                       # the root form of the turn
        "agent-data/notes.md",
        f"agent-data/{a}/x.md",                        # a slug, but no chat head
        f"outputs/{a}",                                # a folder
        f"outputs/{a}/",
        f"outputs/{a}/.env",                           # a secret name
        f"outputs/{a}/client_secret.json",
        f"OUTPUTS/{a}/q3/deep.pdf",                    # a case-blind fold
        f"outputs/{a}./q3/deep.pdf",
        "outputs/q3-20240101/x.pdf",                   # looks like a slug, is not
        f"outputs/{a}/missing.pdf",
        "",
    ]


def test_a_path_out_of_a_chat_folder_is_refused(disk) -> None:
    """Mutation: drop the head rule (``parts[0] in THREAD_HEADS``) →
    ``agent-data/<own slug>/x.md`` attaches → red."""
    for path in _bad_paths(disk):
        for agent in ("orchestrator", None):
            assert _refused(ALICE, ORG_A, path, agent), (path, agent)


def test_no_disk_change_and_no_lookup_for_a_path_of_no_chat(disk, room_rows) -> None:
    """A path that names no chat folder stops before the room lookup and
    before ``ensure_state_dir``, so a refused ref makes no tenant dir."""
    from acb_skills.agent_paths import agent_state_dir, tenant_instance

    never = disk.base / "agents" / "repos" / "agent-h3598n"
    never.mkdir()
    (never / "config.json").write_text(json.dumps({"sharing": {"instancing": "shared"}}),
                                       encoding="utf-8")
    for path in ("outputs/loose.pdf", "agent-data/x.md", f"outputs/{_slug(disk.s_bob)}/../x"):
        assert _refused(ALICE, ORG_A, path, "h3598n")
    assert room_rows.calls == []
    assert not agent_state_dir("agent-h3598n", tenant_instance(ORG_A)).exists()


def _link(target: Path, at: Path, *, is_dir: bool = False) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, at, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this machine cannot make a link: {exc}")


def test_a_link_out_of_the_chat_folder_is_refused(disk) -> None:
    """A container can write a chat folder, so a link in it is refused at
    any depth: to a sibling chat, to a file outside, and through a linked
    folder."""
    a, b = _slug(disk.s_alice), _slug(disk.s_bob)
    outside = disk.base / "outside.pdf"
    outside.write_bytes(b"HOST FILE")
    _link(disk.a / "outputs" / b / "bob.pdf", disk.a / "outputs" / a / "to-bob.pdf")
    _link(outside, disk.a / "outputs" / a / "to-host.pdf")
    _link(disk.a / "outputs" / b, disk.a / "outputs" / a / "bobdir", is_dir=True)
    for name in ("to-bob.pdf", "to-host.pdf", "bobdir/bob.pdf"):
        assert _refused(ALICE, ORG_A, f"outputs/{a}/{name}", "orchestrator"), name


# ── held: a held run can write the folder of its own chat ───────────────────


async def test_a_held_send_takes_no_chat_file(disk) -> None:
    """The held card resolves with ``held=True``. The answer is today's 422,
    word for word. Mutation: ignore ``chat_files`` → red."""
    ref = f"artifact:orchestrator:{disk.rel(disk.s_alice, 'report.pdf')}"
    assert await _metorite(ALICE, ORG_A, ref) == b"%PDF-ALICE"
    with pytest.raises(HTTPException) as exc:
        await _metorite(ALICE, ORG_A, ref, held=True)
    assert exc.value.status_code == 422
    assert "is not a file in your own workspace" in str(exc.value.detail)
    with pytest.raises(HTTPException):
        _attach(ALICE, ORG_A, disk.rel(disk.s_alice, "q3/deep.pdf"), chat_files=False)


# ── (f) a personal agent keeps its rule ─────────────────────────────────────


def test_a_personal_agent_keeps_its_own_rule(disk, room_rows) -> None:
    """The member's own folder comes first, with no room lookup. A ref that
    names a personal agent never falls to a tenant dir."""
    assert _attach(ALICE, ORG_A, "outputs/quote.md") == b"QUOTE"
    rel = disk.rel(disk.s_alice, "report.pdf")
    assert _attach(ALICE, ORG_A, rel) == b"%PDF-PERSONAL"
    assert _attach(ALICE, ORG_A, rel, _EA) == b"%PDF-PERSONAL"
    assert room_rows.calls == []
    deep = disk.rel(disk.s_alice, "q3/deep.pdf")
    assert _refused(ALICE, ORG_A, deep, _EA)
    assert _refused(BOB, ORG_A, "outputs/quote.md")
    assert room_rows.calls == []


# ── (g) the check and the send refuse the same files ────────────────────────


def test_the_check_and_the_send_refuse_the_same_files(disk) -> None:
    """``_refused`` asserts that the send and the check agree on each case."""
    cases = [
        (ALICE, disk.rel(disk.s_alice, "report.pdf"), "orchestrator", False),
        (ALICE, disk.rel(disk.s_alice, "q3/deep.pdf"), None, False),
        (BOB, disk.rel(disk.s_room, "room.pdf"), None, False),
        (BOB, disk.rel(disk.s_alice, "report.pdf"), "orchestrator", True),
        (DAN, disk.rel(disk.s_room, "room.pdf"), None, True),
        (CAROL, disk.rel(disk.s_alice, "report.pdf"), None, True),
        (ALICE, "outputs/loose.pdf", "orchestrator", True),
        (ALICE, f"outputs/{_slug(disk.s_alice)}/../{_slug(disk.s_bob)}/bob.pdf", None, True),
    ]
    for email, path, agent, bad in cases:
        org = ORG_B if email == CAROL else ORG_A
        assert _refused(email, org, path, agent) is bad, (email, path, agent)


# ── the routes pass the verified tenant ─────────────────────────────────────


_CALLERS = {
    "apps/services/gateway/gateway/routes/email/transport/send.py",
    "apps/services/gateway/gateway/routes/email/transport/metorite_files.py",
    "apps/services/gateway/gateway/routes/email/automation/drafting.py",
}
_RESOLVERS = {"resolve_artifact_path", "refused_artifacts", "load_artifact_attachments",
              "_resolve_draft_attachments", "_resolve_artifact", "_chat_thread_file"}


def _callee(call: ast.Call) -> tuple[str, ast.Call]:
    """The resolver that *call* runs, directly or through ``asyncio.to_thread``."""
    fn = call.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    if name == "to_thread" and call.args:
        inner = call.args[0]
        return getattr(inner, "id", getattr(inner, "attr", "")), call
    return name, call


def test_each_route_passes_the_verified_tenant() -> None:
    """Each call of the resolver passes the tenant: ``user.organization_id``
    in a route, or the ``organization_id`` parameter in a helper. Mutation:
    drop it at one call → red."""
    found = 0
    for rel in sorted(_CALLERS):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name, call = _callee(node)
            if name not in _RESOLVERS or name in ("_resolve_artifact", "_chat_thread_file"):
                continue
            found += 1
            org = [k.value for k in call.keywords if k.arg == "organization_id"]
            assert org, f"{rel}:{node.lineno} {name} passes no organization_id"
            src = ast.unparse(org[0])
            assert src in ("user.organization_id", "organization_id"), (
                f"{rel}:{node.lineno} {name} takes the tenant from {src}")
    assert found >= 6, found


# ── the agent tool: the failing turn, end to end ────────────────────────────


agents = F.load_email_agent("ea_attach_shared_agent")


@pytest.fixture
def tool(monkeypatch, disk) -> dict[str, Any]:
    """``send_email`` on the plain card, with ``_post`` wired to the REAL
    gateway routes: the check route and ``POST /email/send``. Only the
    mailbox session and the provider are stand-ins."""
    alice = _user(ALICE, ORG_A)
    sent: dict[str, Any] = {"attachments": None, "posts": []}

    class _Provider:
        last_refused = ()

        async def send_message(self, **kw: Any) -> str:
            sent["attachments"] = kw.get("attachments")
            return "m-3598"

    @asynccontextmanager
    async def _session() -> AsyncIterator[Any]:
        yield SimpleNamespace()

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_Provider())

    async def _no_signature(*_a: Any, **_k: Any) -> str:
        return ""

    from gateway.routes.email import signature

    monkeypatch.setattr(send, "_tenant_session", _session)
    monkeypatch.setattr(send, "provider_session", _provider_session)
    monkeypatch.setattr(signature, "resolve_signature_for_send", _no_signature)

    async def post(path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        sent["posts"].append(path)
        try:
            if path == "/email/metorite-files/resolve":
                return await mf.resolve_metorite_files_route(
                    mf.MetoriteFilesRequest(**(body or {})), user=alice)
            if path == "/email/send":
                from fastapi import BackgroundTasks

                return await send.send_email(
                    send.SendEmailRequest(**(body or {})), BackgroundTasks(), user=alice)
        except HTTPException as exc:
            raise agents.GatewayError(f"Email POST failed ({exc.status_code})",
                                      exc.status_code, detail=exc.detail) from None
        raise AssertionError(f"unexpected POST {path}")

    async def name(_aid: str) -> str:
        return f"Work · {ALICE}"

    async def no_note(*_a: Any) -> str:
        return ""

    async def no_rule(_aid: str, to: list[str], cc: list[str], _bcc: Any) -> Any:
        return cc, []

    async def approve(**_kw: Any) -> bool:
        return True

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: ALICE)
    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", no_note)
    monkeypatch.setattr(agents, "_always_copy_cc", no_rule)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", approve)
    return sent


@pytest.mark.parametrize("form", ["artifact", "bare"])
async def test_the_failing_turn_now_sends_the_file(tool, disk, form) -> None:
    """The turn of 2026-11-17: ``make_document`` gave the path, and
    ``send_email`` named it. The file goes, and no 422 text comes back."""
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    name = f"artifact:orchestrator:{rel}" if form == "artifact" else rel
    out = await agents.send_email(F.BOX, body="The report is attached.", to=[F.KNOWN],
                                  subject="Report", attachments=[name])
    assert out.startswith("Sent email to"), out
    assert "not a file in your own workspace" not in out
    assert "/email/send" in tool["posts"]
    [att] = tool["attachments"]
    assert att["content"] == b"%PDF-DEEP" and att["filename"] == "deep.pdf"


async def test_the_root_name_of_the_turn_stays_refused(tool, disk) -> None:
    """``artifact:orchestrator:x.pdf`` names no chat folder, so no chat owns
    it. It stays refused, before any card and any send."""
    out = await agents.send_email(F.BOX, body="The report is attached.", to=[F.KNOWN],
                                  subject="Report", attachments=["artifact:orchestrator:x.pdf"])
    assert out.startswith("Not sent."), out
    assert "/email/send" not in tool["posts"] and tool["attachments"] is None


async def test_a_colleagues_chat_file_does_not_go(tool, disk) -> None:
    rel = disk.rel(disk.s_bob, "bob.pdf")
    for name in (f"artifact:orchestrator:{rel}", rel):
        out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                      attachments=[name])
        assert out.startswith("Not sent."), out
    assert "/email/send" not in tool["posts"] and tool["attachments"] is None
