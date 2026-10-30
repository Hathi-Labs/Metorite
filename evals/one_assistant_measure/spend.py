"""What a live run spends, and the rules that cap it (2026-10-30 drain).

Live chats go through the PRODUCTION Router, on the one DeepSeek account. On
2026-10-30 the account ran out of funds while the harness total read USD 4.62
and the Router billed USD 12.70 for the eval member. This module holds the
three causes the rows show, each as a pure function a test can fix:

* a DeepSeek peak band charges double, and ``usage.cost`` does not know it
  (:data:`PEAK_FACTOR`, :func:`is_peak`);
* a call with no usage frame read as USD 0 (:func:`unseen_usage_usd`);
* a send that the tap did not see, for example System 1 (:func:`unseen_sends_usd`).
"""

from __future__ import annotations

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


def unseen_usage_usd(request_chars: int, card: dict[str, Any]) -> float:
    """The worst-case USD of a call that returned no usage."""
    rate = card["usd_per_1m"]
    prompt = max(0, request_chars) // CHARS_PER_TOKEN
    return (prompt * rate["input_miss"] + ASSUMED_COMPLETION_TOKENS * rate["output"]) / 1_000_000


def unseen_sends_usd(count: int, card: dict[str, Any]) -> float:
    """The worst-case USD of *count* sends that reached the Router unseen."""
    rate = card["usd_per_1m"]
    one = (UNSEEN_PROMPT_TOKENS * rate["input_miss"]
           + ASSUMED_COMPLETION_TOKENS * rate["output"]) / 1_000_000
    return max(0, count) * one


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
