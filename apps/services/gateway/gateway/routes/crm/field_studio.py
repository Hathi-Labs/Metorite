"""CRM · field studio — Native mode, the backend (WS-53 CRM-C4a).

Spec: ``project-docs/specs/crm_platform.md`` §20.7, items 1 to 13, and the
CRM-C4a contract under it.

    POST   /crm/fields/{entity}                      create a custom field
    PATCH  /crm/fields/{entity}/{key}                change a field
    DELETE /crm/fields/{entity}/{key}                retire a custom field
    POST   /crm/fields/{entity}/{key}/restore        bring a retired field back
    PUT    /crm/field-layout/{entity}                the order of sections and fields
    POST   /crm/field-sections/{entity}              add a section
    PATCH  /crm/field-sections/{entity}/{section_id} rename a section
    DELETE /crm/field-sections/{entity}/{section_id} remove a section

``entity`` is one of ``leads``, ``contacts``, ``organizations`` and ``deals``.
``GET /crm/fields/{entity}?layout=1`` (``fields.py``) is the read.

⚠️ **Dark, admin only, Native only.** Each route needs the CRM kill switch (at
the router), a place on ``CRM_FIELD_STUDIO_ORGS`` (``flags.require_field_studio``,
else 404) and ``admin:access:manage`` (else 403). In Mirror mode Zoho owns the
fields until CRM-C5, so ``core.assert_writable`` gives 409: both studio tables
are in ``core.MIRROR_SETTINGS_TABLES``. A flip of the flag on a box is gate
``enforcement-flip``.

**A field that the studio writes has ``source = 'metorite'``.** The sync writes
``'zoho'``, and its conflict update skips a ``'metorite'`` row
(``crm_sync/engine.py::_FIELD_DEF_SQL``).

**The key never changes** (item 2). :func:`make_key` makes it from the label
at create: ``a-z``, ``0-9`` and ``_``, at most 40 characters, ``_2`` on a
clash. A key never equals a fixed column, and :func:`key_problem` refuses the
route words ``order``, ``layout`` and ``restore``. A rename changes ``label``.

**System fields** (item 3) are the fixed columns of ``core.SYSTEM_FIELDS``.
An admin may change their label, section, order and ``visible``. The first
such edit writes a row with ``is_system = true``. The studio cannot retire or
retype one, and a column in ``Entity.required`` stays required and visible.

**The custom write path** (item 5) lives here too, because it reads the same
definitions. ``records.create_record`` calls :func:`custom_for_create` and
``records.patch_record`` calls :func:`custom_for_patch`. A PATCH becomes a
``core.CustomMerge``: one statement that merges per key, so a key that the
write does not name keeps its value. A write that names a retired key gives
422 ``"<label> is retired. Restore it to write a value."`` and changes nothing.

**No tenant in the request.** The session binds the organization of the
caller (R5, R11). The acting admin comes from ``UserContext`` only.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    ENTITIES,
    SYSTEM_FIELDS,
    _tenant_session,
    assert_writable,
    checked_uuid,
    current_tenant,
    from_jsonb,
    router,
    system_field,
)
from gateway.routes.crm.fields import (
    DEF_COLUMNS,
    ENTITY_BY_SLUG,
    FieldDef,
    FieldLayout,
    FieldSection,
    checked_slug,
    field_wire,
    read_layout,
)
from gateway.routes.crm.flags import require_field_studio
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_GUARDS = [Depends(require_field_studio), require_permission(ADMIN_PERMISSION)]

#: What a studio write stores in ``crm_field_defs.source``.
SOURCE = "metorite"

#: The type words of the studio (§20.7 item 1).
FIELD_TYPES: tuple[str, ...] = (
    "text", "textarea", "number", "decimal", "percent", "currency", "date",
    "datetime", "boolean", "email", "phone", "url", "select", "multiselect",
    "lookup",
)

#: The type change matrix (§20.7 item 9). A change is allowed only when each
#: stored value stays valid. Any other change gives 422 ``field_type_change``.
TYPE_CHANGES: dict[str, frozenset[str]] = {
    "text": frozenset({"textarea"}),
    "number": frozenset({"decimal", "currency", "percent"}),
    "decimal": frozenset({"percent", "currency"}),
    "percent": frozenset({"decimal", "currency"}),
    "currency": frozenset({"decimal", "percent"}),
    "select": frozenset({"multiselect"}),
    "email": frozenset({"text"}),
    "phone": frozenset({"text"}),
    # Review round 1: a URL holds up to 2048 characters and a text field 255,
    # so a URL becomes a textarea.
    "url": frozenset({"textarea"}),
}

#: The targets of a lookup in Native mode (§20.7 item 8). A ``module:<ApiName>``
#: target is for Mirror mode, and it waits for CRM-C5.
NATIVE_LOOKUP_TARGETS: tuple[str, ...] = ("leads", "contacts", "organizations", "deals")

#: Route words a key may never take (§20.7 item 2).
ROUTE_WORDS: frozenset[str] = frozenset({"order", "layout", "restore"})

KEY_MAX = 40
LABEL_MAX = 80
OPTION_LABEL_MAX = 120
HELP_MAX = 500
TEXT_MAX = 255
#: The size of a number that the studio refuses (review round 1). A float
#: keeps 15 significant digits, so a larger value loses its last digits.
NUMBER_LIMIT = 1e15
TEXTAREA_MAX = 32_000
URL_MAX = 2048
EMAIL_MAX = 254
PHONE_MAX = 40

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$",
)
_EMAIL_RE = re.compile(r"^[^\s@<>?&#%,;]+@[^\s@<>?&#%,;]+\.[^\s@<>?&#%,;]+$")
_PHONE_RE = re.compile(r"^\+?[\d\s().\-]+(\s*(x|ext\.?|#)\s*\d+)?$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: The table and the name expression of each lookup target.
_LOOKUP_NAMES: dict[str, tuple[str, str]] = {
    "leads": ("crm_leads", "lead_name"),
    "deals": ("crm_deals", "name"),
    "organizations": ("crm_companies", "name"),
    "contacts": ("crm_contacts", "btrim(concat_ws(' ', first_name, last_name))"),
}


def _unprocessable(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=422, detail={"code": code, "message": message, **extra},
    )


# ── The key rule (item 2) ───────────────────────────────────────────────────


def fixed_columns(slug: str) -> frozenset[str]:
    """Every name that a record of *slug* holds outside ``custom``."""
    entity = ENTITIES[slug]
    return frozenset(entity.model.model_fields) | frozenset(entity.payload.model_fields) | {
        "id", "organization_id", "custom", "zoho_dirty", "zoho_synced_at",
    }


def key_problem(slug: str, key: str) -> str | None:
    """Why *key* cannot be a custom key of *slug*, or ``None`` when it can."""
    if not (1 <= len(key) <= KEY_MAX) or not _KEY_RE.match(key):
        return "Use a-z, 0-9 and _, start with a letter, at most 40 characters."
    if key in ROUTE_WORDS:
        return f"'{key}' is a route word."
    if key in fixed_columns(slug):
        return f"'{key}' is a fixed column of {slug}."
    return None


def validate_key(slug: str, key: str) -> str:
    """*key*, or 422 ``field_key`` with the reason."""
    problem = key_problem(slug, key)
    if problem is not None:
        raise _unprocessable("field_key", problem)
    return key


def make_key(slug: str, label: str, taken: set[str] | frozenset[str]) -> str:
    """The key of a new field, from its *label*.

    ``a-z``, ``0-9`` and ``_``, at most 40 characters. A clash with a taken
    key (in any case), a fixed column or a route word adds ``_2``, ``_3``.
    """
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not base:
        base = "field"
    elif not base[0].isalpha():
        base = f"f_{base}"
    base = base[:KEY_MAX].rstrip("_")
    lowered = {t.lower() for t in taken}
    candidate, n = base, 1
    while key_problem(slug, candidate) is not None or candidate in lowered:
        n += 1
        suffix = f"_{n}"
        candidate = base[: KEY_MAX - len(suffix)].rstrip("_") + suffix
    return candidate


# ── Picklist options (item 6) ───────────────────────────────────────────────


class OptionIn(BaseModel):
    """One option in a create or a PATCH. No ``value`` means a new option."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=OPTION_LABEL_MAX)
    value: str | None = None
    retired: bool = False


def option_value(label: str, taken: set[str]) -> str:
    """The ``value`` of a new option: a slug of its label, ``-2`` on a clash."""
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "option"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def apply_options(current: list[Any] | None, given: list[OptionIn]) -> list[dict[str, Any]]:
    """The options after an edit: add, rename, reorder and retire (item 6).

    An entry with a ``value`` names an option that exists, and its value never
    changes. An entry with no ``value`` is new, and takes a slug of its label.
    An option leaves the list only by ``retired: true``, so a record that
    holds a value always finds its option.
    """
    existing = {
        str(o.get("value")): o for o in (current or [])
        if isinstance(o, dict) and o.get("value") is not None
    }
    taken = set(existing)
    named: set[str] = set()
    out: list[dict[str, Any]] = []
    for option in given:
        label = _clean_label(option.label, "An option label", OPTION_LABEL_MAX)
        if option.value is not None:
            if option.value not in existing:
                raise _unprocessable(
                    "option_unknown", f"No option has the value '{option.value}'.",
                )
            if option.value in named:
                raise _unprocessable(
                    "option_twice", f"The option '{option.value}' is in the list twice.",
                )
            value = option.value
        else:
            value = option_value(label, taken)
            taken.add(value)
        named.add(value)
        entry: dict[str, Any] = {"label": label, "value": value}
        if option.retired:
            entry["retired"] = True
        out.append(entry)
    missing = sorted(set(existing) - named)
    if missing:
        raise _unprocessable(
            "option_removed",
            "An option cannot be removed, because a record can hold its value. "
            "Retire it instead.",
            missing=missing,
        )
    return out


def _active_values(options: Any) -> set[str]:
    return {
        str(o.get("value")) for o in (options or [])
        if isinstance(o, dict) and o.get("value") is not None and not o.get("retired")
    }


# ── One field, as the write path reads it ───────────────────────────────────


@dataclass(frozen=True)
class CustomField:
    """A custom field of one entity: what a record write checks."""

    key: str
    label: str
    type: str
    options: tuple[Any, ...]
    required: bool
    default_value: Any
    lookup_target: str | None
    retired: bool

    @classmethod
    def from_row(cls, row: Any) -> CustomField:
        options = from_jsonb(getattr(row, "options", None))
        return cls(
            key=row.key, label=getattr(row, "label", None) or row.key,
            type=getattr(row, "type", None) or "text",
            options=tuple(options) if isinstance(options, list) else (),
            required=bool(getattr(row, "required", False)),
            default_value=from_jsonb(getattr(row, "default_value", None)),
            lookup_target=getattr(row, "lookup_target", None),
            retired=getattr(row, "retired_at", None) is not None,
        )


_CUSTOM_FIELDS_SQL = (
    "SELECT key, label, type, options, required, default_value::text AS "
    "default_value, lookup_target, is_system, retired_at FROM crm_field_defs "
    "WHERE entity = :entity"
)


async def load_custom_fields(db: Any, slug: str) -> dict[str, CustomField]:
    """The custom fields of *slug*, by key. A system row is not one."""
    rows = (await db.execute(
        text(_CUSTOM_FIELDS_SQL), {"entity": ENTITY_BY_SLUG[slug]},
    )).fetchall()
    return {
        r.key: CustomField.from_row(r) for r in rows if not getattr(r, "is_system", False)
    }


def is_empty(value: Any) -> bool:
    """``null``, a blank string, an empty list or an empty object."""
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, list | dict) and not value)
    )


# ── The value of one field (items 1, 6, 7 and 8) ────────────────────────────


def _bad(field: CustomField, why: str) -> HTTPException:
    return _unprocessable(
        "field_value", f"{field.label}: {why}", field=field.key,
    )


def _is_number(value: Any) -> bool:
    """A finite JSON number. ``bool`` is not one. A JSON integer too big for
    a float raises ``OverflowError`` in ``float()``, and is not one either."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _in_range(field: CustomField, value: Any) -> None:
    """422 for a number whose size is :data:`NUMBER_LIMIT` or more."""
    try:
        too_big = abs(float(value)) >= NUMBER_LIMIT
    except OverflowError:
        too_big = True
    if too_big:
        raise _bad(field, "give a number smaller than 10^15.")


def _text(field: CustomField, value: Any, kept: Any) -> Any:
    limit = TEXT_MAX if field.type == "text" else TEXTAREA_MAX
    if not isinstance(value, str):
        raise _bad(field, "give text.")
    if len(value) > limit:
        raise _bad(field, f"use at most {limit} characters.")
    return value


def _whole(field: CustomField, value: Any, kept: Any) -> Any:
    if not _is_number(value) or (isinstance(value, float) and not value.is_integer()):
        raise _bad(field, "give a whole number.")
    _in_range(field, value)
    return int(value)


def _number(field: CustomField, value: Any, kept: Any) -> Any:
    if not _is_number(value):
        raise _bad(field, "give a number.")
    _in_range(field, value)
    return value


def _date(field: CustomField, value: Any, kept: Any) -> Any:
    """``YYYY-MM-DD`` only. ``date.fromisoformat`` also takes a week date
    (``2026-W42-1``) and a basic form (``20261013``), so the pattern runs first."""
    try:
        if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
            raise ValueError(value)
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise _bad(field, "give a date as YYYY-MM-DD.") from None


def _datetime(field: CustomField, value: Any, kept: Any) -> Any:
    """``YYYY-MM-DDTHH:MM[:SS[.ffffff]]`` with ``Z`` or an offset only."""
    try:
        if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value):
            raise ValueError(value)
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise _bad(field, "give a date and time in ISO 8601, with a time zone.")
    return parsed.isoformat()


def _boolean(field: CustomField, value: Any, kept: Any) -> Any:
    if not isinstance(value, bool):
        raise _bad(field, "give true or false.")
    return value


def _email(field: CustomField, value: Any, kept: Any) -> Any:
    if not isinstance(value, str) or len(value) > EMAIL_MAX or not _EMAIL_RE.match(value.strip()):
        raise _bad(field, "give an email address.")
    return value.strip()


def _phone(field: CustomField, value: Any, kept: Any) -> Any:
    digits = sum(ch.isdigit() for ch in value) if isinstance(value, str) else 0
    if (
        not isinstance(value, str) or len(value) > PHONE_MAX or digits < 4
        or not _PHONE_RE.match(value.strip())
    ):
        raise _bad(field, "give a phone number.")
    return value.strip()


def _url(field: CustomField, value: Any, kept: Any) -> Any:
    if not isinstance(value, str) or len(value) > URL_MAX or not _URL_RE.match(value.strip()):
        raise _bad(field, "give an http or https address.")
    return value.strip()


def _select(field: CustomField, value: Any, kept: Any) -> Any:
    allowed = _active_values(field.options) | ({kept} if isinstance(kept, str) else set())
    if not isinstance(value, str) or value not in allowed:
        raise _bad(field, "choose one of its options.")
    return value


def _multiselect(field: CustomField, value: Any, kept: Any) -> Any:
    held = {str(v) for v in kept} if isinstance(kept, list) else set()
    allowed = _active_values(field.options) | held
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(field, "give a list of its options.")
    out: list[str] = []
    for item in value:
        if item not in allowed:
            raise _bad(field, f"'{item}' is not one of its options.")
        if item not in out:
            out.append(item)
    return out


#: The check of each type word but ``lookup``, which reads the database.
_CHECKS: dict[str, Any] = {
    "text": _text, "textarea": _text, "number": _whole, "decimal": _number,
    "percent": _number, "currency": _number, "date": _date, "datetime": _datetime,
    "boolean": _boolean, "email": _email, "phone": _phone, "url": _url,
    "select": _select, "multiselect": _multiselect,
}


async def check_value(
    db: Any, field: CustomField, value: Any, kept: Any = None,
    *, allow_lookup: bool = True,
) -> Any:
    """*value* as the record stores it, or 422 that names the field label.

    *kept* is the value that the record holds now. A record keeps a retired
    option, so a write may name it again. Any other option must be active.
    """
    if field.type == "lookup":
        if not allow_lookup:
            raise _bad(field, "a lookup has no default.")
        return await _lookup_value(db, field, value)
    check = _CHECKS.get(field.type)
    if check is None:
        raise _bad(field, f"the type '{field.type}' takes no value here.")
    return check(field, value, kept)


async def _lookup_value(db: Any, field: CustomField, value: Any) -> dict[str, str]:
    """``{"id", "name"}`` of a record of the target entity, or 422.

    The read runs in the caller's bound session, so row level security hides
    a record of another organization, and its id gives 422 (item 8).
    """
    target = field.lookup_target
    if target not in _LOOKUP_NAMES:
        raise _bad(field, "this lookup has no Native target.")
    ident = value.get("id") if isinstance(value, dict) else value
    if not isinstance(ident, str):
        raise _bad(field, "give the id of a record.")
    try:
        ident = checked_uuid(ident, field.key)
    except HTTPException:
        raise _bad(field, "give the id of a record.") from None
    table, name = _LOOKUP_NAMES[target]
    row = (await db.execute(
        text(f"SELECT id, {name} AS name FROM {table} WHERE id = CAST(:id AS uuid)"),
        {"id": ident},
    )).first()
    if row is None:
        raise _bad(field, f"no {ENTITIES[target].label.lower()} has that id.")
    return {"id": str(row.id), "name": str(row.name or "")}


# ── The custom write path (item 5) ──────────────────────────────────────────


def _retired(field: CustomField) -> HTTPException:
    return _unprocessable(
        "field_retired", f"{field.label} is retired. Restore it to write a value.",
        field=field.key,
    )


def _field_of(fields: dict[str, CustomField], slug: str, key: str) -> CustomField:
    field = fields.get(key)
    if field is None:
        raise _unprocessable(
            "field_unknown", f"'{key}' is not a custom field of {slug}.", field=key,
        )
    if field.retired:
        raise _retired(field)
    return field


def _required(field: CustomField) -> HTTPException:
    return _unprocessable(
        "field_required", f"{field.label} is required.", field=field.key,
    )


async def custom_for_create(
    db: Any, slug: str, given: dict[str, Any] | None, *, system: bool = False,
) -> dict[str, Any]:
    """The whole ``custom`` object of a new record, or 422.

    Each named key is checked against its field. A field with a default fills
    an absent key. A required field must hold a value.

    ``system=True`` is for a platform writer (the auto-lead step and the lead
    conversion). It applies the defaults and skips "required", because no
    member can fill the field there (owner decision, review round 1).
    """
    fields = await load_custom_fields(db, slug)
    given = given or {}
    out: dict[str, Any] = {}
    for key, value in given.items():
        field = _field_of(fields, slug, key)
        if not is_empty(value):
            out[key] = await check_value(db, field, value)
    for field in fields.values():
        if field.retired:
            continue
        if field.key not in given and field.default_value is not None:
            out[field.key] = field.default_value
        if field.required and not system and is_empty(out.get(field.key)):
            raise _required(field)
    return out


async def custom_for_patch(
    db: Any, slug: str, given: dict[str, Any], stored: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """``(put, drop)`` of a PATCH of ``custom``, or 422.

    A key that the PATCH does not name keeps its value. A JSON ``null`` or an
    empty value removes its key, and a required field refuses that. A retired
    key gives 422, and the stored value stays.
    """
    fields = await load_custom_fields(db, slug)
    put: dict[str, Any] = {}
    drop: list[str] = []
    for key, value in given.items():
        field = _field_of(fields, slug, key)
        if is_empty(value):
            if field.required:
                raise _required(field)
            drop.append(key)
            continue
        put[key] = await check_value(db, field, value, stored.get(key))
    return put, tuple(drop)


# ── The studio routes ───────────────────────────────────────────────────────


FieldType = Literal[
    "text", "textarea", "number", "decimal", "percent", "currency", "date",
    "datetime", "boolean", "email", "phone", "url", "select", "multiselect",
    "lookup",
]


class FieldIn(BaseModel):
    """``POST /crm/fields/{entity}``. The gateway makes the key."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=LABEL_MAX)
    type: FieldType
    options: list[OptionIn] | None = None
    required: bool = False
    help_text: str | None = Field(default=None, max_length=HELP_MAX)
    default_value: Any = None
    lookup_target: str | None = None
    section_id: str | None = None
    visible: bool = True


class FieldPatch(BaseModel):
    """``PATCH /crm/fields/{entity}/{key}``. Only the fields sent change."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX)
    section_id: str | None = None
    required: bool | None = None
    help_text: str | None = Field(default=None, max_length=HELP_MAX)
    default_value: Any = None
    visible: bool | None = None
    options: list[OptionIn] | None = None
    type: FieldType | None = None
    lookup_target: str | None = None


#: What a PATCH may change on a system field (item 3).
SYSTEM_EDITABLE: frozenset[str] = frozenset({"label", "section_id", "visible"})


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=LABEL_MAX)


class LayoutField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    section_id: str | None = None


class LayoutIn(BaseModel):
    """``PUT /crm/field-layout/{entity}``: the sections and the fields, each
    in its new order. One body, one transaction."""

    model_config = ConfigDict(extra="forbid")

    sections: list[str] = []
    fields: list[LayoutField]


class SectionDeleted(BaseModel):
    deleted: str
    #: The fields that now sit in no section.
    moved: int


def _clean_label(value: str, what: str, limit: int) -> str:
    label = value.strip()
    if not label or len(label) > limit or _CONTROL_RE.search(label):
        raise _unprocessable("label", f"{what} needs 1 to {limit} characters, with no control character.")
    return label


async def _section_id(db: Any, slug: str, value: str | None) -> str | None:
    """A section of *slug*, or ``None``, or 422."""
    if value is None:
        return None
    ident = checked_uuid(value, "section_id")
    found = (await db.execute(
        text("SELECT id FROM crm_field_sections WHERE id = CAST(:id AS uuid) "
             "AND entity = :entity"),
        {"id": ident, "entity": ENTITY_BY_SLUG[slug]},
    )).first()
    if found is None:
        raise _unprocessable("section_unknown", f"That section is not a section of {slug}.")
    return ident


def _lookup_target(slug_type: str, target: str | None) -> str | None:
    """The target of a lookup in Native mode, or 422 (item 8)."""
    if slug_type != "lookup":
        if target is not None:
            raise _unprocessable("lookup_target", "Only a lookup field has a target.")
        return None
    if target is None:
        raise _unprocessable("lookup_target", "A lookup field needs a target entity.")
    if target.startswith("module:"):
        raise _unprocessable(
            "lookup_target",
            "A lookup to a Zoho module is for Mirror mode. Choose leads, contacts, "
            "organizations or deals.",
        )
    if target not in NATIVE_LOOKUP_TARGETS:
        raise _unprocessable(
            "lookup_target", f"Choose one of {list(NATIVE_LOOKUP_TARGETS)}.",
        )
    return target


def _check_options_type(kind: str, options: Any) -> None:
    if options is not None and kind not in ("select", "multiselect"):
        raise _unprocessable("options", "Only a picklist or a multi-select has options.")


async def _check_default(db: Any, field: CustomField, value: Any) -> Any:
    """A default that the field accepts, or ``None`` for no default."""
    if value is None:
        return None
    return await check_value(db, field, value, allow_lookup=False)


_FIELD_ROW_SQL = (
    f"SELECT id, {DEF_COLUMNS} FROM crm_field_defs "
    "WHERE entity = :entity AND key = :key"
)


async def _field_row(db: Any, slug: str, key: str) -> Any | None:
    return (await db.execute(
        text(_FIELD_ROW_SQL), {"entity": ENTITY_BY_SLUG[slug], "key": key},
    )).first()


def _wire(slug: str, row: Any) -> FieldDef:
    wired = field_wire(row, slug=slug)
    if wired.is_system:
        fixed = system_field(slug, wired.key)
        if fixed is not None:
            wired = wired.model_copy(update={
                "type": fixed.type, "lookup_target": fixed.lookup_target,
            })
    return wired


def _unique_violation(exc: DBAPIError) -> bool:
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return code == "23505"


async def _next_position(db: Any, slug: str) -> int:
    top = (await db.execute(
        text("SELECT max(position) FROM crm_field_defs WHERE entity = :entity"),
        {"entity": ENTITY_BY_SLUG[slug]},
    )).scalar()
    floor = len(SYSTEM_FIELDS[slug]) - 1
    return max(int(top) if top is not None else -1, floor) + 1


@router.post(
    "/fields/{entity}", response_model=FieldDef, status_code=201, dependencies=_GUARDS,
)
async def create_field(
    entity: str, payload: FieldIn, user: UserContext = Depends(get_current_user),
) -> FieldDef:
    """A new custom field of *entity*, with ``source = 'metorite'``."""
    stored = checked_slug(entity)
    label = _clean_label(payload.label, "A field label", LABEL_MAX)
    _check_options_type(payload.type, payload.options)
    target = _lookup_target(payload.type, payload.lookup_target)
    options = (
        apply_options([], payload.options or [])
        if payload.type in ("select", "multiselect") else None
    )
    try:
        async with _tenant_session() as db:
            await assert_writable(db, current_tenant(), "crm_field_defs", None)
            section = await _section_id(db, entity, payload.section_id)
            taken = {
                r.key for r in (await db.execute(
                    text("SELECT key FROM crm_field_defs WHERE entity = :entity"),
                    {"entity": stored},
                )).fetchall()
            }
            key = validate_key(entity, make_key(entity, label, taken))
            probe = CustomField(
                key=key, label=label, type=payload.type, options=tuple(options or ()),
                required=payload.required, default_value=None,
                lookup_target=target, retired=False,
            )
            default = await _check_default(db, probe, payload.default_value)
            await db.execute(text(
                "INSERT INTO crm_field_defs (entity, key, label, type, options, "
                "source, position, visible, section_id, required, help_text, "
                "default_value, lookup_target) VALUES (:entity, :key, :label, :type, "
                "CAST(:options AS jsonb), :source, :position, :visible, "
                "CAST(:section_id AS uuid), :required, :help_text, "
                "CAST(:default_value AS jsonb), :lookup_target)"
            ), {
                "entity": stored, "key": key, "label": label, "type": payload.type,
                "options": json.dumps(options) if options is not None else None,
                "source": SOURCE, "position": await _next_position(db, entity),
                "visible": payload.visible, "section_id": section,
                "required": payload.required,
                "help_text": (payload.help_text or "").strip() or None,
                "default_value": json.dumps(default) if default is not None else None,
                "lookup_target": target,
            })
            row = await _field_row(db, entity, key)
    except DBAPIError as exc:
        if _unique_violation(exc):
            raise HTTPException(status_code=409, detail={
                "code": "field_key_taken",
                "message": "Another field took that key. Try again.",
            }) from exc
        raise
    return _wire(entity, row)


async def _convert_select_to_multi(db: Any, slug: str, key: str) -> None:
    """Each stored value of *key* becomes a list of one (item 9)."""
    await assert_writable(db, current_tenant(), "crm_field_defs", None)
    table = ENTITIES[slug].table
    await db.execute(text(
        f"UPDATE {table} SET custom = jsonb_set(custom, ARRAY[CAST(:key AS text)], "
        "jsonb_build_array(custom -> CAST(:key AS text))) "
        "WHERE jsonb_typeof(custom -> CAST(:key AS text)) = 'string'"
    ), {"key": key})


async def _holds_values(db: Any, slug: str, key: str) -> bool:
    table = ENTITIES[slug].table
    return (await db.execute(
        text(f"SELECT 1 FROM {table} WHERE jsonb_exists(custom, CAST(:key AS text)) LIMIT 1"),
        {"key": key},
    )).first() is not None


async def _patch_system(
    db: Any, slug: str, key: str, row: Any | None, payload: FieldPatch,
) -> Any:
    """The overlay row of a fixed column. The first edit writes it (item 3)."""
    sent = payload.model_fields_set
    refused = sorted(sent - SYSTEM_EDITABLE)
    if refused:
        raise _unprocessable(
            "system_field",
            "A system field takes a new label, section, order or visibility only.",
            refused=refused,
        )
    index = [f.key for f in SYSTEM_FIELDS[slug]].index(key)
    fixed = SYSTEM_FIELDS[slug][index]
    label = row.label if row is not None else fixed.label
    position = int(row.position) if row is not None else index
    visible = bool(row.visible) if row is not None else True
    section = str(row.section_id) if row is not None and row.section_id else None
    if "label" in sent and payload.label is not None:
        label = _clean_label(payload.label, "A field label", LABEL_MAX)
    if "visible" in sent and payload.visible is not None:
        if not payload.visible and key in ENTITIES[slug].required:
            raise _unprocessable(
                "system_field", f"{label} is required, so it stays visible.",
            )
        visible = payload.visible
    if "section_id" in sent:
        section = await _section_id(db, slug, payload.section_id)
    return await _write_system_row(db, slug, key, label, position, visible, section)


async def _write_system_row(
    db: Any, slug: str, key: str, label: str, position: int, visible: bool,
    section: str | None,
) -> Any:
    fixed = system_field(slug, key)
    if fixed is None:
        raise HTTPException(status_code=404, detail="Field not found.")
    await assert_writable(db, current_tenant(), "crm_field_defs", None)
    await db.execute(text(
        "INSERT INTO crm_field_defs (entity, key, label, type, source, position, "
        "visible, section_id, is_system) VALUES (:entity, :key, :label, :type, "
        ":source, :position, :visible, CAST(:section_id AS uuid), true) "
        "ON CONFLICT (organization_id, entity, key) DO UPDATE SET "
        "label = EXCLUDED.label, position = EXCLUDED.position, "
        "visible = EXCLUDED.visible, section_id = EXCLUDED.section_id, "
        "updated_at = now() WHERE crm_field_defs.is_system"
    ), {
        "entity": ENTITY_BY_SLUG[slug], "key": key, "label": label,
        "type": fixed.type, "source": SOURCE, "position": position,
        "visible": visible, "section_id": section,
    })
    return await _field_row(db, slug, key)


async def _custom_changes(
    db: Any, entity: str, key: str, current: CustomField, payload: FieldPatch,
) -> tuple[dict[str, Any], str]:
    """The column changes of a PATCH of a custom field, and its new type."""
    sent = payload.model_fields_set
    kind = current.type
    changes: dict[str, Any] = {}
    if "type" in sent and payload.type is not None and payload.type != kind:
        if payload.type not in TYPE_CHANGES.get(kind, frozenset()):
            raise _unprocessable(
                "field_type_change",
                f"{current.label} cannot change from {kind} to {payload.type}.",
            )
        changes["type"] = payload.type
        kind = payload.type
    target = current.lookup_target
    if "lookup_target" in sent:
        target = _lookup_target(kind, payload.lookup_target)
        if target != current.lookup_target and await _holds_values(db, entity, key):
            raise _unprocessable(
                "lookup_target_change",
                f"Records hold values of {current.label}, so its target stays.",
            )
        changes["lookup_target"] = target
    options: list[Any] | None = list(current.options) if current.options else None
    if "options" in sent:
        _check_options_type(kind, payload.options)
        if kind in ("select", "multiselect"):
            options = apply_options(list(current.options), payload.options or [])
            changes["options"] = json.dumps(options)
    label = current.label
    if "label" in sent and payload.label is not None:
        label = _clean_label(payload.label, "A field label", LABEL_MAX)
        changes["label"] = label
    if "required" in sent and payload.required is not None:
        changes["required"] = payload.required
    if "visible" in sent and payload.visible is not None:
        changes["visible"] = payload.visible
    if "help_text" in sent:
        changes["help_text"] = (payload.help_text or "").strip() or None
    if "section_id" in sent:
        changes["section_id"] = await _section_id(db, entity, payload.section_id)
    default = current.default_value
    if kind == "multiselect" and current.type == "select" and isinstance(default, str):
        default = [default]
    if "default_value" in sent:
        default = payload.default_value
    probe = CustomField(
        key=key, label=label, type=kind, options=tuple(options or ()),
        required=current.required, default_value=None, lookup_target=target,
        retired=False,
    )
    if "default_value" in sent or "type" in changes or "options" in changes:
        checked = await _check_default(db, probe, default)
        changes["default_value"] = json.dumps(checked) if checked is not None else None
    return changes, kind


@router.patch(
    "/fields/{entity}/{key}", response_model=FieldDef, dependencies=_GUARDS,
)
async def patch_field(
    entity: str, key: str, payload: FieldPatch,
    user: UserContext = Depends(get_current_user),
) -> FieldDef:
    """Change a field. A type change follows the matrix of item 9."""
    stored = checked_slug(entity)
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        row = await _field_row(db, entity, key)
        if system_field(entity, key) is not None:
            written = await _patch_system(db, entity, key, row, payload)
            return _wire(entity, written)
        if row is None or getattr(row, "is_system", False):
            raise HTTPException(status_code=404, detail="Field not found.")
        if row.retired_at is not None:
            raise _unprocessable(
                "field_retired", f"{row.label} is retired. Restore it to change it.",
            )
        current = CustomField.from_row(row)
        changes, kind = await _custom_changes(db, entity, key, current, payload)
        if not changes:
            return _wire(entity, row)
        if current.type == "select" and kind == "multiselect":
            await _convert_select_to_multi(db, entity, key)
        written = await _update_field(db, stored, key, changes)
    return _wire(entity, written)


#: The columns a PATCH may set, and the bind form of each.
_PATCH_COLUMNS: dict[str, str] = {
    "label": ":label", "type": ":type", "options": "CAST(:options AS jsonb)",
    "required": ":required", "visible": ":visible", "help_text": ":help_text",
    "section_id": "CAST(:section_id AS uuid)",
    "default_value": "CAST(:default_value AS jsonb)",
    "lookup_target": ":lookup_target",
}


async def _update_field(db: Any, stored: str, key: str, changes: dict[str, Any]) -> Any:
    await assert_writable(db, current_tenant(), "crm_field_defs", None)
    sets = ", ".join(f"{c} = {_PATCH_COLUMNS[c]}" for c in changes)
    await db.execute(text(
        f"UPDATE crm_field_defs SET {sets}, updated_at = now() "
        "WHERE entity = :entity AND key = :key AND NOT is_system"
    ), {**changes, "entity": stored, "key": key})
    return (await db.execute(
        text(_FIELD_ROW_SQL), {"entity": stored, "key": key},
    )).first()


async def _custom_row(db: Any, slug: str, key: str) -> Any:
    row = await _field_row(db, slug, key)
    if system_field(slug, key) is not None:
        raise _unprocessable("system_field", "A system field cannot be retired or restored.")
    if row is None or getattr(row, "is_system", False):
        raise HTTPException(status_code=404, detail="Field not found.")
    return row


@router.delete(
    "/fields/{entity}/{key}", response_model=FieldDef, dependencies=_GUARDS,
)
async def retire_field(
    entity: str, key: str, user: UserContext = Depends(get_current_user),
) -> FieldDef:
    """Retire a custom field. Each record keeps its value in ``custom``."""
    stored = checked_slug(entity)
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        row = await _custom_row(db, entity, key)
        if row.retired_at is not None:
            return _wire(entity, row)
        # `visible` stays as it is, so a restore gives the field back with
        # the visibility it had (review round 1). Each reader leaves out a
        # retired field by `retired_at`.
        await db.execute(text(
            "UPDATE crm_field_defs SET retired_at = now(), updated_at = now() "
            "WHERE entity = :entity AND key = :key AND NOT is_system"
        ), {"entity": stored, "key": key})
        written = await _field_row(db, entity, key)
    return _wire(entity, written)


@router.post(
    "/fields/{entity}/{key}/restore", response_model=FieldDef, dependencies=_GUARDS,
)
async def restore_field(
    entity: str, key: str, user: UserContext = Depends(get_current_user),
) -> FieldDef:
    """Bring a retired field back, with the values and the visibility it kept."""
    stored = checked_slug(entity)
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        await _custom_row(db, entity, key)
        await db.execute(text(
            "UPDATE crm_field_defs SET retired_at = NULL, updated_at = now() "
            "WHERE entity = :entity AND key = :key AND NOT is_system"
        ), {"entity": stored, "key": key})
        written = await _field_row(db, entity, key)
    return _wire(entity, written)


# ── Sections ────────────────────────────────────────────────────────────────


def _section_wire(row: Any) -> FieldSection:
    return FieldSection(id=str(row.id), label=row.label, position=int(row.position or 0))


@router.post(
    "/field-sections/{entity}", response_model=FieldSection, status_code=201,
    dependencies=_GUARDS,
)
async def create_section(
    entity: str, payload: SectionIn, user: UserContext = Depends(get_current_user),
) -> FieldSection:
    """A new section, last in the order."""
    stored = checked_slug(entity)
    label = _clean_label(payload.label, "A section label", LABEL_MAX)
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        row = (await db.execute(text(
            "INSERT INTO crm_field_sections (entity, label, position) VALUES "
            "(:entity, :label, (SELECT COALESCE(max(position), -1) + 1 FROM "
            "crm_field_sections WHERE entity = :entity)) RETURNING *"
        ), {"entity": stored, "label": label})).first()
    return _section_wire(row)


@router.patch(
    "/field-sections/{entity}/{section_id}", response_model=FieldSection,
    dependencies=_GUARDS,
)
async def rename_section(
    entity: str, section_id: str, payload: SectionIn,
    user: UserContext = Depends(get_current_user),
) -> FieldSection:
    stored = checked_slug(entity)
    ident = checked_uuid(section_id, "section_id")
    label = _clean_label(payload.label, "A section label", LABEL_MAX)
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        row = (await db.execute(text(
            "UPDATE crm_field_sections SET label = :label, updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND entity = :entity RETURNING *"
        ), {"id": ident, "entity": stored, "label": label})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    return _section_wire(row)


@router.delete(
    "/field-sections/{entity}/{section_id}", response_model=SectionDeleted,
    dependencies=_GUARDS,
)
async def delete_section(
    entity: str, section_id: str, user: UserContext = Depends(get_current_user),
) -> SectionDeleted:
    """Remove a section. Its fields move to no section, in the same transaction."""
    stored = checked_slug(entity)
    ident = checked_uuid(section_id, "section_id")
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        found = (await db.execute(text(
            "SELECT id FROM crm_field_sections WHERE id = CAST(:id AS uuid) "
            "AND entity = :entity"
        ), {"id": ident, "entity": stored})).first()
        if found is None:
            raise HTTPException(status_code=404, detail="Section not found.")
        moved = (await db.execute(text(
            "UPDATE crm_field_defs SET section_id = NULL, updated_at = now() "
            "WHERE section_id = CAST(:id AS uuid) RETURNING key"
        ), {"id": ident})).fetchall()
        await db.execute(text(
            "DELETE FROM crm_field_sections WHERE id = CAST(:id AS uuid)"
        ), {"id": ident})
    return SectionDeleted(deleted=ident, moved=len(moved))


# ── The layout (item 4) ─────────────────────────────────────────────────────


#: ⚠️ Each studio route calls ``core.assert_writable`` itself, first in its
#: session: both studio tables are settings tables, so Mirror mode gives 409
#: (item 10). ``test_crm_mirror_readonly.py`` checks each raw write.


async def write_section_position(db: Any, ident: str, position: int) -> None:
    """One section's place. Only :func:`put_layout` calls it, after its guard."""
    await db.execute(text(
        "UPDATE crm_field_sections SET position = :p, updated_at = now() "
        "WHERE id = CAST(:id AS uuid)"
    ), {"id": ident, "p": position})


async def write_field_position(
    db: Any, stored: str, key: str, position: int, section: str | None,
) -> None:
    """One field's place. Only :func:`put_layout` calls it, after its guard."""
    await db.execute(text(
        "UPDATE crm_field_defs SET position = :p, section_id = CAST(:s AS uuid), "
        "updated_at = now() WHERE entity = :entity AND key = :key AND NOT is_system"
    ), {"entity": stored, "key": key, "p": position, "s": section})


@router.put(
    "/field-layout/{entity}", response_model=FieldLayout, dependencies=_GUARDS,
)
async def put_layout(
    entity: str, payload: LayoutIn, user: UserContext = Depends(get_current_user),
) -> FieldLayout:
    """The section order and the field order, in one transaction.

    The body names each section once, and each system field and each live
    custom field once. A retired field may be left out, and it keeps its
    place. Anything else gives 422 and writes nothing.
    """
    stored = checked_slug(entity)
    given_sections = [checked_uuid(s, "sections") for s in payload.sections]
    async with _tenant_session() as db:
        await assert_writable(db, current_tenant(), "crm_field_defs", None)
        existing = {
            str(r.id) for r in (await db.execute(text(
                "SELECT id FROM crm_field_sections WHERE entity = :entity"
            ), {"entity": stored})).fetchall()
        }
        if len(set(given_sections)) != len(given_sections) or set(given_sections) != existing:
            raise _unprocessable(
                "layout_sections", "Name each section of this entity once.",
            )
        rows = (await db.execute(text(_LAYOUT_ROWS_SQL), {"entity": stored})).fetchall()
        system_rows = {r.key: r for r in rows if r.is_system}
        custom = {r.key: r for r in rows if not r.is_system}
        system_keys = [f.key for f in SYSTEM_FIELDS[entity]]
        live = {k for k, r in custom.items() if r.retired_at is None}
        keys = [f.key for f in payload.fields]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        unknown = sorted(set(keys) - set(custom) - set(system_keys))
        missing = sorted((live | set(system_keys)) - set(keys))
        if twice or unknown or missing:
            raise _unprocessable(
                "layout_fields",
                "Name each system field and each live custom field once.",
                twice=twice, unknown=unknown, missing=missing,
            )
        for item in payload.fields:
            if item.section_id is not None and checked_uuid(
                item.section_id, "section_id",
            ) not in existing:
                raise _unprocessable(
                    "section_unknown", f"That section is not a section of {entity}.",
                )
        for position, ident in enumerate(given_sections):
            await write_section_position(db, ident, position)
        for position, item in enumerate(payload.fields):
            section = checked_uuid(item.section_id, "section_id") if item.section_id else None
            if item.key in custom:
                await write_field_position(db, stored, item.key, position, section)
                continue
            kept = system_rows.get(item.key)
            fixed = SYSTEM_FIELDS[entity][system_keys.index(item.key)]
            await _write_system_row(
                db, entity, item.key,
                kept.label if kept is not None else fixed.label,
                position,
                bool(kept.visible) if kept is not None else True,
                section,
            )
        return await read_layout(db, entity)


_LAYOUT_ROWS_SQL = (
    "SELECT key, label, visible, is_system, retired_at FROM crm_field_defs "
    "WHERE entity = :entity"
)


__all__ = [
    "FIELD_TYPES",
    "NATIVE_LOOKUP_TARGETS",
    "ROUTE_WORDS",
    "TYPE_CHANGES",
    "CustomField",
    "apply_options",
    "check_value",
    "custom_for_create",
    "custom_for_patch",
    "key_problem",
    "load_custom_fields",
    "make_key",
    "option_value",
    "validate_key",
]
