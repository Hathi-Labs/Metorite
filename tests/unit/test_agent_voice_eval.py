"""The voice eval runs offline, and its scorer counts each rule (WS-52 S1).

``evals/agent_voice/run.py --scripted`` scores hand-written answers with
``acb_llm.voice.voice_lint``. A live run needs a model, so CI never runs it.

Mutations this file catches (R7): a scripted answer that loses its em dash,
or an expected rule that is wrong, fails the run.
"""
from __future__ import annotations

import copy

from evals.agent_voice import run


def test_there_are_twelve_cases_over_every_surface() -> None:
    from acb_llm.voice import SURFACES

    cases = run.load_cases()
    assert len(cases) == 12
    assert {c["surface"] for c in cases} == set(SURFACES)
    assert len({c["id"] for c in cases}) == 12


def test_the_scripted_run_passes() -> None:
    assert run.run_scripted(run.load_cases(), run.load_scripted()) == []
    assert run.main(["--scripted"]) == run.EXIT_PASS


def test_a_changed_answer_fails_the_scripted_run() -> None:
    answers = copy.deepcopy(run.load_scripted())
    answers["V02"]["text"] = answers["V02"]["text"].replace("—", ",")
    bad = run.run_scripted(run.load_cases(), answers)
    assert len(bad) == 1 and bad[0].startswith("V02")


def test_a_wrong_expectation_fails_the_scripted_run() -> None:
    answers = copy.deepcopy(run.load_scripted())
    answers["V01"]["expected_rules"] = ["filler"]
    assert run.run_scripted(run.load_cases(), answers)


def test_the_live_run_refuses_without_an_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    assert run.main(["--live"]) == run.EXIT_NO_GO
