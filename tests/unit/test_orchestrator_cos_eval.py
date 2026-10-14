"""The chief-of-staff eval runs offline, and each check fires (spec §11.5).

``evals/orchestrator_cos/run.py --scripted`` scores hand-written answers.
Each good answer passes every check, and each bad answer fails exactly the
checks it names. A live run needs the Router, so CI never runs it.

Mutations this file catches (R7): a check that stops firing (a bad answer
then passes it), a case set that loses a kind the owner named, a cap that
drifts, and a live run that starts with no endpoint. Spec 11.8 adds three: a
live overrun of 2026-10-14 that leaves the bad answers or stops failing the
cap, a baseline that fills the after slot, and a replayed tool turn with no
``reasoning_content`` (the 400 of DeepSeek's thinking tier).
"""
from __future__ import annotations

import copy
import json

import pytest

from evals.orchestrator_cos import run


def test_the_case_set_covers_each_kind_the_owner_named() -> None:
    cases = run.load_cases()
    assert 8 <= len(cases) <= 15
    assert len({c["id"] for c in cases}) == len(cases)
    kinds = {c["kind"] for c in cases}
    for kind in ("status", "attention", "decision", "relay", "fact", "detail", "expand",
                 "no_tool_names", "no_guillemets", "one_ask"):
        assert kind in kinds, kind
    # A fact stays short, and an explicit request for detail stays long.
    facts = [c for c in cases if c["kind"] == "fact"]
    assert facts and all(c["checks"].get("max_words", 999) <= 40 for c in facts)
    details = [c for c in cases if c["kind"] in ("detail", "expand")]
    assert details and all(not c["checks"]["routine"] and c["checks"]["min_words"] for c in details)


def test_the_scripted_run_passes() -> None:
    assert run.run_scripted(run.load_cases(), run.load_scripted()) == []
    assert run.main(["--scripted"]) == run.EXIT_PASS


def test_every_check_is_proved_to_fire_by_some_bad_answer() -> None:
    fired = {f for a in run.load_scripted().values() for b in a.get("bad", []) for f in b["expected_fail"]}
    assert fired >= set(run.CHECKS) - {"voice"}


def test_a_pasted_read_fails_the_scripted_run() -> None:
    answers = copy.deepcopy(run.load_scripted())
    case = next(c for c in run.load_cases() if c["id"] == "C04")
    answers["C04"]["good"]["text"] = case["reads"][0]["result"]
    bad = run.run_scripted(run.load_cases(), answers)
    assert bad and bad[0].startswith("C04 good")


def test_an_em_dash_fails_the_voice_check() -> None:
    case = next(c for c in run.load_cases() if c["id"] == "C08")
    got = run.check(case, "Rohan Kulkarni owns the Helios deal — since August.")
    assert got["voice"] is False


def test_the_routine_cap_is_120_words() -> None:
    assert run.ROUTINE_CAP == 120
    case = next(c for c in run.load_cases() if c["id"] == "C04")
    long = "Kestrel is on track for the 30 Oct release. " + "More words here. " * 40
    assert run.check(case, long)["length"] is False


def test_a_card_counts_for_detail_and_never_for_the_cap() -> None:
    detail = next(c for c in run.load_cases() if c["id"] == "C06")
    short = "Nine invoices are open, worth Rs 11,38,500. INV-2207 is overdue."
    assert run.check(detail, short)["length"] is False
    assert run.check(detail, short, "INV-2226 " + "row " * 100)["length"] is True


def test_a_citation_is_not_a_raw_id() -> None:
    case = next(c for c in run.load_cases() if c["id"] == "C08")
    assert run.check(case, "Rohan Kulkarni owns it [entity:a41f].")["no_dump"] is True
    raw = "Rohan Kulkarni owns it (a41f0000-1111-2222-3333-444455556666)."
    assert run.check(case, raw)["no_dump"] is False


def test_longest_copy_counts_words_in_order() -> None:
    assert run.longest_copy("a b c d", "x a b c y") == 3
    assert run.longest_copy("", "a") == 0


def test_the_live_run_refuses_without_an_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    assert run.main(["--live", "--out", "unused-cos.json"]) == run.EXIT_NO_GO


# ── The live overruns of 2026-10-14 (spec 11.8) ────────────────────────────


def test_the_live_overruns_are_recorded_and_fail_the_cap() -> None:
    """The replies that broke the cap on 2026-10-14 are bad answers, copied
    from ``results_2026-10-14.json``. Each still fails ``length``."""
    live = {
        cid: b for cid, a in run.load_scripted().items()
        for b in a.get("bad", []) if str(b.get("source", "")).startswith("live")
    }
    assert set(live) == {"C01", "C02", "C03", "C04", "C09", "C10"}
    recorded = json.loads((run.HERE / "results_2026-10-14.json").read_text(encoding="utf-8"))
    after = {c["id"]: c for c in recorded["arms"]["after"]["cases"]}
    cases = {c["id"]: c for c in run.load_cases()}
    for cid, bad in live.items():
        assert bad["text"] == after[cid]["text"], cid
        assert "length" in bad["expected_fail"], cid
        assert run.check(cases[cid], bad["text"], bad.get("card", ""))["length"] is False, cid


def test_the_live_arms_of_2026_10_14_are_recorded() -> None:
    """The owner's live run of 2026-10-14 filled the ``after`` slot.

    The brevity role took ``length`` from 7/14 to 11/14. The same-day rerun
    of the first role stayed at 7/14, so the gain is the prompt, not the day.
    """
    arms = json.loads((run.HERE / "results.json").read_text(encoding="utf-8"))["arms"]
    assert {"before", "role_v1", "after", "role_v1_rerun"} <= set(arms)
    assert arms["role_v1"]["summary"]["checks"]["length"] == "7/14"
    assert arms["after"]["summary"]["checks"]["length"] == "11/14"
    assert arms["role_v1_rerun"]["summary"]["checks"]["length"] == "7/14"


# ── The harness: a replayed tool turn carries reasoning_content ────────────


class _ThinkingVendor:
    """A stand-in for DeepSeek's thinking tier behind the Router.

    It answers 400 when an assistant turn with tool calls has no
    ``reasoning_content``, as the live tier did on 2026-10-14. Its first
    answer draws a card with reasoning, and its second answer is the text.
    """

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, payload: dict, *, timeout: int = 120) -> dict:
        self.payloads.append(copy.deepcopy(payload))
        for m in payload["messages"]:
            if m.get("role") == "assistant" and m.get("tool_calls") and run.REASONING_KEY not in m:
                raise RuntimeError("HTTP 400: upstream provider error")
        if len(self.payloads) == 1:
            return {"model": "deepseek-v4-pro", "choices": [{"message": {
                "role": "assistant", "content": "", run.REASONING_KEY: "think-1",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": run.UI_TOOL, "arguments": json.dumps({"ui": "{}"})}}],
            }}]}
        return {"model": "deepseek-v4-pro", "choices": [{"message": {
            "role": "assistant", "content": "Kestrel is on track for 30 Oct."}}]}


def _case(cid: str = "C04") -> dict:
    return next(c for c in run.load_cases() if c["id"] == cid)


def test_a_scripted_read_carries_an_empty_reasoning_content() -> None:
    case = _case()
    turns = [m for m in run.messages_for(case, "system") if m.get("tool_calls")]
    assert len(turns) == len(case["reads"]) >= 1
    assert all(m[run.REASONING_KEY] == "" for m in turns)
    off = run.messages_for(case, "system", replay_reasoning=False)
    assert all(run.REASONING_KEY not in m for m in off)


def test_a_live_turn_replays_the_models_own_reasoning(monkeypatch) -> None:
    import evals._runner as runner

    vendor = _ThinkingVendor()
    monkeypatch.setattr(runner, "post_chat_completion", vendor)
    reply = run.ask_live(_case(), "system", "tier-balanced")
    assert reply["text"] == "Kestrel is on track for 30 Oct."
    assert len(vendor.payloads) == 2
    replayed = [m for m in vendor.payloads[1]["messages"] if m.get("tool_calls")]
    assert replayed[-1][run.REASONING_KEY] == "think-1"
    assert all(run.REASONING_KEY in m for m in replayed)


def test_without_the_replay_the_thinking_vendor_refuses(monkeypatch) -> None:
    """The fake fails the way the live tier failed, so the test above proves
    the fix and not the fake (R7)."""
    import evals._runner as runner

    monkeypatch.setattr(runner, "post_chat_completion", _ThinkingVendor())
    with pytest.raises(RuntimeError, match="400"):
        run.ask_live(_case(), "system", "tier-balanced", replay_reasoning=False)


def test_the_cli_passes_the_replay_switch_to_the_run(monkeypatch, tmp_path) -> None:
    seen: list[bool] = []

    def fake_run_live(cases, system, model, judge_model, *, replay_reasoning=True):
        seen.append(replay_reasoning)
        return {"summary": {}, "cases": []}

    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(run, "run_live", fake_run_live)
    monkeypatch.setattr(run, "report", lambda arms: "")
    prompt = tmp_path / "p.txt"
    prompt.write_text("system", encoding="utf-8")
    base = ["--live", "--prompt-file", str(prompt), "--out", str(tmp_path / "r.json")]
    assert run.main(base) == run.EXIT_PASS
    assert run.main([*base, "--no-reasoning-replay"]) == run.EXIT_PASS
    assert seen == [True, False]
