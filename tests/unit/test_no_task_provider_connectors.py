"""D52 fence — the task-provider connector registry stays EMPTY.

Board **WS-39 S1** · decision **D52** (`work_plan.md` §3) · owning section
`project-docs/specs/project_management_app.md` §12.

**What this replaces, and why it is not the same test.**
`test_task_broker_handlers.py` fenced the *inverse* invariant: every action name
routed through `providers.py::_broker_gate` had a `_WRITERS` entry, so a queued
write could always be executed on approval (BO-1a). That test carried its own
blind-walk guard —

    assert gated, "no _broker_gate call sites found — the AST walk went blind"

— which is exactly the assertion D52 makes false: there are no gate call sites
left, because there is no connector left to gate. Deleting `ClickUpProvider`
without replacing that file would have turned a real fence into a red test
somebody silences. So the fence is inverted rather than removed: it now pins the
absence.

**The failure this defends against.** An agent asked to "add an Asana connector"
(or told to re-enable ClickUp from a stale spec section) implements
`BaseTaskProvider`, adds a registry entry, and ships a **second write path into
the task store** — which is precisely what D53's one-store decision exists to
prevent. Structural, not exemplary: it asserts over the whole registry and the
whole module, so it fails for a connector nobody thought to name here.

Reversing D52 is an owner decision recorded by name in `work_plan.md` §3. The
correct response to this test failing is to read that section, not to edit this
file.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_PROVIDERS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "tasks"
    / "providers.py"
)


def test_the_connector_registry_is_empty() -> None:
    """No task-provider connector is registered. D52."""
    from gateway.routes.tasks.providers import _CONNECTORS, connector_names

    assert _CONNECTORS == {}, (
        f"a task-provider connector was registered: {sorted(_CONNECTORS)}. "
        "D52 retired ClickUp outright and Metorite is the project-management "
        "system of record — a connector here is a second write path into the "
        "task store (D53). Reversing D52 is an owner decision in work_plan.md §3."
    )
    assert connector_names() == []


def test_build_provider_refuses_every_name() -> None:
    """With an empty registry the factory refuses, rather than returning None."""
    from fastapi import HTTPException

    from gateway.routes.tasks.providers import build_provider

    for name in ("clickup", "asana", "jira", "linear", ""):
        with pytest.raises(HTTPException) as exc:
            build_provider(name, {"api_token": "t"}, "ws", "acct")
        assert exc.value.status_code == 400


def test_no_provider_subclass_survives_in_the_module() -> None:
    """`BaseTaskProvider` has no concrete implementation left in providers.py.

    Structural: walks the module's own AST rather than importing and inspecting
    ``__subclasses__``, so a class that fails to import still fails the test.
    """
    tree = ast.parse(_PROVIDERS.read_text(encoding="utf-8"))
    subclasses = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(b, ast.Name) and b.id == "BaseTaskProvider"
            for b in node.bases
        )
    ]
    assert subclasses == [], (
        f"BaseTaskProvider implementations found in providers.py: {subclasses}. "
        "See D52 — the registry is empty by decision."
    )


def test_the_broker_writer_map_is_empty() -> None:
    """No queued task write can execute, because none can be enqueued.

    The six `clickup.*` entries pointed at `_raw_*` methods on the deleted
    connector. An entry here with no gate call site behind it would be a handler
    for an action nothing can propose.
    """
    from gateway.routes.tasks import broker_handlers as bh

    assert bh._WRITERS == {}, (
        f"broker writers registered with no connector to run them: "
        f"{sorted(bh._WRITERS)}. D52."
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
