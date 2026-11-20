"""The reason of an MCP connect fault names the real fault (2026-11-20).

Shaurrya's Zoho Books server answered 401 because it wants an OAuth consent.
The log said ``reason=WouldBlock``. anyio raises ``WouldBlock`` inside each
stream receive, so it is the deepest ``__context__`` of almost every fault of
an MCP session, and ``_reason`` took the deepest class. The chain that the
production probe read was ``ExceptionGroup <- HTTPStatusError(401) <-
CancelledError <- WouldBlock``.

``_reason`` now names the HTTP status, a list cap, or the deepest class that is
not noise. It still never reads a message, which can hold a URL or a key.
"""

from __future__ import annotations

import asyncio

import anyio
import httpx
import pytest

pytest.importorskip("orchestrator._maf_mcp")

from orchestrator import _maf_mcp

SECRET_URL = "https://books.example/mcp/SENTINEL-KEY-0000/message"


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", SECRET_URL)
    return httpx.HTTPStatusError(f"Client error for url {SECRET_URL}", request=req,
                                 response=httpx.Response(code, request=req))


def _in_the_sdk(err: BaseException) -> BaseExceptionGroup:
    """*err* as the MCP SDK raises it: inside a task group, with a cancel and
    anyio's ``WouldBlock`` under it."""
    cancel = asyncio.CancelledError()
    cancel.__context__ = anyio.WouldBlock()
    err.__context__ = cancel
    return BaseExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [err])


def test_a_401_inside_the_sdk_group_reads_http_401() -> None:
    assert _maf_mcp._reason(_in_the_sdk(_status_error(401))) == "http_401"


def test_a_503_inside_the_sdk_group_reads_http_503() -> None:
    assert _maf_mcp._reason(_in_the_sdk(_status_error(503))) == "http_503"


@pytest.mark.parametrize("which", ["tools", "pages"])
def test_a_list_cap_reads_too_many(which: str) -> None:
    group = _in_the_sdk(_maf_mcp.McpListTooLong(which))
    assert _maf_mcp._reason(group) == f"too_many_{which}"


def test_a_noise_leaf_gives_the_class_above_it() -> None:
    err = RuntimeError("boom")
    err.__context__ = anyio.WouldBlock()
    assert _maf_mcp._reason(err) == "RuntimeError"


def test_an_outbound_refusal_still_wins() -> None:
    from gateway.outbound_guard import OutboundRefused

    refused = OutboundRefused("books.example", "private address")
    refused.__context__ = _status_error(401)
    assert _maf_mcp._reason(_in_the_sdk(refused)) == "private address"


def test_the_reason_never_holds_the_message() -> None:
    reason = _maf_mcp._reason(_in_the_sdk(_status_error(401)))
    assert "SENTINEL" not in reason and "books.example" not in reason


def test_http_status_reads_the_chain_and_none_without_an_answer() -> None:
    assert _maf_mcp._http_status(_in_the_sdk(_status_error(403))) == 403
    assert _maf_mcp._http_status(_in_the_sdk(RuntimeError("no answer"))) is None
