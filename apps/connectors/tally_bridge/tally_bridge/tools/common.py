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
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from tally_bridge.reader import (
    TallyAnswerError,
    TallyDown,
    TallyRefused,
    TallyTimeout,
)
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


class Refusal(Exception):
    """A plain sentence for the member. The call answers it as a tool error."""


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
        return answer(await asyncio.wait_for(work(), settings.call_deadline_s))
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
