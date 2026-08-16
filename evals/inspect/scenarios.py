"""Inspect AI scenario harness for Metorite skills (ADR-017, HH-1).

Scenario-level evals complementing the promptfoo golden cases: each sample is
a full skill invocation (system prompt from SKILL.md + fixture entity data)
scored on the structural contract the platform depends on (citation tokens,
JSON shape).

CI smoke:   inspect eval evals/inspect/scenarios.py --model mockllm/model --limit 1
Live run:   inspect eval evals/inspect/scenarios.py \
                --model openai/<model> -M base_url=$LITELLM_BASE_URL/v1
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import generate, system_message

_EVALS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_EVALS_DIR))

from _runner import _load_skill_prompt, _resolve_case_input  # noqa: E402


def _sample(skill: str, variables: dict, target: str) -> Sample:
    return Sample(
        input=_resolve_case_input(variables),
        target=target,
        metadata={"skill": skill, "vars": variables},
    )


@scorer(metrics=[accuracy()])
def cited_and_structured():
    """Passes when the completion carries the cite token and, if the target
    names required JSON keys (``json:key1,key2``), parses to JSON with them."""

    async def score(state, target: Target) -> Score:
        text = state.output.completion or ""
        spec = target.text or ""

        cite_ok = bool(re.search(r"\[(message|task|deal|project|customer|meeting):", text))
        if not cite_ok:
            return Score(value=INCORRECT, explanation="missing citation token")

        if spec.startswith("json:"):
            keys = [k for k in spec[5:].split(",") if k]
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return Score(value=INCORRECT, explanation="no JSON object in output")
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                return Score(value=INCORRECT, explanation="invalid JSON")
            missing = [k for k in keys if k not in obj]
            if missing:
                return Score(value=INCORRECT, explanation=f"missing keys: {missing}")

        return Score(value=CORRECT)

    return score


@task
def email_classify() -> Task:
    skill = "triage/email_classify"
    return Task(
        dataset=MemoryDataset([
            _sample(skill, {"email_id": "00000000-0000-0000-0000-000000000050"},
                    "json:label,confidence"),
            _sample(skill, {"email_id": "00000000-0000-0000-0000-000000000051"},
                    "json:label,confidence"),
        ]),
        solver=[system_message(_load_skill_prompt(skill)), generate()],
        scorer=cited_and_structured(),
    )


@task
def entity_link() -> Task:
    skill = "triage/entity_link"
    return Task(
        dataset=MemoryDataset([
            _sample(skill,
                    {"email_id": "00000000-0000-0000-0000-000000000060",
                     "label": "sales_followup"},
                    "json:linked_entities"),
        ]),
        solver=[system_message(_load_skill_prompt(skill)), generate()],
        scorer=cited_and_structured(),
    )


@task
def customer_360_summary() -> Task:
    skill = "sales/customer_360_summary"
    return Task(
        dataset=MemoryDataset([
            _sample(skill, {"customer_id": "00000000-0000-0000-0000-000000000030"}, ""),
        ]),
        solver=[system_message(_load_skill_prompt(skill)), generate()],
        scorer=cited_and_structured(),
    )
