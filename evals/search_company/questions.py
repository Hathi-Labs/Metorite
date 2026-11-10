"""The questions of the retrieval eval, and the two places they come from (WS-KG KG-1).

* The synthetic set is ``fixtures/synthetic.json``. A question names its gold
  record by KEY. :func:`gold_id` turns a key into the id that the seed gives
  the record in one org, so the gold of a fixture run is known before the seed.
* The real set is a file OFF the repo (``KG1_REAL_SET``). A question names its
  gold records by ID. :func:`load_real` reads it. The runner never prints the
  words of a question.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "synthetic.json"

#: The sources that carry questions, in the order of the report.
SOURCES = ("email", "crm", "tasks", "knowledge", "people")
#: Questions for each source in the synthetic set.
PER_SOURCE = 10

#: Fixed, so a gold id depends on the org and the key, and on nothing else.
NAMESPACE = uuid.UUID("5d3a8c1e-7f42-4b6a-9c0d-2e1f8a6b4c30")


@dataclass(frozen=True)
class Question:
    """One question: the words, the source that must answer, the gold ids."""

    id: str
    source: str
    text: str
    gold: tuple[str, ...]


@dataclass(frozen=True)
class Record:
    """One seeded record of the synthetic set."""

    key: str
    source: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class Fixture:
    """The synthetic set: its records and its questions (gold by key)."""

    records: tuple[Record, ...]
    questions: tuple[dict[str, Any], ...]


def gold_id(org: str, key: str) -> str:
    """The id that the seed gives the record *key* in the org *org*."""
    return str(uuid.uuid5(NAMESPACE, f"{org}:{key}"))


def load_fixture(path: Path = FIXTURE) -> Fixture:
    """Read the synthetic set, and refuse a set that is not whole."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = tuple(Record(r["key"], r["source"], r["fields"]) for r in raw["records"])
    fixture = Fixture(records, tuple(raw["questions"]))
    problems = check(fixture)
    if problems:
        raise ValueError("the synthetic set is not whole: " + "; ".join(problems))
    return fixture


def check(fixture: Fixture) -> list[str]:
    """The faults of a synthetic set: counts, duplicate ids, gold that is not seeded."""
    problems: list[str] = []
    keys = [r.key for r in fixture.records]
    if len(set(keys)) != len(keys):
        problems.append("a record key repeats")
    by_key = {r.key: r for r in fixture.records}
    ids = [q["id"] for q in fixture.questions]
    if len(set(ids)) != len(ids):
        problems.append("a question id repeats")
    for source in SOURCES:
        n = sum(1 for q in fixture.questions if q["source"] == source)
        if n != PER_SOURCE:
            problems.append(f"{source} has {n} questions, not {PER_SOURCE}")
    for q in fixture.questions:
        if not q["gold"]:
            problems.append(f"{q['id']} has no gold")
        for key in q["gold"]:
            rec = by_key.get(key)
            if rec is None:
                problems.append(f"{q['id']} names the unknown record {key}")
            elif rec.source != q["source"]:
                problems.append(f"{q['id']} names a record of another source")
    return problems


def resolve(fixture: Fixture, org: str) -> list[Question]:
    """The questions of the synthetic set, with the gold ids of *org*."""
    return [
        Question(q["id"], q["source"], q["text"], tuple(gold_id(org, k) for k in q["gold"]))
        for q in fixture.questions
    ]


def load_real(path: Path) -> list[Question]:
    """Read the real set: ``{"questions": [{id, source, text, gold: [id, ...]}]}``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = [
        Question(str(q["id"]), str(q["source"]), str(q["text"]), tuple(str(g) for g in q["gold"]))
        for q in raw["questions"]
    ]
    if not out:
        raise ValueError("the real set holds no question")
    return out
