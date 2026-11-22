"""The read tools (brief section 6.4). PR 1 has one: ``status``.

Rules of section 6.4 that bind from the first tool:

1. Each read tool sends the four hints of ``READ_ONLY``.
2. An answer is compact JSON.
5. An error is an MCP tool error (``isError``) in one plain sentence.
6. ``tools/list`` never calls Tally. FastMCP answers it from the fixed list.
7. Each call ends inside ``call_deadline_s`` or returns a timeout error.

A tool returns a ``CallToolResult`` itself. So an error reads as the one
sentence, with no "Error executing tool" prefix, and the tool has no output
schema to grow the ``tools/list`` answer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from tally_bridge import __version__
from tally_bridge.reader import TallyReader
from tally_bridge.settings import Settings

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

TALLY_DOWN = "TallyPrime is not running on the office machine."


class CallTimeout(Exception):
    pass


def answer(payload: dict[str, Any]) -> CallToolResult:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def refuse(sentence: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=sentence)], isError=True)


async def within[T](deadline_s: float, work: Awaitable[T]) -> T:
    try:
        return await asyncio.wait_for(work, deadline_s)
    except TimeoutError as exc:
        raise CallTimeout from exc


def timeout_sentence(deadline_s: float) -> str:
    return f"TallyPrime did not answer in {deadline_s:g} seconds. Try again in a minute."


def build_read_server(reader: TallyReader, settings: Settings) -> FastMCP:
    """The MCP server with the read tools. The transport sets the HTTP options."""
    mcp = FastMCP(name="tally")

    @mcp.tool(
        name="status",
        description="Says if TallyPrime is up, and the bridge version.",
        annotations=READ_ONLY,
    )
    async def status() -> CallToolResult:
        try:
            up = await within(settings.call_deadline_s, reader.is_up())
        except CallTimeout:
            return refuse(timeout_sentence(settings.call_deadline_s))
        if not up:
            return refuse(TALLY_DOWN)
        return answer({"tally": "up", "bridge_version": __version__})

    return mcp
