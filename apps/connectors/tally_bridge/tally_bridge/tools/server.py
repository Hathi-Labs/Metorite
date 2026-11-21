"""The read tools (brief section 6.4): ``status`` and ``find_ledgers`` so far.

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

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from tally_bridge import __version__
from tally_bridge.reader import TallyAnswerError, TallyReader, TallyRefused, envelope, records
from tally_bridge.reader.values import Amount
from tally_bridge.settings import Settings
from tally_bridge.tools.common import (
    READ_ONLY,
    TALLY_DOWN,
    Refusal,
    guarded,
    pick_company,
)

FIND_LIMIT = 50

Company = Annotated[str | None, Field(description="Company name. Optional with one company.")]


def _amount(a: Amount) -> dict[str, Any]:
    return {"amount": a.text, "side": a.side}


def build_read_server(reader: TallyReader, settings: Settings) -> FastMCP:
    """The MCP server with the read tools. The transport sets the HTTP options."""
    mcp = FastMCP(name="tally")

    @mcp.tool(
        name="status",
        description="Says if TallyPrime is up, which allowed companies are open, and the version.",
        annotations=READ_ONLY,
    )
    async def status() -> CallToolResult:
        async def work() -> dict[str, Any]:
            if not await reader.is_up():
                raise Refusal(TALLY_DOWN)
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
        query: Annotated[str, Field(max_length=100, description="Part of the ledger name.")],
        group: Annotated[str | None, Field(max_length=100, description="Exact group name.")] = None,
        limit: Annotated[int, Field(ge=1, le=FIND_LIMIT, description="Most rows.")] = 20,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
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

    return mcp
