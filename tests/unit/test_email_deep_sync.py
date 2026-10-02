"""Deep-vs-shallow sync (deep initial load to the floor, recurring shallow) +
inbound deletion reconciliation for the full Outlook snapshot.

Since EM-T6a the floor binds the recurring poll too (D-EM-10). The floor rule
itself is pinned by ``test_email_import_floor.py``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from email_ingestion.providers.base import EmailMessage, SyncResult
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.outlook import OutlookProvider
from email_ingestion.reconcile import reconcile_full_snapshot

NOW = datetime(2026, 6, 23, tzinfo=timezone.utc)


def _outlook() -> OutlookProvider:
    return OutlookProvider({
        "access_token": "x", "refresh_token": "y",
        "client_id": "c", "client_secret": "s",
    })


def _resp(value: list, next_link: str | None = None) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    body: dict = {"value": value}
    if next_link:
        body["@odata.nextLink"] = next_link
    r.json = MagicMock(return_value=body)
    return r


# ── Outlook: since-filter + sweep depth ──────────────────────────────────────

async def test_list_messages_adds_received_filter_when_since() -> None:
    p = _outlook()
    client = AsyncMock()
    client.get.return_value = _resp([])
    p._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    await p.list_messages(folder="inbox", since=NOW - timedelta(days=365))
    params = client.get.await_args.kwargs["params"]
    assert params["$filter"].startswith("receivedDateTime ge ")

    client.get.reset_mock()
    await p.list_messages(folder="inbox")
    assert "$filter" not in client.get.await_args.kwargs["params"]


async def test_sweep_folder_recurring_caps_pages() -> None:
    p = _outlook()
    # Always return a token → would page forever; recurring must stop at the cap.
    p.list_messages = AsyncMock(return_value=([], "TOK"))  # type: ignore[method-assign]
    await p._sweep_folder("inbox", 100)
    assert p.list_messages.await_count == p.RECURRING_SYNC_MAX_PAGES


async def test_sweep_folder_stops_when_token_exhausted() -> None:
    p = _outlook()
    p.list_messages = AsyncMock(  # type: ignore[method-assign]
        side_effect=[([], "TOK"), ([], None)]
    )
    await p._sweep_folder("inbox", 100, max_pages=10)
    assert p.list_messages.await_count == 2


async def test_sync_messages_deep_sets_full_snapshot_and_passes_since() -> None:
    p = _outlook()
    p._get_client = AsyncMock(return_value=AsyncMock())  # type: ignore[method-assign]
    p.list_folders = AsyncMock(return_value=[])  # type: ignore[method-assign]
    seen: list = []

    async def _sweep(folder, max_results, canonical_override=None, *, max_pages=None, since=None):
        seen.append((folder, max_pages, since))
        return []

    p._sweep_folder = _sweep  # type: ignore[method-assign]
    since = NOW - timedelta(days=365)
    res = await p.sync_messages(deep=True, since=since)
    assert res.full_snapshot is True
    assert all(s[2] == since for s in seen)            # since threaded through
    assert all(s[1] == p.DEEP_SYNC_MAX_PAGES for s in seen)

    seen.clear()
    res = await p.sync_messages(deep=False, since=since)
    # EM-T6a item 5: the shallow poll applies the floor too, and keeps its
    # page count.
    assert seen and all(s[2] == since for s in seen)
    assert all(s[1] == p.RECURRING_SYNC_MAX_PAGES for s in seen)


# ── Gmail: after: query + deep paging ────────────────────────────────────────

def _gmail() -> GmailProvider:
    return GmailProvider({"access_token": "x", "refresh_token": "y",
                          "client_id": "c", "client_secret": "s"})


async def test_gmail_sweep_label_adds_after_query() -> None:
    p = _gmail()
    p.list_messages = AsyncMock(return_value=([], None))  # type: ignore[method-assign]
    await p._sweep_label("INBOX", 100, since=NOW - timedelta(days=365))
    assert p.list_messages.await_args.kwargs["query"].startswith("after:")


async def test_gmail_sweep_label_pages_until_exhausted() -> None:
    p = _gmail()
    msg = EmailMessage(provider_message_id="m1", thread_id=None, folder="inbox")
    p.list_messages = AsyncMock(  # type: ignore[method-assign]
        side_effect=[([msg], "TOK"), ([msg], None)]
    )
    out = await p._sweep_label("INBOX", 100)
    assert p.list_messages.await_count == 2
    assert len(out) == 2


# ── Reconciliation ───────────────────────────────────────────────────────────

def _msg(pid: str, folder: str, received: datetime) -> EmailMessage:
    return EmailMessage(provider_message_id=pid, thread_id=None, folder=folder,
                        subject="s", received_at=received)


async def test_reconcile_noop_when_not_full_snapshot() -> None:
    db = AsyncMock()
    n = await reconcile_full_snapshot(
        db, "acc", SyncResult(messages=[_msg("A", "inbox", NOW)], full_snapshot=False)
    )
    assert n == 0
    db.execute.assert_not_called()


async def test_reconcile_trashes_vanished_skips_present() -> None:
    # Snapshot has A & B in inbox; locally we also have C in inbox (vanished).
    snap = SyncResult(
        messages=[_msg("A", "inbox", NOW), _msg("B", "inbox", NOW - timedelta(days=1))],
        full_snapshot=True,
    )
    db = AsyncMock()
    select_result = MagicMock()
    select_result.fetchall.return_value = [
        SimpleNamespace(id="row-A", provider_message_id="A"),  # still present → skip
        SimpleNamespace(id="row-C", provider_message_id="C"),  # vanished → trash
    ]

    calls: list = []

    async def _execute(sql, params=None):
        calls.append(str(sql))
        return select_result if "SELECT" in str(sql) else MagicMock()

    db.execute = AsyncMock(side_effect=_execute)
    n = await reconcile_full_snapshot(db, "acc", snap)
    assert n == 1
    assert any("UPDATE email_messages SET folder = 'trash'" in c for c in calls)
