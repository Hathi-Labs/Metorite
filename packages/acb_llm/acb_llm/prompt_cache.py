"""Provider-aware prompt-cache transform (specs/llm_caching_memory.md, Phase 2/3).

Metorite talks to providers through the **litellm SDK directly** — there is
no LiteLLM proxy process, so the plan's "proxy pre-call hook" doesn't apply.
Instead this module is the single request-transform that BOTH completion paths
call right before ``acompletion()``:

  * ``acb_llm.client.complete`` / ``complete_with_tools`` — orchestrator-internal
    calls (triage, extraction, mutation analysis).
  * ``gateway.routes.v1_compat._handle_chat_completions`` — the OpenAI-compatible
    choke point every agent runtime (native MAF ``OpenAIChatCompletionClient``,
    GitHub-Copilot SDK) POSTs through.

Because litellm accepts ``cache_control`` on OpenAI-format message content blocks
and on tool definitions and translates them to Anthropic's cache_control
(verified against litellm 1.86.0), the transform operates purely on the
OpenAI-shaped request and lets litellm do the provider translation.

Provider behaviour:
  * **Anthropic / Claude** — explicit ``cache_control: {"type": "ephemeral"}`` on
    the stable system block (split at the ``CACHE_BREAK`` sentinel) and on the
    last tool schema. Order Anthropic caches in is tools → system → messages, so
    a stable ``tools`` array is the highest-value block for function-tool agents.
  * **OpenAI** — caching is automatic for prompts ≥ 1024 tokens; we only add
    ``prompt_cache_key`` so same-agent requests route to the same server pool.
    The sentinel is stripped (never reaches the model).
  * **Everything else** (DeepSeek — our current default tier, Groq, Gemini,
    Mistral, OpenRouter, …) — provider handles context caching automatically or
    not at all; we just strip the sentinel so it never leaks into the prompt.

The sentinel is invisible to the model in every case: it is either consumed
(Anthropic: becomes the block boundary) or stripped (everyone else).
"""
from __future__ import annotations

import re
from typing import Any

# Marks the stable-prefix / dynamic-suffix boundary inside a system prompt.
# Inserted by the executor at the point where memory context is appended
# (specs Phase 3.1). Written as an HTML comment so that, in the unlikely event
# it is ever NOT consumed, it reads as an inert comment rather than instructions.
CACHE_BREAK = "<!-- CACHE BREAK -->"

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

# A regex that also swallows surrounding blank lines the executor adds around
# the sentinel, so stripping it leaves clean text (no dangling "\n\n\n").
_SENTINEL_RE = re.compile(r"\n*" + re.escape(CACHE_BREAK) + r"\n*")


def is_anthropic_model(model: str) -> bool:
    """True if *model* routes to Anthropic (explicit cache_control applies).

    Matches the direct ``anthropic/…`` prefix, bare ``claude-…`` names, and the
    Copilot-proxied ``…/claude-…`` entries — but NOT ``openrouter/anthropic/…``
    (OpenRouter proxies Anthropic but does not honour our cache_control blocks;
    treat it as automatic-only to avoid sending blocks that get dropped).
    """
    m = model.lower()
    if m.startswith("openrouter/"):
        return False
    return m.startswith("anthropic/") or "claude" in m


def is_openai_model(model: str) -> bool:
    """True if *model* routes to OpenAI proper (automatic caching + routing key).

    ``openai/…`` and bare ``gpt-…``/``o1``/``o3`` names. Copilot entries proxy
    OpenAI through ``api.githubcopilot.com`` and are handled as ``copilot/…`` by
    the caller's model string, so they are excluded here.
    """
    m = model.lower()
    if m.startswith("openrouter/") or m.startswith("copilot/"):
        return False
    return (
        m.startswith("openai/")
        or m.startswith("gpt-")
        or re.match(r"^o[0-9]", m) is not None
    )


def strip_sentinel(text: str) -> str:
    """Remove the cache-break sentinel (and its padding) from *text*."""
    if CACHE_BREAK not in text:
        return text
    return _SENTINEL_RE.sub("\n\n", text).strip()


def _split_on_sentinel(text: str) -> tuple[str, str]:
    """Split *text* into (stable, dynamic) at the first sentinel.

    Returns ``(text, "")`` when the sentinel is absent — the whole thing is
    treated as the stable prefix (safe: the whole system prompt is cacheable).
    """
    if CACHE_BREAK not in text:
        return text, ""
    stable, dynamic = text.split(CACHE_BREAK, 1)
    return stable.rstrip(), dynamic.lstrip()


def _mark_system_message_anthropic(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a system *msg* split into cached-stable + dynamic blocks.

    The content becomes a list of Anthropic-style text blocks; litellm carries
    the ``cache_control`` on the stable block through to the Anthropic API. If
    there is no dynamic remainder the whole system prompt is one cached block.
    """
    content = msg.get("content")
    # Only handle plain-string system content — if a caller already sent block
    # lists we leave them untouched (they own their own cache_control).
    if not isinstance(content, str) or not content.strip():
        return msg

    stable, dynamic = _split_on_sentinel(content)
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": stable, "cache_control": dict(_EPHEMERAL)}
    ]
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})

    out = dict(msg)
    out["content"] = blocks
    return out


def _mark_last_tool_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a shallow copy of *tools* with cache_control on the LAST tool.

    Marking the final tool caches the entire ``tools`` prefix (Anthropic caches
    up to and including the marked block). We copy only the last entry so the
    caller's list isn't mutated in place.
    """
    if not tools:
        return tools
    out = list(tools)
    last = dict(out[-1])
    # Prefer marking inside ``function`` (OpenAI tool shape) so litellm's
    # anthropic transform picks it up; fall back to top-level for bare tools.
    if isinstance(last.get("function"), dict):
        fn = dict(last["function"])
        fn["cache_control"] = dict(_EPHEMERAL)
        last["function"] = fn
    else:
        last["cache_control"] = dict(_EPHEMERAL)
    out[-1] = last
    return out


def apply_prompt_caching(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    cache_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, dict[str, Any]]:
    """Transform an OpenAI-shaped request for provider prompt caching.

    Args:
        model: resolved model string (e.g. ``anthropic/claude-sonnet-4-5``,
            ``deepseek/deepseek-chat``, ``openai/gpt-4o``).
        messages: OpenAI-format message list (mutated copy returned; input
            is not modified).
        tools: OpenAI-format tool list, or None.
        cache_key: optional routing key (agent name) → OpenAI ``prompt_cache_key``.
        extra: the kwargs dict passed to ``acompletion``; ``prompt_cache_key`` is
            added here for OpenAI. A fresh dict is returned (input not mutated).

    Returns:
        ``(messages, tools, extra)`` ready to hand to ``acompletion``. The
        sentinel is always removed from the outgoing system prompt.
    """
    out_extra = dict(extra or {})

    anthropic = is_anthropic_model(model)
    openai = is_openai_model(model)

    # ── System message ────────────────────────────────────────────────
    new_messages: list[dict[str, Any]] = []
    marked_system = False
    for m in messages:
        if (
            anthropic
            and not marked_system
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and CACHE_BREAK in m["content"]
        ):
            # Only the FIRST system block carries the stable prefix; split it.
            new_messages.append(_mark_system_message_anthropic(m))
            marked_system = True
        elif (
            anthropic
            and not marked_system
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ):
            # No sentinel but Anthropic — cache the whole (stable) system prompt.
            new_messages.append(_mark_system_message_anthropic(m))
            marked_system = True
        elif isinstance(m.get("content"), str) and CACHE_BREAK in m["content"]:
            # Non-Anthropic (or already-marked): strip the sentinel so it never
            # reaches the model.
            m2 = dict(m)
            m2["content"] = strip_sentinel(m["content"])
            new_messages.append(m2)
        else:
            new_messages.append(m)

    # ── Tools ─────────────────────────────────────────────────────────
    new_tools = tools
    if anthropic and tools:
        new_tools = _mark_last_tool_anthropic(tools)

    # ── OpenAI routing key ────────────────────────────────────────────
    if openai and cache_key and "prompt_cache_key" not in out_extra:
        out_extra["prompt_cache_key"] = cache_key

    return new_messages, new_tools, out_extra
