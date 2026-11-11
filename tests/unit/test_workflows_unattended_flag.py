"""H-328 part (b): the org allowlist of unattended workflow runs.

``WORKFLOW_UNATTENDED_ORGS`` is the owner's switch. It ships empty. A schedule
tick, a matured wait and the startup sweep act only for a listed org. This file
fences the reader, the settings default, the owner-only guard, the example
file, and the shape of each scan: it must check the list for each org BEFORE it
reads anything or starts anything (R7). The database cases are in
``test_workflows_lifecycle_rls_r8.py``.

No Postgres: the reader is pure, and the scans are read as source.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_common import env_guard
from acb_common.settings import Settings
from gateway.routes.workflows import unattended

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "workflows"


def _listed(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(
        unattended, "get_settings",
        lambda: SimpleNamespace(workflow_unattended_orgs=raw),
    )


# ── The reader ───────────────────────────────────────────────────────────────


def test_the_setting_ships_empty() -> None:
    assert Settings.model_fields["workflow_unattended_orgs"].default == ""


def test_an_empty_list_allows_no_org(monkeypatch) -> None:
    _listed(monkeypatch, "")
    assert unattended.unattended_orgs() == frozenset()
    assert unattended.org_allowed("0d4b8f5e-0000-4000-8000-000000000001") is False


def test_no_org_is_never_allowed(monkeypatch) -> None:
    _listed(monkeypatch, "org-a")
    assert unattended.org_allowed(None) is False
    assert unattended.org_allowed("") is False


def test_a_comma_list_is_trimmed_and_case_blind(monkeypatch) -> None:
    _listed(monkeypatch, " Org-A , org-b,, ")
    assert unattended.unattended_orgs() == frozenset({"org-a", "org-b"})
    assert unattended.org_allowed("ORG-A") is True
    assert unattended.org_allowed(" org-b ") is True
    assert unattended.org_allowed("org-c") is False


def test_a_star_is_not_a_wildcard(monkeypatch) -> None:
    _listed(monkeypatch, "*")
    assert unattended.unattended_orgs() == frozenset()
    assert unattended.org_allowed("org-a") is False
    _listed(monkeypatch, "*,org-a")
    assert unattended.unattended_orgs() == frozenset({"org-a"})
    assert unattended.org_allowed("org-b") is False


def test_a_missing_setting_allows_no_org(monkeypatch) -> None:
    monkeypatch.setattr(unattended, "get_settings", lambda: SimpleNamespace())
    assert unattended.org_allowed("org-a") is False


# ── Owner-only, and the example file ─────────────────────────────────────────


def test_only_the_owner_writes_the_list() -> None:
    assert "WORKFLOW_UNATTENDED_ORGS" in env_guard.PLATFORM_ENV_NAMES
    assert env_guard.is_platform_env("WORKFLOW_UNATTENDED_ORGS") is True


def test_the_example_file_ships_the_list_empty() -> None:
    body = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^WORKFLOW_UNATTENDED_ORGS=\s*$", body, re.M), (
        "`.env.example` must hold `WORKFLOW_UNATTENDED_ORGS=` with no value"
    )


def test_the_reader_module_is_the_only_reader() -> None:
    """One reader: no other module of the apps or the packages reads the
    setting, by attribute, by ``getattr`` or from the environment. A comment or
    a docstring may name it. ``settings.py`` only defines it, and
    ``env_guard.py`` refuses a write by its exact name."""
    allowed = {
        "apps/services/gateway/gateway/routes/workflows/unattended.py",
        "packages/acb_common/acb_common/env_guard.py",
    }
    names = {"workflow_unattended_orgs"}
    found: set[str] = set()
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            if "workflow_unattended_orgs" not in body.lower():
                continue
            try:
                tree = ast.parse(body)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                hit = (
                    isinstance(node, ast.Attribute) and node.attr in names
                ) or (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.strip().lower() in names
                )
                if hit:
                    found.add(path.relative_to(_ROOT).as_posix())
    assert found == allowed, found ^ allowed


# ── The shape of each scan and sweep (R7) ────────────────────────────────────


def _function(filename: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((_WORKFLOWS / filename).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{filename} has no async function {name}")


def _calls(node: ast.AST, dotted: str) -> list[ast.Call]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if f"{func.value.id}.{func.attr}" == dotted:
                    out.append(sub)
            elif isinstance(func, ast.Name) and func.id == dotted:
                out.append(sub)
    return out


@pytest.mark.parametrize(
    ("filename", "name"),
    [
        ("scheduler.py", "_scan_once"),
        ("scheduler.py", "scan_due_waits"),
        ("service.py", "reconcile_orphaned_runs"),
    ],
)
def test_each_scan_checks_the_list_for_each_org_before_it_reads(filename, name) -> None:
    """A scan loops over the orgs, and its loop body opens with
    ``if not unattended.org_allowed(org): continue``. No statement runs before
    that check, so an unlisted org is never read."""
    fn = _function(filename, name)
    # `orgs = await _organization_ids()` then `for org in orgs` is one shape.
    names = {
        t.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and _calls(n.value, "_organization_ids")
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    loops = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.For)
        and (
            _calls(n.iter, "_organization_ids")
            or (isinstance(n.iter, ast.Name) and n.iter.id in names)
        )
    ]
    assert len(loops) == 1, f"{name} must hold one loop over _organization_ids()"
    first = loops[0].body[0]
    assert isinstance(first, ast.If), f"{name}: the loop must open with the check"
    assert _calls(first.test, "unattended.org_allowed"), f"{name}: no allowlist check"
    assert isinstance(first.test, ast.UnaryOp) and isinstance(first.test.op, ast.Not)
    assert isinstance(first.body[0], ast.Continue), f"{name}: an unlisted org must skip"


@pytest.mark.parametrize("name", ["record_skipped_run", "evaluate_automation_health"])
def test_each_sweep_that_takes_an_org_checks_the_list(name) -> None:
    fn = _function("service.py", name)
    checks = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and _calls(n.test, "unattended.org_allowed")
    ]
    assert len(checks) == 1, f"{name} must check the allowlist once"
    # The check comes before the first session opens.
    session_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in {"_run_session", "_get_db"}
    ]
    assert session_lines and checks[0].lineno < min(session_lines)
