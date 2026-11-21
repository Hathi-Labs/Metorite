"""TLY-1: brief section 6.5 rule 5 (the call limit) and rule 6 (the call log).

Brief test 15: "Read the bridge log. One line for each call. No token in it."
The unit half runs here. The brief test runs on the office PC.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest
from mcp import types

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402
from tally_bridge import calllog  # noqa: E402
from tally_bridge.calllog import CallLimiter, open_call_log  # noqa: E402
from tally_bridge.reader import TallyDown  # noqa: E402
from tally_bridge.reader.parse import parse_answer  # noqa: E402
from tally_bridge.secret_store import MemorySecretStore, new_token, token_sha256  # noqa: E402
from tally_bridge.settings import Settings, SettingsError, settings_from_mapping  # noqa: E402
from tally_bridge.tools import build_read_server  # noqa: E402
from tally_bridge.transport import build_address_app  # noqa: E402

LEDGERS = ROOT / "fixtures" / "synthetic" / "SYNTHETIC_ledgers.xml"
ONE = Settings(allowed_companies=("Alpha Co",))


class Reader:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.posts = 0

    async def is_up(self) -> bool:
        return True

    async def post(self, envelope: bytes):
        self.posts += 1
        if self.fail:
            raise self.fail
        return parse_answer(LEDGERS.read_bytes())


def call(server, tool: str, args: dict):
    req = types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name=tool, arguments=args)
    )
    return asyncio.run(server._mcp_server.request_handlers[types.CallToolRequest](req)).root


def lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == calllog.CALL_LOG]


@pytest.fixture
def caplog_calls(caplog):
    caplog.set_level(logging.INFO, logger=calllog.CALL_LOG)
    return caplog


# --- Rule 5: the call limit ----------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_limiter_allows_the_limit_then_names_the_wait():
    clock = Clock()
    limiter = CallLimiter(3, clock)
    assert [limiter.take() for _ in range(3)] == [0, 0, 0]
    clock.now += 20
    assert limiter.take() == 40
    clock.now += 40
    assert limiter.take() == 0  # the first call left the window


def test_a_refused_call_does_not_count():
    clock = Clock()
    limiter = CallLimiter(1, clock)
    assert limiter.take() == 0
    for _ in range(5):
        assert limiter.take() > 0
    clock.now += 60
    assert limiter.take() == 0


def test_the_61st_call_in_a_minute_is_refused_and_never_reaches_tally(caplog_calls):
    reader = Reader()
    server = build_read_server(reader, ONE)
    for _ in range(60):
        assert call(server, "find_ledgers", {"query": "a"}).isError is not True
    result = call(server, "find_ledgers", {"query": "a"})
    assert result.isError is True
    assert result.content[0].text.startswith("The bridge takes 60 calls a minute. Try again in ")
    assert reader.posts == 60
    assert lines(caplog_calls)[-1]["result"] == "rate_limited"


def test_each_server_has_its_own_limit():
    """One server for each token, so one limit for each token."""
    a = build_read_server(Reader(), Settings(allowed_companies=("Alpha Co",), calls_per_minute=1))
    b = build_read_server(Reader(), Settings(allowed_companies=("Alpha Co",), calls_per_minute=1))
    assert call(a, "status", {}).isError is not True
    assert call(a, "status", {}).isError is True
    assert call(b, "status", {}).isError is not True


@pytest.mark.parametrize("value", ["0", "601", "many"])
def test_the_setting_refuses_a_bad_limit(value):
    with pytest.raises(SettingsError):
        settings_from_mapping({"CALLS_PER_MINUTE": value})


def test_the_default_limit_is_60():
    assert Settings().calls_per_minute == 60


# --- Rule 6: the call log ------------------------------------------------------------


def test_one_line_for_each_call_with_the_six_facts(caplog_calls):
    server = build_read_server(Reader(), ONE)
    call(server, "find_ledgers", {"query": "alpha", "company": "Alpha Co"})
    [line] = lines(caplog_calls)
    assert set(line) == {"time", "tool", "inputs", "row_count", "duration_ms", "result"}
    assert line["tool"] == "find_ledgers"
    assert line["inputs"] == {"query": "alpha", "company": "Alpha Co"}
    assert line["row_count"] == 1
    assert line["result"] == "ok"
    assert isinstance(line["duration_ms"], int)


@pytest.mark.parametrize(
    "reader,args,code",
    [
        (Reader(), {"query": "a", "company": "Other Co"}, "refused"),
        (Reader(TallyDown()), {"query": "a"}, "tally_down"),
        (Reader(), {"limit": 5}, "bad_input"),
    ],
)
def test_the_result_code_names_what_happened(caplog_calls, reader, args, code):
    call(build_read_server(reader, ONE), "find_ledgers", args)
    [line] = lines(caplog_calls)
    assert line["result"] == code
    assert line["row_count"] is None


def test_a_long_input_is_cut(caplog_calls):
    call(build_read_server(Reader(), ONE), "find_ledgers", {"query": "q" * 150})
    [line] = lines(caplog_calls)
    assert line["inputs"]["query"] == "q" * 100 + "..."


def test_brief_test_15_no_token_in_any_log_line(caplog):
    """A call over HTTP with the token: no log line of any logger holds it."""
    caplog.set_level(logging.DEBUG)
    token = new_token()
    app = build_address_app(ONE, MemorySecretStore(token_sha256(token)), Reader())
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "find_ledgers", "arguments": {"query": "alpha"}},
    }
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        assert c.post("/mcp", json=body, headers=headers).status_code == 200
        c.post("/mcp", json=body, headers={**headers, "Authorization": f"Bearer {token}x"})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "find_ledgers" in text
    assert token not in text
    assert token_sha256(token) not in text


def test_the_log_file_rotates_daily_and_keeps_90_days(tmp_path):
    path = open_call_log(str(tmp_path / "logs"))
    try:
        assert path == tmp_path / "logs" / "calls.log"
        [handler] = [
            h
            for h in logging.getLogger(calllog.CALL_LOG).handlers
            if isinstance(h, TimedRotatingFileHandler)
        ]
        assert handler.when == "MIDNIGHT" and handler.backupCount == 90
        assert open_call_log(str(tmp_path / "logs")) == path  # no second handler
        calllog.record("status", {}, "ok", 1.2, None)
        handler.flush()
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["tool"] == "status"
    finally:
        for h in list(logging.getLogger(calllog.CALL_LOG).handlers):
            if isinstance(h, TimedRotatingFileHandler):
                logging.getLogger(calllog.CALL_LOG).removeHandler(h)
                h.close()


def test_no_log_dir_means_no_file():
    assert open_call_log("") is None
