"""The install consent of an app, computed from its manifest (WS-STU J6, task 1426).

Spec: ``project-docs/specs/ai_studio.md`` §14 J6 and §19.2. This module is the
BACKEND of the consent screen: a PURE function that reads a manifest (a dict) and
answers what the app reads, writes and sends, who can see it, its runtime kind
and its connections. The screen (J6b, lane-ui) draws that answer and holds no
list of its own, so a new op is never missing from the screen.
:func:`widens` compares two manifests and names each thing that the second one
adds. A version that widens asks the installer again.

**What it reads.** Only the manifest. It opens no connection, calls no model and
reads no record. The ``run_as`` member lives on the install row, not in the
manifest, so the caller passes it to :func:`consent`.

**What each part means.**

* ``reads`` and ``writes``: the object ops, grouped by object. A list, a get, an
  aggregate and a tree are reads. A create, an update and a delete are writes.
  An op comes from an ``object:<name>.<op>`` action, or from a skill or a view
  that names one with no action. A view, a shell need, a shell search and a
  workflow trigger that name an object read it, so each adds ``list``.
* ``links``: the relation targets that are not an object of this app, such as
  ``crm.deal``. Each one reads the records of another app.
* ``sends``: what leaves the app: the integration actions, the MCP tools, the
  workflows and logic modules that an action runs, the door ops of other apps
  and the workflows of the app itself. ``unknown`` holds each ``uses`` that the
  A2 grammar (``studio_manifest.parse_uses``) does not read.
* ``connections``: the names in ``connections``, split into providers and MCP
  servers. ``needs`` gives the account (``as``) and the scopes of each name.
  ``unknown`` holds each item that has no name, an invalid ``as``, invalid
  scopes or an extra key.
* ``viewers``: the roles, the objects with an ``access`` list (``restricted``)
  and those with none (``open``: every member who reaches the app reads them).
  ``access`` holds each grant list.
* ``knowledge``: the knowledge that the skill of the app reads.
* ``hooks``: ``extends`` and the items of ``contributes``, ``slots``,
  ``needs`` and ``overrides`` (the package host, PKG-5a).
* ``runtime``: the kind and the pinned packages.
* ``sensitive_fields``: the fields that carry ``sensitive: true``. The restricted
  field type of C9 is not built, so this list is empty today. A manifest that
  carries the flag is already counted, and C9 gives it its meaning.
* ``shapes``: the parts that the consent does not rank, so each change widens:
  the app ``kind``, ``id`` and ``team``, the rules of each object by kind
  (:data:`RULE_REACH`), the ``set`` of each action, the reach keys of each
  field, the record tabs, the source of each view and the shell needs.
* ``unclassified``: each top-level key that :data:`TOP_LEVEL_PARTS` does not
  name. A change to one widens.

**Fail closed.** A ``uses``, a connection item, a scope list or a top-level key
that this module cannot read shows up as a widening. It never drops out.
:data:`FIELD_PARTS` names the consent part of each field of each schema model,
and :data:`NO_REACH` gives the reason for each field that has no part. The
fence fails when the schema gains a field that the tables do not name.

Fence: ``tests/unit/test_studio_consent.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from acb_common.studio_manifest import (
    CONNECTION_AS,
    OBJECT_OP_CLASSES,
    connection_as,
    parse_uses,
)

#: The runtime kinds, from the least to the most reach (``ai_studio.md`` §19.3).
RUNTIME_ORDER = ("none", "browser", "sandbox", "packages")

#: The ``UsesRef.source`` values that :func:`consent` sorts. The fence holds
#: this equal to the ``Literal`` of ``studio_manifest.UsesRef``.
USES_SOURCES = frozenset({"object", "manifest", "integration", "workflow", "logic", "mcp"})

#: Why each field with the part ``none`` can never give the app more reach.
#: The fence pins this exact list, so a field moves here only with a reason.
NO_REACH: dict[str, str] = {
    "Manifest.studio": "the schema version, always 1",
    "Manifest.name": "display text",
    "Manifest.purpose": "display text",
    "Manifest.pages": "places views, and each view is counted under views",
    "Manifest.nav": "places pages, and a row count shows the rows of a counted view",
    "ObjectDef.label": "display text",
    "FieldDef.label": "display text",
    "FieldDef.required": "a check on a value that a member types, inside the app",
    "FieldDef.options": "the values of a select, inside the app",
    "FieldDef.min": "a check on a value, inside the app",
    "FieldDef.max": "a check on a value, inside the app",
    "FieldDef.units": "the units of a value, inside the app",
    "FieldDef.normal": "a flag on a value, computed on read",
    "FieldDef.critical": "a flag on a value, computed on read",
    "FieldDef.conversions": "unit factors of a value, inside the app",
    "FieldDef.format": "the format of a barcode value",
    "FieldDef.prefix": "the text of an autonumber",
    "FieldDef.pad": "the width of an autonumber",
    "FieldDef.reset": "when an autonumber starts again",
    "ActionDef.id": "a name, and consent counts the uses and the set of each id",
    "ActionDef.title": "display text",
    "ViewDef.id": "a name, and consent counts the source of each id",
    "ViewDef.block": "how a read draws, and a form writes only through a counted action",
    "ViewDef.title": "display text",
    "ViewDef.fields": "the fields that a counted read draws, after the access rules",
    "ViewDef.group_by": "how a counted read draws",
    "ViewDef.card": "how a counted read draws",
    "ViewDef.start": "how a counted read draws",
    "ViewDef.end": "how a counted read draws",
    "ViewDef.columns": "the fields that a counted read draws, after the access rules",
    "ViewDef.sort": "the order of a counted read",
    "ViewDef.text": "display text",
    "Card.title": "a field of a counted read",
    "Card.meta": "fields of a counted read",
    "PageDef.id": "layout", "PageDef.title": "layout", "PageDef.cols": "layout",
    "PageDef.tabs": "layout", "PageDef.cells": "layout", "PageDef.side": "layout",
    "PageDef.layout": "layout", "PageDef.regions": "layout",
    "TabDef.id": "layout", "TabDef.title": "layout", "TabDef.cells": "layout",
    "CellDef.view": "layout", "CellDef.span": "layout", "SideDef.view": "layout",
    "NavSection.title": "layout", "NavSection.rows": "layout",
    "NavRow.page": "layout", "NavRow.icon": "layout", "NavRow.label": "layout",
    "NavRow.count": "a count of the rows of a counted view",
    "Skill.description": "text to the model, and the counted actions bound what it does",
    "Skill.instructions": "text to the model, and the counted actions bound what it does",
    "Skill.cards": "names views, and each view is counted under views",
    "SkillExample.prompt": "text to the model",
    "SkillExample.args": "an example for the model, and the counted action bounds it",
    "Shell.jobs": "opens a view, and each view is counted under views",
    "ShellJob.verb": "display text",
    "ShellJob.view": "names a counted view",
}

#: The part of the consent that reads each field of each schema model of
#: ``studio_manifest``. ``none`` is a field of :data:`NO_REACH`. The fence
#: walks the models from ``Manifest`` and fails on a field that this table
#: does not name, so a new form of the manifest cannot skip the consent.
FIELD_PARTS: dict[str, dict[str, str]] = {
    "Manifest": {
        "studio": "none", "kind": "app", "id": "app", "name": "none",
        "purpose": "none", "team": "app", "connections": "connections",
        "roles": "viewers", "objects": "viewers", "actions": "reads writes sends",
        "views": "reads sends", "pages": "none", "nav": "none",
        "workflows": "reads sends", "skill": "reads writes sends knowledge",
        "shell": "reads", "contributes": "hooks", "extends": "hooks",
        "slots": "hooks", "needs": "hooks", "overrides": "hooks", "runtime": "runtime",
    },
    "ConnectionNeed": {"name": "connections", "as_": "connections", "scopes": "connections"},
    "ObjectDef": {
        "name": "viewers", "label": "none", "fields": "fields links sensitive_fields",
        "rules": "rules", "access": "viewers", "record": "records",
    },
    "FieldDef": {
        "key": "fields", "type": "fields", "label": "none", "required": "none",
        "many": "fields", "options": "none", "to": "fields links", "hot": "fields",
        "min": "none", "max": "none", "units": "none", "normal": "none",
        "critical": "none", "conversions": "none", "format": "none",
        "lines": "fields", "prefix": "none", "pad": "none", "reset": "none",
    },
    "RecordDef": {"tabs": "records"},
    "ActionDef": {"id": "none", "uses": "reads writes sends", "title": "none",
                  "set": "action_sets"},
    "ViewDef": {
        "id": "none", "block": "none", "title": "none", "source": "reads sends view_sources",
        "object": "reads", "fields": "none", "submit": "sends", "group_by": "none",
        "card": "none", "start": "none", "end": "none", "columns": "none",
        "sort": "none", "actions": "sends", "text": "none",
    },
    "Source": {"object": "reads view_sources", "action": "sends view_sources",
               "args": "view_sources"},
    "Card": {"title": "none", "meta": "none"},
    "PageDef": {
        "id": "none", "title": "none", "cols": "none", "tabs": "none",
        "cells": "none", "side": "none", "layout": "none", "regions": "none",
    },
    "TabDef": {"id": "none", "title": "none", "cells": "none"},
    "CellDef": {"view": "none", "span": "none"},
    "SideDef": {"view": "none"},
    "NavSection": {"title": "none", "rows": "none"},
    "NavRow": {"page": "none", "icon": "none", "label": "none", "count": "none"},
    "WorkflowDef": {"id": "sends", "trigger": "reads sends", "graph": "sends"},
    "Trigger": {"record": "reads", "when": "sends", "schedule": "sends"},
    "Skill": {
        "description": "none", "instructions": "none", "actions": "reads writes sends",
        "cards": "none", "knowledge": "knowledge", "examples": "reads writes sends",
    },
    "SkillExample": {"prompt": "none", "action": "reads writes sends", "args": "none"},
    "Shell": {"jobs": "none", "needs": "reads shell_needs", "search": "reads"},
    "ShellJob": {"verb": "none", "view": "none"},
    "ShellNeed": {"object": "reads shell_needs", "where": "shell_needs"},
    "RuntimeDef": {"kind": "runtime", "packages": "runtime"},
}

#: What each rule kind of ``studio_rules.RULE_KINDS`` does, in the line that
#: :func:`widens` writes. Each kind widens on any change, an add and a drop:
#: a guard that goes away lets more writes through, and a timer, a formula and
#: a roll-up act with no member. The fence holds the keys equal to RULE_KINDS.
RULE_REACH: dict[str, str] = {
    "range": "a guard on the writes",
    "unique": "a guard on the writes",
    "group_sum_min": "a guard on the writes",
    "state_machine": "a guard on the moves of a field",
    "formula": "writes a computed field",
    "rollup": "reads the lines of another object",
    "hierarchy": "a guard on the tree",
    "timer": "runs with no member",
}
#: The bucket of a rule whose kind is not in :data:`RULE_REACH`.
UNKNOWN_RULE = "?"
#: The field keys that decide reach. The keys of :data:`NO_REACH` are the rest.
_FIELD_REACH_KEYS = ("type", "to", "lines", "hot", "many")
_APP_KEYS = ("kind", "id", "team")

#: The top-level keys that :func:`consent` reads. Each other key lands in
#: ``unclassified``, and a change to it widens.
TOP_LEVEL_PARTS = frozenset(FIELD_PARTS["Manifest"])
_HOOK_LISTS = ("slots", "needs", "overrides")
_NEED_KEYS = frozenset({"name", "as", "scopes"})


def _canon(value: Any) -> str:
    """One stable text for a value of any shape, so that two can be compared."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)


def _list(manifest: Any, key: str) -> list[Any]:
    found = manifest.get(key) if isinstance(manifest, Mapping) else None
    # No cut: a cut would hide an action past the cap, and a version could widen
    # there with no new consent. The manifest size cap (``MAX_DOC_BYTES``) bounds the work.
    return list(found) if isinstance(found, list) else []


def _map(manifest: Any, key: str) -> Mapping[str, Any]:
    found = manifest.get(key) if isinstance(manifest, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def _sorted(items: set[str]) -> list[str]:
    return sorted(items)


class _Sink:
    """The sets that :func:`consent` fills from each place that names an action."""

    def __init__(self) -> None:
        self.reads: dict[str, set[str]] = {}
        self.writes: dict[str, set[str]] = {}
        self.sends: dict[str, set[str]] = {
            k: set()
            for k in ("integrations", "mcp_tools", "workflows", "logic", "door_ops", "unknown")
        }

    def read(self, obj: Any) -> None:
        if isinstance(obj, str) and obj:
            self.reads.setdefault(obj, set()).add("list")

    def uses(self, uses: Any) -> None:
        """Sort one ``uses`` value. A value that the A2 grammar does not read
        goes to ``unknown``, so it still shows up (fail closed)."""
        ref = parse_uses(uses)
        if ref is None or not isinstance(uses, str):
            self.sends["unknown"].add(uses if isinstance(uses, str) else _canon(uses))
            return
        if ref.source == "object" and ref.object and ref.op:
            # An op with no class is a write: the wider of the two.
            bucket = self.reads if OBJECT_OP_CLASSES.get(ref.op) == "A" else self.writes
            bucket.setdefault(ref.object, set()).add(ref.op)
        elif ref.source == "integration":
            self.sends["integrations"].add(uses.split(":", 1)[1])
        elif ref.source == "workflow":
            self.sends["workflows"].add(uses.split(":", 1)[1].lower())
        elif ref.source == "logic" and ref.logic:
            self.sends["logic"].add(ref.logic)
        elif ref.source == "mcp" and ref.server and ref.tool:
            self.sends["mcp_tools"].add(uses)
        elif ref.source == "manifest" and ref.app and ref.op:
            self.sends["door_ops"].add(uses)
        else:
            self.sends["unknown"].add(uses)

    def ref(self, name: Any, action_ids: set[str]) -> None:
        """A skill or a view names an action by its id, or by a ``uses`` value.
        An id is counted through ``actions``. Each other name is sorted here."""
        if name is None or (isinstance(name, str) and name in action_ids):
            return
        self.uses(name)


def _connection_need(raw: Any) -> dict[str, Any] | None:
    """``{name, as, scopes}`` of one item of ``connections``, or None when this
    module cannot read the item. ``as`` comes from :func:`connection_as`, the
    one seam, so a plain name gets the default account of the connector list."""
    if isinstance(raw, str):
        return {"name": raw, "as": connection_as(raw), "scopes": None}
    if not isinstance(raw, Mapping) or set(raw) - _NEED_KEYS:
        return None
    name, declared, scopes = raw.get("name"), raw.get("as"), raw.get("scopes")
    if not isinstance(name, str) or (declared is not None and declared not in CONNECTION_AS):
        return None
    if scopes is not None and not (
        isinstance(scopes, list) and all(isinstance(s, str) for s in scopes)
    ):
        return None
    return {
        "name": name,
        "as": connection_as(name, declared),
        "scopes": None if scopes is None else sorted(set(scopes)),
    }


def _connections(m: Mapping[str, Any]) -> dict[str, Any]:
    needs: list[dict[str, Any]] = []
    unknown: set[str] = set()
    for raw in _list(m, "connections"):
        need = _connection_need(raw)
        if need is None:
            unknown.add(_canon(raw))
        else:
            needs.append(need)
    needs.sort(key=lambda n: (n["name"], n["as"], _canon(n["scopes"])))
    names = {n["name"] for n in needs}
    return {
        "providers": sorted(c for c in names if not c.startswith("mcp:")),
        "mcp_servers": sorted(c for c in names if c.startswith("mcp:")),
        "needs": needs,
        "unknown": _sorted(unknown),
    }


def _hooks(m: Mapping[str, Any]) -> dict[str, Any]:
    raw_contributes = m.get("contributes") if isinstance(m, Mapping) else None
    contributes = (
        {
            f"{point}:{_canon(item)}"
            for point, items in raw_contributes.items()
            for item in (items if isinstance(items, list) else [items])
        }
        if isinstance(raw_contributes, Mapping)
        # A value that is not a map of points still counts, as one item.
        else ({f"?:{_canon(raw_contributes)}"} if raw_contributes is not None else set())
    )
    extends = m.get("extends")
    out: dict[str, Any] = {
        "extends": None if extends is None else (
            extends if isinstance(extends, str) else _canon(extends)
        ),
        "contributes": _sorted(contributes),
    }
    for key in _HOOK_LISTS:
        value = m.get(key)
        items = value if isinstance(value, list) else ([] if value is None else [value])
        out[key] = sorted({_canon(item) for item in items})
    return out


def _fill(m: Mapping[str, Any], sink: _Sink) -> set[str]:
    """Sort each action, and each skill, view, shell and workflow part that
    names an action or an object. Returns the app workflows."""
    actions = _list(m, "actions")
    action_ids = {
        a["id"] for a in actions if isinstance(a, Mapping) and isinstance(a.get("id"), str)
    }
    for action in actions:
        uses = action.get("uses") if isinstance(action, Mapping) else None
        sink.uses(uses if isinstance(uses, str) else action)
    for view in _list(m, "views"):
        if not isinstance(view, Mapping):
            continue
        sink.read(view.get("object"))
        source = view.get("source") if isinstance(view.get("source"), Mapping) else {}
        sink.read(source.get("object"))
        for name in (source.get("action"), view.get("submit"), *_list(view, "actions")):
            sink.ref(name, action_ids)
    skill = _map(m, "skill")
    for name in _list(skill, "actions"):
        sink.ref(name, action_ids)
    for example in _list(skill, "examples"):
        if isinstance(example, Mapping):
            sink.ref(example.get("action"), action_ids)
    shell = _map(m, "shell")
    for need in _list(shell, "needs"):
        sink.read(need.get("object") if isinstance(need, Mapping) else None)
    for name in _list(shell, "search"):
        sink.read(name)
    app_workflows: set[str] = set()
    for wf in _list(m, "workflows"):
        trigger = wf.get("trigger") if isinstance(wf, Mapping) else None
        sink.read(trigger.get("record") if isinstance(trigger, Mapping) else None)
        app_workflows.add(_canon(wf))
    return app_workflows


def _objects(m: Mapping[str, Any]) -> dict[str, Any]:
    """The viewers, the links and the sensitive fields of the objects."""
    objects = [o for o in _list(m, "objects")
               if isinstance(o, Mapping) and isinstance(o.get("name"), str)]
    own = {o["name"] for o in objects}
    restricted: set[str] = set()
    open_objects: set[str] = set()
    access: dict[str, str] = {}
    links: set[str] = set()
    sensitive: list[dict[str, str]] = []
    for obj in objects:
        name = obj["name"]
        (restricted if obj.get("access") else open_objects).add(name)
        if obj.get("access"):
            access[name] = _canon(obj["access"])
        for fld in _list(obj, "fields"):
            if not isinstance(fld, Mapping):
                continue
            to = fld.get("to")
            if to is not None and to not in own:
                links.add(to if isinstance(to, str) else _canon(to))
            if fld.get("sensitive") is True and isinstance(fld.get("key"), str):
                sensitive.append({"object": name, "field": fld["key"]})
    return {
        "links": _sorted(links),
        "viewers": {
            "roles": sorted(r for r in _list(m, "roles") if isinstance(r, str)),
            "restricted": _sorted(restricted),
            "open": _sorted(open_objects),
            "access": dict(sorted(access.items())),
        },
        "sensitive_fields": sorted(sensitive, key=lambda s: (s["object"], s["field"])),
    }


def _rule_kind(rule: Any) -> str:
    kind = rule.get("kind") if isinstance(rule, Mapping) else None
    return kind if isinstance(kind, str) and kind in RULE_REACH else UNKNOWN_RULE


def _shapes(m: Mapping[str, Any]) -> dict[str, Any]:
    """The parts that widen on any change, because the consent does not rank
    them: the app keys, the rules, the ``set`` of each action, the reach keys
    of each field, the record tabs, the view sources and the shell needs."""
    rules: dict[str, dict[str, list[str]]] = {}
    fields: dict[str, str] = {}
    records: dict[str, str] = {}
    for obj in _list(m, "objects"):
        if not isinstance(obj, Mapping) or not isinstance(obj.get("name"), str):
            continue
        name = obj["name"]
        by_kind: dict[str, set[str]] = {}
        raw_rules = obj.get("rules")
        for rule in raw_rules if isinstance(raw_rules, list) else (
            [] if raw_rules is None else [raw_rules]
        ):
            by_kind.setdefault(_rule_kind(rule), set()).add(_canon(rule))
        rules[name] = {k: _sorted(v) for k, v in sorted(by_kind.items())}
        for fld in _list(obj, "fields"):
            key = fld.get("key") if isinstance(fld, Mapping) else None
            label = f"{name}.{key}" if isinstance(key, str) else f"{name}.{_canon(fld)}"
            reach = {k: fld.get(k) for k in _FIELD_REACH_KEYS} if isinstance(fld, Mapping) else fld
            fields[label] = _canon(reach)
        if obj.get("record") is not None:
            records[name] = _canon(obj.get("record"))
    action_sets: dict[str, str] = {}
    for action in _list(m, "actions"):
        if isinstance(action, Mapping) and isinstance(action.get("id"), str):
            # The first action of an id wins, as at the door.
            action_sets.setdefault(action["id"], _canon(action.get("set")))
    view_sources: dict[str, str] = {}
    for view in _list(m, "views"):
        if isinstance(view, Mapping) and isinstance(view.get("id"), str):
            view_sources.setdefault(view["id"], _canon(view.get("source")))
    return {
        "app": {k: _canon(m.get(k)) for k in _APP_KEYS},
        "rules": rules,
        "action_sets": action_sets,
        "fields": dict(sorted(fields.items())),
        "records": dict(sorted(records.items())),
        "view_sources": dict(sorted(view_sources.items())),
        "shell_needs": sorted({_canon(n) for n in _list(_map(m, "shell"), "needs")}),
    }


def consent(manifest: Any, *, run_as: str | None = None) -> dict[str, Any]:
    """What the installer agrees to. Never raises: a value that is not a
    manifest gives an empty consent. A part that this module cannot read
    lands in an ``unknown`` or ``unclassified`` list, so it still widens."""
    m: Mapping[str, Any] = manifest if isinstance(manifest, Mapping) else {}
    sink = _Sink()
    app_workflows = _fill(m, sink)
    objects = _objects(m)
    rt = m.get("runtime") if isinstance(m.get("runtime"), Mapping) else {}
    kind = rt.get("kind") if isinstance(rt.get("kind"), str) and rt.get("kind") else "none"
    packages = rt.get("packages") if isinstance(rt.get("packages"), Mapping) else {}
    sends = {k: _sorted(v) for k, v in sink.sends.items()}
    sends["app_workflows"] = _sorted(app_workflows)
    return {
        "reads": [{"object": o, "ops": _sorted(v)} for o, v in sorted(sink.reads.items())],
        "writes": [{"object": o, "ops": _sorted(v)} for o, v in sorted(sink.writes.items())],
        "links": objects["links"],
        "sends": sends,
        "connections": _connections(m),
        "viewers": objects["viewers"],
        "knowledge": sorted({k if isinstance(k, str) else _canon(k)
                             for k in _list(_map(m, "skill"), "knowledge")}),
        "hooks": _hooks(m),
        "runtime": {
            "kind": kind,
            "packages": dict(sorted((str(k), str(v)) for k, v in packages.items())),
        },
        "sensitive_fields": objects["sensitive_fields"],
        "unclassified": {
            str(k): _canon(v) for k, v in sorted(m.items(), key=lambda kv: str(kv[0]))
            if k not in TOP_LEVEL_PARTS
        },
        "shapes": _shapes(m),
        "run_as": run_as,
    }


def _rank(kind: str) -> int:
    return RUNTIME_ORDER.index(kind) if kind in RUNTIME_ORDER else len(RUNTIME_ORDER)


def _needs_by_name(c: dict[str, Any]) -> dict[str, tuple[set[str], set[str], bool]]:
    """Name to (accounts, scopes, a need with no scope list). A name twice
    (the validator refuses it) joins both, so neither copy hides."""
    out: dict[str, tuple[set[str], set[str], bool]] = {}
    for n in c["needs"]:
        accounts, scopes, undeclared = out.get(n["name"], (set(), set(), False))
        accounts.add(n["as"])
        scopes.update(n["scopes"] or ())
        out[n["name"]] = (accounts, scopes, undeclared or n["scopes"] is None)
    return out


def _connection_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    """A new name, a new account, a new scope or a dropped scope list widens.
    Both changes of ``as`` widen: ``org`` shares one account with every
    viewer, and ``member`` reaches the own account of each viewer. A need with
    no ``scopes`` declares none, so a scope after none widens. A scope list
    that goes away widens too, because the connect step can then ask for any
    scope."""
    before, after = _needs_by_name(a), _needs_by_name(b)
    for name, (accounts, scopes, undeclared) in sorted(after.items()):
        if name not in before:
            what = "MCP server" if name.startswith("mcp:") else "connection"
            line = f"Needs the {what} `{name}` with the {' and '.join(sorted(accounts))} account"
            if scopes:
                line += f", for the scopes {', '.join(sorted(scopes))}"
            out.append(line + ".")
            continue
        old_accounts, old_scopes, old_undeclared = before[name]
        for acct in sorted(accounts - old_accounts):
            out.append(f"Uses the {acct} account for `{name}`.")
        added = scopes - old_scopes
        if added:
            out.append(f"Asks `{name}` for the scopes {', '.join(sorted(added))}.")
        if undeclared and not old_undeclared:
            out.append(f"Drops the scope list of `{name}`, so it can ask for any scope.")
    for item in sorted(set(b["unknown"]) - set(a["unknown"])):
        out.append(f"Has a connection item that the consent cannot read: `{item}`.")


def _hook_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    if b["extends"] is not None and b["extends"] != a["extends"]:
        out.append(f"Extends the package `{b['extends']}`.")
    for key in ("contributes", *_HOOK_LISTS):
        if set(b[key]) - set(a[key]):
            out.append(f"Adds or changes an item of `{key}`.")


def _send_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    for item in sorted(set(b["links"]) - set(a["links"])):
        out.append(f"Links to the records of `{item}`.")
    for key, label in (
        ("integrations", "Sends through"),
        ("mcp_tools", "Calls the MCP tool"),
        ("workflows", "Starts workflow"),
        ("logic", "Runs logic"),
        ("door_ops", "Calls the app action"),
        ("unknown", "Uses an action that the consent cannot classify:"),
    ):
        for item in sorted(set(b["sends"][key]) - set(a["sends"][key])):
            out.append(f"{label} `{item}`.")
    old_wf = set(a["sends"]["app_workflows"])
    for item in b["sends"]["app_workflows"]:
        if item not in old_wf:
            wf = json.loads(item)
            wid = wf.get("id") if isinstance(wf, dict) else None
            out.append(f"Adds or changes the app workflow `{wid}`.")


def _viewer_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    # An object that had an access list and has none now is open to every viewer.
    for name in sorted(set(a["restricted"]) & set(b["open"])):
        out.append(f"Opens `{name}` to every member who reaches the app.")
    # A changed grant list can give a role more. The consent does not rank
    # grants, so each change asks again (fail closed).
    for name, grants in b["access"].items():
        old_grants = a["access"].get(name)
        if old_grants is not None and old_grants != grants:
            out.append(f"Changes who can see `{name}`.")
    for item in sorted(set(b["roles"]) - set(a["roles"])):
        out.append(f"Adds the role `{item}`.")


def _runtime_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    if _rank(b["kind"]) > _rank(a["kind"]):
        out.append(f"Runs member code: the runtime goes from `{a['kind']}` to `{b['kind']}`.")
    for pkg, ver in b["packages"].items():
        if a["packages"].get(pkg) != ver:
            out.append(f"Changes the package `{pkg}` to {ver}.")


def _rule_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    """An add, a change and a drop of a rule each widen. A new object counts
    too: its timer runs with no member from the first install."""
    for name, kinds in b.items():
        old = a.get(name, {})
        for kind in sorted(set(kinds) | set(old)):
            if set(kinds.get(kind, ())) != set(old.get(kind, ())):
                what = RULE_REACH.get(kind, "a rule that the consent cannot read")
                out.append(f"Changes the `{kind}` rules of `{name}`: {what}.")


def _shape_lines(a: dict[str, Any], b: dict[str, Any], out: list[str]) -> None:
    """Each change widens, in both directions (fail closed)."""
    for key in _APP_KEYS:
        if a["app"][key] != b["app"][key]:
            out.append(f"Changes the app `{key}`.")
    _rule_lines(a["rules"], b["rules"], out)
    for aid, value in b["action_sets"].items():
        if aid in a["action_sets"] and a["action_sets"][aid] != value:
            out.append(f"Changes the fields that the action `{aid}` sets.")
    for label, value in b["fields"].items():
        if a["fields"].get(label) != value:
            out.append(f"Adds or changes the field `{label}`.")
    for name, value in b["records"].items():
        if a["records"].get(name) != value:
            out.append(f"Changes the record tabs of `{name}`.")
    for vid, value in b["view_sources"].items():
        if vid in a["view_sources"] and a["view_sources"][vid] != value:
            out.append(f"Changes the source of the view `{vid}`.")
    if set(b["shell_needs"]) - set(a["shell_needs"]):
        out.append("Adds or changes a shell need.")


def widens(old: Any, new: Any) -> list[str]:
    """One sentence for each thing that *new* adds to *old* in what the
    installer agrees to. An empty list means the new version asks for nothing
    more, so it needs no new consent. A narrowing is never listed. A part that
    the consent cannot rank (a grant list, an app workflow, a hook, an
    unclassified key) is listed when it is new or changed (fail closed).
    The lines name no value of a record."""
    a, b = consent(old), consent(new)
    out: list[str] = []

    def ops(kind: str, label: str) -> None:
        before = {r["object"]: set(r["ops"]) for r in a[kind]}
        for row in b[kind]:
            added = set(row["ops"]) - before.get(row["object"], set())
            if added:
                out.append(f"{label} `{row['object']}`: {', '.join(sorted(added))}.")

    ops("reads", "Reads")
    ops("writes", "Writes")
    _send_lines(a, b, out)
    _connection_lines(a["connections"], b["connections"], out)
    _viewer_lines(a["viewers"], b["viewers"], out)
    for item in sorted(set(b["knowledge"]) - set(a["knowledge"])):
        out.append(f"The skill reads the knowledge `{item}`.")
    _hook_lines(a["hooks"], b["hooks"], out)
    _runtime_lines(a["runtime"], b["runtime"], out)
    before_s = {(s["object"], s["field"]) for s in a["sensitive_fields"]}
    for s in b["sensitive_fields"]:
        if (s["object"], s["field"]) not in before_s:
            out.append(f"Adds the sensitive field `{s['object']}.{s['field']}`.")
    _shape_lines(a["shapes"], b["shapes"], out)
    for key, value in b["unclassified"].items():
        if a["unclassified"].get(key) != value:
            out.append(f"Changes `{key}`, which the consent does not classify.")
    return out
