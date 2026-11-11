"""WS-VOX VOX-5: the image-need eval of the one assistant, and its fence (R7).

``evals/image_need`` holds the test set (``dataset.py``), the harness
(``harness.py``) and the runner (``run.py``). This suite runs the scripted
mode, which calls no model and no network, and proves that each check of it
can fail:

* the scorer: a ``call`` case with no call, with two calls or with the wrong
  file name fails, and a ``no_call`` case with a call fails;
* the harness: with ``IMAGE_ANALYSIS_ORGS`` off, ``analyze_image`` is not a
  tool, so a ``call`` case fails and the request check fails;
* the request: the upload note, the member's words and the rule of the tool
  description reach the model, and a drift in ``chatUpload.ts`` fails the set;
* the live path, with a script in place of the Router: the budget stops the
  run before a call would pass it.

Hermetic: the harness traps every host HTTP send. Run::

    uv run pytest tests/unit/test_image_need_eval.py -v
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("orchestrator.executor", reason="orchestrator not installed")

from evals.image_need import dataset as ds
from evals.image_need import harness as hs
from evals.image_need import run
from evals.routing.harness import ORCHESTRATOR, ReplyModel, text_turn, tool_turn


def _case(case_id: str) -> ds.Case:
    return next(c for c in ds.CASES if c.id == case_id)


def _one(case_id: str, scripts=run.script_for, image_on: bool = True) -> run.Result:
    problems, results = run.run_scripted([_case(case_id)], scripts=scripts, image_on=image_on)
    assert [p for p in problems if "fewer than" not in p] == [], problems
    (result,) = results
    return result


def _calls(*names: str) -> list[dict[str, str]]:
    return [{"file_name": n, "question": "q"} for n in names]


# ── the set and the scripted run pass ───────────────────────────────────────


def test_the_set_holds_both_groups_and_the_port_holds() -> None:
    assert run.dataset_problems(ds.CASES) == []
    assert sum(c.expected == ds.CALL for c in ds.CASES) >= 6
    assert sum(c.expected == ds.NO_CALL for c in ds.CASES) >= 6


def test_the_scripted_run_passes_through_the_real_orchestrator() -> None:
    problems, results = run.run_scripted()
    assert problems == [], problems
    assert len(results) == len(ds.CASES)
    assert all(r.passed for r in results), [(r.case.id, r.problems) for r in results if not r.passed]


def test_the_command_line_exits_zero() -> None:
    assert run.main(["--scripted"]) == run.EXIT_PASS


def test_the_request_holds_the_tool_the_rule_the_note_and_the_words() -> None:
    from acb_skills.addendum import IMAGE_CALL_RULE

    case = _case("C2")
    models = run.script_for(case)
    with hs.ImageHarness() as harness:
        harness.models = models
        asyncio.run(run._drive(harness, case))
    first = models[ORCHESTRATOR].bodies[0]
    assert hs.TOOL in hs.tool_names(first)
    (tool,) = [t for t in first["tools"] if (t.get("function") or t)["name"] == hs.TOOL]
    assert run._flat(IMAGE_CALL_RULE) in run._flat((tool.get("function") or tool)["description"])
    assert run.request_problems(case, models[ORCHESTRATOR]) == []
    assert harness.analyzed and harness.analyzed[0][0] == case.file_name


# ── the scorer fails each wrong reply ───────────────────────────────────────


def test_a_call_case_with_no_call_fails() -> None:
    assert hs.score(_case("C1"), []) != []


def test_a_call_case_with_two_calls_fails() -> None:
    assert hs.score(_case("C1"), _calls("screenshot.png", "screenshot.png")) != []


def test_a_call_case_that_names_another_file_fails() -> None:
    assert hs.score(_case("C1"), _calls("other.png")) != []


def test_a_no_call_case_with_a_call_fails() -> None:
    assert hs.score(_case("N1"), _calls("screenshot.png")) != []


def test_the_right_replies_pass() -> None:
    assert hs.score(_case("C1"), _calls("screenshot.png")) == []
    assert hs.score(_case("N1"), []) == []


def test_a_scorer_that_ignores_the_call_count_is_caught(monkeypatch) -> None:
    """R7 mutation: a scorer that only checks 'at least one call' passes a
    no_call case that calls. The scripted run must then show the gap."""

    def lax(case: ds.Case, calls: list[dict]) -> list[str]:
        return [] if case.expected == ds.NO_CALL else hs.score(case, calls)

    # With the real scorer, a script that calls on a no_call case fails.
    def calls_everywhere(case: ds.Case) -> dict[str, ReplyModel]:
        return {ORCHESTRATOR: ReplyModel([
            tool_turn(hs.TOOL, {"file_name": case.file_name, "question": case.words}),
            text_turn("Looked."),
        ])}

    real = _one("N1", scripts=calls_everywhere)
    assert not real.passed
    assert any("expected no analyze_image call" in p for p in real.problems)
    # With the mutated scorer the same script passes: the test above is the fence.
    monkeypatch.setattr(hs, "score", lax)
    assert _one("N1", scripts=calls_everywhere).passed


def test_a_script_that_calls_twice_on_a_call_case_fails() -> None:
    def twice(case: ds.Case) -> dict[str, ReplyModel]:
        args = {"file_name": case.file_name, "question": case.words}
        return {ORCHESTRATOR: ReplyModel([
            tool_turn(hs.TOOL, args, "call_1"), tool_turn(hs.TOOL, args, "call_2"),
            text_turn("Looked."),
        ])}

    result = _one("C1", scripts=twice)
    assert not result.passed
    assert any("exactly one" in p for p in result.problems)


# ── the harness with the flag off ───────────────────────────────────────────


def test_with_the_flag_off_the_tool_is_absent_and_a_call_case_fails() -> None:
    case = _case("C1")
    models = run.script_for(case)
    with hs.ImageHarness(image_on=False) as harness:
        harness.models = models
        asyncio.run(run._drive(harness, case))
    assert hs.TOOL not in hs.tool_names(models[ORCHESTRATOR].bodies[0])
    # The same eval on a harness with the flag off fails a call case.
    assert run.request_problems(case, models[ORCHESTRATOR], image_on=True) != []
    result = _one("C1", image_on=False)
    assert not result.passed


def test_with_the_flag_off_a_no_call_case_fails_the_request_check() -> None:
    result = _one("N1", image_on=False)
    assert not result.passed
    assert any("does not offer" in p for p in result.problems)


# ── the upload note ─────────────────────────────────────────────────────────


def test_the_note_has_the_words_the_file_and_its_path() -> None:
    case = _case("C2")
    text = hs.message_for(case, run.THREAD)
    assert text.startswith(case.words + "\n\n")
    assert f"Uploaded 1 file(s): {case.file_name}" in text
    assert "Paths: `inputs/" in text and f"/{case.file_name}`" in text
    assert text.endswith("Read them with read_attachment.")


def test_a_changed_upload_note_in_the_typescript_fails_the_set(monkeypatch, tmp_path) -> None:
    from evals.routing import shell

    (tmp_path / "chatUpload.ts").write_text("export const x = 1;\n", encoding="utf-8")
    monkeypatch.setattr(shell, "CONTROL_PLANE", tmp_path)
    assert len(hs.drift()) >= 4


# ── the set must hold both groups ───────────────────────────────────────────


def test_a_set_with_one_group_fails() -> None:
    only_calls = [c for c in ds.CASES if c.expected == ds.CALL]
    assert any("fewer than 6" in p for p in run.dataset_problems(only_calls))


def test_a_duplicate_id_and_a_wrong_outcome_fail() -> None:
    bad = [*ds.CASES, dataclasses.replace(ds.CASES[0], expected="maybe")]
    problems = run.dataset_problems(bad)
    assert any("not unique" in p for p in problems)
    assert any("'maybe'" in p for p in problems)


# ── the live path, with a script in place of the Router ─────────────────────


def test_the_live_path_scores_and_the_budget_stops_it(monkeypatch) -> None:
    cases = [_case("N1"), _case("C1"), _case("C2")]
    scripts = {c.id: run.script_for(c) for c in cases}

    async def go(budget: float):
        with hs.ImageHarness() as harness:
            real = run._drive

            async def scripted(h, case, tier=None, timeout=120.0):
                h.models = scripts[case.id]
                return await real(h, case)

            monkeypatch.setattr(run, "_drive", scripted)
            monkeypatch.setattr(run, "_case_cost", lambda h, mark: (0.04, 20000, 100))
            return await run._live_cases(harness, cases, "tier-balanced", budget)

    rows, stopped = asyncio.run(go(1.0))
    assert not stopped and [r["passed"] for r in rows] == [True, True, True]
    assert rows[1]["calls"] == 1 and rows[0]["calls"] == 0
    # 0.05 first guess: 0.04 spent + 0.05 worst > 0.08 stops before case 2.
    rows, stopped = asyncio.run(go(0.08))
    assert stopped and len(rows) == 1
    summary = run.summarise(rows, "tier-balanced", 0.08, stopped)
    assert summary["stopped_by_budget"] and summary["cost_usd"] == 0.04


def test_the_addendum_tells_a_task_with_an_upload_to_pass_files() -> None:
    """IMG-A3 (task 2489): create_task(files=...) is named where the model reads it."""
    from acb_skills import addendum

    rule = addendum.FILES_ON_TASK_RULE
    assert "attached files and asks for a task" in rule
    assert "create_task" in rule and "`files`" in rule
    for sections in (addendum.FULL_SECTIONS, addendum.COMPACT_SECTIONS):
        sec = next(s for s in sections if s.gate == ("read_attachment",))
        assert rule in sec.text


def test_the_real_request_for_a_task_with_an_image_holds_the_files_rule() -> None:
    """IMG-A3: the prompt of "make a task with this screenshot" holds the rule.

    The scripted model proves the prompt only. Whether a live model passes
    `files` needs a live run: ``python -m evals.image_need.run --live``.
    """
    case = _case("N2")
    models = run.script_for(case)
    with hs.ImageHarness() as harness:
        harness.models = models
        asyncio.run(run._drive(harness, case))
    body = models[ORCHESTRATOR].bodies[0]
    text = run._flat(body["messages"][0]["content"])
    assert "pass the file names in its `files` argument" in text
