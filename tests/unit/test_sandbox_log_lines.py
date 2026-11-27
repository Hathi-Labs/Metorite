"""SBX-F15, the gap half (SBX-1a, task 3800): no log line holds a need.

Spec: ``project-docs/specs/agent_sandbox.md`` §9.1 and §12.1 (done-when 8 and
13). Code: ``orchestrator/tool_gap_path.py`` (``sandbox.tool.gap``),
``tool_packs.FindTools._no_match`` (``tool_packs.find_gap_failed``) and the
route ``routes/studio/tool_gaps.py``.

What this proves:

1. ``sandbox.tool.gap`` holds the fields of §9.1 only: org, member, run,
   category, answer and outcome. No value holds a word of the need.
2. The source of the line passes no ``need``, no ``query`` and no
   ``candidates``.
3. A fault of the hook logs its class only.
4. The route gives a need only to a holder of ``studio:publish``, and only
   with the flag on. Each other caller gets 403 or 404 with no read.

Mutations (R7), each red here: add ``need=need`` to the log call; log
``str(exc)`` in ``_no_match``; read the store before the share verb.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions
from gateway.routes.studio import tool_gaps as route
from gateway.studio import tool_gaps as gaps_store
from orchestrator import tool_gap_path as tgp

from tests.unit import _tool_gap_fakes as fk

#: The fields of §9.1.
FIELDS = {"org", "member", "run", "category", "answer", "outcome"}
SECRET = "quarterly salary sheet of Ravindra"


# ── 1, 2. The line ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("pick", ["none", "compose", "unsure", "pkg:x"])
async def test_the_gap_line_holds_the_fields_of_9_1_and_no_need(monkeypatch, pick) -> None:
    """Done-when 8. Mutation: add ``need=need`` to the call -> red."""
    world = fk.install(monkeypatch, rows=[fk.knowledge_row("search", "Search salary sheet")],
                       picks={"answer": (pick, 0.9), "category": ("documents", 0.9)})
    with fk.in_run():
        await tgp.gap_hook(SECRET, [])
    [line] = world.logs.events(tgp.GAP_EVENT)
    assert set(line) == FIELDS
    assert line["org"] == fk.ORG and line["member"] == fk.MEMBER and line["run"] == fk.RUN
    words = [w for w in SECRET.lower().split() if len(w) > 3]
    for value in line.values():
        assert not any(w in str(value).lower() for w in words), value
    every = repr(world.logs.lines).lower()
    assert "salary" not in every and "ravindra" not in every


def test_the_source_of_the_line_passes_no_need() -> None:
    """The call of ``sandbox.tool.gap`` names the fields of §9.1 and nothing
    else, in the source, so a later field cannot add the need in silence."""
    tree = ast.parse(inspect.getsource(tgp))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and any(isinstance(a, ast.Name) and a.id == "GAP_EVENT" for a in n.args)]
    assert len(calls) == 1
    assert {k.arg for k in calls[0].keywords} == FIELDS
    assert tgp.GAP_EVENT == "sandbox.tool.gap"


async def test_a_fault_of_the_hook_logs_the_class_only(monkeypatch) -> None:
    """Done-when 13. Mutation: log ``str(exc)`` -> red."""
    from acb_skills import tool_packs as tp

    logs = fk.Logs()
    monkeypatch.setattr(tp, "_log", logs)

    async def _hook(query: str, live: Any) -> str:
        raise ValueError(f"cannot file {query}")

    finder = tp.FindTools(candidates=tp.FindCandidates(), run_key="k", record=None,  # type: ignore[arg-type]
                          build=lambda *a: None, measure=lambda items: 0, on_gap=_hook)
    assert await finder._no_match(SECRET, []) == tp.FIND_NONE
    assert logs.events("tool_packs.find_gap_failed") == [{"error": "ValueError"}]
    assert "salary" not in repr(logs.lines).lower()


# ── 4. The route ───────────────────────────────────────────────────────────


ORG = "0a0a0a0a-0000-4000-8000-0000000000c3"
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]


@pytest.fixture
def store(monkeypatch) -> dict[str, Any]:
    """The store and the session of the route, as fakes that record a read."""
    box: dict[str, Any] = {"reads": 0, "sessions": 0}

    @contextlib.asynccontextmanager
    async def _session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        box["sessions"] += 1
        yield object()

    async def _summary(db: Any, **_k: Any) -> gaps_store.GapSummary:
        box["reads"] += 1
        return gaps_store.GapSummary(
            days=30, by_outcome={"no_match": 2, "compose": 1}, by_category={"documents": 3},
            rows=(gaps_store.GapRow(need=SECRET, category="documents", outcome="no_match",
                                    created_at=datetime(2026, 11, 26, tzinfo=UTC)),))

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(gaps_store, "gap_summary", _summary)
    monkeypatch.setattr(get_settings(), "studio_orgs", ORG)
    monkeypatch.setattr(get_settings(), "user_tool_find_enabled", True)
    return box


def _client(grants: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="ana@gaps.test", role=UserRole.EMPLOYEE, organization_id=ORG,
        access=build_access(grants, []))
    return TestClient(app, raise_server_exceptions=False)


def test_a_holder_of_the_share_verb_reads_the_gap_view(store) -> None:
    r = _client(PUBLISHER).get("/studio/tool-gaps")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 30
    assert body["counts"] == {"by_outcome": {"no_match": 2, "compose": 1},
                              "by_category": {"documents": 3}}
    assert body["gaps"] == [{"need": SECRET, "category": "documents", "outcome": "no_match",
                             "created_at": "2026-11-26T00:00:00+00:00"}]
    assert store["reads"] == 1


def test_a_member_without_the_share_verb_gets_403_and_no_read(store) -> None:
    """Mutation: read the store before the share verb -> red."""
    r = _client(STUDIO).get("/studio/tool-gaps")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden"
    assert SECRET not in r.text
    assert store == {"reads": 0, "sessions": 0}


@pytest.mark.parametrize("off", ["studio_orgs", "user_tool_find_enabled"])
def test_the_route_is_404_with_studio_or_the_flag_off(store, monkeypatch, off) -> None:
    monkeypatch.setattr(get_settings(), off, "" if off == "studio_orgs" else False)
    r = _client(PUBLISHER).get("/studio/tool-gaps")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}
    assert store == {"reads": 0, "sessions": 0}
