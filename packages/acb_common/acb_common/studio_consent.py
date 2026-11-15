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

* ``reads`` and ``writes``: the ``object:<name>.<op>`` actions, grouped by object.
  A list, a get, an aggregate and a tree are reads. A create, an update and a
  delete are writes.
* ``sends``: what leaves the app: the integration actions, the workflows and
  logic modules that an action runs, and the door ops of other apps.
* ``connections``: the names in ``connections``, split into providers and MCP servers.
* ``viewers``: the roles, the objects with an ``access`` list (``restricted``)
  and those with none (``open``: every member who reaches the app reads them).
* ``runtime``: the kind and the pinned packages.
* ``sensitive_fields``: the fields that carry ``sensitive: true``. The restricted
  field type of C9 is not built, so this list is empty today. A manifest that
  carries the flag is already counted, and C9 gives it its meaning.

Fence: ``tests/unit/test_studio_consent.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from acb_common.studio_manifest import OBJECT_OP_CLASSES, OBJECT_OPS

#: The runtime kinds, from the least to the most reach (``ai_studio.md`` §19.3).
RUNTIME_ORDER = ("none", "browser", "sandbox", "packages")

_OBJECT_USES = re.compile(rf"object:([a-z][a-z0-9_]{{0,47}})\.({'|'.join(OBJECT_OPS)})")
_INTEGRATION_USES = re.compile(r"integration:([a-z][a-z0-9_]{0,47}(?:\.[a-z][a-z0-9_]{0,47})?)")
_WORKFLOW_USES = re.compile(r"workflow:([0-9a-fA-F-]{36})")
_LOGIC_USES = re.compile(r"logic:([a-z][a-z0-9_]{0,47})")
_DOOR_USES = re.compile(r"([a-z][a-z0-9_-]{0,47})\.([a-z][a-z0-9_]{0,47})")
_MAX_LIST = 200


def _list(manifest: Mapping[str, Any], key: str) -> list[Any]:
    found = manifest.get(key) if isinstance(manifest, Mapping) else None
    return list(found)[:_MAX_LIST] if isinstance(found, list) else []


def _sorted(items: set[str]) -> list[str]:
    return sorted(items)


def consent(manifest: Any, *, run_as: str | None = None) -> dict[str, Any]:
    """What the installer agrees to. Never raises: a value that is not a
    manifest gives an empty consent, and an action with a ``uses`` that no
    source reads is skipped, because the validator refuses it elsewhere."""
    m: Mapping[str, Any] = manifest if isinstance(manifest, Mapping) else {}
    reads: dict[str, set[str]] = {}
    writes: dict[str, set[str]] = {}
    integrations: set[str] = set()
    workflows: set[str] = set()
    logic: set[str] = set()
    door_ops: set[str] = set()
    for action in _list(m, "actions"):
        uses = action.get("uses") if isinstance(action, Mapping) else None
        if not isinstance(uses, str):
            continue
        if (o := _OBJECT_USES.fullmatch(uses)) is not None:
            bucket = reads if OBJECT_OP_CLASSES[o.group(2)] == "A" else writes
            bucket.setdefault(o.group(1), set()).add(o.group(2))
        elif (i := _INTEGRATION_USES.fullmatch(uses)) is not None:
            integrations.add(i.group(1))
        elif (w := _WORKFLOW_USES.fullmatch(uses)) is not None:
            workflows.add(w.group(1).lower())
        elif (g := _LOGIC_USES.fullmatch(uses)) is not None:
            logic.add(g.group(1))
        elif _DOOR_USES.fullmatch(uses) is not None:
            door_ops.add(uses)
    restricted: set[str] = set()
    open_objects: set[str] = set()
    sensitive: list[dict[str, str]] = []
    for obj in _list(m, "objects"):
        if not isinstance(obj, Mapping) or not isinstance(obj.get("name"), str):
            continue
        name = obj["name"]
        (restricted if obj.get("access") else open_objects).add(name)
        for fld in _list(obj, "fields"):
            if (
                isinstance(fld, Mapping)
                and fld.get("sensitive") is True
                and isinstance(fld.get("key"), str)
            ):
                sensitive.append({"object": name, "field": fld["key"]})
    connections = [c for c in _list(m, "connections") if isinstance(c, str)]
    rt = m.get("runtime") if isinstance(m.get("runtime"), Mapping) else {}
    kind = rt.get("kind") if isinstance(rt.get("kind"), str) and rt.get("kind") else "none"
    packages = rt.get("packages") if isinstance(rt.get("packages"), Mapping) else {}
    return {
        "reads": [{"object": o, "ops": _sorted(v)} for o, v in sorted(reads.items())],
        "writes": [{"object": o, "ops": _sorted(v)} for o, v in sorted(writes.items())],
        "sends": {
            "integrations": _sorted(integrations),
            "workflows": _sorted(workflows),
            "logic": _sorted(logic),
            "door_ops": _sorted(door_ops),
        },
        "connections": {
            "providers": sorted(c for c in connections if not c.startswith("mcp:")),
            "mcp_servers": sorted(c for c in connections if c.startswith("mcp:")),
        },
        "viewers": {
            "roles": sorted(r for r in _list(m, "roles") if isinstance(r, str)),
            "restricted": _sorted(restricted),
            "open": _sorted(open_objects),
        },
        "runtime": {
            "kind": kind,
            "packages": dict(sorted((str(k), str(v)) for k, v in packages.items())),
        },
        "sensitive_fields": sorted(sensitive, key=lambda s: (s["object"], s["field"])),
        "run_as": run_as,
    }


def _rank(kind: str) -> int:
    return RUNTIME_ORDER.index(kind) if kind in RUNTIME_ORDER else len(RUNTIME_ORDER)


def widens(old: Any, new: Any) -> list[str]:
    """One sentence for each thing that *new* adds to *old* in what the
    installer agrees to. An empty list means the new version asks for nothing
    more, so it needs no new consent. A narrowing is never listed. The lines
    name no value of a record."""
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
    for key, label in (
        ("integrations", "Sends through"),
        ("workflows", "Starts workflow"),
        ("logic", "Runs logic"),
        ("door_ops", "Calls the app action"),
    ):
        for item in sorted(set(b["sends"][key]) - set(a["sends"][key])):
            out.append(f"{label} `{item}`.")
    for item in sorted(set(b["connections"]["providers"]) - set(a["connections"]["providers"])):
        out.append(f"Needs the connection `{item}`.")
    for item in sorted(set(b["connections"]["mcp_servers"]) - set(a["connections"]["mcp_servers"])):
        out.append(f"Needs the MCP server `{item}`.")
    # An object that had an access list and has none now is open to every viewer.
    for name in sorted(set(a["viewers"]["restricted"]) & set(b["viewers"]["open"])):
        out.append(f"Opens `{name}` to every member who reaches the app.")
    for item in sorted(set(b["viewers"]["roles"]) - set(a["viewers"]["roles"])):
        out.append(f"Adds the role `{item}`.")
    if _rank(b["runtime"]["kind"]) > _rank(a["runtime"]["kind"]):
        out.append(
            f"Runs member code: the runtime goes from `{a['runtime']['kind']}` "
            f"to `{b['runtime']['kind']}`."
        )
    for pkg, ver in b["runtime"]["packages"].items():
        if a["runtime"]["packages"].get(pkg) != ver:
            out.append(f"Adds the package `{pkg}` {ver}.")
    before_s = {(s["object"], s["field"]) for s in a["sensitive_fields"]}
    for s in b["sensitive_fields"]:
        if (s["object"], s["field"]) not in before_s:
            out.append(f"Adds the sensitive field `{s['object']}.{s['field']}`.")
    return out
