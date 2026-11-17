"""The act record: the ONE writer of ``member_action`` (WS-PZ MM-1, task 2878).

Spec: ``project-docs/specs/personalization.md`` §19.4 and §19.10, and
``project-docs/specs/proactive_loop.md`` §5.3. Table: migration
``member_action`` (453 at build). ``tests/unit/test_member_action_r8.py`` runs
this SQL on a real Postgres.

**What a row is.** One act of one member: a send, a reply, a forward, a done,
an archive, a snooze, a label, a delegate, a My Day card press, or the answer
to a chat card. A row holds the kind of the act, the kind and the id of the
item, a few derived features of the item, and the addresses that the act sent
the item to. **It never holds a subject or a body.** :data:`FEATURE_KEYS` is a
closed list, and a text value is at most :data:`MAX_TEXT` characters.

**Whose act counts.** ``via`` says who did the act. ``app`` is a press in the
app and ``card`` is a yes on a chat card. Both are a choice of the member.
``rule`` is an automatic act. The writer records it and :func:`counts_as_choice`
says False for it, so a learner never reads a rule as the member's choice.

**Gates.** :func:`record_act` writes nothing, and says False, when
``MEMBER_ACTIONS_ORGS`` does not hold the org (:func:`enabled_for`, default
OFF), when the act is in a shared mailbox (§19.4), or when the member turned
``learning`` off (D-PZ-7).

**``via`` is the seam's word.** A route seam says ``app``. A chat card that
calls the same route is also recorded as ``app`` until MM-1b (task 3614) adds
a card marker, so that one yes is one row.

**Tenancy (R5).** The caller opens ``acb_common.db.tenant_session(org)`` and
passes the session. The INSERT writes no ``organization_id``: the column
DEFAULT reads the bound tenant. The org of the flag check comes from the same
session the caller bound, never from request input. The member comes from the
session of the caller, never from a request body.

**It never fails the act.** An act seam calls :func:`record_act` after the
member's act. A failure of the record must not undo the act, so the call runs
in a savepoint, logs the class of the error only, and answers False.

**One writer.** This is the only module that runs an INSERT on
``member_action``. The fence ``tests/unit/test_member_action_writer.py`` scans
the source of the repo for a second one.

**Retention.** A row lives :data:`RETENTION_DAYS` days (D-PZ-51).
:data:`PRUNE_SQL` deletes the older rows of the bound org. The member purge
deletes the rows of a member (``routes/admin/members.py``).
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

from sqlalchemy import text

from acb_common._log import get_logger
from acb_common.pg_text import storable

_log = get_logger("acb_common.member_actions")

#: How long a row lives, in days (D-PZ-51).
RETENTION_DAYS = 180

#: The longest text value in ``features``, in characters. A longer value is a
#: sign of a subject or a body, so the writer refuses it (§19.10).
MAX_TEXT = 120

#: The most addresses that one act may name in ``targets``.
MAX_TARGETS = 20

#: The closed set of acts, by app (§19.4, and §5.3 of ``proactive_loop.md``).
#: A new act is a new entry here and a new seam, never a free string.
ACTS: dict[str, frozenset[str]] = {
    "email": frozenset({
        "reply", "forward", "send", "mark_done", "archive", "snooze", "label",
        "delegate", "draft_sent",
    }),
    "chat": frozenset({"card_approve", "card_reject", "card_revise"}),
    "my_day": frozenset({
        "card_acted", "card_later", "card_snooze", "card_dismiss",
        "card_not_useful", "kind_mute",
    }),
    "reach": frozenset({"alert_useful", "alert_not_useful"}),
}

#: Who did the act, as the CHECK ``member_action_via`` holds them.
VIAS: tuple[str, ...] = ("app", "card", "rule")

#: The values of ``via`` that are a choice of the member.
CHOICE_VIAS: frozenset[str] = frozenset({"app", "card"})

#: The closed set of keys of ``features``: derived features of the item, never
#: its text. ``edit_distance`` is one number (§5.3 of ``proactive_loop.md``).
FEATURE_KEYS: frozenset[str] = frozenset({
    "sender_domain", "sender_kind", "triage_label", "is_reply", "new_thread",
    "edit_distance", "card_kind", "outcome", "signal_kind",
})

#: The ONE INSERT of ``member_action``. No ``organization_id``: the column
#: DEFAULT reads the bound tenant, and RLS holds the row to it.
INSERT_SQL = (
    "INSERT INTO member_action "
    "(member_email, app, act, via, item_kind, item_id, mailbox_id, features, targets) "
    "VALUES (:m, :app, :act, :via, :item_kind, :item_id, :mailbox, "
    "CAST(:features AS jsonb), CAST(:targets AS text[]))"
)

#: The retention delete, for the bound org. :func:`prune` runs it.
PRUNE_SQL = (
    "DELETE FROM member_action "
    "WHERE at < now() - make_interval(days => :days)"
)

#: The member purge: the rows of one member in the bound org. The purge list
#: of ``routes/admin/members.py`` names the same clause.
PURGE_WHERE = (
    "lower(member_email) = :email AND organization_id = "
    "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
)


class ActError(ValueError):
    """The act does not fit the record. The message holds no value of the act."""


def org_of(user: Any) -> str:
    """The organization of the session user, or "" when it has none. A seam
    passes it as ``org``: the user comes from ``get_current_user``, never from
    request input (R5), and a caller with no org records nothing."""
    return str(getattr(user, "organization_id", None) or "")


def counts_as_choice(via: str) -> bool:
    """True when an act with this ``via`` is a choice of the member. A learner
    reads only such rows. ``rule`` is never a choice (§19.4)."""
    return via in CHOICE_VIAS


def domain_of(address: Any) -> str | None:
    """The domain of an address, in lower case, or None. A derived feature of
    an item (``sender_domain``), never a part of its text."""
    if not isinstance(address, str) or "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().strip(">").lower()
    return domain[:MAX_TEXT] or None


def _parse_orgs(raw: str) -> frozenset[str]:
    """The ids in *raw*, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def enabled_for(org_id: str | None) -> bool:
    """True when ``MEMBER_ACTIONS_ORGS`` holds the org, or holds ``*``. Default
    OFF, and no org answers False. Fails closed. The setting is read on each
    call, so a flag reload applies a change with no restart."""
    try:
        org = str(org_id or "").strip().lower()
        if not org:
            return False
        from acb_common import get_settings

        listed = _parse_orgs(str(getattr(get_settings(), "member_actions_orgs", "") or ""))
        return "*" in listed or org in listed
    except Exception:
        return False


def _name(value: Any, what: str, cap: int) -> str:
    """A name of the record: a non-empty string of at most *cap* characters."""
    if not isinstance(value, str) or not value.strip() or len(value) > cap:
        raise ActError(f"{what} is not a short string")
    return value.strip()


def _feature(key: str, value: Any) -> Any:
    """One value of ``features``: a bool, a finite number, or a short string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ActError(f"feature {key} is not finite")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise ActError(f"feature {key} is longer than {MAX_TEXT} characters")
        return storable(value)
    raise ActError(f"feature {key} has no scalar value")


def _features(features: dict[str, Any] | None) -> dict[str, Any]:
    """The features of an act. A key outside :data:`FEATURE_KEYS` is refused,
    so no ``subject`` or ``body`` key can reach the table. A None value drops."""
    out: dict[str, Any] = {}
    for key, value in (features or {}).items():
        if key not in FEATURE_KEYS:
            raise ActError("a feature key is not in the closed list")
        if value is None:
            continue
        out[key] = _feature(key, value)
    return out


def _targets(targets: list[str] | tuple[str, ...] | None) -> list[str]:
    """The addresses that the act sent the item to, in lower case, without
    repeats. Each is an address and at most 254 characters."""
    out: list[str] = []
    for item in targets or ():
        if not isinstance(item, str) or "@" not in item or len(item) > 254:
            raise ActError("a target is not an address")
        low = item.strip().lower()
        if low and low not in out:
            out.append(low)
    if len(out) > MAX_TARGETS:
        raise ActError("too many targets")
    return out


def build_row(*, member: str, app: str, act: str, via: str, item_kind: str,
              item_id: str, mailbox_id: str | None = None,
              features: dict[str, Any] | None = None,
              targets: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """The bind parameters of one act, checked. Raises :class:`ActError` when
    the act does not fit the record. Pure: it opens no session."""
    if app not in ACTS or act not in ACTS[app]:
        raise ActError("the app and act are not in the closed list")
    if via not in VIAS:
        raise ActError("via is not app, card or rule")
    who = _name(member, "member", 254).lower()
    if "@" not in who:
        raise ActError("member is not an address")
    return {
        "m": who, "app": app, "act": act, "via": via,
        "item_kind": _name(item_kind, "item_kind", 40),
        "item_id": _name(item_id, "item_id", 200),
        "mailbox": None if mailbox_id is None else _name(mailbox_id, "mailbox_id", 200),
        "features": json.dumps(_features(features), sort_keys=True),
        "targets": _targets(targets),
    }


#: A shared mailbox (WS-65, D-SM-1) records no act until its spec says how
#: (§19.4): a team's triage is not one member's habit. ``shared_since`` is the
#: mark of such a mailbox, and RLS binds the read to the org.
SHARED_MAILBOX_SQL = (
    "SELECT 1 FROM email_accounts "
    "WHERE id = CAST(:mb AS uuid) AND shared_since IS NOT NULL"
)


async def _is_shared_mailbox(db: Any, mailbox_id: str | None) -> bool:
    """True when *mailbox_id* names a shared mailbox. A value that is not an
    id reads as not shared: the INSERT takes any short name."""
    if mailbox_id is None:
        return False
    try:
        uuid.UUID(mailbox_id)
    except ValueError:
        return False
    return (await db.execute(text(SHARED_MAILBOX_SQL), {"mb": mailbox_id})).first() is not None


async def learning_on(db: Any, org: str, member: str) -> bool:
    """False only when the member turned ``learning`` off (D-PZ-7). A member
    with no profile has it on. The read goes through the profile module, so
    this file never names the profile column."""
    from acb_common import assistant_profile as profile

    row = (await db.execute(
        text(profile.READ_SQL), {"org": org, "m": member})).first()
    stored = profile.stored_profile(row[0] if row is not None else None)
    return stored.get("learning", True) is not False


async def record_act(db: Any, *, org: str, member: str, app: str, act: str,
                     via: str, item_kind: str, item_id: Any,
                     mailbox_id: str | None = None,
                     features: dict[str, Any] | None = None,
                     targets: list[str] | tuple[str, ...] | None = None) -> bool:
    """Record one act of *member*. True when a row was written.

    *db* is the session of the caller, bound to *org* by
    ``tenant_session``. It writes nothing, and answers False, when
    ``MEMBER_ACTIONS_ORGS`` does not hold *org* or the member turned
    ``learning`` off. It never raises: the log names the class of a failure
    only, and a savepoint keeps the transaction of the caller usable.
    """
    try:
        if not enabled_for(org):
            return False
        row = build_row(
            member=member, app=app, act=act, via=via, item_kind=item_kind,
            item_id=str(item_id) if isinstance(item_id, (str, int, uuid.UUID)) else "",
            mailbox_id=mailbox_id, features=features, targets=targets)
        async with db.begin_nested():
            if await _is_shared_mailbox(db, row["mailbox"]):
                return False
            if not await learning_on(db, org, row["m"]):
                return False
            await db.execute(text(INSERT_SQL), row)
        return True
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("member_actions.record_failed", error=type(exc).__name__)
        return False


async def prune(db: Any, days: int = RETENTION_DAYS) -> int:
    """Delete the rows of the bound org older than *days* days. The count of
    the rows that went. No bound tenant deletes nothing (RLS)."""
    result = await db.execute(text(PRUNE_SQL), {"days": int(days)})
    return int(result.rowcount or 0)
