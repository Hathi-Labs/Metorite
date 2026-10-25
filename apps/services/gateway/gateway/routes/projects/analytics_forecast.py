"""Projects · the forecast by count — WS-27bn RA-6.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B ("Forecast"), the §5
rows "Forecast by count", "Not converging" and "Not enough history", and §8
RA-6.

The Forecast panel used to give one date from the mean net rate, and a
second from the estimates. Estimates are rare: the owner's screenshot of
2026-10-25 had 26 open tasks and none sized, so "Nothing sized" answered
nothing. A forecast by COUNT needs no estimate. It reads the weeks the team
had, and it runs those weeks again many times.

⚠️ **Pure, with no database.** :func:`analytics.project_forecast` calls it
with the weekly series of :func:`analytics.velocity_sql`, the spine that
Throughput reads. No second count of "finished" exists.

⚠️ **One run samples a WHOLE observed week, its finishes and its arrivals
together.** The owner's decision of 2026-09-17 binds: scope growth is part of
the answer. A run that sampled finishes only would print the date that the
mean rate refused, because it ignores the work that keeps arriving. So a
week takes its finishes off the open count and puts its arrivals back.

⚠️ **The same seed gives the same dates.** The seed comes from the scope and
the last week of the history (:func:`forecast_seed`), and the generator is a
local ``random.Random``, never the global state. A reload gives the same
answer, and a new week gives a new one.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

#: How many runs the forecast makes.
#:
#: With 2,000 runs the 85% figure has a standard error of under one point
#: (``sqrt(0.85 * 0.15 / 2000)`` is 0.008), so the date moves by a week at
#: most between two seeds. The cost is 2,000 times 52 draws at most, 12 ms
#: in CPython (measured 2026-10-25), on each render of the section.
FORECAST_TRIALS = 2000

#: The most weeks one run simulates.
#:
#: A year. The history is six weeks, and a team, its scope and its work
#: change in a year. A date past that is not a forecast. The cap also bounds
#: the work: a run stops here even when the open count is 7,000.
FORECAST_CAP_WEEKS = 52

#: The fewest weeks of the history with a finished task, for a forecast.
#:
#: Under this the answer is "Not enough history" (verdict ``no_history``).
#: Three good weeks out of six are three numbers, and a run that samples
#: them says more than it knows.
MIN_WEEKS_WITH_FINISHES = 4

#: The two shares the forecast names: half the runs, and 85% of them.
P50 = 0.5
P85 = 0.85

#: The reasons of ``not_converging``.
#: ``arrivals``: work arrives at least as fast as it finishes (the rule of
#: 2026-09-17, kept). ``cap``: fewer than 85% of the runs empty the open
#: count within :data:`FORECAST_CAP_WEEKS`.
REASON_ARRIVALS = "arrivals"
REASON_CAP = "cap"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def forecast_seed(scope: str, last_week: str) -> int:
    """The seed of one forecast: the scope and the last week of the history.

    ``scope`` names the node and the subtree arm, or the portfolio.
    ``last_week`` is the ISO Monday of the last whole week the series holds.
    SHA-256, and not ``hash()``, because ``PYTHONHASHSEED`` changes
    ``hash()`` in each process, and two workers then give two dates.
    """
    key = f"ra6:{scope}:{last_week}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def simulate_weeks(
    *,
    remaining: int,
    finished: Sequence[int],
    created: Sequence[int],
    seed: int,
    trials: int = FORECAST_TRIALS,
    cap: int = FORECAST_CAP_WEEKS,
) -> list[int]:
    """The weeks that each run takes to empty the open count.

    Each simulated week samples one observed week, with replacement. That
    week's finishes come off the open count, and its arrivals go back on.
    A run that does not empty the count within ``cap`` weeks gives
    ``cap + 1``. Each run draws ``cap`` weeks, whether it needs them or not,
    so the generator moves the same way for each input of one seed.
    """
    weeks = max(len(finished), len(created))
    if remaining <= 0 or weeks == 0:
        return []
    nets = [
        (finished[i] if i < len(finished) else 0)
        - (created[i] if i < len(created) else 0)
        for i in range(weeks)
    ]
    rng = random.Random(seed)
    out: list[int] = []
    for _ in range(trials):
        left = remaining
        took = cap + 1
        for week, net in enumerate(rng.choices(nets, k=cap), start=1):
            left -= net
            if left <= 0:
                took = week
                break
        out.append(took)
    return out


def percentile_weeks(runs: Sequence[int], share: float) -> int:
    """The weeks by which ``share`` of the runs are done (nearest rank).

    The ``ceil(share * n)``-th smallest. 2,000 runs at 85% read the 1,700th.
    """
    if not runs:
        raise ValueError("no runs to read a percentile from")
    ordered = sorted(runs)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def converge_per_week(
    *, remaining: int, created_per_week: float, cap: int = FORECAST_CAP_WEEKS,
) -> float:
    """The finishes a week that would converge the forecast.

    The arrivals of a week, plus the open count over the cap. At that steady
    rate the net clears the open count in ``cap`` weeks. Rounded UP to one
    decimal, so the rate the panel states does converge.
    """
    need = max(0.0, float(created_per_week)) + max(0, remaining) / cap
    return math.ceil(round(need * 10, 6)) / 10


def day_words(when: date) -> str:
    """``date(2026, 11, 30)`` → "30 Nov 2026", as the panel's ``shortDate``."""
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def _rate(value: float) -> str:
    """A rate as the panel prints it: 4.17 → "4.2", 5.0 → "5"."""
    one = round(float(value), 1)
    return str(int(one)) if one.is_integer() else str(one)


def _tasks(n: int) -> str:
    return f"{n} open task" if n == 1 else f"{n} open tasks"


def forecast_summary(block: dict[str, Any]) -> str:
    """The one plain sentence of the forecast (§6.8 E5).

    The panel, the email and the chat print this text, so they cannot say
    two things. It holds the server's figures and no member text.
    """
    verdict = block.get("verdict")
    remaining = int(block.get("remaining_tasks") or 0)
    if verdict == "nothing_left":
        return "Nothing is open in this scope."
    if verdict == "no_history":
        had = int(block.get("weeks_with_finishes") or 0)
        sampled = int(block.get("weeks_sampled") or 0)
        need = int(block.get("min_weeks_with_finishes") or MIN_WEEKS_WITH_FINISHES)
        return (
            f"Not enough history to forecast: {had} of the last {sampled} weeks"
            f" had a finished task, and a forecast needs {need}."
        )
    converge = block.get("converge_per_week")
    tail = (
        f" To converge, it needs {_rate(converge)} finished tasks a week."
        if isinstance(converge, int | float) else ""
    )
    if verdict == "not_converging" and block.get("reason") == REASON_CAP:
        return (
            f"Not converging within {block.get('cap_weeks')} weeks: fewer than"
            f" 85% of {int(block.get('trials') or 0):,} runs finish the"
            f" {_tasks(remaining)} in that time."
            + tail
        )
    if verdict == "not_converging":
        return (
            f"Not converging: work arrives at least as fast as it finishes"
            f" ({_rate(block.get('created_per_week') or 0)} a week against"
            f" {_rate(block.get('finished_per_week') or 0)})."
            + tail
        )
    p50, p85 = block.get("p50_date"), block.get("p85_date")
    if verdict == "converging" and p50 and p85:
        return (
            f"Half of {int(block.get('trials') or 0):,} runs finish the"
            f" {_tasks(remaining)} by {day_words(date.fromisoformat(p50))}, and"
            f" 85% finish by {day_words(date.fromisoformat(p85))}."
        )
    return "No forecast."


def count_forecast(
    *,
    remaining: int,
    finished: Sequence[int],
    created: Sequence[int],
    seed: int,
    today: date,
    trials: int = FORECAST_TRIALS,
    cap: int = FORECAST_CAP_WEEKS,
) -> dict[str, Any]:
    """The forecast by count: a verdict, two dates, and the converge line.

    The order of the verdicts is the rule (§5):

    1. ``nothing_left``: no open task.
    2. ``no_history`` ("Not enough history"): fewer than
       :data:`MIN_WEEKS_WITH_FINISHES` weeks of the history had a finish.
    3. ``not_converging``, reason ``arrivals``: the mean net rate is zero or
       less. This is the rule of 2026-09-17, and no run starts.
    4. ``not_converging``, reason ``cap``: fewer than 85% of the runs empty
       the open count within the cap.
    5. ``converging``: the 50% and the 85% dates.

    A ``not_converging`` answer carries ``converge_per_week``. The dates are
    ``today`` plus whole weeks.
    """
    weeks = max(len(finished), len(created))
    fin_total = sum(finished)
    made_total = sum(created)
    per_week_fin = round(fin_total / weeks, 2) if weeks else 0.0
    per_week_made = round(made_total / weeks, 2) if weeks else 0.0
    net = round(per_week_fin - per_week_made, 2)
    with_finishes = sum(1 for n in finished if n > 0)

    out: dict[str, Any] = {
        "weeks_sampled": weeks,
        "finished_per_week": per_week_fin,
        "created_per_week": per_week_made,
        "net_per_week": net,
        "remaining_tasks": remaining,
        "weeks_with_finishes": with_finishes,
        "min_weeks_with_finishes": MIN_WEEKS_WITH_FINISHES,
        "trials": trials,
        "cap_weeks": cap,
        "reason": None,
        "converge_per_week": None,
        "p50_weeks": None,
        "p50_date": None,
        "p85_weeks": None,
        "p85_date": None,
        # The 50% figure, under the names every reader of the forecast
        # already reads: the slip, the email and the chat.
        "weeks_remaining": None,
        "finish_date": None,
    }

    def done(verdict: str, **more: Any) -> dict[str, Any]:
        block = {**out, **more, "verdict": verdict}
        return {**block, "summary": forecast_summary(block)}

    if remaining <= 0:
        return done("nothing_left")
    if with_finishes < MIN_WEEKS_WITH_FINISHES:
        return done("no_history")
    converge = converge_per_week(
        remaining=remaining, created_per_week=per_week_made, cap=cap,
    )
    if net <= 0:
        return done(
            "not_converging", reason=REASON_ARRIVALS, converge_per_week=converge,
        )
    runs = simulate_weeks(
        remaining=remaining, finished=finished, created=created,
        seed=seed, trials=trials, cap=cap,
    )
    p50 = percentile_weeks(runs, P50)
    p85 = percentile_weeks(runs, P85)
    if p85 > cap:
        return done("not_converging", reason=REASON_CAP, converge_per_week=converge)
    p50_date = (today + timedelta(weeks=p50)).isoformat()
    return done(
        "converging",
        p50_weeks=p50,
        p50_date=p50_date,
        p85_weeks=p85,
        p85_date=(today + timedelta(weeks=p85)).isoformat(),
        weeks_remaining=p50,
        finish_date=p50_date,
    )
