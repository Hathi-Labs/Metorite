"""The score of the retrieval eval: pure functions, no I/O (WS-KG KG-1).

* **recall@5.** A question is a hit when one of its gold ids is among the
  first 5 hits of its own source in the answer. That is the cut that
  ``GET /search/company`` makes with ``k=5``, so the number says whether the
  right record reached the member. The mean of the questions is the recall.
  ``merged@5`` is the harder cut: the gold id is among the first 5 hits of the
  whole answer, after the fusion of the sources.
* **p50 and p95.** The latency of one request, by NEAREST RANK:
  ``sorted[ceil(p / 100 * n) - 1]``. No interpolation, so a value is always
  a time that a request took.
* **Empty sources.** A source of the registry with no hit for any question,
  plus each name in ``skipped``. A source that has questions and no hit for
  them is a FAILING source (:func:`verdict`).

The report holds counts, ratios, seconds, source names and question ids. It
never holds the words of a question, a title or a snippet.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

#: The cut of recall.
K = 5
#: The lowest recall at which the fixture run passes. The synthetic baseline is
#: 1.00, because every query holds words of its gold record. The floor leaves
#: room for a drift of the fusion, and no more. The README says why.
FLOOR = 0.90

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2


@dataclass(frozen=True)
class Outcome:
    """What one question did: where its gold landed, and how long it took."""

    question_id: str
    source: str
    #: 1-based place among the hits of its own source, or ``None`` when absent.
    rank: int | None
    #: 1-based place in the whole answer, or ``None`` when absent.
    place: int | None
    seconds: float
    #: Hits of each source in the answer.
    hits: dict[str, int] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    #: A short code when the request failed (``http-500``), or empty.
    error: str = ""


def locate(
    hits: Sequence[dict], source: str, gold: Iterable[str],
) -> tuple[int | None, int | None]:
    """``(rank, place)`` of the first gold hit of *source*, as 1-based numbers."""
    wanted = set(gold)
    rank = 0
    for place, hit in enumerate(hits, start=1):
        if hit.get("app") != source:
            continue
        rank += 1
        if str(hit.get("id")) in wanted:
            return rank, place
    return None, None


def hit_at(position: int | None, k: int = K) -> bool:
    """True when *position* is within the first *k* places."""
    return position is not None and position <= k


def recall_at_k(outcomes: Sequence[Outcome], k: int = K) -> float:
    """The mean over the questions of 'the gold id is within the first k of its source'."""
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if hit_at(o.rank, k)) / len(outcomes)


def merged_at_k(outcomes: Sequence[Outcome], k: int = K) -> float:
    """The mean over the questions of 'the gold id is within the first k of the answer'."""
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if hit_at(o.place, k)) / len(outcomes)


def percentile(values: Sequence[float], pct: float) -> float:
    """The nearest-rank percentile: ``sorted[ceil(pct / 100 * n) - 1]``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(1, math.ceil(pct / 100.0 * len(ordered))) - 1
    return ordered[min(index, len(ordered) - 1)]


@dataclass(frozen=True)
class Summary:
    n: int
    recall: float
    merged: float
    #: ``source -> (questions that hit, questions)``.
    by_source: dict[str, tuple[int, int]]
    p50: float
    p95: float
    #: Sources of the registry with no hit for any question.
    empty: tuple[str, ...]
    #: Sources with questions and no hit for their own questions.
    failing: tuple[str, ...]
    skipped: tuple[str, ...]
    #: Ids (never words) of the questions that missed.
    missed: tuple[str, ...]
    #: Requests that failed. A failed request is a miss, and fails the run.
    errors: int = 0


def summarize(outcomes: Sequence[Outcome], registry: Sequence[str], k: int = K) -> Summary:
    """The score of one run. *registry* is every source the route can ask."""
    total: Counter[str] = Counter()
    own: Counter[str] = Counter()
    by_source: dict[str, list[int]] = {}
    skipped: set[str] = set()
    for o in outcomes:
        for source, n in o.hits.items():
            total[source] += n
        own[o.source] += o.hits.get(o.source, 0)
        row = by_source.setdefault(o.source, [0, 0])
        row[0] += 1 if hit_at(o.rank, k) else 0
        row[1] += 1
        skipped.update(o.skipped)
    asked = list(by_source)
    return Summary(
        n=len(outcomes),
        recall=recall_at_k(outcomes, k),
        merged=merged_at_k(outcomes, k),
        by_source={s: (a, b) for s, (a, b) in by_source.items()},
        p50=percentile([o.seconds for o in outcomes], 50),
        p95=percentile([o.seconds for o in outcomes], 95),
        empty=tuple(s for s in registry if total[s] == 0),
        failing=tuple(s for s in asked if own[s] == 0),
        skipped=tuple(sorted(skipped)),
        missed=tuple(o.question_id for o in outcomes if not hit_at(o.rank, k)),
        errors=sum(1 for o in outcomes if o.error),
    )


def verdict(summary: Summary, floor: float = FLOOR) -> int:
    """0 pass, 1 fail. A failed request, a source with no hit, or a recall under the floor fails."""
    if summary.n == 0 or summary.failing or summary.errors or summary.recall < floor:
        return EXIT_FAIL
    return EXIT_PASS


def render(summary: Summary, *, mode: str, flags: dict[str, str], floor: float | None = None) -> str:
    """The report as text. Counts and names only: no question text."""
    hit = round(summary.recall * summary.n)
    lines = [
        f"search_company retrieval eval ({mode})",
        f"questions: {summary.n}",
        f"recall@{K}: {summary.recall:.3f} ({hit}/{summary.n})",
        f"merged@{K}: {summary.merged:.3f}",
    ]
    for source, (got, asked) in summary.by_source.items():
        lines.append(f"  {source}: {got}/{asked}")
    lines += [
        f"latency p50: {summary.p50:.3f}s",
        f"latency p95: {summary.p95:.3f}s",
        f"empty sources: {', '.join(summary.empty) or 'none'}",
        f"skipped: {', '.join(summary.skipped) or 'none'}",
        f"failed requests: {summary.errors}",
        f"missed question ids: {', '.join(summary.missed) or 'none'}",
        "flags: " + (", ".join(f"{k}={v}" for k, v in flags.items()) or "not told"),
    ]
    if floor is not None:
        status = "PASS" if verdict(summary, floor) == EXIT_PASS else "FAIL"
        failing = f", failing sources: {', '.join(summary.failing)}" if summary.failing else ""
        lines.append(f"floor: recall >= {floor:.2f}{failing} -> {status}")
    return "\n".join(lines)
