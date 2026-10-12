"""WS-17 EM-G2 — the parse and the folder model of Gmail (D-EM-33).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.2, decision
D-EM-33 and question O-GM-1 in §12.2, defects GM-1 and GM-3 to GM-7 in §12.1.

``GmailProvider.get_message`` is the one caller of ``_parse_gmail_message``.
``list_messages`` reaches the parse through it, and so do the sweep, the deep
sync, the body backfill and the four gateway reads. So each test here drives
``get_message`` or ``list_messages`` through a fake HTTP client that answers
from the fixtures.

The fixtures in ``tests/unit/fixtures/gmail/`` have the shape of
``users.messages.get`` with ``format=full``, with base64url bodies. They hold
no real address and no real content.

- (a) ``a_mixed_alternative_pdf.json``: ``multipart/mixed`` with a nested
  ``multipart/alternative`` (text and HTML) and a PDF.
- (b) ``b_single_part_html.json``: one HTML part and no text part.
- (c) ``c_related_inline_image.json``: ``multipart/related`` with an inline
  image.
- (d) ``d_message_id_mixed_case.json``: a ``Message-Id`` header in mixed case.
- (e) ``e_quoted_comma_name.json``: the name ``"Doe, John"``.
- (f) ``f_user_label_only.json``: one user label and no system label.
- (g) ``g_iso_8859_1_body.json``: an ISO-8859-1 body.
- (h) ``h_rfc2047_display_name.json``: RFC 2047 display names. One decoded
  name holds a comma with no quotes.
- (i) ``i_forwarded_rfc822.json``: a forward as an inline ``message/rfc822``
  part with no file name (review round 1).
- (j) ``j_text_part_by_attachment_id.json``: a large HTML part that Gmail
  sends by ``body.attachmentId`` with no ``body.data``, and a ``notes.txt``
  file (EM-G2-f1, §12.3.12.3).

R7 fences named here:

* ``gmail-parse-body``: the walk reads the tree at any depth, a single-part
  HTML mail fills ``body_html``, and the charset of the part decodes it. An
  attached mail and a text part with the disposition ``attachment`` never
  become the body. A codec that raises falls back to UTF-8.
* ``gmail-parse-message-id``: each case of the header name fills
  ``internet_message_id``, in the form of Graph.
* ``gmail-parse-addresses``: a quoted comma and a decoded comma stay inside
  one name. A comma outside quotes keeps the real sender and each real
  recipient. A loose list keeps each real address.
* ``gmail-folder-model``: the system labels decide the folder, else
  ``archive``. A user label never sets it. ``list_messages`` keeps the folder
  of the parse, and the Archive folder pages with a query.
* ``gmail-body-part-fill`` (EM-G2-f1): ``get_message`` fetches a body part
  that comes by ``attachmentId`` once, and never a file. A failed fetch
  leaves that body empty and one record. A spent rate limit raises. These
  cases drive the real client on ``httpx.MockTransport``.

The R8 half is in ``test_email_rekey_reclaim.py``:
``test_two_parsed_gmail_fixtures_with_one_message_id_write_two_rows``.

Run::

    uv run pytest tests/unit/test_gmail_parse.py -v -rs
"""
from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import email_ingestion.providers.gmail as gmail_mod
import httpx
import pytest
from email_ingestion.persist import MAX_BODY_HTML_BYTES, MAX_BODY_TEXT_BYTES
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import EmailAddress, EmailMessage
from email_ingestion.providers.gmail import (
    GMAIL_ARCHIVE_QUERY,
    GMAIL_FILL_MAX_BYTES,
    GMAIL_MAX_TRIES,
    GmailProvider,
    GmailRateLimited,
    _gmail_folder_from_labels,
    _gmail_part_charset,
)
from email_ingestion.providers.outlook import OutlookProvider

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gmail"

#: The domains that RFC 2606 reserves. Each fixture address uses one.
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")

#: The Message-ID of fixture (d), in the case in which the sender wrote it.
_D_MESSAGE_ID = "<5F2C9A1B-77D3-4E0A-9C1B-AbCdEf012345@example.com>"

#: The label list of the fake mailbox: two system labels and two user labels.
#: One user label has the name Archive, as transport/folders.py can meet.
_LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "SENT", "name": "SENT", "type": "system"},
    {"id": "Label_4815162342", "name": "Projects", "type": "user"},
    {"id": "Label_77", "name": "Archive", "type": "user"},
]


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _set_header(raw: dict[str, Any], old_name: str, new_name: str,
                value: str | None = None) -> None:
    """Rename one top-level header, and set its value when one is given."""
    for header in raw["payload"]["headers"]:
        if header["name"] == old_name:
            header["name"] = new_name
            if value is not None:
                header["value"] = value
            return
    raise AssertionError(f"the fixture has no {old_name} header")


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.is_success = True

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeGmail:
    """The three Gmail reads of a parse and of a list page.

    ``GET /users/me/labels`` answers ``_LABELS``. ``GET /users/me/messages``
    records its query and lists ``listed``. ``GET /users/me/messages/<id>``
    answers the raw message of that id, and only with ``format=full``.
    """

    def __init__(self, full: dict[str, dict[str, Any]],
                 listed: list[str] | None = None) -> None:
        self.full = full
        self.listed = listed if listed is not None else list(full)
        self.list_params: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None):
        if path == "/users/me/labels":
            return _Resp({"labels": _LABELS})
        if path == "/users/me/messages":
            self.list_params.append(dict(params or {}))
            return _Resp({"messages": [{"id": i, "threadId": i}
                                       for i in self.listed],
                          "resultSizeEstimate": len(self.listed)})
        prefix = "/users/me/messages/"
        if path.startswith(prefix):
            assert params == {"format": "full"}, params
            return _Resp(self.full[path[len(prefix):]])
        raise AssertionError(f"unexpected GET {path}")


def _provider(fake: _FakeGmail) -> GmailProvider:
    provider = GmailProvider({"access_token": "x", "refresh_token": "y"})
    provider._get_client = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    return provider


async def _parse_raw(raw: dict[str, Any]) -> EmailMessage:
    """The real parse, through ``get_message``, the one caller."""
    fake = _FakeGmail({raw["id"]: raw})
    return await _provider(fake).get_message(raw["id"])


async def _parse(name: str) -> EmailMessage:
    return await _parse_raw(_fixture(name))


# ── gmail-parse-body (items 1 to 3, GM-3) ────────────────────────────────────


@pytest.mark.parametrize(("name", "text_has", "html_has", "files"), [
    ("a_mixed_alternative_pdf.json", "The signed quote is attached.",
     "<div>The signed quote is attached.</div>", ["quote-signed.pdf"]),
    ("c_related_inline_image.json", "Please find the new layout below.",
     'src="cid:ii_m1layout0"', []),
], ids=["a-mixed-alternative-pdf", "c-related-inline-image"])
async def test_a_nested_alternative_gives_text_and_html(
    name: str, text_has: str, html_has: str, files: list[str],
) -> None:
    """GM-3. Any mail with an attachment or an inline image has the body one
    level down. The old parse read the top level only, so both bodies stayed
    empty, and the rules, Reply Zero, the drafter and the embeddings read
    nothing. The walk also keeps the file list: a PDF is a file, an inline
    image is not."""
    msg = await _parse(name)
    assert text_has in msg.body_text
    assert "<div" not in msg.body_text, "the text body holds the HTML part"
    assert msg.body_html is not None and html_has in msg.body_html
    assert [a.filename for a in msg.attachments] == files
    assert msg.has_attachments is bool(files)


async def test_single_part_html_fills_body_html() -> None:
    """Item 2. A single-part HTML mail fills ``body_html`` and leaves
    ``body_text`` empty, as the Outlook parse does. ``body_backfill`` derives
    the text later. The old parse put the HTML into ``body_text``."""
    msg = await _parse("b_single_part_html.json")
    assert msg.body_text == ""
    assert msg.body_html is not None
    assert msg.body_html.startswith(
        "<html><body><p>Your invoice <b>INV-1042</b> is ready.</p>")


async def test_the_charset_of_the_part_decodes_the_body() -> None:
    """Item 3. Each part decodes with the ``charset`` of its own
    ``Content-Type``. A decode as UTF-8 turns each accented letter of an
    ISO-8859-1 body into U+FFFD."""
    msg = await _parse("g_iso_8859_1_body.json")
    assert "Le café est prêt. À bientôt." in msg.body_text
    assert msg.body_html is not None
    assert "Le café est prêt. À bientôt." in msg.body_html
    assert "\ufffd" not in msg.body_text + msg.body_html
    # A part that names no charset, or an unknown one, decodes as UTF-8.
    assert _gmail_part_charset({"headers": []}) == "utf-8"
    assert _gmail_part_charset({"headers": [
        {"name": "content-type", "value": "text/plain"}]}) == "utf-8"
    raw = _fixture("g_iso_8859_1_body.json")
    text_part = raw["payload"]["parts"][0]
    text_part["headers"][0]["value"] = 'text/plain; charset="x-no-such-cs"'
    text_part["body"]["data"] = "Q2Fmw6k"  # base64url of "Café" in UTF-8, no padding
    unknown = await _parse_raw(raw)
    assert unknown.body_text == "Café"


@pytest.mark.parametrize("charset", ["idna", "punycode", "undefined"])
async def test_a_codec_that_raises_decodes_as_utf8(charset: str) -> None:
    """Review round 1, P3a. A sender picks the charset. These three are real
    codecs, and each raises ``UnicodeError`` even with ``errors="replace"``.
    The raise failed the parse, and ``list_messages`` then skipped the mail
    at each sync. So a sender could hide a mail from Metorite."""
    raw = _fixture("g_iso_8859_1_body.json")
    for part in raw["payload"]["parts"]:
        part["headers"][0]["value"] = f"{part['mimeType']}; charset={charset}"
    msg = await _parse_raw(raw)
    assert msg.body_text.startswith("Bonjour,")
    assert msg.body_html is not None
    assert msg.body_html.startswith("<p>Bonjour,</p>")
    fake = _FakeGmail({raw["id"]: raw})
    listed, _ = await _provider(fake).list_messages(folder="INBOX")
    assert [m.provider_message_id for m in listed] == [raw["id"]]


async def test_an_attached_mail_never_becomes_the_body() -> None:
    """Review round 1, P3c. Fixture (i) forwards a mail as an inline
    ``message/rfc822`` part with no file name. The outer mail has a text part
    only. The walk must not read into the attached mail, or the HTML of the
    attached mail becomes the HTML body of the outer mail."""
    msg = await _parse("i_forwarded_rfc822.json")
    assert msg.body_text == "See the mail below.\r\n\r\nRavi\r\n"
    assert msg.body_html is None
    assert msg.attachments == []


async def test_a_text_part_with_attachment_disposition_is_not_the_body() -> None:
    """Review round 1, P3c. A ``text/plain`` part with the disposition
    ``attachment`` and no file name is a file, such as a log. It must not
    become the text body of an HTML mail."""
    raw = _fixture("b_single_part_html.json")
    single = raw["payload"]
    log_text = b"build.log line 1\n"
    raw["payload"] = {
        "partId": "", "mimeType": "multipart/mixed", "filename": "",
        "headers": [h for h in single["headers"]
                    if h["name"] != "Content-Type"] + [
            {"name": "Content-Type",
             "value": 'multipart/mixed; boundary="b_log"'}],
        "body": {"size": 0},
        "parts": [
            {"partId": "0", "mimeType": "text/html", "filename": "",
             "headers": [{"name": "Content-Type",
                          "value": "text/html; charset=UTF-8"}],
             "body": single["body"]},
            {"partId": "1", "mimeType": "text/plain", "filename": "",
             "headers": [{"name": "Content-Type",
                          "value": "text/plain; charset=UTF-8"},
                         {"name": "Content-Disposition",
                          "value": "attachment"}],
             "body": {"size": len(log_text),
                      "data": base64.urlsafe_b64encode(log_text).decode()}},
        ],
    }
    msg = await _parse_raw(raw)
    assert msg.body_text == ""
    assert msg.body_html is not None
    assert msg.body_html.startswith("<html><body><p>Your invoice")


# ── gmail-parse-message-id (items 4 and 5, GM-1, E7) ────────────────────────


@pytest.mark.parametrize("header", ["Message-ID", "Message-Id", "message-id"])
async def test_message_id_reads_in_any_case(header: str) -> None:
    """GM-1. The old header read was case-sensitive, and the parse set no
    ``internet_message_id`` at all. A header name has no case (RFC 5322), and
    senders write each of these three forms."""
    raw = _fixture("d_message_id_mixed_case.json")
    _set_header(raw, "Message-Id", header)
    msg = await _parse_raw(raw)
    assert msg.internet_message_id == _D_MESSAGE_ID


async def test_message_id_keeps_the_form_of_graph() -> None:
    """Item 5. Graph gives ``internetMessageId`` as it came, with its angle
    brackets and its case, and ``automation/identity.py`` compares the column
    with ``=``. So the Gmail parse trims the value and changes nothing else.
    One mail in a Gmail and an Outlook mailbox then has one value."""
    raw = _fixture("d_message_id_mixed_case.json")
    _set_header(raw, "Message-Id", "Message-Id", f"  {_D_MESSAGE_ID}\t")
    gmail = await _parse_raw(raw)
    graph = OutlookProvider({"access_token": "x"})._parse_graph_message({
        "id": "AAMkAGI2-fixture-graph-1",
        "internetMessageId": _D_MESSAGE_ID,
        "body": {"contentType": "text", "content": "Confirmed."},
    })
    assert gmail.internet_message_id == _D_MESSAGE_ID
    assert gmail.internet_message_id == graph.internet_message_id
    # No header is no value, never an empty string.
    raw = _fixture("d_message_id_mixed_case.json")
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"]
                                 if h["name"] != "Message-Id"]
    assert (await _parse_raw(raw)).internet_message_id is None


# ── gmail-parse-addresses (item 6, GM-4, E6) ─────────────────────────────────


async def test_a_quoted_comma_is_one_address() -> None:
    """GM-4. The old parse split each address header on each comma, so
    ``"Doe, John" <john.doe@example.org>`` became two broken addresses."""
    msg = await _parse("e_quoted_comma_name.json")
    assert msg.from_address == EmailAddress(
        name="Doe, John", email="john.doe@example.org")
    assert msg.to_addresses == [
        EmailAddress(name="Rao, Asha", email="asha@example.com"),
        EmailAddress(name="", email="ops@example.com"),
    ]
    assert msg.cc_addresses == [
        EmailAddress(name="Team, Ops", email="ops-team@example.org")]


async def test_an_encoded_display_name_decodes() -> None:
    """Item 6 (E6). An RFC 2047 encoded word decodes. The decode runs AFTER
    the split of the raw header, because a decoded name can hold a comma with
    no quotes. A decode before the split gives three addresses for the two
    in ``To``."""
    msg = await _parse("h_rfc2047_display_name.json")
    assert msg.from_address == EmailAddress(
        name="Müller, Jürgen", email="juergen.mueller@example.net")
    assert msg.to_addresses == [
        EmailAddress(name="André Dupont", email="andre@example.com"),
        EmailAddress(name="Rao, Asha", email="asha@example.com"),
    ]


def test_an_address_header_with_no_address_gives_no_entry() -> None:
    """A group with no member, or an empty header, gives no address. An
    entry with an empty address would reach the recipient lists."""
    assert GmailProvider._parse_address_list("undisclosed-recipients:;") == []
    assert GmailProvider._parse_address_list("") == []


@pytest.mark.parametrize(("header", "name", "email"), [
    ("Doe, John <john.doe@example.org>", "Doe, John", "john.doe@example.org"),
    ("Müller, Jürgen <juergen@example.net>", "Müller, Jürgen",
     "juergen@example.net"),
], ids=["ascii", "non-ascii"])
async def test_a_bare_comma_in_from_keeps_the_real_sender(
    header: str, name: str, email: str,
) -> None:
    """Review round 1, P2. Main read ``Doe, John <john.doe@example.org>`` as
    the name ``Doe, John`` at that address. The first build read the fragment
    ``Doe`` as the sender. A first entry with no ``@`` now falls back to the
    old reading. Gmail sends the non-ASCII form if it decodes an encoded word
    (an EM-G10 check)."""
    raw = _fixture("e_quoted_comma_name.json")
    _set_header(raw, "From", "From", header)
    msg = await _parse_raw(raw)
    assert msg.from_address == EmailAddress(name=name, email=email)


@pytest.mark.parametrize(("header", "emails"), [
    ("Doe, John <john.doe@example.org>, ops@example.com",
     ["john.doe@example.org", "ops@example.com"]),
    ("Müller, Jürgen <juergen@example.net>, Rao, Asha <asha@example.com>",
     ["juergen@example.net", "asha@example.com"]),
], ids=["ascii", "non-ascii"])
async def test_a_bare_comma_in_to_keeps_each_real_address(
    header: str, emails: list[str],
) -> None:
    """Review round 1, P2. A comma outside quotes splits off a fragment with
    no ``@``. The fragment never becomes a recipient, and each real address
    stays."""
    raw = _fixture("e_quoted_comma_name.json")
    _set_header(raw, "To", "To", header)
    msg = await _parse_raw(raw)
    assert [a.email for a in msg.to_addresses] == emails


@pytest.mark.parametrize("header", [
    "a@example.org, b@example.org,",
    "a@example.org; b@example.org",
    "a@example.org,, b@example.org",
], ids=["trailing-comma", "semicolon", "empty-element"])
async def test_a_loose_address_list_keeps_each_real_address(header: str) -> None:
    """Review round 1, P3b. The strict ``getaddresses`` of this Python
    refuses a whole header for one defect, so a trailing comma gave no
    recipient at all. Main kept both addresses, and so does the parse now."""
    raw = _fixture("e_quoted_comma_name.json")
    _set_header(raw, "Cc", "Cc", header)
    msg = await _parse_raw(raw)
    assert [a.email for a in msg.cc_addresses] == [
        "a@example.org", "b@example.org"]


# ── gmail-folder-model (items 7 to 10, GM-5 to GM-7, D-EM-33) ────────────────


async def test_no_system_label_files_as_archive() -> None:
    """GM-5. A Gmail message with no system label is archived mail. The old
    parse filed it as ``inbox``, so archived mail showed in the Inbox."""
    msg = await _parse("f_user_label_only.json")
    assert msg.folder == "archive"
    assert _gmail_folder_from_labels([]) == "archive"
    assert _gmail_folder_from_labels(
        ["CATEGORY_UPDATES", "IMPORTANT", "STARRED", "UNREAD"]) == "archive"


def test_the_system_labels_decide_in_order() -> None:
    """D-EM-33: ``TRASH``, ``SPAM``, ``DRAFT``, ``SENT``, ``INBOX``."""
    order = ["TRASH", "SPAM", "DRAFT", "SENT", "INBOX"]
    folders = ["trash", "junk", "drafts", "sent", "inbox"]
    for i, folder in enumerate(folders):
        assert _gmail_folder_from_labels(order[i:]) == folder, order[i:]


async def test_a_user_label_never_sets_the_folder() -> None:
    """O-GM-1. A user label is a label, as an Outlook category is. It goes
    to ``categories`` and never to ``folder``, also when its name is the name
    of a folder."""
    msg = await _parse("f_user_label_only.json")
    assert (msg.folder, msg.categories) == ("archive", ["Projects"])
    raw = _fixture("f_user_label_only.json")
    raw["labelIds"] = ["Label_77", "INBOX"]
    msg = await _parse_raw(raw)
    assert (msg.folder, msg.categories) == ("inbox", ["Archive"])
    raw["labelIds"] = ["Label_77"]
    msg = await _parse_raw(raw)
    assert (msg.folder, msg.categories) == ("archive", ["Archive"])


async def test_a_label_page_keeps_the_folder_of_the_parse() -> None:
    """GM-7, item 8. "Load older" in a user-label view sends the folder key
    of the view as ``canonical_override``. The old ``list_messages`` filed
    each message of the page under that key, so Inbox mail left the Inbox.
    Now each message keeps the folder of its parse."""
    in_inbox = _fixture("f_user_label_only.json")
    in_inbox["id"] = in_inbox["threadId"] = "m-in-inbox"
    in_inbox["labelIds"] = ["Label_4815162342", "INBOX"]
    archived = _fixture("f_user_label_only.json")
    archived["id"] = archived["threadId"] = "m-archived"
    fake = _FakeGmail({"m-in-inbox": in_inbox, "m-archived": archived})
    msgs, token = await _provider(fake).list_messages(
        folder="Label_4815162342", max_results=100, page_token=None,
        canonical_override="projects")
    assert token is None
    assert [(m.provider_message_id, m.folder) for m in msgs] == [
        ("m-in-inbox", "inbox"), ("m-archived", "archive")]
    assert [m.categories for m in msgs] == [["Projects"], ["Projects"]]
    assert fake.list_params == [
        {"maxResults": 100, "labelIds": ["Label_4815162342"]}]


async def test_the_archive_page_sends_a_query_and_no_label() -> None:
    """GM-6, item 9. Gmail has no ``archive`` label, and it refuses
    ``labelIds=["archive"]``. The Archive folder pages with a query, and the
    query of the caller joins it."""
    archived = _fixture("f_user_label_only.json")
    fake = _FakeGmail({archived["id"]: archived})
    provider = _provider(fake)
    msgs, _ = await provider.list_messages(
        folder="archive", max_results=100, page_token=None,
        canonical_override="archive")
    await provider.list_messages(folder="Archive", query="after:2024/09/01")
    assert fake.list_params == [
        {"maxResults": 100, "q": GMAIL_ARCHIVE_QUERY},
        {"maxResults": 50, "q": f"{GMAIL_ARCHIVE_QUERY} after:2024/09/01"},
    ]
    assert GMAIL_ARCHIVE_QUERY == "-in:inbox -in:sent -in:drafts"
    assert [m.folder for m in msgs] == ["archive"]


async def test_a_user_label_named_archive_does_not_replace_the_query() -> None:
    """Item 10. ``transport/folders.py`` maps the key ``archive`` to the id
    of a label whose name is Archive, and passes ``canonical_override`` of
    ``archive``. The provider still sends the query, so such a label never
    takes the place of the Archive folder. These are the arguments of that
    route."""
    archived = _fixture("f_user_label_only.json")
    fake = _FakeGmail({archived["id"]: archived})
    msgs, _ = await _provider(fake).list_messages(
        folder="Label_77", max_results=100, page_token=None,
        canonical_override="archive")
    assert fake.list_params == [{"maxResults": 100, "q": GMAIL_ARCHIVE_QUERY}]
    assert [m.folder for m in msgs] == ["archive"]


def test_each_fixture_is_a_gmail_message_with_no_real_address() -> None:
    """Item 11. The ten fixtures exist, each has the shape of
    ``users.messages.get`` with ``format=full``, and each address is at a
    reserved example domain (RFC 2606). EM-G2-f1 added (j)."""
    names = sorted(p.name for p in _FIXTURES.glob("*.json"))
    assert [n[:2] for n in names] == [
        "a_", "b_", "c_", "d_", "e_", "f_", "g_", "h_", "i_", "j_"], names
    for name in names:
        raw = _fixture(name)
        assert {"id", "threadId", "labelIds", "snippet", "payload",
                "internalDate"} <= set(raw), name
        domains = re.findall(r"[\w.+=-]+@([\w.-]+)", json.dumps(raw))
        assert domains, name
        for domain in domains:
            assert domain.endswith(_EXAMPLE_DOMAINS), (name, domain)


# ── gmail-body-part-fill (EM-G2-f1, §12.3.12.3) ─────────────────────────────

_API = "/gmail/v1/users/me"
_LOGGER = "email_ingestion.providers.gmail"
#: The part of fixture (j) that Gmail sends by ``attachmentId``.
_J_HTML_PART = "ANGjdJ_fixture_body_part_html"
#: The HTML of that part. It is long, as a part that Gmail does not inline is.
_J_HTML = ("<html><body>" + "<p>One row of the weekly report.</p>" * 2000
           + "</body></html>")

#: One answer of the fake. It makes a new response at each call.
_Answer = Callable[[], httpx.Response]


def _json(body: dict[str, Any], status: int = 200) -> _Answer:
    return lambda: httpx.Response(status, json=body)


def _part_answer(data: bytes) -> _Answer:
    """The answer of ``users.messages.attachments.get``, in base64url."""
    return _json({"size": len(data),
                  "data": base64.urlsafe_b64encode(data).decode()})


class _Wire:
    """One fake Gmail API on ``httpx.MockTransport``.

    ``on`` gives a path under ``/users/me`` its answers, in order. The last
    answer repeats. ``seen`` records the path of each API request. The probe
    of ``authenticate`` answers and is not recorded."""

    def __init__(self) -> None:
        self.answers: dict[str, list[_Answer]] = {
            "/labels": [_json({"labels": []})]}
        self.seen: list[str] = []

    def on(self, path: str, *answers: _Answer) -> None:
        self.answers[path] = list(answers)

    def attachment_gets(self) -> list[str]:
        return [p for p in self.seen if "/attachments/" in p]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(_API)
        if path == "/profile":
            return httpx.Response(200, json={"historyId": "1"})
        self.seen.append(path)
        answers = self.answers.get(path)
        if not answers:
            return httpx.Response(404, json={"error": {"code": 404}})
        return (answers.pop(0) if len(answers) > 1 else answers[0])()


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    """Send each ``httpx.AsyncClient`` of the provider to one fake. The real
    ``_get_client`` builds the client, so ``GmailBearer`` and its rate-limit
    rule apply. ``_wait`` does not sleep."""
    service = _Wire()
    transport = httpx.MockTransport(service.handle)
    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(gmail_mod, "_wait", _no_sleep)
    return service


def _wired_provider() -> GmailProvider:
    return GmailProvider({"access_token": "at-1", "refresh_token": "r-1"},
                         app=OAuthApp(client_id="cid", client_secret="secret"))


def _j_on_the_wire(wire: _Wire, part_answer: _Answer) -> tuple[str, str]:
    """Put fixture (j) and the answer for its HTML part on *wire*. Returns
    the message id and the path of the part fetch."""
    raw = _fixture("j_text_part_by_attachment_id.json")
    mid = raw["id"]
    wire.on(f"/messages/{mid}", _json(raw))
    part_path = f"/messages/{mid}/attachments/{_J_HTML_PART}"
    wire.on(part_path, part_answer)
    return mid, part_path


async def test_a_text_part_by_attachment_id_fills_the_body(wire: _Wire) -> None:
    """EM-G2-f1 acceptance 1, mutation M1. Gmail sends a large body part by
    ``body.attachmentId`` with no ``body.data``. ``get_message`` fetches it
    once, and the parse gives the full HTML. The ``notes.txt`` file of the
    fixture is a ``text/plain`` part by ``attachmentId`` too, and it gets no
    fetch."""
    mid, part_path = _j_on_the_wire(wire, _part_answer(_J_HTML.encode()))

    msg = await _wired_provider().get_message(mid)

    assert msg.body_html == _J_HTML
    assert msg.body_text == (
        "Hi Asha,\r\n\r\nThe full report is in the HTML part.\r\n\r\nRavi\r\n")
    assert wire.attachment_gets() == [part_path]
    assert [a.filename for a in msg.attachments] == ["notes.txt"]


async def test_a_filled_part_keeps_its_charset(wire: _Wire) -> None:
    """EM-G2-f1 acceptance 2, D-G2-f1-4. The filled bytes decode with the
    charset of the part, as inline data does. A decode as UTF-8 turns each
    accented letter of an ISO-8859-1 part into U+FFFD."""
    html = "<p>Bonjour,</p><p>Le café est prêt. À bientôt.</p>"
    raw = _fixture("j_text_part_by_attachment_id.json")
    html_part = raw["payload"]["parts"][0]["parts"][1]
    html_part["headers"][0]["value"] = 'text/html; charset="ISO-8859-1"'
    mid = raw["id"]
    wire.on(f"/messages/{mid}", _json(raw))
    wire.on(f"/messages/{mid}/attachments/{_J_HTML_PART}",
            _part_answer(html.encode("iso-8859-1")))

    msg = await _wired_provider().get_message(mid)

    assert msg.body_html == html
    assert "�" not in msg.body_html
    assert len(wire.attachment_gets()) == 1


async def test_a_file_part_is_never_fetched_for_the_body(wire: _Wire) -> None:
    """EM-G2-f1 acceptance 3, mutation M2, D-G2-f1-3. Fixture (a) has a PDF
    and an inline text body. The test adds a ``text/plain`` file, and an
    attached mail whose HTML comes by ``attachmentId``. A file can be 25 MB,
    and the body needs none of its bytes, so no part gets a fetch."""
    raw = _fixture("a_mixed_alternative_pdf.json")
    raw["payload"]["parts"].append({
        "partId": "2", "mimeType": "text/plain", "filename": "notes.txt",
        "headers": [{"name": "Content-Disposition",
                     "value": 'attachment; filename="notes.txt"'}],
        "body": {"attachmentId": "ANGjdJ_fixture_attachment_notes_txt",
                 "size": 1834}})
    raw["payload"]["parts"].append({
        "partId": "3", "mimeType": "message/rfc822", "filename": "",
        "headers": [], "body": {"size": 0},
        "parts": [{"partId": "3.0", "mimeType": "text/html", "filename": "",
                   "headers": [{"name": "Content-Type",
                                "value": 'text/html; charset="UTF-8"'}],
                   "body": {"attachmentId": "ANGjdJ_fixture_inner_html",
                            "size": 2457600}}]})
    del raw["payload"]["parts"][0]["parts"][1]  # no HTML body of its own
    mid = raw["id"]
    wire.on(f"/messages/{mid}", _json(raw))

    msg = await _wired_provider().get_message(mid)

    assert wire.attachment_gets() == []
    assert "The signed quote is attached." in msg.body_text
    assert msg.body_html is None
    assert [a.filename for a in msg.attachments] == [
        "quote-signed.pdf", "notes.txt"]


async def test_a_failed_body_part_fetch_leaves_a_record_and_an_empty_body(
        wire: _Wire, caplog: pytest.LogCaptureFixture) -> None:
    """EM-G2-f1 acceptance 4, D-G2-f1-5. A 500 on the part fetch leaves that
    body empty, and the text body still parses. The fill logs one
    ``gmail.body_part_failed`` line and adds one record. On the history
    path, ``_fetch_into`` puts the record into ``SyncResult.errors``. The
    record names no subject and no address."""
    mid, part_path = _j_on_the_wire(
        wire, _json({"error": {"code": 500, "message": "backendError"}}, 500))
    record = (f"gmail.body_part_failed id={mid} part=0.1 "
              "error=HTTPStatusError status=500")
    p = _wired_provider()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        msg = await p.get_message(mid)

    assert msg.body_html is None
    assert msg.body_text.startswith("Hi Asha,")
    assert wire.attachment_gets() == [part_path]
    assert p.fetch_failures == [record]
    logged = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("gmail.body_part_failed")]
    assert logged == [f"gmail.body_part_failed message_id={mid} part_id=0.1 "
                      "error=HTTPStatusError status=500"]
    for text in [*p.fetch_failures, *logged]:
        assert "Weekly" not in text and "@" not in text

    wire.on("/history", _json({
        "historyId": "120",
        "history": [{"id": "110",
                     "messagesAdded": [{"message": {"id": mid}}]}]}))
    result = await _wired_provider().sync_messages(history_id="100")

    assert [m.provider_message_id for m in result.messages] == [mid]
    assert result.messages[0].body_html is None
    assert result.errors == [record]
    assert result.new_history_id == "120"


async def test_a_rate_limit_on_the_body_part_fetch_raises(wire: _Wire) -> None:
    """EM-G2-f1 acceptance 5, mutation M3, D-G2-f1-6. The fill does not
    catch a spent rate limit. ``get_message`` raises it, so each caller
    handles it as EM-G4a says. The fill leaves no record of it."""
    mid, part_path = _j_on_the_wire(wire, _json(
        {"error": {"code": 429, "errors": [{"reason": "rateLimitExceeded"}]}},
        429))
    p = _wired_provider()

    with pytest.raises(GmailRateLimited):
        await p.get_message(mid)

    assert wire.attachment_gets() == [part_path] * GMAIL_MAX_TRIES
    assert p.fetch_failures == []


async def test_only_the_first_body_part_of_each_type_is_fetched(
        wire: _Wire) -> None:
    """Review P2, D-G2-f1-7, mutation M5. The parse reads only the first
    ``text/html`` part, so a second large part by ``attachmentId`` gets no
    fetch. One message gets two fetches at most, so a few large mails
    cannot hold their parts in the memory of acb-gateway several times."""
    raw = _fixture("j_text_part_by_attachment_id.json")
    raw["payload"]["parts"].insert(1, {
        "partId": "2", "mimeType": "text/html", "filename": "",
        "headers": [{"name": "Content-Type",
                     "value": 'text/html; charset="UTF-8"'}],
        "body": {"attachmentId": "ANGjdJ_fixture_second_html",
                 "size": 3_000_000}})
    mid = raw["id"]
    wire.on(f"/messages/{mid}", _json(raw))
    first = f"/messages/{mid}/attachments/{_J_HTML_PART}"
    wire.on(first, _part_answer(_J_HTML.encode()))
    wire.on(f"/messages/{mid}/attachments/ANGjdJ_fixture_second_html",
            _part_answer(b"<p>The second part.</p>"))

    msg = await _wired_provider().get_message(mid)

    assert wire.attachment_gets() == [first]
    assert msg.body_html == _J_HTML


async def test_a_body_part_over_the_cap_is_not_fetched(
        wire: _Wire, caplog: pytest.LogCaptureFixture) -> None:
    """Review P2, D-G2-f1-7, mutation M6. A part over
    ``GMAIL_FILL_MAX_BYTES`` gets no fetch. It logs
    ``gmail.body_part_too_large`` and leaves one record, and its body stays
    empty. The text body still parses. The cap comes from the storage caps
    of ``persist``, and it is above both of them."""
    storage_cap = max(MAX_BODY_TEXT_BYTES, MAX_BODY_HTML_BYTES)
    assert storage_cap * 2 == GMAIL_FILL_MAX_BYTES == 4 * 1024 * 1024
    raw = _fixture("j_text_part_by_attachment_id.json")
    size = GMAIL_FILL_MAX_BYTES + 1
    raw["payload"]["parts"][0]["parts"][1]["body"]["size"] = size
    mid = raw["id"]
    wire.on(f"/messages/{mid}", _json(raw))
    wire.on(f"/messages/{mid}/attachments/{_J_HTML_PART}",
            _part_answer(_J_HTML.encode()))
    p = _wired_provider()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        msg = await p.get_message(mid)

    assert wire.attachment_gets() == []
    assert msg.body_html is None
    assert msg.body_text.startswith("Hi Asha,")
    assert p.fetch_failures == [
        f"gmail.body_part_too_large id={mid} part=0.1 size={size}"]
    logged = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("gmail.body_part_too_large")]
    assert logged == [f"gmail.body_part_too_large message_id={mid} "
                      f"part_id=0.1 size={size}"]
