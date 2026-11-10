"""The scoring of the founder question set (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 9 and 10.

A run gives, for each question, the GET requests that the CRM tools sent
(:class:`RouteCall`) and the answer text. :func:`score_question` scores them
on nine bars:

* ``tools``: each :class:`questions.Need` has a request that matches one of
  its alternatives. A request matches when it has the path, and each named
  parameter with its value. A key that ends in ``~`` matches a part of the
  value, without case.
* ``tool_text``: the output of a tool holds each line that the question
  needs, for example the short-read line of CRM-Q30b.
* ``tool_figures``: the output of the tools holds each number fact that a
  route gives. So the scripted run proves the text that the member reads,
  not only the JSON of the route.
* ``numbers``: the answer holds each number fact.
* ``names``: the answer holds each name fact, without case, and the names
  of a ranked list in the order of the rank.
* ``flags``: the answer holds one of the flag phrases of the question.
* ``forbid``: the answer holds no forbidden number and no forbidden pattern.
* ``answer_check``: the answer passes ``acb_common.number_check.check``,
  the chat check of CRM-Q29, with the tool output and the question as its
  sources. So the eval and the chat check cannot disagree in silence. A
  fact in ``answer_check_waits`` may wait for a rule of CRM-Q29.
* ``agree``: each fact with a ``src`` equals the route answer that it names.
  Only the scripted run scores it, on the recorded answers. The R8 test
  scores it on the real route.

A scripted run must pass every bar of every question. A live run passes
when :data:`LIVE_BAR` of the questions pass every bar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from acb_common.number_check import Sources, check, find_figures

from evals.crm_founder_questions.questions import Fact, Need, Question

#: The share of questions that a live run must pass in full.
LIVE_BAR = 0.80

#: The route of the short reads (CRM-Q30b). Each list tool reads it once,
#: beside its own route, so the agreement counts the calls without it.
SHORT_READS = "/crm/sync/short-reads"

#: The key of a recorded refusal: a route that answered with a coded
#: error (``detail.code``), for example 404 ``catalogue_off`` of
#: ``GET /crm/catalogue`` (CRM-Q31-1). The tool reads it as the error it is.
REFUSED = "_refused"


def refused(data: Any) -> dict[str, Any] | None:
    """The refusal that *data* records, or ``None``."""
    found = data.get(REFUSED) if isinstance(data, dict) else None
    return found if isinstance(found, dict) else None

BARS: tuple[str, ...] = (
    "tools", "tool_text", "tool_figures", "numbers", "names", "flags", "forbid",
    "answer_check", "agree",
)


@dataclass(frozen=True)
class RouteCall:
    """One GET that a CRM tool sent, and what the route answered."""

    path: str
    params: dict[str, str]
    data: Any


@dataclass
class QuestionScore:
    id: str
    category: str
    problems: dict[str, list[str]] = field(default_factory=lambda: {b: [] for b in BARS})
    #: False when the run did not score ``agree``, as a live run does not.
    agreed: bool = True
    #: The figures that wait for a rule of CRM-Q29 (``answer_check_waits``).
    #: They do not fail the question, and each summary names them.
    waiting: list[str] = field(default_factory=list)

    def bars(self) -> dict[str, bool]:
        return {b: not self.problems[b] for b in BARS if b != "agree" or self.agreed}

    def passed(self) -> bool:
        return all(self.bars().values())

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "category": self.category, "passed": self.passed(),
                "bars": self.bars(),
                "problems": {b: p for b, p in self.problems.items() if p},
                **({"waiting": self.waiting} if self.waiting else {})}


@dataclass
class Score:
    questions: list[QuestionScore] = field(default_factory=list)
    live: bool = False

    def rate(self) -> float:
        if not self.questions:
            return 0.0
        return sum(q.passed() for q in self.questions) / len(self.questions)

    def bar_rates(self) -> dict[str, float]:
        out = {}
        for bar in BARS:
            scored = [q for q in self.questions if bar in q.bars()]
            if scored:
                out[bar] = round(sum(q.bars()[bar] for q in scored) / len(scored), 3)
        return out

    def passed(self) -> bool:
        if self.live:
            return self.rate() >= LIVE_BAR
        return bool(self.questions) and all(q.passed() for q in self.questions)

    def summary(self) -> dict[str, Any]:
        return {
            "questions": len(self.questions), "passed_questions": sum(
                q.passed() for q in self.questions),
            "rate": round(self.rate(), 3), "bar_rates": self.bar_rates(),
            "passed": self.passed(),
            "failed": [q.summary() for q in self.questions if not q.passed()],
            "waiting": {q.id: q.waiting for q in self.questions if q.waiting},
        }


# ── tools ───────────────────────────────────────────────────────────────────


def call_matches(call: RouteCall, alternative: dict[str, str]) -> bool:
    """True when *call* has the path and each named parameter."""
    for key, want in alternative.items():
        if key == "path":
            if call.path != want:
                return False
        elif key.endswith("~"):
            got = str(call.params.get(key[:-1]) or "")
            if want.lower() not in got.lower():
                return False
        elif str(call.params.get(key)) != str(want):
            return False
    return True


def check_tools(needs: tuple[Need, ...], calls: list[RouteCall]) -> list[str]:
    problems = []
    for index, need in enumerate(needs):
        if not any(call_matches(c, alt) for c in calls for alt in need.alternatives):
            problems.append(f"need {index}: no call matches {need.alternatives[0]}")
    return problems


# ── the answer ──────────────────────────────────────────────────────────────


# The number rule is the ONE check of CRM-Q29 (``acb_common.number_check``).
# These two helpers only ask it in the direction that an eval needs: does a
# text state this fact?


def states(value: Any, text: str) -> bool:
    """True when *text* holds a figure that *value* rounds to, at the
    precision that the text states (``number_check`` rule of a match)."""
    book = Sources()
    book.add_value(value)
    return any(book.matches(figure) for figure in find_figures(text))


def states_exactly(value: Any, text: str) -> bool:
    """True when *text* holds *value* as a figure, for a forbidden number."""
    want = Decimal(str(value))
    return any(abs(figure.value) == want for figure in find_figures(text))


def check_numbers(facts: list[Fact], answer: str) -> list[str]:
    return [f"{f.key}={f.value} missing" for f in facts
            if f.value is not None and not states(f.value, answer)]


def check_tool_figures(facts: list[Fact], texts: list[str]) -> list[str]:
    """Each number fact that a route gives is in the output of a tool. A
    tool that prints a wrong figure fails here, though its route is right."""
    joined = "\n".join(texts)
    return [f"{f.key}={f.value} not in the tool output" for f in facts
            if f.src is not None and f.value is not None and not states(f.value, joined)]


def check_names(facts: list[Fact], answer: str) -> list[str]:
    """Each name is in the answer. The names of a ranked list (a ``rank``
    selector) come in the order of the rank."""
    low = answer.lower()
    out = [f"{f.key}={f.value!r} missing" for f in facts if str(f.value).lower() not in low]
    ranked = [f for f in facts if f.src is not None and f.src[1] == "rank"]
    places = [low.find(str(f.value).lower()) for f in ranked]
    if not out and places != sorted(places):
        out.append(f"the ranked names are out of order: {[f.value for f in ranked]}")
    return out


def check_flags(flags: tuple[str, ...], answer: str) -> list[str]:
    if not flags:
        return []
    low = answer.lower()
    return [] if any(f in low for f in flags) else [f"no flag of {list(flags)[:3]}"]


def check_forbid(q: Question, answer: str) -> list[str]:
    out = [f"forbidden number {n}" for n in q.forbid_numbers
           if states_exactly(n, answer)]
    out += [f"forbidden pattern {p!r}" for p in q.forbid_patterns
            if re.search(p, answer, re.IGNORECASE)]
    return out


def check_answer(q: Question, answer: str, texts: list[str]) -> tuple[list[str], list[str]]:
    """The chat check of CRM-Q29 on the answer: each figure must have a
    source in the tool output of the run or in the question. Gives the
    problems, and the figures that wait (``answer_check_waits``)."""
    result = check(answer, [q.words, *texts])
    waits = [f for f in q.facts if f.key in q.answer_check_waits]
    problems: list[str] = []
    waiting: list[str] = []
    for figure in result.unmatched:
        if any(states(f.value, figure.text) for f in waits):
            waiting.append(f"{figure.text}: {q.wait_reason}")
        else:
            problems.append(f"{figure.text} has no source in the tool output or the question")
    if waits and not waiting:
        problems.append(f"stale wait: {list(q.answer_check_waits)} pass now. Remove the wait.")
    return problems, waiting


# ── agreement with the route ────────────────────────────────────────────────


def select(data: Any, selector: tuple[Any, ...]) -> Any:
    """The value that *selector* names in one route answer, or a KeyError."""
    kind, *rest = selector
    if kind in ("total", "values"):
        return data[kind]
    if kind == "group":
        for g in data["groups"]:
            if g["key"] == rest[0]:
                return g["value"]
        raise KeyError(f"no group {rest[0]!r}")
    if kind == "rank":
        index, part = rest
        return data["groups"][index][part]
    if kind == "module":
        name, part = rest
        for m in data:
            if m["api_name"] == name:
                return m[part]
        raise KeyError(f"no module {name!r}")
    raise KeyError(f"unknown selector {kind!r}")


def same(fact: Any, got: Any) -> bool:
    if fact is None or got is None:
        return fact is None and got is None
    if isinstance(fact, str):
        return fact == got
    return abs(Decimal(str(fact)) - Decimal(str(got))) <= Decimal("0.005")


def check_tool_text(q: Question, texts: list[str]) -> list[str]:
    """Each required line is in the output of a tool (CRM-Q30b)."""
    joined = "\n".join(texts)
    return [f"no tool output holds {line!r}" for line in q.tool_lines if line not in joined]


def check_agree(q: Question, calls: list[RouteCall]) -> list[str]:
    """Each fact with a ``src`` against the route answer of its call. The
    index counts the calls of the tools' own routes. ``("short", ...)`` reads
    the answer of the short reads."""
    # A refused call (the catalogue off) is no answer of a tool's own route.
    answers = [c.data for c in calls if c.path != SHORT_READS and not refused(c.data)]
    short = [c.data for c in calls if c.path == SHORT_READS]
    out = []
    for f in q.facts:
        if f.src is None:
            continue
        head, *selector = f.src
        try:
            if head == "short":
                key, part = selector
                got = next(r for r in short[-1] if r["key"] == key)[part]
            else:
                got = select(answers[head], tuple(selector))
        except (IndexError, KeyError, TypeError, StopIteration) as exc:
            out.append(f"{f.key}: {type(exc).__name__} {exc}")
            continue
        if not same(f.value, got):
            out.append(f"{f.key}: fact {f.value} != route {got}")
    return out


# ── one question ────────────────────────────────────────────────────────────


def score_question(q: Question, calls: list[RouteCall], answer: str, *,
                   agree: bool, texts: list[str] | None = None) -> QuestionScore:
    """*texts* holds the output of each tool, for ``tool_text``."""
    s = QuestionScore(q.id, q.category, agreed=agree)
    s.problems["tools"] = check_tools(q.needs, calls)
    s.problems["tool_text"] = check_tool_text(q, texts or [])
    s.problems["tool_figures"] = check_tool_figures(q.numbers(), texts or [])
    s.problems["numbers"] = check_numbers(q.numbers(), answer)
    s.problems["names"] = check_names(q.names(), answer)
    s.problems["flags"] = check_flags(q.flags, answer)
    s.problems["forbid"] = check_forbid(q, answer)
    s.problems["answer_check"], s.waiting = check_answer(q, answer, texts or [])
    if agree:
        s.problems["agree"] = check_agree(q, calls)
    return s


__all__ = [
    "BARS",
    "LIVE_BAR",
    "SHORT_READS",
    "QuestionScore",
    "RouteCall",
    "Score",
    "call_matches",
    "check_agree",
    "check_answer",
    "check_tool_figures",
    "check_tool_text",
    "check_tools",
    "score_question",
    "select",
    "states",
    "states_exactly",
]
