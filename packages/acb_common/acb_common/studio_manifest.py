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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from acb_common.app_manifests.base import APP_ID_PATTERN, CLASSES, OP_PATTERN, UUID_PATTERN

STUDIO_VERSION = 1

FIELD_TYPES: tuple[str, ...] = (
    "text", "long-text", "number", "money", "date", "datetime", "checkbox",
    "select", "multi-select", "person", "relation", "file", "url", "email",
    "phone", "geo",
)  # fmt: skip

BLOCKS: tuple[str, ...] = (
    "table", "form", "detail", "board", "chart", "stat", "calendar",
    "timeline", "mind-map", "process-flow", "checklist", "map", "markdown",
)  # fmt: skip

OBJECT_OPS: tuple[str, ...] = ("list", "get", "create", "update", "delete")

#: A key that v1 refuses, and the slice that lifts the refusal.
RESERVED_KEYS: dict[str, str] = {
    "rules": "C6",
    "state_machine": "C6",
    "states": "C6",
    "formula": "C7",
    "code": "L1",
}
RESERVED_FIELD_TYPES: dict[str, str] = {"formula": "C7", "rollup": "C7", "roll-up": "C7"}
RESERVED_BLOCKS: dict[str, str] = {"code": "L1", "custom": "L1", "custom-ui": "L1"}

#: Subtrees that hold member data, not structure. The reserved-key scan skips them.
_DATA_KEYS = frozenset({"set", "where", "when", "args"})

_NAME = r"[a-z][a-z0-9_]{0,47}"
_DASHED = r"[a-z][a-z0-9_-]{0,47}"
_RELATION_TO = re.compile(rf"^{APP_ID_PATTERN}\.{_NAME}$")
_OPS = "|".join(OBJECT_OPS)
_USES_RE = re.compile(
    rf"^(?:object:(?P<obj>{_NAME})\.(?P<op>{_OPS})"
    rf"|integration:(?P<integ>{_NAME}(?:\.{_NAME})?)"
    rf"|workflow:(?P<wf>{UUID_PATTERN})"
    rf"|(?P<app>{APP_ID_PATTERN})\.(?P<appop>{OP_PATTERN}))$"
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
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_COLOR_FN_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", re.IGNORECASE)
_PX_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?px\b", re.IGNORECASE)

#: The shapes of a credential in a string value. ``test_studio_manifest.py`` tests each.
_CREDENTIAL_RES = tuple(
    re.compile(p)
    for p in (
        r"\bsk-[A-Za-z0-9_-]{16,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}",
        r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}",
    )
)


class ManifestError(BaseModel):
    """One refusal: a JSON path and one sentence a model can act on."""

    path: str
    message: str


# --- the schema ------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldDef(_Strict):
    key: str = Field(pattern=rf"^{_NAME}$")
    type: Literal[
        "text", "long-text", "number", "money", "date", "datetime", "checkbox",
        "select", "multi-select", "person", "relation", "file", "url", "email",
        "phone", "geo",
    ]  # fmt: skip
    label: str | None = None
    required: bool = False
    many: bool = False
    options: list[str] | None = None
    to: str | None = None


class ObjectDef(_Strict):
    name: str = Field(pattern=rf"^{_NAME}$")
    label: str | None = None
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
        "timeline", "mind-map", "process-flow", "checklist", "map", "markdown",
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


def _cap(text: str, limit: int = 25) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[: limit - 1]) + " ..."


def _path(loc: list[Any]) -> str:
    out = "$"
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _err(path: str, message: str) -> ManifestError:
    return ManifestError(path=path, message=_cap(message))


def _reserved(name: str, slice_id: str, what: str = "") -> str:
    return f"{what}`{name}` is reserved for slice {slice_id} and v1 does not accept it."


def _scan(node: Any, loc: list[Any], out: list[ManifestError], key: str = "") -> None:
    """Walk the raw document for the reserved, style, size and credential refusals."""
    if isinstance(node, dict):
        for k, v in node.items():
            here = [*loc, k]
            lk = str(k).lower()
            if k in RESERVED_KEYS and key not in _DATA_KEYS:
                out.append(_err(_path(here), _reserved(k, RESERVED_KEYS[k], "Key ")))
                continue
            if lk in _COLOR_KEYS:
                out.append(
                    _err(
                        _path(here),
                        f"Key `{k}` is a style choice. A manifest names none. The renderer owns the look.",
                    )
                )
                continue
            _scan(v, here, out, key if key in _DATA_KEYS else lk)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan(v, [*loc, i], out, key)
    elif isinstance(node, str):
        p = _path(loc)
        if _HEX_RE.match(node.strip()) or _COLOR_FN_RE.search(node):
            out.append(_err(p, "A colour literal is not allowed. The renderer owns every colour."))
        elif key in _STYLE_KEYS and node.strip().lower() in _NAMED_COLORS:
            out.append(
                _err(p, f"The colour name `{node}` is not allowed. The renderer owns every colour.")
            )
        if _PX_RE.search(node):
            out.append(_err(p, "A pixel size is not allowed. The renderer owns every size."))
        if any(r.search(node) for r in _CREDENTIAL_RES):
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
        if path in skip:
            continue
        if typ == "extra_forbidden":
            msg = f"`{last}` is not a v1 manifest key. Remove it."
        elif last == "block" and typ == "literal_error":
            if val in RESERVED_BLOCKS:
                msg = _reserved(str(val), RESERVED_BLOCKS[str(val)], "Block ")
            else:
                msg = f"Block `{val}` is unknown. Use {', '.join(BLOCKS[:-1])} or {BLOCKS[-1]}."
        elif last == "type" and typ == "literal_error":
            if val in RESERVED_FIELD_TYPES:
                msg = _reserved(str(val), RESERVED_FIELD_TYPES[str(val)], "Field type ")
            else:
                msg = f"Field type `{val}` is unknown. Use one of the {len(FIELD_TYPES)} v1 types."
        elif typ == "missing":
            msg = f"`{last}` is required here."
        else:
            msg = f"Value is not valid here. {e['msg']}"
        out.append(_err(path, msg))
    return out


def _dupes(items: list[str], base: str, what: str, out: list[ManifestError]) -> None:
    seen: set[str] = set()
    for i, ident in enumerate(items):
        if ident in seen:
            out.append(
                _err(f"{base}[{i}]", f"The {what} `{ident}` is used twice. Each must be unique.")
            )
        seen.add(ident)


def _undeclared(path: str, kind: str, name: str, where: str, out: list[ManifestError]) -> None:
    out.append(_err(path, f"{kind} `{name}` is not declared in {where}."))


def _check_uses(
    a: ActionDef, path: str, objects: dict[str, set[str]], catalog: Mapping[str, str] | None
) -> ManifestError | None:
    m = _USES_RE.match(a.uses)
    if not m:
        return _err(
            path,
            f"`{a.uses}` is not an action id. Use object:<name>.<op>, <app>.<op>, "
            "integration:<action> or workflow:<uuid>.",
        )
    if m.group("obj"):
        if m.group("obj") not in objects:
            return _err(path, f"Object `{m.group('obj')}` is not declared in this manifest.")
        return None
    if catalog is None:
        return None
    cls = catalog.get(a.uses)
    if cls is None:
        return _err(
            path, f"Action `{a.uses}` is not in the catalog. Pick one that the catalog serves."
        )
    if cls not in CLASSES:
        return _err(path, f"Action `{a.uses}` has the unknown class `{cls}`.")
    if cls == "X":
        return _err(
            path, f"Action `{a.uses}` is class X. It is on no door and a package cannot use it."
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
    obj = v.object or (v.source.object if v.source else None)
    if v.source and v.source.action and v.source.action not in action_ids:
        _undeclared(f"{p}.source.action", "Action", v.source.action, "`actions`", out)
    if obj is not None and obj not in objects:
        where = f"{p}.object" if v.object else f"{p}.source.object"
        _undeclared(where, "Object", obj, "this manifest", out)
    elif obj is not None:
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
                out.append(_err(where, f"Field `{name}` is not a field of object `{obj}`."))
    if v.submit and v.submit not in action_ids:
        _undeclared(f"{p}.submit", "Action", v.submit, "`actions`", out)
    for i, name in enumerate(v.actions):
        if name not in action_ids:
            _undeclared(f"{p}.actions[{i}]", "Action", name, "`actions`", out)


def _check_objects(m: Manifest, objects: dict[str, set[str]], out: list[ManifestError]) -> None:
    for oi, o in enumerate(m.objects):
        _dupes([f.key for f in o.fields], f"$.objects[{oi}].fields", "field key", out)
        for fi, f in enumerate(o.fields):
            p = f"$.objects[{oi}].fields[{fi}]"
            if f.type in ("select", "multi-select") and not f.options:
                out.append(_err(f"{p}.options", "A select field needs at least one option."))
            if f.type == "relation":
                if not f.to:
                    out.append(_err(f"{p}.to", "A relation field needs a `to` target."))
                elif f.to not in objects and not _RELATION_TO.match(f.to):
                    out.append(
                        _err(
                            f"{p}.to",
                            f"`{f.to}` is not a declared object or an <app>.<object> target.",
                        )
                    )


def _check_links(m: Manifest, objects: dict[str, set[str]], out: list[ManifestError]) -> None:
    action_ids = {a.id for a in m.actions}
    view_ids = {v.id for v in m.views}
    for wi, w in enumerate(m.workflows):
        rec = w.trigger.record if w.trigger else None
        if rec is not None and rec not in objects:
            _undeclared(f"$.workflows[{wi}].trigger.record", "Object", rec, "this manifest", out)

    if m.skill:
        for i, name in enumerate(m.skill.actions):
            mm = _USES_RE.match(name)
            if name in action_ids or (mm and mm.group("obj") in objects):
                continue
            _undeclared(f"$.skill.actions[{i}]", "Action", name, "`actions`", out)
        for i, name in enumerate(m.skill.cards):
            if name not in view_ids:
                _undeclared(f"$.skill.cards[{i}]", "Card", name, "`views`", out)

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
    for vi, v in enumerate(m.views):
        _check_view(vi, v, objects, action_ids, out)
    _check_links(m, objects, out)
    return out


def validate_manifest(doc: Any, *, catalog: Mapping[str, str] | None = None) -> list[ManifestError]:
    """Return every refusal for ``doc``. An empty list means the manifest is valid.

    ``catalog`` maps an action id to its class letter. With ``None``, the
    unknown-action and class X checks do not run. An ``object:`` action is
    checked against the manifest's own objects and is never looked up.
    """
    if not isinstance(doc, dict):
        return [_err("$", "A manifest is a JSON object.")]
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
