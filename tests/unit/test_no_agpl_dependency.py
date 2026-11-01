"""No AGPL package in our Python or Node lock files (WS-WEB WEB-2).

Spec: ``project-docs/specs/web_research.md`` section 1.2 item 3 ("No AGPL
code in our process") and the fence row of section 14. WEB-2 added
Trafilatura and its closure, so this fence came with it.

* **Python.** ``uv.lock`` holds no licence, so the test reads the licence
  of each locked registry package from its installed metadata: the
  ``License-Expression`` field, the ``License`` field and each ``License ::``
  classifier. A name that is AGPL by itself (:data:`_AGPL_BY_NAME`) fails
  also where its metadata is not installed.
* **Node.** Each ``package-lock.json`` names the licence of each package.

**The one allow-entry.** The gateway already holds ``pymupdf`` (AGPL-3.0),
from before this fence. Task 1313 owns it. The test scans the whole lock,
and not only the closure of ``acb_skills``, because a closure-only scan lets
a new AGPL package in through any other service. So ``pymupdf`` carries an
explicit entry in :data:`ALLOWED` that names the task. The entry fails when
the lock drops the package, so it cannot go stale.

The closure of ``acb_skills``, which WEB-2 changed, gets the strict form:
each registry package of it must have metadata that this machine can read.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import re
import tomllib
from pathlib import Path

from packaging.markers import Marker

REPO = Path(__file__).resolve().parents[2]
UV_LOCK = REPO / "uv.lock"
NODE_LOCKS = sorted(REPO.glob("workbench/*/package-lock.json"))

#: A licence text that is AGPL. ``LGPL`` and ``GPL`` do not match.
_AGPL_RE = re.compile(r"\bAGPL|Affero", re.IGNORECASE)

#: The AGPL packages that the lock still holds. Each entry names its task.
ALLOWED: dict[str, str] = {
    # The gateway reads PDF files with PyMuPDF (apps/services/gateway/
    # pyproject.toml). It came before this fence. Task 1313 removes it or
    # moves it out of our process (web_research.md section 1.2 item 3).
    "pymupdf": "task 1313",
}

#: Names that are AGPL by themselves (web_research.md section 10).
_AGPL_BY_NAME = frozenset({"pymupdf", "pymupdfb", "pymupdf4llm"})

#: The packages that WEB-2 added for Trafilatura. Each must be checked.
WEB2_CLOSURE = frozenset({"trafilatura", "courlan", "htmldate", "justext",
                          "lxml-html-clean", "dateparser", "babel", "tld"})


def _lock() -> dict[str, dict]:
    data = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return {p["name"]: p for p in data["package"]}


def _registry(lock: dict[str, dict]) -> list[str]:
    return sorted(n for n, p in lock.items() if "registry" in p.get("source", {}))


def _licence(name: str) -> str | None:
    """The licence text of an installed package, or None when it is not
    installed here."""
    try:
        meta = md.metadata(name)
    except md.PackageNotFoundError:
        return None
    parts = [meta.get("License-Expression") or "", meta.get("License") or ""]
    parts += [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


def _applies(marker: str | None) -> bool:
    """True when a dependency edge of the lock applies on this machine."""
    if not marker:
        return True
    try:
        return Marker(marker).evaluate()
    except Exception:  # a marker that names an extra: keep the edge
        return True


def _closure(lock: dict[str, dict], root: str) -> set[str]:
    """Each package that ``root`` needs on this machine. An edge with an
    extra, such as ``lxml[html-clean]``, adds the packages of that extra."""
    visited: set[tuple[str, tuple[str, ...]]] = set()
    todo: list[tuple[str, tuple[str, ...]]] = [(root, ())]
    while todo:
        node = todo.pop()
        name, extras = node
        if node in visited or name not in lock:
            continue
        visited.add(node)
        pkg = lock[name]
        edges = list(pkg.get("dependencies", []))
        for extra in extras:
            edges += pkg.get("optional-dependencies", {}).get(extra, [])
        todo += [(d["name"], tuple(d.get("extra", ()))) for d in edges
                 if _applies(d.get("marker"))]
    return {name for name, _extras in visited}


def test_the_pattern_matches_agpl_and_nothing_else() -> None:
    """The check can fail, and it does not fail on the GPL family."""
    for text in ("AGPL-3.0-only", "AGPL-3.0-or-later",
                 "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License",
                 "License :: OSI Approved :: GNU Affero General Public License v3",
                 "(MIT OR AGPL-3.0)"):
        assert _AGPL_RE.search(text), text
    for text in ("LGPL-3.0-or-later", "GPL-3.0-or-later", "MPL-1.1", "Apache-2.0",
                 "MIT", "BSD-3-Clause", "License :: OSI Approved :: GNU Lesser General "
                 "Public License v2 or later (LGPLv2+)"):
        assert not _AGPL_RE.search(text), text


def test_no_agpl_python_package() -> None:
    lock = _lock()
    registry = _registry(lock)
    faults: list[str] = []
    checked = 0
    for name in registry:
        if name in _AGPL_BY_NAME and name not in ALLOWED:
            faults.append(f"{name}: AGPL by its name")
        text = _licence(name)
        if text is None:
            continue
        checked += 1
        if _AGPL_RE.search(text) and name not in ALLOWED:
            faults.append(f"{name}: {text[:200]}")
    assert faults == [], f"an AGPL package in uv.lock: {faults}"
    # The check read real metadata. A venv with no packages cannot pass it.
    assert checked >= 0.9 * len(registry), f"read {checked} of {len(registry)}"


def test_the_acb_skills_closure_is_checked_in_full() -> None:
    """Strict on the closure that WEB-2 changed: no package of it is
    skipped, and no package of it is AGPL."""
    lock = _lock()
    closure = _closure(lock, "acb-skills")
    assert closure >= WEB2_CLOSURE, sorted(WEB2_CLOSURE - closure)
    unread: list[str] = []
    for name in sorted(closure):
        if "registry" not in lock[name].get("source", {}):
            continue
        text = _licence(name)
        if text is None:
            unread.append(name)
        else:
            assert not _AGPL_RE.search(text), f"{name}: {text}"
    assert unread == [], f"the metadata of these packages is not installed: {unread}"


def test_tld_is_taken_under_a_licence_that_is_not_agpl() -> None:
    """tld is MPL-1.1, GPL-2.0 or LGPL-2.1. We take MPL-1.1."""
    text = _licence("tld") or ""
    assert "MPL" in text and not _AGPL_RE.search(text), text


def test_each_allowed_entry_is_still_locked_and_still_agpl() -> None:
    """An entry for a package that left the lock is stale, so it fails."""
    lock = _lock()
    for name, task in ALLOWED.items():
        assert task.startswith("task "), f"{name} names no task"
        assert name in lock, f"{name} left uv.lock. Delete its entry from ALLOWED."
        text = _licence(name)
        if text is not None:
            assert _AGPL_RE.search(text), f"{name} is not AGPL now: {text}"


def test_no_agpl_node_package() -> None:
    assert NODE_LOCKS, "no package-lock.json was found"
    faults: list[str] = []
    for path in NODE_LOCKS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for where, pkg in (data.get("packages") or {}).items():
            licence = pkg.get("license") or pkg.get("licenses") or ""
            if _AGPL_RE.search(json.dumps(licence)):
                faults.append(f"{path.relative_to(REPO).as_posix()}: {where}: {licence}")
    assert faults == [], f"an AGPL package in a Node lock: {faults}"
