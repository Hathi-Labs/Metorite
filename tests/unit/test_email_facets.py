"""Folder-scoped quick-filter facets, and ONE definition of "uncategorized".

Two things are pinned here:

1. The inbox chip row is driven by what a folder actually contains, so it can't
   offer "Cold Email" in Sent — a filter guaranteed to return nothing, whose
   empty result the user can't distinguish from a broken one.

2. "Uncategorized" means the same mail in the inbox chip and in the Email
   Cleaner's Uncategorized tab. Two definitions of it across two views of one
   mailbox is exactly the drift this package keeps paying down, so the SQL lives
   in core and both sides import it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes.email import core
from tests.unit._email_fakes import bind_db


# ── the shared vocabulary ───────────────────────────────────────────────────


def test_known_labels_cover_cleanup_and_conversation_labels() -> None:
    for cat in ("newsletter", "marketing", "receipt", "calendar",
                "notification", "cold email"):
        assert cat in core.KNOWN_LABELS_LOWER
    for conv in ("reply", "awaiting reply", "fyi", "done", "follow-up"):
        assert conv in core.KNOWN_LABELS_LOWER


def test_legacy_reply_zero_names_still_count_as_categorized() -> None:
    """Mail stamped before the Reply Zero rename must not resurface as
    uncategorized — it was categorized, the label just has an older name."""
    assert "to reply" in core.KNOWN_LABELS_LOWER
    assert "actioned" in core.KNOWN_LABELS_LOWER


def test_automation_reads_the_same_vocabulary_as_the_inbox() -> None:
    """The Email Cleaner must not keep a private copy — if these ever diverge,
    the cleaner's Uncategorized tab and the inbox's chip disagree about the
    same messages."""
    from gateway.routes.email.automation import senders

    assert senders._KNOWN_LABELS_LOWER is core.KNOWN_LABELS_LOWER


def test_uncategorized_is_not_merely_untagged() -> None:
    """It's "carries none of the RULE labels", not "has no labels at all". A
    user's own hand-made label doesn't make a message categorized, and treating
    it as such would hide exactly the mail the cleaner exists to find."""
    sql = core.UNCATEGORIZED_SQL
    assert "em.categories" in sql
    assert "em.labels" not in sql
    # Matched case/whitespace-insensitively, because a hand-edited rule can
    # store " newsletter" or "NEWSLETTER" and still mean the preset.
    assert "LOWER(TRIM(c))" in sql
    # NULL categories must still count as uncategorized, not vanish from the
    # comparison entirely.
    assert "COALESCE(em.categories" in sql


# ── the facets endpoint ─────────────────────────────────────────────────────


def _facet_db(label_rows, totals):
    class _DB:
        async def execute(self, clause, params=None):
            sql = str(clause)
            self.last_params = params
            if "GROUP BY 1" in sql:
                return MagicMock(fetchall=MagicMock(return_value=label_rows))
            return MagicMock(fetchone=MagicMock(return_value=totals))

        async def close(self): ...

    return _DB()


async def test_facets_report_counts_per_label_plus_the_two_scalars() -> None:
    from gateway.routes.email.transport.messages import message_facets

    rows = [
        MagicMock(label="newsletter", n=1204),
        MagicMock(label="reply", n=7),
    ]
    totals = MagicMock(total=5000, unread=42, uncategorized=311)

    with patch("gateway.routes.email.transport.messages._tenant_session",
               bind_db(_facet_db(rows, totals))):
        res = await message_facets(
            account_id="a0000000-0000-4000-8000-000000000001", folder="inbox",
            user=MagicMock(email="u@x.io"))

    assert res["labels"] == {"newsletter": 1204, "reply": 7}
    assert res["unread"] == 42
    assert res["uncategorized"] == 311
    assert res["total"] == 5000
    assert res["folder"] == "inbox"


async def test_a_label_with_no_mail_in_this_folder_is_simply_absent() -> None:
    """The chip row hides what isn't there. Sent has no Cold Email, so the key
    must be missing rather than reported as 0 — the UI treats absent and zero
    the same, but an explicit 0 would imply we looked and found a bucket."""
    from gateway.routes.email.transport.messages import message_facets

    with patch("gateway.routes.email.transport.messages._tenant_session",
               bind_db(_facet_db(
                   [], MagicMock(total=90, unread=0, uncategorized=90)))):
        res = await message_facets(
            account_id="a0000000-0000-4000-8000-000000000001", folder="sent",
            user=MagicMock(email="u@x.io"))

    assert res["labels"] == {}
    assert "cold email" not in res["labels"]


async def test_facets_tally_custom_rule_labels_too() -> None:
    """Every label the rules write gets a chip — the tally must not be
    restricted to the built-in vocabulary. A rule with a custom label produced
    mail that was filterable, but its chip never appeared in the inbox row."""
    from gateway.routes.email.transport.messages import message_facets

    captured: list[str] = []

    class _DB:
        async def execute(self, clause, params=None):
            sql = str(clause)
            captured.append(sql)
            if "GROUP BY 1" in sql:
                return MagicMock(fetchall=MagicMock(return_value=[
                    MagicMock(label="supplier updates", n=12)]))
            return MagicMock(fetchone=MagicMock(return_value=MagicMock(
                total=12, unread=0, uncategorized=0)))

        async def close(self): ...

    with patch("gateway.routes.email.transport.messages._tenant_session",
               bind_db(_DB())):
        res = await message_facets(
            account_id="a0000000-0000-4000-8000-000000000001", folder="inbox",
            user=MagicMock(email="u@x.io"))

    label_sql = next(s for s in captured if "GROUP BY 1" in s)
    assert "ANY(:known_labels)" not in label_sql  # no allowlist on the tally
    # Busiest-first and capped, so a runaway AI-resolved label can't flood the
    # chip row.
    assert "ORDER BY n DESC" in label_sql and "LIMIT 40" in label_sql
    assert res["labels"] == {"supplier updates": 12}


async def test_facets_bind_the_shared_label_vocabulary() -> None:
    """The uncategorized count must be computed from the same list the filter
    uses, or the chip would advertise a number the filter can't reproduce."""
    from gateway.routes.email.transport.messages import message_facets

    db = _facet_db([], MagicMock(total=1, unread=0, uncategorized=1))
    with patch("gateway.routes.email.transport.messages._tenant_session",
               bind_db(db)):
        await message_facets(account_id="a0000000-0000-4000-8000-000000000001", folder="inbox",
                             user=MagicMock(email="u@x.io"))

    assert db.last_params["known_labels"] is core.KNOWN_LABELS_LOWER
