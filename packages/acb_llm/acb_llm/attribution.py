"""Stamp WHO and WHICH APP on every model call an agent makes. Usage slice 1.

Spec: ``ai_metering_and_analytics.md`` §5 · D66 · H-73 (the member proof).

🔴 **The Router has always read these headers, and nobody ever sent them.**
Measured on production, 2026-09-24, on the first real Projects chat after the
Router went live: five calls, each with ``agent`` filled in and ``member``,
``app`` and ``run`` all empty. The gateway reads ``X-CC-Member``,
``X-CC-Member-Proof``, ``X-CC-Module`` and ``X-CC-Run``, and forwards them to
the Console. The agents send only ``X-CC-Agent``, as a fixed header chosen
when the client is built.

A fixed header cannot carry the member, because one client serves every person
who chats with that agent. So the stamp happens PER REQUEST, from the run
context the executor bound for the run making the call. ``httpx`` runs a
request hook on the task that sends the request, so the hook sees exactly that
run's fields.

## One seam, five callers

The four app agents and the orchestrator each built their own
``OpenAIChatCompletionClient``. They now build it on
:func:`attributed_openai`, so a sixth agent cannot forget the member — it gets
the stamp by using the same constructor as its neighbours.

## What is signed, and what is only claimed

``X-CC-Member`` names the member for every report. ``X-CC-Member-Proof`` is
added only when the executor marked the member VERIFIED, which means the route
took it from the signed-in session. A cap may key on a proven member alone
(H-73), so the proof must never be minted for a name a request body chose.
"""
from __future__ import annotations

from typing import Any

import httpx

__all__ = ["attributed_copilot_provider", "attributed_openai", "attribution_headers"]

#: The headers the gateway's ``/v1`` reads. Named once, so the gateway and this
#: module cannot spell one of them two ways. ``X-CC-Source`` was the second
#: spelling of the app, and the gateway never read it.
MEMBER = "X-CC-Member"
MODULE = "X-CC-Module"
RUN = "X-CC-Run"
AGENT = "X-CC-Agent"


def _proof_for(member: str) -> str | None:
    """A signed proof for ``member``, or ``None`` when this box cannot sign.

    ⚠️ Imported lazily, as ``routed.py`` does. An agent script that never makes
    a routed call has no reason to load the auth package.
    """
    try:
        from acb_auth import member_proof
        from acb_common.settings import get_settings

        secret = str(getattr(get_settings(), "gateway_session_secret", "") or "")
        if not secret.strip():
            return None
        return member_proof.sign_member(member, secret)
    except Exception:
        return None


def attribution_headers(ctx: dict[str, str] | None = None) -> dict[str, str]:
    """The ``X-CC-*`` headers for the run bound on this task.

    Pass ``ctx`` to test a context directly. Leave it ``None`` in production,
    and the current run context is read.

    ⚠️ **An absent field is OMITTED, never sent empty.** The Console records an
    empty string as a member, which reads as an attribution somebody made
    rather than as the absence of one.

    ⚠️ **A member without an ``@`` is not a member.** An automation binds names
    like ``anonymous`` or a service id, and billing those as a person would
    put every automation's spend under one fake name on the usage page.
    """
    if ctx is None:
        try:
            from acb_common import get_run_context

            ctx = get_run_context()
        except Exception:
            ctx = {}

    out: dict[str, str] = {}
    member = str(ctx.get("user") or "").strip()
    if "@" in member:
        out[MEMBER] = member
        if ctx.get("member_verified") == "1":
            proof = _proof_for(member)
            if proof:
                from acb_auth.member_proof import MEMBER_PROOF_HEADER

                out[MEMBER_PROOF_HEADER] = proof
    app = str(ctx.get("app") or "").strip()
    if app:
        out[MODULE] = app
    run_id = str(ctx.get("run_id") or "").strip()
    if run_id:
        out[RUN] = run_id
    agent = str(ctx.get("agent") or "").strip()
    if agent:
        out[AGENT] = agent
    return out


def attributed_copilot_provider(provider: dict[str, Any] | None) -> dict[str, Any] | None:
    """A COPY of a Copilot SDK ``provider`` dict that carries this run's headers.

    The Copilot SDK path has no ``httpx`` client of ours to hook. Its CLI makes
    the model call, and it sends the ``headers`` of the session's
    ``ProviderConfig`` (SDK 1.0 and later, H-181). So the stamp happens when
    the session is created or resumed, inside the run that uses it.

    ⚠️ **Call it at session time, never when the agent is built.** One agent
    object serves every run, so a provider stamped at build time names the
    first person forever. That is a wrong bill, which is worse than a blank.

    ⚠️ **It returns a copy.** The agent's ``_default_options["provider"]`` is
    shared by every run of that agent. Writing into it would race.

    ⚠️ **The run context wins over a stale stamp.** Any ``X-CC-*`` header the
    input already carries is dropped first, so a provider that was stamped
    for one run cannot leak its member into the next run.
    """
    if not provider:
        return provider
    kept = {
        k: v for k, v in dict(provider.get("headers") or {}).items()
        if not str(k).lower().startswith("x-cc-")
    }
    return {**provider, "headers": {**kept, **attribution_headers()}}


async def _stamp(request: httpx.Request) -> None:
    """The request hook. Adds what is missing and never overwrites.

    ⚠️ **Never overwrites**, because a caller that set a header on purpose knows
    something the run context does not. The agents set ``X-CC-Agent`` to their
    own name, and a delegated sub-agent's run context may still carry its
    parent's.

    ⚠️ **Swallows its own failures.** Attribution that cannot be computed is a
    blank column on a report. Raising here would fail the model call itself.
    """
    try:
        for name, value in attribution_headers().items():
            if name not in request.headers:
                request.headers[name] = value
    except Exception:
        pass


def attributed_openai(
    *,
    base_url: str,
    api_key: str,
    default_headers: dict[str, str] | None = None,
) -> Any:
    """An ``AsyncOpenAI`` whose every request carries this run's attribution.

    Hand it to ``OpenAIChatCompletionClient(async_client=...)``.

    ⚠️ **``DefaultAsyncHttpxClient``, not a bare ``httpx.AsyncClient``.** The
    OpenAI SDK sets its own timeout and connection limits on the client it
    builds for itself. A bare client would silently replace them with
    httpx's defaults, which time out a long completion after five seconds.
    """
    import openai

    return openai.AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers=default_headers,
        http_client=openai.DefaultAsyncHttpxClient(event_hooks={"request": [_stamp]}),
    )
