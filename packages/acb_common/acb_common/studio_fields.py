"""The field types of a Studio object, and the one check of each value (WS-STU C1).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 (the types) and the §14 C1 row.

**One module of field types.** :data:`FIELD_TYPES` is the one list. The
manifest validator (``studio_manifest.py``, A2) and the object store
(``gateway/studio/objects_store.py``, C1) import it, and neither keeps a copy.
``tests/unit/test_studio_fields.py`` fails when a second list appears.

**Pure.** No gateway import and no I/O, so A2, the store and PKG-13 can each
import it. ``app_manifests.base`` is the one other import, for the app id
pattern of a relation target.

**Two checks.**

* :func:`validate_field_spec` checks the spec of one field: its key, its type,
  ``options``, ``to``, ``many``, ``required`` and ``hot``. It answers a list of
  :class:`SpecProblem`, so A2 turns each into a JSON path. :func:`parse_field_spec`
  raises on the first one, for the store.
* :func:`check_value` checks one value of one field, and
  :func:`validate_record` checks a whole record. Each refusal raises
  :class:`FieldError` with the field key and one sentence that names the key.
  A refusal never echoes the value, because a record holds member data.

**Numbers.** A number, a money amount and a coordinate are below 10^15, so a
browser holds each one exactly. A float has no exponent in its text: Postgres
writes ``1e-05`` as ``0.00001``, so a float in exponent form would make the
store's byte count disagree with the CHECK of migration ``studio_objects``.
The skill store of F1 holds the same rule.

**Size caps.** Each cap of a record and of the registry is a constant here.
The migration holds the same numbers, and ``test_studio_objects_r8.py`` proves
that the two agree.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from acb_common.app_manifests.base import APP_ID_PATTERN

#: The sixteen field types of §3.1, in camelCase as the genUI node types are.
FIELD_TYPES: tuple[str, ...] = (
    "text", "longText", "number", "money", "date", "datetime", "checkbox",
    "select", "multiSelect", "person", "relation", "file", "url", "email",
    "phone", "geo",
)  # fmt: skip

#: The types that a hot field may have. Each maps to one value column of
#: ``studio_record_hot`` (see :func:`hot_value`).
HOT_TYPES: tuple[str, ...] = (
    "text", "select", "number", "money", "date", "datetime", "checkbox", "email",
)  # fmt: skip

#: A field key and an object name: lower-case, a letter first, at most 48.
NAME_PATTERN = r"[a-z][a-z0-9_]{0,47}"
#: A relation to an object of another app: ``<app>.<object>``.
RELATION_TO_PATTERN = rf"{APP_ID_PATTERN}\.{NAME_PATTERN}"

# --- the caps ---------------------------------------------------------------

MAX_TEXT_CHARS = 2_000
#: A2's ``MAX_STRING_CHARS``.
MAX_LONG_TEXT_CHARS = 20_000
#: A2's ``MAX_LIST_ITEMS``: the values of one ``many`` field.
MAX_MANY_ITEMS = 500
MAX_OPTIONS = 100
MAX_OPTION_CHARS = 80
MAX_LABEL_CHARS = 120
#: A number, a money amount and a coordinate are below this, in size.
MAX_ABS_NUMBER = 10**15
MAX_ID_CHARS = 64
MAX_URL_CHARS = 2_048
MAX_EMAIL_CHARS = 254
#: The JSON text of one record, in bytes. The CHECK ``studio_records_data_size``.
MAX_RECORD_BYTES = 65_536
#: The registry caps. The store checks each one under the org lock.
MAX_FIELDS_PER_OBJECT = 60
MAX_OBJECTS_PER_PACKAGE = 50
MAX_OBJECTS_PER_ORG = 200
#: The index budget. The trigger ``studio_object_fields_hot_budget`` holds both.
MAX_HOT_PER_OBJECT = 8
MAX_HOT_PER_ORG = 24
#: A field turns hot on an object of at most this many records (the C2 size).
MAX_HOT_BACKFILL = 50_000
#: The text that ``studio_record_hot.v_text`` holds: the first 256 characters.
#: A btree entry has a size limit, so the side table never holds a long text.
HOT_TEXT_CHARS = 256

_SPEC_KEYS = frozenset({"key", "type", "label", "required", "many", "options", "to", "hot"})

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
_RELATION_TO_RE = re.compile(RELATION_TO_PATTERN, re.ASCII)
# `fullmatch` everywhere, and ASCII: Python's `\d` also matches other digits.
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_DATETIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII,
)
_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)
_PHONE_RE = re.compile(r"\+[1-9][0-9]{7,14}", re.ASCII)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", re.ASCII)
#: A simple address check, in the shape of the WHATWG form check, with a dot
#: in the domain.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",
    re.ASCII,
)


class FieldError(ValueError):
    """One refused field spec or value: the field key and one sentence.

    The sentence names the key, so a model can act on it. It never echoes a
    value of a record.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class RecordError(ValueError):
    """A record that the store refuses as a whole: not an object, or too large."""


@dataclass(frozen=True)
class SpecProblem:
    """One problem of a field spec. ``path`` is relative to the field, as
    ``options`` or ``options[2]``. ``got`` is a structure value to echo, or None."""

    path: str
    message: str
    got: str | None = None


@dataclass(frozen=True)
class FieldSpec:
    """One field of an object, after :func:`parse_field_spec`."""

    key: str
    type: str
    label: str | None = None
    required: bool = False
    many: bool = False
    options: tuple[str, ...] | None = None
    to: str | None = None
    hot: bool = False

    def stored_spec(self) -> dict[str, Any]:
        """The ``spec`` column: every part but the key, the type and ``hot``,
        which have their own columns."""
        out: dict[str, Any] = {"required": self.required, "many": self.many}
        if self.label is not None:
            out["label"] = self.label
        if self.options is not None:
            out["options"] = list(self.options)
        if self.to is not None:
            out["to"] = self.to
        return out


# --- the field spec ---------------------------------------------------------


def storable_text(value: str) -> bool:
    """True when Postgres takes *value* in a text column and in jsonb: no NUL,
    and no lone surrogate (it has no UTF-8 form)."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _type_list() -> str:
    return f"{', '.join(FIELD_TYPES[:-1])} or {FIELD_TYPES[-1]}"


def _check_options(kind: str, options: Any, out: list[SpecProblem]) -> None:
    if kind not in ("select", "multiSelect"):
        if options is not None:
            out.append(SpecProblem("options", "Only a select or multiSelect field takes `options`."))
        return
    if not options:
        out.append(SpecProblem("options", "A select field needs at least one option."))
        return
    if not isinstance(options, list):
        out.append(SpecProblem("options", "The options are a list of text."))
        return
    if len(options) > MAX_OPTIONS:
        out.append(SpecProblem("options", f"A select field holds at most {MAX_OPTIONS} options."))
        return
    seen: set[str] = set()
    for i, opt in enumerate(options):
        if (not isinstance(opt, str) or not opt.strip() or len(opt) > MAX_OPTION_CHARS
                or not storable_text(opt)):
            out.append(SpecProblem(
                f"options[{i}]",
                f"Each option is text of 1 to {MAX_OPTION_CHARS} characters, with no NUL."))
        elif opt in seen:
            out.append(SpecProblem(f"options[{i}]", "This option is listed twice. Each must be unique."))
        else:
            seen.add(opt)


def _check_to(kind: str, to: Any, objects: Collection[str] | None,
              out: list[SpecProblem]) -> None:
    if kind != "relation":
        if to is not None:
            out.append(SpecProblem("to", "Only a relation field takes `to`."))
        return
    if not to:
        out.append(SpecProblem("to", "A relation field needs a `to` target."))
        return
    if not isinstance(to, str):
        out.append(SpecProblem("to", "A relation target is text."))
        return
    bare = _NAME_RE.fullmatch(to) is not None and (objects is None or to in objects)
    if not bare and not _RELATION_TO_RE.fullmatch(to):
        out.append(SpecProblem("to", "Not a declared object or an <app>.<object> target.", to))


def _check_flags(kind: str, spec: Mapping[str, Any], out: list[SpecProblem]) -> None:
    for flag in ("required", "many", "hot"):
        value = spec.get(flag, False)
        if value is not None and type(value) is not bool:
            out.append(SpecProblem(flag, f"`{flag}` is true or false."))
    many = spec.get("many") is True
    if many and kind in ("multiSelect", "checkbox"):
        out.append(SpecProblem(
            "many", f"A {kind} field takes no `many`, because it holds one value or one list."))
    if spec.get("hot") is True:
        if kind not in HOT_TYPES:
            out.append(SpecProblem(
                "hot", f"This field type cannot be hot. Use {', '.join(HOT_TYPES)}.", kind))
        elif many:
            out.append(SpecProblem("hot", "A field with `many` cannot be hot."))


def validate_field_spec(spec: Mapping[str, Any], *,
                        objects: Collection[str] | None = None) -> list[SpecProblem]:
    """Every problem of one field spec. An empty list means the spec is valid.

    *objects* is the set of object names that a bare ``to`` may name. With
    None, a bare name passes on its shape alone.
    """
    out: list[SpecProblem] = []
    if not isinstance(spec, Mapping):
        return [SpecProblem("", "A field is an object with a key and a type.")]
    for extra in sorted(str(k) for k in spec if k not in _SPEC_KEYS):
        out.append(SpecProblem(extra, "This is not a key of a field. Remove it.", extra))
    key = spec.get("key")
    if not isinstance(key, str) or not _NAME_RE.fullmatch(key):
        out.append(SpecProblem(
            "key", "A field key is lower-case letters, digits and _, a letter first, at most 48."))
    kind = spec.get("type")
    if not isinstance(kind, str) or kind not in FIELD_TYPES:
        out.append(SpecProblem("type", f"Field type is unknown. Use {_type_list()}."))
        return out
    label = spec.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL_CHARS
                              or not storable_text(label)):
        out.append(SpecProblem("label", f"A label is text of at most {MAX_LABEL_CHARS} characters."))
    _check_options(kind, spec.get("options"), out)
    _check_to(kind, spec.get("to"), objects, out)
    _check_flags(kind, spec, out)
    return out


def _safe_key(key: Any) -> str:
    """The key to name in a refusal: the key when it has the key shape, else ``?``."""
    return key if isinstance(key, str) and _NAME_RE.fullmatch(key) else "?"


def parse_field_spec(spec: Mapping[str, Any], *,
                     objects: Collection[str] | None = None) -> FieldSpec:
    """One field spec as a :class:`FieldSpec`. Raises :class:`FieldError` on the
    first problem of :func:`validate_field_spec`."""
    problems = validate_field_spec(spec, objects=objects)
    if problems:
        key = _safe_key(spec.get("key") if isinstance(spec, Mapping) else None)
        p = problems[0]
        where = f" (`{p.path}`)" if p.path else ""
        raise FieldError(key, f"Field `{key}`{where}: {p.message}")
    options = spec.get("options")
    return FieldSpec(
        key=spec["key"], type=spec["type"], label=spec.get("label"),
        required=spec.get("required") is True, many=spec.get("many") is True,
        options=None if options is None else tuple(options), to=spec.get("to"),
        hot=spec.get("hot") is True)


def stored_field(key: str, kind: str, spec: Mapping[str, Any], hot: bool) -> FieldSpec:
    """A :class:`FieldSpec` from the columns of one ``studio_object_fields`` row."""
    return parse_field_spec({**dict(spec), "key": key, "type": kind, "hot": hot})


# --- the values -------------------------------------------------------------


def _plain_number(value: Any) -> bool:
    """A whole number or a float, not a bool, below 10^15 in size. A float is
    finite and its text has no exponent, so Postgres writes the same digits."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, float) and (not math.isfinite(value) or "e" in float.__repr__(value)):
        return False
    return abs(value) < MAX_ABS_NUMBER


def _text(f: FieldSpec, v: Any, limit: int) -> str:
    if not isinstance(v, str) or len(v) > limit:
        raise FieldError(f.key, f"Field `{f.key}` takes text of at most {limit} characters.")
    if not storable_text(v):
        raise FieldError(f.key, f"Field `{f.key}` holds a NUL or a broken character. Remove it.")
    return v


def _number(f: FieldSpec, v: Any) -> int | float:
    if not _plain_number(v):
        raise FieldError(
            f.key, f"Field `{f.key}` takes a number below 10^15, written with no exponent.")
    return v


def _money(f: FieldSpec, v: Any) -> dict[str, Any]:
    if (not isinstance(v, Mapping) or set(v) != {"minor", "currency"}
            or isinstance(v["minor"], bool) or not isinstance(v["minor"], int)
            or abs(v["minor"]) >= MAX_ABS_NUMBER):
        raise FieldError(
            f.key, f'Field `{f.key}` takes {{"minor": <whole number>, "currency": "INR"}}.')
    if not isinstance(v["currency"], str) or not _CURRENCY_RE.fullmatch(v["currency"]):
        raise FieldError(f.key, f"Field `{f.key}` needs a currency of three capital letters, as INR.")
    return {"minor": v["minor"], "currency": v["currency"]}


def _date(f: FieldSpec, v: Any) -> str:
    try:
        if isinstance(v, str) and _DATE_RE.fullmatch(v):
            date.fromisoformat(v)
            return v
    except ValueError:
        pass
    raise FieldError(f.key, f"Field `{f.key}` takes a real date as YYYY-MM-DD.")


def _datetime(f: FieldSpec, v: Any) -> str:
    """RFC 3339 with an offset. Stored in UTC, with a final ``Z``."""
    try:
        if isinstance(v, str) and _DATETIME_RE.fullmatch(v):
            text = v[:-1] + "+00:00" if v[-1] in "Zz" else v
            when = datetime.fromisoformat(text.replace("t", "T"))
            utc = when.astimezone(UTC).replace(tzinfo=None)
            spec = "microseconds" if utc.microsecond else "seconds"
            return utc.isoformat(timespec=spec) + "Z"
    except (ValueError, OverflowError):
        pass
    raise FieldError(
        f.key, f"Field `{f.key}` takes a date and time with an offset, as 1999-12-31T23:59:00+05:30.")


def _checkbox(f: FieldSpec, v: Any) -> bool:
    if type(v) is not bool:
        raise FieldError(f.key, f"Field `{f.key}` takes true or false.")
    return v


def _select(f: FieldSpec, v: Any) -> str:
    if not isinstance(v, str) or v not in (f.options or ()):
        raise FieldError(f.key, f"Field `{f.key}` takes one of its listed options.")
    return v


def _multi_select(f: FieldSpec, v: Any) -> list[str]:
    options = f.options or ()
    if (not isinstance(v, list) or len(v) > MAX_MANY_ITEMS
            or not all(isinstance(x, str) and x in options for x in v)
            or len(set(v)) != len(v)):
        raise FieldError(f.key, f"Field `{f.key}` takes a list of distinct options from its list.")
    return list(v)


def _address(v: Any) -> bool:
    return isinstance(v, str) and len(v) <= MAX_EMAIL_CHARS and _EMAIL_RE.fullmatch(v) is not None


def _person(f: FieldSpec, v: Any) -> str:
    # The member's address as the member records hold it: lower-case. The
    # store does not check the membership, because the door does.
    if not _address(v) or v != v.lower():
        raise FieldError(f.key, f"Field `{f.key}` takes the lower-case email address of a member.")
    return v


def _email(f: FieldSpec, v: Any) -> str:
    if not _address(v):
        raise FieldError(
            f.key, f"Field `{f.key}` takes an email address of at most {MAX_EMAIL_CHARS} characters.")
    return v.lower()


def _relation(f: FieldSpec, v: Any) -> dict[str, str]:
    if (not isinstance(v, Mapping) or set(v) != {"id"} or not isinstance(v["id"], str)
            or not _ID_RE.fullmatch(v["id"])):
        raise FieldError(
            f.key, f'Field `{f.key}` takes {{"id": "<id>"}}, an id of at most {MAX_ID_CHARS} '
            "letters, digits, dots, colons, dashes or underscores.")
    return {"id": v["id"]}


def _file(f: FieldSpec, v: Any) -> dict[str, str]:
    # The id of an `attachments` row. The door checks that the viewer may open it.
    try:
        if isinstance(v, Mapping) and set(v) == {"id"} and isinstance(v["id"], str):
            return {"id": str(uuid.UUID(v["id"]))}
    except ValueError:
        pass
    raise FieldError(f.key, f'Field `{f.key}` takes {{"id": "<attachment uuid>"}}.')


def _url(f: FieldSpec, v: Any) -> str:
    message = f"Field `{f.key}` takes an http or https address of at most {MAX_URL_CHARS} characters."
    if (not isinstance(v, str) or len(v) > MAX_URL_CHARS or not storable_text(v)
            or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in v)):
        raise FieldError(f.key, message)
    try:
        parts = urlsplit(v)
        _ = parts.port  # a bad port raises here
    except ValueError:
        raise FieldError(f.key, message) from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise FieldError(f.key, message)
    return v


def _phone(f: FieldSpec, v: Any) -> str:
    if not isinstance(v, str) or not _PHONE_RE.fullmatch(v):
        raise FieldError(f.key, f"Field `{f.key}` takes a phone number in E.164 form: + then 8 to 15 digits.")
    return v


def _geo(f: FieldSpec, v: Any) -> dict[str, int | float]:
    if (isinstance(v, Mapping) and set(v) == {"lat", "lng"}
            and _plain_number(v["lat"]) and _plain_number(v["lng"])
            and -90 <= v["lat"] <= 90 and -180 <= v["lng"] <= 180):
        return {"lat": v["lat"], "lng": v["lng"]}
    raise FieldError(
        f.key, f'Field `{f.key}` takes {{"lat": <-90 to 90>, "lng": <-180 to 180>}}, with no exponent.')


_CHECKS: dict[str, Any] = {
    "text": lambda f, v: _text(f, v, MAX_TEXT_CHARS),
    "longText": lambda f, v: _text(f, v, MAX_LONG_TEXT_CHARS),
    "number": _number,
    "money": _money,
    "date": _date,
    "datetime": _datetime,
    "checkbox": _checkbox,
    "select": _select,
    "multiSelect": _multi_select,
    "person": _person,
    "relation": _relation,
    "file": _file,
    "url": _url,
    "email": _email,
    "phone": _phone,
    "geo": _geo,
}
assert tuple(_CHECKS) == FIELD_TYPES, "each field type has one check"


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def check_value(field: FieldSpec, value: Any) -> Any:
    """One value of *field*, as the store keeps it. Raises :class:`FieldError`.

    A ``many`` field takes a list of at most :data:`MAX_MANY_ITEMS` values.
    The answer is normalized: an email in lower case, a datetime in UTC.
    """
    check = _CHECKS[field.type]
    if field.many:
        if not isinstance(value, list) or len(value) > MAX_MANY_ITEMS:
            raise FieldError(
                field.key, f"Field `{field.key}` takes a list of at most {MAX_MANY_ITEMS} values.")
        if any(v is None for v in value):
            raise FieldError(field.key, f"Field `{field.key}` holds an empty value in its list.")
        return [check(field, v) for v in value]
    return check(field, value)


def validate_record(fields: Sequence[FieldSpec], data: Any) -> dict[str, Any]:
    """The whole record *data* as the store keeps it, in the order of *fields*.

    A key that is not a field is refused. A required field must hold a value
    that is not empty. A ``None`` value means "no value", and the answer
    drops it. Raises :class:`FieldError` or :class:`RecordError`.
    """
    if not isinstance(data, Mapping):
        raise RecordError("A record is a JSON object of field values.")
    by_key = {f.key: f for f in fields}
    for key in data:
        if key not in by_key:
            safe = _safe_key(key)
            raise FieldError(safe, f"Field `{safe}` is not a field of this object.")
    out: dict[str, Any] = {}
    for f in fields:
        value = data.get(f.key)
        if _blank(value):
            if f.required:
                raise FieldError(f.key, f"Field `{f.key}` is required.")
            if value is None:
                continue
        out[f.key] = check_value(f, value)
    return out


def record_json(data: Mapping[str, Any]) -> str:
    """The JSON text of a checked record, at most :data:`MAX_RECORD_BYTES` bytes.

    The separators are those of the jsonb text form. :func:`check_value`
    refused each value that Postgres writes longer, so this count is the
    count of the CHECK ``studio_records_data_size``.
    """
    encoded = json.dumps(dict(data), ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
        raise RecordError(
            f"A record holds at most {MAX_RECORD_BYTES} bytes of JSON. Shorten a long value.")
    return encoded


def hot_value(field: FieldSpec, value: Any) -> dict[str, Any] | None:
    """The ``studio_record_hot`` row of one checked value, or None.

    The keys are ``k`` (the field key), and ``t``, ``n``, ``ts`` and ``b`` for
    the four value columns. Exactly one value column is set. A field that is
    not hot, and a missing value, give None.
    """
    if not field.hot or value is None:
        return None
    row: dict[str, Any] = {"k": field.key, "t": None, "n": None, "ts": None, "b": None}
    kind = field.type
    if kind in ("text", "select", "email"):
        row["t"] = value[:HOT_TEXT_CHARS]
    elif kind == "number":
        row["n"] = value
    elif kind == "money":
        row["n"] = value["minor"]
    elif kind == "date":
        row["ts"] = f"{value}T00:00:00Z"
    elif kind == "datetime":
        row["ts"] = value
    elif kind == "checkbox":
        row["b"] = value
    else:  # parse_field_spec refuses `hot` on every other type
        raise FieldError(field.key, f"Field `{field.key}` cannot be hot.")
    return row
