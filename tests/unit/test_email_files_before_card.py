"""Each file of a chat send resolves BEFORE its card (task 2491).

The defect, seen in production on 2026-11-09. ``send_email`` was called with
a chat upload, ``inputs/<thread>/Screenshot ….png``. The member approved the
card, and only then did ``POST /email/send`` refuse the file with 422
(``email.artifact_ref_refused``). The model sent the mail again with no file,
under a second card. So the member was asked twice.

The rule now: on each card path (the plain card, the new-mail card and the
held card) each file resolves the way the send resolves it, before any card.
A file that does not resolve shows NO card, the answer names the file by its
name only, and nothing goes to a send route. A good file shows exactly one
card, then the send.

One resolver. ``send.resolve_artifact_path`` is the rule of the send
(``load_artifact_attachments``) and of the check (``refused_artifacts``),
and ``POST /email/metorite-files/resolve`` runs the check with
``artifacts``. The held card resolves each file before its card itself. Its
first step is ``acb_skills.held_card.parse_file_ref``, which the tool calls
too, so that the answer can name the file.

R7, each with the mutation that turns it red:

* ``test_the_check_and_the_send_refuse_the_same_files`` — give the check a
  rule of its own (``is_file`` only, or no blocked-path rule).
* ``test_the_route_names_each_refused_path_and_reads_no_bytes`` — read the
  bytes in the check, or drop ``refused`` from the answer.
* ``test_a_file_that_does_not_resolve_shows_no_card`` (three paths) — move
  the check after the card, or drop it.
* ``test_a_good_file_shows_one_card_then_the_send`` (three paths) — check
  after the send, or skip the send.
* ``test_an_answer_with_no_refused_list_fails_closed`` — treat a missing list
  as "every file resolves".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools
from acb_skills import held_card as H
from acb_skills import reply_card as rcard
from fastapi import HTTPException
from gateway.routes.email.transport import metorite_files as mf
from gateway.routes.email.transport import send
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F
from tests.unit.test_chat_write_under_rls import _ALICE, _CAROL, _user
from tests.unit.test_h201_email_artifact_sources import _P, _S, _Disk

#: The file of the defect: a chat upload, which the send cannot attach.
UPLOAD = "inputs/chat-th-7f3a/Screenshot 2026-11-09 at 10.04.12.png"
UPLOAD_NAME = "Screenshot 2026-11-09 at 10.04.12.png"
#: A file of the member's own workspace, which the send attaches.
GOOD = "outputs/quote.pdf"
TEXT = "Hi Kim,\n\nThe screenshot is attached."
SEND_ROUTES = ("/email/send", "/email/new-card", "/email/new-card/act",
               "/email/held-card", "/email/held-card/act")


# ── The gateway: one resolver for the check and the send ────────────────────


@pytest.fixture
def disk(tmp_path, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    return _Disk(tmp_path)


def _send_refuses(ref: send.ArtifactAttachment, email: str) -> bool:
    try:
        send.load_artifact_attachments([ref], email)
    except HTTPException as exc:
        assert exc.status_code == 422
        return True
    return False


def test_the_check_and_the_send_refuse_the_same_files(disk) -> None:
    """The check before a card refuses exactly what the send refuses.
    Mutation: give ``refused_artifacts`` a rule of its own → red."""
    cases = [
        (None, "outputs/quote.md", _ALICE, False),
        (_P, "outputs/alice.md", _ALICE, False),
        (None, "outputs/quote.md", _CAROL, True),        # not her own workspace
        (_S, "outputs/org-a-run.md", _ALICE, True),      # a shared agent's clone
        (None, ".env", _ALICE, True),                    # a secret
        (None, "outputs/client_secret.json", _ALICE, True),
        (None, disk.sibling_rel, _ALICE, True),          # a path that climbs out
        (None, disk.sibling_abs, _ALICE, True),
        (None, UPLOAD, _ALICE, True),                    # the chat upload of the defect
        (None, "outputs", _ALICE, True),                 # a folder, not a file
        (None, "", _ALICE, True),
    ]
    for agent, path, email, bad in cases:
        ref = send.ArtifactAttachment(agent=agent, path=path)
        assert _send_refuses(ref, email) is bad, (agent, path, email)
        assert send.refused_artifacts([ref], email) == ([path] if bad else []), (
            agent, path, email)


async def test_the_route_names_each_refused_path_and_reads_no_bytes(
    disk, monkeypatch,
) -> None:
    """``POST /email/metorite-files/resolve`` with ``artifacts`` only checks.
    It answers each refused path as the request gave it, and no bytes.
    Mutation: read the file in the check, or drop ``refused`` → red."""
    def no_read(self: Path) -> bytes:
        raise AssertionError(f"the check read {self.name}")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    req = mf.MetoriteFilesRequest(refs=[], artifacts=[
        send.ArtifactAttachment(path="outputs/quote.md"),
        send.ArtifactAttachment(path=UPLOAD),
        send.ArtifactAttachment(agent=_S, path="outputs/org-a-run.md"),
    ])
    out = await mf.resolve_metorite_files_route(req, user=_user(_ALICE, "org-a"))
    assert out == {"files": [], "refused": [UPLOAD, "outputs/org-a-run.md"]}


def test_the_route_keeps_its_strict_shape() -> None:
    """A field that the route does not name is still a 422, and the check
    takes no more files than one resolve."""
    with pytest.raises(ValidationError):
        mf.MetoriteFilesRequest(refs=[], artifacts=[], send=True)
    with pytest.raises(ValidationError):
        mf.MetoriteFilesRequest(refs=[], artifacts=[
            {"path": f"outputs/{i}.pdf"} for i in range(mf.MAX_METORITE_FILES + 1)])


# ── The tool: no card for a file that does not resolve ──────────────────────

agents = F.load_email_agent("ea_files_before_card")


class _Gw:
    """The gateway as the tool sees it. A workspace file in ``bad`` is one
    that the send refuses."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.bad: set[str] = {UPLOAD}
        self.check: Any = None  # a stand-in answer of the check, when set

    async def post(self, path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        self.posts.append((path, dict(body or {})))
        if path == "/email/metorite-files/resolve":
            if self.check is not None:
                if isinstance(self.check, Exception):
                    raise self.check
                return self.check
            assert body and body["refs"] == []
            return {"files": [], "refused": [a["path"] for a in body["artifacts"]
                                             if a["path"] in self.bad]}
        if path == "/email/send":
            assert not {a["path"] for a in body.get("artifacts") or []} & self.bad
            return {"id": "sent-1"}
        if path == "/email/new-card":
            return {"card_id": "f" * 32, "mailbox": f"Work · {F.MEMBER}", "subject": "Shot",
                    "body": TEXT, "to": [F.KNOWN], "cc": [], "bcc": [],
                    "files": [f'File: "{a["path"]}"' for a in body["artifacts"]]}
        if path == "/email/held-card":
            for ref in body.get("files") or []:
                try:
                    H.parse_file_ref(ref)  # the held card's own first step
                except ValueError as exc:  # the route answers 422, as it does
                    raise agents.GatewayError("Email POST failed (422)", 422,
                                              detail=str(exc)) from None
            return {"card_id": "e" * 32, "title": "Send this new email?", "detail": "d",
                    "context": "c", "rows": [{"id": "to-0", "label": f"To: {F.KNOWN}"}],
                    "form": {"kind": "new", "mailbox": "Work", "subject": "Shot",
                             "body": TEXT,
                             "recipients": {"reply": {"to": [F.KNOWN], "cc": []},
                                            "reply_all": {"to": [F.KNOWN], "cc": []}},
                             "classes": {F.KNOWN: "sent"},
                             "files": ["File: \"quote.pdf\" (2 KB)"],
                             "notices": [], "note": ""}}
        if path in ("/email/new-card/act", "/email/held-card/act"):
            return {"ok": True, "action": "send", "id": "m-9", "to": [F.KNOWN], "cc": [],
                    "subject": "Shot", "body": TEXT, "edited": False, "left_out": 0,
                    "files": 1}
        raise AssertionError(f"unexpected POST {path}")

    def paths(self) -> list[str]:
        return [p for p, _b in self.posts]


@pytest.fixture()
def gw(monkeypatch) -> _Gw:
    g = _Gw()
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: F.MEMBER)

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def no_note(*_a: Any) -> str:
        return ""

    async def no_rule(_aid: str, to: list[str], cc: list[str], _bcc: Any) -> Any:
        return cc, []

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", no_note)
    monkeypatch.setattr(agents, "_always_copy_cc", no_rule)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    return g


def _path(monkeypatch, which: str) -> None:
    """Put the run on one of the three card paths."""
    held = which == "held"
    monkeypatch.setattr(agents, "_held_card_mode", lambda: held)
    monkeypatch.setattr(agents, "_egress_held", lambda: held)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: which != "plain")


def _cards(monkeypatch) -> list[dict[str, Any]]:
    """Stand in for the card. Each card that shows is approved: Approve on the
    plain card, and Send with the form as it shows on the email card."""
    seen: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        seen.append(kw)
        if kw.get("reply") is None:
            return True
        mail = {"kind": "new", "action": "send", "subject": "Shot", "body": TEXT,
                "to": [F.KNOWN], "cc": [], "bcc": []}
        answer = "APPROVE " + json.dumps({"reply": mail})
        return ask_tools.ReplyAnswer(kw.get("request_id") or "r", answer,
                                     rcard.parse_new_mail_answer(answer), F.MEMBER, "th-1")

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return seen


#: The good file of each path: a workspace file, or a file of a mail in a
#: held chat (the held card attaches no workspace path).
_GOOD = {"plain": GOOD, "new": GOOD, "held": f"attachment:{F.FILE_PDF}"}
#: The routes that the good file reaches, in order.
_GOOD_POSTS = {
    "plain": ["/email/metorite-files/resolve", "/email/send"],
    "new": ["/email/metorite-files/resolve", "/email/new-card", "/email/new-card/act"],
    "held": ["/email/held-card", "/email/held-card/act"],
}


@pytest.mark.parametrize("which", ["plain", "new", "held"])
async def test_a_file_that_does_not_resolve_shows_no_card(monkeypatch, gw, which) -> None:
    """The defect of 2026-11-09, on each card path. Mutation: move the check
    after the card, or drop it → a card shows → red."""
    _path(monkeypatch, which)
    cards = _cards(monkeypatch)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[GOOD if which != "held" else _GOOD["held"],
                                               UPLOAD])
    assert cards == []
    assert not set(gw.paths()) & set(SEND_ROUTES), gw.paths()
    assert out.startswith("Not sent.")
    assert f'The file "{UPLOAD_NAME}" is not one this mail can attach.' in out
    assert "Send the email without the file, or ask the member to attach it in Email." in out
    # The answer names the file only: no folder of the workspace.
    assert "inputs/" not in out and "chat-th-7f3a" not in out
    # The good file is not named as a bad one.
    assert "quote.pdf" not in out and F.FILE_PDF not in out


@pytest.mark.parametrize("which", ["plain", "new", "held"])
async def test_a_good_file_shows_one_card_then_the_send(monkeypatch, gw, which) -> None:
    """Mutation: check after the send, or skip the send → red."""
    _path(monkeypatch, which)
    cards = _cards(monkeypatch)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[_GOOD[which]])
    assert len(cards) == 1
    assert gw.paths() == _GOOD_POSTS[which]
    assert out.startswith(("Sent", "To ")), out
    if which == "plain":
        [sent] = [b for p, b in gw.posts if p == "/email/send"]
        assert sent["artifacts"] == [{"path": GOOD}]
    if which == "new":
        [card] = [b for p, b in gw.posts if p == "/email/new-card"]
        assert card["artifacts"] == [{"path": GOOD}]
    if which == "held":
        [card] = [b for p, b in gw.posts if p == "/email/held-card"]
        assert card["files"] == [_GOOD["held"]]


async def test_each_file_that_does_not_resolve_is_named(monkeypatch, gw) -> None:
    """Two bad files and one good one: the answer names the two by name, and
    a name with a colon keeps its whole text."""
    _path(monkeypatch, "plain")
    cards = _cards(monkeypatch)
    gw.bad = {UPLOAD, "00.png"}
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[UPLOAD, GOOD, "Shot 10:00.png"])
    assert cards == [] and "/email/send" not in gw.paths()
    assert (f'The files "{UPLOAD_NAME}" and "Shot 10:00.png" are not ones this mail '
            "can attach.") in out
    assert "quote.pdf" not in out


async def test_a_held_chat_names_the_reason_of_the_held_card(monkeypatch, gw) -> None:
    """A held chat attaches no workspace path. The answer names the file, and
    gives the reason of the held card's own parse."""
    _path(monkeypatch, "held")
    cards = _cards(monkeypatch)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[GOOD])
    assert cards == [] and gw.posts == []
    assert '"quote.pdf"' in out and H.WORKSPACE_FILE_REFUSED in out


@pytest.mark.parametrize("answer", [
    {"files": []},                                            # no ``refused`` list
    {"files": [], "refused": None},
    "not json",
])
async def test_an_answer_with_no_refused_list_fails_closed(monkeypatch, gw, answer) -> None:
    """Mutation: read a missing list as "every file resolves" → a card → red."""
    _path(monkeypatch, "plain")
    cards = _cards(monkeypatch)
    gw.check = answer
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[GOOD])
    assert cards == [] and gw.paths() == ["/email/metorite-files/resolve"]
    assert out == agents._FILES_UNCHECKED


async def test_a_refused_check_fails_closed(monkeypatch, gw) -> None:
    """A 422 of the check (an older gateway refuses the new field) shows no
    card. A 5xx stops the tool, and shows no card either."""
    _path(monkeypatch, "new")
    cards = _cards(monkeypatch)
    gw.check = agents.GatewayError("Email POST failed (422)", 422, detail="extra")
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                  attachments=[GOOD])
    assert out == agents._FILES_UNCHECKED and cards == []
    gw.check = agents.GatewayError("Email POST failed (503)", 503, detail="busy")
    with pytest.raises(agents.GatewayError):
        await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                                attachments=[GOOD])
    assert cards == [] and not set(gw.paths()) & set(SEND_ROUTES)


async def test_many_files_are_checked_in_batches_the_route_takes(monkeypatch, gw) -> None:
    """The route takes at most ``MAX_METORITE_FILES`` paths in one check, so
    the tool sends the files in batches of that size. Mutation: one batch of
    every file (the route answers 422, and no card shows) → red."""
    assert agents._FILE_CHECK_BATCH == mf.MAX_METORITE_FILES
    _path(monkeypatch, "plain")
    cards = _cards(monkeypatch)
    files = [f"outputs/report-{i}.pdf" for i in range(mf.MAX_METORITE_FILES + 5)]
    await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot",
                            attachments=files)
    checks = [b for p, b in gw.posts if p == "/email/metorite-files/resolve"]
    assert [len(b["artifacts"]) for b in checks] == [mf.MAX_METORITE_FILES, 5]
    for body in checks:
        mf.MetoriteFilesRequest(**body)  # each batch is a request the route takes
    assert len(cards) == 1 and gw.paths()[-1] == "/email/send"


async def test_a_send_with_no_workspace_file_makes_no_check(monkeypatch, gw) -> None:
    """No workspace file, no check: the plain card and the send, as before."""
    _path(monkeypatch, "plain")
    cards = _cards(monkeypatch)
    await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Shot")
    assert len(cards) == 1 and gw.paths() == ["/email/send"]
