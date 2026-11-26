"""The runtime kinds of a Studio app, and the pure import scan (STU-B7a).

Spec: ``project-docs/specs/ai_studio.md`` row B7 and section 19.3.

A manifest names one runtime kind in ``runtime.kind``. No key means ``none``.
The builder picks the lowest kind that works.

- ``none``: the platform runs the app. No member code exists.
- ``browser``: member code runs in the viewer's browser, in the sandboxed frame.
- ``sandbox``: member Python runs in a sandbox with no network, on the standard image.
- ``packages``: the same sandbox, plus one frozen package layer.

:data:`SANDBOX_MODULES` is the one map of a standard-image package to its import
name. ``tests/unit/test_coding_sandbox_packages.py`` holds it equal to
``apps/services/orchestrator/sandbox/requirements.in``.

:func:`scan_imports` is pure. It parses with ``ast`` and never runs the code.
No live caller exists yet. The logic slices B4 and B5 call it.

**What the scan does not catch.** It is an early error for the author, not a
fence. It reads ``import`` and ``from ... import`` statements only. It does not
see ``__import__``, ``importlib.import_module`` or ``exec``. A relative import
passes. The whole standard library is allowed, so ``socket``, ``subprocess`` and
``ctypes`` pass too. The sandbox is the real fence: it has no network and it
holds the limits (``ai_studio.md`` section 19.3). Never rely on this scan to
keep code safe.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping

from acb_common.studio_manifest import ManifestError

RUNTIME_KINDS: tuple[str, ...] = ("none", "browser", "sandbox", "packages")

#: Each package of the standard image, by its normalised distribution name,
#: and the module that it imports as (maf_coding_engine.md section 7.2).
SANDBOX_MODULES: dict[str, str] = {
    # The first set of section 7.2.
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "pypdf": "pypdf",
    "fpdf2": "fpdf",
    "markdown": "markdown",
    "tabulate": "tabulate",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "requests": "requests",
    # The second set (2026-10-05): business documents.
    "python-docx": "docx",
    "python-pptx": "pptx",
    "xlsxwriter": "xlsxwriter",
    "reportlab": "reportlab",
    "pdfplumber": "pdfplumber",
    "pillow": "PIL",
    # Analysis and charts.
    "scipy": "scipy",
    "seaborn": "seaborn",
    "networkx": "networkx",
    # HTML, XML and templates.
    "beautifulsoup4": "bs4",
    "lxml": "lxml",
    "jinja2": "jinja2",
    # The data toolkit (WS-43y1a): the SQL engine of sandbox/data_engine.py.
    "duckdb": "duckdb",
}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def allowed_modules(kind: str, packages: Mapping[str, str]) -> set[str]:
    """The top-level import names that *kind* allows."""
    if kind not in ("sandbox", "packages"):
        return set()
    allowed = set(sys.stdlib_module_names) | set(SANDBOX_MODULES.values())
    if kind == "packages":
        for name in packages:
            allowed.add(SANDBOX_MODULES.get(_norm(name), name.replace("-", "_")))
    return allowed


def _err(path: str, message: str) -> ManifestError:
    return ManifestError(path=path, message=message)


def scan_imports(source: str, kind: str, packages: Mapping[str, str]) -> list[ManifestError]:
    """Return one error for each import that *kind* does not allow.

    Pure. No execution. A relative import and ``__future__`` pass. Kind ``none``
    with non-empty source is one error. Kind ``browser`` runs no Python, so
    this scan does not judge it.
    """
    if kind == "none":
        if source.strip():
            return [_err("$.runtime", "Kind `none` runs no member code. Set `runtime.kind` to `sandbox`.")]
        return []
    if kind not in ("sandbox", "packages"):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [_err("$.runtime", f"The Python source has a syntax error on line {exc.lineno}. Fix it.")]
    allowed = allowed_modules(kind, packages)
    where = "the standard library and the sandbox image"
    if kind == "packages":
        where += " and `runtime.packages`"
    errors: list[ManifestError] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            names = [node.module]
        else:
            continue
        for full in names:
            top = full.split(".")[0]
            if top in allowed or top == "__future__" or top in seen:
                continue
            seen.add(top)
            fix = "Use an allowed module." if kind == "packages" else (
                "Use an allowed module or set `runtime.kind` to `packages`.")
            errors.append(_err(
                "$.runtime",
                f"The import `{top}` is outside {where}. {fix}",
            ))
    return sorted(errors, key=lambda e: e.message)
