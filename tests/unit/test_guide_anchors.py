"""Fence F1 for the assistant guide: the anchors and the source agree (WS-OB, GD-1).

Spec: ``project-docs/specs/assistant_guide.md`` §5.1 and §5.7. Each anchor of
``app_manifests/shell/*.json`` has one ``data-guide="<app>.<id>"`` attribute in
``workbench/control_plane/src/**/*.tsx``, and each attribute has a manifest row.
The value is always a literal. A ``data-guide={...}`` expression fails the
fence, because data must not steer a ring (§5.7).

Mutation, seen to fail first: remove one ``data-guide`` attribute, or add one
that no manifest row names, and the matching test goes red.

Run::

    uv run pytest tests/unit/test_guide_anchors.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

from acb_common.app_manifests.shell_manifest import load_anchors

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "workbench/control_plane/src"

#: Every use of the attribute, whatever follows it.
_ANY = re.compile(r"data-guide\b\s*=\s*(?P<value>\S)")
_LITERAL = re.compile(r"data-guide=\"(?P<id>[^\"]*)\"")


def scan(files: dict[str, str]) -> tuple[dict[str, list[str]], list[str]]:
    """The literal ids found (id to files), and the non-literal uses (file:line)."""
    found: dict[str, list[str]] = {}
    bad: list[str] = []
    for name, text in files.items():
        for m in _ANY.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            lit = _LITERAL.match(text, m.start())
            if lit is None:
                bad.append(f"{name}:{line}")
            else:
                found.setdefault(lit.group("id"), []).append(name)
    return found, bad


def _source() -> dict[str, str]:
    return {
        str(p.relative_to(ROOT)).replace("\\", "/"): p.read_text(encoding="utf-8")
        for p in sorted(SRC.rglob("*.tsx"))
        if ".test." not in p.name
    }


def test_each_manifest_anchor_has_an_attribute():
    found, _ = scan(_source())
    missing = [a.full_id for a in load_anchors() if a.full_id not in found]
    assert not missing, f"anchors with no data-guide in the source: {missing}"


def test_each_attribute_has_a_manifest_anchor():
    found, _ = scan(_source())
    known = {a.full_id for a in load_anchors()}
    extra = sorted(set(found) - known)
    assert not extra, f"data-guide values with no manifest anchor: {extra}"


def test_no_attribute_is_an_expression():
    _, bad = scan(_source())
    assert not bad, f"data-guide must be a string literal (spec §5.7): {bad}"


def test_the_six_shell_anchors_exist():
    assert {a.full_id for a in load_anchors()} >= {
        "home.app-list",
        "home.ask",
        "home.my-day",
        "home.account-menu",
        "chat.panel",
        "chat.message-field",
    }


# ── The scan itself: a bad source must fail each direction ─────────────────


def test_scan_sees_a_removed_attribute():
    files = {"a.tsx": '<nav className="x">'}
    found, bad = scan(files)
    assert "home.app-list" not in found and not bad


def test_scan_sees_an_unknown_attribute():
    found, bad = scan({"a.tsx": '<div data-guide="crm.ghost" />'})
    assert set(found) == {"crm.ghost"} and not bad


def test_scan_refuses_an_expression():
    _, bad = scan({"a.tsx": "<div data-guide={id} />\n<p data-guide={`x.${y}`} />"})
    assert bad == ["a.tsx:1", "a.tsx:2"]
