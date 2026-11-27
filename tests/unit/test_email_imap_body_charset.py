"""Task 4016: a body charset that Python does not know never drops an IMAP mail.

``bytes.decode`` raises ``LookupError`` for a charset name that Python does
not know. Real mail uses such names. The IMAP sync skips a message that does
not parse, and the UIDNEXT cursor moves past it, so that mail was lost for
good. ``imap._decode_body`` reads such a body as UTF-8 with each bad byte
replaced, as the inbound receiver does.
"""
from __future__ import annotations

import codecs

import pytest
from email_ingestion.providers.imap import _parse_imap_raw_message

UNKNOWN = ["unknown-8bit", "x-unknown", "x-user-defined", "iso-8859-8-i",
           "cp-850", "x-mac-roman", "binary", "8bit"]


def _single(charset: str, ctype: str) -> str:
    return ("From: Asha <asha@example.com>\r\nTo: me@example.com\r\n"
            "Subject: s\r\nMessage-ID: <m1@example.com>\r\nMIME-Version: 1.0\r\n"
            f'Content-Type: {ctype}; charset="{charset}"\r\n'
            "Content-Transfer-Encoding: 7bit\r\n\r\nhello there\r\n")


def _multipart(charset: str) -> str:
    return ("From: Asha <asha@example.com>\r\nTo: me@example.com\r\n"
            "Subject: s\r\nMessage-ID: <m2@example.com>\r\nMIME-Version: 1.0\r\n"
            'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
            f'--b\r\nContent-Type: text/plain; charset="{charset}"\r\n\r\n'
            "plain part\r\n"
            f'--b\r\nContent-Type: text/html; charset="{charset}"\r\n\r\n'
            "<p>html part</p>\r\n--b--\r\n")


@pytest.mark.parametrize("charset", UNKNOWN)
def test_python_does_not_know_the_charset(charset: str) -> None:
    """The bite: each name raises in ``bytes.decode``."""
    with pytest.raises(LookupError):
        codecs.lookup(charset)


@pytest.mark.parametrize("charset", UNKNOWN)
@pytest.mark.parametrize(("ctype", "field"), [
    ("text/plain", "body_text"), ("text/html", "body_html")])
def test_a_single_part_mail_is_stored(charset: str, ctype: str,
                                      field: str) -> None:
    msg = _parse_imap_raw_message(_single(charset, ctype), "7", [], "")
    assert msg is not None
    assert getattr(msg, field).strip() == "hello there"
    assert msg.from_address.email == "asha@example.com"


@pytest.mark.parametrize("charset", UNKNOWN)
def test_a_multipart_mail_is_stored(charset: str) -> None:
    msg = _parse_imap_raw_message(_multipart(charset), "7", [], "")
    assert msg is not None
    assert msg.body_text.strip() == "plain part"
    assert msg.body_html.strip() == "<p>html part</p>"


def test_a_known_charset_still_decodes_by_its_name() -> None:
    raw = _single("iso-8859-1", "text/plain").replace("hello there", "caf\xe9")
    msg = _parse_imap_raw_message(raw, "7", [], "")
    assert msg is not None
    assert msg.body_text.strip() == "caf\xe9"
