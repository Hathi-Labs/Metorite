"""Pick the unit tests a change can affect, for the fast inner loop.

    uv run python scripts/affected_tests.py            # list them
    uv run python scripts/affected_tests.py --run      # run them with pytest
    uv run python scripts/affected_tests.py --base origin/main --run

``--gate`` is the merge gate's selection: pr-check runs these tests, and the
full suite when the diff is FULL (specs/ci_velocity.md, D176). The plain list
and ``--run`` are for the inner loop, so that an agent, or a person, does not
run all 27,000 tests locally to check a 3-file change.

A test file is picked when it:

1. changed itself,
2. names a changed file by its path (many tests read docs, workflows, SQL),
3. imports a changed module (``import a.b``, ``from a import b``, an alias,
   or a dotted string such as ``patch("a.b.fn")``), or
4. imports a module that imports a changed module (one level, see HUB_LIMIT),
5. lists a folder of the changed file (``glob``, ``rglob``, ``iterdir``), or
6. scans the whole tree, when any Python file changed (tests/ and workbench/ too).

Imports are read with ``ast``, not a regex: a regex lost 38% of the real
import pairs to multi-line imports and ``as`` aliases (measured 2026-10-16).

A change to a file that every test depends on (``pyproject.toml``, a
``conftest.py``, ``uv.lock``) prints FULL, and ``--run`` then runs the whole
suite. Frontend files are not Python's business: the script prints the
``vitest related`` command for each Next app, which does the same job there.

``--gate`` is the PR tier's selection (CIV-2, docs/specs: ci_velocity.md). It
prints one JSON object, ``{"full": bool, "files": [...], "reasons": [...]}``,
for ``git diff BASE...HEAD``. It fails open: any error gives ``full: true``.
It uses the standard library only, because the CI ``changes`` job has plain
``python3`` and no ``uv``.

``--r8`` prints the R8 files, one a line, without the process fences
(CIV-4b). deploy.yml's R8 gate runs them on each release. ``r8_files()`` is
the one rule for an R8 file, and ``--gate`` uses it for a migration change.

Fence: tests/unit/test_affected_tests.py.
"""

from __future__ import annotations

import argparse
import ast
import json
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
# CLAUDE.md section 6: these suites hang on a local run (WS-9, WS-21). CI runs
# them. A picked list and a full run both drop every entry (CIV-15).
LOCAL_HANGS = (
    "tests/unit/test_memory_integration.py",
    "tests/unit/test_calendar_entries_r8.py",
    "tests/unit/test_calendar_plan_decide.py",
    "tests/unit/test_calendar_task_source.py",
)
# Seconds. A test that runs longer fails with a stack trace (pytest-timeout).
TEST_TIMEOUT = 600


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
    # The import root is the folder of the nearest pyproject.toml below the
    # repo root (apps/services/gateway, packages/acb_common). A folder with no
    # __init__.py inside a package (gateway/routes) is a namespace package. An
    # __init__ chain stops there and gives a short name that no test imports
    # (CIV-3 review, P0).
    anc = p.parent
    while anc != root and anc != anc.parent:
        if (anc / "pyproject.toml").is_file():
            rel = list(p.relative_to(anc).with_suffix("").parts)
            if rel and rel[-1] == "__init__":
                rel.pop()
            return ".".join(rel) if rel else None
        anc = anc.parent
    parts = [p.stem] if p.name != "__init__.py" else []
    d = p.parent
    while (d / "__init__.py").is_file():
        parts.insert(0, d.name)
        d = d.parent
    return ".".join(parts) if parts else None


def module_names(path: str, root: Path = ROOT) -> set[str]:
    """Every dotted name a test could import this file by.

    A file outside any pyproject root also has its repo-root-relative name:
    `evals/x/y.py` is `evals.x.y` when `evals` has no `__init__.py` (CIV-3
    review round 2, P1). The short name stays, so no match is lost.
    """
    names: set[str] = set()
    first = module_name(path, root)
    if first:
        names.add(first)
    p = root / path
    if p.suffix == ".py":
        anc = p.parent
        held = False
        while anc != root and anc != anc.parent:
            if (anc / "pyproject.toml").is_file():
                held = True
                break
            anc = anc.parent
        if not held:
            rel = list(Path(path).with_suffix("").parts)
            if rel and rel[-1] == "__init__":
                rel.pop()
            if rel:
                names.add(".".join(rel))
    return names


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


def _composed_path_readers(path: str, tests: dict[str, str]) -> set[str]:
    """Tests that build the path from parts, as in ``DIR / "guarded.py"``.

    The full path and a short basename are not in such a test. It holds the
    QUOTED basename and a folder of the path. The folder is the parent in a
    path context (``"app"``, ``/app/``) or the grandparent as plain text.
    A false hit costs only time (CIV-3 review round 3, P1).
    """
    p = Path(path)
    name = p.name
    if p.suffix != ".py" or len(p.parts) < 3:
        return set()
    parent, grand = p.parent.name, p.parent.parent.name
    quoted = (f'"{name}"', f"'{name}'")
    ctx = re.compile(rf"""["'/]{re.escape(parent)}["'/]""")
    # A name written as part of a path, as in "google_drive/auth.py" (round 4).
    slashed = re.compile(rf"""["'/]{re.escape(name)}["']""")
    return {
        t
        for t, body in tests.items()
        if (any(q in body for q in quoted) or slashed.search(body))
        and (ctx.search(body) or grand in body)
    }


# Every way a unit test lists files it never names (CIV-3 round 5, owner option A).
# A test with one reads the source tree, so any Python change selects it.
_SCAN_IDIOM = re.compile(
    r"\.r?glob\(|\bi?glob\(|\.iterdir\(|\bos\.walk\(|(?<!ast)(?<!os)\.walk\(\s*\)"
    r"|\blistdir\(|\bscandir\(|\bls-files\b|\bls-tree\b|\bgit\W+grep\b|\bfnmatch"
    r"|\biter_modules\(|\bwalk_packages\(|\bimportlib\.resources\b"
    r"""|["'](?:grep|rg)["']\s*[,\]]"""
)


def scanning_tests(tests: dict[str, str]) -> set[str]:
    """Every test that lists files by a scan idiom (CIV-3 round 5).

    No text rule can say which file such a test reads. A change to any Python
    file selects all of them. A docs-only change selects none.
    A false hit costs only time. Helpers under tests/unit/_*.py scan only SQL,
    TypeScript and JSON, so an import of one does not make a test a scanner.
    """
    return {t for t, body in tests.items() if _SCAN_IDIOM.search(body)}


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

    changed_mods = {m for c in changed for m in module_names(c, root)}
    src_users, test_users = _import_index(changed_mods, sources, tests, root)

    if any(c.endswith(".py") for c in changed):
        picked |= scanning_tests(tests)

    for path in changed:
        if path.startswith(FRONTEND_APPS) and not path.endswith(".md"):
            continue
        needles = [path]
        name = Path(path).name
        # A basename is a fair needle only when it is distinctive.
        if len(name) >= 12 and name not in {"__init__.py", "package.json", "README.md"}:
            needles.append(name)
        picked |= {t for t, body in tests.items() if any(n in body for n in needles)}
        picked |= _composed_path_readers(path, tests)

        for dotted in module_names(path, root):
            picked |= test_users.get(dotted, set())
            # One level up: source modules that import the changed module.
            for upstream in src_users.get(dotted, set()) - {dotted}:
                users = test_users.get(upstream, set())
                if len(users) <= HUB_LIMIT:
                    picked |= users
                # CIV-18. A test that imports an aggregating package's
                # `router` mounts every route module the package registers,
                # whatever the package's size. `gateway.routes.projects` has
                # 259 test importers (a hub), but only 35 import its router,
                # and the R8 suites are among them. Without this, a change to
                # `projects/personal.py` skipped `test_personal_privacy_r8.py`.
                picked |= test_users.get(f"{upstream}.router", set())

    if any(c.startswith("infra/postgres/") and c.endswith(".sql") for c in changed):
        picked.add("tests/unit/test_migration_prefixes.py")
    return sorted(picked), full, frontend


# ── --gate: the PR tier (CIV-2) ────────────────────────────────────────────
# A change to one of these can affect any test, so the PR runs the full suite.
GATE_FULL_NAMES = {
    "pyproject.toml",
    "uv.lock",
    ".test_durations",
    "scripts/affected_tests.py",
    "scripts/fences.py",
    "scripts/ci_unit_guard.py",
    "scripts/ci_local.sh",
}
GATE_FULL_PREFIXES = (".github/", "scripts/ci_pc/", "packages/acb_common/")
GATE_DB_MARK = "_DB_GATE"
# An R8 suite is a test file that builds a real database from a ladder, or
# names a database URL variable. 109 of 339 ladder suites carry no _DB_GATE.
CONSOLE_R8_MARKS = ("_customer_console_ladder", "CUSTOMER_CONSOLE_DATABASE_URL")
R8_MARKS = (
    GATE_DB_MARK,
    "_tenant_ladder",
    "TENANT_LADDER_DATABASE_URL",
    *CONSOLE_R8_MARKS,
)


# ── --r8: the R8 files (CIV-2 and CIV-4b) ──────────────────────────────────
# A test file that names one of these variables, or imports a test helper
# that names one, skips when the variable is unset. CI sets both.
R8_VARS = ("CUSTOMER_CONSOLE_DATABASE_URL", "TENANT_LADDER_DATABASE_URL")


def r8_files(root: Path = ROOT) -> list[str]:
    """The unit test files that need a real database (R8).

    A file is an R8 file when its text holds ``_DB_GATE``, names an R8
    variable, or names a ``tests/unit/_*.py`` helper that names one.

    Measured 2026-11-05 on a deploy.yml run with no database: 454 files
    skipped because an R8 variable was unset. ``_DB_GATE`` alone found 348
    of them. This rule found all 454. The helper today is ``_tenant_ladder``,
    which holds the gate for about 290 files that name no variable.
    """
    helpers = [
        p.stem
        for p in sorted((root / TESTS).glob("_*.py"))
        if any(v in p.read_text(encoding="utf-8", errors="replace") for v in R8_VARS)
    ]
    marks = [re.escape(GATE_DB_MARK), *(re.escape(v) for v in R8_VARS)]
    marks += [rf"(?<!\w){re.escape(h)}(?!\w)" for h in helpers]
    found = re.compile("|".join(marks))
    tests = _texts(sorted((root / TESTS).glob("test_*.py")), root)
    return sorted(t for t, body in tests.items() if found.search(body))


def r8_gate_files(root: Path = ROOT) -> list[str]:
    """The R8 files that deploy.yml's R8 gate runs: no PROCESS fence.

    A process fence is advisory, so it must not hold a release. pytest's
    ``--ignore`` does not drop a file that is named on the command line
    (pytest 9.1, measured 2026-11-05), so the list leaves them out.
    """
    import fences  # scripts/fences.py, standard library only

    data = fences.load() if root == ROOT else json.loads(
        (root / "tests" / "fences.json").read_text(encoding="utf-8")
    )
    process = set(data["python"]["process"])
    return [p for p in r8_files(root) if p not in process]


def gate_changed_files(base: str, root: Path = ROOT) -> list[str]:
    """The files of ``git diff BASE...HEAD``. CI has no untracked files.

    ``--no-renames``: a rename then lists the old path too, so the tests that
    import the old module are still selected."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(n.strip().replace("\\", "/") for n in out.stdout.splitlines() if n.strip())


def _full_reason(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if name == "conftest.py":
        return f"{path}: a conftest.py can change every test"
    if path.startswith("tests/") and name.startswith("_"):
        return f"{path}: a shared test helper"
    if path in GATE_FULL_NAMES or path.startswith(GATE_FULL_PREFIXES):
        return f"{path}: shared config, CI or the DB seam"
    return None


def _safety_fences(root: Path) -> list[str]:
    import fences  # scripts/fences.py, standard library only

    data = fences.load() if root == ROOT else json.loads(
        (root / "tests" / "fences.json").read_text(encoding="utf-8")
    )
    return [p for p in data["python"]["safety"] if (root / p).is_file()]


def _gate(changed: list[str], root: Path) -> dict:
    reasons = [r for r in (_full_reason(c) for c in changed) if r]
    tests = _texts(sorted((root / TESTS).glob("test_*.py")), root)
    picked: set[str] = set(_safety_fences(root))

    py = [c for c in changed if c.endswith(".py") and not c.startswith(FRONTEND_APPS)]
    other = [c for c in changed if c not in py]
    # A Python file in a Next app skips `select`, but a scanner still reads it.
    if any(c.endswith(".py") for c in changed):
        picked |= scanning_tests(tests)
    if py:
        picked |= set(select(py, root)[0])
        # A hub: a changed module that more than HUB_LIMIT test files import.
        sources = _texts(
            [
                p
                for top in ("apps", "packages")
                for p in (root / top).rglob("*.py")
                if "node_modules" not in p.parts and ".venv" not in p.parts
            ],
            root,
        )
        mods = {m for c in py for m in module_names(c, root)}
        _, test_users = _import_index(mods, sources, tests, root)
        for m in sorted(mods):
            n = len(test_users.get(m, set()))
            if n > HUB_LIMIT:
                reasons.append(f"{m}: {n} test files import it, more than {HUB_LIMIT}")
    # A file that is not Python: tests that name its path. No import tracing.
    for path in other:
        picked |= {t for t, body in tests.items() if path in body}
    if any(c.startswith("infra/postgres/") for c in changed):
        # The release gate's R8 list (CIV-4b), and CIV-3's marks: a suite
        # that names a ladder helper counts too. The union keeps both.
        picked |= set(r8_files(root))
        picked |= {t for t, body in tests.items() if any(m in body for m in R8_MARKS)}
        picked.add("tests/unit/test_migration_prefixes.py")
    if any(c.startswith("infra/customer_console/") for c in changed):
        picked |= {
            t
            for t, body in tests.items()
            if t.rsplit("/", 1)[-1].startswith("test_customer_console_")
            or any(m in body for m in CONSOLE_R8_MARKS)
        }
    files = sorted(p for p in picked if (root / p).is_file())
    return {"full": bool(reasons), "files": files, "reasons": reasons}


def gate(changed: list[str], root: Path = ROOT) -> dict:
    """The PR tier's selection. Any error inside the selector gives full."""
    try:
        return _gate(changed, root)
    except Exception as exc:  # fail open on purpose
        return {"full": True, "files": [], "reasons": [f"selector error: {exc!r}"]}


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


def drop_local_hangs(picked: list[str]) -> list[str]:
    """Remove the known local hangers from a picked list, and print each one."""
    kept = []
    for t in picked:
        if t.replace("\\", "/") in LOCAL_HANGS:
            print(f"skipped (known local hang, CI runs it): {t}")
        else:
            kept.append(t)
    return kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--run", action="store_true", help="run pytest on the selection")
    ap.add_argument("--gate", action="store_true", help="print the PR tier selection as JSON")
    ap.add_argument(
        "--r8", action="store_true", help="print the R8 files of the release gate, one a line"
    )
    ap.add_argument("files", nargs="*", help="changed files (default: the diff against --base)")
    args, extra = ap.parse_known_args(argv)

    if args.r8:
        # No fail-open here. deploy.yml stops on an error or an empty list,
        # because pytest with no path runs the whole tree.
        print("\n".join(r8_gate_files()))
        return 0

    if args.gate:
        try:
            changed = [f.replace("\\", "/") for f in args.files] or gate_changed_files(args.base)
            out = gate(changed)
        except Exception as exc:  # fail open on purpose
            out = {"full": True, "files": [], "reasons": [f"selector error: {exc!r}"]}
        print(json.dumps(out))
        return 0

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
    if full:
        target = [str(TESTS), *(f"--ignore={h}" for h in LOCAL_HANGS)]
    else:
        target = drop_local_hangs(picked)
    if not target:
        print("nothing to run")
        return 0
    timeout = [] if any(a.startswith("--timeout") for a in extra) else [f"--timeout={TEST_TIMEOUT}"]
    return subprocess.call(
        [sys.executable, "-m", "pytest", *target, "-q", *timeout, *extra], cwd=ROOT
    )


if __name__ == "__main__":
    sys.exit(main())
