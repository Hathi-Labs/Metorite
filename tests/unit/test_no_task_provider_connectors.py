"""D52 fence — no task connector comes back, and the two tripwires stay armed.

Board **WS-39 S1** · decision **D52** (`work_plan.md` §3) · owning section
`project-docs/specs/project_management_app.md` §12.

**Re-cut 2026-09-23 (S8 PR 1, `my_tasks_cutover.md` §5 S8).** The connector
registry lived in `routes/tasks/providers.py`, and the broker writer map in
`routes/tasks/broker_handlers.py`. S8 PR 1 deleted both modules, so the four
tests that read them became one: the modules stay deleted. The two tripwires
below read code that survives, and they stay exactly as they were.

Reversing D52 is an owner decision recorded by name in `work_plan.md` §3. The
correct response to this test failing is to read that section, not to edit
this file.
"""
from __future__ import annotations

import pathlib

_TASKS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "tasks"
)


def test_the_connector_modules_stay_deleted() -> None:
    """A connector needs a registry and a broker writer. Both were deleted
    with the retired store. A revival is a second write path into the task
    store, which D53's one-store decision exists to prevent."""
    for name in ("providers.py", "broker_handlers.py", "sync.py", "accounts.py"):
        assert not (_TASKS / name).exists(), (
            f"routes/tasks/{name} is back. D52 retired the task connectors "
            "and S8 PR 1 deleted this module. Read work_plan.md §3 D52."
        )


def test_the_workflow_registry_has_no_destructive_tool() -> None:
    """The TRIPWIRE the S1 cleanup commit promised, and did not write.

    ⚠️ **This is a tripwire, not an invariant: it is EXPECTED to go red one
    day, and going red is the whole service it performs.**

    `clickup.create_task` was the only `destructive=True` `WorkflowToolSpec`, so
    D52 left the workflow registry with none — and the graph validator reads its
    refusal set from the LIVE registry (`destructive_action_names()`). With that
    set empty, "a destructive node needs an approval gate upstream" **cannot
    fire**, and the eval fixture that exercised it (`gate_write_without_approval
    .json`) was deleted because a fixture that cannot fail is worse than none.

    So when the next destructive tool is registered, this test fails — and its
    message is the instruction: **restore that fixture**, because from that
    moment the approval-gate rule is live again and nothing else is checking it.

    Reads the registry through the module's own accessor rather than the dict,
    so a spec registered by any path is seen.
    """
    from gateway.routes.workflows import tools as wf_tools

    destructive = wf_tools.destructive_action_names()
    assert destructive == set(), (
        f"a destructive workflow tool is registered again: {sorted(destructive)}. "
        "The graph validator's approval-gate rule is therefore LIVE again, and "
        "nothing exercises it: restore `evals/trajectories/` fixture "
        "`gate_write_without_approval.json` (deleted by D52 because it could "
        "not fail against an empty registry) and re-point it at this tool. "
        "Then delete this tripwire — it has done its job."
    )


def test_no_module_imports_the_deleted_clickup_packages() -> None:
    """The deleted packages are not imported anywhere in the tree.

    Catches the half-removal: a connector deleted but still imported is an
    ImportError at startup, and `main.py` swallowed exactly that class of error
    behind a bare `except Exception: pass` before D52 removed the block.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    dead = (
        "ingestion.sources.clickup",
        "skill_clickup_sync",
        "gateway.routes.projects.import_clickup",
        "gateway.routes.projects.import_tasks",
        "gateway.routes.projects.mapping",
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if parts & {".venv", "node_modules", "__pycache__", ".git"}:
            continue
        if path.name == pathlib.Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in dead:
            if name in text:
                offenders.append(f"{path.relative_to(root)} → {name}")
    assert offenders == [], (
        "references to packages deleted by D52 remain:\n  "
        + "\n  ".join(offenders)
    )
