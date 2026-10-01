"""Bulk actions at mailbox scale.

The Email Cleaner used to cap a bulk action at 1000 messages: "archive
everything from this sender" quietly stopped, reported success, and left the
rest sitting there. The cap was never a database limit — one set-based UPDATE
costs the same at 50 rows or 50,000 — it was the PROVIDER, which the reconciler
walked one HTTP call per message.

These tests pin the replacement: providers collapse the work into batches, one
failure never strands the rest, and the id re-keys Outlook hands back are
actually returned so the caller can persist them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from email_ingestion.providers.gmail import GmailProvider
from tests.unit._email_fakes import bind_db


def _gmail() -> GmailProvider:
    return GmailProvider({"access_token": "t", "refresh_token": "r"})


# ── Gmail: batchModify collapses N messages into N/1000 calls ────────────────


@pytest.mark.parametrize(
    ("action", "add", "remove"),
    [
        ("archive", None, ["INBOX"]),
        ("read", None, ["UNREAD"]),
        ("unread", ["UNREAD"], None),
        ("star", ["STARRED"], None),
        ("unstar", None, ["STARRED"]),
    ],
)
async def test_gmail_bulk_action_is_one_batch_call(action, add, remove) -> None:
    g = _gmail()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    with patch.object(g, "_get_client", AsyncMock(return_value=client)):
        rekeys = await g.bulk_apply([f"m{i}" for i in range(250)], action)

    assert client.post.await_count == 1
    path, kwargs = client.post.await_args[0][0], client.post.await_args[1]
    assert path == "/users/me/messages/batchModify"
    assert len(kwargs["json"]["ids"]) == 250
    assert kwargs["json"].get("addLabelIds") == add
    assert kwargs["json"].get("removeLabelIds") == remove
    # Gmail never re-keys a message id.
    assert rekeys == {}


async def test_gmail_chunks_at_the_api_ceiling() -> None:
    """2,500 messages is 3 calls, not 2,500. This is the whole point: the old
    per-message loop would have been 2,500 sequential round-trips."""
    g = _gmail()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    with patch.object(g, "_get_client", AsyncMock(return_value=client)):
        await g.bulk_apply([f"m{i}" for i in range(2500)], "archive")

    sizes = [c[1]["json"]["ids"] for c in client.post.await_args_list]
    assert [len(s) for s in sizes] == [1000, 1000, 500]
    # Every id is covered exactly once — no chunk-boundary drops.
    flat = [i for s in sizes for i in s]
    assert len(set(flat)) == 2500


async def test_a_failed_batch_retries_message_by_message() -> None:
    """batchModify is all-or-nothing, so one stale id would cost the other 999
    their update. Falling back per-message costs a slow retry instead."""
    g = _gmail()
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError("400 invalid id"))
    modified: list[str] = []

    async def fake_modify(pmid, add_labels=None, remove_labels=None):
        modified.append(pmid)

    with patch.object(g, "_get_client", AsyncMock(return_value=client)), \
            patch.object(g, "modify_message", fake_modify):
        await g.bulk_apply(["a", "b", "c"], "archive")

    assert modified == ["a", "b", "c"]


async def test_trash_is_not_routed_through_batch_modify() -> None:
    """Trash has its own endpoint and its own semantics. Assuming batchModify
    accepts the TRASH system label is not something to discover on a
    destructive path."""
    g = _gmail()
    trashed: list[str] = []

    async def fake_trash(pmid):
        trashed.append(pmid)

    client = MagicMock()
    client.post = AsyncMock()
    with patch.object(g, "_get_client", AsyncMock(return_value=client)), \
            patch.object(g, "trash_message", fake_trash):
        await g.bulk_apply(["a", "b"], "trash")

    assert trashed == ["a", "b"]
    client.post.assert_not_awaited()


# ── Base: per-message fallback, failure isolation, id re-keys ───────────────


async def test_base_bulk_apply_returns_provider_rekeys() -> None:
    """Outlook's /move mints a NEW id and invalidates the old one. Dropping
    these leaves every bulk-archived message pointing at a dead id, so the next
    action on it 404s until a full re-sync happens to notice."""
    from email_ingestion.providers.base import BaseEmailProvider

    async def fake_move(pmid, folder):
        return f"new-{pmid}"

    p = MagicMock(spec=BaseEmailProvider)
    p.move_to_folder = fake_move
    rekeys = await BaseEmailProvider.bulk_apply(p, ["a", "b"], "archive")

    assert rekeys == {"a": "new-a", "b": "new-b"}


async def test_base_bulk_apply_reports_no_rekey_when_the_id_is_stable() -> None:
    from email_ingestion.providers.base import BaseEmailProvider

    async def fake_move(pmid, folder):
        return None

    p = MagicMock(spec=BaseEmailProvider)
    p.move_to_folder = fake_move
    assert await BaseEmailProvider.bulk_apply(p, ["a"], "archive") == {}


async def test_one_bad_message_never_strands_the_rest() -> None:
    """At 10,000 messages a single 404 on a since-deleted mail must not leave
    the other 9,999 half-applied."""
    from email_ingestion.providers.base import BaseEmailProvider

    touched: list[str] = []

    async def fake_move(pmid, folder):
        if pmid == "b":
            raise RuntimeError("404 not found")
        touched.append(pmid)
        return None

    p = MagicMock(spec=BaseEmailProvider)
    p.move_to_folder = fake_move
    await BaseEmailProvider.bulk_apply(p, ["a", "b", "c"], "archive")

    assert touched == ["a", "c"]


# ── The route: uncapped, but not unguarded ──────────────────────────────────


async def test_an_unfiltered_bulk_action_is_refused() -> None:
    """Removing the row cap made "trash my entire mailbox" reachable in one
    request. A cap was never the right guard against that — an explicit refusal
    is, because it doesn't also truncate the legitimate case."""
    from fastapi import HTTPException
    from gateway.routes.email.automation.senders import (
        BulkActionRequest,
        bulk_action,
    )

    req = BulkActionRequest(action="trash", account_id="acc-1")
    with pytest.raises(HTTPException) as exc:
        await bulk_action(req, MagicMock(), MagicMock(email="u@x.io"))
    assert exc.value.status_code == 400
    assert "unfiltered" in exc.value.detail.lower()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sender_email": "news@site.com"},
        {"message_ids": ["m1"]},
        {"folder": "inbox"},
        {"older_than_days": 30},
        {"only_read": True},
    ],
)
async def test_any_filter_is_enough_to_proceed(kwargs) -> None:
    """The guard is about scope, not about which filter — every one of these
    narrows the action to something the user actually pointed at."""
    from gateway.routes.email.automation import senders as s

    class _DB:
        async def execute(self, clause, params=None):
            return MagicMock(fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    req = s.BulkActionRequest(action="archive", account_id="acc-1", **kwargs)
    with patch.object(s, "_tenant_session", bind_db(_DB())):
        res = await s.bulk_action(req, MagicMock(), MagicMock(email="u@x.io"))
    assert res == {"affected": 0}


# ── The route: an action only touches mail it would actually change ──────────


def _capture_db(captured: dict):
    """A DB that records the UPDATE's SQL and returns no rows."""
    class _DB:
        async def execute(self, clause, params=None):
            captured["sql"] = str(clause)
            captured["params"] = params
            return MagicMock(fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    return _DB()


async def test_archiving_a_sender_never_reaches_into_the_bin() -> None:
    """A sender-wide "Archive all" carries no folder filter, so without an
    explicit guard the UPDATE swept the sender's TRASHED mail back out into the
    archive — silently un-deleting mail the user had already thrown away."""
    from gateway.routes.email.automation import senders as s

    captured: dict = {}
    req = s.BulkActionRequest(
        action="archive", account_id="acc-1", sender_email="news@site.com")
    with patch.object(s, "_tenant_session", bind_db(_capture_db(captured))):
        await s.bulk_action(req, MagicMock(), MagicMock(email="u@x.io"))

    # Disposed mail (trash/junk/spam/drafts) is out of reach for an archive.
    assert "'trash'" in captured["sql"] and "'junk'" in captured["sql"]


async def test_trashing_still_reaches_archived_mail() -> None:
    """The bin guard is archive-only: archived mail is still live mail the user
    may want to delete, so "Delete all" must not skip it."""
    from gateway.routes.email.automation import senders as s

    captured: dict = {}
    req = s.BulkActionRequest(
        action="trash", account_id="acc-1", sender_email="news@site.com")
    with patch.object(s, "_tenant_session", bind_db(_capture_db(captured))):
        await s.bulk_action(req, MagicMock(), MagicMock(email="u@x.io"))

    # Only already-trashed mail is skipped; the disposed-folder guard (which
    # would also exclude archived mail from reach) is archive-only.
    assert "NOT (LOWER(COALESCE(em.folder, '')) = 'trash')" in captured["sql"]
    assert "NOT IN ('trash'" not in captured["sql"]


@pytest.mark.parametrize(
    ("action", "already"),
    [
        ("archive", "LOWER(COALESCE(em.folder, '')) = 'archive'"),
        ("trash", "LOWER(COALESCE(em.folder, '')) = 'trash'"),
        ("read", "em.is_read = true"),
        ("unread", "em.is_read = false"),
        ("star", "em.is_starred = true"),
        ("unstar", "em.is_starred = false"),
    ],
)
async def test_affected_counts_only_what_changed(action, already) -> None:
    """`affected` is reported back to the user ("Archived 320 emails"). Matching
    rows that are ALREADY in the target state made it claim work it hadn't done
    — the cleaner's "nothing happened" complaint — and re-pushed the same no-op
    calls at the provider."""
    from gateway.routes.email.automation import senders as s

    captured: dict = {}
    req = s.BulkActionRequest(
        action=action, account_id="acc-1", sender_email="news@site.com")
    with patch.object(s, "_tenant_session", bind_db(_capture_db(captured))):
        await s.bulk_action(req, MagicMock(), MagicMock(email="u@x.io"))

    assert f"NOT ({already})" in captured["sql"]


# ── The reconciler: a swallowed provider failure is a lie in the mirror ──────


async def test_failed_ids_are_reported_not_swallowed() -> None:
    """bulk_apply used to log each failure and return nothing, so a run where
    every call 429'd looked identical to a clean one — while the local mirror
    already said 'archive'. Callers can now collect what didn't apply."""
    from email_ingestion.providers.base import BaseEmailProvider

    async def fake_move(pmid, folder):
        if pmid == "bad":
            raise RuntimeError("429 rate limited")
        return None

    p = MagicMock(spec=BaseEmailProvider)
    p.move_to_folder = fake_move
    failed: list[str] = []
    await BaseEmailProvider.bulk_apply(p, ["ok1", "bad", "ok2"], "archive", failed)

    assert failed == ["bad"]


async def test_reconcile_retries_then_reverts_what_never_applied() -> None:
    """A provider archive that never lands must not leave the row claiming it
    did: the next sync takes the provider as authoritative for `folder` and
    would pull the mail back minutes later ("I archived it and it came back")."""
    from gateway.routes.email.automation import senders as s

    calls: list[list[str]] = []

    class _Provider:
        async def bulk_apply(self, pmids, action, failed_out=None):
            calls.append(list(pmids))
            if failed_out is not None:
                failed_out.extend(pmids)  # never succeeds
            return {}

    statements: list[str] = []

    class _DB:
        async def execute(self, clause, params=None):
            statements.append(str(clause))
            return MagicMock()

        async def commit(self): ...
        async def close(self): ...

    class _Sess:
        authed = True
        provider = _Provider()

    class _Ctx:
        async def __aenter__(self): return _Sess()
        async def __aexit__(self, *a): return False

    with patch.object(s, "_tenant_session", bind_db(_DB())), \
            patch.object(s, "provider_session", lambda *a, **k: _Ctx()), \
            patch.object(s.asyncio, "sleep", AsyncMock()):
        await s._bulk_reconcile_provider("acc-1", ["m1"], "archive")

    # Retried rather than given up on after the first transient failure…
    assert len(calls) == s._RECONCILE_ATTEMPTS
    # …and the local mirror was put back so it matches the real mailbox.
    revert = [q for q in statements if "SET folder = 'inbox'" in q]
    assert revert, "the un-applied archive was left claiming success"


async def test_reconcile_leaves_the_mirror_alone_when_it_succeeds() -> None:
    """The happy path must not touch folders — only failures are reverted."""
    from gateway.routes.email.automation import senders as s

    class _Provider:
        async def bulk_apply(self, pmids, action, failed_out=None):
            return {}  # everything applied

    statements: list[str] = []

    class _DB:
        async def execute(self, clause, params=None):
            statements.append(str(clause))
            return MagicMock()

        async def commit(self): ...
        async def close(self): ...

    class _Sess:
        authed = True
        provider = _Provider()

    class _Ctx:
        async def __aenter__(self): return _Sess()
        async def __aexit__(self, *a): return False

    with patch.object(s, "_tenant_session", bind_db(_DB())), \
            patch.object(s, "provider_session", lambda *a, **k: _Ctx()):
        await s._bulk_reconcile_provider("acc-1", ["m1"], "archive")

    assert not [q for q in statements if "SET folder = 'inbox'" in q]


# ── The cleaner's scope: archived mail is handled mail ──────────────────────


def _senders_db(captured: dict):
    class _DB:
        async def execute(self, clause, params=None):
            sql = str(clause)
            # The first query is the per-sender aggregate; keep that one.
            if "GROUP BY LOWER(from_address" in sql:
                captured["sql"] = sql
            return MagicMock(
                fetchall=MagicMock(return_value=[]),
                fetchone=MagicMock(return_value=MagicMock(c=0)),
            )

        async def close(self): ...

    return _DB()


async def test_cleaner_excludes_archived_mail_by_default() -> None:
    """Archiving from the cleaner has to make the row leave the list. While
    archived mail counted, "Archive all" left the row with an unchanged total —
    a cleanup tool reading as a no-op."""
    from gateway.routes.email.automation import senders as s

    captured: dict = {}
    with patch.object(s, "_tenant_session", bind_db(_senders_db(captured))):
        await s.list_senders(
            account_id="acc-1", folder=None, include_archived=False,
            limit=200, offset=0, user=MagicMock(email="u@x.io"))

    assert "<> 'archive'" in captured["sql"]
    # …but a sender you've taken a decision on stays listed, or the
    # Unsubscribed / Auto-archive tabs empty out and can't be undone.
    assert "email_newsletters" in captured["sql"]


async def test_cleaner_can_still_show_the_whole_mailbox() -> None:
    """The preset Marketing / Cold Email rules archive as they label, so their
    category chips only fill in when archived mail is counted."""
    from gateway.routes.email.automation import senders as s

    captured: dict = {}
    with patch.object(s, "_tenant_session", bind_db(_senders_db(captured))):
        await s.list_senders(
            account_id="acc-1", folder=None, include_archived=True,
            limit=200, offset=0, user=MagicMock(email="u@x.io"))

    assert "<> 'archive'" not in captured["sql"]
    # Disposed mail is still out of scope in both modes.
    assert "'trash'" in captured["sql"]


# ── Sender chips: every pill is a label a rule actually wrote ────────────────


def test_sender_chips_show_the_conversation_labels_the_rules_write() -> None:
    """The cleaner used to show a synthesized "Conversation" chip — inferred,
    matching no rule in AI settings and no filter tab, and it MASKED the labels
    the user's rules had genuinely applied. Chips are now the real per-message
    labels: cleanup categories first, then Reply Zero's conversation state."""
    from gateway.routes.email.automation import senders as s

    # Ranked most-used first within each group.
    assert s._cleanup_categories_ranked(
        {"newsletter": 9, "marketing": 2}) == ["Newsletter", "Marketing"]
    # Conversation labels are real, rule-written labels with display names.
    assert s._CONVERSATION_DISPLAY["awaiting reply"] == "Awaiting Reply"
    assert s._CONVERSATION_DISPLAY["fyi"] == "FYI"
    # Legacy spellings collapse onto the modern name rather than duplicating it.
    assert s._CONVERSATION_DISPLAY["reply"] == "Needs Reply"
    assert s._CONVERSATION_DISPLAY["to reply"] == "Needs Reply"
    assert s._CONVERSATION_DISPLAY["actioned"] == "Done"
    # …and "Conversation" is not among them: it is a sender-level inference,
    # never a label on a message.
    assert s.CONVERSATION_SENDER_CATEGORY not in s._CONVERSATION_DISPLAY.values()


def test_conversation_stays_a_sender_ranking_signal() -> None:
    """Dropping the chip must not drop the classification: it is what lifts a
    real correspondent above bulk mail in "important emails"."""
    from gateway.routes.email import core

    assert (core.CONVERSATION_SENDER_CATEGORY.lower()
            in core.HUMAN_SENDER_CATEGORIES_LOWER)


# ── Search: list rows don't pay for message bodies ──────────────────────────


async def test_light_search_omits_message_bodies() -> None:
    """The cleaner's sender drill-down renders subject + snippet only. Fetching
    body_html for a page of marketing mail was megabytes of HTML parsed on the
    main thread and thrown away — the reported page freeze."""
    from gateway.routes.email.transport import search as sr

    captured: dict = {}

    class _DB:
        async def execute(self, clause, params=None):
            sql = str(clause)
            if "FROM email_messages em" in sql and "SELECT em.id" in sql:
                captured["sql"] = sql
            return MagicMock(
                fetchall=MagicMock(return_value=[]),
                fetchone=MagicMock(return_value=MagicMock(total=0)),
                scalar=MagicMock(return_value=0),
            )

        async def close(self): ...

    with patch.object(sr, "_tenant_session", bind_db(_DB())):
        await sr.search_messages(
            q=None, account_id="acc-1", folder="all", label=None, labels=None,
            uncategorized=False, from_addr="news@site.com", to_addr=None,
            is_read=None, is_starred=None, received_after=None,
            received_before=None, has_attachments=None, sender_category=None,
            importance=None, hybrid=False, light=True, page=1, page_size=4,
            user=MagicMock(email="u@x.io"))

    sql = captured["sql"]
    # The column SHAPE is preserved (_row_to_message reads both) but empty.
    assert "'' AS body_text" in sql and "NULL AS body_html" in sql
    assert "em.body_html," not in sql
