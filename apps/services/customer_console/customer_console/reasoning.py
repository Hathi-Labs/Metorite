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

## Why litellm 1.103.0 does not replace this

Re-read on 2026-09-28, at the litellm 1.103.0 upgrade. litellm now has
``DeepSeekChatConfig._fill_reasoning_content``, and it is NOT this fix:

* **It runs only when the CALLER turns thinking on.** ``_thinking_mode_active``
  needs ``thinking={"type": "enabled"}`` in the request. The agents send no
  such parameter, so for our traffic it never runs.
* **When it runs, it sends a blank.** It reads
  ``provider_specific_fields["reasoning_content"]``, a key the framework does
  not write back. It then puts a single space in the field, and litellm's own
  warning says that degrades the reasoning chain in silence.
* **It does not rejoin the split turn.** See :func:`_rejoin_split_turns`.

So this module keeps carrying the model's REAL reasoning. litellm fills only a
turn that still has none, so the two do not collide.

⚠️ **Streaming is NOT covered, on purpose.** ``relay_stream`` promises
byte-identity — *"a frame that arrives as bytes is yielded as it arrived"* —
and a delta-level mirror would break that promise. H-180 carries it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger("platform.router")

__all__ = [
    "REASONING_PARAMS",
    "SAMPLING_PARAMS",
    "publish_reasoning_alias",
    "reasoning_for_vendor",
    "reasoning_params_for",
    "sampling_params_for",
    "step_takes",
    "step_takes_sampling",
]

#: The request parameters that control a model's thinking (task 1128).
REASONING_PARAMS = ("thinking", "reasoning_effort")


def step_takes(model: str, param: str) -> bool:
    """Whether litellm sends *param* to the step model *model* without a 400.

    The Router calls litellm with no ``drop_params``, so a parameter that the
    model's litellm config does not list raises ``UnsupportedParamsError``.
    Measured on litellm 1.103.0: DeepSeek takes ``thinking``, and OpenAI,
    Groq and older Claude and Gemini models refuse it. ``acb_llm/jobs.py``
    holds the full table per vendor, and the same rule (``model_takes``) for
    the gateway's direct path. This copy exists because the Console imports
    no ``acb_*`` package. A model that litellm cannot place reads as False.
    """
    try:
        import litellm

        name, provider, _key, _base = litellm.get_llm_provider(model)
        supported = litellm.get_supported_openai_params(
            model=name, custom_llm_provider=provider) or []
    except Exception:  # a model that litellm cannot place takes nothing
        return False
    return param in supported


def reasoning_params_for(kwargs: dict[str, Any], model: str) -> dict[str, Any]:
    """*kwargs* with no reasoning parameter that the step *model* does not take.

    🔴 **Why, task 1128 (2026-10-31).** The gateway asks for no thinking on a
    short job, because DeepSeek V4 spends a small cap on thinking and answers
    nothing. The gateway names a TIER, and the chain decides the model. A
    chain may mix vendors, so a step whose model refuses ``thinking`` would
    turn a good request into a 400. This drops the parameter for THAT step
    only. Each step builds its own body, so the next step keeps it.

    Returns *kwargs* itself when nothing is dropped, else a new dict.
    """
    drop = [k for k in REASONING_PARAMS if k in kwargs and not step_takes(model, k)]
    if not drop:
        return kwargs
    return {k: v for k, v in kwargs.items() if k not in drop}

#: The sampling values that a Claude 5.5 model refuses (task 2450).
SAMPLING_PARAMS = ("temperature", "top_p", "top_k")

#: The major version of a Claude id: ``claude-sonnet-5-5`` gives 5,
#: ``claude-3-5-sonnet-20241022`` gives 3.
_CLAUDE_MAJOR = re.compile(r"claude-(?:[a-z]+-)?(\d+)")

#: Claude families that never took sampling values.
_NO_SAMPLING_FAMILIES = ("fable", "mythos")

#: (model, dropped keys) pairs already logged by this process. The log line
#: fires once per pair, so a busy chat on Claude 5.5 does not write one line
#: per call (review of 2026-11-08). The cap bounds the memory.
_LOGGED_DROPS: set[tuple[str, str]] = set()
_LOGGED_DROPS_MAX = 256


def _claude_takes_sampling(model: str) -> bool:
    """The last wall: a Claude id at version 5 or later takes no sampling value.

    litellm's rule and its model map cover the Claude ids they know. A Claude
    id that is newer than the map (for example ``claude-sonnet-5-6``) would
    keep ``temperature=0.2`` and answer 400. Claude 5 and later accept only
    ``temperature=1``, so a drop is always safe for them.
    """
    low = model.lower()
    if "claude" not in low:
        return True
    if any(family in low for family in _NO_SAMPLING_FAMILIES):
        return False
    match = _CLAUDE_MAJOR.search(low)
    return not (match and int(match.group(1)) >= 5)


def step_takes_sampling(model: str) -> bool:
    """Whether litellm sends sampling values to the step model *model*.

    Measured on litellm 1.103.0: ``claude-haiku-5-5``, ``claude-sonnet-5-5``,
    ``claude-opus-5-5`` and ``claude-opus-4-8`` raise ``UnsupportedParamsError``
    for ``temperature=0.2``. Only ``temperature=1`` passes. Anthropic models
    use litellm's own rule (``AnthropicModelInfo._supports_sampling_params``).
    Any provider also honours ``supports_sampling_params: False`` in
    ``litellm.model_cost``. A model that litellm cannot place keeps its values.
    """
    try:
        import litellm

        name, provider, _key, _base = litellm.get_llm_provider(model)
    except Exception:  # a model that litellm cannot place keeps its params
        return True
    if provider == "anthropic":
        try:
            from litellm.llms.anthropic.common_utils import AnthropicModelInfo

            if not AnthropicModelInfo._supports_sampling_params(name):
                return False
        except Exception:  # litellm moved the rule: fall to the model map
            pass
    cost = getattr(litellm, "model_cost", None) or {}
    for key in (f"{provider}/{name}", name, model):
        entry = cost.get(key)
        if isinstance(entry, dict) and entry.get("supports_sampling_params") is False:
            return False
    return _claude_takes_sampling(name) and _claude_takes_sampling(model)


def sampling_params_for(kwargs: dict[str, Any], model: str) -> dict[str, Any]:
    """*kwargs* with no sampling value that the step *model* refuses.

    Task 2450. litellm runs here with no ``drop_params``, so ``temperature=0.2``
    on a Claude 5.5 step answers 400. This drops ``temperature``, ``top_p`` and
    ``top_k`` for THAT step only. A ``temperature`` of 1 stays, because litellm
    accepts it. Returns *kwargs* itself when nothing is dropped. The log line
    names the model and the dropped keys, and never the message content.
    """
    drop = [
        k for k in SAMPLING_PARAMS
        if k in kwargs and not (k == "temperature" and kwargs[k] == 1)
    ]
    if not drop or step_takes_sampling(model):
        return kwargs
    seen = (model, ",".join(drop))
    if seen not in _LOGGED_DROPS:
        if len(_LOGGED_DROPS) < _LOGGED_DROPS_MAX:
            _LOGGED_DROPS.add(seen)
        _log.info(
            "router.sampling_params_dropped",
            extra={"sp_model": model, "sp_dropped": seen[1]},
        )
    return {k: v for k, v in kwargs.items() if k not in drop}


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
    return _rejoin_split_turns(out)


def _is_text_only_assistant(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and bool(message.get("content"))
        and not message.get("tool_calls")
    )


def _is_tool_call_assistant(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and bool(message.get("tool_calls"))
    )


def _rejoin_split_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put back together the one turn the framework split in two.

    🔴 **Found by review, then MEASURED, and the first fix missed it.** When
    the model writes text AND calls a tool in one turn, the framework sends
    that turn back as TWO assistant messages: the text alone, then the tool
    calls carrying the reasoning. The vendor refused the text half, which had
    no reasoning of its own::

        text-only turn + tool turn (reasoning on the tool turn)   400
        the same turn MERGED back into one                         OK
        the same reasoning COPIED onto both halves                 OK
        an EARLIER plain answer with no reasoning, then a tool round   OK

    ⚠️ **Merged, not copied.** Both pass. A merge restores the turn the model
    actually produced, and a copy shows the model its own thought twice, as
    if it had reasoned the same way on two turns.

    ⚠️ **Only an ADJACENT pair.** A text answer followed by a user message is a
    finished turn, and the fourth probe shows the vendor accepts it bare.
    Joining across a user message would invent a turn nobody took.
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        here = messages[i]
        after = messages[i + 1] if i + 1 < len(messages) else None
        if (
            after is not None
            and _is_text_only_assistant(here)
            and _is_tool_call_assistant(after)
        ):
            merged = {**after, "content": here["content"]}
            thoughts = [
                t for t in (here.get(VENDOR_KEY), after.get(VENDOR_KEY))
                if isinstance(t, str) and t
            ]
            if thoughts:
                merged[VENDOR_KEY] = "\n".join(thoughts)
            out.append(merged)
            i += 2
            continue
        out.append(here)
        i += 1
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


# ── Empty content beside tool calls (task 1787) ─────────────────────────────
#
# litellm turns ``content: ""`` into the visible sentence "[System: Empty
# message content sanitised to satisfy protocol]" for an Anthropic-shaped step,
# and the model then copies it into its replies. DeepSeek, Groq and Together
# refuse ``content: null`` beside ``tool_calls``. So the right value depends on
# the step that really runs, and only the Router knows it. A failover chain may
# mix both, so ``main._kwargs_for`` calls this once per step.
#
# ⚠️ This package imports no ``acb_*`` package (the Console is the other side of
# the tenant boundary), so ``acb_llm.message_hygiene`` is not reachable here.
# This is the Router's half of the one rule. The gateway half, and the strip of
# stored placeholder lines, stay in ``acb_llm.message_hygiene``.
_NULL_CONTENT_REFUSED_BY = ("deepseek", "groq", "together", "together_ai")


def content_for_step(
    messages: list[dict[str, Any]], model: str,
) -> list[dict[str, Any]]:
    """Give each tool-call assistant turn the empty content *model* accepts.

    The provider prefix of the model id decides, as the credential lookup
    does. So ``anthropic/deepseek-x`` is Anthropic-shaped.

    * An Anthropic-shaped step gets ``None`` for blank text. That is provider
      ``anthropic``, or a Claude model on another provider such as Bedrock.
    * A provider that refuses null content gets ``""``.
    * Any other step, and any other message, is returned unchanged.
    """
    name = (model or "").lower()
    provider = name.split("/", 1)[0]
    want: str | None
    if provider == "anthropic" or (
        provider not in _NULL_CONTENT_REFUSED_BY and "claude" in name
    ):
        want = None
    elif provider in _NULL_CONTENT_REFUSED_BY:
        want = ""
    else:
        return messages
    out: list[dict[str, Any]] = []
    for m in messages:
        if (
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and m.get("tool_calls")
            and (m.get("content") is None
                 or (isinstance(m.get("content"), str)
                     and not m["content"].strip()))
            and m.get("content") != want
        ):
            m = {**m, "content": want}
        out.append(m)
    return out


def messages_for_step(
    messages: list[dict[str, Any]], model: str,
) -> list[dict[str, Any]]:
    """The messages to send to ONE step of a chain: reasoning, then content."""
    return content_for_step(reasoning_for_vendor(messages), model)
