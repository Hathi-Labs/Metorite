"""CP-2b — the tenant deployable must not depend on the Customer Console.

Spec: ``project-docs/specs/customer_console.md`` §6(d), §6(e), §6(j) · clauses
6, 11 and 12's structural halves.

⚠️ **These fences take NO database, deliberately.** Folded into
``tests/unit/test_deployment_resolve_cache.py`` they would skip whenever
``TENANT_LADDER_DATABASE_URL`` is unset — which is precisely the disarmed-gate
failure (CP-3) the whole of §6(i) exists to prevent. A structural fence that
skips is a fence that was never there.

## Why a fence at all, when the import would obviously fail at runtime

**It would not fail.** ``pyproject.toml`` installs ``customer-console`` into the
**root** workspace venv on purpose — *"the root test suite imports
`customer_console`, so it must be installed into the shared venv by `uv sync` or
CI fails with ModuleNotFoundError"*. So::

    from customer_console.lifecycle import capabilities_of   # inside acb_auth

is **green in pytest and broken in the deployable**: the gateway image installs
the gateway's own dependency closure, which does not contain it. That is the
single most likely way CP-2b gets built wrong, and it is invisible to every
test that does not look at the packaging.

The two halves are not redundant. The manifest read (1) catches somebody
*declaring* the dependency; the source scan (2) catches an import the root venv
already satisfies, which no manifest and no runtime assertion sees until a
container is built. **(2) is the load-bearing half.**

Both are **ratchets** in the established style of ``test_db_engine_seam.py``:
they pass on day one, which is the point and is not evidence they work. Each
was shown red first by adding the offending requirement / the offending import,
watching exactly it fail, and taking it back out.
"""

from __future__ import annotations

import ast
import tomllib
from functools import cache
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

#: The distribution name a tenant-side manifest must never require, and the
#: import name a tenant-side module must never import. Two spellings of one
#: package (hatch normalises ``customer-console`` → ``customer_console``), so
#: both are checked rather than one being assumed to imply the other.
_CONSOLE_DIST = "customer-console"
_CONSOLE_MODULE = "customer_console"

#: The manifests that describe what ships INSIDE the tenant deployable.
#:
#: The gateway's own manifest is the container's closure; ``acb_auth``'s is the
#: seam CP-2b's client lives in, and a dependency added there reaches every
#: service that depends on ``acb_auth`` — which is all of them.
_TENANT_MANIFESTS: tuple[str, ...] = (
    "apps/services/gateway/pyproject.toml",
    "packages/acb_auth/pyproject.toml",
)

#: The source trees that are compiled into the tenant deployable.
_TENANT_SOURCE_ROOTS: tuple[str, ...] = (
    "packages",
    "apps/services/gateway",
)


def _requirement_names(manifest: Path) -> set[str]:
    """Every requirement name in *manifest*, normalised, dependency groups too.

    Parsed as TOML rather than grepped: a requirement carrying a version
    specifier, an extra or an environment marker (``customer-console[foo]>=1;
    python_version >= '3.12'``) is the same dependency, and a substring match
    over the file would also read this test's own prose in a comment as a
    violation.
    """
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    raw: list[str] = list(data.get("project", {}).get("dependencies", []) or [])
    for group in (data.get("dependency-groups", {}) or {}).values():
        raw.extend(g for g in group if isinstance(g, str))
    for extra in (
        data.get("project", {}).get("optional-dependencies", {}) or {}
    ).values():
        raw.extend(extra)

    names: set[str] = set()
    for spec in raw:
        # Strip marker, then extras, then any version specifier. PEP 508 names
        # are what remains up to the first non-name character.
        head = spec.split(";", 1)[0].strip()
        head = head.split("[", 1)[0]
        for stop in ("==", ">=", "<=", "~=", "!=", ">", "<", "@", " "):
            head = head.split(stop, 1)[0]
        if head:
            names.add(head.strip().lower().replace("_", "-"))
    return names


def _python_files() -> list[Path]:
    out: list[Path] = []
    for rel in _TENANT_SOURCE_ROOTS:
        root = _REPO / rel
        out.extend(
            p for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and ".venv" not in p.parts
        )
    return out


@cache
def _tree(path: Path) -> ast.Module:
    # utf-8-sig: at least one module in the tree carries a BOM, and a leading
    # ﻿ is a syntax error to ast.parse.
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names this file IMPORTS.

    AST, never text: ``console_resolve.py``'s module docstring argues at length
    about why it must not import ``customer_console``, and a grep would read
    that argument as the violation it forbids.
    """
    roots: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        # `from . import x` has module=None and level>0 — never absolute.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


# ── §6(d) fence 1 — the manifest half ────────────────────────────────────────

def test_the_gateway_does_not_depend_on_the_customer_console() -> None:
    """No tenant-side manifest may REQUIRE the Customer Console.

    The Console is a separate deployable on a separate plane
    (``saas_multitenancy.md`` §0.9.2) holding a cross-tenant database. A tenant
    box that shipped it would have the code that answers *"how many customers
    do we have and what do they owe us"* installed beside the code that serves
    one customer's mail.
    """
    offenders = []
    for rel in _TENANT_MANIFESTS:
        manifest = _REPO / rel
        assert manifest.exists(), f"{rel} moved — this fence is now vacuous"
        if _CONSOLE_DIST in _requirement_names(manifest):
            offenders.append(rel)

    assert offenders == [], (
        f"tenant-side manifest(s) requiring {_CONSOLE_DIST!r}: {offenders}\n\n"
        "The capability decision is made where the state machine is "
        "(customer_console.lifecycle.capabilities_of) and only its RESULT "
        "crosses the wire — customer_console.md §6(d). The deployment stores "
        "the three booleans; it must never import the module that computes "
        "them."
    )


# ── §6(d) fence 2 — the source half, and the load-bearing one ────────────────

def test_no_tenant_module_imports_customer_console() -> None:
    """No module under ``packages/`` or the gateway may import it.

    This is the half that catches the real failure: the root workspace venv
    installs ``customer-console`` so the root test suite can import it, so such
    an import is **green in pytest and broken in the container**. The manifest
    check above cannot see it, because nobody had to declare anything.
    """
    files = _python_files()
    assert len(files) > 100, (
        f"only {len(files)} tenant-side Python files found — the sweep's roots "
        "are wrong and every assertion below is vacuous"
    )
    offenders = sorted(
        str(p.relative_to(_REPO)).replace("\\", "/")
        for p in files
        if _CONSOLE_MODULE in _imported_roots(p)
    )
    assert offenders == [], (
        f"tenant-side module(s) importing {_CONSOLE_MODULE!r}:\n  "
        + "\n  ".join(offenders)
        + "\n\nGreen here, ModuleNotFoundError in the gateway image: the root "
          "venv installs customer-console for the root test suite, the "
          "gateway's own closure does not. See customer_console.md §6(d)."
    )
