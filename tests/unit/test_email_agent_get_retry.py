"""The email agent's ``_get`` (owner goal 2026-10-22, fix every chat case).

R7 - each rule names the mutation that turns it red:

1. **A read retries ONCE on a 503** (the gateway's pool is full). Mutation:
   drop the retry -> ``test_a_503_read_is_retried_once``.
2. **A read does not retry twice**, and a write never retries. Mutation: loop
   the retry -> ``test_a_second_503_is_raised``, or move it into
   ``_request`` -> ``test_a_write_is_never_retried``.
3. **A bare 403 or 404 reads as a plain sentence**, and the gateway's own
   words stay. Mutation: drop the mapping -> ``test_a_bare_404_gets_a_plain_sentence``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load():
    spec = importlib.util.spec_from_file_location("ea_get_retry", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data

    def json(self) -> Any:
        return self._data


def _err(status: int, detail: str = "") -> Exception:
    return agents.GatewayError(f"failed ({status})", status, detail=detail)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def no_wait(_s: float) -> None:
        return None

    monkeypatch.setattr(agents.asyncio, "sleep", no_wait)
    monkeypatch.setattr(agents, "_GET_RETRY_DELAY_S", 0)
    return seen


def _script(monkeypatch: pytest.MonkeyPatch, seen: list, replies: list) -> None:
    async def fake(method: str, path: str, **_kw: Any) -> Any:
        seen.append((method, path))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Resp(reply)

    monkeypatch.setattr(agents, "_request", fake)


async def test_a_503_read_is_retried_once(monkeypatch, calls) -> None:
    _script(monkeypatch, calls, [_err(503), {"ok": 1}])
    assert await agents._get("/email/messages") == {"ok": 1}
    assert calls == [("GET", "/email/messages")] * 2


async def test_a_second_503_is_raised(monkeypatch, calls) -> None:
    _script(monkeypatch, calls, [_err(503), _err(503), {"ok": 1}])
    with pytest.raises(agents.GatewayError) as info:
        await agents._get("/email/messages")
    assert info.value.status == 503 and len(calls) == 2


async def test_another_error_is_not_retried(monkeypatch, calls) -> None:
    _script(monkeypatch, calls, [_err(500), {"ok": 1}])
    with pytest.raises(agents.GatewayError):
        await agents._get("/email/messages")
    assert len(calls) == 1


async def test_a_write_is_never_retried(monkeypatch, calls) -> None:
    _script(monkeypatch, calls, [_err(503), {"ok": 1}])
    with pytest.raises(agents.GatewayError):
        await agents._post("/email/messages/bulk", {"action": "read"})
    assert calls == [("POST", "/email/messages/bulk")]


@pytest.mark.parametrize("status", [403, 404])
async def test_a_bare_404_gets_a_plain_sentence(monkeypatch, calls, status) -> None:
    _script(monkeypatch, calls, [_err(status)])
    with pytest.raises(agents.GatewayError) as info:
        await agents._get("/email/analytics/overview")
    assert info.value.status == status
    assert info.value.detail == "I cannot see that mailbox or message for this account."
    from acb_common.tool_refusals import refusal_text
    text = refusal_text(info.value, agents.EMAIL_VOICE)
    assert "I cannot see that mailbox" in text


async def test_the_gateways_own_words_stay(monkeypatch, calls) -> None:
    _script(monkeypatch, calls, [_err(404, "Mailbox not found")])
    with pytest.raises(agents.GatewayError) as info:
        await agents._get("/email/analytics/overview")
    assert info.value.detail == "Mailbox not found"
