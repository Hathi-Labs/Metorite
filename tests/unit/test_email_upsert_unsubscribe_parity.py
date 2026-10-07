"""Guard: there is ONE received-message upsert, and it persists ``unsubscribe_link``.

The ``email_messages`` upsert SQL used to be copy-pasted across four ingest paths
— manual sync (``transport/sync.py``), the background scheduler (``scheduler.py``),
the on-demand history backfill (``core._upsert_message``) and the inbound webhook
(``inbound.py``). The copies drifted: the scheduler/webhook once omitted
``unsubscribe_link``, so a background-synced marketing email had no one-click
unsubscribe link and was Reply-Zero classified differently than the same mail
pulled by a manual sync.

C1 unified them behind a single helper (``email_ingestion.persist.upsert_message``).
This guard now enforces the *consolidation* rather than parity across copies:

1. the shared helper binds ``unsubscribe_link`` with a COALESCE-preserve on update;
2. every received-message ingest path routes through the helper and no longer
   carries its own ``INSERT INTO email_messages`` — so they cannot drift again.

(``drafting.py`` also INSERTs into ``email_messages``, but for OUTBOUND drafts,
which have no unsubscribe link and a different column set — deliberately its own
path, excluded here.)
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_HELPER = REPO / "apps/services/email_ingestion/email_ingestion/persist.py"

# The received-message ingest paths, and the token proving each routes through
# the shared helper instead of hand-rolling the upsert. transport/sync.py is
# deliberately ABSENT: since the 2.1 sync-core collapse the manual route
# delegates to the scheduler core and is no longer an ingest path at all
# (test_sync_route_is_not_an_ingest_path pins that).
_INGEST_PATHS = {
    "apps/services/email_ingestion/email_ingestion/scheduler.py": "upsert_message(",
    "apps/services/email_ingestion/email_ingestion/inbound.py": "upsert_message(",
    "apps/services/gateway/gateway/routes/email/core.py": "upsert_message(",
}


def test_sync_route_is_not_an_ingest_path():
    """After the 2.1 collapse the manual-sync route delegates to the scheduler
    core; it must neither call the upsert helper nor own a message INSERT —
    either one means an inline sync body has crept back in."""
    src = (REPO / "apps/services/gateway/gateway/routes/email/transport/"
                  "sync.py").read_text(encoding="utf-8")
    assert "upsert_message(" not in src
    assert "INSERT INTO email_messages" not in src


def test_shared_helper_persists_unsubscribe_link():
    src = _HELPER.read_text(encoding="utf-8")
    assert "INSERT INTO email_messages" in src, "persist.py: no message upsert?"
    # Bound in the INSERT ... VALUES and preserved (not clobbered) on re-sync.
    assert ":unsubscribe_link" in src, (
        "persist.upsert_message no longer binds unsubscribe_link — a "
        "background/webhook-synced email would lose one-click unsubscribe."
    )
    # The SET is built from one column list since 2026-10-07 (the "changed?"
    # guard), so read the rendered SQL and not the source text.
    from email_ingestion.persist import _ON_CONFLICT_UPDATE

    assert ("unsubscribe_link = COALESCE(EXCLUDED.unsubscribe_link"
            in _ON_CONFLICT_UPDATE), (
        "the shared upsert must PRESERVE an already-parsed unsubscribe link when "
        "a later sync re-sends the row without one (COALESCE), not clobber it."
    )


def test_every_ingest_path_uses_the_shared_helper():
    for rel, token in _INGEST_PATHS.items():
        src = (REPO / rel).read_text(encoding="utf-8")
        assert token in src, (
            f"{rel} no longer routes through the shared upsert helper "
            f"(email_ingestion.persist.upsert_message) — C1 regressed."
        )
        # ...and must NOT have re-grown its own copy of the received-message
        # INSERT (that is exactly the drift C1 removed). The only file that still
        # owns an INSERT INTO email_messages is the helper itself + drafting.py.
        assert "INSERT INTO email_messages" not in src, (
            f"{rel} carries its own 'INSERT INTO email_messages' again — the "
            f"received-message upsert has been re-duplicated instead of reusing "
            f"the shared helper (drift risk C1 was meant to remove)."
        )
