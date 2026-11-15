"""Prompt-cache marks for each step of a chat chain (task 2875).

**Why the Router does this.** Anthropic caches a prompt only where the request marks it with
``cache_control``. The gateway marks it in ``acb_llm.prompt_cache.apply_prompt_caching``, but only on
the path that calls litellm itself. A call that the Router serves leaves the gateway BEFORE that
point (``v1_compat._handle_chat_completions`` forwards it from ``_router_should_serve``), and the
gateway cannot name the model, because the Router reads its own ``tier_binding`` chain and may fail
over from one vendor to another. So no Anthropic request that went through the Router ever carried a
mark. Measured on 2026-11-15 in ``usage_event``: 0 of 1,766 Claude requests since 2026-11-04 reported a
cached token, and the orchestrator's prompt averages 47,000 tokens. Only the Router knows the step
that really runs, so the marks go here, once per step, like ``reasoning.messages_for_step``.

**What it does, per step, when the flag is on.** For an Anthropic model it puts ``cache_control`` on
the stable part of the first system message (split at the ``CACHE_BREAK`` sentinel that the executor
writes between the persona and the member's memory block) and on the last tool, so the whole tool
array is cached. For every other model it takes the sentinel out, so the text never reaches the
model. A message that holds block lists, and a request with no system text, pass unchanged.

**The flag.** ``CUSTOMER_CONSOLE_PROMPT_CACHE``, default OFF, read at each call. With it off this
module returns its input untouched and the Router behaves as before. The flip is an owner-visible act
of the box (see ``docs/``), because cached tokens change what a request bills.

⚠️ This package imports no ``acb_*`` package (the Console is the other side of the tenant boundary),
so this is a COPY of the rule in ``acb_llm/prompt_cache.py``, as ``reasoning.py`` copies the null
content rule. ``tests/unit/test_customer_console_prompt_cache.py`` runs both on the same requests and
fails when they differ. Fence (R7): that file.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

_log = logging.getLogger("customer_console.prompt_cache")

#: The seam between the stable prefix and the dynamic suffix of the system prompt.
#: The same text as ``acb_llm.prompt_cache.CACHE_BREAK``. A test pins them equal.
CACHE_BREAK = "<!-- CACHE BREAK -->"

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}
_SENTINEL_RE = re.compile(r"\n*" + re.escape(CACHE_BREAK) + r"\n*")
_TRUE = frozenset({"1", "true", "yes", "on"})

#: One log line for each (model, outcome) in a process. Never the message text.
_LOGGED: set[tuple[str, str]] = set()
_LOGGED_MAX = 64


def prompt_cache_on() -> bool:
    """True when ``CUSTOMER_CONSOLE_PROMPT_CACHE`` is on. Read at each call, so a restart flips it."""
    return os.environ.get("CUSTOMER_CONSOLE_PROMPT_CACHE", "").strip().lower() in _TRUE


def is_anthropic_model(model: str) -> bool:
    """True if *model* is served by Anthropic, where an explicit ``cache_control`` applies.

    ``anthropic/...`` and any ``claude`` name, but not ``openrouter/...``: OpenRouter proxies Claude
    and does not honour our marks.
    """
    m = model.lower()
    if m.startswith("openrouter/"):
        return False
    return m.startswith("anthropic/") or "claude" in m


def _strip_sentinel(text: str) -> str:
    """*text* with the sentinel and its padding taken out."""
    if CACHE_BREAK not in text:
        return text
    return _SENTINEL_RE.sub("\n\n", text).strip()


def _mark_system(msg: dict[str, Any]) -> dict[str, Any]:
    """A copy of a system *msg* as a stable block with the mark, then the dynamic block."""
    content = msg["content"]
    if CACHE_BREAK in content:
        stable, dynamic = content.split(CACHE_BREAK, 1)
        stable, dynamic = stable.rstrip(), dynamic.lstrip()
    else:
        stable, dynamic = content, ""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": stable, "cache_control": dict(_EPHEMERAL)}]
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return {**msg, "content": blocks}


def _mark_last_tool(tools: list[Any]) -> list[Any]:
    """A copy of *tools* with the mark on the last one, which caches the whole array."""
    last = tools[-1]
    if not isinstance(last, dict):
        return tools
    marked = dict(last)
    if isinstance(marked.get("function"), dict):
        marked["function"] = {**marked["function"], "cache_control": dict(_EPHEMERAL)}
    else:
        marked["cache_control"] = dict(_EPHEMERAL)
    return [*tools[:-1], marked]


def _note(model: str, outcome: str) -> None:
    key = (model, outcome)
    if key in _LOGGED or len(_LOGGED) >= _LOGGED_MAX:
        return
    _LOGGED.add(key)
    _log.info("router.prompt_cache", extra={"pc_model": model, "pc_outcome": outcome})


def prompt_cache_for_step(kwargs: dict[str, Any], model: str) -> dict[str, Any]:
    """*kwargs* of a chat step, marked for the prompt cache of the step's *model*.

    Returns *kwargs* itself when the flag is off, and when nothing changes. Never raises: a request
    that this cannot read goes out as it came. The input is not changed.
    """
    if not prompt_cache_on():
        return kwargs
    try:
        return _apply(kwargs, model)
    except Exception:  # noqa: BLE001 - a cache mark must never fail a member's turn
        _log.warning("router.prompt_cache_failed", extra={"pc_model": model}, exc_info=True)
        return kwargs


def _apply(kwargs: dict[str, Any], model: str) -> dict[str, Any]:
    messages = kwargs.get("messages")
    if not isinstance(model, str) or not isinstance(messages, list):
        return kwargs
    anthropic = is_anthropic_model(model)
    out_messages: list[Any] = []
    marked = False
    changed = False
    for m in messages:
        text = m.get("content") if isinstance(m, dict) else None
        if not isinstance(m, dict) or not isinstance(text, str):
            out_messages.append(m)
            continue
        if anthropic and not marked and m.get("role") == "system" and text.strip():
            out_messages.append(_mark_system(m))
            marked = changed = True
        elif CACHE_BREAK in text:
            out_messages.append({**m, "content": _strip_sentinel(text)})
            changed = True
        else:
            out_messages.append(m)
    out = dict(kwargs)
    if changed:
        out["messages"] = out_messages
    tools = kwargs.get("tools")
    if anthropic and isinstance(tools, list) and tools:
        marked_tools = _mark_last_tool(tools)
        if marked_tools is not tools:
            out["tools"] = marked_tools
            changed = True
    if not changed:
        return kwargs
    _note(model, "marked" if anthropic else "stripped")
    return out
