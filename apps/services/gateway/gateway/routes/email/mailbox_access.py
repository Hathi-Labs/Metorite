"""The ONE mailbox access seam (WS-65 SM-2, ``shared_mailbox.md`` §8.1).

Each question "may this member read, send from or manage this mailbox" in
``routes/email`` goes through this module. Before WS-65 the gateway wrote the
answer by hand as ``user_id = :uid``, at 85 sites in 26 files. That predicate
is the member boundary inside one organization: row level security binds the
organization only (D15), so a query that drops it reads the mail of every
member.

**Two arms.**

* **Private.** The member is ``user_id`` of the row. A private mailbox has
  ``shared_since IS NULL``.
* **Shared.** The row has ``shared_since`` set (D-SM-1), and a row of
  ``email_mailbox_grants`` names ``org``, the member's address or a group of
  the member, at the level the act needs or higher (D-SM-2). ``user_id`` of a
  shared row is the connector, as a record. It gives no access (D-SM-3).

**The flag.** ``EMAIL_SHARED_MAILBOX`` (``Settings.email_shared_mailbox``,
default false) is read here and nowhere else. With it false, each builder
returns the private arm with the SQL text of before WS-65, character for
character. So no route answers differently, and a grant row gives nobody
access (§8.2 step 2, fence 7).

**D-SM-4, the feature.** A grant reaches only a member who holds
``feature:email``. The email router carries ``require_feature_router("email")``
(``core.router``), so the check runs before any route reads a grant. A
background job that a request starts runs inside that request. Do not call a
builder of this module from a path outside ``routes/email`` that skips the
gate.

**The levels.** ``read`` < ``send`` < ``manage``, and each level includes the
one before it. The owner of a private mailbox passes every level. A caller
that does not name a level gets ``manage``, so a site that nobody classified
fails closed for a shared mailbox.

**Defence in depth (R5).** The shared arm names the bound tenant
(``app.tenant_id``) on the mailbox and the grant, as
``_assert_account_owner(this_tenant=True)`` does. It holds for a role that
bypasses RLS too.

The seam fence is ``tests/unit/test_email_owner_predicate_seam.py``: a new
inline ``user_id = :uid`` under ``routes/email`` fails unless it carries the
marker ``# private-only:`` with a reason, or an entry of
``tests/unit/email_owner_predicate_allowlist.json``. R8:
``tests/unit/test_email_shared_mailbox_r8.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import text

Level = Literal["read", "send", "manage"]

#: The levels, lowest first. Each level includes the ones before it.
LEVELS: tuple[str, ...] = ("read", "send", "manage")


def _bound_org() -> str:
    """The SQL of the session's bound tenant, the one GUC predicate of the
    gateway (``tasks.settings._BOUND_ORG``). Imported at the call, as
    ``core._bound_org`` does, so this module takes no import of the tasks
    package at load."""
    from gateway.routes.tasks.settings import _BOUND_ORG

    return _BOUND_ORG


def shared_enabled() -> bool:
    """The ONE reader of ``EMAIL_SHARED_MAILBOX``. False by default."""
    from acb_common import get_settings

    return bool(getattr(get_settings(), "email_shared_mailbox", False))


def levels_at_least(level: str) -> tuple[str, ...]:
    """The levels that satisfy *level*: ``send`` -> ``("send", "manage")``."""
    if level not in LEVELS:
        raise ValueError(f"unknown mailbox level {level!r}")
    return LEVELS[LEVELS.index(level):]


def _groups_sql(uid: str, row: str) -> str:
    """The ``group:<slug>`` subjects of the member ``:uid`` in the org of *row*.

    It reuses ``projects.core._MY_GROUPS_SQL``, the one query of a member's
    groups, and binds the member once more as ``:uid``. There is no copy.
    """
    from gateway.routes.projects.core import _MY_GROUPS_SQL

    if _MY_GROUPS_SQL.count(":email") != 1:  # pragma: no cover — a guard
        raise RuntimeError(
            "routes/projects/core._MY_GROUPS_SQL changed shape. It must bind the "
            "member's email once as :email."
        )
    body = _MY_GROUPS_SQL.replace(":email", f"lower(CAST(:{uid} AS text))").strip()
    return f"{body} AND g.organization_id = {row}.organization_id"


def mailbox_predicate(
    alias: str | None = None, *, uid: str = "uid", level: str = "manage",
) -> str:
    """The predicate "the member ``:<uid>`` may act on this mailbox at *level*".

    *alias* is the alias of ``email_accounts`` in the caller's statement, or
    ``None`` when the statement reads ``email_accounts`` with no alias. *uid*
    names the bind parameter that holds the member's email.

    With the flag off it is ``<alias>.user_id = :<uid>``, the text of before
    WS-65.
    """
    allowed = levels_at_least(level)
    col = f"{alias}." if alias else ""
    private = f"{col}user_id = :{uid}"
    if not shared_enabled():
        return private
    row = alias or "email_accounts"
    levels = ", ".join(f"'{lv}'" for lv in allowed)
    return (
        f"(({private} AND {row}.shared_since IS NULL)"
        f" OR ({row}.shared_since IS NOT NULL"
        f" AND {row}.organization_id = {_bound_org()}"
        f" AND EXISTS (SELECT 1 FROM email_mailbox_grants emg"
        f" WHERE emg.account_id = {row}.id"
        f" AND emg.organization_id = {row}.organization_id"
        f" AND emg.level IN ({levels})"
        f" AND (emg.subject = 'org'"
        f" OR emg.subject = lower(CAST(:{uid} AS text))"
        f" OR emg.subject IN ({_groups_sql(uid, row)})))))"
    )


def mailbox_ids_sql(*, uid: str = "uid", level: str = "manage") -> str:
    """The ids of the mailboxes that ``:<uid>`` may act on at *level*, as a
    subquery with no alias. With the flag off it is
    ``SELECT id FROM email_accounts WHERE user_id = :<uid>``."""
    return f"SELECT id FROM email_accounts WHERE {mailbox_predicate(uid=uid, level=level)}"


def private_predicate(alias: str | None = None, *, uid: str = "uid") -> str:
    """The predicate of the member's PRIVATE mailboxes only, at every flag.

    For a site that must never see a shared mailbox: "self" (D-SM-5), the
    member's default mailbox, the reconnect of the member's own row. With
    the flag off it is the text of before WS-65. With it on, it adds
    ``shared_since IS NULL``.
    """
    col = f"{alias}." if alias else ""
    private = f"{col}user_id = :{uid}"
    if not shared_enabled():
        return private
    return f"({private} AND {col}shared_since IS NULL)"


async def may_access(
    db: Any, account_id: str, user_email: str, level: str = "manage", *,
    this_tenant: bool = False,
) -> bool:
    """True when *user_email* may act on the mailbox *account_id* at *level*."""
    sql = (
        "SELECT 1 FROM email_accounts WHERE id = :id AND "
        + mailbox_predicate(uid="uid", level=level)
    )
    if this_tenant:
        sql += f" AND organization_id = {_bound_org()}"
    res = await db.execute(text(sql), {"id": account_id, "uid": user_email})
    return res.fetchone() is not None


async def assert_mailbox_access(
    db: Any, account_id: str, user_email: str, level: str = "manage", *,
    this_tenant: bool = False,
) -> None:
    """Raise 404 unless *user_email* may act on *account_id* at *level*.

    404, never 403: a member who may not act on a mailbox does not learn
    that it exists.
    """
    if not await may_access(db, account_id, user_email, level, this_tenant=this_tenant):
        raise HTTPException(status_code=404, detail="Account not found")
