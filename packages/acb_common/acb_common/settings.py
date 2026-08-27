"""Centralised env-driven settings. One source of truth for every service."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment + .env at process start."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    acb_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # Postgres
    database_url: str = Field(
        default="postgresql+psycopg://acb:acb_dev_change_me@localhost:5432/acb"
    )
    # Seconds libpq waits to ESTABLISH a connection before giving up. Bounds the
    # worst case for every DB caller so a slow/firewalled DB host (or a
    # best-effort audit write) can never hang an agent indefinitely — it fails
    # fast and the caller's error handling takes over. Only the CONNECT phase is
    # capped, not query duration; a healthy local DB connects in <100ms so this
    # never trips in normal operation.
    db_connect_timeout: int = 10

    # Size of the ONE shared async pool per process (acb_common.db, BO-10).
    # Ceiling = db_pool_size + db_max_overflow = 30 connections from a process.
    #
    # These are knobs, not constants, because the arithmetic is deployment-wide:
    # a stock Postgres allows 100 connections total and the gateway is not its
    # only client — Langfuse, LiteLLM and the ingestion services draw from the
    # same server. 30 is what `gateway/db.py` already used for the packages that
    # had been converted, and it is deliberately unchanged here so consolidating
    # the rest is a no-op per package rather than a silent retune. Raise it when
    # you have raised `max_connections` (or put PgBouncer in front), not before.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # asyncpg's prepared-statement cache size, passed through to the driver
    # when set (acb_common.db.engine_connect_args). Leave None for the driver
    # default. Set 0 when DATABASE_URL points at a TRANSACTION-mode pooler
    # (PgBouncer / Supabase :6543): server-side prepared statements outlive
    # the transaction there and collide across pooled clients. Session-mode
    # pooling and direct connections need no override.
    db_statement_cache_size: int | None = None

    # Redis (event bus)
    redis_url: str = "redis://localhost:6379/0"

    # LiteLLM SDK routing — gateway /v1/chat/completions endpoint.
    # All LLM calls use the litellm Python SDK directly (no separate proxy).
    litellm_base_url: str = "http://127.0.0.1:8080"
    litellm_master_key: str = "sk-local"
    # SERVICE IDENTITY token — proves a caller *is* the platform and grants
    # full authority (acb_auth.require_internal_auth / SERVICE_ACCESS). Reads
    # GATEWAY_INTERNAL_TOKEN. Distinct from litellm_master_key ON PURPOSE:
    # the LLM key is handed to every agent's BYOK client, so while the two
    # were one value any agent could authenticate as the platform (BO-2
    # residual #4). When this is unset the gateway still falls back to
    # litellm_master_key so an un-migrated deployment keeps working, but it
    # logs a warning on every resolution because the separation is then absent.
    #
    # Never hand this to a /v1 client — use `llm_api_key` below.
    gateway_internal_token: str = ""

    # Master encryption key for the provider key store (ADR-008).
    # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
    # This is the ONLY secret that must be set — all provider keys are encrypted
    # in Postgres with this key.  Falls back to ACB_MASTER_KEY env var.
    acb_master_key: str = ""

    # LLM provider keys (DEPRECATED — use the key store via /settings/llm/key API).
    # Kept as fallback for bootstrap only; acb_llm prefers the key store.
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""     # console.anthropic.com — Claude models
    openrouter_api_key: str = ""    # openrouter.ai — 200+ models via one key
    deepseek_api_key: str = ""      # platform.deepseek.com — DeepSeek V3 / R1
    groq_api_key: str = ""          # console.groq.com — free tier, very fast inference
    mistral_api_key: str = ""       # console.mistral.ai
    together_api_key: str = ""      # api.together.ai — 100+ open-source models

    # Gateway
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gateway_session_secret: str = "change-me-dev-only"
    allowed_email_domain: str = "fracktal.in"
    #: Public origin of the gateway itself, e.g.
    #: "https://api.metorite.com". Set this when the platform
    #: hands an inbound URL to an external system (a workflow's webhook
    #: trigger): those callers must reach the gateway's own public route
    #: directly, NOT the control-plane proxy, which re-serializes bodies
    #: (breaking HMAC) and drops non-JSON payloads. Empty = the UI shows the
    #: path and says the origin is unconfigured rather than inventing one.
    public_api_base_url: str = ""

    # ⚠️ The three CLICKUP_* settings were REMOVED 2026-08-24 by D52 (board
    # WS-39 S1): ClickUp is retired outright and Metorite is the
    # project-management system of record. Do not re-add them — a settings
    # field is how a connector grows back.

    # Zoho CRM (Phase 0)
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_refresh_token: str = ""
    zoho_api_domain: str = "https://www.zohoapis.com"
    zoho_accounts_url: str = "https://accounts.zoho.com"
    zoho_region: str = "in"
    zoho_webhook_secret: str = ""        # HMAC secret for /webhooks/zoho (WBS 1.1)

    # CRM ⟷ Zoho two-way sync (spec crm_app.md §7.1, D-CRM-7). Gates ONLY the
    # scheduled loop the gateway lifespan registers (routes/crm/sync_zoho.py);
    # POST /crm/sync/zoho runs one cycle regardless, because a hand-run cycle
    # is an explicit admin act. Ships OFF: ON means the platform WRITES the
    # live Zoho tenant unattended — pushing native edits up and propagating
    # deletes both ways. Flipping it is an OWNER-GATE act (work_plan.md §6).
    crm_zoho_sync: bool = False

    # CRM auto-lead from inbound email (spec crm_app.md §9 WS-26d-autolead,
    # D-CRM-9). Gates ONLY the CRM step inside
    # `routes/email/scheduler_hooks.py::process_new_mail`, and the gate is read
    # BEFORE the step is entered, so with this off no CRM code runs and no CRM
    # query is issued on the mail path at all. Ships OFF: ON means unknown
    # inbound senders become `crm_leads` rows unattended — each born
    # `zoho_dirty`, i.e. queued for the LIVE Zoho tenant on the next sync cycle,
    # with no confirmation card anywhere on a scheduler hook and no delete tool.
    # Flipping it is an OWNER-GATE act (work_plan.md §6 (b)).
    crm_auto_lead: bool = False

    # ── Customer Console sign-in resolve (WS-31 CP-2b, spec customer_console.md
    # §6(f)) ────────────────────────────────────────────────────────────────
    #
    # `Settings` declares no `env_prefix`, so each field name maps directly to
    # its upper-cased environment variable.
    #
    # ⚠️ SHIPS DARK — **this half of it**. With `customer_console_url` OR
    # `customer_console_deployment_key` unset the GATEWAY's resolve path is
    # INERT: no HTTP call, no projection write, no refusal. Wiring a live
    # deployment (writing either value into its env) is 🔴 OWNER-GATE (§8 gate
    # 7); declaring the fields is not.
    #
    # ⚠️ It is NOT the whole ship-dark guarantee, and reading it as one was
    # finding F1 (2026-08-18). The BFF hop that CALLS this path lives in Next
    # and has its own switch, `CUSTOMER_CONSOLE_RESOLVE_ENABLED` (default unset
    # = OFF, read only by `workbench/control_plane/src/auth.ts`). These two
    # fields say nothing about whether the browser tier asks; that is the
    # flag's job, deliberately, because `CUSTOMER_CONSOLE_URL` is also the
    # billing surface's variable and gating the hop on it armed sign-in the
    # moment a Console address existed. See customer_console.md §6(f)/§6(g).
    #
    # `CUSTOMER_CONSOLE_URL` is deliberately the SAME name the workbench BFF
    # already reads (`app/api/billing/summary/route.ts`) — one Console, one
    # address, two readers. The KEY is a different name from the BFF's
    # `CUSTOMER_CONSOLE_ORG_KEY` because it is a different credential with a
    # different blast radius: `cc_live_` is org-scoped and read-only,
    # `cc_depl_` is deployment-scoped with capability `{resolve}`. Reusing one
    # name for two credentials is how a box presents the wrong one and gets a
    # 401 nobody can explain.
    customer_console_url: str = ""
    customer_console_deployment_key: str = ""
    # ── The THIRD credential, a third NAME on purpose (CP-11 s2) ──
    #
    # `cc_live_`, org-scoped. This box's key to the Console's AI ROUTER
    # (`POST /v1/chat/completions`), read only by
    # `acb_auth.console_resolve.chat_completion_on_console`.
    #
    # ⚠️ Read the paragraph above before adding a fourth. Three Console
    # credentials now sit on this box, and they are NOT interchangeable:
    #
    #   CUSTOMER_CONSOLE_DEPLOYMENT_KEY  cc_depl_  {resolve}   ask about PEOPLE
    #   CUSTOMER_CONSOLE_ORG_KEY         cc_live_  org-scoped  spend AI CREDITS
    #   (the Console's operator token never sits on a tenant box at all)
    #
    # The name deliberately MATCHES the workbench BFF's
    # `CUSTOMER_CONSOLE_ORG_KEY`: the same credential, one name, two
    # readers. That is the `CUSTOMER_CONSOLE_URL` precedent above, and the
    # opposite of the deployment key's argument. Same credential keeps the
    # name. A different credential takes a new one.
    #
    # ⚠️ **On a SHARED box this cannot be right for every tenant**,
    # which `seats.py` and `console_resolve.py:734` both already record. It is
    # correct on a single-org silo, and CP-11 slice 3 must resolve the key
    # per-organization before the flag can go on anywhere else.
    customer_console_org_key: str = ""
    # The freshness/staleness PAIR (§6(c)) — one number cannot express it.
    # Console REACHABLE  → a cached answer is re-consulted past the TTL.
    # Console UNREACHABLE → a cached person proceeds up to the ceiling, and
    #                       then sign-in fails closed even for them. A cache
    #                       with no ceiling is not a cache, it is a second
    #                       identity system that never expires.
    # A cached record already carrying `sign_in: false` overrides both and
    # refuses immediately, at any freshness (clause 6's dead-state rule).
    customer_console_resolve_ttl_seconds: int = 900          # 15 minutes
    customer_console_resolve_max_staleness_seconds: int = 86400  # 24 hours

    # ── Self-serve signup (WS-31 CP-2c, spec customer_console.md §6 CP-2c
    # item 7) ─────────────────────────────────────────────────────────────────
    #
    # The GATEWAY's own reader of the signup flag. Minted here; the exact
    # `=== "true"` idiom of `auth.ts:163` (a truthiness test would arm the route
    # for an operator who wrote `SELF_SERVE_SIGNUP_ENABLED=false` while
    # debugging). Default unset = OFF: `POST /signup/provision` answers
    # `SignupDisabled` and creates no organization.
    #
    # ⚠️ **Three readers, two containers, and this is ONLY the gateway's.** The
    # `/signup` Next page and the `signIn` limbo branch read `SELF_SERVE_SIGNUP_
    # ENABLED` from the *Next* env (`workbench/control_plane/.env.local`); this
    # field is the *gateway* env (`/opt/acb/app/.env`). Each fails closed
    # independently, and the deploy must set BOTH or the halves disagree.
    # `acb_auth/console_resolve.py` does NOT read this flag — the resolve path
    # is byte-identical under both positions. Flipping it live is 🔴 OWNER-GATE
    # (§8 gate 8).
    self_serve_signup_enabled: str = ""

    # Gmail (Phase 1, WBS 1.3)
    gmail_sa_json_path: str = ""         # service-account key file
    gmail_workspace_domain: str = ""     # e.g. fracktal.in
    gmail_default_user: str = ""         # default mailbox to impersonate
    gmail_pubsub_token: str = ""         # bearer token expected on /webhooks/gmail

    # Email OAuth (Gmail + Microsoft) — configured via Integrations → APIs UI
    gmail_oauth_client_id: str = ""
    gmail_oauth_client_secret: str = ""
    msft_oauth_client_id: str = ""
    msft_oauth_client_secret: str = ""

    # Dynamic Agent Loader (v2 — ADR-013)
    # Repos are cloned ONCE into agents_clone_dir/repos/ and refreshed with
    # git pull on each event (no full re-clone per run).

    # -- Auth: PAT (simple, use for dev / small teams) --
    github_token: str = ""                    # PAT with `repo` scope; used in clone URL + remote set-url

    # -- OAuth App (used by Control Plane Device Flow UI only) --
    # Register at: github.com/settings/applications/new  (Callback URL: http://localhost)
    # The client_id is NOT sensitive — it is a public identifier.
    github_client_id: str = ""               # OAuth App Client ID; never the secret

    # -- Auth: GitHub App (recommended for production) --
    # When set, _get_auth_token() should exchange app credentials for a
    # short-lived installation token.  Leave blank to fall back to github_token.
    github_app_id: str = ""                   # e.g. "123456"
    github_app_private_key_path: str = ""     # path to .pem file; never commit the key itself
    github_installation_id: str = ""          # org installation ID (visible in GitHub App settings)

    github_org: str = "FracktalWorks"         # org that owns agent-* and skill-* repos
    # Persistent clone root for agent workspaces + generated artifacts.
    # MUST NOT live under /tmp: systemd-tmpfiles wipes /tmp on every reboot,
    # which destroys all agent clones AND their artifacts at once. Defaults to
    # a dir under $HOME so it survives reboots; override with AGENTS_CLONE_DIR.
    agents_clone_dir: str = Field(
        default_factory=lambda: str(Path.home() / ".acb" / "agents")
    )

    # Custom Apps (App Workshop) workspace root — one folder per app slug.
    # Empty (the default) resolves to {agents_clone_dir}/custom_apps so app
    # workspaces share the reboot-safe home the agent clones use; override with
    # CUSTOM_APPS_ROOT to relocate. Resolution lives in
    # gateway.routes.apps.apps_root() (mirrors how agents_clone_dir consumers
    # resolve their paths).
    custom_apps_root: str = ""

    # T2 (React) App Workshop build dependency cache — react/react-dom/esbuild,
    # installed once at deploy time, never per-app. Empty (the default)
    # resolves to {agents_clone_dir}/vendor/t2-react; override with
    # CUSTOM_APPS_T2_VENDOR_DIR. Resolution lives in
    # gateway.routes.apps.t2_vendor_dir().
    custom_apps_t2_vendor_dir: str = ""

    # -- Bot git identity (written into every local clone via git config) --
    # Commits and PRs opened by Self_Mutation_Node carry this identity.
    # Create a dedicated GitHub machine user (or use the GitHub App's identity).
    github_bot_name: str = "Metorite"
    github_bot_email: str = ""                # default: {github_bot_name}@users.noreply.github.com

    # OpenHands Self-Mutation Sandbox (v2 — ADR-021)
    openhands_api_url: str = ""   # e.g. http://openhands:3000; leave blank to disable mutation

    # Copilot SDK Self-Mutation Sandbox (acb-mutation-runner) — WBS 1.2/1.3
    # A gateway tier alias (resolved dynamically to the configured model), NOT a
    # concrete provider/model. "openai/tier-powerful" was malformed — litellm
    # read it as provider=openai, model="tier-powerful" and 400'd.
    mutation_model: str = "tier-powerful"              # model the sandbox agent uses
    mutation_sandbox_image: str = "acb-mutation-runner:latest"
    mutation_timeout_seconds: int = 600              # hard cap on a single mutation run
    mutation_auto_pr: bool = True                    # open a GitHub PR after a successful fix

    # Resource/capability limits on the `docker run` invocation (BO-7 cheap
    # win 3/3 — the mutation-runner was the concrete precedent BO-7 wants
    # generalized to the normal agent load path, but it shipped with none of
    # its own: no --cap-drop, no --memory/--cpus, unrestricted on a 4GB VPS).
    # Conservative defaults sized for that box — mutation is an occasional,
    # on-demand run, not a resident service, so it doesn't need to compete
    # with Postgres/Redis/gateway/workbench for the whole machine.
    mutation_memory_limit: str = "2g"                # docker --memory
    mutation_cpu_limit: str = "2"                    # docker --cpus
    mutation_pids_limit: int = 512                   # docker --pids-limit (fork-bomb backstop)

    # Native-MAF mutation → monorepo PR (Part 1).
    # A native MAF agent (local_path, no git remote) can't push its self-mutation
    # anywhere, so approving one opens a PR against the Metorite monorepo
    # that edits apps/agents/agent-<name>/ in place. These configure that flow.
    #
    # ⚠️ DEV-ONLY — REPLACE BEFORE PRODUCTION/MULTI-TENANCY. Keep
    # mutation_monorepo_repo pointed ONLY at our own first-party monorepo, and
    # only in first-party/dev environments: third-party/customer agents must
    # never push to the shared monorepo. See
    # docs/DESIGN_LIMITATION_native_maf_mutation.md.
    #
    # The monorepo "owner/name" the PR is opened against. Leave blank to disable
    # the monorepo-PR path (native-MAF approvals then fall back to keep-local).
    mutation_monorepo_repo: str = ""                 # e.g. "Hathi-Labs/Metorite"
    mutation_monorepo_base: str = "main"             # PR base branch
    # Dedicated token with push + pull-request scope on the monorepo. Kept
    # separate from github_token (which only needs clone/read on agent repos) so
    # the broader monorepo-write credential is explicit. Falls back to
    # github_token when blank — see mutation_pr_token property below.
    mutation_pr_token: str = ""

    # Copilot SDK session sandbox (BO-7 phase 2 — containerize Copilot-SDK-
    # shaped agent execution: code_task and the App Workshop app-builder).
    # Unlike the mutation-runner sandbox (ships the whole SDK + a task driver
    # into the container, parsed via stdout sentinels), this containerizes
    # ONLY the `copilot` CLI binary as a TCP JSON-RPC server (the SDK's own
    # `cli_url` transport — see copilot/client.py). All host-side
    # orchestration — MetoriteCopilotAgent, event streaming, permission
    # handling — is unchanged; it just talks to a socket instead of a local
    # subprocess. See orchestrator/copilot_sandbox.py.
    #
    # Comma-separated subset of {"code_task", "app_builder"} — which call
    # sites route through the sandbox. Empty (default) = fully off, in-process
    # everywhere, zero behavior change. Spawn/readiness failure always falls
    # back to the existing in-process path — never hard-fails a call because
    # the sandbox didn't come up.
    copilot_sandbox_scope: str = ""
    copilot_sandbox_image: str = "acb-copilot-sandbox:latest"
    copilot_sandbox_port: int = 41041           # container-internal CLI server port
    copilot_sandbox_memory_limit: str = "768m"  # docker --memory (one interactive session, not a full self-mutation+pytest run)
    copilot_sandbox_cpu_limit: str = "1"        # docker --cpus
    copilot_sandbox_pids_limit: int = 256       # docker --pids-limit (fork-bomb backstop)
    copilot_sandbox_ready_timeout_seconds: float = 8.0   # spawn+TCP-ready budget before falling back in-process
    copilot_sandbox_idle_ttl_seconds: int = 600          # app-builder sticky-container reap window
    copilot_sandbox_state_dir: str = ""         # "" resolves to {agents_clone_dir}/.copilot-sandbox-state

    # Agent dependency installs (packages/acb_skills/acb_skills/loader.py
    # _install_agent_deps) — RCE guard (BO-7 fast pass). Agent repos'
    # requirements.txt/pyproject.toml install straight into the SHARED
    # gateway venv via pip/uv, ahead of any tool-call permission gate; an
    # sdist's setup.py / PEP 517 build backend can run arbitrary code during
    # that install. Default False forces --only-binary=:all: (wheels only —
    # a pure install has no code-execution step; the overwhelming majority of
    # PyPI ships wheels). Set True only for a specific, vetted environment
    # that genuinely needs a source-only package — this widens every agent's
    # install, not just one.
    agent_deps_allow_source_builds: bool = False

    # Copilot SDK chat (coworker sessions via /copilot/chat)
    # Auth order: LITELLM_MASTER_KEY → gateway /v1  |  GITHUB_TOKEN → api.githubcopilot.com
    # Model must be available in whichever provider is active.
    # Also controls the model injected into GitHubCopilotAgent Tier-1.5 runs.
    # Valid values (Copilot API): gpt-4o, gpt-4o-mini, claude-sonnet-4-5, o3-mini, o1
    # Default ALL agents/chats to LiteLLM's balanced tier (routed BYOK through the
    # gateway /v1 → DeepSeek), instead of GitHub Copilot's auto model selection.
    # Override per-deployment in .env (COPILOT_CHAT_MODEL) if needed.
    copilot_chat_model: str = "tier-balanced"

    # BYOK-by-default: route EVERY Copilot SDK agent through the LiteLLM gateway
    # (/v1 BYOK) instead of api.githubcopilot.com.  When on (the default), any
    # resolved model is BYOK-routed; a bare model name the gateway doesn't
    # expose (e.g. an .agent.md ``claude-sonnet-4-5``) is normalized to
    # ``copilot_chat_model`` (tier-balanced) so it always resolves.  Set
    # COPILOT_BYOK_DEFAULT=false to allow bare names to hit GitHub Copilot direct.
    copilot_byok_default: bool = True

    # ---------------------------------------------------------------------------
    # OAuth 2.0 authorization-code flow (M2.6) — Integration token exchange.
    # The Control Plane Integration page redirects to each provider's consent
    # screen; the callback exchanges the code for access + refresh tokens, which
    # are persisted to .env and injected into agents at run time.
    # ---------------------------------------------------------------------------
    # Public base URL the provider redirects back to (no trailing slash).
    oauth_redirect_base: str = "http://localhost:8000"

    # ⚠️ The ClickUp OAuth app fields were REMOVED 2026-08-24 by D52.

    # Google OAuth app (console.cloud.google.com — Gmail scopes)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_access_token: str = ""         # set by the OAuth callback
    google_refresh_token: str = ""
    google_token_expiry: str = ""         # ISO-8601 expiry of the access token

    # Zoho access token cache (refresh_token already above)
    zoho_access_token: str = ""
    zoho_token_expiry: str = ""


    # Apollo.io (prospecting, contact enrichment)
    apollo_api_key: str = ""
    apollo_base_url: str = "https://api.apollo.io/v1"

    # Google Maps Platform (Places API — used by sales-prospector Step 1)
    google_maps_api_key: str = ""

    # Instantly.ai (email sequencing — used by sales-prospector Step 7)
    instantly_api_key: str = ""
    instantly_base_url: str = "https://api.instantly.ai/api/v1"

    # SMTP outbound (generic email send fallback)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    # SerpAPI (Google search results — used by research/prospecting agents)
    serpapi_api_key: str = ""

    # Apify (web scraping actors)
    apify_api_token: str = ""

    # AnyMailFinder (email discovery)
    anymailfinder_api_key: str = ""

    # Google Sheets (service-account key path — can reuse gmail_sa_json_path)
    google_sheets_sa_json_path: str = ""

    # ---------------------------------------------------------------------------
    # Memory layer (WBS 2.5) — Mem0 episodic memory + Graphiti bi-temporal KG
    # ---------------------------------------------------------------------------

    # Mem0 — episodic memory per user (cross-session facts).
    # Backend: Postgres + pgvector (no new infra when mem0_enabled=true).
    # Set mem0_enabled=true once MEM0_ENABLED=true is in .env and the Postgres
    # schema migration (07_mem0_schema.sql) has run.
    mem0_enabled: bool = False

    # Graphiti — bi-temporal entity KG.
    # Requires Neo4j running (docker compose --profile memory up -d neo4j).
    graphiti_enabled: bool = False
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""   # required when graphiti_enabled=true

    # Email semantic search (Phase 2) — embed emails into email_embeddings
    # (pgvector) and blend cosine similarity with full-text rank on the
    # hybrid=true path of /email/search. OFF by default: lexical FTS (Phase 1)
    # is complete on its own, and embedding a mailbox costs tokens + a background
    # sweep. Turn on once migration 73 has run.
    email_semantic_search_enabled: bool = False
    # One model for all email embeddings. The vector column in migration 73 is
    # sized 1536 for this default; changing to a different-dimension model
    # (e.g. gemini text-embedding-004 = 768) requires recreating the column and
    # re-embedding — the model is stored per row so that migration is scriptable.
    email_embedding_model: str = "text-embedding-3-small"
    email_embedding_dim: int = 1536

    # Task-manager semantic capability matching (spec §5, Phase 2) — embed each
    # person's capability text (role · skills · résumé) into gtd_people
    # .capability_embedding and blend cosine similarity with the keyword match
    # when suggesting an assignee. OFF by default: keyword matching is complete on
    # its own and embedding the roster costs tokens. Turn on once migration 75 has
    # run. Reuses email_embedding_model (one embedder for the whole app).
    task_semantic_match_enabled: bool = False

    # WhatsApp semantic search (spec §W10) — embed each message (body +
    # voice-note transcript) into wa_message_embeddings (pgvector) and blend
    # cosine similarity with the full-text rank on the hybrid=true path of
    # /whatsapp/search. OFF by default: lexical FTS is complete on its own and
    # embedding history costs tokens + a background sweep. Turn on once migration
    # 111 has run. Reuses email_embedding_model/dim (one embedder for the app).
    whatsapp_semantic_search_enabled: bool = False

    # ── Token accessors ────────────────────────────────────────────────────

    @property
    def llm_api_key(self) -> str:
        """The key a ``/v1`` client should present. **Never the identity token.**

        Use this for anything whose only job is routing LLM completions
        through the gateway — BYOK clients, the Copilot SDK provider config,
        the mutation container's env. Those all run (or execute) model-authored
        code, so handing them ``gateway_internal_token`` would let an agent
        authenticate as the platform.

        Callers that hit gateway *business* APIs (``/tasks``, ``/email``,
        ``/whatsapp``, workspace upload) still need
        ``gateway_internal_token`` — until they act on behalf of a member
        instead. See project-docs/specs/org_access_control.md §8b.
        """
        return (self.litellm_master_key or "").strip() or "sk-local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor; call from anywhere."""
    return Settings()  # type: ignore[call-arg]
