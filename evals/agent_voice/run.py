"""The voice eval: ``python -m evals.agent_voice.run`` (WS-52 S1, S4 later).

Spec: ``project-docs/specs/agent_writing_voice.md`` §7.

Twelve fixed cases over the five surfaces (``cases.json``). The score is the
findings of :func:`acb_llm.voice.voice_lint`, by rule.

``--scripted`` scores the hand-written answers of ``scripted_answers.json``
and checks that the checker finds exactly the expected rules. It calls no
model, so the unit job runs it (``tests/unit/test_agent_voice_eval.py``).

``--live`` is a MANUAL run. No model is reachable from CI, so CI never runs
it. It asks the model of ``--model`` each case twice: once with a plain
system prompt (the baseline arm) and once with ``voice_prompt(surface)``
added (the voice arm). It goes through ``evals/_runner._call_llm``, so it
needs ``LITELLM_BASE_URL`` (the Router or a local proxy). It writes the
answers and the scores to ``--out``.

Exit codes: 0 pass (or a live run that finished), 1 a scripted mismatch,
2 the run cannot start.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from acb_llm.voice import voice_lint, voice_prompt

HERE = Path(__file__).resolve().parent
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: The baseline arm: what a drafting call said before WS-52.
PLAIN_SYSTEM = (
    "You are an assistant inside a company's work software. Use only the "
    "records in the message."
)


def load_cases() -> list[dict[str, Any]]:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def load_scripted() -> dict[str, dict[str, Any]]:
    data = json.loads((HERE / "scripted_answers.json").read_text(encoding="utf-8"))
    return data["answers"]


def user_message(case: dict[str, Any]) -> str:
    return f"Records:\n{case['records']}\n\n{case['ask']}"


def score(text: str, surface: str) -> Counter[str]:
    """The findings of *text*, by rule."""
    return Counter(f.rule for f in voice_lint(text, surface))


def run_scripted(
    cases: list[dict[str, Any]], answers: dict[str, dict[str, Any]],
) -> list[str]:
    """Each case whose findings differ from its expected rules.

    *answers* is a parameter, so a test can pass a changed set and see the
    run fail (R7)."""
    bad: list[str] = []
    for case in cases:
        entry = answers.get(case["id"])
        if entry is None:
            bad.append(f"{case['id']}: no scripted answer")
            continue
        got = score(entry["text"], case["surface"])
        want = Counter(entry["expected_rules"])
        if got != want:
            bad.append(f"{case['id']}: found {dict(got)}, expected {dict(want)}")
    return bad


def run_live(cases: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Ask each case on both arms, and score each answer."""
    from evals._runner import _call_llm  # noqa: PLC0415 — the one eval HTTP call

    rows: list[dict[str, Any]] = []
    totals = {"plain": Counter(), "voice": Counter()}
    words = {"plain": 0, "voice": 0}
    for case in cases:
        row: dict[str, Any] = {"id": case["id"], "surface": case["surface"]}
        for arm in ("plain", "voice"):
            system = PLAIN_SYSTEM
            if arm == "voice":
                system = f"{voice_prompt(case['surface'])}\n\n{PLAIN_SYSTEM}"
            text = _call_llm(model, system, user_message(case), json_mode=False)
            found = score(text, case["surface"])
            totals[arm].update(found)
            words[arm] += len(text.split())
            row[arm] = {"text": text, "findings": dict(found)}
        rows.append(row)
    return {
        "date": date.today().isoformat(),
        "model": model,
        "totals": {arm: dict(c) for arm, c in totals.items()},
        "per_100_words": {
            arm: round(100 * sum(totals[arm].values()) / max(1, words[arm]), 2)
            for arm in totals
        },
        "cases": rows,
    }


def _print_live(result: dict[str, Any]) -> None:
    print(f"model {result['model']}, {result['date']}")
    for arm in ("plain", "voice"):
        print(f"  {arm}: {result['per_100_words'][arm]} findings per 100 words, "
              f"{result['totals'][arm]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--model", default=os.environ.get("EVAL_VOICE_MODEL", "tier-balanced"))
    parser.add_argument("--out", default=str(HERE / "baseline.json"))
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.scripted:
        bad = run_scripted(cases, load_scripted())
        for line in bad:
            print(line)
        print(f"{len(cases) - len(bad)} of {len(cases)} cases match")
        return EXIT_FAIL if bad else EXIT_PASS

    if not os.environ.get("LITELLM_BASE_URL"):
        print("--live needs LITELLM_BASE_URL (the Router or a local proxy).")
        return EXIT_NO_GO
    result = run_live(cases, args.model)
    Path(args.out).write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    _print_live(result)
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
