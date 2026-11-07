"""H-328 part (b): only named callers pass an org to ``start_run`` or ``resume_run``.

**The risk.** A run that carries an org binds its writes to that org, and it
can send mail or WhatsApp and spend credits. So the code that picks the org of
a run is a security decision. Before part (b), only ``run_published_workflow``
passed one. Now the scans may too. A new caller must not appear without a
review, so this file parses ``routes/workflows/`` and names every caller.

**What fails.** Any function that is not in :data:`ALLOWED` and that passes an
org keyword (``organization_id`` and its spellings) or a ``**`` expansion to
``start_run`` or ``resume_run``. A stale entry fails too, so the list cannot
rot. Paths that part (c) owns (``triggers.py``, ``hooks.py``, the manual Run in
``runs.py``) pass no org today, and they stay outside this list until part (c).

Mutation (R7): add ``start_run(..., organization_id=x)`` to any other function
under ``routes/workflows/`` and this test goes red.

No Postgres: the source is parsed, not run.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "workflows"

#: The functions that start or resume a run.
_TARGETS = frozenset({"start_run", "resume_run"})

#: The keyword names that carry an org.
_ORG_KEYWORDS = frozenset({"organization_id", "organization", "org", "org_id", "tenant_id"})

#: (file, function, target) -> why this caller may pass an org.
ALLOWED: dict[tuple[str, str, str], str] = {
    ("service.py", "run_published_workflow", "start_run"):
        "The agent door. The org is the agent run's binding, taken from the "
        "orchestrator and never from tool input (R5, R11).",
    ("scheduler.py", "_scan_org", "start_run"):
        "The schedule scan. The org is the org of the WORKFLOW ROW, read in "
        "that org's own session. `_scan_once` checks the allowlist first.",
    ("scheduler.py", "scan_due_waits", "resume_run"):
        "The wait scan. The org is the org of the RUN ROW, read in that org's "
        "own session. The scan checks the allowlist for the org first.",
    ("scheduler.py", "scan_decided_pauses", "resume_run"):
        "The decided scan (AUTO-7b). The org is the org of the RUN ROW, read in "
        "that org's own session. Only the org loop of `scan_due_waits` calls it, "
        "after the allowlist check. It resumes only on a yes of the decider, "
        "read from the decision row.",
    ("broker_handlers.py", "_resume_run_handler", "resume_run"):
        "The approval door. The org is the approver's bound tenant, which RLS "
        "makes equal to the org of the run row. A person approved the run.",
}


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _passes_org(call: ast.Call, target: str) -> bool:
    for kw in call.keywords:
        if kw.arg is None or kw.arg in _ORG_KEYWORDS:  # None is `**kwargs`
            return True
    # `resume_run(run_id, <more positional>)` could carry an org by position.
    return target == "resume_run" and len(call.args) > 1


def _callers(source: str) -> set[tuple[str, str]]:
    """(enclosing function, target) for each call that passes an org."""
    found: set[tuple[str, str]] = set()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _fn(self, node: ast.AST) -> None:
            self.stack.append(node.name)  # type: ignore[attr-defined]
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

        def visit_Call(self, node: ast.Call) -> None:
            target = _callee(node)
            if target in _TARGETS and _passes_org(node, target):
                found.add((self.stack[-1] if self.stack else "<module>", target))
            self.generic_visit(node)

    _Visitor().visit(ast.parse(source))
    return found


def _scan_package() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for path in sorted(_WORKFLOWS.rglob("*.py")):
        rel = path.relative_to(_WORKFLOWS).as_posix()
        for fn, target in _callers(path.read_text(encoding="utf-8")):
            out.add((rel, fn, target))
    return out


def test_only_the_named_callers_pass_an_org_to_start_run_or_resume_run() -> None:
    found = _scan_package()
    stray = sorted(found - set(ALLOWED))
    assert stray == [], (
        f"these functions pass an org to start_run or resume_run, and the "
        f"allow list does not name them: {stray}. A run with an org binds its "
        f"writes and can send and spend. Add the caller to ALLOWED with a "
        f"reason, after a review of where its org comes from."
    )


def test_every_allowed_caller_still_exists() -> None:
    stale = sorted(set(ALLOWED) - _scan_package())
    assert stale == [], f"stale entries in ALLOWED: {stale}"


def test_the_scanner_sees_a_forbidden_call() -> None:
    """The fence must be able to fail. Each spelling below is a caller that the
    allow list does not name."""
    for call in (
        "start_run(workflow_id=w, organization_id=org)",
        "service.start_run(workflow_id=w, org=org)",
        "start_run(**kw)",
        "resume_run(run_id, organization_id=org)",
        "resume_run(run_id, org)",
    ):
        source = f"async def rogue():\n    await {call}\n"
        assert _callers(source), call
    clean = "async def fine():\n    await start_run(workflow_id=w)\n    await resume_run(rid)\n"
    assert _callers(clean) == set()


def test_part_c_paths_pass_no_org_yet() -> None:
    """``triggers.py``, ``hooks.py`` and ``runs.py`` are part (c). They start
    runs with no org today, so the scan finds no org call in them."""
    found_files = {rel for rel, _fn, _t in _scan_package()}
    assert not found_files & {"triggers.py", "hooks.py", "runs.py"}, found_files
