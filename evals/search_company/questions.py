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
#: The misses set (WS-FIND FIND-1): 20 synthetic cases that today's search misses.
MISSES = HERE / "fixtures" / "misses.json"

#: The kinds of a miss, each with the rung of ``find_engine.md`` §3.1 that finds it
#: first. A case may name a higher rung than its kind's floor, never a lower one.
MISS_KINDS = {
    "near_name": 3,
    "words_spread": 1,
    "related_only": 4,
    "converted_lead": 1,
    "past_the_cut": 1,
    "past_chat": 6,
    "outside": 7,
}
#: Misses in the set.
MISS_COUNT = 20
#: Sources with no seed: no table holds a past chat or an outside app yet, so such a
#: case misses by construction until HRD-21 (``chat``) or FIND-10 (``outside``).
UNSEEDED_SOURCES = ("chat", "outside")

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
    #: Misses set only: the kind of the miss, the rung that finds it, and the slice.
    kind: str = ""
    rung: int = 0
    slice: str = ""


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


# ── the misses set (WS-FIND FIND-1) ─────────────────────────────────────────


def load_misses(path: Path = MISSES) -> Fixture:
    """Read the misses set, and refuse a set that is not whole."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = tuple(Record(r["key"], r["source"], r["fields"]) for r in raw["records"])
    fixture = Fixture(records, tuple(raw["questions"]))
    problems = check_misses(fixture)
    if problems:
        raise ValueError("the misses set is not whole: " + "; ".join(problems))
    return fixture


def check_misses(fixture: Fixture) -> list[str]:
    """The faults of a misses set: the count, the kind, the rung and the gold."""
    problems: list[str] = []
    keys = [r.key for r in fixture.records]
    if len(set(keys)) != len(keys):
        problems.append("a record key repeats")
    by_key = {r.key: r for r in fixture.records}
    ids = [q["id"] for q in fixture.questions]
    if len(set(ids)) != len(ids):
        problems.append("a question id repeats")
    if len(fixture.questions) != MISS_COUNT:
        problems.append(f"{len(fixture.questions)} misses, not {MISS_COUNT}")
    for q in fixture.questions:
        kind, rung = q.get("kind"), q.get("rung")
        if kind not in MISS_KINDS:
            problems.append(f"{q['id']} has the unknown kind {kind}")
        elif not isinstance(rung, int) or not MISS_KINDS[kind] <= rung <= 7:
            problems.append(f"{q['id']} names the rung {rung}, under the floor of {kind}")
        if not q.get("slice"):
            problems.append(f"{q['id']} names no slice")
        if not q["gold"]:
            problems.append(f"{q['id']} has no gold")
        for key in q["gold"]:
            rec = by_key.get(key)
            if rec is None:
                problems.append(f"{q['id']} names the unknown record {key}")
            elif rec.source != q["source"]:
                problems.append(f"{q['id']} names a record of another source")
        if (q["source"] in UNSEEDED_SOURCES) != (kind in ("past_chat", "outside")):
            problems.append(f"{q['id']}: only past_chat and outside use an unseeded source")
    return problems


def resolve_misses(fixture: Fixture, org: str) -> list[Question]:
    """The misses, with the gold ids of *org* and the kind, rung and slice of each."""
    return [
        Question(q["id"], q["source"], q["text"], tuple(gold_id(org, k) for k in q["gold"]),
                 kind=q["kind"], rung=q["rung"], slice=q["slice"])
        for q in fixture.questions
    ]
