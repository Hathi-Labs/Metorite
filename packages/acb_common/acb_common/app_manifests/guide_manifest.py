"""The guides of the assistant, read from ``guides/<app>.json`` (WS-OB, GD-3).

Spec: ``project-docs/specs/assistant_guide.md`` §5.2 and §5.7.

A file holds a list of guide objects. A guide is data: an id, who may run it,
a title, the ways a member asks for it, its steps and the chat tool that does
the act. ``scripts/gen_shell_manifests.py`` writes ``guides.generated.ts`` from
:func:`load_guides`, and the orchestrator reads the same JSON.

This loader checks the SHAPE only. Whether a path is live, an anchor exists, a
tool is covered and a feature fits its app is fence F2, in
``tests/unit/test_guides.py``, because those facts live in other modules.

A ``point`` step names an anchor id, never a CSS selector (§5.7). The loader
refuses an unknown key, so a guide cannot carry one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acb_common.app_manifests.shell_manifest import ShellManifestError

GUIDES_DIR = Path(__file__).parent / "guides"

#: The step kinds, and the keys that each kind needs besides ``kind`` and ``say``.
STEP_KINDS: dict[str, tuple[str, ...]] = {
    "open": ("path",),
    "point": ("anchor",),
    "fill": ("form", "values"),
    "say": (),
}
_GUIDE_KEYS = {"id", "app", "feature", "admin_only", "title", "asks", "steps", "do_tool"}
MIN_ASKS, MAX_ASKS = 3, 6
#: A step line is short. The chat shows it as one or two sentences.
MAX_SAY_CHARS = 160


@dataclass(frozen=True)
class GuideStep:
    """One step. Only the fields of its ``kind`` hold a value."""

    kind: str
    say: str
    path: str | None = None
    anchor: str | None = None
    form: str | None = None
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Guide:
    id: str
    app: str
    feature: str | None
    admin_only: bool
    title: str
    asks: tuple[str, ...]
    steps: tuple[GuideStep, ...]
    do_tool: str | None


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShellManifestError(f"{where} must be a non-empty string")
    return value


def _step(raw: Any, where: str) -> GuideStep:
    if not isinstance(raw, dict):
        raise ShellManifestError(f"{where}: a step must be an object")
    kind = raw.get("kind")
    if kind not in STEP_KINDS:
        raise ShellManifestError(f"{where}: step kind must be one of {sorted(STEP_KINDS)}")
    want = {"kind", "say", *STEP_KINDS[kind]}
    if set(raw) != want:
        raise ShellManifestError(f"{where}: a {kind!r} step must hold exactly {sorted(want)}")
    say = _text(raw["say"], f"{where}: say")
    if len(say) > MAX_SAY_CHARS:
        raise ShellManifestError(f"{where}: say is longer than {MAX_SAY_CHARS} characters")
    path = anchor = form = None
    values: dict[str, str] = {}
    if kind == "open":
        path = _text(raw["path"], f"{where}: path")
    elif kind == "point":
        anchor = _text(raw["anchor"], f"{where}: anchor")
    elif kind == "fill":
        form = _text(raw["form"], f"{where}: form")
        got = raw["values"]
        if not isinstance(got, dict) or not got:
            raise ShellManifestError(f"{where}: values must be a non-empty object")
        values = {_text(k, f"{where}: a value name"): _text(v, f"{where}: value {k!r}") for k, v in got.items()}
    return GuideStep(kind, say, path, anchor, form, values)


def _guide(raw: Any, source: str) -> Guide:
    if not isinstance(raw, dict) or set(raw) != _GUIDE_KEYS:
        raise ShellManifestError(f"{source}: a guide must hold exactly {sorted(_GUIDE_KEYS)}")
    gid = _text(raw["id"], f"{source}: id")
    where = f"{source}: guide {gid!r}"
    app = _text(raw["app"], f"{where}: app")
    if not gid.startswith(app + "."):
        raise ShellManifestError(f"{where}: the id must start with the app and a dot")
    feature = raw["feature"]
    if feature is not None:
        _text(feature, f"{where}: feature")
    if not isinstance(raw["admin_only"], bool):
        raise ShellManifestError(f"{where}: admin_only must be true or false")
    asks = raw["asks"]
    if not isinstance(asks, list) or not MIN_ASKS <= len(asks) <= MAX_ASKS:
        raise ShellManifestError(f"{where}: asks must hold {MIN_ASKS} to {MAX_ASKS} phrasings")
    clean_asks = tuple(_text(a, f"{where}: an ask") for a in asks)
    if len(set(clean_asks)) != len(clean_asks):
        raise ShellManifestError(f"{where}: asks must not repeat")
    steps = raw["steps"]
    if not isinstance(steps, list) or not steps:
        raise ShellManifestError(f"{where}: steps must be a non-empty list")
    do_tool = raw["do_tool"]
    if do_tool is not None:
        _text(do_tool, f"{where}: do_tool")
    return Guide(
        id=gid,
        app=app,
        feature=feature,
        admin_only=raw["admin_only"],
        title=_text(raw["title"], f"{where}: title"),
        asks=clean_asks,
        steps=tuple(_step(s, f"{where}, step {i + 1}") for i, s in enumerate(steps)),
        do_tool=do_tool,
    )


def load_guides(directory: Path | None = None) -> tuple[Guide, ...]:
    """Every guide of every ``guides/*.json`` file, in file order, then list order.

    A file holds a list. Each guide's ``app`` equals the file name. A guide id
    is unique across files.
    """
    out: list[Guide] = []
    for path in sorted((directory or GUIDES_DIR).glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, list) or not doc:
            raise ShellManifestError(f"{path.name}: a guide file must be a non-empty list")
        for raw in doc:
            guide = _guide(raw, path.name)
            if guide.app != path.stem:
                raise ShellManifestError(f"{path.name}: guide {guide.id!r} must have app {path.stem!r}")
            out.append(guide)
    ids = [g.id for g in out]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ShellManifestError(f"duplicate guide id: {dup}")
    return tuple(out)
