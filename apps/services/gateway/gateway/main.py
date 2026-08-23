"""FastAPI entry point. Run with: uv run uvicorn gateway.main:app --reload"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from acb_auth import (UserContext, UserRole, get_current_user,
                      require_authenticated, require_role)
from acb_common import configure_logging, get_logger, get_settings
from acb_common.db import clear_tenant, release_tenant
from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_log = get_logger("gateway")

# ── Pre-import heavy modules before event loop starts ──────────────────────
# SQLAlchemy / psycopg deadlocks when imported for the first time inside a
# running asyncio event loop.  Importing here (module level, before uvicorn
# starts the loop) avoids the deadlock entirely.
try:
    from orchestrator.agents import build_orchestrator_agent as _build_orchestrator_agent
    _HAS_MAF = True
except ImportError:
    _HAS_MAF = False
    _build_orchestrator_agent = None


# ── Launch-defang kill-switches (WS-29) ────────────────────────────────────
# Several always-on background loops are OUT of launch scope (Tasks, Calendar,
# Projects, User-management + agent chat) and not yet tenant-bound, so they
# would write UNBOUND and error under FORCE ROW LEVEL SECURITY after the RLS
# cutover. Rather than bind them now (deferred: H4 slices 6b/6c), each loop's
# STARTUP is gated behind a default-ON env flag so the cutover runbook can set
# it false and the loop simply never starts. Default ON = byte-identical to
# today (dark). Binding the gate at the call site — not inside the start
# function — keeps the guard testable by monkeypatching the start functions.
def _flag_default_on(name: str) -> bool:
    """Read a default-ON kill-switch env flag.

    OFF only when the value is an explicit recognised falsey token; unset, empty
    or unrecognised = ON, so today's behaviour is preserved until the RLS-cutover
    runbook sets it false (WS-29 launch defang). Inverse of the default-OFF idiom
    in ``acb_graph.db.tenant_bind_enabled`` / ``ingestion.consumer.consumer_enabled``
    (same token vocabulary).
    """
    return os.getenv(name, "").strip().lower() not in {"0", "false", "no", "off"}


def _email_sync_enabled() -> bool:
    """``EMAIL_SYNC_ENABLED`` (default ON) — gates the background email-sync loop."""
    return _flag_default_on("EMAIL_SYNC_ENABLED")


def _workflow_scheduler_enabled() -> bool:
    """``WORKFLOW_SCHEDULER_ENABLED`` (default ON) — gates the workflow schedule
    scanner AND its sibling orphan-run reconciler (one scheduling subsystem)."""
    return _flag_default_on("WORKFLOW_SCHEDULER_ENABLED")


async def _maybe_start_email_sync() -> None:
    """Start the background email-sync loop unless ``EMAIL_SYNC_ENABLED`` is off.

    Default ON = byte-identical to before. The RLS-cutover runbook sets it false
    so the always-on ``_account_sync_loop`` — out of launch scope and not yet
    tenant-bound (H4 slice 6b) — does not start and write unbound under FORCE ROW
    LEVEL SECURITY (WS-29 launch defang). Registering the post-sync hooks is the
    caller's job and stays UNCONDITIONAL: manual sync and the Graph webhook share
    them (``routes/email/scheduler_hooks.py::process_new_mail``).
    """
    if not _email_sync_enabled():
        _log.info("gateway.email_sync_disabled", flag="EMAIL_SYNC_ENABLED")
        return
    from email_ingestion.scheduler import start_background_sync
    await start_background_sync()
    _log.info("gateway.email_sync_started")


async def _maybe_start_workflow_scheduler() -> None:
    """Start the workflow orphan-run reconcile sweep AND the cron schedule
    scanner, unless ``WORKFLOW_SCHEDULER_ENABLED`` is off.

    Both belong to the workflow scheduling subsystem, so one flag gates both.
    Default ON = byte-identical. The RLS-cutover runbook sets it false so neither
    the reconcile sweep nor the cron scanner — out of launch scope and not yet
    tenant-bound (H4 slice 6c) — writes unbound under FORCE ROW LEVEL SECURITY
    (WS-29 launch defang). Each half keeps its own error isolation, exactly as
    the two lifespan blocks it replaces did.
    """
    if not _workflow_scheduler_enabled():
        _log.info(
            "gateway.workflow_scheduler_disabled",
            flag="WORKFLOW_SCHEDULER_ENABLED",
        )
        return
    # Workflow runs are in-process asyncio tasks (BO-20 pending): rows still
    # 'running' from a previous process can never finish — mark them failed
    # BEFORE the scheduler can start new runs. Paused runs survive restarts
    # (resume rebuilds from the pause snapshot) and are left alone.
    try:
        from gateway.routes.workflows import reconcile_orphaned_runs
        await reconcile_orphaned_runs()
    except Exception as exc:
        _log.warning("gateway.workflow_reconcile_skipped", error=str(exc))
    # Start the workflow schedule scanner — cron triggers for published
    # workflows (routes/workflows/scheduler.py; spec workflows_app.md F8).
    try:
        from gateway.routes.workflows import start_workflow_scheduler
        await start_workflow_scheduler()
        _log.info("gateway.workflow_scheduler_started")
    except Exception as exc:
        _log.warning("gateway.workflow_scheduler_skipped", error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Force UTF-8 for all child processes spawned by the gateway (scripts, git, etc.).
    # On Windows the default is cp1252 which breaks any script that prints emoji or
    # non-ASCII characters (e.g. zoho_crm.py's pipeline summary headers).
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # Expose the gateway's venv to every child process (the Copilot CLI, agent
    # shells, install_dependency).  `uv pip install` needs a target venv; the
    # service env often lacks VIRTUAL_ENV, so a bare `uv pip install` from an
    # agent would have nowhere to install.  Point VIRTUAL_ENV at this venv and
    # put its bin first on PATH so runtime dependency installs land here and are
    # importable in-process.
    try:
        import sys as _sys
        from pathlib import Path as _Path
        # sys.prefix IS the venv root in a venv — do NOT derive it from
        # sys.executable, whose bin/python is often a symlink to the system
        # python (resolving it lands on /usr and misses the venv).
        _venv = _Path(_sys.prefix)
        if (_venv / "pyvenv.cfg").is_file():
            os.environ.setdefault("VIRTUAL_ENV", str(_venv))
            _bin = str(_venv / "bin")
            if _bin not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info("gateway.startup", env=settings.acb_env)

    # Ownership bootstrap: if NO member holds `owner`, provision the first
    # EXECUTIVE_EMAILS address (creating its app_user row). Startup is the
    # first place the database AND the environment are both readable —
    # migration 128's SQL bootstrap can only promote rows that already exist,
    # which is how the 2026-07-30 lockout happened (empty app_user → zero
    # members → an invite-only model with no inviter). No-op whenever any
    # owner exists; never blocks startup.
    try:
        from acb_auth import ensure_owner_bootstrap  # noqa: PLC0415

        await ensure_owner_bootstrap()
    except Exception as exc:  # noqa: BLE001
        _log.warning("gateway.owner_bootstrap_skipped", error=str(exc)[:200])

    if _HAS_MAF:
        _log.info("gateway.ag_ui_registered", path="/copilot/chat")

    # Tier 1.5 runtime self-check (M2.6). Surfaces a broken github-copilot
    # sandbox at startup instead of failing silently on the first agent run.
    try:
        checks = _runtime_checks()
        ok = all(c["ok"] for c in checks.values())
        _log.info("gateway.runtime_check", ok=ok, **{k: v["ok"] for k, v in checks.items()})
        for name, c in checks.items():
            if not c["ok"]:
                _log.warning("gateway.runtime_degraded", check=name, detail=c["detail"])
    except Exception as exc:  # pragma: no cover
        _log.warning("gateway.runtime_check_failed", error=str(exc))

    # Pre-warm the Copilot model list cache so /api/models/all returns instantly.
    async def _warmup_copilot_models() -> None:
        try:
            _gh = getattr(settings, "github_token", "") or os.environ.get("GITHUB_TOKEN", "")
            if not _gh:
                return
            os.environ.setdefault("GITHUB_TOKEN", _gh)
            import time as _t

            from copilot import CopilotClient as _CC
            _c = _CC(options={"github_token": _gh}); await _c.start()
            try:
                _m = await _c.list_models()
            finally:
                await _c.stop()
            if _m:
                _copilot_models_cache["data"] = {
                    "models": [{"id": x.id, "label": x.name, "model_picker_enabled": True}
                               for x in _m if not x.policy or x.policy.state == "enabled"],
                    "source": "live",
                }
                _copilot_models_cache["ts"] = _t.monotonic()
                _log.info("gateway.copilot_models_cache_warmed", count=len(_m))
        except Exception as _e:
            _log.warning("gateway.copilot_models_warmup_failed", error=str(_e))

    import asyncio as _asyncio
    _asyncio.ensure_future(_warmup_copilot_models())

    # Sweep any `cc-copilot-*` sandbox containers (BO-7 phase 2 —
    # copilot_sandbox.py) left running by a prior crashed gateway process.
    # Cheap no-op when copilot_sandbox_scope has never been enabled (docker ps
    # finds nothing) or Docker itself isn't available (best-effort, never raises).
    async def _sweep_copilot_sandboxes() -> None:
        try:
            from orchestrator.copilot_sandbox import sweep_orphaned_sandboxes
            await sweep_orphaned_sandboxes()
        except Exception as _e:
            _log.warning("gateway.copilot_sandbox_sweep_failed", error=str(_e))

    _asyncio.ensure_future(_sweep_copilot_sandboxes())

    # Warm-clone every live agent that has a source (GitHub repo or local path)
    # but no clone on disk yet.  Clones are created lazily on first run, so a
    # reboot/deploy that wiped the cache leaves registered agents invisible in
    # the Files/Artifacts viewers until they happen to run again.  This restores
    # them on startup so their workspace is browsable without a manual pull.
    async def _warm_clone_agents() -> None:
        try:
            from acb_skills.loader import _install_agent_deps, load_agent

            from gateway.routes.agent import _AGENT_REGISTRY, _load_dynamic_agents
            from gateway.routes.workspace import _agent_workspace_dir

            entries = list(_AGENT_REGISTRY)
            try:
                entries = _load_dynamic_agents() + entries
            except Exception:
                pass

            seen: set[str] = set()
            for entry in entries:
                name = entry.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                if entry.get("status", "live") != "live":
                    continue
                repo_name = entry.get("repo_name")
                local_path = entry.get("local_path")
                # Only agents we know how to fetch; skip if already on disk.
                if not repo_name and not local_path:
                    continue
                try:
                    _ws = _agent_workspace_dir(name)
                except Exception:
                    continue
                if _ws is not None:
                    # Already cloned — ensure its declared deps are installed
                    # into the shared venv (idempotent; no-op when unchanged),
                    # so all its tools work without waiting for the next run.
                    try:
                        await _asyncio.to_thread(
                            _install_agent_deps, _ws, settings
                        )
                    except Exception:
                        pass
                    continue

                def _clone(n: str, r: str | None, lp: str | None) -> None:
                    with load_agent(n, repo_name=r, local_path=lp):
                        pass

                try:
                    await _asyncio.to_thread(_clone, name, repo_name, local_path)
                    _log.info("gateway.warm_clone_done", agent=name)
                except Exception as exc:
                    _log.warning(
                        "gateway.warm_clone_failed", agent=name, error=str(exc)
                    )
        except Exception as exc:
            _log.warning("gateway.warm_clone_skipped", error=str(exc))

    _asyncio.ensure_future(_warm_clone_agents())

    # Load provider API keys from encrypted Postgres store into litellm SDK.
    #
    # H4: lifespan startup — there is no request, no session and no bound
    # tenant, and the destination (`configure_litellm` / `configure_integrations`
    # → `litellm.<provider>_api_key` + `os.environ`) is process-global anyway.
    # See `acb_llm.client._ensure_keys_loaded`'s H4 note for the full argument;
    # MT-1j slice 5 records this site rather than threading a guessed tenant
    # through it (`saas_multitenancy.md` §11).
    try:
        from acb_llm.key_store import get_key_store
        store = get_key_store()
        existing = await store.get_all()
        if not existing:
            _env_to_provider = {
                "GEMINI_API_KEY": "gemini", "OPENAI_API_KEY": "openai",
                "ANTHROPIC_API_KEY": "anthropic", "DEEPSEEK_API_KEY": "deepseek",
                "OPENROUTER_API_KEY": "openrouter", "GROQ_API_KEY": "groq",
                "MISTRAL_API_KEY": "mistral", "TOGETHER_API_KEY": "together",
            }
            for env_var, provider in _env_to_provider.items():
                val = os.environ.get(env_var, "")
                if val and val.strip():
                    await store.put(provider, val.strip())
        await store.configure_litellm()
        await store.configure_integrations()
        _log.info("gateway.keys_loaded_from_store")
    except Exception as exc:
        _log.warning("gateway.key_store_skipped", error=str(exc))

    # Background email sync scheduler. First wire the gateway's post-sync
    # callbacks (rules / categorize / classify / digest / follow-up) into the
    # scheduler's hook registry, so the scheduler runs them without importing up
    # into the gateway (C2 layering inversion). The hooks register
    # UNCONDITIONALLY — manual sync and the Graph webhook share the same pipeline
    # — while the always-on loop itself is gated behind EMAIL_SYNC_ENABLED (WS-29
    # launch defang; see _maybe_start_email_sync).
    try:
        from gateway.routes.email.scheduler_hooks import (
            register_email_post_sync_hooks,
        )
        register_email_post_sync_hooks()
        await _maybe_start_email_sync()
    except Exception as exc:
        _log.warning("gateway.email_sync_skipped", error=str(exc))

    # Wire the WhatsApp post-sync callbacks (intent classification + Reply Zero
    # chat status) into the ingestion registry. WhatsApp triage is webhook-driven,
    # so the webhook receiver fires these hooks after each batch; this just makes
    # them non-no-ops.
    try:
        from gateway.routes.whatsapp.scheduler_hooks import (
            register_whatsapp_post_sync_hooks,
        )
        register_whatsapp_post_sync_hooks()
        _log.info("gateway.whatsapp_hooks_started")
    except Exception as exc:
        _log.warning("gateway.whatsapp_hooks_skipped", error=str(exc))

    # Start the WhatsApp enrichment loop (group summaries + voice transcription).
    # These are LLM/STT passes, so it is cost-gated OFF unless WHATSAPP_ENRICHMENT
    # is set; the on-demand routes work regardless.
    try:
        from gateway.routes.whatsapp.scheduler import start_whatsapp_enrichment
        started = await start_whatsapp_enrichment()
        _log.info("gateway.whatsapp_enrichment", started=started)
    except Exception as exc:
        _log.warning("gateway.whatsapp_enrichment_skipped", error=str(exc))

    # Start background Tasks (GTD) provider-sync scheduler — one loop per
    # sync-enabled ClickUp/PM workspace keeps the agent's project/task/people
    # picture fresh between visits (routes/tasks/scheduler.py).
    try:
        from gateway.routes.tasks.scheduler import (
            start_background_sync as start_tasks_sync,
        )
        await start_tasks_sync()
        _log.info("gateway.tasks_sync_started")
    except Exception as exc:
        _log.warning("gateway.tasks_sync_skipped", error=str(exc))

    # Nightly auto roll-over of incomplete calendar time-blocks: each user's
    # overdue blocks are packed into their local today once the day rolls over
    # (routes/tasks/calendar.py). Fixes the "fell behind → stale plan" failure.
    try:
        from gateway.routes.tasks.calendar import start_auto_rollover
        await start_auto_rollover()
        _log.info("gateway.tasks_rollover_started")
    except Exception as exc:
        _log.warning("gateway.tasks_rollover_skipped", error=str(exc))

    # Workflow scheduling subsystem — the orphan-run reconcile sweep and the
    # cron schedule scanner, both gated behind WORKFLOW_SCHEDULER_ENABLED (WS-29
    # launch defang; see _maybe_start_workflow_scheduler).
    await _maybe_start_workflow_scheduler()

    # Start the ingestion event-bus consumer — drains ingestion:{clickup,zoho,
    # gmail} through the same event-sink registry the receivers emit to
    # (FOUNDATION_BUILDOUT_CHECKLIST.md §BO-20, BO-20a; §BO-20.0 Option A: the
    # loop lives in the ingestion package, the gateway starts it). Gated OFF by
    # default on INGESTION_CONSUMER — flipping it on is an OWNER-GATE because it
    # also cuts the receivers over to enqueue-only (Q1).
    try:
        from ingestion.consumer import start_ingestion_consumer
        started = await start_ingestion_consumer()
        _log.info("gateway.ingestion_consumer", started=started)
    except Exception as exc:
        _log.warning("gateway.ingestion_consumer_skipped", error=str(exc))

    # Start the CRM ⟷ Zoho two-way sync loop (routes/crm/sync_zoho.py; spec
    # crm_app.md §7.1, D-CRM-7). Gated OFF by default on CRM_ZOHO_SYNC: with
    # the flag off this registers NO loop at all. Flipping it on is an
    # OWNER-GATE because the loop then WRITES the live Zoho tenant unattended.
    # POST /crm/sync/zoho runs one cycle on demand either way.
    try:
        from gateway.routes.crm.sync_zoho import start_crm_zoho_sync
        crm_sync_started = await start_crm_zoho_sync()
        _log.info("gateway.crm_zoho_sync", started=crm_sync_started)
    except Exception as exc:
        _log.warning("gateway.crm_zoho_sync_skipped", error=str(exc))

    # Anthropic prompt-cache warming (specs/llm_caching_memory.md Phase 6).
    # Fire the orchestrator's stable prefix at any Anthropic-backed tier with
    # max_tokens=0 so the first real user request is a cache HIT, not a cold
    # miss. Fire-and-forget + gated on PROMPT_CACHE_PREWARM=1 (off by default:
    # our default tier is DeepSeek, where this is a no-op cost).
    _asyncio.ensure_future(_prewarm_prompt_cache())

    yield

    # Stop background email sync scheduler
    try:
        from email_ingestion.scheduler import stop_background_sync
        await stop_background_sync()
    except Exception:
        pass

    # Stop background Tasks (GTD) provider-sync scheduler
    try:
        from gateway.routes.tasks.scheduler import (
            stop_background_sync as stop_tasks_sync,
        )
        await stop_tasks_sync()
    except Exception:
        pass

    # Stop the calendar auto roll-over loop
    try:
        from gateway.routes.tasks.calendar import stop_auto_rollover
        await stop_auto_rollover()
    except Exception:
        pass

    # Stop the CRM ⟷ Zoho sync loop. Unconditional, like every other
    # supervised loop here: a flag-gated loop that never started is still
    # stopped, so the shutdown path never has to know why it is absent.
    try:
        from gateway.routes.crm.sync_zoho import stop_crm_zoho_sync
        await stop_crm_zoho_sync()
    except Exception:
        pass

    # Stop the WhatsApp enrichment loop
    try:
        from gateway.routes.whatsapp.scheduler import stop_whatsapp_enrichment
        await stop_whatsapp_enrichment()
    except Exception:
        pass

    # Stop the workflow schedule scanner
    try:
        from gateway.routes.workflows import stop_workflow_scheduler
        await stop_workflow_scheduler()
    except Exception:
        pass

    # Stop the ingestion event-bus consumer. Called UNCONDITIONALLY — like
    # stop_whatsapp_enrichment above, it is a no-op when the flag kept the loop
    # from ever starting, and a flag read at shutdown could differ from the one
    # at startup and leak the task.
    try:
        from ingestion.consumer import stop_ingestion_consumer
        await stop_ingestion_consumer()
    except Exception:
        pass

    # Flush audit writes that are still on worker threads. `acb_audit.record`
    # is non-blocking on the event loop (BO-10), which means an event recorded
    # moments before shutdown is in flight rather than committed; exiting here
    # would cancel it. Last, and after every loop above, so writes those loops
    # made on their way out are included. Bounded internally — a wedged audit
    # DB cannot hold the shutdown open.
    try:
        from acb_audit import drain as drain_audit
        await drain_audit()
    except Exception:
        pass

    _log.info("gateway.shutdown")


async def _prewarm_prompt_cache() -> None:
    """Pre-warm the Anthropic KV cache for the orchestrator's stable prefix.

    Gated on ``PROMPT_CACHE_PREWARM=1``. For each configured tier that resolves
    to an Anthropic model, fire the stable prefix once with ``max_tokens`` tiny
    and ``cache_control`` on the system block so the first real user request is
    a cache read (0.10× cost) instead of a cold miss + write (1.25×). Purely a
    latency/first-hit optimisation — never blocks startup and swallows all
    errors. No-op for DeepSeek/OpenAI tiers (OpenAI warms automatically).
    """
    if os.environ.get("PROMPT_CACHE_PREWARM", "0") != "1":
        return
    try:
        from acb_llm.client import _TIER_MODEL, ensure_model_registered
        from acb_llm.prompt_cache import is_anthropic_model
        from litellm import acompletion
        from orchestrator.agents import build_orchestrator_agent

        agent = build_orchestrator_agent(with_history=False)
        opts = agent.default_options
        stable_prefix = (
            (opts.get("instructions") if isinstance(opts, dict) else None) or ""
        )
        if len(stable_prefix) < 400:  # nothing worth caching
            return

        # Build a system message with the cache_control breakpoint at the seam.
        # No sentinel needed — with no dynamic suffix the whole prefix is the
        # cached block; mark it explicitly here.
        system_msg = {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": stable_prefix,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        warmed: set[str] = set()
        for tier_id, model in _TIER_MODEL.items():
            if model in warmed or not is_anthropic_model(model):
                continue
            warmed.add(model)
            ensure_model_registered(model)
            try:
                await acompletion(
                    model=model,
                    messages=[
                        system_msg,
                        {"role": "user", "content": "warm"},
                    ],
                    max_tokens=1,
                    temperature=0.0,
                )
                _log.info(
                    "gateway.cache_prewarm_complete", tier=tier_id, model=model
                )
            except Exception as exc:
                _log.warning(
                    "gateway.cache_prewarm_failed",
                    model=model,
                    error=str(exc)[:150],
                )
    except Exception as exc:
        _log.debug("gateway.cache_prewarm_skipped", error=str(exc)[:150])


# ── Public routes (BO-2 residual #1) ────────────────────────────────────────
#
# Everything not listed here requires authentication — a valid internal bearer
# token, or a domain-verified X-User-Email that arrived with one. Default-deny
# is applied once at the app level so a route added tomorrow is covered without
# anyone remembering to add a guard. The failure mode of opt-in security is the
# route nobody opted in, and that is exactly what happened to
# /agent/workspace/{id}/history and /promote, which were reading and writing
# agent workspaces anonymously until this landed.
#
# Every entry below either authenticates itself by another means or is a
# liveness probe. Gating one would not restrict access — it would break
# ingestion, sign-in, or the health check. Matching is on the route TEMPLATE,
# so a path parameter cannot be crafted to spell a public path.
PUBLIC_ROUTES: frozenset[str] = frozenset({
    # Liveness. Deliberately says nothing beyond status + env name.
    "/health",

    # Provider webhook receivers — each verifies its own signature in
    # ingestion/sources/*/webhook.py.
    "/webhooks/clickup",
    "/webhooks/gmail",
    "/webhooks/zoho",

    # Agent dispatch — HMAC-SHA256 over the raw body (X-CC-Signature), and it
    # fails closed when unconfigured. See routes/agent.verify_webhook_signature.
    "/agent/webhook/{source}",

    # OAuth callbacks: browser redirects from the provider carrying no session.
    # Trust comes from the HMAC-signed `state`. The authorize legs are
    # user-initiated and stay gated.
    "/integrations/oauth/callback/{service}",
    "/email/oauth/{provider}/callback",

    # Microsoft Graph change notification — validationToken echo + clientState.
    "/email/webhook/microsoft",

    # Meta webhook — verify-token on GET, signature on POST.
    "/whatsapp/webhook",

    # The Go bridge posts inbound messages with X-Bridge-Secret (constant-time
    # compare in whatsapp/transport/bridge.bridge_secret_ok).
    "/whatsapp/bridge/ingest",
    "/whatsapp/bridge/reclassify",
    "/whatsapp/bridge/labels",
    "/whatsapp/bridge/avatars",
    "/whatsapp/bridge/paired",
    "/whatsapp/bridge/avatars",

    # Meeting-bot worker callbacks — machine-authed by MEETING_BOT_TOKEN.
    "/notes/meetings/{meeting_id}/live/segment",
    "/notes/stt/bot-live-token",

    # Workflow webhook trigger — the unguessable per-workflow hook token IS
    # the credential (+ optional HMAC); rate-limited and it only fires
    # published workflows with an enabled webhook trigger. See
    # routes/workflows/hooks.py.
    "/workflows/hooks/{hook_token}",
})

def docs_enabled(env: str) -> bool:
    """Whether to serve Swagger/ReDoc/openapi.json.

    Swagger and ReDoc are the one surface the app-level auth dependency cannot
    reach: FastAPI mounts them as plain Starlette routes with no dependency
    chain. They publish the entire API surface, so outside dev the endpoints
    simply do not exist. Read the schema against a local instance instead.
    """
    return env == "dev"


_docs_enabled = docs_enabled(get_settings().acb_env)

app = FastAPI(
    title="AI Company Brain — Gateway",
    version="0.0.1",
    description="Pull queries, push notifications, approvals. See project-docs/system_architecture.md §3.",
    lifespan=lifespan,
    dependencies=[require_authenticated(public=PUBLIC_ROUTES)],
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# ── Tenant scope (MT-1c / H2) ── every HTTP request runs inside its own tenant
# scope: opened empty here, filled in by `_with_resolved_access` when the auth
# dependency resolves the caller's organization, released after the response.
# Pure ASGI (no BaseHTTPMiddleware) so the downstream app runs in THIS task and
# the contextvar token round-trips — which is what makes "one request can never
# inherit another's binding" true on any server task model, not just uvicorn's
# task-per-request. Jobs and consumers get no scope from this; they bind
# explicitly or fail closed with TenantUnbound (H4).
class TenantScopeMiddleware:
    def __init__(self, asgi_app):  # noqa: ANN001 — ASGI protocol shape
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        token = clear_tenant()
        try:
            await self.asgi_app(scope, receive, send)
        finally:
            release_tenant(token)


app.add_middleware(TenantScopeMiddleware)

# ── CORS ── allow workbench dev server (port 3001) and production origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://localhost:3000",
        os.environ.get("WORKBENCH_PUBLIC_URL", ""),
        os.environ.get("GATEWAY_PUBLIC_URL", ""),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Wire AG-UI endpoint — custom per-request endpoint with memory injection ──
# Replaced the singleton _add_ag_ui_endpoint pattern (which pre-built one agent
# at module level, making per-request memory enrichment impossible) with a
# custom FastAPI POST handler that:
#   1. Builds a fresh MAF Agent per-request (cheap — just wires tools, no I/O)
#   2. Enriches its instructions with Mem0 + Graphiti context for this user
#   3. Streams AG-UI SSE events via AgentFrameworkAgent + EventEncoder
#   4. Fires background memory extraction after the run

def _apply_thinking_mode(opts: dict, think_mode: str) -> None:
    """Apply thinking/reasoning mode to agent options.

    Thin delegate to the single implementation in
    ``orchestrator._model_resolution`` — the named-agent path
    (``/agent/run/stream``) needs the same mapping, and two copies is how the
    two run paths' memory injection silently diverged
    (agent_architecture.md §11.1.2).  Kept as a module-level name so existing
    callers and tests here are unaffected; it disappears with ``/copilot/chat``.
    """
    from orchestrator._model_resolution import (
        _apply_thinking_mode as _shared,
    )

    _shared(opts, think_mode)

if _HAS_MAF:
    try:
        from ag_ui.core.events import RunErrorEvent as _RunErrorEvent
        from ag_ui.encoder import EventEncoder as _EventEncoder
        from agent_framework.ag_ui import AgentFrameworkAgent as _AgentFrameworkAgent
        from agent_framework_ag_ui import AGUIRequest as _AGUIRequest

        @app.post("/copilot/chat", tags=["AG-UI"], response_model=None)
        async def copilot_chat(
            request_body: _AGUIRequest,
            background_tasks: BackgroundTasks,
            model: str | None = None,
            assistant_message_id: str | None = None,
            user: UserContext = Depends(get_current_user),
        ) -> StreamingResponse:
            """MAF orchestrator: per-request agent with Mem0+Graphiti memory injection.

            *model* (query param) is the LiteLLM tier the chat UI selected. The
            orchestrator is a native MAF agent, so it reads its model from
            ``default_options["model"]``; we set the resolved tier there and also
            expose it via ``_active_run_model`` so delegated specialists inherit it.

            *assistant_message_id* (query param — the AG-UI ``_AGUIRequest``
            body model drops unknown keys, so it can't ride in the body) is the
            frontend's row id for this turn; the run-end fold-and-persist
            (core_loop_unification Phase 1) upserts that same row.
            """
            from orchestrator.agents import (
                build_orchestrator_agent,
                enrich_instructions_with_memory,
            )

            user_id: str = getattr(user, "email", "") or "anonymous"
            input_data = request_body.model_dump(exclude_none=True)

            # ── Tenant for this run's writes (WS-29 acb_graph slice 6a — DARK) ──
            # Stamp the org SERVER-SIDE from the authenticated identity
            # (``user.organization_id``, filled by ``get_current_user`` /
            # ``_with_resolved_access`` — the SAME provenance ``routes/agent.py``
            # uses for the ``/agent/run/stream`` chat path in slice 2) and hand it
            # to ``run_detached`` below. This chat runs the MAF orchestrator
            # DIRECTLY (``protocol_runner.run``, not ``run_agent_stream``), so
            # ``run_detached``'s tenant bind is what its async ``acb_graph`` reads,
            # the ``on_complete`` persist hook, and any delegated sub-agent (which
            # inherits the bound tenant) resolve when the flag is ON. It MUST NEVER
            # come from ``input_data`` / ``request_body`` / the message list —
            # those are client/agent-visible, so sourcing the tenant from them is a
            # tenant-spoofing hole (R11, user_management_contract.md §0.9.3). DARK:
            # consumed only when ``ACB_GRAPH_TENANT_BIND`` is ON.
            _organization_id = getattr(user, "organization_id", None)

            # ── Set user context for memory tools (remember / save_memory / etc.) ──
            try:
                from acb_skills.memory_tools import _set_memory_user_id
                _set_memory_user_id(user_id)
            except ImportError:
                pass

            # Extract the last user message so memory search is query-focused
            messages = input_data.get("messages", [])
            last_user_msg: str = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                "",
            )

            # Build per-request agent (cheap — tools are closures, no network I/O)
            agent = build_orchestrator_agent(with_history=False)

            # thread_id (also derived below for the Redis relay) — pass it into
            # memory enrichment so the memory block is session-cached and stays
            # byte-stable across turns (specs/llm_caching_memory.md Phase 4).
            _thread_id_for_mem: str = (
                input_data.get("thread_id") or input_data.get("threadId") or ""
            ) or None  # type: ignore[assignment]

            # Inject Mem0 + Graphiti context into default_options (no-op if disabled)
            if last_user_msg:
                enriched = await enrich_instructions_with_memory(
                    agent, user_id, last_user_msg,
                    thread_id=_thread_id_for_mem,
                )
                opts = agent.default_options
                if isinstance(opts, dict) and enriched:
                    opts["instructions"] = enriched

            # Apply thinking mode to agent options
            think_mode = input_data.get("think_mode", "auto")
            if think_mode and think_mode != "auto":
                opts = agent.default_options
                if isinstance(opts, dict):
                    _apply_thinking_mode(opts, think_mode)

            # ── Resolve the selected LiteLLM tier and pin it on the agent ──
            # Native MAF agents read their model from default_options["model"]; if
            # unset they keep the build-time client default (tier-balanced) and the
            # chat UI's tier picker has no effect. Resolve via the BYOK-default
            # policy (empty/bare → tier-balanced or copilot_chat_model).
            _resolved_model = ""
            try:
                from orchestrator.executor import _apply_model_for_maf_agent
                _resolved_model = _apply_model_for_maf_agent(
                    agent, (model or "").strip(), get_settings())
            except Exception:
                pass

            protocol_runner = _AgentFrameworkAgent(agent=agent)

            async def event_generator():
                encoder = _EventEncoder()
                # Expose the run's tier so delegated specialists inherit it. Set
                # HERE (inside the streaming generator) so the tools' ContextVar
                # lookup sees it — the handler body runs in a different context.
                try:
                    from orchestrator.executor import _active_run_model
                    if _resolved_model:
                        _active_run_model.set(_resolved_model)
                except Exception:
                    pass
                try:
                    async for event in protocol_runner.run(input_data):
                        yield encoder.encode(event)
                except Exception as exc:
                    _log.exception("copilot_chat.stream_error")
                    try:
                        yield encoder.encode(_RunErrorEvent(
                            message="Internal error during agent run",
                            code=type(exc).__name__,
                        ))
                    except Exception:
                        pass

            # ── Detached execution + Redis relay (spec_stream_reconnection) ──
            # The orchestrator run executes in a background task that tees every
            # AG-UI frame to the per-thread Redis stream.  This response is a
            # Redis subscriber: client disconnects don't kill the run, and the
            # reconnect endpoint can replay missed events.  thread_id comes from
            # the AG-UI request body (the control plane always sends it).
            _thread_id: str = (
                input_data.get("thread_id") or input_data.get("threadId") or ""
            )

            # Authoritative persistence at run end (core_loop_unification
            # Phase 1/2): fold the run's Redis event log into the same row
            # the Next translator checkpoints. Only when the frontend sent
            # its row id — a minted fallback here would duplicate rows
            # against the translator's own time-based fallback id.
            _persist_cb = None
            if _thread_id and assistant_message_id:
                from gateway.chat_fold import persist_final_assistant_message

                # Snapshot the input conversation for run-boundary memory
                # extraction (P1-9) — captured here so the callback below has it
                # even if the HTTP request scope is gone by run end.
                _mem_conv_in = [
                    {"role": m.get("role", "user"),
                     "content": m.get("content", "")}
                    for m in (messages or []) if m.get("content")
                ]
                _mem_last_user = last_user_msg

                async def _persist_cb() -> None:  # type: ignore[misc]
                    folded = await persist_final_assistant_message(
                        _thread_id, assistant_message_id,
                        user_id=user_id, agent_name="orchestrator",
                        run_id=assistant_message_id,  # run-unique per turn
                        model=(_resolved_model or model),
                    )
                    # Memory extraction at the SAME run boundary (P1-9): fires on
                    # finish/error/cancel/reconnect via run_detached's finally,
                    # so a turn completed after a browser-gone still contributes
                    # to Mem0 — and it now includes the FOLDED ANSWER (the old
                    # background_tasks path saw only the input messages and never
                    # the assistant turn). route.ts no longer extracts for this
                    # orchestrator path. Best-effort.
                    if not (user_id and _mem_conv_in):
                        return
                    try:
                        from acb_memory import (
                            add_episode,
                            add_memories_background,
                        )

                        from gateway.chat_fold import (
                            build_extraction_conversation,
                        )
                        # _mem_conv_in already includes the current user turn
                        # (it's the full messages array), so pass message="".
                        conv = build_extraction_conversation(
                            _mem_conv_in, "", folded,
                        )
                        if not conv:
                            return
                        await add_memories_background(user_id, conv)
                        if _mem_last_user:
                            await add_episode(
                                name=f"chat:{user_id[:20]}",
                                content=_mem_last_user[:500],
                                source_description="copilot_chat",
                                group_id=user_id,
                            )
                    except ImportError:
                        pass
                    except Exception:
                        _log.warning(
                            "copilot_chat.run_end_memory_extraction_failed",
                            thread_id=_thread_id[:12],
                        )

            # ── Observability (E2): orchestrator lifecycle ───────────────────
            # The default chat runs the MAF agent directly (protocol_runner.run),
            # NOT run_agent_stream — so the executor's start/end activity events
            # don't fire here. Emit them explicitly, otherwise the orchestrator
            # (the primary agent) never appears working in the live office/feed.
            # End fires from run_detached's shielded on_complete (every terminal
            # outcome); a missed end self-heals via the presence-key TTL.
            import time as _obs_time
            _obs_run_id = assistant_message_id or _thread_id or None
            _obs_started = _obs_time.monotonic()
            try:
                from acb_common import publish_activity
                publish_activity(
                    kind="agent", phase="start", agent="orchestrator",
                    run_id=_obs_run_id, thread_id=_thread_id or None,
                    user=user_id or None, model=(_resolved_model or model or None),
                    source="chat",
                )
            except Exception:
                pass

            _prior_cb = _persist_cb

            async def _obs_on_complete() -> None:
                try:
                    if _prior_cb is not None:
                        await _prior_cb()
                finally:
                    try:
                        from acb_common import publish_activity
                        publish_activity(
                            kind="agent", phase="end", agent="orchestrator",
                            run_id=_obs_run_id, thread_id=_thread_id or None,
                            status="completed",
                            duration_ms=int((_obs_time.monotonic() - _obs_started) * 1000),
                            source="chat",
                        )
                    except Exception:
                        pass

            async def relayed_generator():
                import json as _json

                from orchestrator.stream_relay import (
                    get_detached_task,
                    run_detached,
                )
                from orchestrator.stream_relay import SupersedeRefused
                try:
                    # `actor` is not decoration: run_detached's reset DELETES the
                    # thread's event log, and without a recorded owner this
                    # endpoint was the one door left open on the §5.2 fix — a
                    # second person on /copilot/chat could still erase a
                    # transcript they did not own. Stamping the actor is what
                    # makes the guard able to refuse.
                    async for evt in run_detached(
                        _thread_id, event_generator(), tee=True,
                        on_complete=_obs_on_complete,
                        actor=(user_id if user_id != "anonymous" else None),
                        source="chat",
                        # Server-side tenant only (see ``_organization_id`` above)
                        # — never input_data/request_body. Binds the detached
                        # drain task so the whole run + on_complete see the right
                        # tenant (R11). WS-29 acb_graph slice 6a — DARK.
                        organization_id=_organization_id,
                    ):
                        yield f"data: {_json.dumps(evt)}\n\n"
                except SupersedeRefused:
                    _log.warning("copilot_chat.supersede_refused")
                    yield "data: " + _json.dumps({
                        "type": "RUN_ERROR",
                        "code": "run_in_progress",
                        "message": (
                            "Another participant has a run in progress on this "
                            "conversation. Nothing was discarded."
                        ),
                    }) + "\n\n"
                    return
                except Exception:
                    if get_detached_task(_thread_id) is not None:
                        _log.warning("copilot_chat.stream_subscribe_lost")
                        return
                    # Redis unavailable — degrade to direct streaming.
                    _log.warning("copilot_chat.stream_relay_unavailable")
                    async for line in event_generator():
                        yield line

            # Fallback memory extraction ONLY for the degraded no-thread_id /
            # no-message-id case (no run boundary to hook): fires on the
            # response lifecycle and sees only the input messages. The normal
            # path extracts at the run boundary inside _persist_cb above (P1-9),
            # with the folded answer included — so skip here to avoid double
            # extraction when that callback is wired.
            if _persist_cb is None:
                try:
                    from acb_memory import (
                        add_episode,
                        add_memories_background,
                    )
                    if last_user_msg and messages:
                        conv = [
                            {"role": m.get("role", "user"), "content": m.get("content", "")}
                            for m in messages if m.get("content")
                        ]
                        background_tasks.add_task(add_memories_background, user_id, conv)
                        # Also populate the bi-temporal knowledge graph (Graphiti)
                        background_tasks.add_task(add_episode,
                            name=f"chat:{user_id[:20]}",
                            content=last_user_msg[:500],
                            source_description="copilot_chat",
                            group_id=user_id,
                        )
                except ImportError:
                    pass

            return StreamingResponse(
                relayed_generator() if _thread_id else event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except Exception as _exc:
        _log.warning("gateway.ag_ui_failed", error=str(_exc))

# Webhook routers (Phase 1 ingestion entry points)
try:
    from ingestion.sources.clickup.webhook import router as _clickup_router

    app.include_router(_clickup_router)
except Exception:  # pragma: no cover - keep gateway bootable even if optional dep missing
    pass

try:
    from ingestion.sources.zoho.webhook import router as _zoho_router

    app.include_router(_zoho_router)
except Exception:  # pragma: no cover
    pass

try:
    from ingestion.sources.gmail.webhook import router as _gmail_router

    app.include_router(_gmail_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.agent import router as _agent_router

    app.include_router(_agent_router)
except Exception:  # pragma: no cover - keep gateway bootable if orchestrator not installed
    pass

try:
    from gateway.routes.integrations import router as _integrations_router

    app.include_router(_integrations_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-23 S1 — read-only skills catalog (skill families + token costs).
    from gateway.routes.integrations_skills import router as _skills_router

    app.include_router(_skills_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-23 S2 — per-agent skill toggles (GET/PUT /agent/{name}/skills).
    from gateway.routes.agent_skills import router as _agent_skills_router

    app.include_router(_agent_skills_router)
except Exception:  # pragma: no cover
    pass

try:
    # E2 Phase 3 — run diagnostics API over the agent_run trace store.
    from gateway.routes.debug import router as _debug_router

    app.include_router(_debug_router)
except Exception:  # pragma: no cover
    pass

try:
    # E2 live — real-time agent/model activity feed (activity bus).
    from gateway.routes.observability import router as _observability_router

    app.include_router(_observability_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.oauth import router as _oauth_router

    app.include_router(_oauth_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.tasks import router as _tasks_router

    app.include_router(_tasks_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.notes import router as _notes_router

    app.include_router(_notes_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-26 — native CRM (spec: project-docs/specs/crm_app.md). Leads,
    # deals, contacts, organizations, the pipeline and one activity timeline.
    from gateway.routes.crm import router as _crm_router

    app.include_router(_crm_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-27 — native project management (spec:
    # project-docs/specs/project_management_app.md). Departments, projects,
    # subprojects, tasks and subtasks, grant-scoped into every Center.
    from gateway.routes.projects import router as _projects_router

    app.include_router(_projects_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-28 — the People Center's directory (spec:
    # project-docs/specs/people_center_app.md). Its own feature gate, but
    # the HR projection is imported from routes/tasks, never re-implemented.
    #
    # ⚠️ TWO routers, and the ORDER IS LOAD-BEARING (WS-28g-2 / D-PC-15).
    # `self_router` serves `/people/me` with **no feature gate** — the
    # directory is gated, your own row is not — while `_people_router` serves
    # everything id-bearing behind `feature:people`. FastAPI matches in
    # registration order, and the gated router's `/people/{person_id}` pattern
    # matches the literal path `/people/me`. Included the other way round, a
    # member without the grant is refused at their own profile by the
    # directory's gate: exactly the defect this ticket fixed, reintroduced by
    # an include order. Fenced by `tests/unit/test_people_profile.py`.
    from gateway.routes.people import router as _people_router
    from gateway.routes.people import self_router as _people_self_router

    app.include_router(_people_self_router)
    app.include_router(_people_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.settings import router as _settings_router

    app.include_router(_settings_router)
except Exception:  # pragma: no cover
    pass

try:
    # BO-1 / A2 — Action Broker approval inbox over the pending_actions queue.
    from gateway.routes.actions import router as _actions_router

    app.include_router(_actions_router)
except Exception:  # pragma: no cover
    pass

try:
    # BO-1 / A2 — persistent handlers so a QUEUED task write executes on approval
    # (re-resolves the account token). Dormant unless ACTION_BROKER_ENFORCE is on.
    from gateway.routes.tasks.broker_handlers import register_task_broker_handlers

    register_task_broker_handlers()
except Exception:  # pragma: no cover
    pass

try:
    # D-CRM-8 — every Zoho sync push routes through the Action-Broker gate, so
    # the three `crm.zoho_*` actions need handlers that really execute when a
    # queued push is approved. Unlike the ClickUp set (BO-1a: six gated, four
    # registered), ALL THREE gated CRM actions are registered here.
    from gateway.routes.crm.broker_handlers import register_crm_broker_handlers

    register_crm_broker_handlers()
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.v1_compat import routers as _v1_routers

    for _r in _v1_routers:
        app.include_router(_r)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.chat import router as _chat_router

    app.include_router(_chat_router)
except Exception:  # pragma: no cover
    pass

try:
    # Rooms share /chat's prefix — a room IS a chat session — but live in their
    # own module because membership, presence, and the live room stream are a
    # different concern from history CRUD.
    from gateway.routes.rooms import router as _rooms_router

    app.include_router(_rooms_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.workspace import router as _workspace_router

    app.include_router(_workspace_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.memory import router as _memory_router

    app.include_router(_memory_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.email import router as _email_router

    app.include_router(_email_router)
except Exception:  # pragma: no cover
    pass

try:
    from gateway.routes.whatsapp import router as _whatsapp_router
    from gateway.routes.whatsapp import ws_router as _whatsapp_ws_router

    app.include_router(_whatsapp_router)
    # Separate router: the feature gate's dependency needs an HTTP Request,
    # which FastAPI never supplies to a WebSocket route. See core.ws_router.
    app.include_router(_whatsapp_ws_router)
except Exception:  # pragma: no cover
    pass

try:
    # Custom Apps / App Workshop (docs/app-workshop/README.md) — app CRUD,
    # workspace files, publish/versions, and the App Runtime API (prefix /apps).
    from gateway.routes.apps import router as _apps_router

    app.include_router(_apps_router)
except Exception:  # pragma: no cover
    pass

try:
    # Workflows app (project-docs/specs/workflows_app.md) — visual
    # automation builder: workflow CRUD/publish/runs, Module Studio, the node
    # catalog, and the inbound webhook trigger (prefix /workflows).
    from gateway.routes.workflows import router as _workflows_router

    app.include_router(_workflows_router)

    # Approval-node resume handler (workflow.resume_run) into the Action
    # Broker registry, and the workflow event dispatcher into the ingestion
    # event-hook registry — so provider webhooks can fire event triggers
    # without ingestion importing upward.
    from gateway.routes.workflows.broker_handlers import register_handlers

    register_handlers()
    try:
        from gateway.routes.workflows.triggers import dispatch_event as _wf_dispatch
        from ingestion.event_hooks import register_event_sink

        register_event_sink(_wf_dispatch)
        # WS-27f: assignment IS dispatch. A SECOND sink beside the workflows
        # dispatcher rather than a call inside `PUT /tasks/{id}/assignees` —
        # a slow or broken agent must not be able to fail the act of assigning
        # somebody a task.
        from gateway.routes.projects.agent_dispatch import on_event as _pm_agent_dispatch

        register_event_sink(_pm_agent_dispatch)
    except Exception:  # pragma: no cover - ingestion optional in some deploys
        pass
except Exception:  # pragma: no cover
    pass

try:
    # Org access control (project-docs/specs/org_access_control.md) —
    # member roster + lifecycle, roles, per-user overrides (prefix /admin),
    # plus /auth/me, which every signed-in member calls to resolve their own
    # feature and agent access.
    from gateway.routes.admin import me_router as _me_router
    from gateway.routes.admin import router as _admin_router

    app.include_router(_admin_router)
    app.include_router(_me_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-31 CP-2b — the ONE site that asks the Customer Console whether a
    # sign-in may proceed (customer_console.md §6 clause 11). Authenticated by
    # the app-wide `require_authenticated` above and deliberately NOT in
    # PUBLIC_ROUTES.
    #
    # ⚠️ Mounted always, and it is NOT inert when the Console settings are
    # missing: it REFUSES (`ConsoleUnavailable`). Reaching it means the BFF's
    # own flag was flipped, i.e. somebody declared this box wired, and a
    # half-provisioned box that admitted would be the fail-open posture CP-0
    # removed — finding F5, 2026-08-18. Ship-dark lives in the BFF's
    # CUSTOMER_CONSOLE_RESOLVE_ENABLED, which decides whether the route is
    # called at all.
    from gateway.routes.signin import router as _signin_router

    app.include_router(_signin_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-31 CP-2c slice 2 — the self-serve signup flow's server half
    # (customer_console.md §6 CP-2c). Authenticated by the app-wide
    # `require_authenticated` above and deliberately NOT in PUBLIC_ROUTES; the
    # caller is an ORGLESS session and the route is IDENTITY-ONLY (it creates a
    # tenant, it never binds or assumes one). Ships dark behind the gateway's
    # own `SELF_SERVE_SIGNUP_ENABLED` — off ⇒ `SignupDisabled`, nothing created.
    from gateway.routes.signup import router as _signup_router

    app.include_router(_signup_router)
except Exception:  # pragma: no cover
    pass

try:
    # WS-30 SC-2a — the customer seat-admin WRITE proxy (subscription_console.md
    # SC-2a / customer_console.md §6 item (h)). Authenticated by the app-wide
    # `require_authenticated` above and deliberately NOT in PUBLIC_ROUTES; it
    # forwards a session-authenticated seat write to the Console's deployment-key
    # `seat_admin` door. Ships dark — `is_wired()` false ⇒ 503, nothing written.
    from gateway.routes.seats import router as _seats_router

    app.include_router(_seats_router)
except Exception:  # pragma: no cover
    pass

# ---------- Health ----------

class Health(BaseModel):
    status: str
    env: str


def _runtime_checks() -> dict[str, dict]:
    """Validate the GitHub Copilot SDK (Tier 1.5) sandbox prerequisites.

    Checks (M2.6 cloud-sandbox requirements):
      - copilot SDK importable (bundled copilot.exe present)
      - pwsh on PATH (copilot.exe shell tool backend)
      - GITHUB_TOKEN configured (Copilot auth)

    Returns ``{check_name: {"ok": bool, "detail": str}}``. Never raises.
    """
    import shutil

    settings = get_settings()
    checks: dict[str, dict] = {}

    # copilot SDK importable
    try:
        import copilot  # noqa: F401

        checks["copilot_sdk"] = {"ok": True, "detail": "importable"}
    except Exception as exc:
        checks["copilot_sdk"] = {"ok": False, "detail": f"import failed: {exc}"}

    # pwsh on PATH
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    checks["pwsh"] = (
        {"ok": True, "detail": pwsh}
        if pwsh
        else {"ok": False, "detail": "pwsh not found on PATH — shell tool will fail on Linux"}
    )

    # GITHUB_TOKEN configured
    token = getattr(settings, "github_token", "") or os.environ.get("GITHUB_TOKEN", "")
    checks["github_token"] = (
        {"ok": True, "detail": "configured"}
        if token
        else {"ok": False, "detail": "GITHUB_TOKEN not set — github-copilot agents will fail"}
    )

    return checks


@app.get("/health", response_model=Health, tags=["meta"])
async def health() -> Health:
    return Health(status="ok", env=get_settings().acb_env)


@app.get("/health/runtime", tags=["meta"])
async def health_runtime() -> dict:
    """Report Tier 1.5 sandbox readiness (copilot SDK, pwsh, GITHUB_TOKEN)."""
    checks = _runtime_checks()
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}


# NOTE: /v1/chat/completions is served by routes/v1_compat.py (the full
# implementation: streaming, tools, provider message-sanitization, prompt-cache
# breakpoints, AND observability emission). It is registered before this module
# body runs, so a duplicate handler here would be permanently shadowed — it was
# removed (2026-07-09). Mem0 + every other OpenAI client already resolve to
# v1_compat. Only /v1/embeddings remains below (v1_compat doesn't serve it).


class EmbeddingRequest(BaseModel):
    model: str = "text-embedding-3-small"
    input: str | list[str]

@app.post("/v1/embeddings", tags=["openai"])
async def embeddings(
    req: EmbeddingRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """OpenAI-compatible embeddings endpoint.
    
    When OPENAI_API_KEY is available, proxies to the real OpenAI API.
    Otherwise returns a dummy embedding (zero-vector of 1536 dims) so
    Mem0's add() can complete — facts are stored without semantic search.
    """
    oai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if oai_key:
        from openai import OpenAI
        client = OpenAI(api_key=oai_key)
        inputs = req.input if isinstance(req.input, list) else [req.input]
        resp = client.embeddings.create(model=req.model, input=inputs)
        return resp.model_dump()
    # No embedding provider configured → return a zero vector so Mem0's add()
    # can still complete, but WARN loudly: semantic search is silently degraded
    # (every "similarity" is identical), which otherwise looks like memory works
    # when it doesn't (M13). Set OPENAI_API_KEY to restore real embeddings.
    inputs = req.input if isinstance(req.input, list) else [req.input]
    _log.warning(
        "gateway.embeddings_degraded_zero_vector",
        model=req.model, count=len(inputs),
        detail="OPENAI_API_KEY unset — returning zero vectors; semantic search disabled",
    )
    dummy = [0.0] * 1536
    return {
        "object": "list",
        "model": req.model,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": dummy,
            }
            for i in range(len(inputs))
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }



# ---------- Copilot models ----------
# Returns the list of models available via the GitHub Copilot SDK.
# The UI at /api/models/all calls this to populate the Copilot SDK model group.
# If GITHUB_TOKEN is not set, returns the static fallback list so the UI still
# works without blocking.

_COPILOT_MODELS_STATIC = [
    {"id": "claude-sonnet-4.6",    "label": "Claude Sonnet 4.6"},
    {"id": "claude-sonnet-4.5",    "label": "Claude Sonnet 4.5"},
    {"id": "claude-haiku-4.5",     "label": "Claude Haiku 4.5"},
    {"id": "claude-opus-4.6",      "label": "Claude Opus 4.6"},
    {"id": "claude-opus-4.6-fast", "label": "Claude Opus 4.6 (fast mode)"},
    {"id": "claude-opus-4.5",      "label": "Claude Opus 4.5"},
    {"id": "gpt-5.4",              "label": "GPT-5.4"},
    {"id": "gpt-5-mini",           "label": "GPT-5 mini"},
]


def _copilot_ctx(model_id: str) -> int:
    """Curated context-window (tokens) for a Copilot SDK model id.

    Used as a fallback when the SDK model object doesn't expose its limits.
    Substring match so version variants (claude-sonnet-4.5/4.6, gpt-5.4/5.5)
    resolve to the right family.
    """
    mid = (model_id or "").lower()
    if "claude" in mid:
        return 200_000
    if "gpt-5" in mid or "gpt5" in mid:
        return 400_000
    if "gpt-4.1" in mid:
        return 1_000_000
    if "gpt-4o" in mid or "gpt-4" in mid:
        return 128_000
    if mid.startswith(("o1", "o3", "o4")) or "-o3" in mid or "-o1" in mid:
        return 200_000
    if "gemini-3" in mid or "gemini-2.5" in mid or "gemini" in mid:
        return 1_000_000
    # DeepSeek V4 via Copilot CLI: docs configure an 840K prompt limit
    # (https://api-docs.deepseek.com/quick_start/agent_integrations/copilot_cli);
    # native DeepSeek API context is 1M but Copilot CLI caps the prompt budget.
    if "deepseek-v4" in mid or "deepseek/deepseek-v4" in mid:
        return 840_000
    if "deepseek" in mid:
        return 128_000
    return 0


def _sdk_ctx(model: object) -> int:
    """Best-effort extraction of a Copilot SDK model's context window.

    The GitHub Copilot models API exposes
    capabilities.limits.max_context_window_tokens; the SDK may surface it as
    nested attributes or a dict.  Returns 0 when unavailable (caller falls back
    to the curated map)."""
    caps = getattr(model, "capabilities", None)
    # Dict form
    if isinstance(caps, dict):
        lim = caps.get("limits") or {}
        if isinstance(lim, dict):
            v = lim.get("max_context_window_tokens") or lim.get("max_prompt_tokens")
            if isinstance(v, int) and v > 0:
                return v
    # Attribute form
    lim = getattr(caps, "limits", None)
    for attr in ("max_context_window_tokens", "max_prompt_tokens"):
        v = getattr(lim, attr, None)
        if isinstance(v, int) and v > 0:
            return v
    return 0

import time as _time

_copilot_models_cache: dict = {"data": None, "ts": 0.0}
_COPILOT_MODELS_CACHE_TTL = 300


@app.get("/copilot/models", tags=["copilot"])
async def copilot_models() -> dict:
    """Return Copilot SDK models with 5-min TTL cache."""
    _now = _time.monotonic()
    if _copilot_models_cache["data"] is not None and (_now - _copilot_models_cache["ts"]) < _COPILOT_MODELS_CACHE_TTL:
        return _copilot_models_cache["data"]
    settings = get_settings()
    github_token: str = (
        os.environ.get("COPILOT_GITHUB_TOKEN", "")
        or getattr(settings, "github_token", "")
        or os.environ.get("GITHUB_TOKEN", "")
    )
    if github_token:
        try:
            os.environ.setdefault("GITHUB_TOKEN", github_token)
            from copilot import CopilotClient
            _sdk = CopilotClient(options={"github_token": github_token})
            await _sdk.start()
            try:
                _models = await _sdk.list_models()
            finally:
                await _sdk.stop()
            if _models:
                result = {
                    "models": [
                        {
                            "id": m.id,
                            "label": m.name,
                            "model_picker_enabled": True,
                            # Real context window from the provider (SDK) when
                            # exposed; curated fallback otherwise.
                            "context_window": _sdk_ctx(m) or _copilot_ctx(m.id),
                        }
                        for m in _models if not m.policy or m.policy.state == "enabled"
                    ],
                    "source": "live",
                }
                _copilot_models_cache["data"] = result
                _copilot_models_cache["ts"] = _now
                return result
        except Exception as _e:
            _log.warning("gateway.copilot_models_failed", error=str(_e))
    static = {
        "models": [
            dict(m, model_picker_enabled=False, context_window=_copilot_ctx(m["id"]))
            for m in _COPILOT_MODELS_STATIC
        ],
        "source": "static",
    }
    _copilot_models_cache["data"] = static
    _copilot_models_cache["ts"] = _now - (_COPILOT_MODELS_CACHE_TTL - 30)
    return static


# ---------- Pull mode (Phase 0 stub) ----------

class PullRequest(BaseModel):
    query: str
    user_email: str | None = None


class PullResponse(BaseModel):
    answer: str
    citations: list[str] = []
    trace_id: str | None = None


@app.post("/pull", response_model=PullResponse, tags=["pull"])
async def pull(req: PullRequest, _user: UserContext = Depends(get_current_user)) -> PullResponse:
    """Phase-0 pull Q&A: routes through the MAF orchestrator agent."""
    import asyncio
    import uuid

    from acb_llm.guardrails import CITATION_RE  # local import to avoid cold-start cost

    trace_id = uuid.uuid4().hex
    user_id: str = req.user_email or getattr(_user, "email", "") or "anonymous"
    _log.info("pull.received", query=req.query, user=user_id, trace_id=trace_id)
    try:
        from orchestrator.agents import build_orchestrator_agent, enrich_instructions_with_memory
        agent = build_orchestrator_agent(with_history=False)
        # Inject Mem0 + Graphiti context for this user + query (no-op if disabled)
        enriched = await enrich_instructions_with_memory(agent, user_id, req.query)
        opts = agent.default_options
        if isinstance(opts, dict) and enriched:
            opts["instructions"] = enriched
        async with agent:
            response = await agent.run(req.query)
        text = response.text or ""
    except Exception as exc:
        _log.exception("pull.failed", trace_id=trace_id)
        return PullResponse(
            answer=f"[agent error] {type(exc).__name__}: {exc}", citations=[], trace_id=trace_id
        )
    citations = sorted({m.group(0) for m in CITATION_RE.finditer(text)})
    # Background: extract facts from this exchange into Mem0
    try:
        from acb_memory import (
            add_episode,
            add_memories_background,
        )
        messages = [
            {"role": "user", "content": req.query},
            {"role": "assistant", "content": text},
        ]
        asyncio.create_task(add_memories_background(user_id, messages))
        asyncio.create_task(add_episode(
            name=f"pull:{trace_id[:8]}",
            content=f"Q: {req.query}\nA: {text[:500]}",
            source_description="pull_endpoint",
            group_id=user_id,
        ))
    except ImportError:
        pass
    return PullResponse(answer=text, citations=citations, trace_id=trace_id)


# ---------- Sales Pull (WBS 1.5) ----------

@app.post("/pull/sales", response_model=PullResponse, tags=["pull"],
          dependencies=[require_role(UserRole.EXECUTIVE)])
async def pull_sales(req: PullRequest) -> PullResponse:
    """Sales-flavoured pull Q&A: uses customer-360 / quiet-deal context blocks."""
    import uuid

    from acb_llm.guardrails import CITATION_RE

    trace_id = uuid.uuid4().hex
    _log.info("pull.sales.received", query=req.query, user=req.user_email, trace_id=trace_id)
    try:
        from orchestrator.agents import build_orchestrator_agent
        agent = build_orchestrator_agent(with_history=False)
        async with agent:
            response = await agent.run(req.query)
        text = response.text or ""
    except Exception as exc:
        _log.exception("pull.sales.failed", trace_id=trace_id)
        return PullResponse(
            answer=f"[agent error] {type(exc).__name__}: {exc}", citations=[], trace_id=trace_id
        )
    citations = sorted({m.group(0) for m in CITATION_RE.finditer(text)})
    return PullResponse(answer=text, citations=citations, trace_id=trace_id)
