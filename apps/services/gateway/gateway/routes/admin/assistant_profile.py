"""``/auth/me/assistant-profile`` — the member's assistant profile (WS-63 PZ-1).

Spec: ``project-docs/specs/personalization.md`` §4. The column is
``user_settings.assistant_profile`` (migration 294). It holds what the member
told the assistant to keep, in this shape (§4.2)::

    {"v": 1, "learning": true,
     "address": {"name", "formality", "greeting"},
     "style":   {"length", "language", "notes"},
     "aliases": [{"id", "alias", "email"}],
     "facts":   [{"id", "text", "kind", "source", "at"}]}

=========================  ===============================================
Route                      Does
=========================  ===============================================
``GET``                    The profile, or the empty profile
``PUT``                    Body ``{key, value}``, ``key`` is ``learning``,
                           ``address`` or ``style``. Merges that one key
``POST .../facts``         Add one fact. The server sets ``id``,
                           ``source`` (``settings``) and ``at``
``PATCH .../facts/{id}``   Change the text or the kind
``DELETE .../facts/{id}``  Forget one fact
``.../aliases[/{id}]``     The same three for aliases
``DELETE``                 Forget everything. The column becomes NULL
``GET .../address``        ``{"name"}``, the form of address of §5.2 (PZ-4)
``GET .../export``         The whole profile as a download, JSON or text
                           (PZ-9). Read-only, and only the member's own
=========================  ===============================================

**The shape follows** ``routes/shell/prefs.py``. The write is strict and the
read is lenient: a stored key or item of the wrong shape drops from the
answer. A PUT merges ONE key with ``jsonb_set`` and never replaces the
column, so a layout save (``PUT /auth/me/shell``) and a My Day save keep the
profile, and the profile keeps them.

**A fact or an alias changes in ONE statement.** The add appends inside the
upsert, and an edit or a delete rebuilds the list with ``jsonb_agg`` inside
the ``UPDATE``. The row lock of that statement serialises two writes of one
member, so a concurrent add loses nothing. No route reads the list in one
statement and writes it in a second.

**The caps** (§4.2). Each cap is a 422 that names the cap. The count caps and
the 6,000-character budget of the whole profile are checked on the stored
value that the write returns, inside the same transaction. A write that
breaks one raises, and ``tenant_session`` rolls it back.

**Aliases.** The address must be a person or a member of the bound org,
who has not left (PZ-5). ``assistant_profile_people.email_in_org`` is the one
check, in the same session as the write. An address outside the org is a
422, :data:`OUTSIDE_ORG`. The check needs no feature, because the member
typed the address and the answer is only yes or no.

**A member with no settings row.** The first write makes the row with the
calendar seed (``_seed_from_work_schedule``), exactly as ``prefs.py`` does.

**A write from the chat** (PZ-6a, §5.5). The four profile tools of
``acb_skills.profile_tools`` send ``X-Profile-Via: chat`` or ``whatsapp``.
The header can only restrict or label, and never widen. With it, a write
refuses with 409 ``learning_off`` when the stored ``learning`` is false, a
PUT of ``learning`` itself is a 422, and a new fact takes the header as its
``source``. Without it, a write is a Settings write and ignores ``learning``.
Any other value of the header is a 422. The web relay does not forward it.

The member comes from the session and the tenant from the bound session,
never from the body (R5, R11). Each body forbids an extra field. The routes
need no feature, as ``/auth/me/shell`` needs none. The routes take no flag.
The per-turn block of PZ-2 reads the profile behind
``ASSISTANT_PROFILE_ENABLED``. The shape, the caps and the lenient read are
``acb_common.assistant_profile``, the one parser. Fences:
``tests/unit/test_assistant_profile.py`` and
``tests/unit/test_assistant_profile_r8.py`` (R8).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user

# The shape, the caps and the lenient read live in ONE parser, which the
# executor's per-turn block reads too (WS-63 PZ-2). This module keeps the
# models, the write SQL and the routes.
from acb_common.assistant_profile import (  # noqa: F401 — re-exported names
    ALIAS_MAX,
    ALIASES_MAX,
    EMAIL_MAX,
    FACT_KINDS,
    FACT_MAX,
    FACT_SOURCES,
    FACTS_MAX,
    FORMALITY,
    GREETING,
    LANGUAGE_MAX,
    LAST_CHANGE,
    LENGTH,
    NAME_MAX,
    NOTE_MAX,
    NOTES_MAX,
    PROFILE_MAX,
    PROFILE_VERSION,
    PUT_KEYS,
    UNDO_WINDOW_S,
    _as_dict,
    _choice,
    _clean_address,
    _clean_style,
    _email,
    _lenient,
    _object,
    _stored_alias,
    _stored_fact,
    _stored_keys,
    _text,
    empty_profile,
    mark_changed,
    profile_size,
    stored_profile,
)
from fastapi import Body, Depends, Header, HTTPException, Query, Response
from gateway.db import tenant_session as _tenant_session
from gateway.routes.admin.me import me_router
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

FactKind = Literal["about", "routine", "preference", "work", "relationship"]


# ── Strict cleaning (the write) ────────────────────────────────────────────


def _clean_learning(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("learning is true or false")
    return value


_CLEAN = {"learning": _clean_learning, "address": _clean_address, "style": _clean_style}


class ProfilePut(BaseModel):
    """What ``PUT /auth/me/assistant-profile`` accepts: one key, its value."""

    model_config = ConfigDict(extra="forbid")

    key: Literal["learning", "address", "style"]
    value: Any

    @model_validator(mode="after")
    def _value(self) -> ProfilePut:
        self.value = _CLEAN[self.key](self.value)
        return self


class FactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: FactKind

    @field_validator("text", mode="before")
    @classmethod
    def _t(cls, v: Any) -> str:
        return _text(v, FACT_MAX, "a fact")


class FactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    kind: FactKind | None = None

    @field_validator("text", mode="before")
    @classmethod
    def _t(cls, v: Any) -> str:
        return _text(v, FACT_MAX, "a fact")

    @model_validator(mode="after")
    def _some(self) -> FactPatch:
        if not self.model_fields_set or any(
                getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("a change names the text or the kind")
        return self


class AliasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    email: str

    @field_validator("alias", mode="before")
    @classmethod
    def _a(cls, v: Any) -> str:
        return _text(v, ALIAS_MAX, "an alias")

    @field_validator("email", mode="before")
    @classmethod
    def _e(cls, v: Any) -> str:
        return _email(v)


class AliasPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str | None = None
    email: str | None = None

    @field_validator("alias", mode="before")
    @classmethod
    def _a(cls, v: Any) -> str:
        return _text(v, ALIAS_MAX, "an alias")

    @field_validator("email", mode="before")
    @classmethod
    def _e(cls, v: Any) -> str:
        return _email(v)

    @model_validator(mode="after")
    def _some(self) -> AliasPatch:
        if not self.model_fields_set or any(
                getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("a change names the alias or the email")
        return self


# ── SQL ────────────────────────────────────────────────────────────────────
#
# Every statement names the bound tenant beside RLS, not instead of it, as
# ``me.py`` does. ``CAST`` and never ``::`` beside a bind name, because
# SQLAlchemy misreads it (``test_sql_bindparam_jsonb_cast.py``). ``{org}``
# is the only placeholder, and ``_sql`` fills it with ``_BOUND_ORG``.

_READ_SQL = (
    "SELECT assistant_profile FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)

#: The stored column as an object. NULL, or a stray scalar, counts as empty.
_BASE = (
    "CASE WHEN jsonb_typeof(user_settings.assistant_profile) = 'object' "
    "THEN user_settings.assistant_profile ELSE CAST('{}' AS jsonb) END"
)
#: The base with the version set.
_VERSIONED = f"jsonb_set({_BASE}, ARRAY['v'], CAST('1' AS jsonb), true)"

#: PUT, a new row: the version and the one key.
_NEW_KEY = "jsonb_build_object('v', 1, CAST(:pkey AS text), CAST(:pval AS jsonb))"
#: PUT, an existing row: merge the one key, keep every other key.
_MERGE_KEY = f"jsonb_set({_VERSIONED}, ARRAY[CAST(:pkey AS text)], CAST(:pval AS jsonb), true)"

#: The stored list ``:lkey`` (``facts`` or ``aliases``). Not an array counts
#: as empty, so ``jsonb_array_elements`` never sees an object.
_LIST = (
    "CASE WHEN jsonb_typeof(user_settings.assistant_profile -> CAST(:lkey AS text)) "
    "= 'array' THEN user_settings.assistant_profile -> CAST(:lkey AS text) "
    "ELSE CAST('[]' AS jsonb) END"
)

#: POST, a new row: the version and a list of one item.
_NEW_ITEM = (
    "jsonb_build_object('v', 1, CAST(:lkey AS text), "
    "jsonb_build_array(CAST(:item AS jsonb)))"
)
#: POST, an existing row: append the item to the list, in the one statement.
_APPEND_ITEM = (
    f"jsonb_set({_VERSIONED}, ARRAY[CAST(:lkey AS text)], "
    f"({_LIST}) || jsonb_build_array(CAST(:item AS jsonb)), true)"
)

#: The row of this member holds an item ``:iid`` in the list ``:lkey``.
_HAS_ITEM = (
    "organization_id = {org} AND user_id = :uid AND EXISTS ("
    f"SELECT 1 FROM jsonb_array_elements({_LIST}) AS x(e) "
    "WHERE x.e ->> 'id' = CAST(:iid AS text))"
)

#: PATCH: rebuild the list in order, with the one item merged.
_PATCH_ITEM_EXPR = (
    "jsonb_set("
    "user_settings.assistant_profile, ARRAY[CAST(:lkey AS text)], ("
    "SELECT jsonb_agg(CASE WHEN t.e ->> 'id' = CAST(:iid AS text) "
    "THEN t.e || CAST(:patch AS jsonb) ELSE t.e END ORDER BY t.n) "
    f"FROM jsonb_array_elements({_LIST}) WITH ORDINALITY AS t(e, n)), true)"
)

#: DELETE one: rebuild the list in order, without the one item.
_DELETE_ITEM_EXPR = (
    "jsonb_set("
    "user_settings.assistant_profile, ARRAY[CAST(:lkey AS text)], COALESCE(("
    "SELECT jsonb_agg(t.e ORDER BY t.n) "
    f"FROM jsonb_array_elements({_LIST}) WITH ORDINALITY AS t(e, n) "
    "WHERE t.e ->> 'id' IS DISTINCT FROM CAST(:iid AS text)), "
    "CAST('[]' AS jsonb)), true)"
)


def _item_update(expr: str) -> str:
    return (f"UPDATE user_settings SET assistant_profile = {expr}, updated_at = now() "
            f"WHERE {_HAS_ITEM} RETURNING assistant_profile")


_PATCH_ITEM_SQL = _item_update(_PATCH_ITEM_EXPR)
_DELETE_ITEM_SQL = _item_update(_DELETE_ITEM_EXPR)

# ── The last chat change (PZ-6a fix round 1) ───────────────────────────────
#
# A write with ``X-Profile-Via`` records ONE ``last_change`` in the column,
# in the SAME statement as the change, so a spoken "undo that" can restore
# it (``POST .../undo``). ``:last`` holds the op and the time. The SQL adds
# the prior value from the row as it was before the statement. A Settings
# write records nothing. The lenient read, the block and the caps ignore
# the key (``acb_common.assistant_profile.LAST_CHANGE``).

#: The base of the record: ``{op, at, key|list, id, after}``.
_LAST = "CAST(:last AS jsonb)"
#: PUT, a new row: no prior value.
_PUT_LAST_NEW = f"{_LAST} || jsonb_build_object('prior', CAST('{{}}' AS jsonb))"
#: PUT, an existing row: the prior value of the one key.
_PUT_LAST_MERGE = (
    f"{_LAST} || jsonb_build_object('prior', COALESCE("
    "user_settings.assistant_profile -> CAST(:pkey AS text), CAST('{}' AS jsonb)))"
)


def _item_last(after: bool) -> str:
    """The record of a PATCH (*after*) or a DELETE: the item as it was, and
    for a PATCH the item as it becomes."""
    extra = ", 'after', x.e || CAST(:patch AS jsonb)" if after else ""
    return (
        f"{_LAST} || COALESCE((SELECT jsonb_build_object('prior', x.e{extra}) "
        f"FROM jsonb_array_elements({_LIST}) AS x(e) "
        "WHERE x.e ->> 'id' = CAST(:iid AS text) LIMIT 1), CAST('{}' AS jsonb))"
    )


def _with_last(expr: str, last: str) -> str:
    """*expr* with the record *last* under :data:`LAST_CHANGE`."""
    return f"jsonb_set({expr}, ARRAY['{LAST_CHANGE}'], {last}, true)"


_PATCH_ITEM_CHAT_SQL = _item_update(_with_last(_PATCH_ITEM_EXPR, _item_last(True)))
_DELETE_ITEM_CHAT_SQL = _item_update(_with_last(_DELETE_ITEM_EXPR, _item_last(False)))

#: A chat write on an existing row writes only while ``learning`` is not
#: false. The row lock of :func:`_learning_gate` covers a row that exists.
#: This covers the row that a Settings write makes between the gate and
#: the upsert, so a chat write never lands while the switch is off.
_LEARNING_NOT_OFF = (
    "(user_settings.assistant_profile -> 'learning') "
    "IS DISTINCT FROM CAST('false' AS jsonb)"
)

#: The undo: the whole column, under the row lock of ``_READ_LOCKED_SQL``.
_UNDO_WRITE_SQL = (
    "UPDATE user_settings SET assistant_profile = CAST(:p AS jsonb), "
    "updated_at = now() WHERE organization_id = {org} AND user_id = :uid"
)

#: DELETE all: the column becomes NULL. The row and its other columns stay.
_FORGET_SQL = (
    "UPDATE user_settings SET assistant_profile = NULL, updated_at = now() "
    "WHERE organization_id = {org} AND user_id = :uid"
)


def _sql(template: str) -> Any:
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return text(template.replace("{org}", _BOUND_ORG))


async def _read(db: Any, uid: str) -> Any:
    return (await db.execute(_sql(_READ_SQL), {"uid": uid})).first()


async def _upsert(db: Any, uid: str, new: str, merge: str, params: dict[str, Any],
                  where: str = "") -> Any:
    """One write of the column, in one statement: ``new`` for a new row,
    ``merge`` for an existing row. Returns the stored column after it.
    *where* guards the update of an existing row. When it stops the update,
    this returns None."""
    from gateway.routes.tasks.settings import _seed_from_work_schedule, upsert_settings_sql
    from sqlalchemy import text

    params = {"uid": uid, **params}
    seed: dict[str, Any] = {}
    # The read decides the seed only. It never carries a value into the
    # write, so two writes cannot write each other over.
    if await _read(db, uid) is None:
        seed = await _seed_from_work_schedule(db, uid)
    params.update(seed)
    cols = ["assistant_profile", *seed]

    def _ph(k: str) -> str:
        return new if k == "assistant_profile" else f":{k}"

    sql = upsert_settings_sql(cols, _ph, update=["assistant_profile"],
                              merge={"assistant_profile": merge})
    guard = f" WHERE {where}" if where else ""
    row = (await db.execute(text(f"{sql}{guard} RETURNING assistant_profile"),
                            params)).first()
    return _as_dict(row[0]) if row is not None else None


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


#: The values of ``X-Profile-Via``, the channel of a chat write (PZ-6a).
VIA_VALUES: tuple[str, ...] = ("chat", "whatsapp")

#: The 409 detail of a chat write while the member's switch is off (§7.3).
LEARNING_OFF = "learning_off"

#: The row of this member, locked, so a concurrent switch waits (PZ-6a).
_READ_LOCKED_SQL = _READ_SQL + " FOR UPDATE"


def _via(value: str | None) -> str | None:
    """The channel of a chat write, or ``None`` for a Settings write. The
    header can only restrict or label, so an unknown value is a 422 and
    never a Settings write."""
    # FastAPI gives a string or None. A direct call (a test) that passes no
    # header gets the ``Header`` default object, which is no header.
    if not isinstance(value, str):
        return None
    if value not in VIA_VALUES:
        raise _refuse(f"X-Profile-Via is one of {', '.join(VIA_VALUES)}")
    return value


async def _learning_gate(db: Any, uid: str, via: str | None) -> None:
    """Refuse a chat write while the member's switch is off (§7.3, D-PZ-2).
    A Settings write (*via* ``None``) passes, because the member acts there.

    A member with no settings row has the default, ``learning`` true, so a
    chat write passes, and its upsert makes the row. The lock takes no row
    then. So the upsert of a chat write carries :data:`_LEARNING_NOT_OFF`:
    when a Settings write made the row with the switch off in between, the
    upsert changes nothing, and the write is a 409 too."""
    if via is None:
        return
    row = (await db.execute(_sql(_READ_LOCKED_SQL), {"uid": uid})).first()
    if stored_profile(row[0] if row is not None else None)["learning"] is False:
        raise HTTPException(status_code=409, detail=LEARNING_OFF)


def _check_caps(stored: Any) -> None:
    """The caps that only the stored value can show. Raise inside the
    session, so ``tenant_session`` rolls the write back."""
    if not isinstance(stored, dict):
        return
    for key, cap in (("facts", FACTS_MAX), ("aliases", ALIASES_MAX)):
        items = stored.get(key)
        if isinstance(items, list) and len(items) > cap:
            raise _refuse(f"at most {cap} {key}")
    if profile_size({k: v for k, v in stored.items() if k != LAST_CHANGE}) > PROFILE_MAX:
        raise _refuse(f"profile exceeds {PROFILE_MAX} characters")


#: The 422 of a chat write that asks to forget the whole profile.
FORGET_ALL_IN_SETTINGS = "forget everything in Settings, not from the chat"


def _last(record: dict[str, Any]) -> str:
    """The ``:last`` bind of a chat write: the record and its time."""
    return json.dumps({**record, "at": int(time.time())})


#: The answers of :func:`undo_last_change` that refuse.
UNDO_NOTHING = "nothing_to_undo"
UNDO_TOO_OLD = "too_old"
UNDO_CHANGED = "changed_since"


def _item_index(items: list[Any], item_id: Any) -> int:
    for n, one in enumerate(items):
        if isinstance(one, dict) and one.get("id") == item_id:
            return n
    return -1


def undo_last_change(raw: Any, now: float) -> tuple[dict[str, Any] | None, str]:
    """The stored column with its last chat change undone, or ``(None, why)``.

    Pure. It applies :data:`LAST_CHANGE` only while it is younger than
    :data:`UNDO_WINDOW_S` and the change is still in place: a later
    Settings edit, or the card's own Undo, makes it :data:`UNDO_CHANGED`.
    The record leaves the column with the undo, so a second undo finds
    nothing. A PUT gets its prior whole object back. An added item goes,
    only while it equals the item that the chat wrote.
    A removed item comes back as it was. A changed item gets its prior form.
    """
    base = _as_dict(raw)
    if not isinstance(base, dict) or not isinstance(base.get(LAST_CHANGE), dict):
        return None, UNDO_NOTHING
    out = dict(base)
    last = out.pop(LAST_CHANGE)
    at = last.get("at")
    if not isinstance(at, (int, float)) or not 0 <= now - at <= UNDO_WINDOW_S:
        return None, UNDO_TOO_OLD
    op = last.get("op")
    if op == "put":
        key = last.get("key")
        if key not in ("address", "style") or out.get(key) != last.get("after"):
            return None, UNDO_CHANGED
        prior = last.get("prior")
        out[key] = prior if isinstance(prior, dict) else {}
        return out, ""
    lkey = last.get("list")
    if lkey not in ("facts", "aliases"):
        return None, UNDO_NOTHING
    items = list(out.get(lkey)) if isinstance(out.get(lkey), list) else []
    if op == "post":
        # The added item goes only while it is exactly as the chat wrote it.
        # A fact edited in Settings since then stays (PZ-6a fix round 2).
        n = _item_index(items, last.get("id"))
        if n < 0 or not isinstance(last.get("after"), dict) or items[n] != last["after"]:
            return None, UNDO_CHANGED
        del items[n]
    elif op == "delete":
        prior = last.get("prior")
        if not isinstance(prior, dict) or _item_index(items, prior.get("id")) >= 0:
            return None, UNDO_CHANGED
        items.append(prior)
    elif op == "patch":
        prior = last.get("prior")
        n = _item_index(items, last.get("id"))
        if not isinstance(prior, dict) or n < 0 or items[n] != last.get("after"):
            return None, UNDO_CHANGED
        items[n] = prior
    else:
        return None, UNDO_NOTHING
    out[lkey] = items
    return out, ""


# ── The routes ─────────────────────────────────────────────────────────────


def _member(user: UserContext) -> str:
    """The session's address, the key that ``/tasks/settings`` writes under."""
    uid = getattr(user, "email", None) or ""
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


def _validate(model: type[BaseModel], body: Any) -> Any:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        # No input in the detail: it would echo the member's own text back,
        # and a lone surrogate in it cannot be encoded, so the 422 became a 500.
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False,
                              include_input=False),
        ) from exc


async def _write(uid: str, op: str, fn: Any) -> Any:
    """Run ``fn(db)`` in one tenant session. A 4xx passes through, and any
    other fault is a 503 with no detail.

    Every write route goes through here. After the session commits, the
    write moves the member's change count (``mark_changed``), so the
    executor's cached block of this member is read again on the next turn
    (PZ-2, ``personalization.md`` §5.1). A 4xx or a 503 moves nothing.
    """
    try:
        async with _tenant_session() as db:
            result = await fn(db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth.assistant_profile.%s_failed", op,
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    mark_changed(uid)
    return result


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _item(stored: Any, key: str, item_id: str) -> dict[str, Any]:
    """The item *item_id* of a committed write, as the read shows it.

    The write has committed when this runs, so it never answers an error. An
    item that the lenient read drops answers as stored (PZ-1 verify, finding
    2: a 503 here reported a committed change as a failure).
    """
    for one in stored_profile(stored).get(key, []):
        if one["id"] == item_id:
            return dict(one)
    raw = _as_dict(stored)
    items = raw.get(key) if isinstance(raw, dict) else None
    for one in items if isinstance(items, list) else []:
        if isinstance(one, dict) and one.get("id") == item_id:
            return dict(one)
    return {"id": item_id}


@me_router.get("/me/assistant-profile", summary="The caller's assistant profile (PZ-1)")
async def get_assistant_profile(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The stored profile, or the empty profile when the member set nothing.

    A failed read answers 503, never the empty profile, so a database fault
    never reads as "the member set nothing".
    """
    uid = _member(user)
    try:
        async with _tenant_session() as db:
            row = await _read(db, uid)
    except Exception as exc:
        logger.warning("auth.assistant_profile.read_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return stored_profile(row[0] if row is not None else None)


@me_router.get("/me/assistant-profile/address",
               summary="The name the assistant calls the caller (PZ-4)")
async def get_assistant_profile_address(
    user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """``{"name": ...}``, the form of address of §5.2, or ``""`` for no name.

    The same order as the executor's block (``resolve_address_name``): the
    stored ``address.name``, then ``people.preferred_name``, then the first
    word of ``app_user.display_name``. My Day greets with it. The member
    comes from the session and the org from the bound session (R5). A
    failed read answers 503, never a name that may be wrong.
    """
    from acb_common.assistant_profile import ADDRESS_SQL_TEMPLATE, resolve_address_name

    uid = _member(user)
    try:
        async with _tenant_session() as db:
            row = await _read(db, uid)
            names = (await db.execute(_sql(ADDRESS_SQL_TEMPLATE), {"m": uid})).first()
    except Exception as exc:
        logger.warning("auth.assistant_profile.address_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    stored = stored_profile(row[0] if row is not None else None)
    preferred, _zone, display = names[:3] if names else (None, None, None)
    return {"name": resolve_address_name(stored["address"].get("name"), preferred, display)}


#: The file names of the two exports (PZ-9).
EXPORT_FILES: dict[str, tuple[str, str]] = {
    "json": ("assistant-profile.json", "application/json"),
    "text": ("assistant-profile.txt", "text/plain; charset=utf-8"),
}


@me_router.get("/me/assistant-profile/export",
               summary="Download the caller's assistant profile (PZ-9)")
async def export_assistant_profile(
    user: UserContext = Depends(get_current_user),
    format: Literal["json", "text"] = Query(default="json"),
) -> Response:
    """The member's own profile as a file (``personalization.md`` §13.3 item 7).

    JSON is ``{exported_at, member, profile, text}``. ``profile`` is the
    answer of the GET, with each fact's source and date. ``text`` is
    ``render_export``. ``?format=text`` gives the text alone. Each answer is
    an attachment, with ``Cache-Control: no-store``.

    The route reads, and writes nothing. The member comes from the session
    and the org from the bound session (R5), so the route takes no address
    and can name no other member. The switch does not stop it: ``learning``
    stops new writes, and the member may always read their own words. A
    member with no profile gets the empty profile, never a 404. A failed
    read answers 503, never the empty profile.
    """
    from acb_common.assistant_profile import render_export

    uid = _member(user)
    try:
        async with _tenant_session() as db:
            row = await _read(db, uid)
    except Exception as exc:
        logger.warning("auth.assistant_profile.export_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    raw = row[0] if row is not None else None
    text = render_export(raw)
    name, media = EXPORT_FILES[format]
    if format == "text":
        content = text + "\n"
    else:
        content = json.dumps({
            "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "member": uid,
            "profile": stored_profile(raw),
            "text": text,
        }, ensure_ascii=False, indent=2) + "\n"
    return Response(content=content, media_type=media, headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{name}"',
    })


@me_router.put("/me/assistant-profile", summary="Save one key of the assistant profile (PZ-1)")
async def put_assistant_profile(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Save one key. Every other key of the column stays. A chat write
    cannot set ``learning``, which only the member sets in Settings."""
    uid = _member(user)
    via = _via(x_profile_via)
    put = _validate(ProfilePut, body)
    if via is not None and put.key == "learning":
        raise _refuse("learning is set in Settings, not from the chat")
    params = {"pkey": put.key, "pval": json.dumps(put.value)}
    new, merge, where = _NEW_KEY, _MERGE_KEY, ""
    if via is not None:
        params["last"] = _last({"op": "put", "key": put.key, "after": put.value})
        new = _with_last(_NEW_KEY, _PUT_LAST_NEW)
        merge = _with_last(_MERGE_KEY, _PUT_LAST_MERGE)
        where = _LEARNING_NOT_OFF

    async def _run(db: Any) -> Any:
        await _learning_gate(db, uid, via)
        stored = await _upsert(db, uid, new, merge, params, where)
        if stored is None:
            raise HTTPException(status_code=409, detail=LEARNING_OFF)
        _check_caps(stored)
        return stored

    return stored_profile(await _write(uid, "write", _run))


@me_router.delete("/me/assistant-profile", status_code=204, response_class=Response,
                  summary="Forget the whole assistant profile (PZ-1)")
async def delete_assistant_profile(
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> Response:
    """The column becomes NULL. The settings row and its other columns stay.
    Forget-all is a Settings act: a chat write (``X-Profile-Via``) is a 422."""
    uid = _member(user)
    if _via(x_profile_via) is not None:
        raise _refuse(FORGET_ALL_IN_SETTINGS)

    async def _run(db: Any) -> None:
        await db.execute(_sql(_FORGET_SQL), {"uid": uid})

    await _write(uid, "forget", _run)
    return Response(status_code=204)


#: The 422 of an alias whose address is not in the org (PZ-5).
OUTSIDE_ORG = "that address is not a member or a person of this organization"


async def _in_org(db: Any, email: str | None) -> None:
    """Refuse an alias address that is not in the bound org. ``None`` is a
    patch that keeps the address, so it passes."""
    if email is None:
        return
    from gateway.routes.admin.assistant_profile_people import email_in_org

    if not await email_in_org(db, email):
        raise _refuse(OUTSIDE_ORG)


async def _add(uid: str, key: str, item: dict[str, Any],
               via: str | None = None) -> dict[str, Any]:
    params = {"lkey": key, "item": json.dumps(item)}
    new, merge, where = _NEW_ITEM, _APPEND_ITEM, ""
    if via is not None:
        # The item as written, so an undo can see a later Settings edit
        # (PZ-6a fix round 2).
        params["last"] = _last({"op": "post", "list": key, "id": item["id"],
                                "after": item})
        new, merge = _with_last(_NEW_ITEM, _LAST), _with_last(_APPEND_ITEM, _LAST)
        where = _LEARNING_NOT_OFF

    async def _run(db: Any) -> Any:
        await _learning_gate(db, uid, via)
        if key == "aliases":
            await _in_org(db, item["email"])
        stored = await _upsert(db, uid, new, merge, params, where)
        if stored is None:
            raise HTTPException(status_code=409, detail=LEARNING_OFF)
        _check_caps(stored)
        return stored

    return _item(await _write(uid, "add", _run), key, item["id"])


async def _patch(uid: str, key: str, item_id: str, patch: dict[str, Any],
                 via: str | None = None) -> dict[str, Any]:
    params = {"uid": uid, "lkey": key, "iid": item_id, "patch": json.dumps(patch)}
    sql = _PATCH_ITEM_SQL
    if via is not None:
        params["last"] = _last({"op": "patch", "list": key, "id": item_id})
        sql = _PATCH_ITEM_CHAT_SQL

    async def _run(db: Any) -> Any:
        await _learning_gate(db, uid, via)
        if key == "aliases":
            await _in_org(db, patch.get("email"))
        row = (await db.execute(_sql(sql), params)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        stored = _as_dict(row[0])
        _check_caps(stored)
        return stored

    return _item(await _write(uid, "edit", _run), key, item_id)


async def _remove(uid: str, key: str, item_id: str, via: str | None = None) -> Response:
    params = {"uid": uid, "lkey": key, "iid": item_id}
    sql = _DELETE_ITEM_SQL
    if via is not None:
        params["last"] = _last({"op": "delete", "list": key, "id": item_id})
        sql = _DELETE_ITEM_CHAT_SQL

    async def _run(db: Any) -> None:
        await _learning_gate(db, uid, via)
        row = (await db.execute(_sql(sql), params)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")

    await _write(uid, "delete", _run)
    return Response(status_code=204)


@me_router.post("/me/assistant-profile/facts", status_code=201,
                summary="Add one fact to the assistant profile (PZ-1)")
async def add_fact(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Add one fact. The server sets ``id``, ``source`` and ``at``. The
    source is the chat channel of ``X-Profile-Via``, else ``settings``."""
    uid = _member(user)
    via = _via(x_profile_via)
    fact = _validate(FactIn, body)
    item = {"id": _new_id("f"), "text": fact.text, "kind": fact.kind,
            "source": via or "settings",
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return await _add(uid, "facts", item, via)


@me_router.patch("/me/assistant-profile/facts/{fact_id}",
                 summary="Change one fact of the assistant profile (PZ-1)")
async def edit_fact(
    fact_id: str,
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Change the text or the kind. 404 when the member has no such fact."""
    uid = _member(user)
    via = _via(x_profile_via)
    patch = _validate(FactPatch, body)
    return await _patch(uid, "facts", fact_id, patch.model_dump(exclude_unset=True), via)


@me_router.delete("/me/assistant-profile/facts/{fact_id}", status_code=204,
                  response_class=Response,
                  summary="Forget one fact of the assistant profile (PZ-1)")
async def delete_fact(
    fact_id: str,
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> Response:
    """Forget one fact. 404 when the member has no such fact."""
    uid = _member(user)
    return await _remove(uid, "facts", fact_id, _via(x_profile_via))


@me_router.post("/me/assistant-profile/aliases", status_code=201,
                summary="Add one alias to the assistant profile (PZ-1)")
async def add_alias(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Add one alias. 422 when the address is not a person or a member of
    the org (PZ-5)."""
    uid = _member(user)
    via = _via(x_profile_via)
    alias = _validate(AliasIn, body)
    return await _add(uid, "aliases",
                      {"id": _new_id("a"), "alias": alias.alias, "email": alias.email},
                      via)


@me_router.patch("/me/assistant-profile/aliases/{alias_id}",
                 summary="Change one alias of the assistant profile (PZ-1)")
async def edit_alias(
    alias_id: str,
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Change the alias or the email. A new email gets the check of the add.
    404 when the member has no such alias."""
    uid = _member(user)
    via = _via(x_profile_via)
    patch = _validate(AliasPatch, body)
    return await _patch(uid, "aliases", alias_id, patch.model_dump(exclude_unset=True), via)


@me_router.delete("/me/assistant-profile/aliases/{alias_id}", status_code=204,
                  response_class=Response,
                  summary="Forget one alias of the assistant profile (PZ-1)")
async def delete_alias(
    alias_id: str,
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> Response:
    """Forget one alias. 404 when the member has no such alias."""
    uid = _member(user)
    return await _remove(uid, "aliases", alias_id, _via(x_profile_via))


@me_router.post("/me/assistant-profile/undo",
                summary="Undo the last chat change of the assistant profile (PZ-6a)")
async def undo_assistant_profile(
    user: UserContext = Depends(get_current_user),
    x_profile_via: str | None = Header(default=None),
) -> dict[str, Any]:
    """Undo the member's last chat change, from :data:`LAST_CHANGE`.

    Only a chat write (``X-Profile-Via``) records a change, so only a chat
    call undoes one. The web card undoes with its own inverse call. 404
    with :data:`UNDO_NOTHING`, :data:`UNDO_TOO_OLD` or
    :data:`UNDO_CHANGED` when nothing can be undone. The read and the write
    run under ONE row lock, so a concurrent write waits. It ignores
    ``learning``: an undo puts the member's profile back, and learns
    nothing new.
    """
    uid = _member(user)
    if _via(x_profile_via) is None:
        raise _refuse("an undo of a chat change comes from the chat")

    async def _run(db: Any) -> dict[str, Any]:
        row = (await db.execute(_sql(_READ_LOCKED_SQL), {"uid": uid})).first()
        raw = _as_dict(row[0]) if row is not None else None
        last = raw.get(LAST_CHANGE) if isinstance(raw, dict) else None
        out, why = undo_last_change(raw, time.time())
        if out is None:
            raise HTTPException(status_code=404, detail=why)
        _check_caps(out)
        await db.execute(_sql(_UNDO_WRITE_SQL), {"uid": uid, "p": json.dumps(out)})
        return {k: last[k] for k in ("op", "key", "list") if k in last}

    return {"undone": await _write(uid, "undo", _run)}
