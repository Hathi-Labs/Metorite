"""The runner of the mapping eval: ``python -m evals.crm_mapping`` (WS-53 CRM-Q20-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, CRM-Q20 item 4.

``--scripted`` runs the REAL ``map_pipeline.Pipeline`` on each synthetic
schema, in ``on`` for the three features. The source reads the synthetic
records through the REAL ``detail_module`` and ``detail_slot``, the step 1
of the run. A scripted asker answers each question from
``scripted_answers.json``. Then :mod:`evals.crm_mapping.checkers` scores the
items, the requests and a second run on the same schema. The ``dewin``
schema also runs in ``shadow`` for the three features, and the report holds
its agreement counts. It calls no model, no Router and no database.

The model sweep asks the decide tier through the Router on a local stack.
It is not built yet, so without ``--scripted`` the runner says so and exits
with code 2.

Exit codes: 0 every bar passed, 1 a bar failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from types import MappingProxyType
from typing import Any

from gateway.crm_sync import map_pipeline as mp

from evals.crm_mapping import checkers
from evals.crm_mapping.dataset import EvalSchema, load_answers, load_schemas

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

ON = {mp.FEATURE_ROLE: "on", mp.FEATURE_TARGET: "on", mp.FEATURE_STAGE: "on"}
SHADOW = {mp.FEATURE_ROLE: "shadow", mp.FEATURE_TARGET: "shadow", mp.FEATURE_STAGE: "shadow"}

#: The shadow agreement that the scripted answers of ``dewin`` give: the
#: target questions asked, and the ones that agree with the answer key.
DEWIN_SHADOW = {mp.FEATURE_TARGET: [6, 5]}


class EvalSource:
    """The synthetic records of one schema, through the real step 1."""

    def __init__(self, schema: EvalSchema) -> None:
        self.schema = schema

    async def module_detail(self, module: mp.ModuleShape, slot: str | None) -> mp.ModuleShape:
        rows = self.schema.records.get(module.api_name, [])
        distinct: dict[str, list[str]] = {}
        if slot in mp.STAGE_SLOTS:
            for f in module.fields:
                if f.type == "select":
                    counts = Counter(str(r[f.key]) for r in rows if r.get(f.key))
                    distinct[f.key] = [v for v, _n in sorted(counts.items(),
                                                             key=lambda kv: (-kv[1], kv[0]))]
        return mp.detail_module(module, rows, distinct=distinct)

    async def slot_detail(self, slot: mp.SlotShape) -> mp.SlotShape:
        return mp.detail_slot(slot, self.schema.core_records.get(slot.slot, []))


def _names(state: dict[str, Any]) -> dict[str, str]:
    """Each object of a state by its name: the ref of a field or a module."""
    out: dict[str, str] = {}
    for group in ("fields", "modules"):
        for ref, obj in (state.get(group) or {}).items():
            out.setdefault(str(obj.get("name")), ref)
    return out


class ScriptedAsker:
    """Answers each question from the scripted set. A question with no entry
    gets a sure ``none``."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, list[Any]]] = []
        self.seen: set[str] = set()

    def _key(self, qid: str, state: dict[str, Any]) -> str:
        if qid.startswith("s.v"):
            return f"s:{state['values'][qid[2:]]}"
        return qid

    def _one(self, qid: str, question: Any, state: dict[str, Any], reask: bool) -> Any:
        import acb_llm

        entry = self.answers.get(self._key(qid, state)) or {"pick": "none", "p": 0.9}
        pick = entry["pick"]
        ref = pick if pick in question.criteria else _names(state).get(pick, "none")
        p = float(entry.get("p2", entry["p"]) if reask else entry["p"])
        others = [k for k in question.criteria if k != ref][:2]
        probabilities = {ref: p}
        for share, key in zip((0.6, 0.4), others, strict=False):
            probabilities[key] = round((1 - p) * share, 4)
        return acb_llm.ChoiceAnswer(choice=ref, probabilities=MappingProxyType(probabilities),
                                    confidence=p)

    async def __call__(self, feature: str, requests: list[Any]) -> Any:
        import acb_llm

        self.calls.append((feature, requests))
        answers: dict[str, Any] = {}
        for state, questions in requests:
            for qid, question in questions.items():
                # A question asked before is a re-ask (step 5).
                answers[qid] = self._one(qid, question, state, qid in self.seen)
        for _state, questions in requests:
            self.seen |= set(questions)
        return acb_llm.Decision(answers=MappingProxyType(answers),
                                request_id=f"scripted-{len(self.calls)}")


async def run_one(schema: EvalSchema, answers: dict[str, Any], modes: dict[str, str],
                  cache: mp.Cache | None = None) -> tuple[mp.Result, ScriptedAsker]:
    asker = ScriptedAsker(answers)
    pipeline = mp.Pipeline(schema.inputs, modes, cache or mp.Cache(), EvalSource(schema), asker)
    return await pipeline.run(), asker


def _cache_of(result: mp.Result) -> mp.Cache:
    cache = mp.Cache()
    for key, (digest, items) in result.done.items():
        cache.schema[key] = digest
        cache.items[key] = items
    return cache


async def score_schema(schema: EvalSchema, answers: dict[str, Any]) -> checkers.Score:
    """Run one schema in ``on``, then once more on its cache, and score both."""
    score = checkers.Score(schema.id)
    result, asker = await run_one(schema, answers.get("on", {}), ON)
    score.requests, score.questions, score.tokens = result.requests, result.questions, result.tokens
    expected = schema.expected
    score.misses += checkers.check_roles(expected.get("roles") or {}, result.items)
    score.misses += checkers.check_targets(expected.get("targets") or {}, result.items)
    score.misses += checkers.check_stages(expected.get("stages") or {}, result.items)
    problems, leaks = checkers.check_shape(asker.calls)
    score.shape_problems += problems + checkers.first_round_samples(asker.calls)
    score.value_leaks += leaks
    if result.tokens > mp.TOKEN_CAP or result.requests > mp.RUN_REQUESTS:
        score.cost_problems.append(f"tokens={result.tokens} requests={result.requests}")
    if result.reason != "done":
        score.cost_problems.append(f"reason={result.reason}")
    again, second = await run_one(schema, answers.get("on", {}), ON, _cache_of(result))
    if second.calls:
        score.cache_problems.append(f"calls={len(second.calls)}")
    if sorted(map(json.dumps, again.items)) != sorted(map(json.dumps, result.items)):
        score.cache_problems.append("items_differ")
    return score


async def shadow_report(schema: EvalSchema, answers: dict[str, Any]) -> dict[str, Any]:
    """The ``dewin`` schema in ``shadow``: the agreement counts, and no item."""
    result, asker = await run_one(schema, answers.get("shadow", {}), SHADOW)
    roles_asked = sum(1 for _f, reqs in asker.calls for _s, q in reqs
                      for qid in q if qid.startswith("role."))
    return {"items": len(result.items), "role_questions": roles_asked,
            "agreement": {k: list(v) for k, v in sorted(result.shadow.items())},
            "requests": result.requests, "tokens_estimated": result.tokens,
            "empty_slots": result.empty_slots, "sure_candidates": result.sure_candidates}


def shadow_ok(report: dict[str, Any]) -> bool:
    return (report["items"] == 0 and report["role_questions"] == 0
            and report["agreement"] == {k: v for k, v in DEWIN_SHADOW.items()})


async def run_all(answers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Each schema, and the shadow run of ``dewin``. *answers* is a parameter
    so that a test can pass a changed set and see a bar fail (R7)."""
    answers = answers if answers is not None else load_answers()
    scores = []
    shadow: dict[str, Any] = {}
    for schema in load_schemas():
        scores.append(await score_schema(schema, answers.get(schema.id, {})))
        if schema.id == "dewin":
            shadow = await shadow_report(schema, answers.get(schema.id, {}))
    passed = all(s.passed() for s in scores) and shadow_ok(shadow)
    return {"mode": "scripted", "schemas": [s.summary() for s in scores],
            "shadow_dewin": shadow, "shadow_ok": shadow_ok(shadow), "passed": passed}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.crm_mapping",
                                description=__doc__.splitlines()[0])
    p.add_argument("--scripted", action="store_true",
                   help="replay scripted_answers.json, and call no model")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.scripted:
        print("The model sweep is not built yet (crm_platform.md 18.9.6). "
              "Run with --scripted.", file=sys.stderr)
        return EXIT_NO_GO
    report = asyncio.run(run_all())
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return EXIT_PASS if report["passed"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
