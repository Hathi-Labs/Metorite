"""An IMAP/SMTP send carries the Bcc in the envelope, never in the MIME (§21.15 review P1).

``smtplib.SMTP.send_message`` with no ``to_addrs`` reads only To, Cc and Bcc
headers. The provider set no Bcc header, so a Bcc went nowhere while the chat
said "and a Bcc to ...". The send now names To + Cc + Bcc as the envelope, and
the MIME holds no Bcc header. A recipient that the server refuses is kept in
``last_refused``, and the gateway reports it.

The test drives the real ``send_message`` of the pinned SMTP class, with the
network part replaced.
"""
from __future__ import annotations

from email import message_from_bytes
from typing import Any
from unittest.mock import MagicMock, patch

from email_ingestion.providers import imap as imap_mod
from email_ingestion.providers.imap import IMAPProvider


def _imap() -> IMAPProvider:
    return IMAPProvider({
        "imap_host": "imap.example.com", "imap_username": "me@example.com",
        "imap_password": "pw", "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_username": "me@example.com", "smtp_password": "pw",
    })


def _server(refused: dict[str, Any] | None = None) -> tuple[Any, dict[str, Any]]:
    """A ``_PinnedSMTP`` that runs smtplib's own ``send_message`` and records
    the ``sendmail`` call, with no socket."""
    seen: dict[str, Any] = {}
    server = imap_mod._PinnedSMTP.__new__(imap_mod._PinnedSMTP)
    server.ehlo_or_helo_if_needed = lambda: None
    server.esmtp_features = {}
    server.starttls = MagicMock()
    server.login = MagicMock()
    server.quit = MagicMock()

    def sendmail(from_addr: str, to_addrs: list[str], msg: str, *_a: Any, **_k: Any) -> dict:
        seen.update(from_addr=from_addr, to_addrs=list(to_addrs), msg=msg)
        return dict(refused or {})

    server.sendmail = sendmail
    return server, seen


async def _send(server: Any, p: IMAPProvider, **kw: Any) -> str:
    with patch.object(imap_mod, "pinned_public_address", return_value="93.184.216.34"), \
            patch.object(imap_mod, "_PinnedSMTP", MagicMock(return_value=server)), \
            patch.object(imap_mod, "_PinnedSMTP_SSL", MagicMock(return_value=server)):
        return await p.send_message(subject="Re: hi", body_text="hello", **kw)


async def test_the_envelope_names_to_cc_and_bcc_and_the_mime_has_no_bcc() -> None:
    server, seen = _server()
    p = _imap()
    await _send(server, p, to=["a@b.test"], cc=["c@d.test"], bcc=["hidden@e.test"])
    assert seen["to_addrs"] == ["a@b.test", "c@d.test", "hidden@e.test"]
    raw = seen["msg"] if isinstance(seen["msg"], bytes) else seen["msg"].encode()
    mime = message_from_bytes(raw)
    assert mime["Bcc"] is None
    assert b"hidden@e.test" not in raw
    assert mime["To"] == "a@b.test" and mime["Cc"] == "c@d.test"
    assert p.last_refused == frozenset()


async def test_a_refused_bcc_is_kept_for_the_report() -> None:
    server, _seen = _server({"Hidden@e.test": (550, b"no such user")})
    p = _imap()
    await _send(server, p, to=["a@b.test"], bcc=["hidden@e.test"])
    assert p.last_refused == frozenset({"hidden@e.test"})
