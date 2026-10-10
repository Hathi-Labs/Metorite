"""The Router's refusal log line names the cause and holds no content.

Measured 2026-10-08: the gateway logged ``v1.router_served status=400`` 18
times in three days, for every System-1 request. The Console logged a bare
``router.provider_error`` beside each one. The status sat in ``extra``, and
this logger has a plain formatter, so the journal never showed it. So the
cause was a guess.

The line now carries four fields, in the message text and in ``extra``:
``upstream_status``, ``vendor``, ``error_class`` and ``hints``. ``hints`` is
each word of ``router.ERROR_HINTS`` that the vendor's message names. The
message itself never reaches the line, because it can quote the request.

Hermetic: no SQL runs here, so R8 binds nothing. The walk is the REAL
``call_chain``, with a provider stub that raises.

Mutations this file catches (R7), each run red before the change:

* the fields leave the message text (``extra`` only, as before) ->
  ``test_the_journal_line_names_the_status_the_vendor_and_the_hint``;
* the line quotes the vendor's message ->
  ``test_the_line_holds_no_text_of_the_vendor_message``;
* the walk drops the vendor of the refusing step ->
  ``test_the_walk_names_the_vendor_that_refused``;
* the class-name check goes -> ``test_an_error_class_that_is_not_a_plain_name_is_unnamed``;
* a hint matches a word inside another word ->
  ``test_a_hint_needs_a_whole_word``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from customer_console import router as router_mod
from customer_console.router import (
    ResolvedTier,
    UpstreamFailed,
    call_chain,
    describe_failure,
    set_provider_call,
)

#: Request content that a vendor quotes back in its error. It must never
#: reach a log line.
SECRET = "Invoice 4471 for Acme Corp, overdue since March"


class VendorError(Exception):
    """A provider error, shaped like the ones litellm raises."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


def _step(model: str) -> ResolvedTier:
    return ResolvedTier(tier="fast", model=model, task="chat")


def _refused(message: str, status: int = 400, model: str = "deepseek/x-flash") -> UpstreamFailed:
    """The REAL walk, on a provider that refuses with *message*."""

    async def _call(**_kwargs: Any) -> Any:
        raise VendorError(status, message)

    previous = router_mod._PROVIDER_CALL[0]
    set_provider_call(_call)
    try:
        with pytest.raises(UpstreamFailed) as caught:
            asyncio.run(call_chain([_step(model)], lambda s: {"model": s.model}))
    finally:
        set_provider_call(previous)
    return caught.value


#: The message that the vendor behind ``tier-fast`` is suspected to send.
JSON_SCHEMA_REFUSAL = (
    f"DeepseekException - This response_format type is unavailable now. {SECRET}"
)


def test_the_walk_names_the_vendor_that_refused() -> None:
    failed = _refused(JSON_SCHEMA_REFUSAL)
    assert (failed.status, failed.vendor) == (400, "deepseek")
    assert isinstance(failed.__cause__, VendorError)


def test_the_fields_are_a_status_a_vendor_a_class_and_our_words() -> None:
    fields = describe_failure(_refused(JSON_SCHEMA_REFUSAL))
    assert fields == {
        "upstream_status": 400,
        "vendor": "deepseek",
        "error_class": "VendorError",
        "hints": "response_format",
    }


def test_the_journal_line_names_the_status_the_vendor_and_the_hint(caplog) -> None:
    main = pytest.importorskip("customer_console.main")
    failed = _refused(JSON_SCHEMA_REFUSAL)
    with caplog.at_level(logging.WARNING, logger="platform.router"):
        main._upstream_refusal(failed)
    lines = [r.getMessage() for r in caplog.records if r.name == "platform.router"]
    assert lines == [
        "router.provider_error upstream_status=400 vendor=deepseek "
        "error_class=VendorError hints=response_format",
    ]
    record = next(r for r in caplog.records if r.name == "platform.router")
    assert record.upstream_status == 400  # the structured copy stays


def test_the_line_holds_no_text_of_the_vendor_message(caplog) -> None:
    main = pytest.importorskip("customer_console.main")
    failed = _refused(f"{SECRET} json_schema is not supported, see {SECRET}")
    with caplog.at_level(logging.DEBUG):
        main._upstream_refusal(failed)
        main._log_refusal("router.stream_open_failed", failed)
    text = "\n".join(
        f"{r.getMessage()} {r.__dict__}" for r in caplog.records
    )
    assert "router.stream_open_failed upstream_status=400" in text
    assert "hints=json_schema" in text
    for word in ("Invoice", "4471", "Acme", "overdue", "supported"):
        assert word not in text, word


def test_a_hint_needs_a_whole_word() -> None:
    fields = describe_failure(_refused("the top_ply and xmax_tokens fields"))
    assert fields["hints"] == "none"


def test_an_error_class_that_is_not_a_plain_name_is_unnamed() -> None:
    """A type name is code. One that does not read as a plain name is dropped."""
    odd = type("Bad Name: " + SECRET, (VendorError,), {})
    failed = UpstreamFailed(400, vendor="deepseek")
    failed.__cause__ = odd(400, "x")
    assert describe_failure(failed)["error_class"] == "unnamed"


def test_a_failure_with_no_cause_still_logs() -> None:
    assert describe_failure(UpstreamFailed(None)) == {
        "upstream_status": None,
        "vendor": "none",
        "error_class": "none",
        "hints": "none",
    }
