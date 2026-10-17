"""The scoring of the mapping eval (WS-53 CRM-Q20-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, CRM-Q20 item 4.

:func:`score` compares the items of one run with the expected answers of its
schema. Seven bars:

* ``roles``: each empty slot gets the expected module, or no role.
* ``targets``: each module that plays a slot gets the expected field for
  each target, and no other target item.
* ``stages``: the stage field and the map of its values are as expected.
* ``shape``: each request holds 16 questions or fewer, each option of a
  question names an object of its state (or ``none``, or a stage word), and
  a first round holds no sample.
* ``no_value``: no state holds a string that looks like an email address or
  a phone number. A re-ask sends their pattern class only.
* ``cost``: the run keeps under the token cap and the request cap.
* ``cache``: a second run on the same schema makes no ``decide`` call, and
  gives the same items.
* ``catalogue`` (CRM-Q31-3): the purpose and the key fields of each module
  that the schema expects. A wrong answer fails the bar, a wrong amount too.
  A part that the run left empty is not a failure, because none beats a
  wrong answer.

A scripted run replays answers that an agent wrote, so each bar must hold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gateway.crm_sync import map_pipeline as mp

#: The options that a question may name and that are no object of its state.
_FREE_OPTIONS = frozenset({"none", "won", "lost", "open"})
#: CRM-Q31-3: a purpose question names the purposes, which are no object of
#: its state either.
_FREE_OPTIONS = _FREE_OPTIONS | frozenset(mp.cat.PURPOSES)


@dataclass
class Score:
    """The counts and the misses of one schema."""

    schema: str
    misses: list[str] = field(default_factory=list)
    shape_problems: list[str] = field(default_factory=list)
    value_leaks: list[str] = field(default_factory=list)
    cost_problems: list[str] = field(default_factory=list)
    cache_problems: list[str] = field(default_factory=list)
    requests: int = 0
    questions: int = 0
    tokens: int = 0

    def bars(self) -> dict[str, bool]:
        return {
            "roles": not [m for m in self.misses if m.startswith("role")],
            "targets": not [m for m in self.misses if m.startswith("target")],
            "stages": not [m for m in self.misses if m.startswith("stage")],
            "shape": not self.shape_problems,
            "no_value": not self.value_leaks,
            "cost": not self.cost_problems,
            "cache": not self.cache_problems,
            "catalogue": not [m for m in self.misses if m.startswith("catalogue")],
        }

    def passed(self) -> bool:
        return all(self.bars().values())

    def summary(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "requests": self.requests, "questions": self.questions,
            "tokens_estimated": self.tokens, "bars": self.bars(), "passed": self.passed(),
            "misses": self.misses, "shape_problems": self.shape_problems,
            "value_leaks": self.value_leaks, "cost_problems": self.cost_problems,
            "cache_problems": self.cache_problems,
        }


def check_roles(expected: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    got = {i["slot"]: i["module"] for i in items if i.get("kind") == "role"}
    misses = []
    for slot, module in expected.items():
        if got.get(slot) != module:
            misses.append(f"role:{slot}:{got.get(slot)}!={module}")
    for slot in set(got) - set(expected):
        misses.append(f"role:{slot}:unexpected")
    return misses


def check_targets(expected: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    got: dict[str, dict[str, str]] = {}
    for i in items:
        if i.get("kind") == "target":
            got.setdefault(f"{i['slot']}.{i['module']}", {})[i["target"]] = i["fields"][0]
    misses = []
    for key in sorted(set(expected) | set(got)):
        want, have = expected.get(key, {}), got.get(key, {})
        for target in sorted(set(want) | set(have)):
            if want.get(target) != have.get(target):
                misses.append(f"target:{key}:{target}:{have.get(target)}!={want.get(target)}")
    return misses


def check_stages(expected: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    got = {f"{i['slot']}.{i['module']}": {"field": i["field_api_name"], "values": i["values"]}
           for i in items if i.get("kind") == "stage"}
    misses = []
    for key in sorted(set(expected) | set(got)):
        if expected.get(key) != got.get(key):
            misses.append(f"stage:{key}:{got.get(key)}!={expected.get(key)}")
    return misses


def check_catalogue(expected: dict[str, Any], entries: list[Any]) -> list[str]:
    """Each expected part of each module against the final catalogue. An
    expected ``null`` must stay empty. A part that the run left empty is no
    miss."""
    got = {e.module: e for e in entries}
    misses = []
    for module, parts in sorted(expected.items()):
        entry = got.get(module)
        for part, want in sorted(parts.items()):
            if entry is None:
                have = None
            elif part == "purpose":
                have = entry.purpose
            else:
                found = entry.fields.get(part)
                have = found.key if found is not None else None
            if have is not None and have != want:
                misses.append(f"catalogue:{module}:{part}:{have}!={want}")
    return misses


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def check_shape(calls: list[tuple[str, list[Any]]]) -> tuple[list[str], list[str]]:
    """The shape problems and the value leaks of each request."""
    problems: list[str] = []
    leaks: list[str] = []
    for n, (_feature, requests) in enumerate(calls):
        for state, questions in requests:
            if len(questions) > 16:
                problems.append(f"call{n}:questions={len(questions)}")
            refs = set(state.get("fields") or {}) | set(state.get("modules") or {})
            for qid, q in questions.items():
                bad = [k for k in q.criteria if k not in refs and k not in _FREE_OPTIONS]
                if bad:
                    problems.append(f"call{n}:{qid}:options")
            for s in _strings(state):
                if mp.pattern_of(s) in ("email", "phone"):
                    leaks.append(f"call{n}:{mp.pattern_of(s)}")
    return problems, leaks


def first_round_samples(calls: list[tuple[str, list[Any]]]) -> list[str]:
    """A first request of an entity never holds a sample: only a re-ask
    does. Each call here is the first one when no call before it asked one
    of its questions."""
    seen: set[str] = set()
    problems = []
    for n, (_feature, requests) in enumerate(calls):
        for state, questions in requests:
            first = not (set(questions) & seen)
            sampled = "samples" in state or any(
                "samples" in f for f in (state.get("fields") or {}).values())
            if first and sampled and not any(q.startswith("s.v") for q in questions):
                problems.append(f"call{n}:samples_in_first_round")
            seen |= set(questions)
    return problems
