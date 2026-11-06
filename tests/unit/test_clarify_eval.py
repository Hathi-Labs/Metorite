"""CL-7 — the clarify eval. Free, scripted, and fenced (R7).

Task 1609, epic CL (task 1607). ``tests/fixtures/clarify_cases.json`` holds the
cases. Each case has a capture, a fixture project tree, the scripted model
answer, and what clarify must make of it: the ``[P#]`` resolution, the
disposition, ``clarified_by`` and ``fallback_reason``.

A stub model returns the scripted answer. The test drives ``_llm_propose``, so
it covers the prompt call, the parse, the checks and the project resolution.
Then it runs ``propose_with_llm`` and ``stamp_clarified_by``, the same two steps
the route runs. No paid model is called.

The live mode is OFF. ``CLARIFY_EVAL_LIVE=1`` sends each distinct capture to the
real model. It prints a cost estimate first. It stops when the estimate is above
USD 0.50, unless ``CLARIFY_EVAL_LIVE_OK=1`` says the owner agreed to the cost
(owner rule, memory "Eval cost needs owner OK"). Nobody runs it in CI.

Mutations this file catches:

* ``max_tokens`` falls back under 900 -> ``test_the_budget_is_900_and_the_cap_is_8``;
* a fallback stops logging ``clarify.llm_failed`` -> ``test_each_case_*`` (fallback kind);
* ``_resolve_project_match`` accepts a space or folder -> the ``space-folder`` cases;
* the subtask cap goes back to 12 -> ``good-subtasks-cap``.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from gateway.routes.tasks import ai as tasks_ai

REPO = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (REPO / "tests/fixtures/clarify_cases.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
TREE = FIXTURE["tree"]
KINDS = {"good", "pin-fuzzy", "bad-token", "outside-list", "space-folder",
         "confidence", "fallback"}
REASONS = {"truncated", "bad_json", "unknown_disposition", "empty_next_action",
           "timeout", "error"}


def _projects() -> list[SimpleNamespace]:
    return [SimpleNamespace(id=p["id"], outcome=p["outcome"], purpose="",
                            status=p["status"], account_id=None)
            for p in TREE["projects"]]


def _places() -> list[dict[str, Any]]:
    spaces, folders = [], []
    for s in TREE["spaces"]:
        spaces.append(SimpleNamespace(id=s["id"], name=s["name"]))
        for f in s["folders"]:
            folders.append(SimpleNamespace(
                id=f["id"], name=f["name"], space_id=s["id"]))
    return tasks_ai._collect_places([], spaces, folders)


def _item(case: dict) -> SimpleNamespace:
    cap = case["capture"]
    return SimpleNamespace(id="item-1", title=cap["title"],
                           description=cap["description"], project_id=None)


def _stub(case: dict, seen: list[dict]):
    model = case["model"]

    async def fake(**kwargs):
        seen.append(kwargs)
        if model["raises"] == "timeout":
            raise TimeoutError("scripted")
        if model["raises"]:
            raise RuntimeError("scripted provider error")
        content = model["content"]
        if not isinstance(content, str):
            content = json.dumps(content)
        choice = SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=model["finish_reason"])
        return SimpleNamespace(choices=[choice]), kwargs.get("model")

    return fake


def _run(case: dict, monkeypatch) -> tuple[dict, dict | None, list[dict], list]:
    """Do what the route does, with the stub model. Returns the proposal, the
    LLM core, the model calls and the captured log events."""
    seen: list[dict] = []
    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback",
                        _stub(case, seen), raising=False)
    projects, item, diag = _projects(), _item(case), {}
    core = None
    with structlog.testing.capture_logs() as logs:
        if case["llm_on"]:
            core = asyncio.run(tasks_ai._llm_propose(
                item, [], projects, {}, "tier-fast",
                places=_places(), diag=diag))
    proposal = tasks_ai.propose_with_llm(item, [], projects, {}, core)
    tasks_ai.stamp_clarified_by(
        proposal, core, diag, asked_llm=case["llm_on"])
    return proposal, core, seen, logs


def test_the_eval_is_big_and_wide_enough():
    assert len(CASES) >= 30
    assert len({c["id"] for c in CASES}) == len(CASES)
    assert {c["kind"] for c in CASES} == KINDS
    reasons = {c["expect"]["fallback_reason"] for c in CASES
               if c["kind"] == "fallback"} - {None}
    assert reasons == REASONS, "one case per fallback reason"
    confs = {c["expect"].get("confidence") for c in CASES
             if c["kind"] == "confidence"}
    assert confs == {"low", "medium", "high"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_case(case, monkeypatch):
    exp = case["expect"]
    proposal, core, seen, logs = _run(case, monkeypatch)

    # Who answered, and why a fallback happened.
    assert proposal["clarified_by"] == exp["clarified_by"]
    assert proposal.get("fallback_reason") == exp["fallback_reason"]

    if exp["clarified_by"] == "heuristic":
        assert core is None
        base = tasks_ai.propose(_item(case), [], _projects(), {})
        for key in ("disposition", "next_action", "project_id"):
            assert proposal[key] == base[key]
        failed = [e for e in logs if e["event"] == "clarify.llm_failed"]
        if exp["fallback_reason"]:
            assert [e["reason"] for e in failed] == [exp["fallback_reason"]]
        else:
            assert failed == [], "no model call, so no failure to log"
        return

    # The model answered: the core and the merged proposal agree with the case.
    assert core is not None
    assert not [e for e in logs if e["event"] == "clarify.llm_failed"]
    assert core["disposition"] == exp["disposition"]
    got = core.get("llm_project")
    assert (got.id if got is not None else None) == exp["project"]
    if "confidence" in exp:
        assert core["confidence"] == exp["confidence"]
    if "due_date" in exp:
        assert core.get("due_date") == exp["due_date"]
    if "subtasks_len" in exp:
        assert len(core["subtasks"]) == exp["subtasks_len"]
    if "final_disposition" in exp:
        assert proposal["disposition"] == exp["final_disposition"]
    if exp["project"]:
        assert proposal["project_id"] == exp["project"]
    # A space, a folder or a parked project is never the chosen project.
    chosen = {p["id"] for p in TREE["projects"] if p["status"] == "ACTIVE"}
    assert exp["project"] is None or exp["project"] in chosen
    assert len(seen) == 1


def test_the_budget_is_900_and_the_cap_is_8(monkeypatch):
    """CL-1: the clarify call asks for 900 tokens and 8 subtasks."""
    case = next(c for c in CASES if c["id"] == "good-quote-email")
    _proposal, _core, seen, _logs = _run(case, monkeypatch)
    assert seen[0]["max_tokens"] == tasks_ai.CLARIFY_MAX_TOKENS == 900
    assert tasks_ai.CLARIFY_MAX_SUBTASKS == 8
    prompt = seen[0]["messages"][0]["content"]
    assert "never list more than 8" in prompt
    assert "at most 8" in prompt


def test_the_parent_check_has_a_timeout(monkeypatch):
    """CL-1: a slow parent check returns None. It does not hold the card."""
    monkeypatch.setattr(tasks_ai, "PARENT_CHECK_TIMEOUT_S", 0.05)

    class Src:
        async def siblings(self, *a, **k):
            return [SimpleNamespace(id="t1", title="Draft the quote")]

    async def slow(**kwargs):
        await asyncio.sleep(2)

    monkeypatch.setattr(tasks_ai, "item_source", lambda: Src())
    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", slow,
                        raising=False)
    out = asyncio.run(tasks_ai._find_parent_task(
        None, "u", SimpleNamespace(id="i", title="Send the quote"), "p1",
        "tier-fast"))
    assert out is None


# ── Live mode (OFF by default) ───────────────────────────────────────────────

LIVE = os.environ.get("CLARIFY_EVAL_LIVE") == "1"
# Rough list price, USD per 1M tokens. An estimate only. It errs high.
_IN_PER_M, _OUT_PER_M = 1.0, 4.0
_BUDGET_USD = 0.50


def live_cost_estimate(cases: list[dict]) -> float:
    """USD for one call per distinct capture: 1500 tokens in, 900 out."""
    calls = len({c["capture"]["title"] for c in cases})
    return calls * (1500 * _IN_PER_M + 900 * _OUT_PER_M) / 1_000_000


def test_the_live_estimate_is_small_and_known():
    est = live_cost_estimate(CASES)
    assert 0 < est < 1.0, est


@pytest.mark.skipif(not LIVE, reason="set CLARIFY_EVAL_LIVE=1 for the paid run")
def test_live_model_keeps_the_rules():  # pragma: no cover - paid, opt-in
    """Send each distinct capture to the real model. Check the rules that must
    hold for ANY model answer. It never pins a wording."""
    good = [c for c in CASES if c["kind"] in ("good", "confidence")]
    est = live_cost_estimate(good)
    print(f"\nCLARIFY_EVAL_LIVE: {len(good)} calls, estimated USD {est:.3f}")
    if est > _BUDGET_USD and os.environ.get("CLARIFY_EVAL_LIVE_OK") != "1":
        pytest.fail(
            f"estimate USD {est:.2f} is above USD {_BUDGET_USD}. "
            "Get the owner OK, then set CLARIFY_EVAL_LIVE_OK=1.")
    active = {p["id"] for p in TREE["projects"] if p["status"] == "ACTIVE"}
    for case in good:
        diag: dict = {}
        core = asyncio.run(tasks_ai._llm_propose(
            _item(case), [], _projects(), {}, "tier-fast",
            places=_places(), diag=diag))
        if core is None:
            print(f"  {case['id']}: fallback {diag.get('reason')}")
            continue
        got = core.get("llm_project")
        assert got is None or got.id in active
        assert core["disposition"] in tasks_ai._LLM_DISPOSITIONS
        assert len(core.get("subtasks", [])) <= tasks_ai.CLARIFY_MAX_SUBTASKS
