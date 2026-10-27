# Apps

History of this file is in git (git log -p).

## Purpose
Three distinct kinds of unit, grouped by lifecycle into subfolders. The split
is load-bearing. **Services** are deployed processes. **Agents** and **skills**
are *loaded by* a service at runtime and are never deployed on their own.

A bare test file name below is under `tests/unit/`, and a bare spec name is
under `project-docs/specs/`.

## `services/` — deployed processes
- gateway/ -- FastAPI entry point, AG-UI chat, agent routes, OAuth, integration credential management (DB-backed, encrypted at rest), MCP server registry, plugin registry. Local contract: `apps/services/gateway/AGENTS.md`.
- orchestrator/ -- Agent execution engine, mutation layer, MAF integration. Local contract: `apps/services/orchestrator/AGENTS.md`.
- email_ingestion/ -- Multi-provider email sync engine (Gmail, Microsoft 365, IMAP/SMTP, aiosmtpd inbound, background scheduler). Local contract: `apps/services/email_ingestion/AGENTS.md`.
- agent_edge/ -- The public edge for outside agents (WS-55, spec `external_agent_access.md`). It is a separate unit (D-EA-2) that holds a signing key and no internal bearer. Local contract: `apps/services/agent_edge/AGENTS.md`.
- reconciler/ -- Nightly source-of-truth diff and escalation.
- whatsapp_ingestion/ -- WhatsApp ingestion over the official Meta WhatsApp Business Cloud API.
- whatsapp_bridge/ -- The unofficial WhatsApp transport (Go, whatsmeow) that pairs a personal number by QR code.
- meeting_bot/ -- A self-hosted headless-Chrome worker that joins a meeting, records the audio and hands it to Metorite.
- ingestion/ -- Zoho and Gmail webhook receivers, MCP servers, the Redis-Streams producer (`queue.py`), the drain loop (`consumer.py`) and the sink registry (`event_hooks.py`). The design record is `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-20.
  - ⚠️ `sources/zoho/writer.py` is the only outward-WRITE client in this package, and the only Zoho write surface in the repo. Its one caller is `gateway/routes/crm/sync_zoho.py::execute_push`. Do not import it from anywhere else, and do not add a second write client. Fence: `test_crm_zoho_sync.py`.
  - Its sibling `client.py` stays read-only. `SETTINGS_API_VERSION` names the settings API version ONCE. A tenant that refuses it raises `ZohoApiVersionError`, and nothing tries a ladder of older versions.
  - **Receivers:** verify the credential, log, best-effort `queue.enqueue`, then schedule `emit_event(source, event_type, payload)` as a background task. **Never `await` the emit inline.** A provider webhook must never 5xx.
  - **Fail closed:** with `zoho_webhook_secret` or `gmail_pubsub_token` empty, each push gets 401. Provisioning them is an OWNER action.
  - ⚠️ With `INGESTION_CONSUMER` set, a receiver enqueues only, and the consumer is the sole caller of `emit_event`. A failed `enqueue` then drops the event. Never "fix" it with a fallback to an inline emit.
  - **`(source, event_type)` is a user-visible vocabulary** (workflow event triggers match on it). Do not invent strings in a PR. Coerce the provider's value to `str` before `xadd`.
  - **Never pass `event=` to a logger**, because it is structlog's own message parameter. Use `zoho_event=` or `gmail_event=`. This rule is advisory (R7), because no guard pins it now.
  - `consumer.py` creates its group at `$` (the tail) and runs one supervised `XREADGROUP` loop over all streams. Each dispatch is bounded by `_DISPATCH_TIMEOUT_SECS` (30 s), and it acks regardless of outcome until BO-20b. Fence: `test_ingestion_consumer.py`.
  - `event_hooks.py` is the sink registry, so **ingestion never imports upward**. `emit_event` takes a keyword-only `raise_on_error: bool = False`. **Never flip the default, and keep it keyword-only.** Sinks must be idempotent per `(source, event_type, payload)`.
  - ⚠️ `raise_on_error=True` is a no-op against the only sink in production (`workflows.triggers.dispatch_event`). Do not build on a strict emit that can fail in production until BO-20b slice 2 lands.
- action_broker/ -- Approval-gated source-of-truth write executor: authority-tier disposition and a fail-closed handler registry.
  - ⚠️ **The queue binds the tenant.** Each `pending_actions` read and write opens `acb_graph.tenant_session(org)`, and `org` is `current_tenant()`, never a proposal field. Do not add a `get_session()` here. Fences: `test_action_broker_tenancy_r8.py` (R8), `test_action_broker.py`.
  - `ACTION_BROKER_ENFORCE` ships OFF in every environment, so the posture is audit-and-chokepoint. Flipping it is OWNER-GATE, and it stays blocked on BO-1d (`work_plan.md` WS-1).
  - The task connector registry stays empty. Fence: `test_no_task_provider_connectors.py`.
- customer_console/ -- WS-31, the operator-side subscription, seat and AI-metering engine (spec `customer_console.md`).
  - ⚠️ **A different plane and a different database.** It is cross-tenant by design, so it has its OWN engine (`customer_console/db.py`, in `test_db_engine_seam.py::_ALLOWED_SYNC`). Its OWN migration ladder is `infra/customer_console/`, **never** `infra/postgres/`. `CUSTOMER_CONSOLE_DATABASE_URL` has no default.
  - **Four auth schemes:** operator, internal (the Router writes the meter), organization key `cc_live_…` (read-only), and deployment key `cc_depl_…`. A deployment key's reach is decided per door by its CAPABILITY SET (`resolve`, `provision`, `seat_admin`, `member_admin`), never by the scheme.
  - Every door comes from the one `deployment_or_operator(capability)` factory. Register each closure in `AUTHENTICATING_DEPENDENCIES` separately. A refused capability is a 403, logged without the secret. Fence: `test_customer_console_resolve.py`.
  - **The credential, never the body, decides the subject (R11).** A deployment key that names `org_slug` or `deployment_label` gets 400, never ignored. Never add a second endpoint to separate the two arms.
  - **Nothing infers the box.** A `count(deployment)=1` fallback is forbidden by name. Provisioning places but never MOVES. Fence: `test_customer_console_lifecycle.py`.
  - The metered party never reports its own usage. Balance is `SUM(credit_ledger.delta)`, and no code writes a balance column (`TestNoCodePathUpdatesABalanceColumn`).
  - 🔴 OWNER-GATE: issuing or widening a real `cc_depl_` key, and flipping `CUSTOMER_CONSOLE_SPEND_GATE` for a real customer. A live seat write on a real org, a model bound to a tier and the Razorpay account (even in TEST mode) are OWNER-GATE too. No HTTP route mints a deployment key.
  - **The member doors.** `POST /registry/members` is create-or-reinstate and mints no role (D12). `store.activate_invited_member` guards with `AND status = 'invited'` in its own WHERE, never an `if`. `POST /registry/members/remove` hands the last owner on through `store.make_owner`.
  - **The rename door (CP-8).** `POST /registry/orgs/rename` uses `member_admin` and `_MEMBER_ADMIN_ROLES`, and adds no capability. `store.rename_org` writes `organization.name` and never the slug. It refuses an empty name or a name over 80 characters with 422. Fence: `test_customer_console_org_rename.py` (R8).
  - Member fences: `test_customer_console_member_write.py` (R8), `test_invite_console_mirror.py`, `test_customer_console_seat_overview.py` (R8).
  - ⚠️ **`customer_console/payments.py` is the ONLY path from money to entitlement.**
    - Money is integer PAISE past `payments.paise()`, which refuses a float.
    - `payments.fulfil()` is the ONE checkout writer of entitlements, with exactly TWO call sites. `POST /billing/subscriptions/activate` is its offline sibling, not a third call site. `store.lock_org_activation` serialises that sibling.
    - Transport dedup (`payment_event.provider_event_id`) and the money guard (`payment_order.status`) are two different guards. `payment.failed` transitions nothing, and nothing in the tree writes `failed`.
    - A capture that grants nothing logs at ERROR. Never downgrade that line, and never delete an unattributed receipt. CP-8's reconciliation owns those rows.
    - No org-key route writes an entitlement or a ledger row (`test_no_org_key_route_writes_an_entitlement_or_ledger_row`). Do not extend its declared metering exemption.
    - The org-key reads (`GET /billing/catalog`, `GET /me/seats`, `GET /me/members`) mint nothing and take the org from `caller.organization_id`. They reuse the one store computation, never a second SQL.
  - ⚠️ **A customer refusal lands in the meter.** Only the 400 unknown tier, the 402 no credit and the 403 run ceiling write a refusal row. `main._record_refusal` opens its OWN short transaction.
    - Every read that counts calls excludes a refusal. 🔴 `last_seen_by_org` does **not**, on purpose. Fences: `test_customer_console_sql.py::TestARefusalIsNotACall`, `test_customer_console_router.py::TestARefusalReachesTheMeter` (R8).
  - ⚠️ **A stream fails over before its first frame, and never after it.** `router.walk_chain` is the one failover policy, for buffered and streamed calls.
    - ⚠️ `walk_chain` does not read `MAX_CHAIN_ATTEMPTS`. Cap the list with `attempts[:router_mod.MAX_CHAIN_ATTEMPTS]` before you hand it over.
    - 🔴 Use `anyio.from_thread.run`, NEVER `asyncio.run`, for a stream. Close every provider stream through `router.aclose_quietly`. **H-80** holds the threadpool hazard.
    - Fences: `test_customer_console_router.py::TestAStreamFailsOverBeforeItsFirstFrame` (R8), `test_router_failover.py::TestTheStreamWalk`. Spec: `ai_metering_and_analytics.md` §8.6.
  - ⚠️ **An image follows the chat model when it can** (`router.resolve_vision_chain`). The declared task resolves FIRST, then the lift, then the fall to `tier-vision`, then a 400.
    - 🔴 `model_profile.reads_images` is the ONE source for the flag, and `router._models_that_read_images` is its one reader on the serving path. Nothing sniffs `messages`. A blind step never enters the lift chain (**H-81**).
    - Fence: `test_customer_console_router.py::TestTheRouterImageRule`. Spec: `ai_metering_and_analytics.md` §8.5.
  - ⚠️ **The serving doors** are chat, transcribe, image, speak and `POST /v1/decide`. The door declares the task, and the `model` field names a TIER.
    - 🔴 The per-unit cost reads `task_catalog.natural_unit` through `_PER_UNIT_COLUMNS`. `SERVING_INVOCATIONS` stays a subset of `catalog.KNOWN_INVOCATIONS`.
    - A speak call never streams. `ImageRequest` carries NO `size` field until **H-87** lands. Fences: `test_customer_console_tasks.py` (R8), `test_customer_console_payments.py`.
    - `/v1/decide` uses the native handler seam (`customer_console/handlers.py`). One wire class, `handlers.SystemOneHandler`, serves each vendor. The native dispatch lives INSIDE `router._default_provider_call`.
    - 🔴 The model prefix and the native verb must name the SAME vendor. `catalog.check_invocation_for_task` pairs verb and task. Fence: `test_customer_console_decide.py` (R8).
  - ⚠️ `gateway/routes/signin.py` (`POST /signin/resolve`) is the ONE holder of `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` and the one caller of `acb_auth.console_resolve`. It answers 200 always, and it refuses when the gateway's Console env is missing.
    - Never add it to `PUBLIC_ROUTES`. Never hang the resolve off `_with_resolved_access` or `resolve_access`, because it allocates a seat. Fence: `test_signin_resolve_route.py`.
  - ⚠️ `gateway/routes/email_otp.py` is the server half of the email-OTP adapter, and it decides NOTHING. It calls `acb_auth.email_otp`.
    - The address is the authenticated `X-User-Email`. A body that names `email`, `identifier` or `org` gets 400. It is not in `PUBLIC_ROUTES`, and it has no flag of its own. Fence: `test_email_otp_token.py`.

## `agents/` — agent definitions (dynamically loaded at runtime)
Identity, system prompt, tool set and integrations. `build_agents()` loads them
through a `local_path` entry in `agent_registry.json` and the gateway `_AGENT_REGISTRY`.
They run *inside* the orchestrator or gateway process, with no server of their own.
- **Every agent writes in one voice** (spec `agent_writing_voice.md`). `_tool_injection._apply_voice` puts `acb_llm.voice` in each agent's system prompt once, after `instructions.md`.
  - Do not copy the voice rules into an `instructions.md`. `floor_opt_out` cannot remove the voice. Fence: `test_agent_voice.py`.
- agent-orchestrator/ -- Wraps the built-in orchestrator Agent so it goes through the same `/agent/run/stream` path as all other agents.
- agent-task-manager/ -- My Tasks, the personal task agent. It is a shared agent, so D85 refuses its Copilot CLI shell.
- agent-apis-config/ -- API discovery and configuration assistant, a native MAF `Agent`.
- agent-email-assistant/ -- Email AI assistant: read, search, summarize and draft replies across Gmail and Microsoft accounts.
  - Each text that a sender wrote reaches the model inside the ONE frame of `acb_skills/untrusted.py`. No tool builds a marker itself. Fences: `test_untrusted_frame.py`, `evals/email_injection/`.
  - `floor_opt_out` names `web_search` and `fetch_page`, so it holds no network tool. Each run that it delegates to binds `no_egress` (`UNTRUSTED_INPUT_AGENTS`). Fence: `test_floor_opt_out.py`.
  - `read_email_attachment` must not be named `read_attachment`, because an agent tool with a platform tool's name drops that platform tool. Fence: `test_email_attachment_text.py`.
  - A send, a forward, an unsubscribe and an outward rule each show a card that names every target before the post. `_is_outward_action` is the one check of a rule. Fences: `test_email_rule_confirm.py`, `test_email_send_cards.py`, `test_email_forward.py`.
  - With `EMAIL_HELD_CARD_SEND` on, a held chat sends only through the held card (`_held_card_mode`). Fences: `test_held_send_no_ticked.py`, `test_egress_card_bound_pin.py`.
  - `narrow_and_read` exists only when `NARROWING_AGENTS` names `email-assistant`. Setting that flag on production is OWNER-GATE. Fence: `test_email_narrow_source.py`.
- agent-whatsapp-assistant/ -- WhatsApp inbox briefing, triage and reply drafting (drafts only).
  - `narrow_and_read` exists only when `NARROWING_AGENTS` names `whatsapp-assistant` (OWNER-GATE on production). Its READ writes no row and sends no read receipt. Fences: `test_whatsapp_narrow_source.py`, `test_whatsapp_read_no_mark.py` (R8).
- agent-crm/ -- CRM assistant (`crm-assistant`). ⚠️ **The directory is `agent-crm`, the agent is `crm-assistant`**, and the `local_path` in `_AGENT_REGISTRY` is the whole mapping.
  - **Every write asks a human first and fails CLOSED.** Each write tool awaits `request_confirmation` **before it builds a mutating request**, and none passes `non_interactive_default="approve"`. Fence: `test_crm_agent_write.py`.
  - **The card must NAME the record being written to.** A tool takes names where a person speaks names, never creates vocabulary, and refuses a name that matches two rows. Identity is never an argument.
  - `_ALLOWED_METHODS` is `{"GET", "POST", "PATCH"}`, checked in the single `_request` helper. Do not add `DELETE` or `PUT`.
  - **Any new tool must route its path segments through `_record_uuid` and `_entity_slug`** and bind the results to `slug` and `record`. Fence: `test_crm_agent.py::test_the_path_guard_is_structural_not_a_docstring`.
  - It imports the read tools of `acb_skills.crm_tools` and keeps no copy. A Mirror run drops the record writes (orchestrator contract 19). Fence: `test_crm_mirror_run_tools.py`.
- agent-projects/ -- Projects assistant (`projects-assistant`, spec `projects_ai_chat.md`). ⚠️ **The directory is `agent-projects`, the agent is `projects-assistant`.** Its tools live in `apps/skills/skill-projects`, and `agents.py` holds identity only.
  - Every tool calls `/projects/*` as the acting member, so the route's rule is the one authority.
  - **The route manifest is the allowlist.** `skill_projects.manifest.MANIFEST` maps every `/projects` route to a tool and a class: A (read), B (reversible write), C (hard-to-undo write) or X (excluded). The client refuses a route that the manifest does not carry.
  - Every request field (`SENDS`, `FIELD_EXEMPT`, `FIELD_PLANNED`) and every UI client method (`UI_ACTIONS`, `UI_EXEMPT`, `UI_PLANNED`) has a decision in the manifest. The two hard deletes are class X (D-PM-35).
  - Coverage fences: `test_projects_chat_coverage.py`, `test_projects_field_parity.py`, `test_projects_ui_actions.py`, `test_projects_task_fields.py`, `test_projects_project_fields.py`.
  - The rules for code live in `acb_skills.addendum.RUN_SECTIONS`, never in `instructions.md`. D85 withholds `code_task`, `run_script` and `install_dependency`. Fences: `test_projects_agent.py`, `test_projects_sandbox_tools.py`.
  - In a `no_egress` run, a write refuses an `agent:` assignee before any write (`skill_projects.writes._refuse_agent_assignee`). Fence: `test_delegation_no_egress.py`.
  - A `GatewayRefusal` reaches the model as text through `skill_projects.refusals.refusals_as_text`, with a strict input model (`extra="forbid"`). Fence: `test_projects_agent_refusals.py`.
- agent-app-builder/ -- App Workshop builder (Custom Apps), on the Copilot SDK engine. It enforces the platform contract (window.cc only, see docs/app-workshop/README.md §4.0). ⚠️ D85 refuses its Copilot CLI shell, so its build stops until WS-43h runs it in the sandbox.

## `skills/` — importable tool packages
Capabilities (tools) an agent picks up, with no identity and no server. Python
packages imported as tool providers. (Distinct from the repo-root `skills/` folder,
which holds SKILL.md and subprocess-script skills for the DOE-v2 registry agents.)
- skill-my-tasks/ -- the My Tasks tools (capture, clarify, organize, engage) over the gateway lens routes
- skill-projects/ -- the Projects tool family for `projects-assistant` over the gateway `/projects` API. `manifest.py` is the route allowlist and the coverage record (see `agent-projects/` above)

## Conventions
- Each service, agent and skill has its own pyproject.toml and is a uv workspace member (`apps/services/*`, `apps/agents/*`, `apps/skills/*` — see root `pyproject.toml`)
- Moving a dir between groups means updating its `local_path` (registry and gateway `_AGENT_REGISTRY`). Python imports are by package name and do not change.
- Services communicate through Redis Streams (event bus)
- Gateway is the only internet-facing service. The one exception is `agent_edge`, which serves outside agents on its own host (D-EA-1, D-EA-2).
- All agents go through the unified `/agent/run/stream` endpoint (including orchestrator)
- The `/copilot/chat` endpoint in main.py stays for backward compatibility, but the workbench frontend no longer routes to it

## Child DOX Index
- apps/services/agent_edge/AGENTS.md
- apps/services/gateway/AGENTS.md
- apps/services/orchestrator/AGENTS.md
- apps/services/email_ingestion/AGENTS.md
