"""NS-6 slice 6d: the routing eval of the one assistant, and its fence (R7).

``evals/routing`` holds the routing test set (``dataset.py``) and its runner
(``run.py``). This suite runs the scripted mode, which calls no model and no
network, and proves that each check of it can fail:

* the catalog: an agent id that the registry does not hold, an agent that
  leaves the registry, a specialist or a live app with no case, a preview
  pane as the app, an ``asks`` case that names a specialist, and a line of
  ``surfaceContext`` that changed in ``assistant.ts``;
* the delegation through the real ``orchestrator`` run: a script that calls
  the wrong agent, a case with no specialist that delegates, an ``asks``
  case that does not ask, a message that drops the member's words, a page
  context that the model never sees, and a sub-run that never reaches the
  agent.

Hermetic: the harness stubs the registry reads of the database and traps
every host HTTP send. Run::

    uv run pytest tests/unit/test_routing_eval.py -v
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("orchestrator.executor", reason="orchestrator not installed")

from evals.routing import dataset as ds
from evals.routing import (
    run,
    shell,
)
from evals.routing.dataset import Case
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    text_turn,
    tool_turn,
)


def _catalog(cases: list[Case]) -> list[str]:
    with RoutingHarness():
        return run.catalog_problems(cases)


def _one(case_id: str, scripts: Any = run.script_for) -> run.Result:
    problems, results = run.run_scripted(ds.CASES, only=[case_id], scripts=scripts)
    assert problems == [], problems
    (result,) = results
    return result


# ── the set and the scripted run pass ───────────────────────────────────────


def test_the_set_covers_every_specialist_and_every_live_app() -> None:
    assert len(ds.CASES) >= run.MIN_CASES
    assert _catalog(list(ds.CASES)) == []
    assert any(c.asks for c in ds.CASES)
    assert any(c.expected is None and not c.asks for c in ds.CASES)
    # Cross-app: a case asked in one live app for another app's specialist.
    assert sum(1 for c in ds.CASES
               if c.expected and c.app and run.pane_of(c.expected) not in (None, c.app)) >= 4


def test_the_scripted_run_passes_through_the_real_orchestrator() -> None:
    problems, results = run.run_scripted()
    assert problems == [], problems
    assert results and all(r.passed for r in results), [
        (r.case.id, r.problems) for r in results if not r.passed]
    with RoutingHarness():
        maf = {name for name, e in run.registry().items()
               if name != ORCHESTRATOR and (e.get("agent_runtime") or "maf") == "maf"}
    expected = {c.expected for c in ds.CASES if c.expected}
    # One driven case for each MAF specialist the set expects, one that the
    # orchestrator answers, and one that asks. Both forms of delegation run.
    assert {r.case.expected for r in results if r.case.expected} == maf & expected
    assert any(r.case.expected is None and not r.case.asks for r in results)
    assert any(r.case.asks for r in results)
    assert {r.form for r in results if r.case.expected} == {"call_agent", "tool"}


def test_the_command_line_exits_zero() -> None:
    assert run.main(["--scripted", "--cases", "N1"]) == run.EXIT_PASS


# ── the catalog: each check can fail ────────────────────────────────────────


def test_an_agent_id_that_the_registry_does_not_hold_fails() -> None:
    bad = Case("X1", "Show the pipeline.", "/crm", "crm-helper")
    problems = _catalog([*ds.CASES, bad])
    assert "X1: 'crm-helper' is not an agent of the registry" in problems, problems


def test_an_agent_that_leaves_the_registry_fails(monkeypatch) -> None:
    import gateway.routes.agent as routes_agent

    kept = [e for e in routes_agent._AGENT_REGISTRY if e["name"] != "crm-assistant"]
    monkeypatch.setattr(routes_agent, "_AGENT_REGISTRY", kept)
    problems = _catalog(list(ds.CASES))
    assert "C1: 'crm-assistant' is not an agent of the registry" in problems, problems


def test_a_specialist_and_a_live_app_with_no_case_fail() -> None:
    problems = _catalog([c for c in ds.CASES if c.expected != "crm-assistant"])
    assert "the orchestrator holds 'crm-assistant', and no case expects it" in problems
    assert "the live app /crm names 'crm-assistant', and no case asks it there" in problems


def test_a_preview_pane_and_a_specialist_on_an_asks_case_fail() -> None:
    preview = Case("X2", "Build an app.", "/build/apps", "app-builder")
    asks = Case("X3", "Do it.", None, "projects-assistant", asks=True)
    problems = _catalog([*ds.CASES, preview, asks])
    assert "X2: the app '/build/apps' is a preview pane, not a live pane" in problems, problems
    assert "X3: a case that asks first names no specialist" in problems, problems


def test_a_changed_line_of_surface_context_fails(monkeypatch) -> None:
    monkeypatch.setattr(shell, "HEADING", "## Where you are")
    problems = _catalog(list(ds.CASES))
    assert any(p.startswith("surfaceContext drifted: the heading") for p in problems), problems


# ── the delegation: each check can fail ─────────────────────────────────────


def test_a_script_that_calls_the_wrong_agent_fails() -> None:
    def wrong(case: Case, form: str) -> dict[str, ReplyModel]:
        return run.script_for(dataclasses.replace(case, expected="email-assistant"), form)

    result = _one("P1", wrong)
    assert not result.passed
    assert ("expected a call of 'projects-assistant', and it routed ['email-assistant']"
            in result.problems), result.problems


def test_a_case_with_no_specialist_that_delegates_fails() -> None:
    def delegates(case: Case, form: str) -> dict[str, ReplyModel]:
        return run.script_for(dataclasses.replace(case, expected="projects-assistant"), form)

    result = _one("N1", delegates)
    assert any(p.startswith("expected no delegate") for p in result.problems), result.problems


def test_a_case_that_asks_and_does_not_fails() -> None:
    def answers(case: Case, form: str) -> dict[str, ReplyModel]:
        return run.script_for(dataclasses.replace(case, asks=False), form)

    result = _one("A1", answers)
    assert ("expected a question card for the member, and none was answered"
            in result.problems), result.problems


def test_a_message_that_drops_the_members_words_fails() -> None:
    def vague(case: Case, form: str) -> dict[str, ReplyModel]:
        models = run.script_for(case, form)
        models[ORCHESTRATOR] = ReplyModel([
            tool_turn("call_agent", {"agent_name": case.expected, "message": "Do the thing."}),
            text_turn("Done."),
        ])
        return models

    result = _one("P1", vague)
    assert "'projects-assistant''s model never got the member's words" in result.problems, \
        result.problems


def test_a_page_context_that_the_model_never_sees_fails(monkeypatch) -> None:
    monkeypatch.setattr(run.shell, "system_context", lambda app, facts=(): shell.persona())
    result = _one("P1")
    assert "the orchestrator's model did not see the page context" in result.problems, \
        result.problems


def test_a_sub_run_that_never_reaches_the_agent_fails(monkeypatch) -> None:
    from orchestrator import executor

    async def nowhere(agent_name: str, *_a: Any, **_k: Any) -> str:
        return "(nothing ran)"

    monkeypatch.setattr(executor, "_run_sub_agent_streaming", nowhere)
    result = _one("E1")
    assert "expected a sub-run of 'email-assistant', and it reached []" in result.problems, \
        result.problems
    assert any("never built 'email-assistant'" in p for p in result.problems), result.problems


# ── the live path, with a script in place of the Router ─────────────────────


def _live_row(case_id: str, scripts: Any = run.script_for) -> dict[str, Any]:
    """One case through the live path: the stand-in for the specialist, and
    the score. Only the orchestrator's wire is scripted, so nothing leaves."""
    import asyncio

    case = ds.case(case_id)
    with RoutingHarness(live=True) as harness:
        harness.live = False  # the wire only: the stand-in stays in place
        harness.models = {ORCHESTRATOR: scripts(case, "call_agent")[ORCHESTRATOR]}
        (row,) = asyncio.run(run._live_cases(harness, [case], "tier-test", 1))
        assert harness.loaded == [ORCHESTRATOR], "a specialist ran in the live path"
        stand_ins = list(harness.delegated)
    row["stand_ins"] = stand_ins
    return row


def test_the_live_path_records_the_delegate_and_scores_it() -> None:
    row = _live_row("E3")
    assert row["got"] == "email-assistant" and row["passed"] is True, row
    assert row["stand_ins"] == [("email-assistant", ds.case("E3").words)], row
    asks = _live_row("A2")
    assert asks["got"] is None and asks["asked"] is True and asks["passed"] is True, asks
    summary = run.summarise([row, asks], "tier-test")
    assert summary["accuracy"] == 1.0 and summary["runs"] == 2, summary


def test_the_live_score_fails_a_wrong_delegate() -> None:
    def wrong(case: Case, form: str) -> dict[str, ReplyModel]:
        return run.script_for(dataclasses.replace(case, expected="crm-assistant"), form)

    row = _live_row("E3", wrong)
    assert row["got"] == "crm-assistant" and row["passed"] is False, row
    assert run.summarise([row], "tier-test")["accuracy"] == 0.0
