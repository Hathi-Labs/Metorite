"""Structured logging via structlog. Call configure_logging() at process start.

Two output modes (``LOG_FORMAT`` env, or the ``json_logs`` arg):
  * ``console`` (default) — colored, human-readable; good for local dev.
  * ``json``            — one JSON object per line; machine-parseable so prod
                          logs (journald → ``journalctl -o cat``) can be
                          grepped/filtered by field and shipped to an aggregator.

Correlation: :func:`bind_run_context` binds ``run_id``/``thread_id``/``agent``/
``user``/``instance`` into structlog's contextvars so EVERY log line emitted
during a run carries them automatically — the thing that makes "show me all logs
for run X" possible (E2 observability). Bind at the run boundary, clear in
``finally``.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Iterator, Mapping
from contextvars import ContextVar

import structlog


def _want_json(json_logs: bool | None) -> bool:
    """Resolve the renderer: explicit arg wins, else ``LOG_FORMAT`` env.

    ``LOG_FORMAT=json`` (or ``1``/``true``) → JSON; anything else → console.
    """
    if json_logs is not None:
        return json_logs
    fmt = os.environ.get("LOG_FORMAT", "").strip().lower()
    return fmt in ("json", "1", "true", "yes")


def configure_logging(
    level: str = "INFO", *, json_logs: bool | None = None,
) -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    renderer: object = (
        structlog.processors.JSONRenderer()
        if _want_json(json_logs)
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# ── Run correlation (E2 observability) ───────────────────────────────────────

# The context keys bound per run. Kept small + stable so every log line during a
# run gains the same joinable fields (and so bind/clear stay symmetric).
# ``source`` is the originating app (chat / email / tasks / …) so the live
# activity feed can attribute an activation to the surface that triggered it.
# ``instance`` is the run's tenant partition, drawn from the SAME vocabulary
# the manifest, the blob store (migration 136) and the state directory use
# (``AgentManifest.instance_key``: ``''`` shared · ``u:<email>`` personal ·
# ``t:<team>`` team). It completes decision D1's attribution four-tuple
# (run_id, member, agent, instance) — WS-6a, specs/observability_e2.md §7.
# The value identifies the partition of the run THAT RESOLVED IT; because the
# context is inherited, a delegated sub-run that resolves none carries the
# caller's key while writing blobs under the shared partition (the delegation
# asymmetry recorded in §7 WS-6a). Do not treat the stamp as a join key onto
# ``agent_blob`` without checking which run emitted it.
# ⚠️ Twin tuple: ``activity._INHERIT`` must stay in sync — pinned by
# tests/unit/test_observability.py::test_inherit_and_run_context_keys_match.
_RUN_CONTEXT_KEYS = (
    "run_id", "thread_id", "agent", "user", "source", "instance",
    # 📌 Usage attribution. `app` is the product app the agent belongs to, as
    # its `config.json` declares it, so a bill can say which app spent the
    # credits. `source` says which SURFACE started the run (chat, a workflow),
    # and one app is reached from several surfaces, so the two are not the
    # same fact. `member_verified` is "1" only when `user` came from the
    # signed-in session and not from the request body. Only a verified member
    # may be SIGNED for the Router (H-73).
    "app", "member_verified",
)


def bind_run_context(
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    user: str | None = None,
    source: str | None = None,
    instance: str | None = None,
    app: str | None = None,
    member_verified: bool = False,
) -> None:
    """Bind run-correlation fields into structlog contextvars.

    After this, every ``get_logger(...).info(...)`` on the SAME asyncio task /
    thread context automatically includes the given fields — so you can filter
    all log lines for one agent run. Only non-empty values are bound. Pair with
    :func:`clear_run_context` in a ``finally`` at the run boundary.

    Additive by design: each call binds only the fields it is given, leaving
    already-bound ones alone. That is what lets the run boundary bind early
    (so failures *during* agent load are still correlated) and top up
    ``instance`` once the loaded config makes it resolvable — see
    ``orchestrator.executor._bind_run_instance``.

    The shared partition is ``''``, which is falsy, so a shared agent binds no
    ``instance`` key at all rather than an empty/quoted value — "absent" is the
    wire representation of shared, matching ``agent_blob.instance``.
    """
    fields = {
        k: v
        for k, v in (
            ("run_id", run_id),
            ("thread_id", thread_id),
            ("agent", agent),
            ("user", user),
            ("source", source),
            ("instance", instance),
            ("app", app),
        )
        if v
    }
    # 🔴 **The flag belongs to the USER bound beside it, and to no other.**
    # A first version bound it only when True, so binding a new user left a
    # parent's "1" in place. A nested run that rebinds `user` to a request
    # body's claim then inherited its parent's verification, and the stamp
    # signed an address the body chose (H-73). Found by review. So binding a
    # user now always settles the flag: set for a verified user, CLEARED for
    # any other. A call that binds no user leaves both alone, which is right
    # for a child acting for the same person as its parent.
    if user:
        if member_verified:
            # A string, because every other field is one and a log line that
            # prints `True` beside `"chat"` is a second vocabulary for a flag.
            fields["member_verified"] = "1"
        else:
            structlog.contextvars.unbind_contextvars("member_verified")
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


@contextlib.contextmanager
def run_context_scope() -> Iterator[None]:
    """Snapshot this task's run fields, and put them back EXACTLY on exit.

    🔴 **Not :func:`clear_run_context`, and a nested run is why.** A run can
    start inside another run: a sub-agent dispatch awaits ``run_agent`` on its
    parent's task. Clearing on the way out would wipe the PARENT's member and
    app, so every model call the parent made after the child finished would
    bill nobody. Restoring the snapshot leaves the parent exactly as it was.
    """
    before = {
        k: v
        for k, v in structlog.contextvars.get_contextvars().items()
        if k in _RUN_CONTEXT_KEYS
    }
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*_RUN_CONTEXT_KEYS)
        if before:
            structlog.contextvars.bind_contextvars(**before)


# ── The name of a background model call (AI-call attribution, 2026-10-10) ────

#: The shape of the agent name that a background model call carries:
#: ``<app>.<feature>``, for example ``email.rule_match``. One dot, and two
#: lower-case words. Spec: ``customer_console.md`` §4.3a.
AUTOMATION_AGENT_RE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")

#: The automation name that :func:`automation_agent_scope` bound on this task,
#: or None. It is how the two readers tell OUR name from a chat agent's name.
#: ⚠️ A ContextVar, not a run-context key, so it never reaches a log line and
#: no request can set it.
_AUTOMATION_AGENT: ContextVar[str | None] = ContextVar("acb_automation_agent", default=None)


def _bound_agent() -> str:
    return str(structlog.contextvars.get_contextvars().get("agent") or "").strip()


@contextlib.contextmanager
def automation_agent_scope(name: str | None) -> Iterator[None]:
    """Name the model calls in this block ``name``, unless a chat agent runs.

    🔴 **97% of the email AI calls reached the Router with no agent.**
    Measured on production, 2026-10-10: about 8,500 email calls in 7 days with
    ``agent`` NULL, so the Operator dashboard showed them as "not attributed".
    A background job binds a member and an app, and no agent at all.

    - ``name`` is a CODE CONSTANT of the shape :data:`AUTOMATION_AGENT_RE`.
      Member text never reaches it (R5). A name of another shape binds
      nothing and logs ``attribution.agent_name_refused``.
    - **A chat agent wins.** When the task holds an agent that this function
      did not bind, such as ``email-assistant``, the block keeps it.
    - **A narrower automation name wins over a wider one.** Inside a job that
      bound ``email.automation``, a call that binds ``email.rule_match``
      replaces it for that call alone.
    - Scoped like :func:`run_context_scope`, so the agent before the block
      comes back exactly on exit.

    Fences: ``tests/unit/test_usage_attribution.py`` (``TestBackgroundAI*``).
    """
    agent = str(name or "").strip()
    if agent and not AUTOMATION_AGENT_RE.fullmatch(agent):
        structlog.get_logger("acb_common.attribution").warning(
            "attribution.agent_name_refused", name=agent[:64])
    bound = _bound_agent()
    held = _AUTOMATION_AGENT.get()
    if not AUTOMATION_AGENT_RE.fullmatch(agent) or (bound and bound != held):
        yield
        return
    with run_context_scope():
        token = _AUTOMATION_AGENT.set(agent)
        try:
            structlog.contextvars.bind_contextvars(agent=agent)
            yield
        finally:
            try:
                _AUTOMATION_AGENT.reset(token)
            except ValueError:  # a token from another context
                _AUTOMATION_AGENT.set(held)


def chat_agent_label(name: str | None) -> str:
    """The name a CHAT agent reports. The ONE rule against a claimed name.

    An agent name may hold a dot (``agent_paths.AGENT_NAME_RE``, and a MAF
    manifest slug), so a member could name an agent ``email.rule_match``. A
    name of the shape :data:`AUTOMATION_AGENT_RE` comes back as
    ``agent:<name>``, the grant grammar's spelling of an agent. Every other
    name comes back unchanged, so ``email-assistant`` stays as it is. It is
    idempotent, because ``agent:`` holds a colon.

    Callers: :func:`attributed_agent`, ``acb_llm.attribution.attributed_openai``
    (the fixed ``X-CC-Agent`` of every MAF client), ``acb_skills.system_one``
    and ``acb_skills.decide_tools``.
    """
    label = str(name or "").strip()
    if AUTOMATION_AGENT_RE.fullmatch(label):
        return f"agent:{label}"
    return label


def attributed_agent(ctx: Mapping[str, str], module: str | None) -> str | None:
    """The agent name that a model call reports. The ONE rule for both readers.

    ``acb_llm.routed.run_attribution`` (the in-process Router and ``decide``)
    and ``acb_llm.attribution.attribution_headers`` (the HTTP agents) both
    call it, so the two paths cannot name one call two ways.

    1. A bound agent is reported as it is.
    2. 🔴 **A chat agent cannot claim an automation name.** An agent name may
       hold a dot (``agent_paths.AGENT_NAME_RE``), so a member could name an
       agent ``email.rule_match``. A bound name of that shape that
       :func:`automation_agent_scope` did not bind goes through
       :func:`chat_agent_label`, and so reports ``agent:<name>``.
    3. **The backstop.** With no agent bound and a module known, the call
       reports ``<module>.automation``. With no module it reports None. It
       never invents an app.
    """
    bound = str(ctx.get("agent") or "").strip()
    if bound:
        if bound == _AUTOMATION_AGENT.get():
            return bound
        return chat_agent_label(bound)
    app = str(module or "").strip()
    return f"{app}.automation" if app else None


@contextlib.contextmanager
def job_member_scope(
    owner: str | None, *, app: str | None = None, agent: str | None = None,
) -> Iterator[None]:
    """Run a BACKGROUND job as the member it belongs to. H-152.

    🔴 **A job with no session reached the Router with no member.** Under the
    per-box deployment key the Console derives the organization from
    ``X-CC-Member``, and a call without one is a 400. A schedule, a sync loop
    or a webhook has no signed-in person, so the job binds the member who OWNS
    the thing it works on: the mailbox, the workflow, the WhatsApp account.

    ⚠️ **``owner`` must come from OUR tables, never from a request body.**
    The member decides which organization pays, so it is bound VERIFIED
    (H-73) and the Router may cap on it. A caller that holds only a claim must
    use :func:`bind_run_context` without ``member_verified``.

    ⚠️ **An inherited member is DROPPED first.** A sync loop started inside a
    request copies that request's context for its whole life, so without this
    every mailbox would bill whoever last pressed Save. An owner that is not
    an address (``None``, ``anonymous``) therefore leaves the job memberless,
    never billed to a bystander.

    ``agent`` names the job's model calls, as ``email.automation``
    (AI-call attribution, 2026-10-10). It goes through
    :func:`automation_agent_scope`, so a chat agent the task already holds
    keeps its name, and a call inside may name a narrower feature.

    Scoped like :func:`run_context_scope`, so the caller's fields come back
    exactly on exit.
    """
    with run_context_scope():
        structlog.contextvars.unbind_contextvars("user", "member_verified")
        member = str(owner or "").strip()
        if "@" in member:
            bind_run_context(user=member, app=app, member_verified=True)
        elif app:
            bind_run_context(app=app)
        with automation_agent_scope(agent):
            yield


def clear_run_context() -> None:
    """Unbind the run-correlation fields bound by :func:`bind_run_context`."""
    structlog.contextvars.unbind_contextvars(*_RUN_CONTEXT_KEYS)


def get_run_context() -> dict[str, str]:
    """Return the currently-bound run-correlation fields (for attribution).

    Lets non-run code (e.g. the LLM client's usage emitter) tag its records
    with the active run without threading ids through every call signature.
    """
    ctx = structlog.contextvars.get_contextvars()
    return {k: ctx[k] for k in _RUN_CONTEXT_KEYS if ctx.get(k)}
