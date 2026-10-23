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

**Aliases.** PZ-1 checks the SHAPE of the address only. The check that the
address is a member or a person of the org is PZ-5 (``resolve_person``).

**A member with no settings row.** The first write makes the row with the
calendar seed (``_seed_from_work_schedule``), exactly as ``prefs.py`` does.

The member comes from the session and the tenant from the bound session,
never from the body (R5, R11). Each body forbids an extra field. The routes
need no feature, as ``/auth/me/shell`` needs none. No flag: nothing reads the
profile before PZ-2, and PZ-2 adds the flag. Fences:
``tests/unit/test_assistant_profile.py`` and
``tests/unit/test_assistant_profile_r8.py`` (R8).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Body, Depends, HTTPException, Response
from gateway.db import tenant_session as _tenant_session
from gateway.routes.admin.me import me_router
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

#: The version of the shape. The first write sets it.
PROFILE_VERSION = 1
#: The most characters of the whole profile, as compact JSON (§4.2 budget).
PROFILE_MAX = 6_000
#: ``address.name``.
NAME_MAX = 40
#: ``style.language``, a BCP 47 tag.
LANGUAGE_MAX = 16
#: ``style.notes``: the most items, and the longest item.
NOTES_MAX = 5
NOTE_MAX = 200
#: ``facts``: the most items, and the longest text.
FACTS_MAX = 40
FACT_MAX = 300
#: ``aliases``: the most items, and the longest alias.
ALIASES_MAX = 50
ALIAS_MAX = 60
#: The longest address that an alias takes (RFC 5321).
EMAIL_MAX = 254

PUT_KEYS: tuple[str, ...] = ("learning", "address", "style")
FORMALITY: tuple[str, ...] = ("casual", "neutral", "formal")
GREETING: tuple[str, ...] = ("none", "brief", "warm")
LENGTH: tuple[str, ...] = ("short", "default", "detailed")
FACT_KINDS: tuple[str, ...] = ("about", "routine", "preference", "work", "relationship")
FACT_SOURCES: tuple[str, ...] = ("chat", "whatsapp", "settings")

FactKind = Literal["about", "routine", "preference", "work", "relationship"]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")


def empty_profile() -> dict[str, Any]:
    """The profile of a member who set nothing (§4.2 defaults)."""
    return {"v": PROFILE_VERSION, "learning": True, "address": {}, "style": {},
            "aliases": [], "facts": []}


# ── Strict cleaning (the write) ────────────────────────────────────────────


def _text(value: Any, cap: int, what: str) -> str:
    """One line of free text: a string, trimmed, not empty, at most ``cap``
    characters, with no control character and no lone surrogate."""
    if not isinstance(value, str):
        raise ValueError(f"{what} is text")
    out = value.strip()
    if not out:
        raise ValueError(f"{what} is empty")
    if len(out) > cap:
        raise ValueError(f"{what} is longer than {cap} characters")
    # The text goes into a prompt. Postgres stores no NUL, and jsonb refuses
    # a lone surrogate (a JS `slice()` through an emoji), which is a 503 and
    # not a 422 unless the route refuses it first.
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in out):
        raise ValueError(f"{what} holds a control character or a lone surrogate")
    return out


def _choice(value: Any, choices: tuple[str, ...], what: str) -> str:
    if value not in choices:
        raise ValueError(f"{what} is one of {', '.join(choices)}")
    return str(value)


def _email(value: Any) -> str:
    out = _text(value, EMAIL_MAX, "the email")
    if not _EMAIL.match(out):
        raise ValueError("the email is not an address")
    return out


def _object(value: Any, keys: tuple[str, ...], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is an object")
    unknown = [k for k in value if k not in keys]
    if unknown:
        # The message names the known keys, never the member's. A key with a
        # lone surrogate cannot be encoded, and it made this a 500 (PZ-1
        # review, P2).
        raise ValueError(f"{what} has an unknown key. Known keys: {', '.join(keys)}")
    return value


def _clean_learning(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("learning is true or false")
    return value


def _clean_address(value: Any) -> dict[str, Any]:
    raw = _object(value, ("name", "formality", "greeting"), "the address")
    out: dict[str, Any] = {}
    if "name" in raw:
        out["name"] = _text(raw["name"], NAME_MAX, "address.name")
    if "formality" in raw:
        out["formality"] = _choice(raw["formality"], FORMALITY, "address.formality")
    if "greeting" in raw:
        out["greeting"] = _choice(raw["greeting"], GREETING, "address.greeting")
    return out


def _clean_style(value: Any) -> dict[str, Any]:
    raw = _object(value, ("length", "language", "notes"), "the style")
    out: dict[str, Any] = {}
    if "length" in raw:
        out["length"] = _choice(raw["length"], LENGTH, "style.length")
    if "language" in raw:
        lang = _text(raw["language"], LANGUAGE_MAX, "style.language")
        if not _LANGUAGE.match(lang):
            raise ValueError("style.language is a BCP 47 tag")
        out["language"] = lang
    if "notes" in raw:
        notes = raw["notes"]
        if not isinstance(notes, list):
            raise ValueError("style.notes is a list of lines")
        if len(notes) > NOTES_MAX:
            raise ValueError(f"style.notes holds at most {NOTES_MAX} lines")
        out["notes"] = [_text(n, NOTE_MAX, "a style note") for n in notes]
    return out


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


# ── Lenient reading (the answer) ───────────────────────────────────────────


def _lenient(clean: Any, value: Any) -> Any:
    try:
        return clean(value)
    except ValueError:
        return None


def _stored_fact(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    text = _lenient(lambda v: _text(v, FACT_MAX, "a fact"), item.get("text"))
    if text is None:
        return None
    # A kind this build does not know (a later slice may add one) reads as
    # ``about``. The fact still counts against the caps, so the member must
    # see it to delete it (PZ-1 verify, finding 2).
    kind = item.get("kind") if item.get("kind") in FACT_KINDS else "about"
    out: dict[str, Any] = {"id": item["id"], "text": text, "kind": kind}
    if item.get("source") in FACT_SOURCES:
        out["source"] = item["source"]
    if isinstance(item.get("at"), str):
        out["at"] = item["at"]
    return out


def _stored_alias(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    alias = _lenient(lambda v: _text(v, ALIAS_MAX, "an alias"), item.get("alias"))
    email = _lenient(_email, item.get("email"))
    if alias is None or email is None:
        return None
    return {"id": item["id"], "alias": alias, "email": email}


def _stored_keys(raw: Any, clean: Any) -> dict[str, Any]:
    """The keys of ``address`` or ``style`` that pass their own check."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        one = _lenient(clean, {k: v})
        if one:
            out.update(one)
    return out


def _as_dict(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def stored_profile(raw: Any) -> dict[str, Any]:
    """A stored value, as the routes answer it. Lenient on purpose: a key or
    an item of the wrong shape drops from the answer rather than fail the
    read. A key that is absent takes its §4.2 default."""
    raw = _as_dict(raw)
    out = empty_profile()
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("learning"), bool):
        out["learning"] = raw["learning"]
    out["address"] = _stored_keys(raw.get("address"), _clean_address)
    out["style"] = _stored_keys(raw.get("style"), _clean_style)
    for key, one, cap in (("facts", _stored_fact, FACTS_MAX),
                          ("aliases", _stored_alias, ALIASES_MAX)):
        items = raw.get(key)
        if isinstance(items, list):
            out[key] = [x for x in map(one, items) if x is not None][:cap]
    return out


def profile_size(profile: Any) -> int:
    """The characters of a profile, as compact JSON. The §4.2 budget."""
    return len(json.dumps(profile, ensure_ascii=False, separators=(",", ":")))


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
_PATCH_ITEM_SQL = (
    "UPDATE user_settings SET assistant_profile = jsonb_set("
    "user_settings.assistant_profile, ARRAY[CAST(:lkey AS text)], ("
    "SELECT jsonb_agg(CASE WHEN t.e ->> 'id' = CAST(:iid AS text) "
    "THEN t.e || CAST(:patch AS jsonb) ELSE t.e END ORDER BY t.n) "
    f"FROM jsonb_array_elements({_LIST}) WITH ORDINALITY AS t(e, n)), true), "
    "updated_at = now() "
    f"WHERE {_HAS_ITEM} RETURNING assistant_profile"
)

#: DELETE one: rebuild the list in order, without the one item.
_DELETE_ITEM_SQL = (
    "UPDATE user_settings SET assistant_profile = jsonb_set("
    "user_settings.assistant_profile, ARRAY[CAST(:lkey AS text)], COALESCE(("
    "SELECT jsonb_agg(t.e ORDER BY t.n) "
    f"FROM jsonb_array_elements({_LIST}) WITH ORDINALITY AS t(e, n) "
    "WHERE t.e ->> 'id' IS DISTINCT FROM CAST(:iid AS text)), "
    "CAST('[]' AS jsonb)), true), updated_at = now() "
    f"WHERE {_HAS_ITEM} RETURNING assistant_profile"
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


async def _upsert(db: Any, uid: str, new: str, merge: str, params: dict[str, Any]) -> Any:
    """One write of the column, in one statement: ``new`` for a new row,
    ``merge`` for an existing row. Returns the stored column after it."""
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
    row = (await db.execute(text(f"{sql} RETURNING assistant_profile"), params)).first()
    return _as_dict(row[0]) if row is not None else None


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _check_caps(stored: Any) -> None:
    """The caps that only the stored value can show. Raise inside the
    session, so ``tenant_session`` rolls the write back."""
    if not isinstance(stored, dict):
        return
    for key, cap in (("facts", FACTS_MAX), ("aliases", ALIASES_MAX)):
        items = stored.get(key)
        if isinstance(items, list) and len(items) > cap:
            raise _refuse(f"at most {cap} {key}")
    if profile_size(stored) > PROFILE_MAX:
        raise _refuse(f"profile exceeds {PROFILE_MAX} characters")


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
    other fault is a 503 with no detail."""
    try:
        async with _tenant_session() as db:
            return await fn(db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth.assistant_profile.%s_failed", op,
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc


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


@me_router.put("/me/assistant-profile", summary="Save one key of the assistant profile (PZ-1)")
async def put_assistant_profile(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save one key. Every other key of the column stays."""
    uid = _member(user)
    put = _validate(ProfilePut, body)
    params = {"pkey": put.key, "pval": json.dumps(put.value)}

    async def _run(db: Any) -> Any:
        stored = await _upsert(db, uid, _NEW_KEY, _MERGE_KEY, params)
        _check_caps(stored)
        return stored

    return stored_profile(await _write(uid, "write", _run))


@me_router.delete("/me/assistant-profile", status_code=204, response_class=Response,
                  summary="Forget the whole assistant profile (PZ-1)")
async def delete_assistant_profile(user: UserContext = Depends(get_current_user)) -> Response:
    """The column becomes NULL. The settings row and its other columns stay."""
    uid = _member(user)

    async def _run(db: Any) -> None:
        await db.execute(_sql(_FORGET_SQL), {"uid": uid})

    await _write(uid, "forget", _run)
    return Response(status_code=204)


async def _add(uid: str, key: str, item: dict[str, Any]) -> dict[str, Any]:
    params = {"lkey": key, "item": json.dumps(item)}

    async def _run(db: Any) -> Any:
        stored = await _upsert(db, uid, _NEW_ITEM, _APPEND_ITEM, params)
        _check_caps(stored)
        return stored

    return _item(await _write(uid, "add", _run), key, item["id"])


async def _patch(uid: str, key: str, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    params = {"uid": uid, "lkey": key, "iid": item_id, "patch": json.dumps(patch)}

    async def _run(db: Any) -> Any:
        row = (await db.execute(_sql(_PATCH_ITEM_SQL), params)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        stored = _as_dict(row[0])
        _check_caps(stored)
        return stored

    return _item(await _write(uid, "edit", _run), key, item_id)


async def _remove(uid: str, key: str, item_id: str) -> Response:
    params = {"uid": uid, "lkey": key, "iid": item_id}

    async def _run(db: Any) -> None:
        row = (await db.execute(_sql(_DELETE_ITEM_SQL), params)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")

    await _write(uid, "delete", _run)
    return Response(status_code=204)


@me_router.post("/me/assistant-profile/facts", status_code=201,
                summary="Add one fact to the assistant profile (PZ-1)")
async def add_fact(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add one fact. The server sets ``id``, ``source`` and ``at``."""
    uid = _member(user)
    fact = _validate(FactIn, body)
    item = {"id": _new_id("f"), "text": fact.text, "kind": fact.kind, "source": "settings",
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return await _add(uid, "facts", item)


@me_router.patch("/me/assistant-profile/facts/{fact_id}",
                 summary="Change one fact of the assistant profile (PZ-1)")
async def edit_fact(
    fact_id: str,
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Change the text or the kind. 404 when the member has no such fact."""
    uid = _member(user)
    patch = _validate(FactPatch, body)
    return await _patch(uid, "facts", fact_id, patch.model_dump(exclude_unset=True))


@me_router.delete("/me/assistant-profile/facts/{fact_id}", status_code=204,
                  response_class=Response,
                  summary="Forget one fact of the assistant profile (PZ-1)")
async def delete_fact(fact_id: str, user: UserContext = Depends(get_current_user)) -> Response:
    """Forget one fact. 404 when the member has no such fact."""
    return await _remove(_member(user), "facts", fact_id)


@me_router.post("/me/assistant-profile/aliases", status_code=201,
                summary="Add one alias to the assistant profile (PZ-1)")
async def add_alias(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add one alias. PZ-1 checks the SHAPE of the email only. The check that
    it is a member or a person of the org is PZ-5."""
    uid = _member(user)
    alias = _validate(AliasIn, body)
    return await _add(uid, "aliases",
                      {"id": _new_id("a"), "alias": alias.alias, "email": alias.email})


@me_router.patch("/me/assistant-profile/aliases/{alias_id}",
                 summary="Change one alias of the assistant profile (PZ-1)")
async def edit_alias(
    alias_id: str,
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Change the alias or the email (its shape only, as the add). 404 when
    the member has no such alias."""
    uid = _member(user)
    patch = _validate(AliasPatch, body)
    return await _patch(uid, "aliases", alias_id, patch.model_dump(exclude_unset=True))


@me_router.delete("/me/assistant-profile/aliases/{alias_id}", status_code=204,
                  response_class=Response,
                  summary="Forget one alias of the assistant profile (PZ-1)")
async def delete_alias(alias_id: str, user: UserContext = Depends(get_current_user)) -> Response:
    """Forget one alias. 404 when the member has no such alias."""
    return await _remove(_member(user), "aliases", alias_id)
