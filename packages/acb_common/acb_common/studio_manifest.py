"""The Studio package manifest, schema v1, and its one validator (STU-A2).

Spec: ``project-docs/specs/ai_studio.md`` section 3 (the model), 3.6 (the
example), 3.7 (the refusals) and 5 (the blocks).

**One manifest, one validator.** The gateway routes, the builder tools and the
skill tool pack all call :func:`validate_manifest`. It is pure data. It
imports no gateway code and does no I/O. The catalog is an INPUT: a map of
action id to class letter. With no catalog, only structure is checked.

**Each error is a path and one sentence.** A model can act on it. A message
has at most 25 words. A key that a later slice will lift is "reserved": the
message names the slice, as ``rules`` names C6.

**The TypeScript twin** is ``workbench/control_plane/src/lib/studio/manifest.ts``.
``tests/unit/test_studio_manifest_ts_parity.py`` holds the two equal. The JSON
Schema export is ``studio_manifest.schema.json`` beside this file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from acb_common.app_manifests.base import APP_ID_PATTERN, CLASSES, OP_PATTERN, UUID_PATTERN
from acb_common.secret_shapes import SECRET_SHAPES, is_secret_key
from acb_common.studio_fields import (
    FIELD_TYPES,
    MAX_FIELDS_PER_OBJECT,
    MAX_HOT_PER_OBJECT,
    MAX_HOT_PER_ORG,
    MAX_LABEL_CHARS,
    MAX_OBJECTS_PER_PACKAGE,
    NAME_PATTERN,
    validate_field_spec,
)

STUDIO_VERSION = 1

#: The one list of field types is `studio_fields.FIELD_TYPES` (C1). This module
#: imports it and keeps no copy, and a caller may import it from here too.

BLOCKS: tuple[str, ...] = (
    "table", "form", "detail", "board", "chart", "stat", "calendar",
    "timeline", "mindMap", "processFlow", "checklist", "map", "markdown",
)  # fmt: skip

OBJECT_OPS: tuple[str, ...] = ("list", "get", "create", "update", "delete")

#: The class of each object verb (STU-B2a, ``ai_studio.md`` §3.2). It is the
#: one source of the class of an ``object:<name>.<op>`` action. The B1 catalog
#: (``catalog_rows``) gives the class of each other source. A read is A. A
#: create or an update is B, because the app can undo it. A delete is C.
#: ``test_studio_action_door.py`` holds these keys equal to :data:`OBJECT_OPS`.
OBJECT_OP_CLASSES: dict[str, str] = {
    "list": "A",
    "get": "A",
    "create": "B",
    "update": "B",
    "delete": "C",
}

#: The caps that bound the work. Each refusal names a path and one sentence.
MAX_DOC_BYTES = 256 * 1024
MAX_STRING_CHARS = 20_000
MAX_LIST_ITEMS = 500
MAX_DEPTH = 20  # the same bound as `_GENUI_MAX_DEPTH` in `acb_skills/write_artifact.py`

#: A key that v1 refuses, and the slice that lifts the refusal.
RESERVED_KEYS: dict[str, str] = {
    "rules": "C6",
    "state_machine": "C6",
    "states": "C6",
    "formula": "C7",
    "code": "L1",
}
RESERVED_FIELD_TYPES: dict[str, str] = {"formula": "C7", "rollup": "C7"}
RESERVED_BLOCKS: dict[str, str] = {"code": "L1", "custom": "L1", "customUi": "L1"}

#: Subtrees that hold member data, not structure. The reserved-key scan skips them.
_DATA_KEYS = frozenset({"set", "where", "when", "args"})

_NAME = NAME_PATTERN
_DASHED = r"[a-z][a-z0-9_-]{0,47}"
_OPS = "|".join(OBJECT_OPS)
# `fullmatch` everywhere: Python `$` also matches before a final newline.
_USES_RE = re.compile(
    rf"(?:object:(?P<obj>{_NAME})\.(?P<op>{_OPS})"
    rf"|integration:(?P<integ>{_NAME}(?:\.{_NAME})?)"
    rf"|workflow:(?P<wf>{UUID_PATTERN})"
    rf"|(?P<app>{APP_ID_PATTERN})\.(?P<appop>{OP_PATTERN}))"
)

_COLOR_KEYS = frozenset({"color", "colour", "font", "fontsize", "font_size", "font-size"})
_STYLE_KEYS = frozenset({
    "style", "styles", "theme", "background", "bg", "fill", "stroke", "border", "accent", "tint",
})  # fmt: skip
_NAMED_COLORS = frozenset({
    "red", "green", "blue", "yellow", "orange", "purple", "pink", "black", "white",
    "gray", "grey", "brown", "cyan", "magenta", "teal", "navy", "maroon", "olive",
    "lime", "aqua", "silver", "gold", "indigo", "violet", "crimson", "coral",
})  # fmt: skip
_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_COLOR_FN_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", re.IGNORECASE)
_PX_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?px\b", re.IGNORECASE)


class ManifestError(BaseModel):
    """One refusal: a JSON path and one sentence a model can act on."""

    path: str
    message: str


@dataclass(frozen=True)
class UsesRef:
    """The parts of one ``uses`` value of the A2 grammar (STU-B2a).

    ``source`` is ``object``, ``manifest`` (an ``<app>.<op>`` door op),
    ``integration`` or ``workflow``, the source names of the B1 catalog.
    ``object`` and ``op`` are set for an ``object:`` value only.
    """

    source: Literal["object", "manifest", "integration", "workflow"]
    object: str | None = None
    op: str | None = None


def parse_uses(uses: object) -> UsesRef | None:
    """The parts of *uses*, or None when it is not of the A2 grammar."""
    if not isinstance(uses, str):
        return None
    m = _USES_RE.fullmatch(uses)
    if m is None:
        return None
    if m.group("obj"):
        return UsesRef(source="object", object=m.group("obj"), op=m.group("op"))
    if m.group("integ"):
        return UsesRef(source="integration")
    if m.group("wf"):
        return UsesRef(source="workflow")
    return UsesRef(source="manifest")


# --- the schema ------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FieldDef(_Strict):
    key: str = Field(pattern=rf"^{_NAME}$")
    # The one list, `studio_fields.FIELD_TYPES`. A tuple in `Literal[...]` is
    # the same as its members written out, so the schema export is unchanged.
    type: Literal[FIELD_TYPES]  # type: ignore[valid-type]
    label: str | None = None
    required: bool = False
    many: bool = False
    options: list[str] | None = None
    to: str | None = None
    #: The field gets a row in the index side table of C1 (`studio_record_hot`).
    hot: bool = False


class ObjectDef(_Strict):
    name: str = Field(pattern=rf"^{_NAME}$")
    #: The CHECK `studio_objects_label` of C1 holds the same cap.
    label: str | None = Field(default=None, max_length=MAX_LABEL_CHARS)
    fields: list[FieldDef] = Field(default_factory=list)


class ActionDef(_Strict):
    id: str = Field(pattern=rf"^{OP_PATTERN}$")
    uses: str
    title: str | None = None
    set: dict[str, Any] | None = None


class Source(_Strict):
    object: str | None = None
    action: str | None = None
    args: dict[str, Any] | None = None


class Card(_Strict):
    title: str | None = None
    meta: list[str] = Field(default_factory=list)


class ViewDef(_Strict):
    id: str = Field(pattern=rf"^{_DASHED}$")
    block: Literal[
        "table", "form", "detail", "board", "chart", "stat", "calendar",
        "timeline", "mindMap", "processFlow", "checklist", "map", "markdown",
    ]  # fmt: skip
    title: str | None = None
    source: Source | None = None
    object: str | None = None
    fields: list[str] = Field(default_factory=list)
    submit: str | None = None
    group_by: str | None = None
    card: Card | None = None
    start: str | None = None
    end: str | None = None
    columns: list[str] = Field(default_factory=list)
    sort: str | None = None
    actions: list[str] = Field(default_factory=list)
    text: str | None = None


class Trigger(_Strict):
    record: str | None = None
    when: dict[str, Any] | None = None
    schedule: str | None = None


class WorkflowDef(_Strict):
    id: str = Field(pattern=rf"^{_DASHED}$")
    trigger: Trigger | None = None
    graph: str | None = None


class SkillExample(_Strict):
    prompt: str
    action: str | None = None
    args: dict[str, Any] | None = None


class Skill(_Strict):
    description: str
    instructions: str = ""
    actions: list[str] = Field(default_factory=list)
    cards: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    examples: list[SkillExample] = Field(default_factory=list)


class ShellJob(_Strict):
    verb: str
    view: str


class ShellNeed(_Strict):
    object: str
    where: dict[str, Any] | None = None


class Shell(_Strict):
    jobs: list[ShellJob] = Field(default_factory=list)
    needs: list[ShellNeed] = Field(default_factory=list)
    search: list[str] = Field(default_factory=list)


class Manifest(_Strict):
    studio: Literal[1]

    @field_validator("studio", mode="before")
    @classmethod
    def _studio_is_the_integer_one(cls, v: Any) -> Any:
        # `Literal[1]` also accepts `True` and `1.0`, even in strict mode.
        if type(v) is not int:
            raise ValueError("`studio` must be the whole number 1")
        return v

    id: str = Field(pattern=rf"^{_DASHED}$")
    name: str
    purpose: str
    team: str | None = None
    connections: list[str] = Field(default_factory=list)
    objects: list[ObjectDef] = Field(default_factory=list)
    actions: list[ActionDef] = Field(default_factory=list)
    views: list[ViewDef] = Field(default_factory=list)
    workflows: list[WorkflowDef] = Field(default_factory=list)
    skill: Skill | None = None
    shell: Shell | None = None


# --- the validator ---------------------------------------------------------


_GOT = " Got `"


def _short(value: Any, limit: int = 40) -> str:
    """The echo of a bad value: one line, at most ``limit`` characters."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _got(value: Any) -> str:
    return f"{_GOT}{_short(value)}`."


def _cap(text: str, limit: int = 25) -> str:
    """Cut the head to ``limit`` words. The ` Got `...`` tail stays last and whole."""
    head, sep, tail = text.partition(_GOT)
    words = head.split()
    if len(words) > limit:
        head = " ".join(words[: limit - 1]) + " ..."
    return head + sep + tail


def _path(loc: list[Any]) -> str:
    out = "$"
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _err(path: str, message: str, limit: int = 25) -> ManifestError:
    return ManifestError(path=path, message=_cap(message, limit))


def _reserved(name: str, slice_id: str, what: str = "") -> str:
    return f"{what}`{_short(name)}` is reserved for slice {slice_id} and v1 does not accept it."


def _limit_error(doc: Any) -> ManifestError | None:
    """The first cap that ``doc`` breaks, or ``None``. Iterative, so depth cannot overflow."""
    total = 0
    stack: list[tuple[Any, list[Any], int]] = [(doc, [], 0)]
    while stack:
        node, loc, depth = stack.pop()
        if depth > MAX_DEPTH:
            return _err(_path(loc), f"The nesting is deeper than {MAX_DEPTH} levels. Flatten it.")
        if isinstance(node, dict):
            total += 2 + sum(len(str(k)) + 4 for k in node)
            stack.extend((v, [*loc, k], depth + 1) for k, v in node.items())
        elif isinstance(node, list):
            if len(node) > MAX_LIST_ITEMS:
                return _err(
                    _path(loc), f"This list has more than {MAX_LIST_ITEMS} items. Split it."
                )
            total += 2 + len(node)
            stack.extend((v, [*loc, i], depth + 1) for i, v in enumerate(node))
        elif isinstance(node, str):
            if len(node) > MAX_STRING_CHARS:
                return _err(
                    _path(loc),
                    f"This text is longer than {MAX_STRING_CHARS} characters. Shorten it.",
                )
            total += len(node) + 2
        elif node is None or isinstance(node, (bool, int, float)):
            total += 8
        else:
            return _err(_path(loc), "This value is not JSON. Use text, numbers, lists and objects.")
        if total > MAX_DOC_BYTES:
            return _err(
                "$", f"The manifest is larger than {MAX_DOC_BYTES // 1024} KiB. Shorten it."
            )
    return None


def _scan(
    node: Any,
    loc: list[Any],
    out: list[ManifestError],
    key: str = "",
    secret: bool = False,
    depth: int = 0,
) -> None:
    """Walk the raw document for the reserved, style and credential refusals.

    ``_limit_error`` runs first, so the depth is bounded. The guard stays as a backstop.
    ``secret`` is true under a key whose name says it holds a secret.
    """
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            here = [*loc, k]
            lk = str(k).lower()
            if isinstance(k, str) and k in RESERVED_KEYS and key not in _DATA_KEYS:
                out.append(_err(_path(here), _reserved(k, RESERVED_KEYS[k], "Key ")))
                continue
            if lk in _COLOR_KEYS:
                out.append(
                    _err(
                        _path(here),
                        f"Key `{_short(k)}` is a style choice. A manifest names none."
                        " The renderer owns the look.",
                    )
                )
                continue
            sub_key = key if key in _DATA_KEYS else lk
            _scan(v, here, out, sub_key, secret or is_secret_key(k), depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan(v, [*loc, i], out, key, secret, depth + 1)
    elif isinstance(node, str):
        p = _path(loc)
        if _HEX_RE.fullmatch(node.strip()) or _COLOR_FN_RE.search(node):
            out.append(_err(p, "A colour literal is not allowed. The renderer owns every colour."))
        elif key in _STYLE_KEYS and node.strip().lower() in _NAMED_COLORS:
            out.append(
                _err(
                    p,
                    f"A colour name is not allowed. The renderer owns every colour.{_got(node)}",
                )
            )
        if _PX_RE.search(node):
            out.append(_err(p, "A pixel size is not allowed. The renderer owns every size."))
        if (secret and node.strip()) or any(rx.search(node) for _, rx in SECRET_SHAPES):
            out.append(
                _err(p, "This value looks like a credential. Remove it and use a connection.")
            )


def _structure_errors(exc: ValidationError, skip: set[str]) -> list[ManifestError]:
    out: list[ManifestError] = []
    for e in exc.errors():
        loc = list(e["loc"])
        path = _path(loc)
        last = loc[-1] if loc else ""
        typ = e["type"]
        val = e.get("input")
        limit = 25
        if path in skip:
            continue
        if typ == "extra_forbidden":
            msg = f"`{_short(last)}` is not a v1 manifest key. Remove it."
        elif last == "block" and typ == "literal_error":
            if isinstance(val, str) and val in RESERVED_BLOCKS:
                msg = _reserved(val, RESERVED_BLOCKS[val], "Block ")
            else:
                msg = f"Block is unknown. Use {', '.join(BLOCKS[:-1])} or {BLOCKS[-1]}.{_got(val)}"
                limit = 25 + len(BLOCKS)
        elif last == "type" and typ == "literal_error":
            if isinstance(val, str) and val in RESERVED_FIELD_TYPES:
                msg = _reserved(val, RESERVED_FIELD_TYPES[val], "Field type ")
            else:
                msg = (
                    "Field type is unknown. "
                    f"Use {', '.join(FIELD_TYPES[:-1])} or {FIELD_TYPES[-1]}.{_got(val)}"
                )
                limit = 25 + len(FIELD_TYPES)
        elif typ == "missing":
            msg = f"`{_short(last)}` is required here."
        else:
            msg = f"Value is not valid here. {e['msg']}."
        out.append(_err(path, msg, limit))
    return out


def _dupes(items: list[str], base: str, what: str, out: list[ManifestError]) -> None:
    seen: set[str] = set()
    for i, ident in enumerate(items):
        if ident in seen:
            out.append(
                _err(
                    f"{base}[{i}]",
                    f"The {what} `{_short(ident)}` is used twice. Each must be unique.",
                )
            )
        seen.add(ident)


def _undeclared(path: str, kind: str, name: str, where: str, out: list[ManifestError]) -> None:
    out.append(_err(path, f"{kind} `{_short(name)}` is not declared in {where}."))


def _check_uses(
    a: ActionDef, path: str, objects: dict[str, set[str]], catalog: Mapping[str, str] | None
) -> ManifestError | None:
    m = _USES_RE.fullmatch(a.uses)
    if not m:
        return _err(
            path,
            "Not an action id. Use object:<name>.<op>, <app>.<op>, "
            f"integration:<action> or workflow:<uuid>.{_got(a.uses)}",
        )
    if m.group("obj"):
        if m.group("obj") not in objects:
            return _err(
                path, f"Object `{_short(m.group('obj'))}` is not declared in this manifest."
            )
        return None
    if catalog is None:
        return None
    cls = catalog.get(a.uses)
    if cls is None:
        return _err(
            path,
            "That action is not in the catalog. Pick one that the catalog serves." + _got(a.uses),
        )
    if cls not in CLASSES:
        return _err(path, f"That action has the unknown class `{_short(cls)}`.{_got(a.uses)}")
    if cls == "X":
        return _err(
            path,
            "That action is class X. It is on no door and a package cannot use it." + _got(a.uses),
        )
    return None


def _check_view(
    vi: int,
    v: ViewDef,
    objects: dict[str, set[str]],
    action_ids: set[str],
    out: list[ManifestError],
) -> None:
    p = f"$.views[{vi}]"
    src_obj = v.source.object if v.source else None
    obj = v.object or src_obj
    if v.source and v.source.action and v.source.action not in action_ids:
        _undeclared(f"{p}.source.action", "Action", v.source.action, "`actions`", out)
    if v.object and src_obj and v.object != src_obj:
        out.append(
            _err(
                f"{p}.source.object",
                "A view names one object. `object` and `source.object` differ.",
            )
        )
    for where, name in ((f"{p}.object", v.object), (f"{p}.source.object", src_obj)):
        if name is not None and name not in objects:
            _undeclared(where, "Object", name, "this manifest", out)
    if obj is not None and obj in objects and not (v.object and src_obj and v.object != src_obj):
        named: list[tuple[str, str]] = []
        named += [(f"{p}.fields[{i}]", x) for i, x in enumerate(v.fields)]
        named += [(f"{p}.columns[{i}]", x) for i, x in enumerate(v.columns)]
        for attr in ("group_by", "start", "end", "sort"):
            if getattr(v, attr):
                named.append((f"{p}.{attr}", getattr(v, attr)))
        if v.card:
            if v.card.title:
                named.append((f"{p}.card.title", v.card.title))
            named += [(f"{p}.card.meta[{i}]", x) for i, x in enumerate(v.card.meta)]
        for where, name in named:
            if name not in objects[obj]:
                out.append(
                    _err(where, f"Field `{_short(name)}` is not a field of object `{_short(obj)}`.")
                )
    if v.submit and v.submit not in action_ids:
        _undeclared(f"{p}.submit", "Action", v.submit, "`actions`", out)
    for i, name in enumerate(v.actions):
        if name not in action_ids:
            _undeclared(f"{p}.actions[{i}]", "Action", name, "`actions`", out)


def _check_objects(m: Manifest, objects: dict[str, set[str]], out: list[ManifestError]) -> None:
    """The field rules are `studio_fields.validate_field_spec`, the one check
    that the object store runs too. This adds the JSON path and the caps."""
    if len(m.objects) > MAX_OBJECTS_PER_PACKAGE:
        out.append(_err("$.objects", f"A package holds at most {MAX_OBJECTS_PER_PACKAGE} objects."))
    # An org holds this many hot fields in all, so a package with more installs nowhere.
    if sum(f.hot for o in m.objects for f in o.fields) > MAX_HOT_PER_ORG:
        out.append(_err(
            "$.objects", f"A package holds at most {MAX_HOT_PER_ORG} hot fields, the budget of an org."))
    for oi, o in enumerate(m.objects):
        base = f"$.objects[{oi}].fields"
        _dupes([f.key for f in o.fields], base, "field key", out)
        if len(o.fields) > MAX_FIELDS_PER_OBJECT:
            out.append(_err(base, f"An object holds at most {MAX_FIELDS_PER_OBJECT} fields."))
        if sum(f.hot for f in o.fields) > MAX_HOT_PER_OBJECT:
            out.append(_err(base, f"An object holds at most {MAX_HOT_PER_OBJECT} hot fields."))
        for fi, f in enumerate(o.fields):
            for problem in validate_field_spec(f.model_dump(), objects=objects):
                tail = "" if problem.got is None else _got(problem.got)
                out.append(_err(f"{base}[{fi}].{problem.path}", problem.message + tail))


def _check_skill_ref(
    path: str,
    name: str,
    action_ids: set[str],
    objects: dict[str, set[str]],
    catalog: Mapping[str, str] | None,
    out: list[ManifestError],
) -> None:
    """A skill names an action by its id, or by `object:<name>.<op>` of a declared object."""
    mm = _USES_RE.fullmatch(name)
    if not (name in action_ids or (mm and mm.group("obj") in objects)):
        _undeclared(path, "Action", name, "`actions`", out)
    elif catalog is not None and catalog.get(name) == "X":
        out.append(_err(path, "That action is class X. A package cannot use it." + _got(name)))


def _check_set_keys(
    ai: int, a: ActionDef, objects: dict[str, set[str]], out: list[ManifestError]
) -> None:
    """A create or update action sets only fields of its target object."""
    m = _USES_RE.fullmatch(a.uses)
    if not (m and m.group("obj") in objects and m.group("op") in ("create", "update")):
        return
    for key in a.set or {}:
        if key not in objects[m.group("obj")]:
            out.append(
                _err(
                    f"$.actions[{ai}].set.{key}",
                    f"Field `{_short(key)}` is not a field of object `{m.group('obj')}`.",
                )
            )


def _check_skill(
    skill: Skill,
    action_ids: set[str],
    view_ids: set[str],
    objects: dict[str, set[str]],
    catalog: Mapping[str, str] | None,
    out: list[ManifestError],
) -> None:
    for i, name in enumerate(skill.actions):
        _check_skill_ref(f"$.skill.actions[{i}]", name, action_ids, objects, catalog, out)
    for i, ex in enumerate(skill.examples):
        if ex.action is not None:
            _check_skill_ref(
                f"$.skill.examples[{i}].action", ex.action, action_ids, objects, catalog, out
            )
    for i, name in enumerate(skill.cards):
        if name not in view_ids:
            _undeclared(f"$.skill.cards[{i}]", "Card", name, "`views`", out)


def _check_links(
    m: Manifest,
    objects: dict[str, set[str]],
    catalog: Mapping[str, str] | None,
    out: list[ManifestError],
) -> None:
    action_ids = {a.id for a in m.actions}
    view_ids = {v.id for v in m.views}
    for wi, w in enumerate(m.workflows):
        rec = w.trigger.record if w.trigger else None
        if rec is not None and rec not in objects:
            _undeclared(f"$.workflows[{wi}].trigger.record", "Object", rec, "this manifest", out)

    if m.skill:
        _check_skill(m.skill, action_ids, view_ids, objects, catalog, out)
    if m.shell:
        for i, j in enumerate(m.shell.jobs):
            if j.view not in view_ids:
                _undeclared(f"$.shell.jobs[{i}].view", "View", j.view, "`views`", out)
        for i, n in enumerate(m.shell.needs):
            if n.object not in objects:
                _undeclared(f"$.shell.needs[{i}].object", "Object", n.object, "this manifest", out)
        for i, name in enumerate(m.shell.search):
            if name not in objects:
                _undeclared(f"$.shell.search[{i}]", "Object", name, "this manifest", out)


def _semantic_errors(m: Manifest, catalog: Mapping[str, str] | None) -> list[ManifestError]:
    out: list[ManifestError] = []
    objects = {o.name: {f.key for f in o.fields} for o in m.objects}
    action_ids = {a.id for a in m.actions}

    _dupes([o.name for o in m.objects], "$.objects", "object", out)
    _dupes([a.id for a in m.actions], "$.actions", "action id", out)
    _dupes([v.id for v in m.views], "$.views", "view id", out)
    _dupes([w.id for w in m.workflows], "$.workflows", "workflow id", out)
    _check_objects(m, objects, out)
    for ai, a in enumerate(m.actions):
        bad = _check_uses(a, f"$.actions[{ai}].uses", objects, catalog)
        if bad:
            out.append(bad)
        _check_set_keys(ai, a, objects, out)
    for vi, v in enumerate(m.views):
        _check_view(vi, v, objects, action_ids, out)
    _check_links(m, objects, catalog, out)
    return out


def validate_manifest(doc: Any, *, catalog: Mapping[str, str] | None = None) -> list[ManifestError]:
    """Return every refusal for ``doc``. An empty list means the manifest is valid.

    ``catalog`` maps an action id to its class letter. With ``None``, the
    unknown-action and class X checks do not run. An ``object:`` action is
    checked against the manifest's own objects and is never looked up.
    """
    if not isinstance(doc, dict):
        return [_err("$", "A manifest is a JSON object.")]
    too_big = _limit_error(doc)
    if too_big:
        return [too_big]
    scanned: list[ManifestError] = []
    _scan(doc, [], scanned)
    skip = {e.path for e in scanned}
    try:
        model = Manifest.model_validate(doc)
    except ValidationError as exc:
        errors = scanned + _structure_errors(exc, skip)
    else:
        errors = scanned + _semantic_errors(model, catalog)
    seen: set[tuple[str, str]] = set()
    unique: list[ManifestError] = []
    for e in errors:
        if (e.path, e.message) not in seen:
            seen.add((e.path, e.message))
            unique.append(e)
    return sorted(unique, key=lambda e: e.path)
