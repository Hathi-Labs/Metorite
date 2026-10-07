"""The scoring of the Insights eval (WS-17 EM-T14b-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §13.9.2 item 4.

:func:`score` compares each stored fact with the expected facts of its mail.
The tier of record must pass three bars of the spec:

* ``amount``: on 95 % of the found facts or more, the stored amount and
  currency equal the expected ones.
* ``recall``: the job finds 80 % of the expected facts or more.
* ``quote``: no fact has a quote that is not in its source.

The eval adds a fourth bar, ``no_fact``: a mail that expects no fact gives
no fact. Without it, the injection mail and the spreadsheet could give a
fact and the run would still pass.

The quote bar folds white space with its own two lines, and not with the
fold of the module under test, so a defect in that fold cannot hide here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.email_insights.dataset import Expected, Mail

AMOUNT_BAR = 0.95
RECALL_BAR = 0.80

#: ``{mail id: [(source key, fact), ...]}``. A fact is an ``insights_store.Fact``.
Produced = dict[str, list[tuple[str, Any]]]


@dataclass
class Score:
    """The counts of one run, and the result of each bar."""

    expected: int = 0
    found: int = 0
    amount_ok: int = 0
    due_ok: int = 0
    misses: list[str] = field(default_factory=list)
    wrong_amounts: list[str] = field(default_factory=list)
    quote_misses: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.expected if self.expected else 1.0

    @property
    def amount_rate(self) -> float:
        return self.amount_ok / self.found if self.found else 1.0

    def bars(self) -> dict[str, bool]:
        return {
            "amount": self.amount_rate >= AMOUNT_BAR,
            "recall": self.recall >= RECALL_BAR,
            "quote": not self.quote_misses,
            "no_fact": not self.unexpected,
        }

    def passed(self) -> bool:
        return all(self.bars().values())

    def summary(self) -> dict[str, Any]:
        return {
            "expected": self.expected, "found": self.found,
            "recall": round(self.recall, 3), "amount_rate": round(self.amount_rate, 3),
            "due_ok": self.due_ok, "bars": self.bars(), "passed": self.passed(),
            "misses": self.misses, "wrong_amounts": self.wrong_amounts,
            "quote_misses": self.quote_misses, "unexpected": self.unexpected,
        }


def _plain_fold(text: str) -> str:
    return " ".join(text.split())


def _matches(exp: Expected, key: str, fact: Any) -> bool:
    return (key == exp.source and fact.fact_type == exp.fact_type
            and (exp.ref is None or fact.ref == exp.ref))


def _score_mail(mail: Mail, facts: list[tuple[str, Any]], score: Score) -> None:
    sources = mail.sources()
    for key, fact in facts:
        if _plain_fold(fact.quote) not in _plain_fold(sources[key].text):
            score.quote_misses.append(f"{mail.id}/{key}: {fact.quote!r}")
    unused = list(range(len(facts)))
    for exp in mail.expected:
        score.expected += 1
        hit = next((i for i in unused if _matches(exp, *facts[i])), None)
        if hit is None:
            score.misses.append(f"{mail.id}/{exp.source}/{exp.fact_type}/{exp.ref}")
            continue
        unused.remove(hit)
        fact = facts[hit][1]
        score.found += 1
        if (fact.amount, fact.currency) == (exp.amount, exp.currency):
            score.amount_ok += 1
        else:
            score.wrong_amounts.append(
                f"{mail.id}: stored {fact.amount} {fact.currency}, expected "
                f"{exp.amount} {exp.currency}")
        score.due_ok += int(fact.due_on == exp.due_on)
    if not mail.expected:
        score.unexpected += [f"{mail.id}/{facts[i][0]}/{facts[i][1].fact_type}" for i in unused]


def score(mails: list[Mail], produced: Produced) -> Score:
    """Score each mail of the set against the facts of one run."""
    result = Score()
    for mail in mails:
        _score_mail(mail, produced.get(mail.id, []), result)
    return result
