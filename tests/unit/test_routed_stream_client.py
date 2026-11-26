"""Task 3542: the parser of the Router's stream, ``acb_llm.routed``.

``console_resolve.stream_completion_on_console`` yields the Router's SSE bytes
as ``aiter_bytes`` gives them, so a chunk can end in the middle of a frame,
of a line or of a UTF-8 character. ``RouterStreamParser`` turns those bytes
into frames, and ``context._routed_text`` sends each delta to the composer.

Pure: no network, no clock and no settings. The client and the bounds are
fenced in ``test_internal_ai_is_routed.py`` (``TestTheSTREAMINGArmIsRouted``).
"""
from __future__ import annotations

import json

import pytest
from acb_llm.routed import (
    MAX_STREAM_FRAME_BYTES,
    RouterStreamBroken,
    RouterStreamFrame,
    RouterStreamParser,
)

#: Four frames as the Console sends them: the output of
#: ``customer_console.router.frame_of`` on litellm ``ModelResponseStream``
#: chunks (``model_dump_json(exclude_none=True)``). The role frame carries the
#: reasoning, the finish frame has an empty ``delta``, and the usage frame has
#: no choice at all. ``test_the_fixture_is_what_frame_of_makes`` checks them.
REASONING = (
    b'data: {"id":"x","created":1,"model":"deepseek/deepseek-v4-flash",'
    b'"object":"chat.completion.chunk","choices":[{"index":0,"delta":'
    b'{"reasoning_content":"think","role":"assistant"}}]}\n\n'
)
CONTENT = (
    b'data: {"id":"x","created":1,"model":"deepseek/deepseek-v4-flash",'
    b'"object":"chat.completion.chunk","choices":[{"index":0,"delta":'
    b'{"content":"Hi"}}]}\n\n'
)
FINISH = (
    b'data: {"id":"x","created":1,"model":"deepseek/deepseek-v4-flash",'
    b'"object":"chat.completion.chunk","choices":[{"finish_reason":"stop",'
    b'"index":0,"delta":{}}]}\n\n'
)
USAGE = (
    b'data: {"id":"x","created":1,"model":"deepseek/deepseek-v4-flash",'
    b'"object":"chat.completion.chunk","choices":[],"usage":'
    b'{"completion_tokens":2,"prompt_tokens":3,"total_tokens":5}}\n\n'
)
DONE = b"data: [DONE]\n\n"
STREAM = REASONING + CONTENT + FINISH + USAGE + DONE


def _content(text: str) -> bytes:
    return b"data: " + json.dumps(
        {"model": "m", "choices": [{"index": 0, "delta": {"content": text}}]},
        ensure_ascii=False,
    ).encode("utf-8") + b"\n\n"


def _parse(*chunks: bytes) -> tuple[RouterStreamParser, list[RouterStreamFrame]]:
    parser = RouterStreamParser()
    frames: list[RouterStreamFrame] = []
    for chunk in chunks:
        frames.extend(parser.feed(chunk))
    return parser, frames


def _deltas(frames: list[RouterStreamFrame]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for frame in frames:
        if frame.reasoning:
            out.append(("reasoning", frame.reasoning))
        if frame.content:
            out.append(("content", frame.content))
    return out


# ── A whole stream ──────────────────────────────────────────────────────────


def test_a_reasoning_then_content_stream_reads_in_order() -> None:
    parser, frames = _parse(STREAM)
    assert _deltas(frames) == [("reasoning", "think"), ("content", "Hi")]
    assert parser.done and parser.frames == 4
    parser.end()  # a data frame, then [DONE]: no raise


def test_every_data_frame_is_a_frame_even_with_no_delta() -> None:
    """The finish frame and the usage frame carry no text. Each is still a
    frame, because any frame is a sign of life for the idle bound."""
    _parser, frames = _parse(STREAM)
    assert [bool(f.reasoning or f.content) for f in frames] == [True, True, False, False]
    assert frames[2].finish_reason == "stop"


def test_the_usage_frame_is_never_content() -> None:
    _parser, frames = _parse(STREAM)
    usage = frames[3]
    assert usage.content == "" and usage.reasoning == ""
    assert usage.usage == {"completion_tokens": 2, "prompt_tokens": 3, "total_tokens": 5}
    assert all("completion_tokens" not in f.content for f in frames)


def test_each_frame_names_the_model_that_answered() -> None:
    _parser, frames = _parse(STREAM)
    assert {f.model for f in frames} == {"deepseek/deepseek-v4-flash"}


def test_the_fixture_is_what_frame_of_makes() -> None:
    """The fixture above is not a guess: ``frame_of`` on real litellm chunks
    gives the same JSON, except for the ``created`` time."""
    pytest.importorskip("litellm")
    pytest.importorskip("fastapi")
    from customer_console.router import frame_of
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage

    model = "deepseek/deepseek-v4-flash"
    chunks = [
        ModelResponseStream(id="x", model=model, choices=[StreamingChoices(
            index=0, delta=Delta(role="assistant", reasoning_content="think"))]),
        ModelResponseStream(id="x", model=model, choices=[StreamingChoices(
            index=0, delta=Delta(content="Hi"))]),
        ModelResponseStream(id="x", model=model, choices=[StreamingChoices(
            index=0, delta=Delta(), finish_reason="stop")]),
        ModelResponseStream(id="x", model=model, choices=[], usage=Usage(
            prompt_tokens=3, completion_tokens=2, total_tokens=5)),
    ]
    made = [json.loads(frame_of(c)[len(b"data: "):]) for c in chunks]
    fixed = [json.loads(f[len(b"data: "):]) for f in (REASONING, CONTENT, FINISH, USAGE)]
    for one in (*made, *fixed):
        one.pop("created")
    assert made == fixed
    _parser, frames = _parse(*(frame_of(c) for c in chunks), DONE)
    assert _deltas(frames) == [("reasoning", "think"), ("content", "Hi")]


# ── Bytes that do not align with frames ─────────────────────────────────────


def test_one_byte_at_a_time_reads_the_same() -> None:
    """``aiter_bytes`` does not cut on a frame. The worst cut is every byte."""
    whole = _parse(STREAM)[1]
    parser, frames = _parse(*(STREAM[i:i + 1] for i in range(len(STREAM))))
    assert frames == whole and parser.done


def test_a_utf8_character_split_across_two_chunks_is_whole() -> None:
    raw = _content("Namaste, ₹500 — é") + DONE
    cut = raw.index("₹".encode()) + 1  # inside the three bytes of the sign
    parser, frames = _parse(raw[:cut], raw[cut:])
    assert _deltas(frames) == [("content", "Namaste, ₹500 — é")]
    assert parser.done


def test_crlf_line_ends_read_the_same() -> None:
    _parser, frames = _parse(STREAM.replace(b"\n", b"\r\n"))
    assert _deltas(frames) == [("reasoning", "think"), ("content", "Hi")]


def test_a_crlf_split_between_two_chunks_reads_the_same() -> None:
    raw = (CONTENT + DONE).replace(b"\n", b"\r\n")
    cut = raw.index(b"\r\n") + 1  # the \r in one chunk, the \n in the next
    _parser, frames = _parse(raw[:cut], raw[cut:])
    assert _deltas(frames) == [("content", "Hi")]


def test_data_with_no_space_after_the_colon() -> None:
    raw = CONTENT.replace(b"data: ", b"data:") + b"data:[DONE]\n\n"
    parser, frames = _parse(raw)
    assert _deltas(frames) == [("content", "Hi")] and parser.done


def test_the_data_lines_of_one_event_join_with_a_newline() -> None:
    raw = b'data: {"choices":[{"delta":\ndata: {"content":"two lines"}}]}\n\n' + DONE
    _parser, frames = _parse(raw)
    assert _deltas(frames) == [("content", "two lines")]


# ── Lines that are not frames ───────────────────────────────────────────────


def test_a_keepalive_comment_makes_no_frame() -> None:
    parser, frames = _parse(b": keepalive\n\n", CONTENT, b":\n\n", b": ping\n\n", DONE)
    assert _deltas(frames) == [("content", "Hi")]
    assert len(frames) == 1 and parser.frames == 1


def test_the_other_fields_are_not_read() -> None:
    raw = b"event: message\nid: 7\nretry: 1000\n" + CONTENT + DONE
    _parser, frames = _parse(raw)
    assert _deltas(frames) == [("content", "Hi")]


def test_a_blank_line_with_no_data_makes_no_frame() -> None:
    parser, frames = _parse(b"\n\n\n", CONTENT, DONE)
    assert len(frames) == 1 and parser.frames == 1


def test_a_choice_with_no_delta_is_an_empty_frame() -> None:
    raw = (b'data: {"choices":[{"index":0}]}\n\n'
           b'data: {"choices":["not a dict"]}\n\n'
           b'data: {"choices":[{"delta":{"content":null}}]}\n\n' + DONE)
    parser, frames = _parse(raw)
    assert _deltas(frames) == [] and parser.frames == 3


# ── [DONE] and the end of the bytes ─────────────────────────────────────────


def test_done_ends_the_read_and_the_rest_is_not_read() -> None:
    parser, frames = _parse(CONTENT + DONE + _content("after the end"), _content("later"))
    assert _deltas(frames) == [("content", "Hi")]
    assert parser.done and parser.frames == 1


def test_bytes_that_end_before_done_are_truncated() -> None:
    parser, frames = _parse(CONTENT, b'data: {"choices":[{"delta":{"content":"cut')
    assert _deltas(frames) == [("content", "Hi")]
    with pytest.raises(RouterStreamBroken) as info:
        parser.end()
    assert info.value.code == "stream_truncated"


def test_no_bytes_at_all_is_truncated() -> None:
    with pytest.raises(RouterStreamBroken, match="stream_truncated"):
        RouterStreamParser().end()


def test_a_lone_done_is_a_stream_that_never_opened() -> None:
    """A Console from before AO-3 sends exactly this when every step of the
    chain failed before a frame. It is not an empty answer. Since AO-3 the
    Console answers 503 ``unavailable`` (``main._stream_unavailable``)."""
    parser, frames = _parse(DONE)
    assert frames == [] and parser.done
    with pytest.raises(RouterStreamBroken) as info:
        parser.end()
    assert info.value.code == "stream_never_opened"


# ── A frame that is broken ──────────────────────────────────────────────────


def test_an_error_frame_fails_and_its_text_stays_out() -> None:
    raw = (CONTENT + b'data: {"error":{"message":"SECRETWORD7 quoted from the '
           b'prompt","type":"server_error","code":500}}\n\n')
    with pytest.raises(RouterStreamBroken) as info:
        _parse(raw)
    assert info.value.code == "stream_error_frame"
    assert str(info.value) == "stream_error_frame"
    assert "SECRETWORD7" not in repr(info.value)


def test_a_null_error_is_not_an_error() -> None:
    """The OpenAI client fails a frame on a truthy ``error`` only."""
    raw = b'data: {"error":null,"choices":[{"delta":{"content":"ok"}}]}\n\n' + DONE
    _parser, frames = _parse(raw)
    assert _deltas(frames) == [("content", "ok")]


@pytest.mark.parametrize("data", [
    b"data: {not json}\n\n",
    b'data: ["a", "list"]\n\n',
    b"data: 42\n\n",
    b'data: "a string"\n\n',
    b'data: {"choices":[{"delta":{"content":"\xff\xfe"}}]}\n\n',
], ids=["not-json", "list", "number", "string", "bad-utf8"])
def test_a_malformed_frame_fails(data: bytes) -> None:
    with pytest.raises(RouterStreamBroken) as info:
        _parse(CONTENT, data)
    assert info.value.code == "stream_malformed_frame"


def test_a_line_past_the_cap_fails_before_it_grows() -> None:
    parser = RouterStreamParser(max_frame_bytes=64)
    with pytest.raises(RouterStreamBroken) as info:
        for _ in range(10):
            parser.feed(b"data: " + b"x" * 20)  # no newline, ever
    assert info.value.code == "stream_frame_too_large"


def test_an_event_of_many_data_lines_past_the_cap_fails() -> None:
    parser = RouterStreamParser(max_frame_bytes=64)
    with pytest.raises(RouterStreamBroken, match="stream_frame_too_large"):
        parser.feed(b"data: " + b"y" * 30 + b"\n" + b"data: " + b"y" * 40 + b"\n")


def test_the_default_cap_holds_a_whole_long_answer() -> None:
    """A 3,000-token draft in ONE frame is about 12 KB, far under the cap."""
    long_text = "word " * 4000
    parser, frames = _parse(_content(long_text) + DONE)
    assert frames[0].content == long_text and parser.done
    assert MAX_STREAM_FRAME_BYTES >= 1_048_576
