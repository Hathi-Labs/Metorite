"""The Mem0 purge of one mailbox (WS-17 EM-T8f-1, MB-17).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item 2.

Three writers put the drafting memory of a mailbox into Mem0 under the key
``email_memory_scope(owner, account_id)``, which is ``<owner>#acct:<id>``:

* the writing style (``automation/assistant.py``),
* the learned reply preferences (``automation/drafting.py``, the learning job),
* the precedent of each draft, "I replied: ..." (``automation/drafting.py``,
  ``_orchestrate_draft``). It runs in a background task, and the follow-up
  job and the rule runner reach it too.

The drafter reads them back under the same key. Before EM-T8f-1, a disconnect
deleted the mailbox row and left those memories in Mem0 (MB-17).

The rules of this module:

1. ``transport/accounts.py::delete_account`` calls
   ``schedule_mailbox_memory_purge`` only after its ``DELETE`` commits. A 404,
   a 409 or a failed ``DELETE`` raises before that line, so it starts no purge.
2. The purge refuses a key with no mailbox in it. ``email_memory_scope`` gives
   the BARE member email when the account id is empty, and the bare key holds
   every personal memory of the member. A purge of it would delete them all.
   An account id that is not a UUID is refused too.
3. The key holds the account id in its canonical form, ``str(UUID(id))``. The
   writers use the id that the database returns, which is that form. A path
   id in capitals, or with no hyphens, finds the row in Postgres and would
   find no Mem0 key.
4. The purge makes TWO passes, ``SECOND_PASS_DELAY_S`` apart. No lock holds the
   third writer, and stopping the sync does not stop a background add that is
   still running. Such an add can land after the first pass, and the second
   pass deletes it. An add later than the second pass stays. A durable sweeper
   is deferred (spec §11.7.6, EM-T8f-1 round 1 notes).
5. The purge runs in a task, and ``_PURGES`` holds a strong reference to it
   until it ends. The route answers 204 and does not wait for Mem0.
6. A failed pass logs ``email.disconnect.memory_purge_failed`` with the pass
   and the error class only. The log holds no memory text and no address.
   When both passes work, ``email.disconnect.memory_purged`` logs the count of
   each pass as ``first`` and ``second``.

Fence: ``tests/unit/test_email_disconnect_memory_purge.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from gateway.routes.email.core import _log, email_memory_scope

#: The part of a Mem0 key that names one mailbox. ``email_memory_scope``
#: writes it, and a key without it is the bare member scope.
ACCOUNT_SCOPE_MARK = "#acct:"

#: The time between the two passes of a purge. A background add of the drafter
#: finishes well inside it (one extraction call on the fast tier).
SECOND_PASS_DELAY_S = 120.0

#: The wait between the passes. Tests replace it, so a test does not wait two
#: minutes.
_sleep = asyncio.sleep

#: The purges that are still running. The set holds a strong reference to each
#: task until it ends, so the loop does not collect a purge part way.
_PURGES: set[asyncio.Task[int | None]] = set()


class ScopeRefused(ValueError):
    """The key names no mailbox. A purge of it could delete every personal
    memory of the member."""


def mailbox_memory_scope(owner_email: str, account_id: Any) -> str:
    """The Mem0 key of one mailbox, or ``ScopeRefused``.

    The key comes from ``email_memory_scope``, the one function that the
    writers and the reader use, with the account id in canonical UUID form.
    """
    try:
        canonical = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        raise ScopeRefused("a mailbox purge needs a mailbox id") from None
    scope = email_memory_scope(owner_email, canonical)
    member, mark, account = scope.partition(ACCOUNT_SCOPE_MARK)
    if not mark or not account.strip() or not member:
        raise ScopeRefused("a mailbox purge needs an account id and an owner")
    return scope


async def _one_pass(scope: str, account_id: str, phase: str) -> int | None:
    """One ``delete_scope`` call. The count, or ``None`` after a logged error."""
    try:
        # Imported at the call, as the writers do, so the route never depends
        # on acb_memory at import time.
        from acb_memory.mem0_client import get_memory_client

        return await get_memory_client().delete_scope(scope)
    except Exception as exc:
        # Logged, and the 204 stands. The class only: a Mem0 or database
        # error text can quote a row.
        _log.warning("email.disconnect.memory_purge_failed",
                     account_id=account_id, phase=phase,
                     error=type(exc).__name__)
        return None


async def purge_mailbox_memory(owner_email: str, account_id: Any) -> int | None:
    """Delete each Mem0 memory of one mailbox, in two passes. The answer is
    the sum of the two counts, or ``None`` when the purge was refused or a
    pass failed.

    It never raises, because it runs in a task that nothing awaits. A failed
    first pass does not stop the second one.
    """
    shown = str(account_id)[:64]
    try:
        scope = mailbox_memory_scope(owner_email, account_id)
    except ScopeRefused:
        _log.warning("email.disconnect.memory_purge_refused", account_id=shown)
        return None
    first = await _one_pass(scope, shown, "first")
    await _sleep(SECOND_PASS_DELAY_S)
    second = await _one_pass(scope, shown, "second")
    if first is None or second is None:
        return None
    _log.info("email.disconnect.memory_purged", account_id=shown,
              first=first, second=second)
    return first + second


def schedule_mailbox_memory_purge(
    owner_email: str, account_id: Any,
) -> asyncio.Task[int | None]:
    """Start the purge of one mailbox in a task, and keep a reference to it.

    The caller must call this only after the ``DELETE`` of the mailbox row
    commits (rule 1 of this module).
    """
    task = asyncio.create_task(purge_mailbox_memory(owner_email, account_id))
    _PURGES.add(task)
    task.add_done_callback(_PURGES.discard)
    return task
