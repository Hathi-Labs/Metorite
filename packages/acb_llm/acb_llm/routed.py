"""Serve an in-product completion through OUR Router, so it bills. H-171.

Spec: ``launch_surface.md`` §4.1 (*"AI usage is metered separately in
credits"*) · **D57.7** (a routed call that fails, FAILS) · CP-6 (the balance
gate) · ``customer_console.md`` §6B.

🔴 **The product's own AI was never metered, and that made it free for ever.**
Measured 2026-09-23: ``acb_llm`` held ZERO references to the Console.
``acompletion_with_fallback`` imported litellm and called it directly, so the
apps runtime, the email automation, the assistants and the agents — 29 files —
spent our vendor account and billed nobody. Turning ``ROUTER_SERVING_ENABLED``
on did not change it, because the Router was not on that path.

⚠️ **It was OBSERVED and not BILLED, and the difference is the whole ticket.**
``_emit_usage`` already logs the vendor cost in USD and publishes a live
activity with D1's four-tuple. What never happened was a ``usage_event`` row
or a credit drawn down. So this is not new observability. It is the missing
half of the money loop.

## What routing changes, stated plainly

* **The tier decides, not the model.** Callers already pass ``tier-fast`` and
  friends, so the tier goes through untouched and the Router's own
  ``tier_binding`` chain picks the model. The caller's ``fallback_model`` is
  therefore IGNORED on this path — the Router has a better chain, ranked by an
  operator, and running a second one here would be two opinions about failover.
* **The Console's verdicts arrive.** Out of credits is a 402, an unknown tier
  is a 400, the per-run breaker is a 403. Each is an ANSWER and not an outage.
* **D57.7: no fallback to local litellm.** A routed call that fails, fails. A
  quiet fall back to the direct path would restore the very hole this closes,
  and it would do it exactly when the Console is refusing to be paid.

## Attribution costs the call sites nothing

``get_run_context()`` already carries ``user``, ``agent`` and ``source`` for
in-run code, which is the property ``_emit_usage``'s docstring records: *"an
in-run caller passes none of them"*. The routed call reads the same context, so
none of the 29 sites changes to gain per-member and per-app billing.
"""
from __future__ import annotations

from typing import Any

from acb_common import get_logger

__all__ = ["RoutedRefusal", "completion_on_router", "routing_is_on"]

_log = get_logger("acb_llm.routed")

#: The OPTIONAL fields `customer_console.main.CompletionRequest` accepts.
#:
#: ⚠️ Named rather than derived, so widening it is an edit somebody
#: justifies. Deriving it from the Console would couple this package to that
#: service's imports, and `test_internal_ai_is_routed.py` checks the two agree
#: against the REAL model rather than against this list.
#:
#: 🔴 **`tools` IS here, and a first version wrongly said it was not.**
#: I read the model with a line-bounded grep, saw no `tools`, and concluded
#: tool-calling could not be routed. The field exists, along with
#: `tool_choice`, `parallel_tool_calls`, `reasoning_effort`, `seed` and
#: `thinking`. A test that asserts against the REAL model caught it.
#:
#: ⚠️ `model`, `messages`, `max_tokens` and `temperature` are set
#: explicitly above, and `stream` is deliberately absent — this path is the
#: non-streaming one, and sending `stream: true` here would ask the Console
#: for frames nothing reads.
_FORWARDABLE = (
    "task", "client_ref", "top_p", "n", "stop", "seed", "user",
    "presence_penalty", "frequency_penalty", "logit_bias", "response_format",
    "tools", "tool_choice", "parallel_tool_calls", "reasoning_effort",
    "thinking",
)


class RoutedRefusal(Exception):
    """The Router ANSWERED, and the answer was no.

    ⚠️ **Not an outage, and the distinction is load-bearing.** A 402 means the
    customer is out of credits, a 400 means the tier is unknown, a 403 is the
    per-run breaker. Flattening these into "the AI is down" would tell somebody
    to check the network when the real answer is that they must buy credits.

    Carries the Console's own status and detail so a caller can render the
    verdict rather than paraphrase it.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"router refused ({status}): {detail}")
        self.status = status
        self.detail = detail


def routing_is_on() -> bool:
    """Whether an in-product completion should go through the Console.

    BOTH halves, and neither implies the other. ``router_serving_enabled`` is
    the owner's decision that customers are billed for AI. ``router_is_wired``
    is whether this box can reach the Console at all. A box with the flag on
    and no address must NOT silently serve unbilled.

    ⚠️ Imported lazily. ``acb_llm`` is imported by agents and scripts that have
    no reason to load the auth package, and a module-level import here would
    put the Console client in every one of them.
    """
    try:
        from acb_auth.console_resolve import router_is_wired
        from acb_common.settings import get_settings
    except Exception:
        return False
    settings = get_settings()
    return bool(getattr(settings, "router_serving_enabled", False)) and router_is_wired()


def _attribution() -> dict[str, str | None]:
    """Who to bill and what to blame, from the ambient run context.

    ⚠️ ``user`` is the run context's name for the member, and ``source`` for
    the app. The two vocabularies meet here and nowhere else, so the mapping is
    written once rather than at 29 call sites.
    """
    try:
        from acb_common._log import get_run_context

        ctx = get_run_context()
    except Exception:
        ctx = {}
    return {
        "member": ctx.get("user") or None,
        "agent": ctx.get("agent") or None,
        "module_slug": ctx.get("source") or None,
        "run_id": ctx.get("run_id") or None,
    }


async def completion_on_router(
    *,
    tier: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    """One completion, served and BILLED by the Console. Returns ``(resp, model)``.

    Raises:
        RoutedRefusal: the Router answered and refused. A verdict, not an
            outage — see the class.
        ConsoleRouterUnavailable: no answer was produced. **D57.7 forbids
            falling back to local litellm here**, so this propagates.
    """
    from acb_auth.console_resolve import chat_completion_on_console

    attribution = _attribution()
    # An explicit `source` beats the inferred one: Custom Apps all run through
    # one module, so only the caller knows which app it is.
    if source:
        attribution["module_slug"] = source

    # 🔴 **Built against `CompletionRequest`, which is `extra="forbid"`.**
    # A first version sent `{"tier": ...}` and spread the caller's `**extra`.
    # Both are 422s: the field is called `model` and it carries the TIER ALIAS,
    # and an unknown key is refused rather than ignored. Nothing caught it,
    # because the test stubbed the client — a fake agrees with whatever it is
    # handed, which is exactly what R8 exists to say.
    #
    # ⚠️ `model` holds `tier-fast`, not a resolved model id. The Console reads
    # its own `tier_binding` chain, which an operator ordered and which fails
    # over. Resolving here would take that away.
    payload: dict[str, Any] = {
        "model": tier,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # ⚠️ **An allowlist, never a spread.** The caller's `extra` carries
    # litellm transport options — `timeout`, `cache`, `prompt_cache_key` — and
    # forwarding those to a `forbid` model refuses the whole call. Only the
    # keys `CompletionRequest` names may travel, and the rest stay local.
    for key in _FORWARDABLE:
        if extra and key in extra and extra[key] is not None:
            payload[key] = extra[key]

    status, body = await chat_completion_on_console(payload, **attribution)

    if status >= 400:
        detail = ""
        if isinstance(body, dict):
            raw = body.get("detail")
            detail = raw if isinstance(raw, str) else str(raw or "")
        _log.warning(
            "acb_llm.router_refused",
            router_tier=tier, router_status=status, router_detail=detail[:200],
        )
        raise RoutedRefusal(status, detail or "no reason given")

    # ⚠️ Rebuilt as the type the 29 call sites already hold. They read
    # `resp.choices[0].message.content`, and handing them a dict would turn a
    # working feature into an AttributeError at the far end of a refactor.
    from litellm import ModelResponse

    resp = ModelResponse(**body) if isinstance(body, dict) else body
    used = str(getattr(resp, "model", "") or body.get("model", tier))
    return resp, used
