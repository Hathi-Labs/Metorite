"""Task 4016: a display name never plants the sender address.

The old parse of the IMAP provider and of the inbound SMTP receiver took the
text inside the FIRST ``<...>`` pair. So ``"<ceo@company.test>"
<mallory@evil.test>`` stored ``ceo@company.test`` as the sender, and that
false sender reached the chat approval card and the held chat. The old parse
also decoded the RFC 2047 encoded words BEFORE the split, so an encoded name
planted the same angle pair. The Gmail parse fell back to the same split
when the first entry had no ``@``.

``email_ingestion.addresses`` is now the one parse of an address header. It
splits the RAW header, and it decodes a display name only after the split.
A sender that is malformed or ambiguous gives an empty address, never a
guess. These tests run each provider over the same planted headers.

The mutation check: :func:`_old_split` is the removed parse. Each planted
header gives ``ceo@company.test`` through it, so a return of that parse
turns these tests red.
"""
from __future__ import annotations

import ast
import base64
import pathlib
from email.utils import getaddresses as real_getaddresses
from typing import Any

import pytest
from aiosmtpd.smtp import Envelope
from email_ingestion import addresses, inbound
from email_ingestion.addresses import (
    NO_SENDER,
    header_text,
    parse_address_list,
    parse_sender,
)
from email_ingestion.providers.base import EmailAddress
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.imap import _parse_imap_raw_message

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO / "apps" / "services" / "email_ingestion" / "email_ingestion"

FALSE = "ceo@company.test"
REAL = "mallory@evil.test"

#: Headers whose real address is ``REAL``, with ``FALSE`` in the name.
PLANTED = {
    "quoted-name": f'"<{FALSE}>" <{REAL}>',
    "encoded-name": f"=?utf-8?q?=3Cceo=40company=2Etest=3E?= <{REAL}>",
    "encoded-quoted-name":
        f"=?utf-8?q?=22=3Cceo=40company=2Etest=3E=22?= <{REAL}>",
    "encoded-comma-name": f"=?utf-8?q?=3Cceo=40company=2Etest=3E=2C_x?= <{REAL}>",
    "escaped-quote-name": f'"a \\"<{FALSE}>\\"" <{REAL}>',
    "comment-name": f"(<{FALSE}>) {REAL}",
    "fragment-first": f'Foo, "<{FALSE}>" <{REAL}>',
    "folded": f"=?utf-8?q?=3Cceo=40company=2Etest=3E?=\r\n <{REAL}>",
}

#: Headers with no one clear address. Each gives the empty sender.
AMBIGUOUS = {
    "address-as-name": f"{FALSE} <{REAL}>",
    "two-angle-pairs": f"<{FALSE}> <{REAL}>",
    "no-space": f"{FALSE}<{REAL}>",
    "pair-then-pair": f'"x" <{REAL}> <{FALSE}>',
    "raw-at-in-encoded-word": f"=?utf-8?q?{FALSE}?= <{REAL}>",
    "unterminated-quote": f'"unterminated <{FALSE}>',
    "unbalanced-comment": f"{FALSE})<{REAL}>",
    "two-addresses": f"{FALSE}, {REAL}",
    "group-of-two": f"Group: {FALSE}, {REAL};",
    "empty-angle": "<>",
    "no-address": "Mail Delivery Subsystem",
    "deep-comment": "(" * 1200 + "a@b.test" + ")" * 1200,
    "long-backslash-run": "\\" * 100_000 + f" <{REAL}>",
}

#: Real sender headers that must keep their address and name.
REAL_SENDERS = [
    ('"Doe, John" <john.doe@example.org>', "Doe, John", "john.doe@example.org"),
    ("Doe, John <john.doe@example.org>", "Doe, John", "john.doe@example.org"),
    ("Asha Rao <asha@example.com>", "Asha Rao", "asha@example.com"),
    ("Asha<asha@example.com>", "Asha", "asha@example.com"),
    ("<asha@example.com>", "", "asha@example.com"),
    ("asha@example.com", "", "asha@example.com"),
    ("asha@example.com (Asha Rao)", "Asha Rao", "asha@example.com"),
    ("=?utf-8?b?SsO2cmc=?= <j@xn--bcher-kva.de>", "Jörg", "j@xn--bcher-kva.de"),
    ("Jörg <jörg@bücher.de>", "Jörg", "jörg@bücher.de"),
    ("=?iso-8859-1?q?M=FCller=2C_J=FCrgen?= <j@example.net>",
     "Müller, Jürgen", "j@example.net"),
    ("Ops <ops@[192.0.2.1]>", "Ops", "ops@[192.0.2.1]"),
]


def _old_split(raw: str) -> str:
    """The removed parse: decode first, then the first ``<...>`` pair."""
    decoded = addresses.decode_display_name(header_text(raw))
    if "<" in decoded and ">" in decoded:
        return decoded.split("<")[1].split(">")[0].strip()
    return decoded.strip()


def _mail(from_lines: list[str], to: str = "member@company.test") -> str:
    head = "".join(f"From: {line}\r\n" for line in from_lines)
    return (f"{head}To: {to}\r\nSubject: hello\r\n"
            "Message-ID: <m1@evil.test>\r\n\r\nbody\r\n")


# ── the mutation check ─────────────────────────────────────────────────────


@pytest.mark.parametrize("header", PLANTED.values(), ids=PLANTED.keys())
def test_the_old_split_plants_the_false_sender(header: str) -> None:
    """Each planted header bites: the old parse gives the false address. The
    tests below run the same headers through the new parse."""
    assert _old_split(header) == FALSE


def test_no_module_of_the_package_splits_a_header_on_an_angle() -> None:
    """The fence. No module of ``email_ingestion`` calls ``split("<")`` or
    ``partition("<")``. A header parse uses ``email_ingestion.addresses``."""
    found: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"split", "rsplit", "partition",
                                           "rpartition"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in {"<", ">"}):
                found.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert found == []


# ── the shared helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize("header", PLANTED.values(), ids=PLANTED.keys())
def test_a_planted_name_never_becomes_the_sender(header: str) -> None:
    sender = parse_sender(header)
    assert sender is not None
    assert sender.email == REAL


@pytest.mark.parametrize("header", AMBIGUOUS.values(), ids=AMBIGUOUS.keys())
def test_an_ambiguous_sender_gives_no_address(header: str) -> None:
    """A guess can come from the display name, so no guess is made."""
    assert parse_sender(header) == NO_SENDER


@pytest.mark.parametrize(("header", "name", "email"), REAL_SENDERS)
def test_a_real_sender_keeps_its_address_and_name(
    header: str, name: str, email: str,
) -> None:
    assert parse_sender(header) == EmailAddress(name=name, email=email)


def test_an_encoded_name_decodes_after_the_split() -> None:
    """The name shows the decoded text, and the address stays the real one."""
    sender = parse_sender(PLANTED["encoded-comma-name"])
    assert sender == EmailAddress(name=f"<{FALSE}>, x", email=REAL)


def test_no_from_value_gives_none() -> None:
    assert parse_sender("") is None
    assert parse_sender(None) is None
    assert parse_sender([]) is None
    assert parse_sender(["", "  "]) is None


def test_two_from_headers_give_one_sender_only_when_they_agree() -> None:
    assert parse_sender(["a@example.org", "A <A@example.org>"]) == EmailAddress(
        name="", email="a@example.org")
    assert parse_sender([f"Boss <{FALSE}>", REAL]) == NO_SENDER
    assert parse_sender([REAL, "x <>"]) == NO_SENDER


def test_raw_8bit_bytes_read_as_utf8() -> None:
    """``message_from_bytes`` keeps raw 8-bit bytes as escaped surrogates."""
    escaped = "J\udcc3\udcb6rg <j@example.org>"
    assert header_text(escaped) == "Jörg <j@example.org>"
    assert parse_sender(escaped) == EmailAddress(name="Jörg", email="j@example.org")


@pytest.mark.parametrize(("header", "emails"), [
    ('"Doe, John" <j@x.test>, ops@x.test', ["j@x.test", "ops@x.test"]),
    ("Doe, John <j@x.test>, ops@x.test", ["j@x.test", "ops@x.test"]),
    ("a@x.test, b@x.test,", ["a@x.test", "b@x.test"]),
    ("a@x.test; b@x.test", ["a@x.test", "b@x.test"]),
    ("a@x.test,, b@x.test", ["a@x.test", "b@x.test"]),
    ("Team: a@x.test, b@x.test;", ["a@x.test", "b@x.test"]),
    ('"j d"@x.test, Doe', ['"j d"@x.test']),
    (f'"<{FALSE}>" <{REAL}>, z@x.test', [REAL, "z@x.test"]),
    (f"=?utf-8?q?{FALSE[:3]}=2C_=3C{FALSE[4:]}=3E?= <{REAL}>", [REAL]),
    ("undisclosed-recipients:;", []),
    ("", []),
    ("(" * 1200, []),
], ids=["quoted-comma", "bare-comma", "trailing-comma", "semicolon",
        "empty-element", "group", "quoted-local", "planted", "encoded-planted",
        "empty-group", "empty", "deep-comment"])
def test_a_list_keeps_each_real_address(header: str, emails: list[str]) -> None:
    """A fragment with no address is never a recipient. The address of an
    entry is never text of its name."""
    assert [a.email for a in parse_address_list(header)] == emails


def test_a_list_decodes_each_name_after_the_split() -> None:
    header = ("=?utf-8?q?Rao=2C_Asha?= <asha@example.com>, "
              "=?utf-8?b?QW5kcsOp?= <andre@example.com>")
    assert parse_address_list(header) == [
        EmailAddress(name="Rao, Asha", email="asha@example.com"),
        EmailAddress(name="André", email="andre@example.com"),
    ]


@pytest.mark.parametrize("mode", ["strict-keyword", "no-strict-keyword"])
def test_the_result_does_not_depend_on_the_strict_keyword(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """CVE-2023-27043 added ``strict`` to ``getaddresses``. A Python from
    before that fix has no such keyword, and the parse must give the same
    answers there."""
    if mode == "no-strict-keyword":
        def old_getaddresses(fieldvalues: list[str], **kwargs: Any):
            if kwargs:
                raise TypeError("getaddresses() got an unexpected keyword")
            return real_getaddresses(fieldvalues, strict=False)
        monkeypatch.setattr(addresses, "getaddresses", old_getaddresses)
    for header in PLANTED.values():
        assert parse_sender(header).email == REAL
    for header in AMBIGUOUS.values():
        assert parse_sender(header) == NO_SENDER
    for header, name, email in REAL_SENDERS:
        assert parse_sender(header) == EmailAddress(name=name, email=email)


# ── the IMAP provider ──────────────────────────────────────────────────────


@pytest.mark.parametrize("header", PLANTED.values(), ids=PLANTED.keys())
def test_imap_stores_the_real_sender(header: str) -> None:
    msg = _parse_imap_raw_message(_mail([header]), "7", [], "")
    assert msg is not None
    assert msg.from_address.email == REAL


@pytest.mark.parametrize("header", AMBIGUOUS.values(), ids=AMBIGUOUS.keys())
def test_imap_stores_no_sender_for_an_ambiguous_header(header: str) -> None:
    msg = _parse_imap_raw_message(_mail([header]), "7", [], "")
    assert msg is not None
    assert msg.from_address == NO_SENDER


def test_imap_reads_each_from_header() -> None:
    """Two ``From`` headers with different addresses give no sender, so
    neither header wins by its place."""
    msg = _parse_imap_raw_message(_mail([f"Boss <{FALSE}>", REAL]), "7", [], "")
    assert msg is not None
    assert msg.from_address == NO_SENDER


def test_imap_keeps_real_recipients() -> None:
    to = ('"Rao, Asha" <asha@example.com>, =?utf-8?q?Doe=2C_John?= '
          f'<john@example.org>, "<{FALSE}>" <{REAL}>')
    msg = _parse_imap_raw_message(_mail(["Asha <asha@example.com>"], to=to),
                                  "7", [], "")
    assert msg is not None
    assert msg.from_address == EmailAddress(name="Asha", email="asha@example.com")
    assert msg.to_addresses == [
        EmailAddress(name="Rao, Asha", email="asha@example.com"),
        EmailAddress(name="Doe, John", email="john@example.org"),
        EmailAddress(name=f"<{FALSE}>", email=REAL),
    ]


def test_imap_with_no_from_header_has_no_sender() -> None:
    raw = "To: member@company.test\r\nSubject: s\r\n\r\nbody\r\n"
    msg = _parse_imap_raw_message(raw, "7", [], "")
    assert msg is not None
    assert msg.from_address is None


# ── the inbound SMTP receiver ──────────────────────────────────────────────


async def _inbound(monkeypatch: pytest.MonkeyPatch, raw: bytes,
                   mail_from: str = "bounce@evil.test") -> Any:
    seen: list[Any] = []

    async def fake_persist(msg: Any) -> None:
        seen.append(msg)

    monkeypatch.setattr(inbound, "_inbound_account_id", "acct-1")
    monkeypatch.setattr(inbound, "_persist_message", fake_persist)
    envelope = Envelope()
    envelope.original_content = raw
    envelope.mail_from = mail_from
    envelope.rcpt_tos = ["member@company.test"]
    await inbound.InboundHandler()._parse_and_persist(envelope)
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize("header", PLANTED.values(), ids=PLANTED.keys())
async def test_inbound_stores_the_real_sender(
    monkeypatch: pytest.MonkeyPatch, header: str,
) -> None:
    msg = await _inbound(monkeypatch, _mail([header]).encode("utf-8"))
    assert msg.from_address.email == REAL


@pytest.mark.parametrize("header", AMBIGUOUS.values(), ids=AMBIGUOUS.keys())
async def test_inbound_stores_no_sender_for_an_ambiguous_header(
    monkeypatch: pytest.MonkeyPatch, header: str,
) -> None:
    msg = await _inbound(monkeypatch, _mail([header]).encode("utf-8"))
    assert msg.from_address == NO_SENDER


async def test_inbound_reads_each_from_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _mail([f"Boss <{FALSE}>", REAL]).encode("utf-8")
    msg = await _inbound(monkeypatch, raw)
    assert msg.from_address == NO_SENDER


async def test_inbound_reads_a_raw_utf8_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SMTPUTF8 mail carries raw UTF-8 bytes in its headers."""
    raw = _mail(["Jörg <jörg@bücher.de>"],
                to='"Rao, Asha" <asha@example.com>, ops@example.com')
    msg = await _inbound(monkeypatch, raw.encode("utf-8"))
    assert msg.from_address == EmailAddress(name="Jörg", email="jörg@bücher.de")
    assert msg.to_addresses == [
        EmailAddress(name="Rao, Asha", email="asha@example.com"),
        EmailAddress(name="", email="ops@example.com"),
    ]


async def test_inbound_with_no_from_header_uses_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"To: member@company.test\r\nSubject: s\r\n\r\nbody\r\n"
    msg = await _inbound(monkeypatch, raw, mail_from="bounce@evil.test")
    assert msg.from_address == EmailAddress(name="", email="bounce@evil.test")


# ── the Gmail provider ─────────────────────────────────────────────────────


def _gmail_raw(from_values: list[str]) -> dict[str, Any]:
    headers = [{"name": "From", "value": v} for v in from_values]
    headers += [{"name": "To", "value": "member@company.test"},
                {"name": "Subject", "value": "hello"},
                {"name": "Message-ID", "value": "<m1@evil.test>"}]
    data = base64.urlsafe_b64encode(b"body").decode()
    return {"id": "g1", "threadId": "g1", "labelIds": ["INBOX"],
            "snippet": "body", "internalDate": "1728055230000",
            "payload": {"mimeType": "text/plain", "headers": headers,
                        "body": {"size": 4, "data": data}}}


def _gmail_parse(from_values: list[str]) -> Any:
    return GmailProvider({"access_token": "x"})._parse_gmail_message(
        _gmail_raw(from_values))


@pytest.mark.parametrize("header", PLANTED.values(), ids=PLANTED.keys())
def test_gmail_stores_the_real_sender(header: str) -> None:
    assert _gmail_parse([header]).from_address.email == REAL


@pytest.mark.parametrize("header", AMBIGUOUS.values(), ids=AMBIGUOUS.keys())
def test_gmail_stores_no_sender_for_an_ambiguous_header(header: str) -> None:
    assert _gmail_parse([header]).from_address == NO_SENDER


def test_gmail_reads_each_from_header() -> None:
    assert _gmail_parse([f"Boss <{FALSE}>", REAL]).from_address == NO_SENDER


def test_gmail_with_no_from_header_has_an_empty_sender() -> None:
    assert _gmail_parse([]).from_address == NO_SENDER
