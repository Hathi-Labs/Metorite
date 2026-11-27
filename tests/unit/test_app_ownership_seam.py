"""SW-45 — ``authority.py`` is the one reader of the app lead.

Spec: ``project-docs/specs/agent_coordination.md`` §3.10 rule 4 ("The app
lead is ``pm_projects.lead``, read through ``authority.py``"), §3.10.1 and
SW-45 (Fence).

The fence reads the source of every Python module under ``apps/`` and
``packages/``, test modules excepted. It reads the SQL text only: each string
constant that is not a docstring. A module may not name the lead column of a
project outside :data:`ALLOWED`:

* rule A: any string names ``pm_projects.lead``.
* rule B: in a module whose strings name ``pm_projects``, a string compares
  or reads the lead: ``lead =``, ``lower(lead)`` or ``<alias>.lead``.

A Python assignment such as ``lead = row.lead`` is no SQL, so the fence does
not read it. Rule B needs ``pm_projects`` in the module, so the ``lead`` of
another table (``brief_runs``, the CRM) does not count.

``ALLOWED`` holds the public walk SQL of ``authority.py`` and one older
reader, each with a reason. A new reader of the lead calls
``authority.own_apps``, ``authority.app_owner`` or ``authority.led_projects``.

Mutations (R7). Each one turned the named test red.

* S1. Add ``"SELECT id FROM pm_projects WHERE lead = :me"`` to
  ``routes/projects/claim_scope.py``. ``test_no_module_outside_the_seam_reads_the_lead``.
* S2. Rename ``authority.OWN_APPS_SQL``. ``test_each_allowed_symbol_exists``.
* S3. Drop rule B from :func:`_hits`. ``test_the_scan_finds_a_lead_comparison``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPES = ("apps", "packages")
SKIP_PARTS = frozenset({"tests", "node_modules", ".venv", "__pycache__", "build", "dist"})

AUTHORITY = "apps/services/gateway/gateway/routes/projects/authority.py"
PERSONAL = "apps/services/gateway/gateway/routes/projects/personal.py"

#: ``(module, top-level symbol) -> reason``. The symbol is the name that the
#: SQL string sits in: a module constant, a function or a class.
ALLOWED: dict[tuple[str, str], str] = {
    (AUTHORITY, "LED_SUBTREE_SQL"): "The public walk SQL of the seam: each project that the member leads, and each project below one.",
    (AUTHORITY, "OWN_APPS_SQL"): "The public walk SQL of the seam (SW-45): the own apps, with the nearest app of each project.",
    (AUTHORITY, "APP_LEADS_SQL"): "The public walk SQL of the seam (SW-45): the leads of a project and its ancestors, nearest first.",
    (PERSONAL, "led_projects_for"): (
        "WS-39 S6e `/projects/my/led`, older than SW-45. It lists the projects that the member leads"
        " directly, with no walk, for My Tasks. It decides no authority and no app ownership."),
}

_RULE_A = re.compile(r"\bpm_projects\.lead\b", re.IGNORECASE)
_RULE_B = re.compile(
    r"(?<![\w.])lead\s*=|\blower\(\s*(?:\w+\.)?lead\s*\)|\b\w+\.lead\b", re.IGNORECASE)
_PROJECTS = re.compile(r"\bpm_projects\b", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the docstring nodes of *tree*."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                found.add(id(body[0].value))
    return found


def _top_name(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            return target.id
    return "<module>"


def _strings(source: str) -> list[tuple[str, str]]:
    """``(top-level symbol, text)`` for each string constant that is not a
    docstring. The pieces of one f-string are one text."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: list[tuple[str, str]] = []
    for top in tree.body:
        name = _top_name(top)
        parts_of: set[int] = set()
        for node in ast.walk(top):
            if isinstance(node, ast.JoinedStr):
                pieces = [v.value for v in node.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                parts_of.update(id(v) for v in node.values)
                found.append((name, " ".join(pieces)))
        for node in ast.walk(top):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip and id(node) not in parts_of):
                found.append((name, node.value))
    return found


def _hits(source: str) -> list[tuple[str, str]]:
    """``(symbol, text)`` of each string that names the lead of a project."""
    strings = _strings(source)
    projects = any(_PROJECTS.search(t) for _, t in strings)
    return [(name, t) for name, t in strings
            if _RULE_A.search(t) or (projects and _RULE_B.search(t))]


def _modules() -> list[Path]:
    out: list[Path] = []
    for scope in SCOPES:
        for path in (ROOT / scope).rglob("*.py"):
            rel = path.relative_to(ROOT)
            if SKIP_PARTS.intersection(rel.parts) or path.name.startswith("test_"):
                continue
            out.append(path)
    return out


def test_no_module_outside_the_seam_reads_the_lead():
    bad: list[str] = []
    for path in _modules():
        rel = path.relative_to(ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            hits = _hits(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name, text_ in hits:
            if (rel, name) not in ALLOWED:
                bad.append(f"{rel}::{name}: {text_.strip()[:100]!r}")
    assert not bad, (
        "Read the app lead through authority.py (own_apps, app_owner or"
        " led_projects), not with SQL of your own:\n" + "\n".join(bad))


def test_each_allowed_symbol_exists():
    """A stale entry hides nothing and must go."""
    for (rel, name), reason in ALLOWED.items():
        assert reason.strip(), (rel, name)
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert name in {n for n, _ in _hits(source)}, (
            f"{rel}::{name} holds no SQL that reads the lead. Remove its entry.")


def test_the_seam_holds_its_public_walk_sql():
    from gateway.routes.projects import authority

    for name in ("LED_SUBTREE_SQL", "OWN_APPS_SQL", "APP_LEADS_SQL"):
        assert isinstance(getattr(authority, name), str), name


def test_the_scan_finds_a_lead_comparison():
    sql = 'X = "SELECT id FROM pm_projects WHERE lead = :me"\n'
    assert _hits(sql) == [("X", "SELECT id FROM pm_projects WHERE lead = :me")]
    assert _hits('def f():\n    return "SELECT * FROM pm_projects p WHERE lower(p.lead) = :w"\n')
    assert _hits('Y = "SELECT pm_projects.lead FROM pm_projects"\n')
    assert _hits('def g(x):\n    return f"SELECT p.lead FROM pm_projects p WHERE p.id = {x}"\n')


def test_the_scan_ignores_python_and_other_tables():
    assert not _hits("lead = row.lead\n")
    assert not _hits('S = "UPDATE brief_runs SET lead = :lead"\n')
    assert not _hits('def f():\n    """pm_projects.lead is the app lead."""\n')
    assert not _hits('Z = "SELECT lead_id FROM pm_projects"\n')
