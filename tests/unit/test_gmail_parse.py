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

R7 fences named here:

* ``gmail-parse-body``: the walk reads the tree at any depth, a single-part
  HTML mail fills ``body_html``, and the charset of the part decodes it.
* ``gmail-parse-message-id``: each case of the header name fills
  ``internet_message_id``, in the form of Graph.
* ``gmail-parse-addresses``: a quoted comma and a decoded comma stay inside
  one name.
* ``gmail-folder-model``: the system labels decide the folder, else
  ``archive``. A user label never sets it. ``list_messages`` keeps the folder
  of the parse, and the Archive folder pages with a query.

The R8 half is in ``test_email_rekey_reclaim.py``:
``test_two_parsed_gmail_fixtures_with_one_message_id_write_two_rows``.

Run::

    uv run pytest tests/unit/test_gmail_parse.py -v -rs
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from email_ingestion.providers.base import EmailAddress, EmailMessage
from email_ingestion.providers.gmail import (
    GMAIL_ARCHIVE_QUERY,
    GmailProvider,
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
    assert "�" not in msg.body_text + msg.body_html
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
    """Item 11. The eight fixtures exist, each has the shape of
    ``users.messages.get`` with ``format=full``, and each address is at a
    reserved example domain (RFC 2606)."""
    names = sorted(p.name for p in _FIXTURES.glob("*.json"))
    assert [n[:2] for n in names] == [
        "a_", "b_", "c_", "d_", "e_", "f_", "g_", "h_"], names
    for name in names:
        raw = _fixture(name)
        assert {"id", "threadId", "labelIds", "snippet", "payload",
                "internalDate"} <= set(raw), name
        domains = re.findall(r"[\w.+=-]+@([\w.-]+)", json.dumps(raw))
        assert domains, name
        for domain in domains:
            assert domain.endswith(_EXAMPLE_DOMAINS), (name, domain)
