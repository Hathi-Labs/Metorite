"""WS-61 KB-5c — the expiry of Knowledge proposals, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §8.4 and §13.

* **Off means no loop.** With ``KNOWLEDGE_PROPOSAL_EXPIRY`` off, no task starts.
* **One org for each session.** A tick binds one ``tenant_session`` for each
  org that Knowledge serves, and never two orgs in one session.
* **An org that Knowledge does not serve is skipped**, and one failing org
  does not stop the next.
* **No model and no embed.** The module imports neither.
* **The log holds counts only.**

The SQL is proved against a real database in
``test_kb_proposal_expiry_r8.py``.

Mutations this file catches (R7):

* the ``organization_id`` predicate leaves a statement -> ``TestTheSql``;
* ``run_org`` binds a second org in one session -> ``TestTheTick``;
* the switch check leaves the start function -> ``TestTheSwitch``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge.proposal_expiry", reason="gateway not installed")

from acb_common import get_settings
from gateway.routes.knowledge import proposal_expiry as expiry

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps/services/gateway/gateway/main.py"

ORG = "66666666-6666-6666-6666-666666666666"
OTHER = "77777777-7777-7777-7777-777777777777"
SECRET = "The vault code is BLUEFIN-7731"


def _flags(monkeypatch: pytest.MonkeyPatch, orgs: str, *, on: bool = True) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", orgs, raising=False)
    monkeypatch.setattr(settings, "knowledge_proposal_expiry", on, raising=False)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Db:
    """A session that records its statements and returns no row."""

    def __init__(self, org: str, log: list[tuple[str, str]]) -> None:
        self.org = org
        self.log = log

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.log.append((self.org, str(stmt)))
        assert (params or {}).get("org") == self.org, "a statement named another org"
        return _Result([])


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch):
    """The loop with its org list and its sessions replaced."""
    expiry._stopping.clear()
    sessions: list[str] = []
    statements: list[tuple[str, str]] = []
    failing: set[str] = set()

    async def orgs() -> list[str]:
        return [ORG, OTHER]

    @contextlib.asynccontextmanager
    async def fake_session(org: str):
        sessions.append(org)
        if org in failing:
            raise RuntimeError(SECRET)
        yield _Db(org, statements)

    monkeypatch.setattr(expiry, "_organization_ids", orgs)
    monkeypatch.setattr(expiry, "tenant_session", fake_session)
    yield sessions, statements, failing
    expiry._stopping.clear()


def _tick() -> expiry.TickResult:
    return asyncio.run(expiry.run_tick())


class TestTheSwitch:
    def test_the_switch_off_starts_no_task(self, monkeypatch, wired):
        _flags(monkeypatch, "*", on=False)

        async def go() -> bool:
            started = await expiry.start_kb_proposal_expiry()
            assert not expiry.loop_running()
            return started

        assert asyncio.run(go()) is False

    def test_the_switch_defaults_on(self):
        assert type(get_settings()).model_fields["knowledge_proposal_expiry"].default is True

    def test_stop_is_safe_when_never_started(self):
        asyncio.run(expiry.stop_kb_proposal_expiry())
        assert not expiry.loop_running()

    def test_the_start_function_reads_the_switch(self):
        tree = ast.parse(Path(expiry.__file__).read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "start_kb_proposal_expiry")
        called = {c.func.id for c in ast.walk(start)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "expiry_enabled" in called

    def test_main_starts_and_stops_it_and_never_reads_the_switch(self):
        src = MAIN.read_text(encoding="utf-8")
        assert "start_kb_proposal_expiry" in src and "stop_kb_proposal_expiry" in src
        assert "knowledge_proposal_expiry" not in src


class TestTheTick:
    def test_one_session_for_each_org(self, monkeypatch, wired):
        sessions, statements, _ = wired
        _flags(monkeypatch, "*")
        result = _tick()
        assert sessions == [ORG, OTHER]
        assert result.orgs == 2
        # Each statement ran in the session of the org that it names.
        assert {o for o, _ in statements} == {ORG, OTHER}

    def test_an_org_that_knowledge_does_not_serve_is_skipped(self, monkeypatch, wired):
        sessions, _, _ = wired
        _flags(monkeypatch, ORG)
        result = _tick()
        assert sessions == [ORG] and result.orgs == 1

    def test_knowledge_off_touches_no_org(self, monkeypatch, wired):
        sessions, _, _ = wired
        _flags(monkeypatch, "*")
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False, raising=False)
        assert _tick().orgs == 0 and sessions == []

    def test_the_proposals_flag_is_not_needed(self, monkeypatch, wired):
        sessions, _, _ = wired
        _flags(monkeypatch, ORG)
        monkeypatch.setattr(get_settings(), "knowledge_proposals", False, raising=False)
        _tick()
        assert sessions == [ORG]

    def test_one_failing_org_does_not_stop_the_next(self, monkeypatch, wired):
        sessions, _, failing = wired
        _flags(monkeypatch, "*")
        failing.add(ORG)
        result = _tick()
        assert sessions == [ORG, OTHER]
        assert result.failed == 1

    def test_a_stopping_loop_starts_no_org(self, monkeypatch, wired):
        sessions, _, _ = wired
        _flags(monkeypatch, "*")
        expiry._stopping.set()
        _tick()
        assert sessions == []


class TestTheSql:
    def test_the_age_is_14_days(self):
        assert expiry.EXPIRY_DAYS == 14
        assert expiry.TICK_SECONDS == 3600.0 and expiry.MAX_PER_ORG == 200

    @pytest.mark.parametrize("sql", [expiry._EXPIRE_EDITS_SQL, expiry._ARCHIVE_PAGES_SQL,
                                     expiry._VERSION_SQL, expiry._DROP_LINKS_SQL])
    def test_each_statement_names_the_organization(self, sql):
        assert "organization_id = CAST(:org AS uuid)" in sql

    def test_the_update_of_pages_keeps_the_draft_guard(self):
        sql = expiry._ARCHIVE_PAGES_SQL
        assert sql.count("state = 'draft'") >= 2
        assert "origin->>'kind' = 'agent_proposal'" in sql
        assert "updated_at < now()" in sql

    def test_the_edit_update_sets_decided_at(self):
        sql = expiry._EXPIRE_EDITS_SQL
        assert "decided_at = now()" in sql and "status = 'pending'" in sql

    def test_the_version_is_a_system_sync_archive(self):
        assert (expiry.SYSTEM, expiry.VERSION_REASON, expiry.VERSION_VIA) == (
            "system", "archive", "sync")

    def test_the_module_imports_no_model_and_no_embed_door(self):
        tree = ast.parse(Path(expiry.__file__).read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names += [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names += [a.name for a in node.names]
        bad = [n for n in names if "embed" in n.lower() or "acb_llm" in n or "routed" in n]
        assert not bad, bad


class TestTheLog:
    def test_the_tick_line_holds_counts_only(self, monkeypatch, wired):
        _flags(monkeypatch, "*")
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

        monkeypatch.setattr(expiry, "_log", _Log())
        _tick()
        assert [e for e, _ in lines] == ["knowledge.proposal_expiry.tick"]
        assert set(lines[0][1]) == {"orgs", "edits", "pages", "failed"}
        assert all(isinstance(v, int) for v in lines[0][1].values())

    def test_a_failed_org_logs_the_error_type_only(self, monkeypatch, wired):
        _, _, failing = wired
        _flags(monkeypatch, ORG)
        failing.add(ORG)
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

        monkeypatch.setattr(expiry, "_log", _Log())
        _tick()
        assert ("knowledge.proposal_expiry.org_failed", {"error": "RuntimeError"}) in lines
        assert "BLUEFIN" not in repr(lines)
