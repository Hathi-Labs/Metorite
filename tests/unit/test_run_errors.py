"""The run-error vocabulary: one stable code per failure class.

Owner report, 2026-10-08. A Projects chat showed the member the raw Python
repr of an ``APIConnectionError``. The fix is a code the SERVER names
(``acb_llm.run_errors``) and words the BROWSER holds per code
(``workbench/control_plane/src/lib/runErrors.ts``).

Mutations each test was run against are named in the test.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import openai
import pytest
from acb_llm.run_errors import (
    RUN_ERROR_CODES,
    classify_run_error,
    classify_status,
    run_error_event,
    run_ref,
)
from agent_framework.exceptions import ChatClientException

ROOT = Path(__file__).resolve().parents[2]
TS_WORDS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "runErrors.ts"

_REQ = httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions")


def _status_error(status: int) -> openai.APIStatusError:
    return openai.APIStatusError(
        f"Error code: {status}", response=httpx.Response(status, request=_REQ), body=None,
    )


def _wrapped(inner: BaseException) -> ChatClientException:
    """The exact shape the member saw: the agent framework's wrapper, with
    the SDK error as its second arg, raised ``from`` it."""
    try:
        try:
            raise inner
        except BaseException as exc:
            raise ChatClientException(
                "<class 'agent_framework_openai._chat_completion_client."
                "OpenAIChatCompletionClient'> service failed to complete the "
                f"prompt: {exc}",
                inner_exception=exc,  # type: ignore[arg-type]
            ) from exc
    except ChatClientException as outer:
        return outer


class TestTheClassifier:
    """Mutation: make ``_code_of`` return None for openai.APIConnectionError,
    and the first two cases fail. Swap the APITimeoutError and
    APIConnectionError checks, and the timeout case fails."""

    @pytest.mark.parametrize(
        ("exc", "code"),
        [
            (openai.APIConnectionError(request=_REQ), "connection"),
            (_wrapped(openai.APIConnectionError(request=_REQ)), "connection"),
            (httpx.ConnectError("[Errno 111] Connection refused"), "connection"),
            (_wrapped(httpx.ConnectError("refused")), "connection"),
            (ConnectionRefusedError(111, "refused"), "connection"),
            (httpx.RemoteProtocolError("Server disconnected"), "connection"),
            (openai.APITimeoutError(request=_REQ), "timeout"),
            (httpx.ReadTimeout("read timed out"), "timeout"),
            (TimeoutError(), "timeout"),
            (_wrapped(_status_error(402)), "credits"),
            (_status_error(429), "rate_limited"),
            (_status_error(400), "model_refused"),
            (_status_error(422), "model_refused"),
            (_status_error(403), "permission"),
            (_status_error(503), "connection"),
            (_status_error(504), "timeout"),
            (_status_error(401), "unknown"),
            (_status_error(500), "unknown"),
            (asyncio.CancelledError(), "cancelled"),
            (KeyError("projects"), "unknown"),
            (RuntimeError("something odd"), "unknown"),
        ],
    )
    def test_each_failure_class_maps_to_its_code(self, exc, code):
        assert classify_run_error(exc) == code

    def test_the_owners_exact_failure_is_connection(self):
        """The repr the member read, rebuilt from the real classes."""
        exc = _wrapped(openai.APIConnectionError(request=_REQ))
        assert "APIConnectionError" in str(exc)  # the repr the member saw
        assert classify_run_error(exc) == "connection"

    def test_every_code_it_returns_is_in_the_vocabulary(self):
        for status in range(100, 600):
            assert classify_status(status) in RUN_ERROR_CODES

    def test_a_chain_that_loops_does_not_hang(self):
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert classify_run_error(a) == "unknown"


class TestTheEvent:
    """Mutation: drop the ``code not in RUN_ERROR_CODES`` guard, and the
    off-vocabulary case fails. Put ``type(exc).__name__`` back as the code,
    and the no-leak case fails."""

    def test_an_unknown_exception_names_no_class_and_no_repr(self):
        exc = ValueError("('<class \"x.Y\">', APIConnectionError('boom'))")
        ev = run_error_event(exc, run_id="9b1d2c3e-aaaa")
        assert ev["type"] == "RUN_ERROR"
        assert ev["code"] == "unknown"
        assert ev["ref"] == "9b1d2c3e"
        assert ev["runId"] == "9b1d2c3e-aaaa"
        # The member-facing fields carry nothing of the exception.
        for field in ("code", "ref"):
            assert "<class" not in ev[field] and "Error" not in ev[field]
        # The raw text stays, for the fold and the copy button only.
        assert ev["message"] == str(exc)

    def test_a_code_outside_the_vocabulary_becomes_unknown(self):
        assert run_error_event(code="ValueError")["code"] == "unknown"

    def test_the_raw_message_is_capped(self):
        assert len(run_error_event(RuntimeError("x" * 9000))["message"]) == 2000

    def test_the_ref_is_short_and_readable(self):
        assert run_ref("3F9C2A1B-77") == "3f9c2a1b"
        assert re.fullmatch(r"[0-9a-f]{8}", run_ref(None))

    def test_the_log_line_carries_the_ref_and_never_the_message(self, monkeypatch):
        from acb_llm import run_errors

        seen: list[dict] = []

        class _Log:
            def warning(self, event, **kw):
                seen.append({"event": event, **kw})

        monkeypatch.setattr(run_errors, "_log", _Log())
        run_error_event(RuntimeError("member words"), run_id="abcdef1234")
        assert seen and seen[0]["ref"] == "abcdef12"
        assert "member words" not in repr(seen)


def test_the_browser_holds_words_for_every_code():
    """The two lists cannot drift. Mutation: delete ``rate_limited`` from
    ``RUN_ERROR_WORDS`` in runErrors.ts, and this fails."""
    src = TS_WORDS.read_text(encoding="utf-8")
    block = src[src.index("export const RUN_ERROR_WORDS"):]
    block = block[: block.index("\n};")]
    ts_codes = set(re.findall(r"^\s{2}([a-z_]+):\s*\{", block, re.M))
    assert ts_codes == set(RUN_ERROR_CODES)
