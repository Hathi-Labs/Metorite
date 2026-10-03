"""The Mem0 purge of one mailbox (WS-17 EM-T8f-1, MB-17).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item 2.

Two writers put the drafting memory of a mailbox into Mem0 under the key
``email_memory_scope(owner, account_id)``, which is ``<owner>#acct:<id>``. They
are the writing style (``automation/assistant.py``) and the learned reply
preferences (``automation/drafting.py``). The drafter reads them back under the
same key. Before EM-T8f-1, a disconnect deleted the mailbox row and left those
memories in Mem0 (MB-17).

The rules of this module:

1. ``transport/accounts.py::delete_account`` calls
   ``schedule_mailbox_memory_purge`` only after its ``DELETE`` commits. A 404,
   a 409 or a failed ``DELETE`` raises before that line, so it starts no purge.
2. The purge refuses a key with no account part. ``email_memory_scope`` gives
   the BARE member email when the account id is empty, and the bare key holds
   every personal memory of the member. A purge of it would delete them all.
3. The purge runs in a task, and ``_PURGES`` holds a strong reference to it
   until it ends. The route answers 204 and does not wait for Mem0.
4. A failed purge logs ``email.disconnect.memory_purge_failed`` with the error
   class only. The log holds no memory text and no address. A good purge logs
   ``email.disconnect.memory_purged`` with the count.

Fence: ``tests/unit/test_email_disconnect_memory_purge.py``.
"""

from __future__ import annotations

import asyncio

from gateway.routes.email.core import _log, email_memory_scope

#: The part of a Mem0 key that names one mailbox. ``email_memory_scope``
#: writes it, and a key without it is the bare member scope.
ACCOUNT_SCOPE_MARK = "#acct:"

#: The purges that are still running. The set holds a strong reference to each
#: task until it ends, so the loop does not collect a purge part way.
_PURGES: set[asyncio.Task[int | None]] = set()


class BareScopeRefused(ValueError):
    """The key names no mailbox, so a purge of it would delete every personal
    memory of the member."""


def mailbox_memory_scope(owner_email: str, account_id: str) -> str:
    """The Mem0 key of one mailbox, or ``BareScopeRefused``.

    The key comes from ``email_memory_scope``, the one function that the
    writers and the reader use. A key with no account id after
    ``ACCOUNT_SCOPE_MARK`` is refused.
    """
    scope = email_memory_scope(owner_email, account_id)
    member, mark, account = scope.partition(ACCOUNT_SCOPE_MARK)
    if not mark or not account.strip() or not member:
        raise BareScopeRefused("a mailbox purge needs an account id and an owner")
    return scope


async def purge_mailbox_memory(owner_email: str, account_id: str) -> int | None:
    """Delete each Mem0 memory of one mailbox. The answer is the count, or
    ``None`` when the purge was refused or failed.

    It never raises, because it runs in a task that nothing awaits.
    """
    try:
        scope = mailbox_memory_scope(owner_email, account_id)
    except BareScopeRefused:
        _log.warning("email.disconnect.memory_purge_refused", account_id=account_id)
        return None
    try:
        # Imported at the call, as the two writers do, so the route never
        # depends on acb_memory at import time.
        from acb_memory.mem0_client import get_memory_client

        count = await get_memory_client().delete_scope(scope)
    except Exception as exc:
        # Logged, and the 204 stands. The class only: a Mem0 or database
        # error text can quote a row.
        _log.warning("email.disconnect.memory_purge_failed",
                     account_id=account_id, error=type(exc).__name__)
        return None
    _log.info("email.disconnect.memory_purged", account_id=account_id, count=count)
    return count


def schedule_mailbox_memory_purge(
    owner_email: str, account_id: str,
) -> asyncio.Task[int | None]:
    """Start the purge of one mailbox in a task, and keep a reference to it.

    The caller must call this only after the ``DELETE`` of the mailbox row
    commits (rule 1 of this module).
    """
    task = asyncio.create_task(purge_mailbox_memory(owner_email, account_id))
    _PURGES.add(task)
    task.add_done_callback(_PURGES.discard)
    return task
