"""Pick the unit tests a change can affect, for the fast inner loop.

    uv run python scripts/affected_tests.py            # list them
    uv run python scripts/affected_tests.py --run      # run them with pytest
    uv run python scripts/affected_tests.py --base origin/main --run

This is NOT the merge gate. pr-check runs the WHOLE suite on every PR. This
script exists so that an agent, or a person, does not run all 27,000 tests
locally to check a 3-file change.

A test file is picked when it:

1. changed itself,
2. names a changed file by its path (many tests read docs, workflows, SQL),
3. imports a changed module (``import a.b``, ``from a import b``, an alias,
   or a dotted string such as ``patch("a.b.fn")``), or
4. imports a module that imports a changed module (one level, see HUB_LIMIT).

Imports are read with ``ast``, not a regex: a regex lost 38% of the real
import pairs to multi-line imports and ``as`` aliases (measured 2026-10-16).

A change to a file that every test depends on (``pyproject.toml``, a
``conftest.py``, ``uv.lock``) prints FULL, and ``--run`` then runs the whole
suite. Frontend files are not Python's business: the script prints the
``vitest related`` command for each Next app, which does the same job there.

Fence: tests/unit/test_affected_tests.py.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path("tests/unit")
FULL_SUITE = {"pyproject.toml", "uv.lock", "tests/conftest.py", "tests/unit/conftest.py"}
# A module that more test files than this import (gateway.main, for one) is a
# hub. Expanding through it would pick most of the suite for any route change.
HUB_LIMIT = 40
FRONTEND_APPS = ("workbench/control_plane/", "workbench/operator_console/")
# CLAUDE.md section 6: this suite hangs on a local run. CI runs it.
LOCAL_HANG = "tests/unit/test_memory_integration.py"


def changed_files(base: str, root: Path = ROOT) -> list[str]:
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]

    merge_base = git("merge-base", base, "HEAD")[0]
    names = set(git("diff", "--name-only", merge_base))
    names |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(n.replace("\\", "/") for n in names)


def module_name(path: str, root: Path = ROOT) -> str | None:
    """The dotted import name of a .py file, from its topmost package dir."""
    p = root / path
    if p.suffix != ".py":
        return None
    parts = [p.stem] if p.name != "__init__.py" else []
    d = p.parent
    while (d / "__init__.py").is_file():
        parts.insert(0, d.name)
        d = d.parent
    return ".".join(parts) if parts else None


# `patch("gateway.routes.crm.fn")`, `import_module("x.y")` and the like.
_DOTTED_STRING = re.compile(r"""["']([A-Za-z_]\w*(?:\.\w+)+)["']""")
# Only for a file `ast` cannot parse. Statement by statement, so one import
# never swallows the next line.
_FALLBACK = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ]+)|import\s+([\w., ]+))", re.M)


def _resolve(node: ast.ImportFrom, module: str | None, is_package: bool) -> str | None:
    if not node.level:
        return node.module
    if not module:
        return None
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    if not base:
        return node.module
    return ".".join(base + ([node.module] if node.module else []))


def imports(body: str, module: str | None = None, is_package: bool = False) -> set[str]:
    """Every dotted name a file imports or names, with all its prefixes.

    Importing ``a.b.c`` runs ``a/__init__.py`` and ``a/b/__init__.py`` too, so
    a change to either affects the importer. Hence the prefixes. ``module``
    lets a relative import (``from . import x``) resolve.
    """
    names: set[str] = set(_DOTTED_STRING.findall(body))
    try:
        # Silence SyntaxWarnings (invalid escapes) from files we only index.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(body)
    except (SyntaxError, ValueError):
        for m in _FALLBACK.finditer(body):
            if m.group(1):
                names.add(m.group(1))
                names |= {
                    f"{m.group(1)}.{n.split()[0]}" for n in m.group(2).split(",") if n.strip()
                }
            else:
                names |= {n.split()[0] for n in m.group(3).split(",") if n.strip()}
    else:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mod = _resolve(node, module, is_package)
                if mod:
                    names.add(mod)
                    names |= {f"{mod}.{a.name}" for a in node.names if a.name != "*"}
    out: set[str] = set()
    for n in names:
        parts = n.split(".")
        out |= {".".join(parts[: i + 1]) for i in range(len(parts))}
    return out


def _texts(files: list[Path], root: Path) -> dict[str, str]:
    out = {}
    for f in files:
        try:
            out[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def _import_index(
    changed_mods: set[str], sources: dict[str, str], tests: dict[str, str], root: Path
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Map dotted name -> importing source modules, and -> importing tests.

    Parse only files that spell a needed module's last name. Every import of
    `a.b.leaf` (absolute, relative, aliased or a dotted string) spells
    "leaf", so this loses nothing. Parsing all ~4,000 files took 26 s.
    """

    def leaf(dotted: str) -> str:
        return dotted.rsplit(".", 1)[-1]

    leaves = {leaf(m) for m in changed_mods}
    src_users: dict[str, set[str]] = {}
    for s, body in sources.items():
        mod = module_name(s, root) if any(lf in body for lf in leaves) else None
        for n in imports(body, mod, s.endswith("__init__.py")) if mod else ():
            src_users.setdefault(n, set()).add(mod)
    upstream = set().union(*(src_users.get(m, set()) for m in changed_mods))
    leaves |= {leaf(m) for m in upstream}
    test_users: dict[str, set[str]] = {}
    for t, body in tests.items():
        for n in imports(body) if any(lf in body for lf in leaves) else ():
            test_users.setdefault(n, set()).add(t)
    return src_users, test_users


def select(changed: list[str], root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    """Return (test files, reasons for a full run, frontend files)."""
    tests = _texts(sorted((root / TESTS).glob("test_*.py")), root)
    sources = _texts(
        [
            p
            for top in ("apps", "packages")
            for p in (root / top).rglob("*.py")
            if "node_modules" not in p.parts and ".venv" not in p.parts
        ],
        root,
    )
    full = sorted(c for c in changed if c in FULL_SUITE)
    frontend = [c for c in changed if c.startswith(FRONTEND_APPS)]
    picked: set[str] = {c for c in changed if c in tests}

    changed_mods = {m for m in (module_name(c, root) for c in changed) if m}
    src_users, test_users = _import_index(changed_mods, sources, tests, root)

    for path in changed:
        if path.startswith(FRONTEND_APPS) and not path.endswith(".md"):
            continue
        needles = [path]
        name = Path(path).name
        # A basename is a fair needle only when it is distinctive.
        if len(name) >= 12 and name not in {"__init__.py", "package.json", "README.md"}:
            needles.append(name)
        picked |= {t for t, body in tests.items() if any(n in body for n in needles)}

        dotted = module_name(path, root)
        if not dotted:
            continue
        picked |= test_users.get(dotted, set())
        # One level up: source modules that import the changed module.
        for upstream in src_users.get(dotted, set()) - {dotted}:
            users = test_users.get(upstream, set())
            if len(users) <= HUB_LIMIT:
                picked |= users

    if any(c.startswith("infra/postgres/") and c.endswith(".sql") for c in changed):
        picked.add("tests/unit/test_migration_prefixes.py")
    return sorted(picked), full, frontend


def frontend_commands(frontend: list[str]) -> list[str]:
    """One `vitest related` command per Next app, with that app's files."""
    out = []
    for app in FRONTEND_APPS:
        rel = [f[len(app) :] for f in frontend if f.startswith(app)]
        if rel:
            out.append(
                f"cd {app.rstrip('/')} && npx tsc --noEmit && npx vitest related --run "
                + " ".join(rel)
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--run", action="store_true", help="run pytest on the selection")
    ap.add_argument("files", nargs="*", help="changed files (default: the diff against --base)")
    args, extra = ap.parse_known_args(argv)

    changed = [f.replace("\\", "/") for f in args.files] or changed_files(args.base)
    picked, full, frontend = select(changed)

    print(f"{len(changed)} changed files -> {len(picked)} test files")
    for t in picked:
        print(f"  {t}")
    for cmd in frontend_commands(frontend):
        print(f"frontend: {cmd}")
    if full:
        print(f"FULL: {', '.join(full)} changed, so every test can be affected")

    if not args.run:
        return 0
    target = [str(TESTS), f"--ignore={LOCAL_HANG}"] if full else picked
    if not target:
        print("nothing to run")
        return 0
    return subprocess.call([sys.executable, "-m", "pytest", *target, "-q", *extra], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
