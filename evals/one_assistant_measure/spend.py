"""What a live run spends, and the rules that cap it (2026-10-30 drain).

Live chats go through the PRODUCTION Router, on the one DeepSeek account. On
2026-10-30 the account ran out of funds while the harness total read USD 4.62
and the Router billed USD 12.70 for the eval member. This module holds the
three causes the rows show, each as a pure function a test can fix:

* a DeepSeek peak band charges double, and ``usage.cost`` does not know it
  (:data:`PEAK_FACTOR`, :func:`is_peak`);
* a call with no usage frame read as USD 0 (:func:`unseen_usage_usd`);
* a send that the tap did not see, for example System 1 (:func:`unseen_sends_usd`).

The pilot of 2026-11-15 (TS-7a-cap) found a fourth cause. The card priced
every buffered call as DeepSeek, but the Router served Claude. The count read
USD 0.328, and the bill read USD 0.4889. So a call is now priced by the card of
the model that SERVED it (:func:`card_of`). An unknown model takes the most
expensive rate of the card (:func:`worst_card`), never the cheapest.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

#: The hours (UTC) of the DeepSeek peak bands: 01:00-04:00 and 06:00-10:00,
#: Monday to Friday. A call that starts in a band costs double.
#: Source: https://api-docs.deepseek.com/quick_start/pricing/
#: Each pair is ``(first hour, hour after the last)``.
PEAK_BANDS_UTC: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))
#: Peak days: Monday is 0, Friday is 4 (``datetime.weekday``).
PEAK_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)
#: The multiplier of a peak call.
PEAK_FACTOR = 2.0

#: A call with no usage frame: price it from the size of its request. Three
#: characters a token overstates the tokens, and the call reads as a cache miss.
CHARS_PER_TOKEN = 3
#: ... and as a reply of this many tokens. A conservative figure, not a measure.
ASSUMED_COMPLETION_TOKENS = 4096
#: A send that the tap did not see has no request size. Price it at this worst
#: case: 20k input tokens, no cache, and a reply of 4k tokens.
UNSEEN_PROMPT_TOKENS = 20_000


#: A dated alias of a model: ``claude-sonnet-5-5-20261101``. Only an exact
#: suffix of 8 digits drops before the lookup. No other part of a name drops.
DATE_SUFFIX = re.compile(r"-\d{8}$")


def model_key(served: str) -> str:
    """The key of *served* in the card: lower case, with no provider prefix.
    ``anthropic/claude-haiku-5-5`` and ``claude-haiku-5-5`` are one model."""
    return served.strip().lower().rsplit("/", 1)[-1]


def worst_card(card: dict[str, Any]) -> dict[str, Any]:
    """The price of a model that the card does not know: the highest input
    rate and the highest output rate of the card. A cache hit counts as a miss,
    and the DeepSeek peak factor applies. It can only count high."""
    rates = [m["usd_per_1m"] for m in card["models"].values()]
    top_in = max(r["input_miss"] for r in rates)
    top_out = max(r["output"] for r in rates)
    return {"model": "unknown", "known": False, "peak": True,
            "usd_per_1m": {"input_miss": top_in, "input_hit": top_in, "output": top_out}}


def card_of(served: str, card: dict[str, Any]) -> dict[str, Any]:
    """The card that prices a call: the card of the model that SERVED it.

    The tier that the call names does not set the price, because the Router
    binds a tier to a model and can change it (``tier-balanced`` served
    DeepSeek on 2026-10-25 and Claude on 2026-11-15). The lookup is exact,
    after the provider prefix and a date suffix (:data:`DATE_SUFFIX`) drop.
    It never matches a part of a name. No served name, or a name that the
    card does not hold, gets :func:`worst_card`."""
    key = DATE_SUFFIX.sub("", model_key(served)) if served else ""
    entry = card["models"].get(key) if key else None
    if entry is None:
        return worst_card(card)
    return {"model": key, "known": True, "peak": bool(entry.get("peak")),
            "usd_per_1m": entry["usd_per_1m"]}


def is_peak(moment: datetime) -> bool:
    """True when *moment* is inside a DeepSeek peak band."""
    when = moment.astimezone(UTC)
    return when.weekday() in PEAK_WEEKDAYS and any(a <= when.hour < b for a, b in PEAK_BANDS_UTC)


def next_off_peak(moment: datetime) -> datetime:
    """The first moment at or after *moment* that is off peak. A moment that is
    off peak already is returned as it is."""
    when = moment.astimezone(UTC)
    if not is_peak(when):
        return when
    step = when.replace(minute=0, second=0, microsecond=0)
    while is_peak(step):
        step += timedelta(hours=1)
    return step


def peak_gate(now: datetime, allow_peak: bool) -> tuple[bool, str]:
    """``(ok, message)`` for the start of a live run. A peak band refuses, unless
    *allow_peak*. The message names the next off-peak start."""
    if not is_peak(now) or allow_peak:
        if is_peak(now):
            return True, "WARNING: --allow-peak. This run starts in a DeepSeek peak band and costs double."
        return True, ""
    start = next_off_peak(now).strftime("%Y-%m-%d %H:%M UTC")
    return False, (f"NO-GO: {now.astimezone(UTC):%Y-%m-%d %H:%M} UTC is in a DeepSeek peak band, "
                   f"which costs double. The next off-peak start is {start}. "
                   "Pass --allow-peak to run now.")


#: The owner approved USD 10 of live eval spend in total (task 3649). A run
#: whose budget plus the spend of earlier chunks is above it does not start.
OWNER_SPEND_CEILING_USD = 10.0


def ceiling_gate(budget: float, spent_before: float) -> tuple[bool, str]:
    """``(ok, message)``: refuse a live run when ``budget + spent_before`` is
    above :data:`OWNER_SPEND_CEILING_USD`, or when a figure is negative or not a number."""
    try:
        b, s = float(budget), float(spent_before)
    except (TypeError, ValueError):
        return False, "NO-GO: --budget-usd and --spent-before must be numbers."
    if not (math.isfinite(b) and math.isfinite(s)) or b < 0 or s < 0:
        return False, "NO-GO: --budget-usd and --spent-before must be finite and not negative."
    if b + s > OWNER_SPEND_CEILING_USD:
        return False, (f"NO-GO: --budget-usd {b:.2f} plus --spent-before {s:.2f} is above the "
                       f"owner ceiling of USD {OWNER_SPEND_CEILING_USD:.2f} of live eval spend.")
    return True, ""


def unseen_usage_usd(request_chars: int, card: dict[str, Any]) -> float:
    """The worst-case USD of a call that returned no usage."""
    rate = card["usd_per_1m"]
    prompt = max(0, request_chars) // CHARS_PER_TOKEN
    return (prompt * rate["input_miss"] + ASSUMED_COMPLETION_TOKENS * rate["output"]) / 1_000_000


def unseen_sends_usd(count: int, card: dict[str, Any]) -> float:
    """The worst-case USD of *count* sends that reached the Router unseen. No
    response names the model of such a send, so the most expensive rate of
    *card* prices it (:func:`worst_card`)."""
    rate = worst_card(card)["usd_per_1m"]
    one = (UNSEEN_PROMPT_TOKENS * rate["input_miss"]
           + ASSUMED_COMPLETION_TOKENS * rate["output"]) / 1_000_000
    return max(0, count) * one


def bill_check_line(member: str, started_utc: str, counted: float) -> str:
    """The line that a live run prints at its end. The harness does not read
    ``usage_event``: a person compares the bill by hand. When the card priced
    every call, the counted total is at or above the bill, and at most 2 times it."""
    return (f"check usage_event for {member} since {started_utc}: expected about USD "
            f"{counted:.4f}. The real bill is at most that, and at least half of it "
            "(the count is at most 2 times the bill), if every call was priced.")


def spend_by_agent(rows: list[dict[str, Any]]) -> dict[str, float]:
    """The counted USD of each agent over *rows*. The sends that no tap saw
    sit under ``unseen``."""
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        for c in r["model_calls"]:
            out[c["agent"]] += c["cost_usd"]
        if r.get("unseen_usd"):
            out["unseen"] += r["unseen_usd"]
    return dict(out)


def spend_table(rows: list[dict[str, Any]]) -> str:
    """The counted spend of each agent, as a Markdown table, largest first."""
    by = spend_by_agent(rows)
    total = sum(by.values())
    lines = ["| Agent | Counted USD | Share |", "|---|---|---|"]
    for agent, usd in sorted(by.items(), key=lambda kv: -kv[1]):
        share = f"{100 * usd / total:.0f}%" if total else "0%"
        lines.append(f"| {agent} | {usd:.4f} | {share} |")
    lines.append(f"| total | {total:.4f} | 100% |")
    return "\n".join(lines)
