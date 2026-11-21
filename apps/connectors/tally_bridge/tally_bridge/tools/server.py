"""The read tools (brief section 6.4): ``status`` and ``find_ledgers`` here,
and the seven report tools in ``tools/reports.py``.

Rules of section 6.4 that bind each tool:

1. It sends the four hints of ``READ_ONLY``.
2. It answers compact JSON. An amount is a string with two decimals and a
   ``side`` of ``Dr`` or ``Cr`` (null for zero).
3. It answers ``company``, ``row_count`` and ``truncated``.
5. An error is an MCP tool error in one plain sentence (``common.guarded``).
6. ``tools/list`` never calls Tally. FastMCP answers it from the fixed list.
7. Each call ends inside ``call_deadline_s``.

Each tool takes the optional ``company`` input (the owner's answer of
2026-11-21 on task 3656). Section 6.5 rule 4: only an allowed company passes.

A tool returns a ``CallToolResult`` itself, so it has no output schema to
grow the ``tools/list`` answer.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import Field, ValidationError

from tally_bridge import __version__, calllog
from tally_bridge.calllog import CallLimiter
from tally_bridge.reader import (
    TallyAnswerError,
    TallyDown,
    TallyReader,
    TallyRefused,
    envelope,
    records,
)
from tally_bridge.reader.values import Amount
from tally_bridge.settings import Settings
from tally_bridge.tools.common import (
    CALL_CODE,
    READ_ONLY,
    TEXT_LIMIT,
    Refusal,
    guarded,
    pick_company,
    refuse,
)
from tally_bridge.tools.reports import register_report_tools

FIND_LIMIT = 50

log = logging.getLogger("tally_bridge.tools")

Company = Annotated[str | None, Field(description="Company name. Optional with one company.")]


def _amount(a: Amount) -> dict[str, Any]:
    return {"amount": a.text, "side": a.side}


def build_read_server(reader: TallyReader, settings: Settings) -> FastMCP:
    """The MCP server with the read tools. The transport sets the HTTP options."""
    mcp = _BridgeMCP(CallLimiter(settings.calls_per_minute), name="tally")

    @mcp.tool(
        name="status",
        description="Says if TallyPrime is up, which allowed companies are open, and the version.",
        annotations=READ_ONLY,
    )
    async def status() -> CallToolResult:
        async def work() -> dict[str, Any]:
            if not await reader.is_up():
                raise TallyDown
            try:
                root = await reader.post(envelope.collection("BridgeCompanies", company=None))
                open_now: set[str] | None = set(records.company_names(root))
            except (TallyRefused, TallyAnswerError):
                open_now = None
            companies = [
                {"name": n, "open": None if open_now is None else n in open_now}
                for n in settings.allowed_companies
            ]
            # Only allowed companies are named. A company that the setup screen
            # did not allow stays unnamed, even when it is open.
            return {"tally": "up", "bridge_version": __version__, "companies": companies}

        return await guarded(settings, "status", work)

    @mcp.tool(
        name="find_ledgers",
        description="Finds ledgers by name, with their group and closing balance.",
        annotations=READ_ONLY,
    )
    async def find_ledgers(
        query: Annotated[str, Field(description="Part of the ledger name.")],
        group: Annotated[str | None, Field(description="Exact group name.")] = None,
        limit: Annotated[int, Field(description="Most rows, 50 or fewer.")] = 20,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            # The bounds are checked here, not in the schema, so that a bad
            # value gets one plain sentence (brief section 6.4 rule 5).
            if not 1 <= limit <= FIND_LIMIT:
                raise Refusal(f"The limit is {limit}. Ask for 1 to {FIND_LIMIT} rows.")
            if len(query) > TEXT_LIMIT or len(group or "") > TEXT_LIMIT:
                raise Refusal(f"Keep the query and the group to {TEXT_LIMIT} characters.")
            name = pick_company(settings, company)
            # The query never goes to Tally. The bridge filters here, so no
            # text of the member reaches an envelope.
            root = await reader.post(envelope.collection("BridgeLedgers", company=name))
            needle = query.casefold()
            hits = [
                led
                for led in records.ledgers(root)
                if needle in led.name.casefold()
                and (group is None or led.group.casefold() == group.casefold())
            ]
            rows = [
                {"name": led.name, "group": led.group, "closing": _amount(led.closing)}
                for led in hits[:limit]
            ]
            return {
                "company": name,
                "row_count": len(rows),
                "truncated": len(hits) > limit,
                "ledgers": rows,
            }

        return await guarded(settings, "find_ledgers", work, company=company)

    register_report_tools(mcp, reader, settings)
    _compact_schemas(mcp)
    return mcp


class _BridgeMCP(FastMCP):
    """FastMCP, with three changes that hold for every tool call.

    1. The call limit (brief section 6.5 rule 5): past the limit, the call
       answers one plain sentence and never reaches the tool or Tally.
    2. The call log (rule 6): one line for each call, with no token.
    3. A missing or wrong input answers one plain sentence that names the
       inputs (section 6.4 rule 5), not the raw validation text of pydantic.
    """

    def __init__(self, limiter: CallLimiter, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._limiter = limiter

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        start = time.monotonic()
        code, rows = "error", None
        CALL_CODE.set("error")
        try:
            wait = self._limiter.take()
            if wait:
                code = "rate_limited"
                limit = self._limiter.limit
                return refuse(
                    f"The bridge takes {limit} calls a minute. Try again in {wait} seconds."
                )
            result = await self._checked_call(name, arguments)
            code = CALL_CODE.get()
            rows = _row_count(result)
            return result
        except _InputError:
            code = "bad_input"
            raise
        except ToolError as exc:
            if str(exc).startswith("Unknown tool"):
                code = "unknown_tool"
                raise
            # A crash inside a tool. The member gets one plain sentence, and the
            # log keeps the detail.
            log.exception("tool %s failed", name)
            raise ToolError("The bridge failed on this call. Try again.") from None
        finally:
            calllog.record(name, arguments, code, (time.monotonic() - start) * 1000, rows)

    async def _checked_call(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            cause = exc.__cause__
            if not isinstance(cause, ValidationError):
                raise
            fields = sorted({str(e["loc"][0]) for e in cause.errors() if e.get("loc")})
            if not fields:
                raise _InputError("Check the inputs of this call.") from None
            raise _InputError(f"Check these inputs: {', '.join(fields)}.") from None


class _InputError(ToolError):
    """A missing or wrong input, as one plain sentence."""


def _row_count(result: Any) -> int | None:
    if not isinstance(result, CallToolResult) or result.isError or not result.content:
        return None
    try:
        value = json.loads(result.content[0].text).get("row_count")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, int) else None


def _compact_schemas(mcp: FastMCP) -> None:
    """Keep the tools/list answer small (brief section 6.4 rule 8).

    Pydantic adds a ``title`` to each input and wraps an optional input in
    ``anyOf`` with null. Neither tells the agent anything. An optional input
    keeps its default of null and stays out of ``required``.
    """
    for tool in mcp._tool_manager.list_tools():
        schema = tool.parameters
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
            kinds = prop.pop("anyOf", None)
            if kinds:
                prop["type"] = next(k["type"] for k in kinds if k.get("type") != "null")
