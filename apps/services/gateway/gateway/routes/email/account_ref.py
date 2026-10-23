"""The ``account_id`` that a caller sends to an email route.

The one reader of that value for every email route that takes it. Before
this module, each route bound the raw value as a UUID. The chat agent sent
``all``, ``__all__``, ``placeholder`` and a mailbox address, and asyncpg
then refused the value inside the query. The route answered 500 (production
log, October 2026: 28 answers of 500 on ``GET /email/messages``).

:func:`resolve_account_filter` reads the value in four shapes:

* empty, or one of :data:`ALL_ACCOUNTS` in any case: no mailbox filter. A
  route that needs one mailbox answers 422.
* a UUID: that id, in canonical form (``str(uuid.UUID(value))``). The route
  then applies its own owner check, as it did before.
* a value with ``@``: a mailbox address. The read finds it only among the
  mailboxes of the acting member in the bound tenant. The predicate is the
  owner predicate that the routes use (``user_id``), and the tenant predicate
  narrows it. No match answers 404.
* anything else: 422, with :data:`BAD_ACCOUNT_ID_DETAIL`.

R5: the member comes from the session, and the tenant from the GUC of the
session. The request gives the address only.

Fence: ``tests/unit/test_email_account_ref.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from gateway.routes.email.mailbox_access import mailbox_predicate
from gateway.routes.email.core import _bound_org, _tenant_session
from sqlalchemy import text

#: The values that mean "every mailbox of the member". Compared in lower case.
ALL_ACCOUNTS = frozenset({"all", "__all__", "*", "any"})

#: The detail of a 422 for a value of no known shape.
BAD_ACCOUNT_ID_DETAIL = (
    'account_id must be an account id from list_accounts, an address of one '
    'of your accounts, or "all"'
)

#: The detail of a 422 when a route needs one mailbox and gets "all".
ONE_ACCOUNT_DETAIL = (
    "This request needs one mailbox. account_id must be an account id from "
    "list_accounts or an address of one of your accounts"
)

#: The detail of a 404 for an address that is not a mailbox of the member.
#: It names no address: the tool layer removes an address from a detail
#: (``safe_detail``), so the text must read well without one.
NO_SUCH_ADDRESS_DETAIL = (
    "That address is not one of your mailboxes. Call list_accounts for your "
    "mailbox ids."
)

#: The detail of a 422 for an address that two mailboxes of the member share.
AMBIGUOUS_ADDRESS_DETAIL = (
    "More than one of your mailboxes has that address. Send the mailbox id "
    "from list_accounts."
)

#: The read of a mailbox by its address. The owner predicate is the one of
#: the routes, and the tenant predicate is defence in depth (R5): RLS keeps
#: another tenant out for ``acb_app``, and this holds also for a role that
#: bypasses RLS.
ADDRESS_LOOKUP_SQL = (
    "SELECT id FROM email_accounts "
    "WHERE {owner} AND LOWER(email_address) = LOWER(:addr) "
    "AND organization_id = {bound_org} "
    "ORDER BY id LIMIT 2"
)


async def _rows_for_address(db: Any, address: str, owner: str) -> list[Any]:
    """The ids of the mailboxes of *owner* in the bound tenant with *address*."""
    return list((await db.execute(
        text(ADDRESS_LOOKUP_SQL.format(
            bound_org=_bound_org(),
            owner=mailbox_predicate(uid="uid", level="read"))),
        {"uid": owner, "addr": address},
    )).fetchall())


def account_ref_kind(raw: str | None) -> str:
    """The shape of *raw*: ``all``, ``uuid``, ``address`` or ``bad``.

    No database read. :func:`resolve_account_filter` acts on the answer.
    """
    value = (raw or "").strip()
    if not value or value.lower() in ALL_ACCOUNTS:
        return "all"
    if "@" in value:
        return "address"
    try:
        uuid.UUID(value)
    except ValueError:
        return "bad"
    return "uuid"


async def resolve_account_filter(
    db: Any | None, raw: str | None, owner: str, *, required: bool = False,
    bad_is_not_found: bool = False,
) -> str | None:
    """The mailbox id that a route filters by, or None for every mailbox.

    ``owner`` is the email of the acting member (``user.email``). ``required``
    is for a route that acts on one mailbox: "all" then answers 422.

    Give the open session of the route as ``db``. A route that opens no
    session before it reads the value gives ``None``, and only an address then
    opens a session of its own for the one read.

    ``bad_is_not_found`` keeps an older contract: a value of no known shape
    answers 404 "Account not found", not 422. Only the storage routes use it,
    because their fence pins that answer (``test_email_storage_limit.py``).

    Never 500 for a value of the request. See the module docstring for the
    four shapes.
    """
    kind = account_ref_kind(raw)
    if kind == "all":
        if required:
            raise HTTPException(status_code=422, detail=ONE_ACCOUNT_DETAIL)
        return None
    value = (raw or "").strip()
    if kind == "uuid":
        # Canonical: asyncpg takes 32 to 36 characters only, and
        # ``uuid.UUID`` also reads the braced and the ``urn:uuid:`` forms.
        return str(uuid.UUID(value))
    if kind == "bad":
        if bad_is_not_found:
            raise HTTPException(status_code=404, detail="Account not found")
        raise HTTPException(status_code=422, detail=BAD_ACCOUNT_ID_DETAIL)
    if db is None:
        async with _tenant_session() as own:
            rows = await _rows_for_address(own, value, owner)
    else:
        rows = await _rows_for_address(db, value, owner)
    if not rows:
        raise HTTPException(
            status_code=404, detail=NO_SUCH_ADDRESS_DETAIL)
    if len(rows) > 1:
        raise HTTPException(
            status_code=422, detail=AMBIGUOUS_ADDRESS_DETAIL)
    return str(rows[0].id)
