"""The fakes of the no-match hook of ``find_tools`` (SBX-1a, task 3800).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1 and §12.1. Four suites
share them: ``test_tool_gap_detection.py``, ``test_tool_compose_check.py``,
``test_user_tool_injection.py`` and ``test_sandbox_log_lines.py``. The R8
suite ``test_user_tool_registry.py`` uses the real store and the real search.

:func:`install` puts a fake at each seam that ``orchestrator.tool_gap_path``
reads at call time: the gate, the member, the features, the catalog, the
session, the library search, the decide engine and the store. Each fake
records what it saw, so a test asserts the order of the steps too.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

ORG = "0a0a0a0a-0000-4000-8000-0000000000b1"
MEMBER = "jane@gaps.test"
THREAD = "chat-sbx1a-1"
RUN = "run-sbx1a-1"
#: The need of most cases: two catalog rows and one package share its stems.
NEED = "search the knowledge notes for the supplier price list"


def holder_features() -> frozenset[str]:
    """The features of a member who holds ``feature:*``."""
    from acb_auth import build_access

    return frozenset(build_access(["feature:*"], []).allowed_features())


@dataclass
class Logs:
    """A stand-in for a structlog logger: each call as (event, fields)."""

    lines: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _add(self, event: str, **fields: Any) -> None:
        self.lines.append((event, dict(fields)))

    info = warning = error = debug = _add

    def events(self, name: str) -> list[dict[str, Any]]:
        return [fields for event, fields in self.lines if event == name]


@dataclass
class World:
    """What the fakes give, and what they saw."""

    studio_open: bool = True
    features: frozenset[str] | None = None
    typed_on: bool = True
    #: The catalog rows that ``catalog_rows`` gives.
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: The hits that ``search_visible`` gives.
    hits: list[Any] = field(default_factory=list)
    #: The picks of the engine by item id: (choice, confidence). None: no engine.
    picks: dict[str, tuple[str | None, float | None]] | None = None
    #: What ``record_gap`` answers.
    record_result: str | None = "00000000-0000-4000-8000-00000000aaaa"
    sessions_open: int = 0
    opened: int = 0
    catalog_calls: int = 0
    catalog_saw_open: list[int] = field(default_factory=list)
    searches: list[tuple[str, str]] = field(default_factory=list)
    asks: list[tuple[str, list[Any], str]] = field(default_factory=list)
    ask_saw_open: list[int] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    logs: Logs = field(default_factory=Logs)


def knowledge_row(name: str, title: str) -> dict[str, Any]:
    """A ``manifest`` row of the ``knowledge`` app, as ``catalog_rows`` gives it."""
    return {"id": f"knowledge.{name}", "title": title, "source": "manifest", "class": "A",
            "app": "knowledge", "tool": None}


def install(monkeypatch: Any, **given: Any) -> World:
    """Put the fakes in place, with the flags on for :data:`ORG`."""
    from acb_common import db as acb_db
    from acb_skills import decide_tools
    from acb_skills.system_one import Answer
    from gateway.routes.studio import actions
    from gateway.studio import package_search
    from gateway.studio import tool_gaps as gaps_store
    from orchestrator import _tool_injection as ti
    from orchestrator import executor as ex
    from orchestrator import in_app_path as iap
    from orchestrator import tool_gap_path as tgp

    world = World(features=holder_features())
    for key, value in given.items():
        setattr(world, key, value)
    settings = get_settings()
    monkeypatch.setattr(settings, "user_tool_find_enabled", True)
    monkeypatch.setattr(settings, "tool_search_orgs", ORG)

    async def _open() -> bool:
        world.steps.append("gate")
        return world.studio_open

    async def _features() -> frozenset[str] | None:
        return world.features

    async def _catalog() -> Any:
        world.steps.append("catalog")
        world.catalog_calls += 1
        world.catalog_saw_open.append(world.sessions_open)
        rows = actions.CatalogRows(list(world.rows))
        rows.stale = False
        return rows

    @contextlib.asynccontextmanager
    async def _session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        world.sessions_open += 1
        world.opened += 1
        try:
            yield object()
        finally:
            world.sessions_open -= 1

    async def _search(db: Any, *, viewer: str, query: str, limit: int = 5) -> list[Any]:
        world.steps.append("library")
        assert world.sessions_open == 1, "the library check runs in its own session"
        world.searches.append((viewer, query))
        return list(world.hits)

    async def _ask(context: str, items: list[Any], *, purpose: str) -> list[Any] | None:
        world.steps.append("ask")
        world.asks.append((context, list(items), purpose))
        world.ask_saw_open.append(world.sessions_open)
        if world.picks is None:
            return None
        return [Answer(id=item.id, choice=world.picks.get(item.id, (None, None))[0],
                       confidence=world.picks.get(item.id, (None, None))[1], reason="")
                for item in items]

    async def _record(db: Any, **kwargs: Any) -> str | None:
        world.steps.append("record")
        assert world.sessions_open == 1, "the record runs in its own session"
        world.records.append(kwargs)
        return world.record_result

    monkeypatch.setattr(ti, "_studio_member_open", _open)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    monkeypatch.setattr(iap, "member_features", _features)
    monkeypatch.setattr(actions, "catalog_rows", _catalog)
    monkeypatch.setattr(acb_db, "tenant_session", _session)
    monkeypatch.setattr(package_search, "search_visible", _search)
    monkeypatch.setattr(decide_tools, "typed_choices_on", lambda: world.typed_on)
    monkeypatch.setattr(decide_tools, "ask_typed", _ask)
    monkeypatch.setattr(decide_tools, "_run_threshold", lambda: 0.7)
    monkeypatch.setattr(gaps_store, "record_gap", _record)
    monkeypatch.setattr(tgp, "_log", world.logs)
    return world


@contextlib.contextmanager
def in_run(org: str = ORG, **over: Any) -> Iterator[None]:
    """The frame of a chat run: the bound tenant and the run binding."""
    values: dict[str, Any] = {"run_id": RUN, "session_id": THREAD, "agent_name": "orchestrator",
                              "member": MEMBER, "workspace_root": "/tmp/orch-sbx1a"}
    values.update(over)
    token = bind_tenant(org)
    try:
        with artifact_context_scope():
            bind_artifact_context(**values)
            yield
    finally:
        release_tenant(token)


def hit(app_id: str, shared: int = 3) -> Any:
    """A hit of the library check."""
    from gateway.studio.package_search import LibraryHit

    return LibraryHit(app_id=app_id, shared=shared)
