"""Post-sync hook registry — the gateway registers its rule / classify / digest
callbacks here at startup so the ingestion scheduler can run them WITHOUT
importing up into the gateway package.

Historically the scheduler lazy-imported ``gateway.routes.email`` to run these
post-sync jobs — an ``email_ingestion -> gateway`` dependency, i.e. the *lower*
layer reaching *up* into the higher one (the C2 layering inversion). Now the
direction is correct: the gateway (higher layer) imports this module and
registers its callbacks; the scheduler (this package) only ever reads the
registry.

Every hook is optional and takes a single ``account_id``; an unregistered hook
is a no-op, so ``email_ingestion`` still runs standalone (e.g. in unit tests, or
any process that syncs mail without the gateway wired in).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

PostSyncHook = Callable[[str], Awaitable[None]]
# Unlike the plain hooks, the label-learning hook needs the per-message data the
# scheduler captured DURING persist — it takes ``(account_id, changes)`` where
# ``changes`` is a list of ``(message, old_categories)`` pairs (the categories a
# message had BEFORE this sync's upsert overwrote them). See ``learn_label_changes``.
LabelLearnHook = Callable[[str, list[Any]], Awaitable[None]]


@dataclass
class PostSyncHooks:
    """The callbacks the scheduler invokes after a successful account sync."""

    # The new-mail pipeline (auto-run rules → categorize senders → classify
    # threads → auto-archive), run when a sync brought in new mail. The SAME
    # hook is enqueued by the manual-sync route and the webhook (H1), so new mail
    # is processed identically however it arrived.
    on_new_mail: PostSyncHook | None = None
    # Run every cycle (each is internally time-gated):
    #
    # Reply Zero classification is deliberately NOT part of on_new_mail alone.
    # It has a BACKLOG to work through — threads that predate the rules, or that
    # a capped earlier cycle didn't reach — and gating it on new mail arriving
    # means a quiet mailbox never catches up. It is cheap when there is nothing
    # to do: one indexed query returning no rows.
    classify_threads: PostSyncHook | None = None
    send_digest: PostSyncHook | None = None
    send_follow_up_reminders: PostSyncHook | None = None
    # Subscription/watch upkeep:
    ensure_subscription: PostSyncHook | None = None
    # Learn FROM-classification patterns from manual label changes the USER made
    # in their mail client, detected as a category delta during persist. Carries
    # the per-message pre-upsert categories (destroyed by the upsert on a
    # categories-authoritative provider like Outlook), so it does NOT fit the
    # plain () -> None shape. The scheduler path used to skip this entirely —
    # every label change made during normal polling was silently lost.
    learn_label_changes: LabelLearnHook | None = None


hooks = PostSyncHooks()


def register_post_sync_hooks(**kwargs: PostSyncHook | None) -> None:
    """Wire the gateway's post-sync callbacks in — called once at app startup.

    Raises ``AttributeError`` for an unknown hook name so a typo fails loudly
    rather than silently registering nothing.
    """
    for name, fn in kwargs.items():
        if not hasattr(hooks, name):
            raise AttributeError(f"unknown post-sync hook: {name!r}")
        setattr(hooks, name, fn)


def triage_once_per_cycle() -> bool:
    """The ONE reader of ``email_triage_once_per_cycle`` (WS-17 EM-T16 PR-A).

    True: a cycle that ran ``on_new_mail`` skips ``classify_threads``, and
    the Reply Zero backfill keeps its back-off marks in ``on``. The scheduler
    and ``replyzero`` both read it here. Fence:
    ``tests/unit/test_email_triage_once.py``.
    """
    from acb_common.settings import get_settings

    return bool(getattr(get_settings(), "email_triage_once_per_cycle", False))


async def run_hook(hook: PostSyncHook | None, account_id: str) -> None:
    """Await ``hook`` if one is registered; no-op otherwise."""
    if hook is not None:
        await hook(account_id)


async def run_label_learn_hook(
    hook: LabelLearnHook | None, account_id: str, changes: list[Any],
) -> None:
    """Await the label-learning hook with the ``(message, old_categories)``
    changes captured during persist; no-op if unregistered or nothing changed."""
    if hook is not None and changes:
        await hook(account_id, changes)
