"""Reviving label-learning on the scheduler sync path (email item 2.1).

Only the manual-sync route used to learn FROM-classification patterns from the
label changes a user makes in their client; the scheduler — which is what
actually polls every ~300s — never did, so those changes were silently dropped.
The fix: the scheduler captures the pre-upsert categories during persist and
hands them to a gateway-registered hook that runs the SAME learner.

These lock the two new seams: the gating invoker, the shared orchestration, and
that the hook is actually wired at startup (an unregistered hook would silently
restore the old dropped-on-the-floor behaviour).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from email_ingestion import post_sync
from gateway.routes.email import scheduler_hooks
from gateway.routes.email.transport import sync as sync_mod


# ── the gating invoker ──────────────────────────────────────────────────────

async def test_invoker_noops_without_a_hook() -> None:
    # No registered hook → nothing to call, no crash.
    await post_sync.run_label_learn_hook(None, "acc", [("m", [])])


async def test_invoker_noops_on_empty_changes() -> None:
    hook = AsyncMock()
    await post_sync.run_label_learn_hook(hook, "acc", [])
    hook.assert_not_awaited()  # a clean sync with no label deltas costs nothing


async def test_invoker_fires_with_changes() -> None:
    hook = AsyncMock()
    changes = [("m1", ["Old"]), ("m2", [])]
    await post_sync.run_label_learn_hook(hook, "acc", changes)
    hook.assert_awaited_once_with("acc", changes)


# ── the shared orchestration ────────────────────────────────────────────────

def _msg(cats):
    return SimpleNamespace(
        categories=cats, thread_id="t1",
        from_address=SimpleNamespace(email="sender@x.com"))


def _session(db: AsyncMock, opened: list[bool]):
    """A fake ``_tenant_session`` that records each open (EM-T1b-1)."""
    @asynccontextmanager
    async def _ts(org=None):
        opened.append(True)
        yield db
    return _ts


async def test_orchestrator_noops_when_no_rules() -> None:
    db = AsyncMock()
    opened: list[bool] = []
    with patch.object(sync_mod, "_tenant_session", _session(db, opened)), \
            patch.object(sync_mod, "_build_label_rule_map",
                         AsyncMock(return_value=({}, {}))), \
            patch.object(sync_mod, "_learn_from_label_changes",
                         AsyncMock()) as learn:
        await sync_mod.learn_from_label_change_events(
            "acc", [(_msg(["New"]), ["Old"])])
    learn.assert_not_awaited()  # nothing to trace a category to


async def test_orchestrator_learns_each_change_then_applies_corrections() -> None:
    db = AsyncMock()
    opened: list[bool] = []
    changes = [(_msg(["Receipt"]), []), (_msg(["Newsletter"]), ["Receipt"])]
    with patch.object(sync_mod, "_tenant_session", _session(db, opened)), \
            patch.object(sync_mod, "_build_label_rule_map",
                         AsyncMock(return_value=({"receipt": "r1"}, {}))), \
            patch.object(sync_mod, "_learn_from_label_changes",
                         AsyncMock()) as learn, \
            patch.object(sync_mod, "_apply_label_status_corrections",
                         AsyncMock()) as apply_corr:
        await sync_mod.learn_from_label_change_events("acc", changes)
    assert learn.await_count == 2  # one per captured change
    apply_corr.assert_awaited_once()  # queued reply-status corrections applied
    assert opened == [True], "the pattern writes run in ONE tenant session"
    # EM-T1b-1: the seam commits on exit. A commit inside the block would end
    # SET LOCAL, so every write after it would run with no tenant.
    db.commit.assert_not_awaited()


async def test_orchestrator_noops_on_empty_changes() -> None:
    opened: list[bool] = []
    with patch.object(sync_mod, "_tenant_session",
                      _session(AsyncMock(), opened)), \
            patch.object(sync_mod, "_build_label_rule_map",
                         AsyncMock()) as build:
        await sync_mod.learn_from_label_change_events("acc", [])
    build.assert_not_awaited()
    assert opened == [], "no change, no session"


# ── the hook is actually wired ──────────────────────────────────────────────

def test_hook_slot_exists_and_registers() -> None:
    # The dataclass carries the slot…
    assert hasattr(post_sync.PostSyncHooks(), "learn_label_changes")
    # …and startup registration actually populates it — an unregistered hook
    # would silently return to dropping every label change on the scheduler path.
    scheduler_hooks.register_email_post_sync_hooks()
    assert post_sync.hooks.learn_label_changes is not None
