"""Task 4025 (rung 0, data loss): the IMAP trash moves the RIGHT mail to Trash.

``IMAPProvider.trash_message`` once ran UID COPY with the Trash folder
selected, so the copy read a Trash UID and not the INBOX one. Then it flagged
the INBOX UID and ran a plain EXPUNGE. So the INBOX mail was lost for good, a
Trash mail with the same UID number was copied twice, and each mail that
another client had flagged ``\\Deleted`` went too. It also deleted when no
Trash folder existed or when the copy failed.

``FakeImap`` keeps real UIDs for each folder, a selected folder, and the
answers of a real server: NO for a missing folder, an error (imaplib's BAD)
for an unknown command or a name with a space and no quotes, and a UID COPY
of an absent UID that copies nothing and answers OK.

Owning spec: ``project-docs/specs/email_app_master_plan.md`` §18.13.3.
Run: ``uv run pytest tests/unit/test_email_imap_trash.py -q``
"""

from __future__ import annotations

import imaplib
import re
from typing import Any

import pytest
from email_ingestion.providers.imap import IMAPProvider

_FULL_CAPS = ("IMAP4rev1", "MOVE", "UIDPLUS")
_COPY_CAPS = ("IMAP4rev1", "UIDPLUS")
_BARE_CAPS = ("IMAP4rev1",)


def _astring(arg: str) -> str:
    """The mailbox name of one IMAP argument, as a server reads it."""
    if len(arg) >= 2 and arg[0] == arg[-1] == '"':
        return re.sub(r"\\(.)", r"\1", arg[1:-1])
    if not arg or re.search(r'[(){ %*"\\\x00-\x1f]', arg):
        raise imaplib.IMAP4.error(f"BAD bad mailbox argument {arg!r}")
    return arg


class FakeImap:
    """An IMAP server in memory, with the folder state that a real one keeps."""

    def __init__(
        self,
        folders: dict[str, list[tuple[bytes, set[str]]]],
        *,
        caps: tuple[str, ...] = _FULL_CAPS,
        special: dict[str, str] | None = None,
        refuse: set[str] | None = None,
    ) -> None:
        self.boxes: dict[str, dict[int, tuple[bytes, set[str]]]] = {}
        self.uidnext: dict[str, int] = {}
        for name, mails in folders.items():
            self.boxes[name] = {i + 1: (body, set(flags))
                                for i, (body, flags) in enumerate(mails)}
            self.uidnext[name] = len(mails) + 1
        self.caps = caps
        self.capabilities = caps  # what imaplib read before the login
        self.special = special or {}
        self.refuse = refuse or set()
        self.selected: str | None = None
        self.readonly = False
        self.log: list[str] = []

    # The commands that imaplib.IMAP4 exposes and the provider calls.

    def noop(self) -> tuple[str, list[bytes]]:
        return "OK", [b""]

    def capability(self) -> tuple[str, list[bytes]]:
        return "OK", [" ".join(self.caps).encode()]

    def list(self, *_a: Any) -> tuple[str, list[Any]]:
        out: list[Any] = []
        for name in self.boxes:
            flags = "\\HasNoChildren"
            if name in self.special:
                flags += " " + self.special[name]
            if " " in name:
                out.append(f'({flags}) "/" "{name}"'.encode())
            else:
                out.append(f'({flags}) "/" {name}'.encode())  # an atom, as Dovecot
        return "OK", out

    def select(self, mailbox: str = "INBOX", readonly: bool = False):
        name = _astring(mailbox)
        self.log.append(f"{'EXAMINE' if readonly else 'SELECT'} {name}")
        if name not in self.boxes:
            self.selected = None
            return "NO", [b"Mailbox does not exist"]
        self.selected, self.readonly = name, readonly
        return "OK", [str(len(self.boxes[name])).encode()]

    def expunge(self):
        self.log.append("EXPUNGE")
        box = self._box()
        if self.readonly:
            return "NO", [b"read-only"]
        for u in [u for u, (_b, f) in box.items() if "\\Deleted" in f]:
            del box[u]
        return "OK", [None]

    def uid(self, command: str, *args: Any):
        command = command.upper()
        self.log.append(f"UID {command} {' '.join(str(a) for a in args if a)}")
        box = self._box()
        if command in self.refuse:
            return "NO", [b"refused"]
        handler = {"SEARCH": self._search, "COPY": self._copy, "MOVE": self._copy,
                   "STORE": self._store, "EXPUNGE": self._uid_expunge}.get(command)
        if handler is None:
            raise imaplib.IMAP4.error(f"BAD unknown command UID {command}")
        return handler(box, command, *args)

    # The UID commands of the fake.

    def _search(self, box: dict, _cmd: str, *args: Any):
        m = re.fullmatch(r"UID (\d+)", str(args[-1]))
        hits = [str(u) for u in sorted(box) if m is None or u == int(m.group(1))]
        return "OK", [" ".join(hits).encode()]

    def _copy(self, box: dict, command: str, uid_set: Any, mailbox: str):
        if command == "MOVE" and "MOVE" not in self.caps:
            raise imaplib.IMAP4.error("BAD unknown command MOVE")
        dest = _astring(mailbox)
        if dest not in self.boxes:
            return "NO", [b"[TRYCREATE] no such mailbox"]
        for u in [u for u in self._uids(uid_set) if u in box]:
            body, flags = box[u]
            n = self.uidnext[dest]
            self.boxes[dest][n] = (body, set(flags))
            self.uidnext[dest] = n + 1
            if command == "MOVE":
                del box[u]
        return "OK", [b"done"]

    def _store(self, box: dict, _cmd: str, uid_set: Any, op: str, value: str):
        if self.readonly:
            return "NO", [b"read-only"]
        names = str(value).strip("()").split()
        for u in [u for u in self._uids(uid_set) if u in box]:
            if str(op).startswith("+"):
                box[u][1].update(names)
            else:
                box[u][1].difference_update(names)
        return "OK", [b"done"]

    def _uid_expunge(self, box: dict, _cmd: str, uid_set: Any):
        if "UIDPLUS" not in self.caps:
            raise imaplib.IMAP4.error("BAD unknown command UID EXPUNGE")
        for u in self._uids(uid_set):
            if u in box and "\\Deleted" in box[u][1]:
                del box[u]
        return "OK", [None]

    # Helpers of the fake.

    def _box(self) -> dict[int, tuple[bytes, set[str]]]:
        if self.selected is None:
            raise imaplib.IMAP4.error("command illegal in state AUTH")
        return self.boxes[self.selected]

    @staticmethod
    def _uids(uid_set: Any) -> list[int]:
        return [int(x) for x in str(uid_set).split(",")]

    def snapshot(self) -> dict[str, list[tuple[bytes, frozenset[str]]]]:
        """Each folder's mail, by content and flags, with no UID."""
        return {name: sorted((b, frozenset(f)) for b, f in box.values())
                for name, box in self.boxes.items()}


def _mail(tag: str) -> bytes:
    return f"Subject: {tag}\r\nMessage-ID: <{tag}@test>\r\n\r\n{tag}".encode()


def _mailbox(trash: str = "Trash", **kw: Any) -> FakeImap:
    """INBOX holds A (uid 1), B (uid 2) and C (uid 3). Another client flagged
    C ``\\Deleted`` and has not expunged it. Trash holds T1 (uid 1) and T2
    (uid 2), so the UID 2 of B also names T2 in Trash."""
    return FakeImap({
        "INBOX": [(_mail("A"), {"\\Seen"}), (_mail("B"), set()),
                  (_mail("C"), {"\\Deleted"})],
        trash: [(_mail("T1"), {"\\Seen"}), (_mail("T2"), {"\\Seen"})],
        "Sent": [(_mail("S1"), {"\\Seen"})],
    }, **kw)


def _provider(server: FakeImap) -> IMAPProvider:
    p = IMAPProvider({})
    p._imap = server  # type: ignore[assignment]
    return p


def _expect_b_trashed(before: dict, after: dict, trash: str = "Trash") -> None:
    """B left INBOX and is in Trash once, and no other mail changed."""
    assert [b for b, _f in after["INBOX"]] == [_mail("A"), _mail("C")]
    assert dict(after["INBOX"])[_mail("C")] == frozenset({"\\Deleted"})
    assert after[trash] == sorted([*before[trash], (_mail("B"), frozenset())])
    assert after["Sent"] == before["Sent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caps", [_FULL_CAPS, _COPY_CAPS], ids=["move", "copy-uidplus"])
async def test_the_trashed_mail_is_in_trash_and_no_other_mail_changed(caps):
    server = _mailbox(caps=caps)
    before = server.snapshot()
    assert await _provider(server).trash_message("2") is None
    _expect_b_trashed(before, server.snapshot())
    assert "EXPUNGE" not in server.log  # never the plain EXPUNGE


@pytest.mark.asyncio
async def test_the_copy_and_the_move_run_with_inbox_selected():
    for caps, verb in ((_FULL_CAPS, "MOVE"), (_COPY_CAPS, "COPY")):
        server = _mailbox(caps=caps)
        await _provider(server).trash_message("2")
        at = next(i for i, line in enumerate(server.log)
                  if line.startswith(f"UID {verb}"))
        selects = [line for line in server.log[:at] if line.split()[0] in
                   ("SELECT", "EXAMINE")]
        assert selects[-1] == "SELECT INBOX"


@pytest.mark.asyncio
async def test_with_neither_move_nor_uidplus_the_original_stays_flagged():
    """No safe expunge of one UID: the copy is in Trash, B stays flagged
    \\Deleted in INBOX, and C (flagged by another client) is not removed."""
    server = _mailbox(caps=_BARE_CAPS)
    before = server.snapshot()
    await _provider(server).trash_message("2")
    after = server.snapshot()
    assert dict(after["INBOX"]) == {
        _mail("A"): frozenset({"\\Seen"}),
        _mail("B"): frozenset({"\\Deleted"}),
        _mail("C"): frozenset({"\\Deleted"}),
    }
    assert after["Trash"] == sorted([*before["Trash"], (_mail("B"), frozenset())])
    assert not any("EXPUNGE" in line for line in server.log)


@pytest.mark.asyncio
@pytest.mark.parametrize("caps", [_FULL_CAPS, _COPY_CAPS, _BARE_CAPS])
async def test_a_folder_name_with_a_space_is_quoted(caps):
    server = _mailbox(trash="Deleted Items", caps=caps)
    before = server.snapshot()
    await _provider(server).trash_message("2")
    after = server.snapshot()
    if caps == _BARE_CAPS:  # the original stays flagged, as above
        assert after["Deleted Items"] == sorted(
            [*before["Deleted Items"], (_mail("B"), frozenset())])
        assert len(after["INBOX"]) == 3
    else:
        _expect_b_trashed(before, after, "Deleted Items")


@pytest.mark.asyncio
async def test_the_special_use_trash_flag_wins_over_a_name():
    server = _mailbox(special={"Papierkorb": "\\Trash"})
    server.boxes["Papierkorb"], server.uidnext["Papierkorb"] = {}, 1
    await _provider(server).trash_message("2")
    assert [b for b, _f in server.boxes["Papierkorb"].values()] == [_mail("B")]
    assert len(server.boxes["Trash"]) == 2


@pytest.mark.asyncio
async def test_no_trash_folder_raises_and_changes_nothing():
    server = FakeImap({"INBOX": [(_mail("A"), set()), (_mail("B"), set())]})
    before = server.snapshot()
    with pytest.raises(RuntimeError, match="no Trash folder"):
        await _provider(server).trash_message("2")
    assert server.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("caps,verb", [(_FULL_CAPS, "MOVE"), (_COPY_CAPS, "COPY"),
                                       (_BARE_CAPS, "COPY")])
async def test_a_refused_copy_or_move_raises_and_changes_nothing(caps, verb):
    server = _mailbox(caps=caps, refuse={verb})
    before = server.snapshot()
    with pytest.raises(RuntimeError, match=verb):
        await _provider(server).trash_message("2")
    assert server.snapshot() == before
    assert not any(line.startswith("UID STORE") for line in server.log)


@pytest.mark.asyncio
async def test_a_bad_answer_raises_and_changes_nothing():
    """A server that does not know MOVE although it named it answers BAD,
    and imaplib raises."""
    server = _mailbox(caps=_FULL_CAPS)
    server.caps = _COPY_CAPS  # the commands refuse MOVE
    server.capability = lambda: ("OK", [b"IMAP4rev1 MOVE UIDPLUS"])  # type: ignore[method-assign]
    before = server.snapshot()
    with pytest.raises(imaplib.IMAP4.error):
        await _provider(server).trash_message("2")
    assert server.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("caps", [_FULL_CAPS, _COPY_CAPS, _BARE_CAPS])
async def test_a_uid_that_is_not_in_inbox_raises_and_changes_nothing(caps):
    server = _mailbox(caps=caps)
    server.boxes["Trash"][7] = (_mail("T7"), set())  # UID 7 is in Trash only
    before = server.snapshot()
    with pytest.raises(RuntimeError, match="not in INBOX"):
        await _provider(server).trash_message("7")
    assert server.snapshot() == before


@pytest.mark.asyncio
async def test_an_id_that_is_not_a_uid_raises_before_any_command():
    server = _mailbox()
    with pytest.raises(ValueError):
        await _provider(server).trash_message("draft-Drafts")
    assert server.log == []


@pytest.mark.asyncio
async def test_a_bulk_trash_goes_on_past_one_failure():
    """The bulk path of the base class: a UID that is not in INBOX lands in
    ``failed_out``, and the other UIDs are trashed."""
    server = _mailbox(caps=_COPY_CAPS)
    failed: list[str] = []
    rekeys = await _provider(server).bulk_apply(["1", "99", "2"], "trash", failed)
    assert rekeys == {} and failed == ["99"]
    assert [b for b, _f in server.boxes["INBOX"].values()] == [_mail("C")]
    assert sorted(b for b, _f in server.boxes["Trash"].values()) == sorted(
        [_mail("T1"), _mail("T2"), _mail("A"), _mail("B")])
