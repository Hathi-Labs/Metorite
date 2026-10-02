"""There is ONE sync core, and the manual route is a thin wrapper over it.

trigger_sync (manual) and _sync_account (scheduler) were two sync cores that
had drifted — the manual copy lost rotated refresh tokens and wiped the Outlook
sync cursor (review §3.2). 2.1 collapsed them: trigger_sync now only checks
ownership and delegates to ``email_ingestion.scheduler._sync_account`` — the
same code the background scheduler and the Graph webhook run — via the shared
``_run_manual_sync`` helper.

These pin (a) the delegation, so the manual route can never re-grow an inline
sync body, and (b) the two historical defects, now on the SINGLE core where the
logic actually lives.
"""
from __future__ import annotations

import inspect

from email_ingestion import scheduler as sched
from gateway.routes.email.transport import sync as s


# ── the manual route is a wrapper, not a second core ────────────────────────

def test_trigger_sync_delegates_to_the_one_core() -> None:
    src = inspect.getsource(s._run_manual_sync)
    assert "_sync_account" in src, (
        "the manual sync no longer delegates to the shared core")


def test_trigger_sync_has_no_inline_sync_body() -> None:
    """The drift class only comes back if someone re-inlines the body. None of
    the core's work — fetch, persist, cursor write, auth — may reappear here."""
    src = inspect.getsource(s.trigger_sync)
    for marker in ("sync_messages", "upsert_message", "last_history_id",
                   "authenticate()", "email_sync_log"):
        assert marker not in src, (
            f"trigger_sync contains '{marker}' — the manual path has re-grown "
            f"an inline sync body instead of delegating to _sync_account")


def test_resync_routes_through_the_shared_helper() -> None:
    """resync used to call the trigger_sync ROUTE directly with the wrong
    positional args (``user`` landed in the ``background`` slot), so every
    direct resync crashed before reaching the provider. It must call the plain
    helper, never a route handler."""
    src = inspect.getsource(s.resync_account)
    assert "_run_manual_sync(" in src
    assert "trigger_sync(" not in src


# ── the two historical defects, pinned on the single core ───────────────────

def _core_src() -> str:
    # EM-T4f part 2: ``_sync_account`` is now the per-mailbox lock, and the
    # cycle it runs is ``_sync_cycle``.
    return inspect.getsource(sched._sync_cycle)


def test_rotated_creds_are_persisted_immediately_after_auth() -> None:
    src = _core_src()
    # The post-auth persist must come BEFORE the sync_messages call, not only
    # in the end-of-sync block — Microsoft rotates the refresh token on refresh
    # and a mid-sync failure would otherwise lose it.
    auth = src.index("authenticate()")
    first_persist = src.index("credentials_encrypted = :creds")
    sync_call = src.index("provider.sync_messages")
    assert auth < first_persist < sync_call, (
        "rotated creds must be persisted right after auth, before the sync body")


def test_history_cursor_is_coalesced_not_overwritten() -> None:
    src = _core_src()
    # Outlook's new_history_id is None; a bare assignment would NULL the cursor.
    assert "last_history_id = COALESCE(" in src
    assert "last_history_id = :history_id," not in src
