"""Copilot-SDK session tuning: permission handler + infinite-session policy.

Extracted from ``executor.py`` (foundation maintainability refactor) — no
behaviour change (log event strings preserved). These configure a GitHub
Copilot-SDK agent's per-run session:

* :func:`_copilot_permission_handler` selects the SDK permission handler
  (risk-aware vs blanket approve_all) per ``AGENT_PERMISSION_MODE``.
* :func:`_copilot_infinite_session_config` / :func:`_apply_copilot_infinite_sessions`
  neutralise the Copilot backend's context-window auto-compaction, which
  false-trips "context length exceeded" on BYOK models whose real window the
  backend can't learn.
"""
from __future__ import annotations

import inspect
import os
from typing import Any

from acb_common import get_logger
from acb_llm.attribution import attributed_copilot_provider

_log = get_logger("orchestrator.copilot_session")


def _refuse_every_request(request: Any, invocation: Any) -> Any:
    """The handler when the permission policy cannot load: refuse it all (D85).

    The old fallback was a bare ``approve_all``, which waived the D85 shell
    guard together with B6. A run that cannot load its policy has no safe
    default, so it may do nothing that needs a permission.
    """
    del request, invocation
    from copilot.generated.rpc import PermissionDecisionReject
    return PermissionDecisionReject(
        feedback=(
            "Blocked by Metorite: the permission policy did not load, so this "
            "run may do nothing that needs a permission."
        ),
    )


def _copilot_permission_handler(existing: Any = None) -> Any:
    """Return the Copilot-SDK permission handler for a run (B6 / HH-6).

    With no *existing* handler: ``AGENT_PERMISSION_MODE=approve_all`` → the
    SDK's blanket ``approve_all`` (escape hatch / old behaviour). Otherwise →
    our risk-aware handler, which blocks dangerous shell + out-of-workspace
    writes, defers destructive tools to their own request_confirmation gate,
    and logs every privileged op.

    *existing* is a handler that the agent's own factory set (its
    ``on_permission_request``). It stays the agent's handler, and B6 does not
    replace it (H-211 owns that).

    D85: whichever handler it picks, it returns it inside
    ``permission_policy.guard_shared_agent_shell``. So a shared agent's
    Copilot CLI shell is refused in every mode, and whatever handler its
    factory set, until the sandbox covers it. If the policy module cannot
    load, it returns :func:`_refuse_every_request`, never a bare
    ``approve_all``. Call :func:`_install_copilot_permission_handler`, which
    is the one way a Copilot agent gets its handler.
    """
    from copilot import PermissionHandler as _PH  # noqa: PLC0415

    try:
        from acb_skills.permission_policy import (  # noqa: PLC0415
            guard_shared_agent_shell,
            risk_aware_permission_handler,
        )
    except Exception:  # noqa: BLE001
        _log.error("copilot.permission_policy_unavailable")
        return _refuse_every_request
    if existing is not None:
        return guard_shared_agent_shell(existing)
    if os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower() == (
        "approve_all"
    ):
        return guard_shared_agent_shell(_PH.approve_all)
    return guard_shared_agent_shell(risk_aware_permission_handler)


def _install_copilot_permission_handler(agent: Any) -> None:
    """Give a Copilot agent its permission handler, always with the D85 guard.

    THE one way every Copilot path sets ``_permission_handler``: the run, the
    sub-agent, the Tier 1.5 and Tier 2 runs, the batch helper and
    ``code_session``. It ALWAYS wraps what is there. A slot that is empty
    gets the B6 handler. A slot that the agent's factory filled (any
    ``on_permission_request``, ``approve_all`` included) keeps that handler,
    inside the guard. An agent with no ``_permission_handler`` slot is not a
    Copilot agent, and it is left alone. Calling it twice is a no-op, because
    the guard does not wrap itself.
    """
    if not hasattr(agent, "_permission_handler"):
        return
    try:
        agent._permission_handler = _copilot_permission_handler(
            agent._permission_handler,
        )
    except Exception as exc:  # a frozen agent is logged, not hidden
        _log.error(
            "copilot.permission_handler_install_failed",
            agent=getattr(agent, "name", type(agent).__name__),
            error=str(exc)[:200],
        )


def _copilot_infinite_session_config() -> dict[str, Any] | None:
    """Return the ``infinite_sessions`` SessionConfig block for Copilot-SDK runs.

    The Copilot backend runs its own "infinite session" auto-compaction, keyed to
    *the model's context window* — it starts background compaction at
    ``background_compaction_threshold`` (default 0.80) and HARD-BLOCKS the turn at
    ``buffer_exhaustion_threshold`` (default 0.95) of that window. For our BYOK
    models (e.g. DeepSeek-V4-Pro, real 1M context) the backend does NOT know the
    true window — its ``client.list_models()`` talks to api.githubcopilot.com, which
    has no entry for our gateway-routed model — so it falls back to a small default
    window and its 0.95 guard trips a false "context length exceeded" on short,
    tool-heavy runs (diagnosed on technical-project-planner run 5b8c5836, 2026-07-03).

    Since the backend can't be told the real window for a BYOK model, we relax the
    guards so it stops prematurely blocking. Our own gateway-side context assembly
    (acb_llm.assemble_run_context / C2) already bounds the prompt, and the real
    DeepSeek API honours its 1M window — so the Copilot backend's guess should not
    be the thing that fails the run.

    Env overrides (all optional):
      - ``COPILOT_INFINITE_SESSIONS=off``  → disable the backend compaction entirely
        (``enabled: false``) — the strongest "stop guessing my window" setting.
      - ``COPILOT_COMPACTION_THRESHOLD``   → background_compaction_threshold (float).
      - ``COPILOT_BUFFER_THRESHOLD``       → buffer_exhaustion_threshold (float).
    Returns ``None`` when the operator has explicitly opted out of any override
    (``COPILOT_INFINITE_SESSIONS=default``), leaving the SDK's own defaults intact.
    """
    mode = os.environ.get("COPILOT_INFINITE_SESSIONS", "").strip().lower()
    if mode == "default":
        return None  # leave SDK defaults untouched (escape hatch)
    if mode == "off":
        return {"enabled": False}

    def _f(name: str, fallback: float) -> float:
        raw = os.environ.get(name, "").strip()
        try:
            v = float(raw) if raw else fallback
        except ValueError:
            v = fallback
        # Keep in the valid (0, 1] band the backend expects.
        return min(max(v, 0.01), 1.0)

    # Relaxed defaults: don't background-compact until nearly full, and don't
    # hard-block until the window is genuinely exhausted. This neutralises the
    # premature 0.80/0.95 trip on a wrongly-small assumed window without turning
    # compaction fully off (a genuinely huge run can still be managed).
    return {
        "enabled": True,
        "background_compaction_threshold": _f("COPILOT_COMPACTION_THRESHOLD", 0.92),
        "buffer_exhaustion_threshold": _f("COPILOT_BUFFER_THRESHOLD", 0.99),
    }


def effective_infinite_sessions(
    default_options: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The ``infinite_sessions`` block for a session config (or None to omit).

    THE single place the policy is decided, so every path that builds a Copilot
    SessionConfig applies the same rule:

    - BYOK agent (``provider`` in ``default_options``) → ``{"enabled": False}``.
      The Copilot backend cannot learn a BYOK model's real window (its
      ``list_models()`` queries api.githubcopilot.com, which has no entry for our
      gateway-routed model), so it falls back to ~90K and any threshold x 90K is
      useless against a 1M-window model. Disabling is the only correct policy —
      the wire protocol exposes no absolute-window field to correct it with.
    - Copilot-native agent → relaxed thresholds (the backend knows the real
      window, so its compaction is meaningful).
    - ``COPILOT_INFINITE_SESSIONS=default`` → ``None`` (leave SDK defaults).

    Must be evaluated at session-build time, not at import/wrap time: BYOK
    detection sets ``_default_options["provider"]`` late in the run setup.
    """
    mode = os.environ.get("COPILOT_INFINITE_SESSIONS", "").strip().lower()
    if mode == "default":
        return None  # operator opted out — leave the SDK's own defaults intact
    is_byok = bool((default_options or {}).get("provider"))
    if mode == "off" or is_byok:
        return {"enabled": False}
    return _copilot_infinite_session_config() or {"enabled": False}


_SDK_SESSION_PARAMS: frozenset[str] | None = None


def _sdk_session_params() -> frozenset[str]:
    """The keyword names ``CopilotClient.create_session`` / ``resume_session`` take.

    SDK 1.0 turned the one ``config`` dict into keyword arguments, so a key the
    SDK does not know is now a ``TypeError`` instead of a silently dropped
    field (H-181). Read once from the installed SDK, so an upgrade that adds a
    parameter needs no edit here.
    """
    global _SDK_SESSION_PARAMS
    if _SDK_SESSION_PARAMS is None:
        from copilot import CopilotClient

        names: set[str] = set()
        for fn in (CopilotClient.create_session, CopilotClient.resume_session):
            names.update(
                p.name for p in inspect.signature(fn).parameters.values()
                if p.kind is inspect.Parameter.KEYWORD_ONLY
            )
        _SDK_SESSION_PARAMS = frozenset(names)
    return _SDK_SESSION_PARAMS


def session_kwargs_for_this_run(config: dict[str, Any]) -> dict[str, Any]:
    """Turn a session ``config`` into the SDK's keyword arguments, for THIS run.

    The one place every Copilot session is shaped before the SDK sees it:

    - **Attribution (H-181).** ``provider`` becomes a copy that carries the
      ``X-CC-*`` headers of the run bound on this task
      (:func:`acb_llm.attribution.attributed_copilot_provider`). It runs when
      the session is created or resumed, and every run creates or resumes its
      session, so the headers are always the run's own.
    - **Unknown keys are dropped.** ``max_tokens`` (never on the wire),
      ``thinking`` and ``model_params`` (the MAF think-mode keys) ride on
      ``_default_options`` and would raise ``TypeError`` in SDK 1.0.
    """
    allowed = _sdk_session_params()
    out = {k: v for k, v in config.items() if k in allowed}
    dropped = sorted(set(config) - set(out))
    if dropped:
        _log.debug("copilot.session_keys_dropped", keys=dropped)
    if out.get("provider"):
        out["provider"] = attributed_copilot_provider(out["provider"])
    return out


def _apply_copilot_infinite_sessions(agent: Any) -> bool:
    """Inject ``infinite_sessions`` into a Copilot agent's SessionConfig.

    The agent-framework wrapper's ``_create_session`` builds SessionConfig from a
    FIXED set of keys (model/system_message/tools/permission/mcp) and drops
    ``infinite_sessions`` — even though the underlying ``client.create_session``
    honours it. So setting it on ``agent._default_options`` alone is not enough; we
    wrap ``_create_session`` to merge our block into the config it produces.

    The effective config is computed at CALL TIME (inside ``_wrapped``), not at wrap
    time.  This matters because BYOK provider detection sets
    ``agent._default_options["provider"]`` AFTER ``_inject_agent_tools`` (which calls
    this function) but BEFORE ``agent.run()`` (which calls ``_create_session``).  By
    detecting BYOK at call time we can always apply the correct policy:

    - BYOK agent (``provider`` set in ``_default_options``): ``{enabled: False}`` —
      the Copilot backend cannot learn the real context window for a BYOK model (its
      ``list_models()`` queries ``api.githubcopilot.com``, which has no entry for our
      gateway-routed model), so it falls back to ~90K and any threshold × 90K is
      useless against a 1M-window model. Disabling is the only correct policy.
    - Copilot-native agent: relaxed thresholds from ``_copilot_infinite_session_config``
      (backend knows the real window and its compaction is meaningful).

    Idempotent (guards ``__cc_inf_sessions__``); best-effort (never raises). Returns
    True if the wrap was applied.

    ⚠️ **The wrap is installed even under ``COPILOT_INFINITE_SESSIONS=default``
    (H-181).** It is now also where the batch run and a delegated sub-agent get
    their attribution headers and lose the keys SDK 1.0 refuses. The opt-out
    still leaves the SDK's compaction defaults alone: no block is injected.
    """
    orig = getattr(agent, "_create_session", None)
    if not callable(orig) or getattr(agent, "__cc_inf_sessions__", False):
        return False

    import functools  # noqa: PLC0415

    @functools.wraps(orig)
    async def _wrapped(streaming: bool, runtime_options: Any = None) -> Any:
        # ``_create_session`` builds SessionConfig and calls
        # ``client.create_session`` in one shot, dropping infinite_sessions on the
        # way. We can't edit the config it produces, so we intercept at the client:
        # swap in a create_session that merges our block, run the original, restore.
        #
        # Compute effective config NOW — provider may have been set after the wrap.
        # None means the operator opted out: inject no block, but still shape
        # the call (attribution + unknown keys) below.
        effective_cfg = effective_infinite_sessions(
            getattr(agent, "_default_options", {}) or {}
        )

        client = getattr(agent, "_client", None)
        orig_client_create = getattr(client, "create_session", None) if client else None
        if not callable(orig_client_create):
            return await orig(streaming, runtime_options)

        # SDK 1.0 (H-181): the wrapper calls ``create_session(**kwargs)``, so
        # the interceptor takes keywords. It is also the attribution choke
        # point for the paths that keep the wrapper's own ``_create_session``
        # (the batch run and a delegated sub-agent).
        async def _client_create(**kwargs: Any) -> Any:
            if effective_cfg is not None and kwargs.get("infinite_sessions") is None:
                kwargs["infinite_sessions"] = effective_cfg
            return await orig_client_create(**session_kwargs_for_this_run(kwargs))

        try:
            client.create_session = _client_create  # type: ignore[attr-defined]
            return await orig(streaming, runtime_options)
        finally:
            client.create_session = orig_client_create  # type: ignore[attr-defined]

    try:
        agent._create_session = _wrapped  # type: ignore[attr-defined]
        agent.__cc_inf_sessions__ = True  # type: ignore[attr-defined]
        _log.info("executor.copilot_infinite_sessions_applied", byok_aware=True)
        return True
    except Exception:  # noqa: BLE001
        return False
