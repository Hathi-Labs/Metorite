"""Carry a thinking model's reasoning across a tool round-trip. H-179.

Spec: ``customer_console.md`` §6A (the Router owns the vendor seam).

🔴 **A tool-calling agent could not hold a conversation with a thinking
model.** Owner report, 2026-09-24, minutes after ``ROUTER_SERVING_ENABLED``
went on. The Projects chat made four calls and the fifth returned 400::

    The `reasoning_content` in the thinking mode must be passed back to the API.

DeepSeek's thinking models return their reasoning in ``reasoning_content`` and
REQUIRE it back on every later assistant turn. The agent framework does keep
reasoning — but it reads and writes ``reasoning_details``, which is
OpenRouter's name for the same thing::

    # agent_framework_openai/_chat_completion_client.py:699
    if reasoning_details := getattr(choice.message, "reasoning_details", None):

Two vendors, two spellings, and nothing in our tree spoke either. So the
framework found nothing, kept nothing, and sent nothing back.

## Why this lives in the Router

The Router is already the seam that turns a TIER into a vendor model. It is
the only place that sees both the caller's OpenAI-shaped request and the
vendor's answer, so a vendor's spelling is exactly its business. Fixing it in
the agent framework would mean patching a third-party client, and fixing it in
each of the four agents would mean four copies of one rule.

## What was MEASURED, against the live vendor

Four probes against ``deepseek/deepseek-v4-pro`` on 2026-09-24:

============================================== ======
Assistant turns carry reasoning, all of them   OK
Only the LAST assistant turn carries it        **400**
``reasoning_content`` AND ``reasoning_details`` OK
``reasoning_details`` alone — today's shape    **400**
============================================== ======

⚠️ **The second row is why this walks the whole history.** Reasoning on the
final turn alone is refused, so a fix that patched only the newest message
would still fail on the second tool call.

⚠️ **The third row is why nothing is stripped.** The vendor tolerates the
extra key, so both spellings travel together. Removing one would make this
module decide which vendor is listening, and it does not have to.

⚠️ **Streaming is NOT covered, on purpose.** ``relay_stream`` promises
byte-identity — *"a frame that arrives as bytes is yielded as it arrived"* —
and a delta-level mirror would break that promise. H-180 carries it.
"""
from __future__ import annotations

from typing import Any

__all__ = ["publish_reasoning_alias", "reasoning_for_vendor"]

#: What DeepSeek calls it, and what it wants back.
VENDOR_KEY = "reasoning_content"

#: What OpenRouter calls it, and the ONLY name the agent framework knows.
FRAMEWORK_KEY = "reasoning_details"


def _as_text(details: Any) -> str | None:
    """Flatten whatever sits under ``reasoning_details`` into one string.

    Two shapes reach this, because two producers write the key:

    * **A plain string** — what :func:`publish_reasoning_alias` writes, since
      it mirrors DeepSeek's own string and the framework round-trips the value
      verbatim (``json.dumps`` out, ``json.loads`` back).
    * **A list of parts** — OpenRouter's native shape, each part a mapping with
      a ``text`` field.

    ⚠️ Returns ``None`` rather than ``""`` for anything else. An empty string
    is a value the vendor would accept and learn nothing from, and it would
    hide a shape we failed to read. ``None`` leaves the message untouched, so
    the vendor's own error names the problem.
    """
    if isinstance(details, str):
        return details or None
    if isinstance(details, list):
        parts = [
            str(p["text"])
            for p in details
            if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]
        ]
        return "\n".join(parts) or None
    return None


def reasoning_for_vendor(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every assistant turn the ``reasoning_content`` the vendor demands.

    Reads ``reasoning_details`` (the framework's spelling) and writes
    ``reasoning_content`` (the vendor's) beside it. Returns a NEW list, and
    copies only the messages it changes.

    ⚠️ **Additive, and vendor-neutral by construction.** It fires on the
    PRESENCE of the framework's key, never on a vendor name. So it cannot go
    stale when a tier is re-pointed, and there is no provider list here for
    somebody to forget to update. A conversation that carries no reasoning is
    returned unchanged.

    ⚠️ **An existing ``reasoning_content`` always wins.** A caller that already
    speaks the vendor's spelling is right, and overwriting it with our
    flattened copy would lose whatever the caller meant.
    """
    if not messages:
        return messages

    out: list[dict[str, Any]] = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or message.get(VENDOR_KEY)
            or FRAMEWORK_KEY not in message
        ):
            out.append(message)
            continue
        text = _as_text(message[FRAMEWORK_KEY])
        if text is None:
            out.append(message)
            continue
        # A copy, because `req.messages` belongs to the request and a failover
        # step must not inherit the edit the step before it made.
        out.append({**message, VENDOR_KEY: text})
    return out


def publish_reasoning_alias(response: Any) -> Any:
    """Mirror the vendor's ``reasoning_content`` under the framework's name.

    Without this the framework never sees the reasoning at all, so there is
    nothing for :func:`reasoning_for_vendor` to send back one turn later. The
    two functions are one mechanism, and neither works alone.

    Mutates and returns the response. Rebuilding a ``ModelResponse`` here would
    cost a full re-validation on the hottest path in the system, and the object
    is ours by this point — it is about to be serialised and discarded.

    ⚠️ **Never overwrites.** A vendor that speaks ``reasoning_details`` natively
    already put the richer structure there, and a flattened string is worse.

    ⚠️ **Swallows its own failures.** A response shape we cannot walk is a
    reason to lose the reasoning, never a reason to lose the completion the
    customer has already been charged for.
    """
    try:
        for choice in _field(response, "choices") or []:
            message = _field(choice, "message")
            if message is None:
                continue
            reasoning = _field(message, VENDOR_KEY)
            if not isinstance(reasoning, str) or not reasoning:
                continue
            if _field(message, FRAMEWORK_KEY):
                continue
            if isinstance(message, dict):
                message[FRAMEWORK_KEY] = reasoning
            else:
                setattr(message, FRAMEWORK_KEY, reasoning)
    except Exception:
        pass
    return response


def _field(obj: Any, name: str) -> Any:
    """Read one field from a mapping OR an object.

    🔴 **Both shapes reach the route, and a first version read only one.**
    The provider call returns whatever it returns: litellm hands back a
    ``ModelResponse``, and the route relays it unchanged. A version that walked
    attributes alone skipped every dict in silence. The wiring test caught
    it, because its stub answers with a dict.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
