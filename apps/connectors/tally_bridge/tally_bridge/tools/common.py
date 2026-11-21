"""What every read tool shares (brief section 6.4 rules 2, 3, 5 and 7, and
section 6.5 rule 4).

- ``answer`` and ``refuse``: compact JSON, or one plain sentence as an MCP
  tool error.
- ``pick_company``: the allow-list. A company that the setup screen did not
  allow is refused by name, and so is every other.
- ``guarded``: runs the work of one call inside the deadline, and turns each
  reader error into one plain sentence. The log keeps the detail.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from tally_bridge.reader import (
    TallyAnswerError,
    TallyDown,
    TallyRefused,
    TallyTimeout,
)
from tally_bridge.reader.values import Amount
from tally_bridge.settings import Settings

log = logging.getLogger("tally_bridge.tools")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

TALLY_DOWN = "TallyPrime is not running on the office machine."
UNREADABLE = "TallyPrime sent an answer that the bridge cannot read."
NONE_ALLOWED = "No company is allowed yet. Choose the companies in the bridge setup."


# Brief section 6.4 rule 4: one answer goes into the model context.
MAX_ANSWER_BYTES = 20_000
TOO_LARGE = "The answer is too large. Ask for a shorter range or a narrower filter."
TEXT_LIMIT = 100


class Refusal(Exception):
    """A plain sentence for the member. The call answers it as a tool error."""


def amount(a: Amount) -> dict[str, Any]:
    return {"amount": a.text, "side": a.side}


def day(text: str, field: str) -> date:
    """A date input. Raises Refusal unless it reads YYYY-MM-DD."""
    try:
        return date.fromisoformat(text.strip())
    except (ValueError, AttributeError):
        raise Refusal(f"Write {field} as YYYY-MM-DD.") from None


def day_range(start: str, end: str, max_days: int) -> tuple[date, date]:
    """A date range of max_days or fewer, both ends included."""
    first, last = day(start, "from_date"), day(end, "to_date")
    if last < first:
        raise Refusal("The end date is before the start date.")
    days = (last - first).days + 1
    if days > max_days:
        raise Refusal(f"The range is {days} days. Ask for {max_days} days or less.")
    return first, last


def short(*texts: str | None) -> None:
    """Refuse a text input that is longer than TEXT_LIMIT."""
    if any(len(t or "") > TEXT_LIMIT for t in texts):
        raise Refusal(f"Keep each name and filter to {TEXT_LIMIT} characters.")


def fy_start(d: date) -> date:
    """The first day of the Indian financial year (1 April) that holds d."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def answer(payload: dict[str, Any]) -> CallToolResult:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def refuse(sentence: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=sentence)], isError=True)


def timeout_sentence(deadline_s: float) -> str:
    return f"TallyPrime did not answer in {deadline_s:g} seconds. Try again in a minute."


def _allowed_list(settings: Settings) -> str:
    return ", ".join(settings.allowed_companies)


def pick_company(settings: Settings, company: str | None) -> str:
    """The company of this call, from the allow-list. Raises Refusal."""
    allowed = settings.allowed_companies
    if not allowed:
        raise Refusal(NONE_ALLOWED)
    if company is None or not company.strip():
        if len(allowed) == 1:
            return allowed[0]
        raise Refusal(f"Name the company. The allowed companies are: {_allowed_list(settings)}.")
    if company not in allowed:
        raise Refusal(
            f"The company '{company}' is not allowed. "
            f"The allowed companies are: {_allowed_list(settings)}."
        )
    return company


async def guarded(
    settings: Settings,
    tool: str,
    work: Callable[[], Awaitable[dict[str, Any]]],
    company: str | None = None,
) -> CallToolResult:
    """Run one call. Every failure becomes one plain sentence."""
    try:
        result = answer(await asyncio.wait_for(work(), settings.call_deadline_s))
        if len(result.content[0].text.encode("utf-8")) > MAX_ANSWER_BYTES:
            return refuse(TOO_LARGE)
        return result
    except Refusal as exc:
        return refuse(str(exc))
    except TallyDown:
        return refuse(TALLY_DOWN)
    except (TallyTimeout, TimeoutError):
        return refuse(timeout_sentence(settings.call_deadline_s))
    except TallyRefused as exc:
        log.warning("tally refused %s: %s", tool, exc.detail)
        where = f" Check that the company '{company}' is open in TallyPrime." if company else ""
        return refuse("TallyPrime refused the request." + where)
    except TallyAnswerError as exc:
        log.warning("unreadable answer for %s: %s", tool, exc)
        return refuse(UNREADABLE)
