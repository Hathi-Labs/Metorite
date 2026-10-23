"""Contact-card signature parsing — the card must not invent contact details.

The people card behind a sender's name reads phone/title/company out of the
sender's own signature. That parse is the only place the card guesses, so it is
pinned here from both directions: it finds what a real signature carries, and it
stays silent on order numbers, dates, and quoted replies (where the "signature"
belongs to whoever was being replied TO, not to the sender).
"""

from __future__ import annotations

import inspect

from gateway.routes.email.transport import contacts as C
from gateway.routes.email.transport.contacts import (
    _org_from_domain,
    _parse_links,
    _parse_phones,
    _parse_title_and_org,
    _preview,
    _signature_block,
)
from tests.unit._email_fakes import bind_db


class _Row:
    def __init__(self, snippet: str = "", body_text: str = ""):
        self.snippet = snippet
        self.body_text = body_text


_INTL_SIG = """Thanks, that works.

--
Priya Sharma
Head of Partnerships
Fracktal Works Pvt Ltd
Mobile: +91 98765 43210 | Office: 080-4567-8900
https://fracktal.in
"""

_US_SIG = """Best,
Alex Morgan
VP Engineering
Acme Corp
T: (415) 555-0132
www.acme.com
"""


def test_signature_block_stops_at_the_quoted_reply():
    body = _INTL_SIG + (
        "\nOn Mon, Jul 20, 2026 at 9:12 AM Alex <alex@acme.com> wrote:\n"
        "> Regards, Alex Morgan\n> Mobile: +1 415 555 0132\n"
    )
    block = _signature_block(body)
    assert "Priya Sharma" in block
    assert "Alex Morgan" not in block
    # The other person's number must not become this contact's number.
    assert _parse_phones(block) == ["+91 98765 43210", "080-4567-8900"]


def test_parses_international_signature():
    block = _signature_block(_INTL_SIG)
    assert _parse_phones(block) == ["+91 98765 43210", "080-4567-8900"]
    assert _parse_links(block) == ["https://fracktal.in"]
    assert _parse_title_and_org(block, "Priya Sharma") == (
        "Head of Partnerships", "Fracktal Works Pvt Ltd")


def test_parses_us_signature_with_parenthesised_area_code():
    block = _signature_block(_US_SIG)
    assert _parse_phones(block) == ["(415) 555-0132"]
    # A bare www. link is served href-ready.
    assert _parse_links(block) == ["https://www.acme.com"]
    assert _parse_title_and_org(block, "Alex Morgan") == ("VP Engineering", "Acme Corp")


def test_deduplicates_one_number_written_twice():
    block = _signature_block("Sam Rao\nCTO\nTel: +91 98765 43210\nWhatsApp: 9876543210\n")
    assert _parse_phones(block) == ["+91 98765 43210"]


def test_never_reads_a_phone_out_of_ids_or_dates():
    for body in (
        "Your order 20260727 has shipped. Invoice #98765432, total 12,499.00 INR.",
        "Meeting on 2026-07-27 at 14:30, room 4501.",
        "Tracking number 1Z999AA10123456784 is now active.",
    ):
        assert _parse_phones(_signature_block(body)) == [], body


def test_marketing_plumbing_is_not_the_senders_link():
    block = _signature_block(
        "Read more at https://news.example.com/a?utm_source=x\n"
        "Unsubscribe: https://click.list-manage.com/u/abc\n"
    )
    assert _parse_links(block) == []


def test_title_needs_the_name_to_anchor_on():
    # Without a display name to find in the block there is no reliable signal,
    # so the parser must decline rather than pick an arbitrary line.
    assert _parse_title_and_org(_signature_block(_US_SIG), None) == (None, None)
    assert _parse_title_and_org(_signature_block(_US_SIG), "Someone Else") == (None, None)


def test_free_mail_domain_is_never_an_organisation():
    assert _org_from_domain("gmail.com") is None
    assert _org_from_domain("proton.me") is None
    assert _org_from_domain("fracktal.in") == "Fracktal"
    assert _org_from_domain(None) is None


def test_every_query_is_scoped_to_the_callers_own_accounts():
    """The card is keyed by a bare email address, so nothing but the account
    scope stands between it and another user's mail. Every SELECT over
    email_messages must go through ``_account_scope`` (which resolves
    email_accounts.user_id), and the rollup lookups must filter on :uid too."""
    src = inspect.getsource(C.contact_card)
    assert '_account_scope(account_id, params, level="read")' in src
    # Three statements read mail/rollups; each must carry a user predicate.
    for fragment in ("FROM email_messages em\n                 WHERE {from_them}",
                     "WHERE {scope}"):
        assert fragment in src, fragment
    # Since WS-65 SM-2 the predicate comes from the one mailbox seam.
    assert src.count('mailbox_ids_sql(uid="uid", level="read")') >= 2, (
        "the email_senders / email_newsletters lookups must be scoped to the "
        "caller's accounts, not matched on the address alone"
    )
    assert "user.email or \"anonymous\"" in src


def test_the_users_own_drafts_are_not_correspondence_from_the_contact():
    """A draft the user wrote sits in their mailbox; some providers echo the
    counterparty into from_address. Counting those would inflate every stat and
    put an unsent draft on the card as if the person had sent it."""
    src = inspect.getsource(C.contact_card)
    assert "<> 'drafts'" in src


# ── the contacts directory the card writes into ─────────────────────────────


def test_a_hand_edited_field_is_never_overwritten_by_a_signature():
    """`manual_fields` is the whole basis for trusting a Contacts view: a human
    correction must outrank anything the next mail's sign-off says, forever.
    Every writable column needs its own guard — one missed column is one field
    that silently reverts."""
    sql = C._REMEMBER_CONTACT_SQL
    for col in ("display_name", "title", "organization", "phones", "links"):
        assert f"'{col}' = ANY(email_contacts.manual_fields)" in sql, col


def test_an_empty_parse_never_blanks_what_we_already_knew():
    """A one-line reply carries no signature. Writing its empty parse over the
    row would erase the phone number an earlier mail taught us — so scalars
    COALESCE and arrays are only taken when they actually contain something."""
    sql = C._REMEMBER_CONTACT_SQL
    for col in ("display_name", "title", "organization"):
        assert f"COALESCE(EXCLUDED.{col}, email_contacts.{col})" in sql, col
    for col in ("phones", "links"):
        assert f"cardinality(EXCLUDED.{col}) > 0" in sql, col


def test_derived_facts_are_not_persisted():
    """Counts and last-seen are a live query over email_messages. Freezing them
    into the contact row would be wrong the moment the next mail arrives."""
    sql = C._REMEMBER_CONTACT_SQL.lower()
    for derived in ("received", "unread", "last_seen", "first_seen", "threads"):
        assert derived not in sql, derived


def test_the_domain_guess_is_shown_but_never_stored():
    """'acme.com' → 'Acme' is a guess ABOUT the address, not something the
    person told us. It fills the card when nothing better is known; writing it
    would seed the directory with invented company names."""
    src = inspect.getsource(C.contact_card)
    remember = src.index("_remember_contact(db")
    fallback = src.index("_org_from_domain(domain)")
    assert fallback > remember, (
        "the domain fallback must be applied AFTER the directory write, or "
        "every contact on a corporate domain gets a fabricated organisation "
        "persisted the first time their card is opened"
    )


def test_a_failed_directory_write_still_serves_the_card():
    """The write is a side effect of a read. A missing table on an un-migrated
    DB, or any other failure, must cost the user nothing."""
    src = inspect.getsource(C._remember_contact)
    assert "except Exception" in src and "return None" in src
    card_src = inspect.getsource(C.contact_card)
    assert "if merged is not None:" in card_src, (
        "the card must fall back to the freshly parsed details when the "
        "directory write returned nothing"
    )


def test_preview_prefers_the_snippet_and_truncates():
    assert _preview(_Row(snippet="  hello   there \n")) == "hello there"
    assert _preview(_Row(body_text="x" * 500)).endswith("…")
    assert len(_preview(_Row(body_text="x" * 500))) == 181
    assert _preview(_Row()) == ""


# ── /contacts/suggest — recipient autocomplete ────────────────────────────────


async def test_suggest_unions_sent_contacts_and_senders_ranked() -> None:
    """The people picker draws from all three sources — people you've written
    to, the learned directory, known senders — dedup'd by address, prefix
    matches and sent-to weight first, and never offers the user's own
    connected addresses."""
    from unittest.mock import AsyncMock, MagicMock, patch

    captured: dict = {}

    class _DB:
        async def execute(self, clause, params=None):
            captured["sql"] = str(clause)
            captured["params"] = params
            row = MagicMock()
            row.email = "ayush@fracktal.in"
            row.name = "Ayush Sarkar"
            return MagicMock(fetchall=MagicMock(return_value=[row]))

        async def close(self): ...

    with patch.object(C, "_tenant_session", bind_db(_DB())):
        out = await C.suggest_contacts(
            q="Ayu", account_id="a0000000-0000-4000-8000-000000000001", limit=8,
            user=MagicMock(email="me@fracktal.in"))

    assert [(s.email, s.name) for s in out] == [("ayush@fracktal.in", "Ayush Sarkar")]
    sql, params = captured["sql"], captured["params"]
    # Case-insensitive match, prefix ranked first.
    assert params["any"] == "%ayu%" and params["pre"] == "ayu%"
    # All three candidate sources union'd, dedup'd by address.
    assert "folder, '')) = 'sent'" in sql
    assert "FROM email_contacts" in sql and "FROM email_senders" in sql
    assert "GROUP BY c.email" in sql
    # Sent-to outranks directory outranks sender; prefix hits lead.
    assert "ORDER BY prefix_hit DESC, weight DESC" in sql
    # The user's own connected addresses are never suggested.
    assert "email_address" in sql and "NOT IN" in sql
    # Scoped to the caller's accounts (and optionally one of them).
    assert "user_id = :uid" in sql and params["aid"] == "a0000000-0000-4000-8000-000000000001"


async def test_suggest_is_best_effort_on_failure() -> None:
    """A typeahead must never error the composer — [] on any failure (e.g. the
    email_contacts migration not yet applied)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    db = AsyncMock()
    db.execute.side_effect = RuntimeError("relation email_contacts missing")
    with patch.object(C, "_tenant_session", bind_db(db)):
        out = await C.suggest_contacts(
            q="ay", account_id=None, limit=8, user=MagicMock(email="me@x.io"))
    assert out == []


async def test_suggest_names_the_source_and_reads_the_org_through_the_seam() -> None:
    """Owner, 2026-10-22: the chat finds a colleague by name. Each hit names
    its source, and the org rows name the bound tenant in their own predicate,
    so an unbound GUC matches no row. The R8 half is
    ``test_email_contact_suggest_r8.py``."""
    from unittest.mock import MagicMock, patch

    captured: dict = {}
    rows = []
    for email, src in (("kiran@fracktal.in", "org"), ("k@client.test", "sent"),
                       ("k@x.test", "seen"), ("k@y.test", "bogus")):
        r = MagicMock()
        r.email, r.name, r.source = email, "Kiran", src
        rows.append(r)

    class _DB:
        async def execute(self, clause, params=None):
            captured["sql"] = str(clause)
            captured["params"] = params
            return MagicMock(fetchall=MagicMock(return_value=rows))

    with patch.object(C, "_tenant_session", bind_db(_DB())):
        out = await C.suggest_contacts(
            q="Kiran", account_id=None, limit=8, domain="@Fracktal.IN",
            user=MagicMock(email="me@fracktal.in"))

    assert [s.source for s in out] == ["org", "sent", "seen", "seen"]
    sql, params = captured["sql"], captured["params"]
    assert "FROM app_user au" in sql and "FROM people pp" in sql
    assert sql.count("current_setting('app.tenant_id', true)") == 2
    assert params["dom"] == r"(^|\.)fracktal\.in$"
    assert "c.email <> LOWER(:uid)" in sql


async def test_suggest_refuses_a_domain_that_is_not_one() -> None:
    from unittest.mock import MagicMock

    import pytest
    from fastapi import HTTPException

    for bad in ("a b", "x;y", "a..b", "fracktal.in)|("):
        with pytest.raises(HTTPException) as err:
            await C.suggest_contacts(
                q="k", account_id=None, limit=8, domain=bad,
                user=MagicMock(email="me@x.io"))
        assert err.value.status_code == 422
    assert C._domain_pattern("fracktal") == r"(^|\.)fracktal(\.|$)"
    assert C._domain_pattern(None) == ""
