"""JSON Patch for the Studio builder tools (STU-G1).

Spec: ``project-docs/specs/ai_studio.md`` section 6.2.

A small subset of RFC 6902 with the pointers of RFC 6901. Only ``add``,
``replace`` and ``remove`` exist. ``move``, ``copy`` and ``test`` are refused
with a plain sentence. The patch runs on a deep copy of the document. On any
refusal the caller gets the reason and the document it passed in is not
changed. The module is pure data. It does no I/O.

The manifest checks live in ``studio_manifest.validate_manifest``. This module
only moves data, so a patch can build a manifest that fails that validator.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

#: The ops that exist here. The RFC adds ``move``, ``copy`` and ``test``.
PATCH_OPS: tuple[str, ...] = ("add", "replace", "remove")

#: The most ops in one patch.
MAX_OPS = 200


class PatchError(ValueError):
    """One refusal, as one sentence the model can act on."""


def parse_pointer(pointer: Any) -> list[str]:
    """The tokens of an RFC 6901 pointer. ``""`` is the whole document."""
    if not isinstance(pointer, str):
        raise PatchError("A patch path must be a string such as `/objects/0/name`.")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"The path `{_show(pointer)}` must start with `/`.")
    tokens = pointer[1:].split("/")
    out: list[str] = []
    for token in tokens:
        if "~" in token.replace("~0", "").replace("~1", ""):
            raise PatchError(f"The path `{_show(pointer)}` has a bad `~` escape. Use `~0` or `~1`.")
        out.append(token.replace("~1", "/").replace("~0", "~"))
    return out


def _show(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _index(token: str, length: int, path: str, *, append_ok: bool) -> int:
    """The array index of *token*. ``-`` is the end, when *append_ok*."""
    if token == "-":
        if not append_ok:
            raise PatchError(f"The path `{path}` ends in `-`, which only `add` may use.")
        return length
    if not token.isascii() or not token.isdigit() or (len(token) > 1 and token[0] == "0"):
        raise PatchError(f"The path `{path}` needs an array index, not `{_show(token)}`.")
    idx = int(token)
    limit = length if append_ok else length - 1
    if idx > limit:
        raise PatchError(f"The path `{path}` is past the end of the list, which has {length} items.")
    return idx


def _walk(doc: Any, tokens: Sequence[str], path: str) -> Any:
    """The container that holds the last token. Every parent must exist."""
    node = doc
    for token in tokens[:-1]:
        if isinstance(node, dict):
            if token not in node:
                raise PatchError(f"The path `{path}` does not exist: no `{_show(token)}` there.")
            node = node[token]
        elif isinstance(node, list):
            node = node[_index(token, len(node), path, append_ok=False)]
        else:
            raise PatchError(f"The path `{path}` goes through a value that has no children.")
    return node


def _apply_one(doc: Any, op: Any, number: int) -> Any:
    where = f"Op {number}: "
    if not isinstance(op, dict):
        raise PatchError(where + "each op must be an object with `op` and `path`.")
    kind = op.get("op")
    if kind not in PATCH_OPS:
        if kind in ("move", "copy", "test"):
            raise PatchError(
                where + f"`{kind}` is not supported. Use `add`, `replace` or `remove`.")
        raise PatchError(where + "`op` must be `add`, `replace` or `remove`.")
    path = op.get("path")
    try:
        tokens = parse_pointer(path)
        if kind != "remove" and "value" not in op:
            raise PatchError(f"`{kind}` needs a `value`.")
        return _apply_tokens(doc, kind, tokens, path, op.get("value"))
    except PatchError as exc:
        raise PatchError(where + str(exc)) from None


def _apply_tokens(doc: Any, kind: str, tokens: list[str], path: str, value: Any) -> Any:
    if not tokens:
        if kind == "remove":
            raise PatchError("The whole document cannot be removed.")
        return copy.deepcopy(value)
    parent = _walk(doc, tokens, path)
    last = tokens[-1]
    if isinstance(parent, dict):
        present = last in parent
        if kind in ("replace", "remove") and not present:
            raise PatchError(f"The path `{path}` does not exist, so `{kind}` cannot act on it.")
        if kind == "remove":
            del parent[last]
        else:
            parent[last] = copy.deepcopy(value)
    elif isinstance(parent, list):
        if kind == "add":
            parent.insert(_index(last, len(parent), path, append_ok=True), copy.deepcopy(value))
        elif kind == "replace":
            parent[_index(last, len(parent), path, append_ok=False)] = copy.deepcopy(value)
        else:
            del parent[_index(last, len(parent), path, append_ok=False)]
    else:
        raise PatchError(f"The path `{path}` goes through a value that has no children.")
    return doc


def apply_patch(doc: Any, ops: Any) -> Any:
    """A patched deep copy of *doc*. Raise :class:`PatchError` on any refusal.

    *doc* is never changed. Ops run in order, and one bad op refuses the whole
    patch.
    """
    if not isinstance(ops, list):
        raise PatchError("`ops` must be a list of patch operations.")
    if len(ops) > MAX_OPS:
        raise PatchError(f"A patch holds at most {MAX_OPS} ops. This one holds {len(ops)}.")
    work = copy.deepcopy(doc)
    for number, op in enumerate(ops):
        work = _apply_one(work, op, number)
    return work
