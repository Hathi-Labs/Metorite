"""A measured rate: invented derived numbers fail the one number check.

WS-53 CRM-Q29 fix round 3 (``crm_platform.md`` §18.11.2, item 8). The
re-review of round 2 measured that 92 to 100 percent of invented numbers
passed the pair rule, because a pool of tool figures gives many sums,
differences and percents. Round 3 says that a derived figure passes only when
the answer also cites BOTH of its operands, with no room for "about".

This file builds a fixed set of answers with a seeded generator. Each answer
cites two true tool figures and then states one INVENTED figure: a number that
is not a tool figure, and not the sum, the difference, the percent share or
the percent change of the two cited figures, at its stated precision. A
reference oracle in this file decides "invented", so the oracle does not
share code with the check under test.

The rate before round 3 was measured with the same corpus on ``413ebb1c8``
and is recorded in the spec. The fence is the bound below.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

import pytest
from acb_common.number_check import check

#: The aim of round 3: fewer than 10 percent of invented figures pass.
MAX_PASS_RATE = 0.10

LAKH = 100_000
CRORE = 10_000_000

#: The group counts of a money result: a small org, and the 50 groups that
#: the route keeps at most (a larger pool gives more pairs).
GROUPS = (12, 50)
STAGES = ("Lead", "Qualified", "Demo", "Proposal", "Negotiation", "Won", "Lost",
          "On hold", "Churned", "Renewal")


@dataclass(frozen=True)
class Case:
    answer: str
    sources: tuple[str, ...]
    form: str


def _money_result(rng: random.Random, groups: int) -> tuple[str, list[int]]:
    values = sorted((rng.randint(5 * LAKH, 150 * LAKH) for _ in range(groups)),
                    reverse=True)
    lines = ["Deals, sum of Amount by owner:"]
    lines += [f"- owner{i}@evals.test: INR {v:,}" for i, v in enumerate(values)]
    lines.append(f"Total: INR {sum(values):,}.")
    return "\n".join(lines), [*values, sum(values)]


def _count_result(rng: random.Random) -> tuple[str, list[int]]:
    values = sorted((rng.randint(3, 400) for _ in STAGES), reverse=True)
    lines = ["Deals, count by stage:"]
    lines += [f"- {s}: {v}" for s, v in zip(STAGES, values, strict=True)]
    lines.append(f"Total: {sum(values)}.")
    return "\n".join(lines), [*values, sum(values)]


def _pair_values(a: int, b: int) -> list[Decimal]:
    """Each value that the two cited operands give (the reference oracle)."""
    da, db = Decimal(a), Decimal(b)
    out = [da + db, abs(da - db)]
    if da:
        out += [db / da * 100, abs(db - da) / da * 100]
    if db:
        out += [da / db * 100, abs(da - db) / db * 100]
    return out


def _near(value: Decimal, targets: list[Decimal], room: Decimal) -> bool:
    return any(abs(value - t) <= room for t in targets)


#: Each form of an invented figure: (name, kind, how to draw, how to write,
#: the room of its stated precision, approx).
def _forms() -> list[tuple[str, str]]:
    return [
        ("whole lakh", "money"), ("one decimal lakh", "money"),
        ("about lakh", "money"), ("one decimal crore", "money"),
        ("whole percent", "percent"), ("one decimal percent", "percent"),
        ("count", "count"),
    ]


def _draw(rng: random.Random, form: str) -> tuple[str, Decimal, Decimal, bool]:
    """One figure of *form*: its text, its value, its room, and approx."""
    if form == "whole lakh":
        x = rng.randint(10, 300)
        return f"₹{x} lakh", Decimal(x * LAKH), Decimal(LAKH) / 2, False
    if form == "one decimal lakh":
        x = Decimal(rng.randint(100, 3000)) / 10
        return f"₹{x} lakh", x * LAKH, Decimal(LAKH) / 20, False
    if form == "about lakh":
        x = Decimal(rng.randint(100, 3000)) / 10
        return f"about ₹{x} lakh", x * LAKH, Decimal(LAKH) / 20, True
    if form == "one decimal crore":
        x = Decimal(rng.randint(10, 30)) / 10
        return f"₹{x} crore", x * CRORE, Decimal(CRORE) / 20, False
    if form == "whole percent":
        x = rng.randint(1, 300)
        return f"{x}%", Decimal(x), Decimal("0.5"), False
    if form == "one decimal percent":
        x = Decimal(rng.randint(10, 3000)) / 10
        return f"{x}%", x, Decimal("0.05"), False
    x = rng.randint(1, 800)
    return f"{x} deals", Decimal(x), Decimal("0.5"), False


def invented_cases(per_form: int = 10, seed: int = 29, groups: int = 12) -> list[Case]:
    """The fixed corpus: *per_form* answers for each form, 70 in all. The
    money result holds *groups* owners."""
    rng = random.Random(seed)
    cases: list[Case] = []
    for form, kind in _forms():
        made = 0
        while made < per_form:
            money_text, money = _money_result(rng, groups)
            count_text, counts = _count_result(rng)
            pool = counts if kind == "count" else money
            a, b = rng.sample(pool[:-1], 2)
            text, value, room, approx = _draw(rng, form)
            # The oracle: a true figure is not invented. A direct source
            # value (with 10 percent for "about") or a value of the cited pair.
            direct = [Decimal(v) for v in (*money, *counts)]
            direct_room = max(room, value * Decimal("0.10")) if approx else room
            if _near(value, direct, direct_room):
                continue
            if _near(value, _pair_values(a, b), room):
                continue
            if kind == "count":
                cite = f"{a} deals in one stage and {b} in another"
            else:
                cite = f"one owner holds INR {a:,} and another INR {b:,}"
            answer = f"Of the pipeline, {cite}, so the gap is {text}."
            cases.append(Case(answer, (money_text, count_text), form))
            made += 1
    return cases


def pass_rate(cases: list[Case]) -> float:
    """The share of *cases* whose answer passes the check."""
    passed = sum(1 for c in cases if check(c.answer, list(c.sources)).ok)
    return passed / len(cases)


@pytest.mark.parametrize("groups", GROUPS)
def test_the_corpus_is_large_enough(groups: int) -> None:
    cases = invented_cases(groups=groups)
    assert len(cases) >= 50
    assert len({c.form for c in cases}) == len(_forms())


@pytest.mark.parametrize("groups", GROUPS)
def test_invented_derived_figures_fail(groups: int) -> None:
    """The fence of round 3, rule 1: fewer than 10 percent pass."""
    cases = invented_cases(groups=groups)
    rate = pass_rate(cases)
    assert rate < MAX_PASS_RATE, f"{rate:.0%} of {len(cases)} invented figures passed"


@pytest.mark.parametrize("groups", GROUPS)
def test_the_cited_operands_of_each_case_pass(groups: int) -> None:
    """A control: the two cited figures of each answer are true, so only the
    invented figure can fail. Each answer with its last figure taken out
    passes."""
    for case in invented_cases(groups=groups):
        head = case.answer.rsplit(", so the gap is", 1)[0] + "."
        assert check(head, list(case.sources)).ok, head
