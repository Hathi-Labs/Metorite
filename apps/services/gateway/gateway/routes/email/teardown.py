"""The teardown of one mailbox (WS-17 EM-G11f1, PR-G7).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.9.

``transport/accounts.py::delete_account`` (``DELETE /email/accounts/{id}``)
runs a disconnect in this order. Each step is bounded, and no session is open
across a network call:

1. The ownership read, in one block. For a Gmail row, the same block reads the
   filter ids that Metorite made (:func:`read_filter_ids`), because the
   cascade of step 3 deletes them.
2. The sync loop stops, with no block open.
3. The ``DELETE`` of the ``email_accounts`` row. The cascade deletes the
   children. A 404, a 409 or a failed ``DELETE`` raises here, so no later
   step runs, and nothing changes at Google.
4. The Mem0 purge starts in a task (``memory_purge.py``). It starts before any
   new await, so a cancel of the request cannot skip it.
5. :func:`remove_metorite_filters` deletes the filters at Gmail (gap D2),
   from the ids of step 1, while the grant is still valid.
6. :func:`revoke_grant` revokes the Google grant (gap D1), unless another
   Metorite mailbox uses the same Gmail account (:func:`grant_shared_elsewhere`).
7. The Graph subscription of an Outlook row goes, as before EM-G11f1.
8. :func:`drop_mailbox_cache` deletes the Redis keys that carry the mailbox id,
   for each provider, and bumps the My Day version of the owner.

This module holds the legs that EM-G11f1 adds: steps 1 (the filter read), 5,
6 and 8. EM-G11f1 wires the disconnect route ONLY. The admin member purge and
the org purge wait for a spec edit (§12.3.13.9).

The rules of this module:

* A failure of a leg never blocks the disconnect. The route still answers 204.
* No log line holds a token, a refresh token or mail text. A failure logs the
  error class or a fixed reason.
* The revoke sends the token in the form body of a POST, never in the URL.
* Google keeps one grant for each Google account and each OAuth client. So a
  revoke ends the grant of EVERY Metorite row of that Gmail account, in any
  organization. Migration 263's ``email_gmail_grant_shared`` answers whether
  another such row exists, and the revoke runs only on a clear ``false``. A
  ``true``, a ``NULL`` or a failed check skips it (review round 1, P1). A
  ``NULL`` logs ``owner_blind`` and a failed check logs ``check_failed``, each
  at WARNING (review round 2).
* An Outlook mailbox keeps the behaviour of ``main``. Its filters stay, and
  Microsoft has no revoke for one delegated grant. The spec records the open
  point about the Outlook filters (§12.3.13.9).

Fence: ``tests/unit/test_email_mailbox_teardown.py`` and the R8 case in
``tests/unit/test_email_disconnect_order.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
from gateway.routes.email.core import _decrypt_credentials, _instantiate_provider, _log
from sqlalchemy import text

#: Google's endpoint that revokes an OAuth grant. A POST of the refresh token
#: ends the grant, and the access tokens of the grant end with it.
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

#: The bound on the revoke call. A slow Google must never hold back the 204.
REVOKE_TIMEOUT_S = 5.0

#: The bound on the whole filter removal, for every filter of the mailbox.
FILTER_REMOVAL_TIMEOUT_S = 10.0

#: The bound on the Redis deletes of one mailbox.
CACHE_DROP_TIMEOUT_S = 5.0

#: The page size of one Redis ``DELETE``.
_CACHE_DELETE_BATCH = 200

#: Another Gmail row of this address, in any tenant (migration 263). One bit,
#: never a row. NULL means that the function cannot tell.
GRANT_SHARED_SQL = (
    "SELECT public.email_gmail_grant_shared(:email, CAST(:exclude AS uuid))")

#: The filter ids that Metorite made for one mailbox. The owner predicate
#: repeats the one of the ownership read, in the same block.
FILTER_IDS_SQL = """SELECT n.auto_archive_filter_id AS fid
   FROM email_newsletters n
   JOIN email_accounts a ON a.id = n.account_id
   WHERE n.account_id = :id AND a.user_id = :uid
     AND n.auto_archive_filter_id IS NOT NULL
   ORDER BY n.created_at, n.id"""


# ── step 1: the filter read, inside the ownership block ─────────────────────


async def read_filter_ids(db: Any, account_id: str, owner: str) -> list[str]:
    """The ids of the Gmail filters that Metorite made for one mailbox.

    ``senders.py::_create_block_filter`` writes each id into
    ``email_newsletters.auto_archive_filter_id``. The caller runs this inside
    the block of its ownership read, so RLS binds the read to the tenant.
    Each id appears once, in the order of its row.
    """
    rows = (await db.execute(
        text(FILTER_IDS_SQL), {"id": account_id, "uid": owner})).fetchall()
    seen: dict[str, None] = {}
    for row in rows:
        fid = str(row.fid or "").strip()
        if fid:
            seen.setdefault(fid, None)
    return list(seen)


# ── step 5: the filters at Gmail, after the commit ──────────────────────────


async def remove_metorite_filters(
    account_id: str, provider: str, creds_blob: Any, filter_ids: list[str],
) -> None:
    """Delete each Gmail filter that Metorite made, best effort (gap D2).

    Only a ``gmail`` row with at least one id gets a call. The provider comes
    from ``_instantiate_provider``, and ``delete_filter`` refreshes an expired
    access token. A failure of one filter logs its error class, and the next
    filter still goes. The whole step has a bound of
    ``FILTER_REMOVAL_TIMEOUT_S``. It never raises.

    The caller runs it only after the ``DELETE`` commits, with the ids that
    its ownership read kept, and before the revoke ends the grant. It holds
    no session open, because each delete is a network call.
    """
    if provider != "gmail" or not filter_ids:
        return
    removed = failed = 0
    try:
        async with asyncio.timeout(FILTER_REMOVAL_TIMEOUT_S):
            creds, _store = _decrypt_credentials(creds_blob)
            gmail = _instantiate_provider(provider, creds)
            for fid in filter_ids:
                try:
                    await gmail.delete_filter(fid)
                except Exception as exc:
                    failed += 1
                    _log.warning(
                        "email.disconnect.filter_remove_failed",
                        account_id=account_id, error=type(exc).__name__,
                        status=_status_of(exc))
                    continue
                removed += 1
    except Exception as exc:
        # The bound, a credential blob that does not decrypt, or a provider
        # that does not build. The DELETE still runs.
        _log.warning("email.disconnect.filters_failed", account_id=account_id,
                     error=type(exc).__name__, removed=removed, failed=failed)
        return
    _log.info("email.disconnect.filters_removed", account_id=account_id,
              removed=removed, failed=failed)


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status that an error carries, or None. Never its text."""
    return getattr(getattr(exc, "response", None), "status_code", None)


# ── step 6: the revoke at Google, unless the grant is shared ───────────────


def _http_client() -> httpx.AsyncClient:
    """The client of the revoke call. Tests replace it with a mock transport."""
    return httpx.AsyncClient(timeout=REVOKE_TIMEOUT_S)


async def _post_revoke(token: str) -> int:
    """POST one token to Google, and return the HTTP status.

    The token goes in the form body. It is never in the URL, so no access log
    and no ``httpx`` log line can hold it.
    """
    async with _http_client() as client:
        resp = await client.post(GOOGLE_REVOKE_URL, data={"token": token})
    return resp.status_code


def _grant_token(creds: Any) -> str | None:
    """The token that ends the grant: the refresh token, or else the access
    token. Google revokes the refresh token of an access token too."""
    if not isinstance(creds, dict):
        return None
    for field in ("refresh_token", "access_token"):
        value = creds.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


#: The verdicts of :func:`grant_shared_elsewhere`. Only ``GRANT_FREE`` lets
#: the revoke run. Each other verdict is the reason of ``email.revoke_skipped``.
GRANT_FREE = "free"
GRANT_SHARED = "shared_grant"
GRANT_OWNER_BLIND = "owner_blind"
GRANT_CHECK_FAILED = "check_failed"
GRANT_NO_ADDRESS = "no_address"


async def grant_shared_elsewhere(
    open_session: Callable[[], Any], email: Any, account_id: str,
) -> str:
    """The verdict on another Metorite Gmail row of ``email``, in any tenant.

    One short block of ``open_session``, which is the ``_tenant_session`` of
    the caller. Migration 263's function answers one bit across tenants and
    never a row. The verdicts:

    * ``GRANT_FREE``: the function answered ``false``. No other row exists.
    * ``GRANT_SHARED``: the function answered ``true``.
    * ``GRANT_OWNER_BLIND``: the function answered ``NULL``, because its
      owner cannot see through RLS. That is a deploy fault, so the caller
      logs it at WARNING.
    * ``GRANT_CHECK_FAILED``: the call raised. This logs the error class.
    * ``GRANT_NO_ADDRESS``: the row holds no address to ask about.

    The caller skips the revoke unless the verdict is ``GRANT_FREE``. It
    never raises.
    """
    address = str(email or "").strip()
    if not address:
        return GRANT_NO_ADDRESS
    try:
        exclude: str | None = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        exclude = None
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_S):
            async with open_session() as db:
                answer = (await db.execute(
                    text(GRANT_SHARED_SQL),
                    {"email": address, "exclude": exclude})).scalar()
    except Exception as exc:
        _log.warning("email.disconnect.grant_check_failed",
                     account_id=exclude or str(account_id)[:64],
                     error=type(exc).__name__)
        return GRANT_CHECK_FAILED
    if answer is True:
        return GRANT_SHARED
    if answer is False:
        return GRANT_FREE
    return GRANT_OWNER_BLIND


async def revoke_grant(
    account_id: str, row: Any, open_session: Callable[[], Any],
) -> None:
    """Revoke the OAuth grant of a deleted Gmail mailbox, best effort (gap D1).

    ``row`` is the ``RETURNING`` row of the ``DELETE``. It carries the
    provider, the address and the credential blob. Only a ``gmail`` row gets
    a call. Microsoft has no revoke for one delegated grant, so an Outlook
    row revokes nothing. An IMAP row has no grant.

    Google keeps one grant for each Google account and OAuth client. So when
    :func:`grant_shared_elsewhere` does not answer ``GRANT_FREE``, the revoke
    is skipped, and ``email.revoke_skipped`` logs the verdict as its reason.
    ``shared_grant`` is an INFO line. ``owner_blind``, ``check_failed`` and
    ``no_address`` are WARNING lines, because each one is a fault. A wrong revoke breaks a live mailbox of another member.
    A skipped one leaves a grant that the member can remove at Google.

    Google answers 200 when the grant ends. Any other outcome logs
    ``email.revoke_failed`` with the provider and a fixed reason, and the
    route still answers 204. The call has a bound of ``REVOKE_TIMEOUT_S``.
    It never raises.
    """
    if row.provider != "gmail":
        return
    verdict = await grant_shared_elsewhere(
        open_session, getattr(row, "email_address", None), account_id)
    if verdict != GRANT_FREE:
        emit = _log.info if verdict == GRANT_SHARED else _log.warning
        emit("email.revoke_skipped", account_id=account_id, provider="gmail",
             reason=verdict)
        return
    reason = await _revoke_reason(row.credentials_encrypted)
    if reason is None:
        _log.info("email.disconnect.grant_revoked", account_id=account_id,
                  provider="gmail")
        return
    _log.warning("email.revoke_failed", account_id=account_id,
                 provider="gmail", reason=reason)


async def _revoke_reason(creds_blob: Any) -> str | None:
    """Run the revoke. None when Google ended the grant, else the reason."""
    try:
        creds, _store = _decrypt_credentials(creds_blob)
    except Exception:
        return "unreadable_credentials"
    token = _grant_token(creds)
    if token is None:
        return "no_token"
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_S):
            status = await _post_revoke(token)
    except TimeoutError:
        return "timeout"
    except httpx.HTTPError:
        return "transport_error"
    except Exception as exc:
        return type(exc).__name__
    return None if status == 200 else f"http_{status}"


# ── step 8: the Redis keys of the mailbox, and the My Day bump ──────────────


def _mailbox_namespaces() -> tuple[str, ...]:
    """The Redis namespaces whose keys carry the mailbox id as the first part.

    * ``digest.py``: the brief cache, ``key(ns, account_id, day, digest)``.
    * ``automation/replyzero.py``: the status back-off mark,
      ``key(ns, account_id, thread_id, message_id)``.
    * ``email_ingestion/llm_cap.py``: the daily budget count,
      ``key(ns, account_id, day)``.

    The HTML, the inline pictures and the attachment bytes carry a message row
    id, not the mailbox id. They end within one hour. The imports are local,
    so this module adds no import cycle.
    """
    from email_ingestion.llm_cap import BUDGET_NAMESPACE
    from gateway.routes.email.automation.replyzero import STATUS_BACKOFF_NAMESPACE
    from gateway.routes.email.digest import BRIEF_CACHE_NAMESPACE

    return (BRIEF_CACHE_NAMESPACE, STATUS_BACKOFF_NAMESPACE, BUDGET_NAMESPACE)


def _redis() -> Any:
    """The tenant Redis client. Tests replace it."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


async def _drop_namespace(client: Any, namespace: str, mailbox: str) -> int:
    """Delete each key of one namespace under one mailbox. The count.

    The caller binds the organization. ``match`` builds a pattern under the
    tenant prefix, so a scan never leaves the tenant. Each key that the scan
    gives is built again with ``key``, because the client takes a ``TenantKey``
    only. The rest of the key after the mailbox id is ONE part, so the wire
    key of the new ``TenantKey`` is the key that the scan gave.
    """
    from acb_common.tenant_redis import key, match

    prefix = key(namespace, mailbox).value + ":"
    batch: list[Any] = []
    dropped = 0
    async for raw in client.scan_iter(match(namespace, mailbox)):
        name = raw.decode() if isinstance(raw, bytes) else str(raw)
        rest = name[len(prefix):] if name.startswith(prefix) else ""
        if not rest:
            continue
        try:
            batch.append(key(namespace, mailbox, rest))
        except ValueError:
            continue
        if len(batch) >= _CACHE_DELETE_BATCH:
            dropped += int(await client.delete(*batch) or 0)
            batch = []
    if batch:
        dropped += int(await client.delete(*batch) or 0)
    return dropped


async def drop_mailbox_cache(org: str | None, owner: str, account_id: str) -> None:
    """Delete the Redis keys of a deleted mailbox, and bump the owner's My Day.

    ``org`` comes from the row that the ownership read found, never from
    request input (R5). The keys use the mailbox id in its canonical UUID
    form, as the writers do. The deletes have a bound of
    ``CACHE_DROP_TIMEOUT_S``. A failure logs the error class only. It never
    raises.

    The My Day bump does nothing while ``MY_DAY_CACHE`` is off.
    """
    if not org:
        return
    try:
        mailbox = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        mailbox = None
    if mailbox is not None:
        await _drop_keys(org, mailbox)
    try:
        from gateway.routes.shell import cache as feed_cache

        await feed_cache.bump_member(org, owner)
    except Exception as exc:
        _log.warning("email.disconnect.my_day_bump_failed",
                     account_id=mailbox or str(account_id)[:64],
                     error=type(exc).__name__)


async def _drop_keys(org: str, mailbox: str) -> None:
    from acb_common.tenant_redis import organization_scope

    dropped = 0
    try:
        async with asyncio.timeout(CACHE_DROP_TIMEOUT_S):
            with organization_scope(org):
                client = _redis()
                for namespace in _mailbox_namespaces():
                    dropped += await _drop_namespace(client, namespace, mailbox)
    except Exception as exc:
        _log.warning("email.disconnect.cache_drop_failed", account_id=mailbox,
                     error=type(exc).__name__, dropped=dropped)
        return
    _log.info("email.disconnect.cache_dropped", account_id=mailbox,
              keys=dropped)
