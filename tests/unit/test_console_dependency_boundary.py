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


# ── Clause 11 — one caller, and the seat cap depends on it staying one ───────

#: The resolve client, and the modules allowed to reach it.
#:
#: A second call site is not a style problem: ``console_resolve`` allocates a
#: SEAT (via ``resolve_for_signin``). The proposed default for CP-2b was to wire
#: it behind ``acb_auth.access.resolve_access``, which has six production callers
#: — one of them (``gateway/routes/rooms.py``) fanning it over every participant
#: of a room. That would have fired one Console request and one seat allocation
#: per participant per room load, which is exactly the farmable cap clause 11
#: exists to prevent.
#:
#: ⚠️ **Two importers as of CP-2c slice 2 (2026-08-20), and no more.**
#: ``routes/signup.py`` calls a DIFFERENT function on this module —
#: ``provision_org_on_console``, which mirrors a provision and allocates no seat
#: by itself — and is likewise a **session-email-only** route (the owner is the
#: authenticated session, never the body). What stays forbidden is wiring
#: EITHER function behind ``resolve_access`` = farmable seat burn. A third
#: importer is a new call site that no runtime assertion sees until a customer's
#: seat cap is exhausted.
_RESOLVE_CLIENT = "packages/acb_auth/acb_auth/console_resolve.py"
_THE_ONE_CALLER = "apps/services/gateway/gateway/routes/signin.py"
_THE_SIGNUP_CALLER = "apps/services/gateway/gateway/routes/signup.py"
_ALLOWED_CALLERS = (_THE_ONE_CALLER, _THE_SIGNUP_CALLER)


def _imports_console_resolve(path: Path) -> bool:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            if any(a.name == "acb_auth.console_resolve" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "acb_auth.console_resolve":
                return True
            if node.module == "acb_auth" and any(
                a.name == "console_resolve" for a in node.names
            ):
                return True
    return False


def test_resolve_is_reachable_only_from_the_signin_path() -> None:
    """``console_resolve`` has exactly TWO callers, and both are named here.

    A structural fence is preferred to an example one (R7): the failure is a
    second call site added later, which no runtime assertion sees until a
    customer's seat cap is exhausted.

    ⚠️ Grown from ONE to TWO importers by CP-2c slice 2 (2026-08-20):
    ``routes/signin.py`` (``resolve_for_signin``) and ``routes/signup.py``
    (``provision_org_on_console``). Both are **session-email-only** routes;
    what stays forbidden is wiring either behind ``resolve_access`` (six
    callers, one a room fan-out) = farmable seat burn. A THIRD is the drift.

    ⚠️ It is deliberately paired with a frontend fence. This one alone is
    satisfied by a BFF that calls ``POST /signin/resolve`` from anywhere;
    ``signin.test.ts``'s *"resolve fires only from the signIn callback"* is
    what closes that half, because the chain crosses a language boundary.
    """
    assert (_REPO / _RESOLVE_CLIENT).exists(), (
        f"{_RESOLVE_CLIENT} moved — this fence is now vacuous"
    )
    for rel in _ALLOWED_CALLERS:
        assert (_REPO / rel).exists(), (
            f"{rel} moved — a named caller is gone, and a fence that pins "
            "'exactly these' to a missing file pins nothing"
        )

    callers = sorted(
        str(p.relative_to(_REPO)).replace("\\", "/")
        for p in _python_files()
        if _imports_console_resolve(p)
    )
    assert callers == sorted(_ALLOWED_CALLERS), (
        f"console_resolve callers drifted: {callers}\n\n"
        "It allocates a SEAT (`resolve_for_signin`). Exactly two sites may call "
        "it — the completion of a sign-in and the self-serve signup provision, "
        "both with a provider-verified session email. Never `resolve_access` "
        "(six callers, one of them a fan-out over a room's participants), "
        "never `_with_resolved_access` (every authenticated request). "
        "customer_console.md §6 clause 11 · CP-2c slice 2."
    )


# ── Clause 6 / §6(j) — the box branches on the OUTCOME, never on a string ────

#: The Customer Console's org-lifecycle vocabulary. It belongs to ONE state
#: machine, ``customer_console.lifecycle``, and a tenant-side module that
#: compared against any of these words would be a second copy of that machine
#: spelled as an ``if`` — the exact thing §6(d) refuses.
_LIFECYCLE_WORDS = frozenset(
    {"trial", "active", "past_due", "suspended", "cancelled", "deleted"}
)

#: Scoped to the modules CP-2b OWNS rather than swept repo-wide, and the reason
#: is a real pre-existing hit rather than convenience:
#: ``gateway/routes/admin/members.py`` tests ``patch.status in ("suspended",
#: "removed")`` — that is ``org_membership.status``, a MEMBERSHIP status
#: (``invited|active|suspended|removed``, migration 159's CHECK), a different
#: vocabulary that happens to share two words. A repo-wide sweep would be red on
#: day one against correct code, and a fence that is red for the wrong reason
#: gets an exemption list, and an exemption list is where the real violation
#: eventually hides.
_CP2B_TENANT_MODULES: tuple[str, ...] = (
    _RESOLVE_CLIENT,
    _THE_ONE_CALLER,
    _THE_SIGNUP_CALLER,
)


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Object ids of every docstring constant in *tree*.

    Excluded from the scan below on purpose: this module's own prose has to be
    able to EXPLAIN the rule — *"`deleted` is the only state that refuses
    sign-in"* — and a scan that read its own documentation as the violation
    would push the explanation out of the file, which is where the reason for
    a rule goes to die.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def test_the_deployment_never_branches_on_a_lifecycle_string() -> None:
    """No CP-2b tenant module carries a lifecycle state as a string constant.

    After §6(j) the branch inputs are (a) the HTTP status, (b)
    ``len(organizations)`` and (c) three booleans. ``registry_status``'s only
    reader anywhere is the word handed to refusal copy — never a comparison.

    Constants, not text: a module that never spells the word cannot compare
    against it, and it cannot build one to compare against either without an
    obvious contrivance. Docstrings are excluded so the rule can be argued in
    the file it binds.
    """
    offenders: list[str] = []
    for rel in _CP2B_TENANT_MODULES:
        path = _REPO / rel
        assert path.exists(), f"{rel} moved — this fence is now vacuous"
        tree = _tree(path)
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
            ):
                for word in sorted(_LIFECYCLE_WORDS):
                    if word in node.value:
                        offenders.append(f"{rel}:{node.lineno} → {word!r}")
    assert offenders == [], (
        "lifecycle state name(s) as string constants in CP-2b tenant code:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe box stores and applies the CAPABILITY BOOLEANS. A "
          "deployment that reads a status word and decides is a second copy "
          "of customer_console.lifecycle written as an `if`; a deployment "
          "that reads `sign_in` cannot drift, because there is nothing to "
          "drift from. customer_console.md §6(d) / §6(j)."
    )
