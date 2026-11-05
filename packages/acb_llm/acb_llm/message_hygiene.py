"""One place that keeps litellm's empty-text placeholder out of our chat.

WS task 1787. In production the main chat showed
``[System: Empty message content sanitised to satisfy protocol]`` before its
real reply.

**Where it came from.** litellm rewrites an empty or whitespace-only text
``content`` of a ``user`` or ``assistant`` message to that sentence. It does
this for every Anthropic-shaped request, and ``litellm.modify_params`` does
not gate it (``anthropic_messages_pt`` in
``litellm/litellm_core_utils/prompt_templates/factory.py``, which calls
``_sanitize_empty_text_content``). An assistant turn that held only tool calls
reached that code with ``content=""``, because the gateway seam wrote ``""``
(``gateway.routes.v1_compat._sanitize_messages_for_provider``, rule 2). The
model then read the sentence as its own past text and copied it.

**The fix has three parts, all here:**

* ``clean_model_messages`` builds the list that goes to the model. A
  tool-call-only assistant turn carries ``content=None``. An empty text block
  is dropped. A stored turn that holds the placeholder is cleaned.
* ``strip_placeholder_lines`` and ``PlaceholderStreamFilter`` remove the
  placeholder from model output, as it streams and as it is stored.
* ``EMPTY_TEXT_PLACEHOLDER`` is the one constant.

Fence: ``tests/unit/test_message_hygiene.py``.
"""
from __future__ import annotations

import re
from typing import Any

# The text that litellm writes. It is a private name in litellm
# (``_EMPTY_TEXT_PLACEHOLDER`` in
# ``litellm/litellm_core_utils/prompt_templates/factory.py``), so this is the
# one copy we own. ``test_message_hygiene.py`` fails when litellm changes it.
EMPTY_TEXT_PLACEHOLDER = (
    "[System: Empty message content sanitised to satisfy protocol]"
)

# A whole line that holds only a placeholder. Text that names the phrase in the
# middle of a sentence does not match, because the line must start and end with
# the bracket.
_PLACEHOLDER_LINE_RE = re.compile(
    r"^[ \t]*\[System:[^\]\n]*sanitised to satisfy protocol\][ \t]*$"
)
_LINE_SPLIT_RE = re.compile(r"[^\n]*\n|[^\n]+")
_HOLD_PREFIX = "[System:"
_HOLD_MAX = 200


def is_anthropic_shaped(name: str | None) -> bool:
    """True when *name* (a provider or model) is Anthropic-shaped.

    Only these requests pass through litellm's placeholder rewrite, so only
    they need ``content=None`` for a tool-call turn. DeepSeek, Groq and
    Together refuse null content. An unknown name is NOT Anthropic-shaped.
    """
    lowered = (name or "").lower()
    return "anthropic" in lowered or "claude" in lowered


def is_placeholder_line(line: str) -> bool:
    """True when *line* (no newline) holds only the placeholder."""
    return bool(_PLACEHOLDER_LINE_RE.match(line))


def strip_placeholder_lines(text: str) -> str:
    """Remove each line of *text* that holds only the placeholder.

    The line break after a removed line goes with it. Text with no such line
    comes back unchanged, byte for byte.
    """
    if not text or "sanitised to satisfy protocol" not in text:
        return text
    kept = [
        seg for seg in _LINE_SPLIT_RE.findall(text)
        if not is_placeholder_line(seg.rstrip("\n").rstrip("\r"))
    ]
    return "".join(kept)


def _could_still_match(partial: str) -> bool:
    """True when an unfinished line may yet become a placeholder line."""
    stripped = partial.strip()
    if not stripped:
        return True
    if len(stripped) > _HOLD_MAX:
        return False
    if _HOLD_PREFIX.startswith(stripped):
        return True
    return stripped.startswith(_HOLD_PREFIX) and (
        "]" not in stripped or is_placeholder_line(stripped)
    )


class PlaceholderStreamFilter:
    """Remove placeholder lines from a stream of text deltas.

    A line that may become a placeholder is held until it ends. Any other
    line passes at once, so normal text streams with no delay. Call ``flush``
    when the message ends, to release a held partial line.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._passthrough = False

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        out: list[str] = []
        for seg in _LINE_SPLIT_RE.findall(delta):
            if self._passthrough:
                out.append(seg)
                if seg.endswith("\n"):
                    self._passthrough = False
                continue
            self._buf += seg
            if self._buf.endswith("\n"):
                line = self._buf.rstrip("\n").rstrip("\r")
                if not is_placeholder_line(line):
                    out.append(self._buf)
                self._buf = ""
            elif not _could_still_match(self._buf):
                out.append(self._buf)
                self._buf = ""
                self._passthrough = True
        return "".join(out)

    def flush(self) -> str:
        buf, self._buf, self._passthrough = self._buf, "", False
        if buf and not is_placeholder_line(buf):
            return buf
        return ""


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _clean_blocks(blocks: list[Any], role: str) -> list[Any]:
    """Drop empty text blocks and placeholder-only text blocks."""
    kept: list[Any] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if role == "assistant":
                cleaned = strip_placeholder_lines(text)
                if not cleaned.strip():
                    continue
                if cleaned != text:
                    block = {**block, "text": cleaned}
        kept.append(block)
    return kept


def clean_model_messages(
    messages: list[dict[str, Any]], *, null_content_ok: bool = True,
) -> list[dict[str, Any]]:
    """Return *messages* in the shape that is safe to send to a model.

    * An assistant turn with tool calls and no text carries ``content=None``,
      never ``""`` or whitespace. A provider that refuses null content
      (``null_content_ok=False``) gets ``""``, and those providers do not run
      litellm's Anthropic placeholder.
    * An empty text block is dropped, not sent.
    * An assistant turn with neither text nor tool calls is dropped.
    * An empty user turn is dropped, except the last message of the list.
    * A line that holds only the placeholder is removed from assistant text,
      so a stored turn that kept it no longer teaches the model to copy it.

    The input list and its dicts are not changed.
    """
    out: list[dict[str, Any]] = []
    last = len(messages) - 1
    for idx, original in enumerate(messages):
        if not isinstance(original, dict):
            out.append(original)
            continue
        role = original.get("role")
        if role not in ("assistant", "user"):
            out.append(original)
            continue

        msg = original
        content = msg.get("content")
        if isinstance(content, str):
            if role == "assistant":
                cleaned = strip_placeholder_lines(content)
                if cleaned != content:
                    msg = {**msg, "content": cleaned}
                    content = cleaned
        elif isinstance(content, list):
            blocks = _clean_blocks(content, str(role))
            if len(blocks) != len(content) or any(
                a is not b for a, b in zip(blocks, content, strict=True)
            ):
                msg = {**msg, "content": blocks}
            content = blocks

        has_text = bool(content) if isinstance(content, list) else not _is_blank(
            content)
        if has_text:
            out.append(msg)
            continue

        if role == "assistant":
            if msg.get("tool_calls"):
                want: Any = None if null_content_ok else ""
                if msg.get("content") != want or "content" not in msg:
                    msg = {**msg, "content": want}
                out.append(msg)
            # else: nothing to say and nothing to call. Drop the turn.
            continue

        # An empty user turn. The last one stays as the caller sent it, and is
        # never rewritten to an empty block list.
        if idx == last:
            out.append(original)
    return out
