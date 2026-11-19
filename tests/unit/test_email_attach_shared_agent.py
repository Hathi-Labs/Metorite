"""A file that a chat made attaches to an email, by an artifact ref (task 3598).

The owner asked on 2026-11-17: a PDF report that the chat made must go with
an email. In a real turn ``make_document`` gave
``{"path": "outputs/<chat slug>/x.pdf"}``, and ``send_email`` named it as
``artifact:orchestrator:x.pdf`` and as
``artifact:orchestrator:outputs/<chat slug>/x.pdf``. The gateway refused both
with 422 "is not a file in your own workspace". The resolver read only a
``personal`` agent's own folder (H-201 part 2). The orchestrator is a
``shared`` agent, and it writes into the TENANT dir that every member of the
org shares, one folder for each chat.

The rule now (``workspace._chat_thread_file``):

* a chat file goes ONLY as an ``artifact:<agent>:<path>`` ref
  (``metorite_files._artifact``). Only that ref pins a digest of the bytes
  that the card showed (H-392), so a file that a room member or a container
  changes after the card sends nothing (review P1 of 209adc111);
* the bare path and the ``<agent>:<path>`` form refuse a chat file, as
  before this task;
* the path is ``<inputs|outputs>/<thread slug>/<file>`` in the tenant dir of
  the shared agent, for the CALLER's org;
* the slug names one chat, and ``resolve_room_access`` must give the caller
  ``can_send`` in it: an owner or a member of the room;
* the file opens with no link at any depth, and up to the cap of a send;
* a held send takes no chat file, because a held run can write its folder.

This file is hermetic: the room rows come from a fake ``rooms._load_room``,
so the REAL ``resolve_room_access`` decides each role. The R8 twin
``test_email_attach_shared_agent_r8.py`` runs the same rule on Postgres
under FORCE RLS.

Mutations this suite catches (R7):

* the room check in ``_chat_thread_file`` dropped:
  ``test_another_members_chat_is_refused`` and the role tests go red;
* the tenant root found from the request path and not from the verified
  org: ``test_another_org_is_refused_and_the_root_is_the_callers`` goes red;
* ``chat_files`` ignored in a held send: ``test_a_held_send_takes_no_chat_file``;
* the head rule dropped (a slug folder under any head):
  ``test_a_path_out_of_a_chat_folder_is_refused`` goes red;
* the send route turns ``chat_files`` on for ``artifacts``:
  ``test_the_bare_and_agent_path_forms_refuse_a_chat_file`` and
  ``test_only_the_artifact_door_takes_a_chat_file`` go red;
* a call that passes ``**{"chat_files": True}``:
  ``test_only_the_artifact_door_takes_a_chat_file`` goes red;
* the email tool drops its rewrite of a bare chat path
  (``agents._chat_file_refs``):
  ``test_a_bare_make_document_path_sends_through_the_digest_door`` goes red;
* the rewrite takes the agent from ``agent_name``, which a pack call sets to
  the owner of the pack: the ``test_a_pack_call_*`` tests go red;
* the rewrite reads the run's own working dir, not the chat at the top:
  ``test_a_delegated_run_names_the_agent_of_the_chat_at_the_top`` goes red;
* the rewrite runs before the held branch:
  ``test_a_held_chat_gets_no_rewrite_and_no_chat_file`` goes red.

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
from fastapi import BackgroundTasks, HTTPException
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
_GATEWAY = _REPO / "apps/services/gateway/gateway"


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

    @staticmethod
    def _put(root: Path, rel: str, data: bytes) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    @staticmethod
    def rel(sid: str, name: str, head: str = "outputs") -> str:
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


def _attach(email: str, org: str | None, path: str, agent: str = "orchestrator") -> bytes:
    """The bytes of the chat door, as ``metorite_files._artifact`` calls it."""
    [got] = send.load_artifact_attachments(
        [send.ArtifactAttachment(agent=agent, path=path)], email,
        organization_id=org, chat_files=True)
    return got["content"]


def _refused(email: str, org: str | None, path: str, agent: str = "orchestrator") -> bool:
    """True when the chat door refuses the file with 422. The read and the
    path rule must agree."""
    ref = send.ArtifactAttachment(agent=agent, path=path)

    def status(call: Any) -> int:
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 422, exc.detail
            return 422
        return 200

    sent = status(lambda: send.load_artifact_attachments(
        [ref], email, organization_id=org, chat_files=True))
    checked = status(lambda: send.resolve_artifact_path(
        ref, email, organization_id=org, chat_files=True))
    assert sent == checked, (path, agent, email)
    return sent == 422


async def _metorite(email: str, org: str | None, ref: str, *, held: bool = False) -> bytes:
    [f] = await mf.resolve_metorite_files(_user(email, org), [ref], held=held)
    return f.content


@pytest.fixture
def routes(monkeypatch) -> dict[str, Any]:
    """The REAL ``POST /email/send`` route function. Only the mailbox session,
    the provider and the signature are stand-ins. ``sent`` records each call
    of the provider."""
    sent: dict[str, Any] = {"calls": []}

    class _Provider:
        last_refused = ()

        async def send_message(self, **kw: Any) -> str:
            sent["calls"].append(kw.get("attachments"))
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
    return sent


async def _send_route(user: Any, **fields: Any) -> Any:
    req = send.SendEmailRequest(account_id=F.BOX, to=[F.KNOWN], subject="Report",
                                body_text="The report is attached.", **fields)
    return await send.send_email(req, BackgroundTasks(), user=user)


# ── (a) the owner of the chat attaches its file by an artifact ref ──────────


async def test_a_make_document_path_attaches_by_an_artifact_ref(disk) -> None:
    """The failing turn's full path, as an ``artifact:`` ref, and the aliases
    of the default agent. Before the fix each one was a 422."""
    rel = disk.rel(disk.s_alice, "report.pdf")
    assert await _metorite(ALICE, ORG_A, f"artifact:orchestrator:{rel}") == b"%PDF-ALICE"
    deep = disk.rel(disk.s_alice, "q3/deep.pdf")
    assert await _metorite(ALICE, ORG_A, f"artifact:orchestrator:{deep}") == b"%PDF-DEEP"
    up = disk.rel(disk.s_alice, "upload.png", "inputs")
    assert await _metorite(ALICE, ORG_A, f"artifact:metorite:{up}") == b"PNG-UPLOAD"
    for alias in ("orchestrator", "metorite", "default", _ORCH):
        assert _attach(ALICE, ORG_A, rel, alias) == b"%PDF-ALICE", alias
    # A chat of another shared agent reads that agent's tenant dir.
    plan = disk.rel(disk.s_other_agent, "plan.pdf")
    assert await _metorite(ALICE, ORG_A, f"artifact:h3598s:{plan}") == b"%PDF-OTHER-AGENT"
    assert _refused(ALICE, ORG_A, plan, "orchestrator")


# ── (2) the bare and the agent:path forms refuse a chat file ────────────────


async def test_the_bare_and_agent_path_forms_refuse_a_chat_file(disk, routes) -> None:
    """Review P1 of 209adc111: these forms carry no digest. So the check
    before a card, the check route and the REAL send route each refuse a
    chat file, and the provider is never called. Mutation: the send route
    turns ``chat_files`` on for ``artifacts`` → red."""
    alice = _user(ALICE, ORG_A)
    for path in (disk.rel(disk.s_alice, "q3/deep.pdf"),
                 disk.rel(disk.s_alice, "upload.png", "inputs"),
                 disk.rel(disk.s_room, "room.pdf")):
        refs = [send.ArtifactAttachment(path=path),
                send.ArtifactAttachment(agent="orchestrator", path=path)]
        assert send.refused_artifacts(refs, ALICE) == [path, path]
        out = await mf.resolve_metorite_files_route(
            mf.MetoriteFilesRequest(refs=[], artifacts=refs), user=alice)
        assert out["refused"] == [path, path]
        for ref in refs:
            with pytest.raises(HTTPException) as exc:
                await _send_route(alice, artifacts=[ref])
            assert exc.value.status_code == 422
            assert "is not a file in your own workspace" in str(exc.value.detail)
    assert routes["calls"] == []


# ── (1) the digest of an artifact ref holds for a chat file ─────────────────


async def test_a_chat_file_that_changes_after_the_card_sends_nothing(disk, routes) -> None:
    """H-392 holds for a chat file. The card check gives the digest of the
    bytes it showed. A room member then writes new bytes (the PUT of the file
    route, or a container). The REAL send route answers 409, and the provider
    is never called. The same send with the bytes unchanged goes."""
    alice = _user(ALICE, ORG_A)
    rel = disk.rel(disk.s_room, "room.pdf")
    ref = f"artifact:orchestrator:{rel}"
    card = await mf.resolve_metorite_files_route(mf.MetoriteFilesRequest(refs=[ref]), user=alice)
    [shown] = card["files"]
    pinned = [{"ref": shown["ref"], "digest": shown["digest"]}]
    (disk.a / rel).write_bytes(b"%PDF-BOBS-SWAPPED-BYTES")
    with pytest.raises(HTTPException) as exc:
        await _send_route(alice, metorite_files=pinned)
    assert exc.value.status_code == 409 and exc.value.detail == mf.FILE_CHANGED
    assert routes["calls"] == []
    (disk.a / rel).write_bytes(b"%PDF-ROOM")
    res = await _send_route(alice, metorite_files=pinned)
    assert res["ok"] is True
    [[att]] = routes["calls"]
    assert att["content"] == b"%PDF-ROOM"


async def test_a_chat_file_over_the_cap_is_refused_unread(disk, monkeypatch) -> None:
    """The read of a chat file stops at the cap of a send."""
    from gateway.routes.email.automation import held_card

    monkeypatch.setattr(held_card, "MAX_FILES_BYTES", 4)
    with pytest.raises(HTTPException) as exc:
        _attach(ALICE, ORG_A, disk.rel(disk.s_alice, "report.pdf"))
    assert exc.value.status_code == 422 and "larger than" in str(exc.value.detail)


# ── (b) another member's chat in the same org ───────────────────────────────


async def test_another_members_chat_is_refused(disk) -> None:
    """Mutation: drop the room check in ``_chat_thread_file`` → red."""
    alices = disk.rel(disk.s_alice, "report.pdf")
    bobs = disk.rel(disk.s_bob, "bob.pdf")
    assert _refused(BOB, ORG_A, alices)
    assert _refused(ALICE, ORG_A, bobs)
    with pytest.raises(HTTPException) as exc:
        await _metorite(BOB, ORG_A, f"artifact:orchestrator:{alices}")
    assert exc.value.status_code == 422
    # Each one still attaches the file of the own chat.
    assert _attach(BOB, ORG_A, bobs) == b"%PDF-BOB-PRIVATE"


# ── (c) another org: the root is the caller's own ───────────────────────────


async def test_another_org_is_refused_and_the_root_is_the_callers(
    disk, monkeypatch,
) -> None:
    """Carol is in org B. Org A's chats are not hers, and her own chat reads
    org B's tenant dir, never org A's, whatever the request names.
    Mutation: find the root from the request path → red."""
    from gateway.routes import workspace

    seen: list[str | None] = []
    real = workspace._tenant_agent_workspace

    def spy(agent: str, organization_id: str | None) -> Path | None:
        seen.append(organization_id)
        return real(agent, organization_id)

    monkeypatch.setattr(workspace, "_tenant_agent_workspace", spy)
    assert _refused(CAROL, ORG_B, disk.rel(disk.s_alice, "report.pdf"))
    # Her own chat: org B's file, and never the file planted in org A's dir.
    planted = disk.rel(disk.s_carol, "planted.pdf")
    assert _refused(CAROL, ORG_B, planted)
    assert _attach(CAROL, ORG_B, disk.rel(disk.s_carol, "carol.pdf")) == b"%PDF-CAROL"
    # A request that names a tenant changes nothing: the model has no such
    # field, and the root comes from the session's org.
    ref = send.ArtifactAttachment.model_validate(
        {"agent": "orchestrator", "path": planted, "organization_id": ORG_A,
         "org": ORG_A, "tenant": ORG_A})
    assert not any(k in send.ArtifactAttachment.model_fields
                   for k in ("organization_id", "org", "tenant"))
    with pytest.raises(HTTPException) as exc:
        send.load_artifact_attachments([ref], CAROL, organization_id=ORG_B, chat_files=True)
    assert exc.value.status_code == 422
    assert seen and set(seen) == {ORG_B}, seen
    with pytest.raises(ValidationError):
        mf.MetoriteFilesRequest(refs=[], artifacts=[], organization_id=ORG_A)
    # Org A's member reads org A's planted file only through a chat of org A,
    # and that slug is Carol's chat of org B: no row in org A, so refused.
    assert _refused(ALICE, ORG_A, planted)


def test_no_tenant_no_email_or_no_agent_reads_no_chat_file(disk, room_rows) -> None:
    """A caller with no org or no email, and a ref that names no agent, get
    no chat file, and no room lookup runs."""
    rel = disk.rel(disk.s_alice, "report.pdf")
    deep = disk.rel(disk.s_alice, "q3/deep.pdf")
    assert _refused(ALICE, None, rel)
    assert _refused("", ORG_A, rel)
    assert _refused("default", ORG_A, rel)
    assert _refused(ALICE, ORG_A, deep, "")
    assert room_rows.calls == []


# ── (d) the role in the room ────────────────────────────────────────────────


def test_a_room_member_attaches_and_a_viewer_does_not(disk) -> None:
    room = disk.rel(disk.s_room, "room.pdf")
    assert _attach(ALICE, ORG_A, room) == b"%PDF-ROOM"   # owner
    assert _attach(BOB, ORG_A, room) == b"%PDF-ROOM"     # member
    assert _refused(DAN, ORG_A, room)                    # viewer


def test_a_reader_a_leaver_and_a_chat_with_no_row_are_refused(disk) -> None:
    """An org chat gives Bob read and no send. Bob left the chat that he
    made. A folder whose chat has no row belongs to no one."""
    assert _refused(BOB, ORG_A, disk.rel(disk.s_org, "org.pdf"))
    assert _refused(BOB, ORG_A, disk.rel(disk.s_left, "left.pdf"))
    assert _attach(ALICE, ORG_A, disk.rel(disk.s_left, "left.pdf")) == b"%PDF-LEFT"
    for email in (ALICE, BOB):
        assert _refused(email, ORG_A, disk.rel(disk.s_norow, "norow.pdf"))


async def test_a_failed_room_lookup_answers_503(disk, room_rows) -> None:
    """An outage is not a refusal: 503, so the tool says "try again" and no
    card shows."""
    room_rows.fail = True
    rel = disk.rel(disk.s_alice, "report.pdf")
    ref = send.ArtifactAttachment(agent="orchestrator", path=rel)
    for call in (lambda: send.load_artifact_attachments(
                     [ref], ALICE, organization_id=ORG_A, chat_files=True),
                 lambda: send.resolve_artifact_path(
                     ref, ALICE, organization_id=ORG_A, chat_files=True)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        await _metorite(ALICE, ORG_A, f"artifact:orchestrator:{rel}")
    assert exc.value.status_code == 503


# ── (e) paths that leave a chat folder ──────────────────────────────────────


def _bad_paths(disk: _Disk) -> list[str]:
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
        assert _refused(ALICE, ORG_A, path), path


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
        assert _refused(ALICE, ORG_A, f"outputs/{a}/{name}"), name


# ── held: a held run can write the folder of its own chat ───────────────────


async def test_a_held_send_takes_no_chat_file(disk) -> None:
    """The held card resolves with ``held=True``. The answer is the 422 of
    before, word for word. Mutation: ignore ``chat_files`` → red."""
    ref = f"artifact:orchestrator:{disk.rel(disk.s_alice, 'report.pdf')}"
    assert await _metorite(ALICE, ORG_A, ref) == b"%PDF-ALICE"
    with pytest.raises(HTTPException) as exc:
        await _metorite(ALICE, ORG_A, ref, held=True)
    assert exc.value.status_code == 422
    assert "is not a file in your own workspace" in str(exc.value.detail)


# ── (f) a personal agent keeps its rule ─────────────────────────────────────


def test_a_personal_agent_keeps_its_own_rule(disk, room_rows) -> None:
    """The member's own folder comes first, with no room lookup, on both
    doors. A ref that names a personal agent never falls to a tenant dir."""
    def plain(email: str, path: str, agent: str | None = None) -> bytes:
        [got] = send.load_artifact_attachments(
            [send.ArtifactAttachment(agent=agent, path=path)], email)
        return got["content"]

    assert plain(ALICE, "outputs/quote.md") == b"QUOTE"
    rel = disk.rel(disk.s_alice, "report.pdf")
    assert plain(ALICE, rel) == b"%PDF-PERSONAL"
    assert _attach(ALICE, ORG_A, rel, _EA) == b"%PDF-PERSONAL"
    assert room_rows.calls == []
    assert _refused(ALICE, ORG_A, disk.rel(disk.s_alice, "q3/deep.pdf"), _EA)
    assert send.refused_artifacts([send.ArtifactAttachment(path="outputs/quote.md")], BOB) \
        == ["outputs/quote.md"]
    assert room_rows.calls == []


# ── (g) the check and the send refuse the same files ────────────────────────


def test_the_check_and_the_send_refuse_the_same_files(disk) -> None:
    """On the ``artifacts`` door, ``refused_artifacts`` refuses exactly what
    ``load_artifact_attachments`` refuses: each chat file too. On the chat
    door, ``_refused`` asserts that the read and the path rule agree."""
    paths = [disk.rel(disk.s_alice, "report.pdf"), disk.rel(disk.s_alice, "q3/deep.pdf"),
             disk.rel(disk.s_room, "room.pdf"), "outputs/quote.md", "outputs/loose.pdf"]
    for path in paths:
        for agent in (None, "orchestrator"):
            ref = send.ArtifactAttachment(agent=agent, path=path)
            try:
                send.load_artifact_attachments([ref], ALICE)
                bad = False
            except HTTPException as exc:
                assert exc.status_code == 422
                bad = True
            assert send.refused_artifacts([ref], ALICE) == ([path] if bad else []), (path, agent)
    cases = [
        (ALICE, disk.rel(disk.s_alice, "report.pdf"), False),
        (BOB, disk.rel(disk.s_room, "room.pdf"), False),
        (BOB, disk.rel(disk.s_alice, "report.pdf"), True),
        (DAN, disk.rel(disk.s_room, "room.pdf"), True),
        (CAROL, disk.rel(disk.s_alice, "report.pdf"), True),
        (ALICE, "outputs/loose.pdf", True),
    ]
    for email, path, bad in cases:
        org = ORG_B if email == CAROL else ORG_A
        assert _refused(email, org, path) is bad, (email, path)


# ── one door: only an artifact ref turns chat files on ──────────────────────


def _calls(name: str) -> list[tuple[str, ast.Call]]:
    out: list[tuple[str, ast.Call]] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called == "to_thread" and node.args:
                inner = node.args[0]
                called = getattr(inner, "id", getattr(inner, "attr", ""))
            if called == name:
                out.append((path.relative_to(_REPO).as_posix(), node))
    return out


def test_only_the_artifact_door_takes_a_chat_file() -> None:
    """Review P1 of 209adc111, and P2 of 567049def.

    * Only ``metorite_files._artifact`` passes ``chat_files`` to a public
      resolver, and it passes ``not held`` with ``user.organization_id``.
    * ``_resolve_artifact`` takes ``chat_files`` as a keyword only, and its
      only callers are the two public resolvers, which pass their own value.
    * No call of the three names passes ``*`` or ``**``, so no hidden
      ``chat_files`` can reach them.
    * The public resolvers default ``chat_files`` to off, and
      ``_chat_thread_file`` has one caller.

    Mutations: the send route passes ``chat_files=True``, or
    ``**{"chat_files": True}`` → red."""
    import inspect

    door = "apps/services/gateway/gateway/routes/email/transport/metorite_files.py"
    home = "apps/services/gateway/gateway/routes/email/transport/send.py"
    on: list[str] = []
    for name in ("load_artifact_attachments", "resolve_artifact_path", "_resolve_artifact"):
        for rel, call in _calls(name):
            where = f"{rel}:{call.lineno} {name}"
            assert all(k.arg is not None for k in call.keywords), f"{where} passes **"
            assert not any(isinstance(a, ast.Starred) for a in call.args), f"{where} passes *"
            kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
            if name == "_resolve_artifact":
                assert rel == home, where
                assert kw.get("chat_files") == "chat_files", where
                assert kw.get("organization_id") == "organization_id", where
                continue
            if "chat_files" in kw:
                on.append(where)
                assert rel == door, f"{where} turns chat files on"
                assert kw["chat_files"] == "not held"
                assert kw.get("organization_id") == "user.organization_id"
    assert len(on) == 1, on
    users = [rel for rel, _c in _calls("_chat_thread_file")]
    assert users == [home], users
    for name in ("resolve_artifact_path", "load_artifact_attachments"):
        default = send.__dict__[name].__kwdefaults__["chat_files"]
        assert default is False, name
    param = inspect.signature(send._resolve_artifact).parameters["chat_files"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


async def test_a_draft_save_refuses_a_bare_chat_path(disk, monkeypatch) -> None:
    """``PUT /email/drafts`` (``upsert_draft``) resolves ``artifacts`` with
    chat files off, so a bare or ``<agent>:<path>`` chat file is a 422, and
    no provider draft is written. The Save draft of the new-mail card hands
    the recorded files to this route (the next test)."""
    from gateway.routes.email.automation import drafting as dr

    provider_calls: list[str] = []

    class _Provider:
        def __getattr__(self, name: str) -> Any:
            async def call(*_a: Any, **_k: Any) -> Any:
                provider_calls.append(name)
                return "pm-1"
            return call

    @asynccontextmanager
    async def _session() -> AsyncIterator[Any]:
        yield SimpleNamespace()

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_Provider())

    async def nothing(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr(dr, "_tenant_session", _session)
    monkeypatch.setattr(dr, "_assert_account_owner", nothing)
    monkeypatch.setattr(dr, "_account_signature", nothing)
    monkeypatch.setattr(dr, "provider_session", _provider_session)
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    for ref in ({"path": rel}, {"agent": "orchestrator", "path": rel}):
        req = dr.DraftUpsertRequest(account_id=F.BOX, to=[F.KNOWN], subject="Report",
                                    body="The report.", artifacts=[ref], from_chat=True)
        with pytest.raises(HTTPException) as exc:
            await dr.upsert_draft(req, _user(ALICE, ORG_A))
        assert exc.value.status_code == 422, ref
    assert provider_calls == []


async def test_the_new_mail_card_save_hands_the_recorded_bare_path_on(
    disk, monkeypatch,
) -> None:
    """The Save draft act of the new-mail card passes the RECORDED
    ``artifacts`` to ``upsert_draft`` unchanged, so the refusal above holds
    on that path too. A chat file goes on a card only as an artifact ref."""
    from gateway.routes.email.automation import drafting
    from gateway.routes.email.automation import held_card as hc
    from gateway.routes.email.automation import new_mail_card as nm

    from tests.unit.test_email_new_mail_card import _act, _answer, _card, edit_card

    F.flag_on(monkeypatch)
    edit_card(monkeypatch, "*")
    monkeypatch.setattr(nm, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    F.install_sends(monkeypatch)
    monkeypatch.setattr(nm, "_tenant_session", hc._tenant_session)
    monkeypatch.setattr(nm, "resolve_self", hc.resolve_self)
    saves: list[Any] = []

    async def upsert(req: Any, user: Any) -> dict[str, Any]:
        saves.append(req)
        return {"id": "draft-new-1"}

    monkeypatch.setattr(drafting, "upsert_draft", upsert)
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    card = await _card(artifacts=[{"path": rel}])
    await _act(card["card_id"], _answer(action="save"))
    [req] = saves
    assert [a.path for a in req.artifacts] == [rel] and not req.attachments
    assert send.refused_artifacts(list(req.artifacts), F.MEMBER) == [rel]


# ── the agent tool: the failing turn, end to end ────────────────────────────


agents = F.load_email_agent("ea_attach_shared_agent")


@pytest.fixture
def tool(monkeypatch, disk, routes) -> dict[str, Any]:
    """``send_email`` on the plain card, with ``_post`` wired to the REAL
    gateway routes: the check route and ``POST /email/send``. Only the
    mailbox session and the provider are stand-ins."""
    alice = _user(ALICE, ORG_A)
    state: dict[str, Any] = {"posts": [], "before_send": None}

    async def post(path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        state["posts"].append(path)
        state.setdefault("bodies", []).append((path, dict(body or {})))
        try:
            if path == "/email/metorite-files/resolve":
                return await mf.resolve_metorite_files_route(
                    mf.MetoriteFilesRequest(**(body or {})), user=alice)
            if path == "/email/send":
                return await send.send_email(
                    send.SendEmailRequest(**(body or {})), BackgroundTasks(), user=alice)
            if path == "/email/new-card":
                from gateway.routes.email.automation import new_mail_card as nm

                return await nm.new_mail_card(nm.NewMailCardRequest(**(body or {})), alice)
            if path == "/email/new-card/act":
                from gateway.routes.email.automation import new_mail_card as nm

                return await nm.new_mail_card_act(
                    nm.NewMailActRequest(**(body or {})), BackgroundTasks(), alice)
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
        if state["before_send"] is not None:
            state["before_send"]()
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
    state["sent"] = routes["calls"]
    return state


async def test_the_failing_turn_sends_the_file_by_its_artifact_ref(tool, disk) -> None:
    """The turn of 2026-11-17: ``make_document`` gave the path, and
    ``send_email`` named it as ``artifact:orchestrator:<path>``. The card
    and the send check its digest, and the file goes."""
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    out = await agents.send_email(F.BOX, body="The report is attached.", to=[F.KNOWN],
                                  subject="Report", attachments=[f"artifact:orchestrator:{rel}"])
    assert out.startswith("Sent email to"), out
    assert "not a file in your own workspace" not in out
    [[att]] = tool["sent"]
    assert att["content"] == b"%PDF-DEEP" and att["filename"] == "deep.pdf"


def _bind_run(disk: _Disk, agent: str, root: Path | None = None, **extra: Any) -> None:
    """Bind the artifact context of a chat run, as the executor binds it:
    the run's agent, its session and its working dir (``disk.a``, the
    orchestrator's tenant dir of org A, unless *root* names another)."""
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(session_id=disk.s_alice, agent_name=agent,
                          workspace_root=str(root or disk.a), **extra)


#: The ref that the tool writes for a file of ``disk.a``: the agent part is
#: the agent dir of that working dir (``state/<agent>/<slug>``).
def _ref(rel: str) -> str:
    return f"artifact:{_ORCH}:{rel}"


async def test_a_bare_make_document_path_sends_through_the_digest_door(tool, disk) -> None:
    """Opus round 2, P1: the model passes the path that ``make_document``
    gave, in the bare form. The tool writes the artifact ref of the agent
    whose working dir holds the folder, so the card and the send check its
    digest, and the file goes. ``/email/send`` carries no ``artifacts``.
    Mutation: drop ``_chat_file_refs`` → "Not sent." → red."""
    from acb_skills.write_artifact import artifact_context_scope

    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    with artifact_context_scope():
        _bind_run(disk, "orchestrator")
        out = await agents.send_email(F.BOX, body="The report is attached.", to=[F.KNOWN],
                                      subject="Report", attachments=[rel])
    assert out.startswith("Sent email to"), out
    [sent] = [b for p, b in tool["bodies"] if p == "/email/send"]
    assert "artifacts" not in sent
    assert [m["ref"] for m in sent["metorite_files"]] == [_ref(rel)]
    [[att]] = tool["sent"]
    assert att["content"] == b"%PDF-DEEP"


async def test_a_pack_call_rewrites_a_bare_path_and_sends_it(tool, disk) -> None:
    """Opus round 3, P0. On the one assistant's pack path, ``send_email``
    runs inside ``tool_packs.owner_scope("email-assistant", False)``, which
    sets ``agent_name`` to the owner of the pack. The ref still names the
    agent whose working dir holds the folder, and the file goes.
    Mutation: take the agent from ``agent_name`` → ``artifact:email-
    assistant:...``, which the gateway refuses → red."""
    from acb_skills.tool_packs import owner_scope
    from acb_skills.write_artifact import artifact_context, artifact_context_scope

    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    with artifact_context_scope():
        _bind_run(disk, "orchestrator")
        with owner_scope("email-assistant", False):
            assert artifact_context()["agent_name"] == "email-assistant"
            out = await agents.send_email(F.BOX, body="The report is attached.",
                                          to=[F.KNOWN], subject="Report", attachments=[rel])
    assert out.startswith("Sent email to"), out
    [sent] = [b for p, b in tool["bodies"] if p == "/email/send"]
    assert [m["ref"] for m in sent["metorite_files"]] == [_ref(rel)]
    [[att]] = tool["sent"]
    assert att["content"] == b"%PDF-DEEP"


@pytest.fixture
def new_card_world(monkeypatch, tool) -> dict[str, Any]:
    """The REAL new-mail card routes behind ``tool``: the record and the
    answer in a fake Redis, and the mailbox checks as stand-ins. The act
    sends through the REAL ``POST /email/send``, so its digest check runs."""
    from acb_common import get_settings
    from acb_skills import ask_tools
    from acb_skills import reply_card as rcard
    from gateway.routes.email import core
    from gateway.routes.email.automation import held_card as hc
    from gateway.routes.email.automation import new_mail_card as nm

    monkeypatch.setattr(get_settings(), "email_edit_card_orgs", "*")
    monkeypatch.setattr(nm, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)

    async def owner(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(core, "_assert_account_owner", owner)
    monkeypatch.setattr(nm, "_tenant_session", hc._tenant_session)
    monkeypatch.setattr(nm, "resolve_self", hc.resolve_self)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    thread = "th-3598"

    async def card(**kw: Any) -> Any:
        """Alice presses Send on the card, as it shows."""
        mail = {"kind": "new", "action": "send", "subject": "Report",
                "body": "The report is attached.", "to": [F.KNOWN], "cc": [], "bcc": []}
        answer = "APPROVE " + json.dumps({"reply": mail})
        cid = str(kw.get("request_id") or "")
        await rcard.record_reply_answer(ORG_A, cid, ALICE, answer, thread)
        return ask_tools.ReplyAnswer(cid, answer, rcard.parse_new_mail_answer(answer),
                                     ALICE, thread)

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return tool


async def test_a_pack_call_sends_a_bare_path_through_the_new_mail_card(
    new_card_world, disk,
) -> None:
    """The one email card, on the pack path. The tool writes the ref, the
    REAL ``POST /email/new-card`` resolves it and records its digest, and the
    REAL act sends it through ``POST /email/send``, which checks the digest
    again. Mutation: take the agent from ``agent_name`` → red."""
    from acb_skills.tool_packs import owner_scope
    from acb_skills.write_artifact import artifact_context_scope

    tool = new_card_world
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    with artifact_context_scope():
        _bind_run(disk, "orchestrator")
        with owner_scope("email-assistant", False):
            out = await agents.send_email(F.BOX, body="The report is attached.",
                                          to=[F.KNOWN], subject="Report", attachments=[rel])
    [card] = [b for p, b in tool["bodies"] if p == "/email/new-card"]
    assert card["metorite_files"] == [_ref(rel)] and card["artifacts"] == []
    assert "/email/new-card/act" in tool["posts"], (out, tool["posts"])
    [[att]] = tool["sent"]
    assert att["content"] == b"%PDF-DEEP" and att["filename"] == "deep.pdf"


async def test_a_delegated_run_names_the_agent_of_the_chat_at_the_top(tool, disk) -> None:
    """A sub-run of the email assistant works in the member's own folder, and
    carries the chat at the top in ``deliver_to``. The ref names the agent of
    the top chat's working dir, never the sub-run's own.
    Mutation: read the sub-run's own ``workspace_root`` → red."""
    from acb_skills.write_artifact import DELIVER_TO, artifact_context_scope

    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    top = {"workspace_root": str(disk.a), "session_id": disk.s_alice,
           "agent_name": "orchestrator", "instance": ""}
    with artifact_context_scope():
        _bind_run(disk, "email-assistant", disk.alice_ea, **{DELIVER_TO: top})
        out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                      attachments=[rel])
    assert out.startswith("Sent email to"), out
    [sent] = [b for p, b in tool["bodies"] if p == "/email/send"]
    assert [m["ref"] for m in sent["metorite_files"]] == [_ref(rel)]


async def test_a_held_chat_gets_no_rewrite_and_no_chat_file(tool, disk, monkeypatch) -> None:
    """A held run sends only through the held card, which takes no bare
    path and no chat file. The rewrite runs after the held branch, so the
    held card's own refusal comes back and nothing is posted.
    Mutation: call the rewrite before the held branch → a post → red."""
    from acb_skills.write_artifact import artifact_context_scope

    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    with artifact_context_scope():
        _bind_run(disk, "orchestrator")
        out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                      attachments=[rel])
    assert out.startswith("Not sent."), out
    import acb_skills.held_card as H

    assert H.WORKSPACE_FILE_REFUSED in out
    assert tool["posts"] == [] and tool["sent"] == []


async def test_the_rewrite_reaches_the_new_mail_card_and_keeps_other_files(
    tool, disk, monkeypatch,
) -> None:
    """The one email card gets the ref too. A path of no chat folder, a named
    ``<agent>:<path>`` and a Metorite ref stay as the model wrote them. A
    working dir outside ``state/`` (an app's own dir) gives no rewrite."""
    from acb_skills.write_artifact import artifact_context_scope

    seen: dict[str, Any] = {}

    async def card(*_a: Any, **kw: Any) -> str:
        seen.update(kw)
        return "To kim"

    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    monkeypatch.setattr(agents, "_new_mail_card", card)
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    keep = ["outputs/quote.md", f"orchestrator:{rel}", "knowledge:x"]
    assert agents._chat_file_refs([rel, *keep]) == [rel, *keep]  # no bound run
    with artifact_context_scope():
        _bind_run(disk, "orchestrator", disk.base / "an-app-dir")
        assert agents._chat_file_refs([rel]) == [rel]
    with artifact_context_scope():
        _bind_run(disk, "orchestrator")
        assert agents._chat_file_refs([rel, *keep]) == [_ref(rel), *keep]
        assert agents._chat_file_refs(None) is None
        await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                attachments=[rel])
    assert seen["attachments"] == [_ref(rel)]


@pytest.mark.parametrize("form", ["bare", "agent-path"])
async def test_the_bare_and_agent_path_forms_of_a_chat_file_show_no_card(
    tool, disk, form,
) -> None:
    """With no bound run (so no chat folder to name), these forms pin no
    digest. A chat file is refused before any card, and nothing goes. The
    ``<agent>:<path>`` form is never rewritten."""
    rel = disk.rel(disk.s_alice, "q3/deep.pdf")
    name = rel if form == "bare" else f"orchestrator:{rel}"
    out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                  attachments=[name])
    assert out.startswith("Not sent."), out
    assert tool["posts"] == ["/email/metorite-files/resolve"] and tool["sent"] == []


async def test_a_file_that_changes_while_the_card_is_open_does_not_go(tool, disk) -> None:
    """A room member writes new bytes while Alice's card is open. The send
    answers 409, and nothing goes from her mailbox."""
    rel = disk.rel(disk.s_room, "room.pdf")
    tool["before_send"] = lambda: (disk.a / rel).write_bytes(b"%PDF-BOBS-INVOICE")
    with pytest.raises(agents.GatewayError) as exc:
        await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                attachments=[f"artifact:orchestrator:{rel}"])
    assert exc.value.status == 409
    assert tool["sent"] == []


async def test_the_root_name_of_the_turn_stays_refused(tool, disk) -> None:
    """``artifact:orchestrator:x.pdf`` names no chat folder, so no chat owns
    it. It stays refused, before any card and any send."""
    out = await agents.send_email(F.BOX, body="The report is attached.", to=[F.KNOWN],
                                  subject="Report", attachments=["artifact:orchestrator:x.pdf"])
    assert out.startswith("Not sent."), out
    assert "/email/send" not in tool["posts"] and tool["sent"] == []


async def test_a_colleagues_chat_file_does_not_go(tool, disk) -> None:
    """Opus round 3, P2: a refused ``artifact:`` ref gets the next step of
    task 2491 too, so the model asks before a send without the file."""
    rel = disk.rel(disk.s_bob, "bob.pdf")
    for name in (f"artifact:orchestrator:{rel}", rel):
        out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                      attachments=[name])
        assert out.startswith("Not sent."), out
        assert out.endswith(agents._FILE_NEXT_STEP), out
    assert "/email/send" not in tool["posts"] and tool["sent"] == []


async def test_a_refused_artifact_ref_on_the_new_mail_card_gets_the_next_step(
    new_card_world, disk,
) -> None:
    """The REAL ``POST /email/new-card`` refuses a colleague's chat file with
    422. The tool answer names the gateway's reason and the next step, and
    no card shows. A refusal that is not about a file gets no next step."""
    tool = new_card_world
    rel = disk.rel(disk.s_bob, "bob.pdf")
    out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN], subject="R",
                                  attachments=[f"artifact:orchestrator:{rel}"])
    assert out.startswith("Not sent. The attachment '"), out
    assert out.endswith(agents._FILE_NEXT_STEP)
    assert "/email/new-card/act" not in tool["posts"] and tool["sent"] == []
    assert agents._refused_text("The email has no text, so nothing was sent.") == (
        "Not sent. The email has no text, so nothing was sent.")


# ── Task 3812: the text and the files agree ────────────────────────────────


def test_promises_file_reads_the_words() -> None:
    """A text that says a file is attached or enclosed makes the promise.
    Mutation: drop ``_promises_file`` → every case below is red."""
    for text in ("Please find attached the deck.", "See the attachment.", "Enclosed is the PDF"):
        assert agents._promises_file(text), text
    for text in ("Thanks, talk soon.", "", None):
        assert not agents._promises_file(text), text


async def test_a_text_that_says_attached_with_no_file_warns_on_the_card(
    monkeypatch, tool,
) -> None:
    """The card of a mail with no file, whose text says "attached", shows the
    warning line. A mail with no such words shows none.
    Mutation: drop the notice in ``send_email`` → the first assert is red."""
    seen: list[str] = []

    async def approve(**kw: Any) -> bool:
        seen.append(str(kw.get("context") or ""))
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", approve)
    await agents.send_email(F.BOX, body="Please find attached the deck.", to=[F.KNOWN],
                            subject="Deck")
    await agents.send_email(F.BOX, body="Thanks, talk soon.", to=[F.KNOWN], subject="Hi")
    assert agents._PROMISE_NO_FILE in seen[0]
    assert agents._PROMISE_NO_FILE not in seen[1]
