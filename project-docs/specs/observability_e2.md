# E2 — Observability & Debugging (interaction logging + run traces)

> **Status:** Phases 1–4 **shipped** (2026-07-03). Phase 5 (live activity feed)
> **shipped** (2026-07-09). Phase 6 + 6.1–6.6 (cross-app cost, per-agent
> correlation, access fix + durable history, office UX) and 6.8 (real Pixel Lab
> sprites + Avatar Studio) **shipped** (2026-07-09/10). E2 C+ → A.
> **§7 (WS-6): WS-6a + WS-6c shipped 2026-08-02** (incl. repair round 1) —
> decision D1's attribution stamp now exists as a substrate: `instance`
> completes the run context's four-tuple and `_emit_usage` forwards it, so every
> in-run model activation is attributed by (run_id, member, agent, instance)
> with no call-site changes. It reaches two readers: the live presence key
> (`/observability/active` + `/roster`, via `activity.refresh_run_presence`)
> and the daily cost rollup (`cost_summary().by_instance`). Two asymmetries are
> recorded, not hidden: the `phase="start"` **stream** event predates the
> partition, and a **delegated sub-run inherits its caller's** partition.
> **Still open: WS-6b** (carry the tuple to the v1_compat choke point),
> **WS-6d** (durable `llm_call` cost table), **WS-6e** (`agent_run` token
> columns), and the owner-gated Langfuse/OTel half (WS-6f–i). Nothing durable is
> written yet — the stamp lands on logs, the Redis feed and the Redis day-hash
> only.
> Deep tracing is still absent (BO-5).
> **Verified against code on 2026-08-01** (paths, `agent_run` token columns,
> uv.lock telemetry deps); `bind_run_context` key set re-verified 2026-08-02.
> **Module:** E2 (core_module_map.md).
> **Goal (user request):** log every agent/model interaction so an engineer can
> debug "error X happened with agent Y" after the fact (Phases 1–4); AND give
> operators a live, cross-app view of "whenever any agent or model is activated"
> across chats and every app (Phase 5); and eventually run feature tests against
> the live VPS.

## ⏩ RUNBOOK — the user reports "error X with agent Y" (start here)

Follow in order; each step is more granular than the last. Prefer the API path
(no SSH). See `observability-run-traces` memory for the exact copy-paste commands.

1. **Is the feature even up, and where's it broken?** On the VPS:
   `cd /opt/acb/app && uv run python scripts/feature_check.py` (add `--only chat_maf`
   / `chat_copilot` to isolate a runtime, `--json` for machine output). It drives
   the real endpoints, prints a pass/fail table, and **on failure prints the exact
   `run_id` + the next command to run.**
2. **Get the durable record + full trace (no SSH):** `GET /debug/runs?agent=Y&status=error&since_hours=24`
   → pick the `run_id` → `GET /debug/runs/{run_id}` for the full trace + traceback.
   (EXECUTIVE/AGENT-gated — traces hold message content.) `POST /debug/runs/{id}/flag`
   to preserve a successful run's trace before it's pruned.
3. **Correlated log stream (deepest, needs SSH):** `ssh acb@187.127.172.200` then
   `journalctl -u acb-gateway -o cat | grep '"agent": "Y"'` — every line for the
   run carries `run_id`/`thread_id`/`agent`/`user` and the `run_error` line carries
   the full `exc_info` traceback. Requires `LOG_FORMAT=json` in `/opt/acb/app/.env`
   (already set in prod). Durable DB fallback if journald has rotated:
   `docker exec acb-postgres psql … "SELECT … FROM agent_run WHERE agent_name='Y' AND status='error' …"`.

**Retention (know this before you look):** metadata + tool_summary are kept for
ALL runs; the full `trace` + `error_traceback` only for errored / cancelled /
flagged runs. A *successful* run you want to inspect must be `flag`ged first (or
reproduced with an induced error). Redis event stream is latest-run-only, 1h TTL —
`agent_run` is the thing that survives.

## The gap (audited 2026-07-03)

- **Logs weren't correlated or machine-parseable.** structlog was configured
  with `merge_contextvars` (capability present) but **nothing bound** run ids —
  and it rendered **colored console text**, not JSON. You couldn't grep "all
  logs for run X"; most lines (incl. `acb_llm.usage`) carried no run id.
- **The full run trace lived only in Redis, 1-hour TTL, latest-run-only.** An
  hour after an error, the detail was gone; only the UI-shaped folded
  `chat_message` survived. `audit_event` kept a coarse start/complete/error row
  with the exception **message only — no traceback**.
- **LLM telemetry wasn't attributed** to a run/agent/user.
- No trace-dump endpoint beyond the coarse `/agent/run/{id}/status`.

VPS access already existed (SSH from the dev machine → `journalctl -u
acb-gateway`, `docker exec acb-postgres psql`; Hostinger MCP for metrics), so
the missing piece was the *structured, queryable interaction record* — not
reachability.

## What shipped — Phases 1+2

### Phase 1 — Correlated, JSON-able logs (`packages/acb_common/acb_common/_log.py`)
- `bind_run_context(run_id, thread_id, agent, user, source, instance)` /
  `clear_run_context()` / `get_run_context()` bind the run fields into structlog
  contextvars. The executor binds them at the run boundary in `run_agent_stream`
  (and clears in the `finally`), so **every log line the run emits — across all
  tiers and injected tools on that context — automatically carries them**.
  Verified: `agent.step` and `acb_llm.usage` (which passes no ids) both come out
  tagged. `instance` (§7 WS-6a) is bound by a second, additive call once the
  agent config is loaded — see `executor._bind_run_instance`; binds are additive
  by design, so a later call never disturbs an earlier one.
- `configure_logging(level, json_logs=?)` + `LOG_FORMAT` env: `LOG_FORMAT=json`
  → `JSONRenderer` (one JSON object per line, greppable / aggregator-ready);
  default stays the colored console renderer for local dev. **Prod turns this
  on by adding `LOG_FORMAT=json` to the systemd `EnvironmentFile` (`.env`)** —
  no code change (it's read at `configure_logging` time).
- LLM usage attribution (`acb_llm/client.py::_emit_usage`): the `acb_llm.usage`
  log line is auto-correlated via contextvars; the optional `audit_event` row
  (`LLM_USAGE_AUDIT=1`) now also carries `run_id`/`agent`/`user` via
  `get_run_context()`, and its `actor` becomes `agent:<name>` so cost is
  attributable per agent.

### Phase 2 — Durable run-trace store (`agent_run` table)
- Migration `infra/postgres/50_agent_run_trace.sql`: one row per run —
  `run_id, thread_id, agent_name, user_id, model, status,
  started_at/ended_at/duration_ms, {prompt,completion,total}_tokens,
  tool_count, tool_summary(JSONB), error_message/error_type/error_traceback,
  trace(JSONB), flagged`. Indexed for the diagnostics queries (by agent, by
  status, by thread, by time; partial index on `status='error'`).
- `apps/services/gateway/gateway/run_trace.py`: `build_run_trace_row(...)` (pure,
  unit-tested) derives status from the events (RUN_ERROR → error, cancelled
  RUN_FINISHED → cancelled, else completed) and a lightweight
  `[{name,status}]` tool summary. **Retention policy (user choice): metadata +
  tool summary for ALL runs; the full `trace` (content + tool results +
  reasoning) ONLY for errored / cancelled / flagged runs** — you rarely debug
  successful runs, and this bounds storage + sensitive-data exposure.
  `record_run_trace(...)` upserts it (never raises).
- Wired at the run boundary in `chat_fold.persist_final_assistant_message`
  (both orchestrator paths) — it already replays the Redis event log and folds
  it, so the trace write reuses that same replay (one extra DB write, all data
  in hand). A run that produced no message still gets a row (itself a signal).
- Traceback capture (`executor.py`): the run-error handler now logs with
  `exc_info=True` (the `format_exc_info` processor renders the full stack, and
  with Phase-1 correlation that line carries the run id), and the
  `agent_run_error` audit payload now includes `error_type` + `traceback`.

## Debugging workflow this enables (today)
- **"Error X happened with agent Y"** → over SSH:
  `journalctl -u acb-gateway -o cat | grep '"agent": "Y"'` (once `LOG_FORMAT=json`
  is set) to see every correlated line incl. the traceback; and
  `docker exec acb-postgres psql -c "SELECT * FROM agent_run WHERE agent_name='Y'
  AND status='error' ORDER BY started_at DESC LIMIT 20"` for the durable record
  + full trace of each failure.
- **Cost / token attribution** → today this is the correlated `acb_llm.usage`
  log lines (per agent) plus the Redis day-rollup behind `/observability/cost`
  (Phase 6) — **not** the `agent_run` token columns.
  ⚠️ **Corrected 2026-08-01:** `agent_run.{prompt,completion,total}_tokens`
  exist in the migration and are SELECTed and served by
  `routes/debug.py` (list + detail) and `routes/observability.py` (`/runs`),
  but `run_trace.py::_persist_row` never lists them in its INSERT — so they are
  **always NULL**. The API reports a token count it has never written. Writing
  them is §7 item **WS-6e**.

## Phase 3 — Diagnostics API (`apps/services/gateway/gateway/routes/debug.py`)
Read-only, EXECUTIVE/AGENT-gated (a trace can hold message content):
- `GET /debug/runs?agent=&status=&user=&thread_id=&since_hours=&limit=` — list
  recent runs newest-first, all filters AND-combined, `limit` clamped [1,500].
  **Lean rows — no `trace` blob** (that's the detail view). Invalid status → 400.
- `GET /debug/runs/{run_id}` — full record: metadata + tokens + error +
  traceback + the folded `trace` (present only per the retention policy). 404 if
  unknown.
- `POST /debug/runs/{run_id}/flag` — set `flagged=true` to keep a run's trace.

This is what lets me query prod **without SSH** (and a UI panel could surface
failures). Extends the coarse `GET /agent/run/{id}/status` (`agent.py:1571`).
Verified end-to-end via TestClient against the live DB: filters, the lean-vs-
full split, retention honored through the API, the EXECUTIVE gate (employee →
403), and 404s.

## Phase 4 — VPS feature-check harness (`scripts/feature_check.py`)
One command, human-readable pass/fail table (or `--json`), CI/monitoring exit
code (non-zero on any fail):

    cd /opt/acb/app && uv run python scripts/feature_check.py

Checks: `health`, `debug_api` (the diagnostics API must itself be up),
`chat_maf` and `chat_copilot` (drive `/agent/run/stream` on each runtime, assert
`RUN_FINISHED` + text + no `RUN_ERROR`). For each run it then looks up the E2
**run trace** (`GET /debug/runs/{run_id}`) and prints the durable status — and on
failure prints the exact `GET /debug/runs/{id}` + `journalctl | grep <run_id>`
lines to debug. Shares the `CC_*` env config with the exhaustive
`tests/integration/test_chat_features.py` (which stays the CI-depth suite; this
is the fast operator "is it up, and where's it broken?" sweep). `--only <name>`
runs a single check.

Debugging loop is now fully self-serve: `feature_check.py` says WHAT broke +
the run_id → `GET /debug/runs/{id}` gives the full trace + traceback, no SSH
needed (SSH `journalctl` remains available for the correlated log stream).

## Tests
- `tests/unit/test_observability.py` (11): contextvar bind/clear (no leak),
  merge_contextvars present in the chain, run-trace row derivation (metadata-
  only on success, full trace on error/cancel/flag, cancelled/no-folded cases).
- Migration validated against the live local DB (idempotent re-run) + an E2E
  `_persist_row` write/read-back proving the errored-vs-successful retention
  policy. Full suite: 664 green, zero regressions.

## Phase 5 — Live activity feed (operator-facing, cross-app)
Phases 1–4 are an *engineer's post-hoc* view (logs + `agent_run` + `/debug`).
Phase 5 adds the *operator's live* view the user asked for: "see whenever any
agent or model is activated," across chat AND every app (email, tasks, …).

- **Global activity bus** (`packages/acb_common/acb_common/activity.py`): one
  process-wide Redis stream `cc:activity` that every activation publishes a
  small event to. `publish_activity(**fields)` is best-effort + non-blocking +
  never raises (a dropped event can never affect the run that emitted it — the
  durable record stays in `agent_run`). Presence keys `cc:activity:live:{run_id}`
  (TTL `LIVE_TTL_SECONDS`) track in-flight runs and self-heal if an "end" is
  lost. Cross-app coverage is automatic because the two publish sites are shared
  libraries:
  - **Agent activations** — the executor run boundary (`executor.py`
    `run_agent_stream`): a `kind="agent" phase="start"` event right after
    `bind_run_context`, and a `phase="end"` event (status + duration_ms) in the
    `finally`.
  - **Model activations** — `acb_llm._emit_usage` fires `kind="model"`
    (model/tier/tokens) on EVERY completion → chat, email automation, and tasks
    all covered with no per-app wiring.
  - **Source attribution** — `bind_run_context(..., source=)` adds `source` to
    the run-context contextvars (chat / email / tasks / webhook); model calls
    inside a run inherit it, so the feed shows which app triggered each call.
- **Live API** (`apps/services/gateway/gateway/routes/observability.py`, EXECUTIVE/AGENT-
  gated like `/debug`): `GET /observability/activity/recent` (backfill),
  `GET /observability/activity/stream` (SSE tail with heartbeats),
  `GET /observability/active` (runs in flight now).
- **UI** — new `/observability` page ("Live Activity", nav under Apps): backfills
  via `recent`, live-tails via `EventSource` on the SSE proxy, and polls
  `active` for the "running now" panel. Next proxies:
  `src/app/api/observability/{activity/recent,activity/stream,active}/route.ts`.
- **Tests** — `tests/unit/test_activity_bus.py` (7): event shaping + run-context
  inheritance + source binding + the best-effort/non-blocking publish contract
  (never raises on shaping or write failure). Full unit suite green (801).
- **Relation to Phase 3** — `/observability` is the *live signal* (ephemeral,
  Redis); `/debug` stays the *durable trace* (Postgres `agent_run`). Cost/token
  rollups + per-agent history are the next increment (read `agent_run` +
  `audit_event`; needs `LLM_USAGE_AUDIT=1`).

## Phase 6 — Cross-app coverage, live cost, agent office
Turns the live feed into the full "complete visibility" app the user asked for.

- **Universal app coverage (zero-touch).** Email and the task manager reach the
  model through `acb_llm.context.acompletion_with_fallback` (not `client.complete`,
  which covers agent runs), and it previously emitted nothing. It now calls
  `_emit_usage(...)` on success, and `_infer_app_source()` walks the stack for
  the caller's `gateway.routes.<app>` module → attributes the call to
  `email` / `tasks` / **any future app** with NO per-call-site changes. Agent
  runs keep their `source` from the run context (chat/…). Verified: email→email,
  tasks→tasks, a hypothetical `newapp`→newapp, orchestrator→None.
- **Live cost.** `_emit_usage` prices every call via litellm (`completion_cost`
  → `cost_per_token` fallback; unknown model → `None`, shown as "—", never a
  misleading $0) and puts `cost_usd` on the model activation. `activity._axadd`
  folds priced calls into a per-UTC-day Redis hash `cc:cost:{date}` (additive
  `total|` / `model|` / `source|` / `agent|` fields, ~45-day TTL) — an always-on
  rollup with NO per-call Postgres write (that stays the `LLM_USAGE_AUDIT` opt-in).
  `cost_summary(days)` reads it; `GET /observability/cost` serves per-day totals
  + by-model + by-app.
- **Roster / office.** `GET /observability/roster` merges the agent registry with
  the live presence set → each agent reports `working` / `idle`. Powers the
  8-bit office.
- **UI.** `/observability` is now a 3-view app: **Office** (an 8-bit room —
  each agent is a character at a desk that works/sleeps/errors live; a server
  rack lights up per active model; today's $ ticker), **Live feed** (stream +
  per-call cost), **Cost** (daily bars, by-model, by-app). Click any agent →
  drawer with recent runs + errors (proxied from `/debug/runs?agent=`). All
  dependency-free (CSS keyframes, no chart lib). New proxies:
  `api/observability/{cost,roster,runs}/route.ts`.
- **Tests.** +9 (cost pricing incl. unknown-model→None, source inference across
  apps, cost-rollup field parsing + aggregation, empty history). Full unit suite
  807 green. Frontend: `next build` clean (page + 6 API routes), `tsc`/eslint clean.
- **To make a NEW app observable:** nothing — if it calls models via
  `acompletion_with_fallback` (or runs an agent), it shows up attributed. Only
  add a `sourceClass()` colour in the page if you want a custom app badge.

### Phase 6.1 — review + fixes (agent/chat wiring)
A full trace of how activations connect to the CHAT agents surfaced three gaps,
now fixed:
- **Agent model calls + cost were invisible.** MAF agents (both the default
  orchestrator and named agents) don't call `acb_llm.complete` — their
  `OpenAIChatCompletionClient` POSTs to the gateway's own `/v1/chat/completions`
  (`routes/v1_compat.py`, the gateway binds :8080; no separate proxy), which
  called litellm directly and emitted NOTHING. Fixed: v1_compat now emits the
  model activation + cost on both the non-streaming and streaming paths
  (streaming rebuilds usage via litellm's `stream_chunk_builder` AFTER the
  stream, so the provider request + forwarded bytes are unchanged — zero risk to
  the agent stream). source="chat".
- **The orchestrator never showed as working.** The default chat
  (`main.py::copilot_chat`) runs the MAF agent via `protocol_runner.run`, NOT
  `run_agent_stream`, so the executor's start/end events never fired for it.
  Fixed: copilot_chat emits agent start/end (end via `run_detached`'s shielded
  `on_complete`, so it fires on every terminal outcome; a miss self-heals via the
  presence TTL).
- **The orchestrator wasn't in the roster.** It isn't a registered specialist,
  so `/observability/roster` omitted it. Fixed: the roster seeds "orchestrator"
  as a baseline entry AND merges any live-but-unregistered agent (sub-agents),
  so the primary agent is always on stage.
- Mem0's OpenAI-compat endpoint (`main.py`) also emits now (source="memory";
  defensive — normally shadowed by v1_compat's same-path route).
- Verified: `_usage_stats` reads litellm `ModelResponse` (`.get` present); +4
  tests incl. an end-to-end v1_compat TestClient drive (811 total green).
### Phase 6.2 — per-agent model correlation (v1_compat headers)
v1_compat runs as a bare HTTP request (no run context), so model calls could only
be tagged by app. Fixed for the primary agent: the orchestrator's
`OpenAIChatCompletionClient` is built with `default_headers={"X-CC-Agent",
"X-CC-Source"}` (`agents.py::_make_openai_client`), v1_compat reads them and
forwards `agent`/`source` into `_emit_usage`, so the orchestrator's model calls
+ cost now attribute to `agent="orchestrator"` (→ per-agent cost). **Fail-soft:**
no header → source="chat", no agent (prior behaviour) — it cannot regress.
Verified via TestClient (header present → agent tagged; absent → chat fallback).
- **Extended to native MAF named agents.** `agent-email-assistant` builds its own
  `OpenAIChatCompletionClient` (in-repo) → tagged with
  `default_headers` (agent="email-assistant"), so its model calls + cost now
  correlate too.
- **Still app-level (by design):** agents that run on `GitHubCopilotAgent`
  (task-manager, apis-config) reach the model through the Copilot SDK's BYOK
  provider, which doesn't expose a client-header hook here — their model calls
  show source="chat"/no agent, but their start/end lifecycle events DO carry the
  agent (via the executor), and they're never mislabelled as orchestrator.
  Copilot-SDK mutation traffic also lands in "chat". Per-agent model correlation
  for those needs an SDK-level header pass-through (upstream) — deferred.
- **Cleanup:** removed the permanently-shadowed duplicate `/v1/chat/completions`
  handler in `main.py` (v1_compat's registers first and is the full
  implementation); `/v1/embeddings` stays.

### Phase 6.3 — access fix + durable history ("it doesn't work")
Symptom: the page showed nothing for the operator while chat/email worked.
Root cause: the observability + `/debug` routes were `require_role(EXECUTIVE,
AGENT)`, but the SSO proxy only sends `X-User-Role: executive` when the email is
in `EXECUTIVE_EMAILS` (empty by default, and the operator's domain isn't the
`fracktal.in` default) — so every observability call 403'd and the proxies
degrade to empty → a silent blank page. chat/memory/tasks were unaffected (no
role gate).
- **Fix:** the live observability views (`recent`/`stream`/`active`/`roster`/
  `cost`/`runs`) now allow any AUTHENTICATED caller (EXECUTIVE + AGENT +
  EMPLOYEE) — they expose operational METADATA only. The full message-content
  trace stays EXECUTIVE-gated at `/debug/runs/{id}`.
- **Durable history (answers "can I see history of activity?"):** the live feed
  is the ephemeral Redis stream (~2000 events, lost on flush). Added
  `GET /observability/runs` over the durable `agent_run` table (lean rows:
  metadata + error message, no trace blob) + a **History** tab (durable,
  filter All/Errors) and repointed the per-agent drawer at it. This shows runs
  going back as far as retention — including data recorded since E2 Phase 2,
  before the live bus existed.
- Tests: +4 (employee-role 200 on runs/cost, DB-less degrade to [], bad-status
  ignored). Full unit suite 817. `next build` + tsc + eslint clean.
- **Background-agent coverage confirmed:** chat (orchestrator via `copilot_chat`
  + named agents via `run_agent_stream`) AND the email app (Reply Zero runs the
  `email-assistant` through `run_agent_stream`) both emit start/end + presence,
  so a background run is observable in the office/feed/active even after the
  browser closes (presence is server-side Redis). Email agent runs now set
  `payload["source"]="email"` so they're attributed to the email app, not "chat".

### Phase 6.4 — pixel-art office UX
The office view now renders a **procedural pixel-art character at a desk per
agent** (`src/app/observability/pixel.tsx`): deterministic palette per agent
(skin/hair/shirt/hair-style from a name hash), with three states —
**working** (green monitor, gentle bob, screen flicker), **sleeping** (dimmed +
desaturated, floating Zzz, dark monitor), **error** (red monitor, shake). When
**≥2 agents are working at once** (multi-agent orchestration) a separate **war
room** card appears with the collaborating agents seated at a **conference
table** (collaboration chatter dots + clickable name chips). Sprites are
generated inline SVG — no external assets (CSP-safe), crisp, theme-agnostic,
`prefers-reduced-motion` aware. **Swap seam:** `<PixelWorker src=…>` accepts a
real sprite PNG/data-URI per agent+state and keeps the same animation classes,
so hand-authored art drops in without touching the page. Verified by rendering
the sprites headless (Playwright/Chromium) before shipping; `next build` + tsc +
eslint clean.

> **Continuing the pixel-art art pipeline** (generating real sprites via Pixel
> Lab, wiring the swap seam, backend avatar config): see
> [`archive/pixel_art_office_pipeline.md`](archive/pixel_art_office_pipeline.md) — the handoff
> guide (ASSET SPEC, anchors, seam, Pixel Lab plan, TODOs). Pixel Lab is blocked
> by egress policy on the web environment; continue on a system with API access.

### Phase 6.5 — roomed, layered, configurable agent scenes
The office now composes each agent as a **layered scene inside a themed room**
(`src/app/observability/scene.tsx`), replacing the "floating box" desks:
- **Layered composition** (room → rug → chair → outfit → hands → head → face →
  hair → accessory → desk props → desk), each layer driven by an `AvatarConfig`.
- **Semantic + per-agent look:** `deriveAvatar(name)` maps the agent's role
  (coder / sales / planner / triage / reconciler / orchestrator) to a **room +
  outfit + accessory + props signature**, with per-agent variation (skin, hair
  style/colour) from a name hash — a brand-new agent gets a fitting avatar with
  zero config. `override` pins any field for when backend avatar config lands.
- **Real environment:** each agent sits in a room (wall + window/board/whiteboard
  + floor tiles + rug + desk + monitor(s) + props), not a void.
- **Animation:** hands **type** on the keyboard, eyes **blink**, screen
  **flickers**, mug **steams**; sleeping dims + `Zzz`; error shakes. All CSS,
  `prefers-reduced-motion` aware. Descriptor-based rects (stable keys) so no
  hydration churn.
- **Asset swap seam (for Higgsfield/hand-authored art):** the layer order +
  anchor grid is the contract; replace a layer's rects with `<image href=…>` at
  the same coords. The ASSET SPEC (cell, anchors, z-order, recolor, animation
  strips, manifest) is the mix-and-match contract for externally-generated
  sprites. `.mcp.json` carries a (auth-gated, inert until connected) `higgsfield`
  http entry for when we generate real assets to that spec.
- Verified via headless render (Playwright/Chromium) of the composed scenes;
  `next build` + tsc + eslint clean.

### Phase 6.6 — office polish (animations, war room, per-agent cost, Lucide)
- Richer animation: head bobs/tilts while typing (grouped, recursive renderer);
  sleeping slumps + slow-breathes with a floating Zzz; hands alternate faster.
- `WarRoomScene` — multi-agent collaboration is now a proper conference ROOM
  (walls, floor, presentation screen of shared work, table) with the working
  agents seated around it + chatter, replacing the bare table.
- Per-agent cost: `cost_summary` returns `by_agent`; the agent drawer shows that
  agent's spend (window + calls). (Per-agent attribution is exact only where the
  X-CC-Agent header is set — orchestrator + email-assistant today.)
- Live feed / header / tabs / office / server-rack / empty states use Lucide
  icons instead of emoji, consistent with the app theme.

### Phase 6.8 — real pixel-art sprites + Avatar Studio (2026-07-10)
The office is now **real pixel art**, and each agent's look is **customizable**.
Pixel Lab turned out to be reachable from the operator's own machine (the egress
403 was only on the web env), so the whole pipeline in `archive/pixel_art_office_pipeline.md`
was unblocked and shipped:
- **Real role cast.** `scripts/gen_office_sprites.py` generates a transparent,
  waist-up pixel-art bust per role (coder / sales / planner / triage / reconciler
  / orchestrator / default) via Pixel Lab `generate-image-pixflux`, trims the
  margins, and embeds them as data-URIs in `sprites.generated.ts` (CSP-safe, no
  external asset host, ~156 KB).
- **Seam.** `scene.tsx` gains `spriteFor(name, config)` (per-agent pinned sprite →
  role sprite → null). When a sprite resolves, `AgentScene` renders it as an
  `<image>` inside the themed room with a contact shadow and the working/idle/error
  animations (breathe/dim+Zzz/red-shake); with no sprite it falls back to the
  procedural rects — so a brand-new agent is never broken. Validated headless.
- **Backend override layer.** `agent_avatars` table (migration 64) keyed by agent
  name — covers built-ins like `orchestrator`, not just `dynamic_agents`. New
  endpoints on the observability router: `GET /observability/avatars`,
  `PUT/DELETE /observability/avatars/{name}`, and `POST /observability/avatars/
  generate` (calls Pixel Lab with `PIXELLAB_API_KEY` held **server-side** — the
  browser never sees the key; degrades 503 when unset). Writes are gated to any
  authenticated caller (the Phase 6.3 lesson: EXECUTIVE-gating silently 403s the
  operator). `/roster` merges `avatar:{config,sprite}` so every viewer sees the
  pinned look; the office applies it as a `deriveAvatar` override.
- **Avatar Studio.** New tab on `/observability`: agent picker · live `AgentScene`
  preview (toggle working/sleeping/error) · look controls (skin, hair style+colour,
  outfit type+colour, accessory, room, wall, desk props) · a "Generate with Pixel
  Lab" panel (prompt → sprite → pin). `avatar-studio.tsx`; 3 Next proxies under
  `api/observability/avatars/`. Keyed child editor seeds from the stored override
  via `useState` initializers (no set-state-in-effect).
- **Tests** — +4 unit (name-regex validation, `_load_avatars` DB-down → {}
  degradation, generate 503-without-key / 400-empty-desc). `next build` + tsc +
  eslint clean; full observability+activity unit suites green (27).
- **Still procedural/whole-character** (not per-layer): the sprites are complete
  busts, not mix-and-match layers, so recolour/animation-strip/room-tileset (the
  §4 ASSET SPEC) remain the future upgrade; the seam is ready for them.

### §6.7 — Observability plumbing: landscape review (recommendation, NOT built)
> This is a **recommendation memo**, not a ticket list. The dispatchable
> tickets derived from it — plus the D1 attribution stamp and the durable cost
> table — live in **[§7 below](#7-ws-6--open-work-dispatchable)**. `work_plan.md`
> cites "§6.7" for WS-6; read §7 for what to actually build.

Where our bespoke layer (activity bus + `agent_run` + cost rollup + the office
UI) is a **live operator** surface no off-the-shelf tool provides, DEEP tracing
(nested spans: run → tool → LLM call, token/cost per span, replay, evals) is
where standard tools win. Key finding: **Langfuse is already provisioned in
`infra/docker-compose.yml` (with `.env` key *slots*) but WIRED TO NOTHING**, and
the LiteLLM OTel callback is gated off (`OTEL_EXPORTER_OTLP_ENDPOINT` unset).

**Dependency reality (re-verified 2026-08-01 — read this before estimating):**
`opentelemetry-api` / `-sdk` / `-semantic-conventions` ARE in `uv.lock`, but
`opentelemetry-exporter-otlp` and `langfuse` are **absent from `uv.lock`
entirely**, and no `langfuse` import exists anywhere in Python.
`acb_llm/client.py::_init_telemetry` appends litellm's `"otel"` callback only
when `OTEL_EXPORTER_OTLP_ENDPOINT` is set — so setting that env var **today buys
nothing**: the exporter it needs isn't installed. Both items below therefore
start with a dependency add, and both are OWNER-GATE (see §7).
Container: `infra/docker-compose.yml:97-112` (`langfuse/langfuse:2`,
`profiles: ["obs"]`, dormant). Keys: `.env.example:63-65` — `LANGFUSE_HOST` set,
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` **empty placeholders**.

Highest-leverage, low-effort wins (not yet done):
1. **Wire the dormant Langfuse** — LiteLLM has a native `langfuse` callback; set
   `litellm.callbacks=["langfuse"]` gated on `LANGFUSE_*` keys (mirror
   `_init_telemetry`). Every model call → a nested trace, free, with token/cost
   analytics + eval hooks. Complements, doesn't replace, the live bus.
2. **OTel GenAI semantic conventions** as the wire format (spans with `gen_ai.*`
   attributes) → backend-agnostic; point the already-present OTel callback at a
   collector/Langfuse OTLP endpoint.
3. **Correlate** our `run_id` ↔ OTel/Langfuse `trace_id` so the office/drawer can
   deep-link "open trace in Langfuse". Emit spans at agent/tool boundaries, not
   just model calls.
Split of responsibility: bespoke = live glanceability; Langfuse/OTel = deep
post-hoc tracing + evals + analytics.

### What v1_compat IS (not legacy)
`routes/v1_compat.py` is the gateway's **OpenAI-compatible LLM egress** — the
single `/v1/chat/completions` every agent runtime (MAF `OpenAIChatCompletionClient`,
Copilot SDK, Mem0) POSTs through. It is NOT legacy; it deliberately REPLACES a
standalone LiteLLM proxy process: the gateway serves the OpenAI wire protocol
itself, reading provider keys from encrypted Postgres, resolving tier aliases
(tier-fast/balanced/powerful → concrete models), sanitising messages for provider
quirks (e.g. DeepSeek null-content), and applying prompt-cache breakpoints — "THE
choke point every agent runtime POSTs through". The name ("compat") undersells it
(it's the LLM gateway, not a throwaway shim). No refactor needed for correctness;
optional cleanups: (a) the shadowed duplicate `/v1/chat/completions` in `main.py`
(Mem0) could be removed, (b) a clearer name like `llm_gateway.py` — both cosmetic.

## 7. WS-6 — open work (dispatchable)

> Added 2026-08-01. This is the ticket list `work_plan.md` WS-6 dispatches
> against ("Observability wiring + attribution — BO-5 + decision D1"). §6.7 above
> is the recommendation memo it derives from; the durable cost table used to be
> a one-clause aside in the Status changelog; **decision D1's attribution stamp
> appeared in no spec at all before this section.** Every item — AGENT-SAFE and
> OWNER-GATE alike — states a done-when and a gate label. An agent must refuse
> OWNER-GATE items and say so.
>
> **Read [Open questions](#open-questions--decide-before-or-while-building-do-not-decide-silently) before starting.** Four things this
> section deliberately does not settle bear on WS-6b/6d; two of them change what
> a correct implementation looks like.
>
> Ticket IDs are **lettered** (`WS-6a`…`WS-6i`) deliberately: this doc already
> has *phases* 6.1–6.8, and numbering the tickets would reuse those IDs for two
> different things in one file (R2).
>
> **Decision D1 (`work_plan.md` §3), the thing this section exists to
> implement — quoted in full, parentheticals included:**
> *"Stamp every LLM call at the gateway choke points with (run_id, member_email,
> agent, instance). Per-room (multiplayer §5.3), per-instance (agent-kinds §9.4),
> per-member and per-Center views are all rollups of that one record.
> Owner: WS-6."*
>
> **This slice produces RECORDS ONLY.** Budget *enforcement* (per-member caps,
> 429s, degrade-to-read-only) is **WS-16** per D2/D8 — do not build it here even
> though `routes/apps/runtime.py` shows how. WS-16 is gated on this slice.

### Where we actually are (table re-verified 2026-08-02, after WS-6a/6c)

| D1 field | State today | Anchor |
|---|---|---|
| `run_id` | Bound in the run context, inherited by model events; **still dropped** before v1_compat | `packages/acb_common/acb_common/_log.py` `_RUN_CONTEXT_KEYS` |
| `member_email` | Bound as `user` in the run context, inherited; **still dropped** before v1_compat | same |
| `agent` | ✅ carried, via `X-CC-Agent`, and inherited in-run | `routes/v1_compat.py:425` |
| `instance` | ✅ **bound in the run context (WS-6a)**, inherited onto model activations (WS-6c), patched onto the live presence key (so `/observability/active` + `/roster` carry it) and folded into the daily cost rollup as `by_instance`; **not** carried to v1_compat | `_log.py` `_RUN_CONTEXT_KEYS`, `executor._bind_run_instance`, `activity.refresh_run_presence`, `activity._record_cost` |

`_RUN_CONTEXT_KEYS` is now
`("run_id", "thread_id", "agent", "user", "source", "instance")`.
**The remaining hole is v1_compat, and it is WS-6b's** — it reads only
`x-cc-agent` and `x-cc-source` (`routes/v1_compat.py:425-426`) and forwards
them into `_emit_usage` (`:573` streaming, `:594` non-streaming); run_id,
member and instance are lost because v1_compat is a bare HTTP request with no
inherited context. `_emit_usage` now *accepts* all three (WS-6c) — nothing
passes them yet.

**Do not invent a second instance key.** The vocabulary already exists and is
authoritative: `_resolve_agent_instance()`
(`apps/services/orchestrator/orchestrator/executor.py:917`) returns
`''` (shared) | `u:<email>` | `t:<team>`, per migration `136_agent_blob_instance.sql`.
Reuse it verbatim.

**Durable cost today:** none for agents. Cost lives in Redis day-hashes only
(`acb_common/activity.py::_record_cost`, `cc:cost:{YYYY-MM-DD}`, ~45-day TTL,
gone on a Redis flush) plus the opt-in `audit_event` row behind
`LLM_USAGE_AUDIT=1` (`acb_llm/client.py:559`). **The precedent to generalise is
`app_audit`** (`infra/postgres/114_custom_apps.sql:80-93` — `tokens_in` (:89) /
`tokens_out` (:90) / `cost_usd NUMERIC(12,6)` (:91) / `model` (:92) / `at`
(:93)), already read by
`routes/apps/runtime.py::_month_ai_usage` (:107). Model the new table on it.

### Slice IN — the agent-safe items

The five items below are **AGENT-SAFE**: **a–c** build the stamp, **d** is
where it lands durably, **e** closes the lie the API already tells.

**Shipped 2026-08-02 (one PR): WS-6a + WS-6c — the substrate.** They were split
out and dispatched alone because they change no wire protocol, no schema and no
auth surface: the run context gains a key and the emitter gains three optional
parameters that nothing passes yet. **WS-6b, WS-6d and WS-6e remain open** —
they are the consumers, and each still needs a decision recorded before it is
dispatchable (6b: where `member_email` may come from, per the identity trap;
6d: how that distinction is spelled in the schema; 6e: the upsert's
token-column semantics).

---

**WS-6a — Bind `instance` into the run context.** ✅ **SHIPPED 2026-08-02**
`_RUN_CONTEXT_KEYS` and `bind_run_context(...)` gained an `instance` field
(same "only non-empty values are bound" rule; `clear_run_context` stays
symmetric because it unbinds the key tuple), bound from
`_resolve_agent_instance()`'s value for the run.
- ⚠️ **Ordering trap — this was the whole difficulty of the item.** In
  `run_agent_stream` the `bind_run_context` call sits near the top, but
  `_agent_instance` is not resolved until well after `load_agent` returns,
  because `_resolve_agent_instance` needs `loaded.config`. One bind can never
  carry the instance.
  **Resolved as: a second, additive bind** — `executor._bind_run_instance()`,
  issued at the resolve site. The first bind stays early so failures *during*
  load are still correlated, and only one key moves. (`bind_run_context` is
  additive by contract, documented in its docstring — that property is now
  load-bearing, so don't "optimise" it into a replace.)
- **Known asymmetry #1 — start vs end (deliberate, and its presence-key
  consequence is FIXED).** The agent `phase="start"` activity event is
  published before the load, so it carries no `instance`; the `phase="end"`
  event (still emitted before `clear_run_context()` in the `finally`) inherits
  it. That is honest — at start time the partition genuinely is not yet known —
  but anything joining *stream* events by run must not assume both ends carry
  it.
  ⚠️ That asymmetry was not merely cosmetic: `_axadd` writes the presence key
  `cc:activity:live:{run_id}` **from the start event's body**
  (`activity.py`, the `phase == "start"` branch), so `active_runs()` — and
  therefore `GET /observability/active` and `GET /observability/roster`, the
  office view — could never carry `instance` for **any** run.
  **Fixed by patching the presence snapshot**, not by re-publishing the start
  event: `acb_common.activity.refresh_run_presence(run_id, **fields)` merges
  fields into the existing key (`SET … XX`, TTL refreshed) and
  `executor._bind_run_instance(instance, run_id)` calls it right after the
  bind. Rationale: presence is a *snapshot*, so overwriting it is invisible
  and idempotent, whereas a second `start` **stream** entry is visible to
  every consumer (SSE feed, office) and reads as a second activation. A miss
  is a no-op by design — a patch that arrives after the end event must not
  resurrect a finished run. Pinned by
  `tests/unit/test_activity_bus.py::test_presence_carries_the_instance_after_
  the_late_bind` (asserts through `active_runs()` and that no stream entry is
  added), `::test_presence_refresh_never_resurrects_a_finished_run`, and
  `tests/unit/test_instance_wiring.py::test_the_late_bind_also_patches_the_
  live_presence_key`.
- **Known asymmetry #2 — delegation inherits the caller's partition.**
  `_run_sub_agent_streaming` (`executor.py:500`) neither resolves nor binds an
  instance, and it cannot unbind the caller's: `bind_context` is additive, so
  clearing would need `unbind_contextvars("instance")` + restore around the
  sub-run. Consequence: when a **personal** agent delegates to a **shared**
  one, the sub-run's `phase=start/end` events and every `_emit_usage` inside it
  carry `instance=u:<caller>` — while that same sub-run's `agent_blob` rows
  carry `instance=''`, because the sub-run uses `loaded.agent_dir` directly
  (`:644`) with no `_resolve_effective_agent_dir` call.
  **So the stamp identifies the partition of the run that RESOLVED it, not
  necessarily the partition that run's artefacts live in.** Stated where it
  can mislead: `_log.py`'s `_RUN_CONTEXT_KEYS` comment,
  `_bind_run_instance`'s docstring, `packages/AGENTS.md` and
  `apps/services/orchestrator/AGENTS.md` §9.
  **Consequence for WS-6d:** the durable `llm_call` row's `instance` is the
  *billing/caller* partition, which is the right subject for a per-member cap
  but is **not** a foreign key onto `agent_blob.instance` — do not model it as
  a join and do not reconcile the two tables on that column. Giving delegation
  its own partition is a separate ticket (it needs the unbind/restore, and a
  decision about whether a delegated shared agent should even be attributed to
  the caller's tenant); it is **not** in WS-6d.
- 🚧 **Scope line (deliberate, do not "finish" it casually).** WS-6a covers
  `run_agent_stream` **only**. The 2026-08-01 draft said "the other resolve
  site (`executor.py:1728`) needs the same treatment" — it does not.
  Before this PR `bind_run_context` had exactly ONE call site in the whole
  repo, in `run_agent_stream`; **`run_agent` binds no run context at all**, so
  stamping it would mean *adding* correlation to a path that never had it — a
  behaviour change (new fields on every log line that path emits, and a new
  clear obligation in its `finally`) outside this ticket. It is a real gap:
  batch runs are unattributed today, and they were unattributed before WS-6.
  It belongs to whoever gives `run_agent` a run boundary.
- **Done when:** `get_run_context()` inside a run of an instanced agent returns
  `instance` equal to `_resolve_agent_instance(config, name, actor)` for that
  run, and `''` for a shared agent (absent key, not the string `"''"`);
  `clear_run_context()` leaves no `instance` behind (extend the existing
  no-leak assertion in `tests/unit/test_observability.py`).
  ✅ Met: `tests/unit/test_instance_wiring.py::test_the_run_context_carries_
  the_key_the_executor_resolved` + `::test_a_shared_run_stamps_no_partition`
  (executor composition), `tests/unit/test_observability.py::test_instance_is_
  bound_and_readable_like_every_other_run_field`, `::test_shared_agent_binds_
  no_instance_key_at_all`, `::test_a_second_bind_tops_up_instance_without_
  disturbing_the_first`, and the extended `::test_clear_removes_context_no_leak`.
- 🔒 **Twin-tuple drift gate.** `_log._RUN_CONTEXT_KEYS` (what a run *binds*)
  and `activity._INHERIT` (what an event *copies* when its emitter omits it)
  must be extended together or a new key half-lands — bound onto every log
  line but absent from every activity/cost event, or the reverse. Both
  AGENTS.md files said so in prose; it is now enforced by
  `tests/unit/test_observability.py::test_inherit_and_run_context_keys_match`,
  whose failure message names the offending keys and what to do (same style as
  `tests/unit/test_skills_registry.py`'s drift gates).
- 📊 **Interim aggregate — `instance` is a cost-rollup dimension.** Before
  this, `instance` existed only on raw stream entries, which `STREAM_MAXLEN`
  bounds to ~2000 events: "what did alice's personal agents cost today" was
  unanswerable until WS-6d. `_record_cost` now folds an
  `instance|<key>|cost`/`|calls` field into the same `cc:cost:{day}` hash it
  already writes for `model`/`source`/`agent`, and `cost_summary()` surfaces
  it as `by_instance` — additive, same route (`GET /observability/cost`), same
  response shape as the existing `by_agent`. Pinned by
  `test_activity_bus.py::test_cost_rollup_folds_the_tenant_partition` (write
  side, incl. "a shared run writes no `instance|…` field at all") and the
  extended `::test_cost_summary_aggregates_daily_rollups` (read side).
  **This does not close WS-6d.** It is the *live* surface only: bounded by
  `COST_TTL_SECONDS` (~45 days), lost on a Redis flush, no per-call row, and
  it inherits delegation asymmetry #2 above. The durable per-completion record
  remains WS-6d's.
  ↳ *Exposure, stated:* a `u:<email>` partition key is a member identifier, and
  `GET /observability/cost` is open to any AUTHENTICATED caller (Phase 6.3).
  That is not a new class of exposure — the same route's sibling feed already
  carries `user` (the member email) on every event, and `by_agent` is already
  served there — but Q3's "no retention/PII policy" gap now covers this
  dimension too, at a 45-day TTL.
- **Files:** `packages/acb_common/acb_common/_log.py`,
  `packages/acb_common/acb_common/activity.py`
  (`refresh_run_presence`, `_record_cost`, `cost_summary`),
  `packages/acb_common/acb_common/__init__.py`,
  `apps/services/orchestrator/orchestrator/executor.py`.

**WS-6b — Carry the four-tuple to the v1_compat choke point.** AGENT-SAFE
Propagate `(run_id, member_email, agent, instance)` to
`/v1/chat/completions` as `X-CC-Run` / `X-CC-User` / `X-CC-Instance` alongside
today's `X-CC-Agent` / `X-CC-Source`; read them fail-soft in
`_handle_chat_completions` next to the existing `:425-426` reads —
**subject to the identity constraint below, which changes where `member_email`
may come from.**
- ⚠️ **Header trap — static `default_headers` cannot work here.**
  `_make_openai_client` (`apps/services/orchestrator/orchestrator/agents.py:392-418`)
  sets `default_headers` at client **construction** time. That is fine for
  `agent` (constant per client) but wrong for `run_id`/`member`/`instance`,
  which vary per request. It also misses two clients built independently in-repo:
  `apps/agents/agent-email-assistant/agents.py:1957` and
  `apps/agents/agent-whatsapp-assistant/agents.py:483`. **A per-request httpx
  event hook that reads the run contextvar at send time covers all three
  construction sites at once; static headers cover none of the varying fields.**
  This is an engineering call, not an owner call — but record the choice in the
  PR. (The orchestrator client is rebuilt per request — `main.py` calls
  `build_orchestrator_agent(with_history=False)` at **four** sites,
  `apps/services/gateway/gateway/main.py:366`, `:589`, `:1341`, `:1392` — so a
  construction-time stamp *would* accidentally work on the orchestrator path and
  nowhere else. Do not be misled by that, and do not stamp only one of the four.)
- 🔐 **Identity trap — `X-CC-User` is unauthenticated, and WS-16 inherits it.**
  A client-supplied `X-CC-User` read fail-soft is a **caller-asserted identity**,
  not an authenticated one. `/v1/chat/completions` is guarded by
  `require_llm_api_auth` (`packages/acb_auth/acb_auth/deps.py:303-327`, wired at
  `routes/v1_compat.py:655` as `_auth = [Depends(require_llm_api_auth)]`), which
  validates a **shared** token and establishes **no per-user identity** — it
  accepts either `LITELLM_MASTER_KEY` or the service token and returns nothing
  (`-> None`; it hands back no `UserContext`, unlike `get_current_user`).
  That key is handed out on purpose: `deps.py:66-73` says of it *"This one is
  handed OUT — every agent's BYOK client presents it… Treat it as semi-public
  within the deployment: anything it authenticates, an agent can do,"* and its
  shipped default is the shared dev string `.env.example:35`
  (`LITELLM_MASTER_KEY=sk-local-dev-change-me`).
  This repo has already paid for trusting a bare user header once: the sibling
  guard's docstring (`deps.py:281-284`) records that SSO `X-User-*` headers are
  deliberately **NOT** accepted there because *"they are spoofable without the
  Next.js proxy"*, and `deps.py:239-254` records that trusting a bare
  `X-User-Email` *"was a full cross-account auth bypass."*
  **The consequence, stated plainly:** WS-6b + WS-6d build the durable
  per-member cost record, and that record is exactly what **WS-16**'s per-member
  budget caps are gated on (`work_plan.md` §2 WS-16 "🟡 WS-6", and D2 "per-member
  monthly caps ship first"). Sourcing `member_email` from a forgeable header
  bakes a budget-evasion vector into the schema WS-16 inherits — any holder of
  the `/v1` key (i.e. every agent BYOK client) could spend against **another**
  member's cap, or attribute its spend to a member who does not exist and so
  consume no cap at all.
  **Therefore, binding on this slice:**
  1. `member_email` **must be established server-side** — derived from the run
     context the gateway already owns, or correlated after the fact via
     `run_id` (which `agent_run` already binds to a `user_id`). A server-side
     join on `run_id` is the cheap correct answer and needs no new trust.
  2. If a header path is used at all (e.g. as a fast path before the join), the
     value is persisted as **untrusted / self-asserted** — distinguish it in the
     row (a nullable `member_email_asserted`, or a `member_source` discriminator
     of `context` vs `header`) — and it **must never** be the basis for budget
     enforcement. WS-16 reads only server-established members.
  3. Do not widen `require_llm_api_auth` to fix this. It is documented as
     deliberately weaker than `require_internal_auth` (`deps.py:312-316`);
     changing it is an auth-behaviour change and therefore OWNER-GATE.
- **Done when:** a chat run through the MAF orchestrator produces an
  `_emit_usage` call carrying non-empty `run_id`, `member_email`, `agent`, and
  an `instance` equal to `_resolve_agent_instance()`'s value for that run
  (`''` for a shared agent). Note the coverage hole in **Q2** below: this
  criterion is satisfiable while `task-manager` and `apis-config` are still
  entirely unstamped — passing it is not evidence of full coverage.
- **Done when (identity, non-negotiable):** a bare `/v1/chat/completions` caller
  holding only the `/v1` key and sending a forged
  `X-CC-User: someone-else@example.com` **must not** produce a durable row
  attributed to that member as an enforceable subject — either the value is
  ignored in favour of the server-established member, or it is stored flagged
  self-asserted and excluded from any per-member cost rollup a budget could read.
  Pin it as a test alongside the fail-soft pin below.
- **Done when (fail-soft, non-negotiable):** with **every** `X-CC-*` header
  stripped, `/v1/chat/completions` returns a byte-identical response and still
  emits an event with `source='chat'` and null agent/run/member — no regression.
  Mirror the existing pin `test_v1_compat_without_header_falls_back_to_chat`
  in `tests/unit/test_v1_compat_telemetry.py`.
- **Files:** `apps/services/gateway/gateway/routes/v1_compat.py`,
  `apps/services/orchestrator/orchestrator/agents.py`,
  `apps/agents/agent-email-assistant/agents.py`,
  `apps/agents/agent-whatsapp-assistant/agents.py`.

**WS-6c — Widen `_emit_usage` to accept and forward the stamp.**
✅ **SHIPPED 2026-08-02**
`_emit_usage(model, tier, response, source=, agent=)` gained `run_id=`,
`member=`, `instance=`, deferring to `get_run_context()` so in-run callers
(`acompletion_with_fallback`, agent runs) need no call-site change and only
an out-of-run choke point passes them explicitly.
- ⚠️ **This ticket was half-shipped before it was written — extend, don't
  rebuild.** `acb_common/activity.py`'s `_INHERIT` tuple already copied
  `agent`/`user`/`thread_id`/`run_id`/`source` from the run context onto any
  event whose caller omitted them, so "zero changes at the call site" was
  **already true for four of the five fields**. The genuine work was therefore
  small: add `instance` to `_INHERIT` (which needs WS-6a to have something to
  inherit) and widen the signature so a caller with no run context of its own
  can supply the tuple. `member` maps onto the feed's existing `user` field —
  no second vocabulary.
- **Done when:** an in-run model call is attributed with the full four-tuple
  with **zero** changes at its call site; the existing activity-event shape
  gains fields but breaks no consumer (`/observability/activity/*` and the
  office UI keep rendering).
  ✅ Met: `tests/unit/test_llm_usage_telemetry.py::test_in_run_call_is_
  attributed_with_no_call_site_change` asserts all four fields on the event as
  it reaches `_axadd` after a call with **no** attribution kwargs;
  `::test_existing_source_and_agent_kwargs_are_untouched` pins the pre-existing
  caller shape; `::test_shared_agent_run_emits_no_instance_field` and
  `test_activity_bus.py::test_build_event_for_a_shared_run_carries_no_instance`
  pin that a shared run's event is byte-identical to its pre-WS-6 shape.
  Consumers: the gateway serves these events as raw dicts (no response model)
  and `workbench/control_plane/src/app/observability/page.tsx:39` types them
  with a structural TS `interface` + a `JSON.parse(...) as ActivityEvent` cast,
  so an added optional field is inert at runtime.
- **Preserve the null-cost contract:** `_compute_cost`
  (`acb_llm/client.py:480`) returns `None` for an unpriced/stub-registered
  model and the UI shows "—". **Never coerce that to `0`** — a misleading $0 is
  worse than an unknown. Pin it.
  ✅ Met and pinned end-to-end: `::test_unpriced_model_publishes_unknown_cost_
  never_zero` — the published event carries no `cost_usd` at all (`None` is
  dropped by `_build_event`), so `_record_cost` skips it rather than folding a
  fake $0 into the daily rollup.
- **Not done here:** having v1_compat actually *pass* the stamp is **WS-6b**
  (header propagation + the identity constraint), which is held. This ticket
  builds the socket, not the plug.
- **Files:** `packages/acb_llm/acb_llm/client.py`,
  `packages/acb_common/acb_common/activity.py` (`_INHERIT`).

**WS-6d — Durable `llm_call` cost table (the deferred durable cost table).** AGENT-SAFE
One durable row per completion, modelled on `app_audit`. **R1: find the next
free migration number by listing `infra/postgres/` at build time — do not write
a literal number into this spec or into a filename you guessed.** The file must
be idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`):
deploy re-applies every migration file on every deploy.
- Columns: `run_id`, `member_email`, `agent`, `instance`, `source`, `model`,
  `tier`, `tokens_in`, `tokens_out`, `cost_usd NUMERIC(12,6) **NULL**`, `at`.
  `NULL`, not `app_audit`'s `DEFAULT 0` — see the null-cost contract in WS-6c.
- 🔐 **`member_email` carries WS-6b's identity constraint into the schema.**
  Whatever WS-6b decides, this table must make "server-established member" vs
  "caller-asserted member" *readable from the row* (see WS-6b's identity
  trap) — WS-16 will build per-member caps on these
  rows, and a rollup that cannot tell the two apart is a budget-evasion vector.
  This is a schema decision, so getting it wrong here is expensive to undo.
- ⚠️ **`instance` is the CALLER's partition, not the artefact's.** Read
  delegation asymmetry #2 under WS-6a before designing the column: a delegated
  sub-run stamps the caller's `instance` while writing `agent_blob` rows under
  `''`. So model this column on `agent_blob`'s *vocabulary*, but do not treat
  it as a foreign key onto `agent_blob.instance` and do not build a
  reconciliation that joins the two on it.
- Index for the rollups the D1 decision names: at least `(at)`,
  `(member_email, at)`, `(agent, at)`.
- The Redis day-rollup stays as-is (it is the *live* surface); this is the
  durable record behind it. Do not replace one with the other.
- **Done when:** `SUM(cost_usd) GROUP BY member_email` over `llm_call` for a UTC
  day reconciles to within rounding of `cost_summary()`'s `totals.cost` for the
  same day.
- **Done when:** the migration re-runs clean against an already-migrated DB
  (idempotency, not optional).
- **Done when:** an unpriced model writes `cost_usd IS NULL`, never `0`.
- **Files:** `infra/postgres/<next free>_llm_call.sql`,
  `packages/acb_llm/acb_llm/client.py` (write site).

**WS-6e — Write `agent_run.{prompt,completion,total}_tokens`.** AGENT-SAFE
Closes the correction recorded above under *Debugging workflow this enables*:
the columns exist in `50_agent_run_trace.sql`, `routes/debug.py` (:139-151) and
`routes/observability.py` (:288, :308) SELECT and serve them, and
`run_trace.py::_persist_row` (the INSERT at :169-217) never lists them — so the
API has always reported a token count that was never written. Backfill them at
the run boundary from the run's own completions.
- **Done when:** after a completed run, `GET /debug/runs/{run_id}` returns a
  non-null `total_tokens` (today: always null).
- **Done when:** the upsert's `ON CONFLICT … DO UPDATE` handles tokens the same
  way it handles the other late-arriving fields — a second write must not null
  out a value the first write recorded.
- **Files:** `apps/services/gateway/gateway/run_trace.py`
  (`build_run_trace_row` + `_persist_row`), caller in `chat_fold`.

### Slice OUT — OWNER-GATE, do not build in this slice

⚠️ **Citation, honestly:** `work_plan.md` §6 enumerates force-push/history
rewrite, credential rotation, the enforcement flips (`ACTION_BROKER_ENFORCE`,
`AGENT_PERMISSION_MODE`, `MEM0_ENABLED`, `GRAPHITI_ENABLED`,
`WHATSAPP_ENRICHMENT`, `SKILLS_FAIL_CLOSED`, `SKILLS_INDEX_ONLY`), the
bot-account/Meta items, live-DB one-offs, and any deploy that changes auth
behaviour — **it does not name the observability flags below.** They are gated
by the same principle (a prod credential/env action with a cost, privacy, or
auth consequence), and §6 should gain them when the board is next updated; that
edit belongs to `work_plan.md`'s owner, not to a WS-6 PR. Until then the gate
labels *in this section* are the binding instruction: an agent must **refuse**
these four and report which gate.

**WS-6f — Wire the dormant Langfuse.** 🔒 **OWNER-GATE**
Gate: requires generating Langfuse project credentials and populating the empty
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` slots in prod `.env`, plus
bringing up the `--profile obs` container. Both are credential + prod-env
actions. Also requires adding `langfuse` to `uv.lock` (absent today).
- Shape when cleared: gate `litellm.callbacks=["langfuse"]` on the keys being
  present, mirroring `_init_telemetry`'s env gate.
- **Done when (post-clearance):** with `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` set
  and the `--profile obs` container up, one chat run produces a Langfuse trace
  containing that run's model call(s) with token counts; with the keys **unset**
  the callback list is unchanged and no import of `langfuse` is attempted (the
  keys-absent path must not regress a default deployment).

**WS-6g — OTel GenAI export.** 🔒 **OWNER-GATE**
Gate: setting `OTEL_EXPORTER_OTLP_ENDPOINT` in prod. Note this env var is
currently **inert** — `opentelemetry-exporter-otlp` is not in `uv.lock`, so the
callback registered by `_init_telemetry` (`acb_llm/client.py:376-388`) has
nothing to export through. Adding the dependency is agent-safe prep; flipping
the env var is not.
- **Done when (agent-safe prep half):** `opentelemetry-exporter-otlp` resolves
  in `uv.lock` and `uv run python -c "import opentelemetry.exporter.otlp"`
  succeeds, with `OTEL_EXPORTER_OTLP_ENDPOINT` still unset and
  `_init_telemetry` still registering nothing — the dependency add alone changes
  no runtime behaviour.
- **Done when (post-clearance):** with the endpoint set against a collector, one
  model call emits a span carrying `gen_ai.*` attributes and a `trace_id` that
  can be joined to our `run_id`.

**WS-6h — `LLM_USAGE_AUDIT=1`.** 🔒 **OWNER-GATE**
Gate: prod env flip with a per-call Postgres write cost. WS-6d is designed to
make this flag unnecessary for cost attribution; do not flip it as a shortcut.
- **Done when:** *nothing* — this item's correct terminal state is that it is
  never flipped. It closes when WS-6d has shipped and a WS-6 PR records that
  `llm_call` supersedes the flag for cost attribution, leaving the flag as the
  debug-only path it was. If you find yourself wanting to flip it, the durable
  table is missing or wrong; fix that instead.

**WS-6i — Re-enabling the MAF telemetry kill switch.** 🔒 **OWNER-GATE / DO NOT TOUCH**
`executor.py:114` disables `agent_framework`'s OpenTelemetry instrumentation
because its streaming cleanup resets a ContextVar in a different async context
("Token was created in a different Context" at the end of every streamed run).
It is guarded by `tests/unit/test_executor_telemetry_killswitch.py`. Re-enabling
it is not a config change — it needs the upstream bug fixed first.
- **Done when:** *not in this workstream.* The only acceptance is an upstream
  `agent_framework` release whose streaming cleanup no longer resets the
  ContextVar in a foreign context, demonstrated by
  `tests/unit/test_executor_telemetry_killswitch.py` being **rewritten** to
  assert the opposite and a full streamed run completing without the
  "Token was created in a different Context" error. Until then, an agent that
  touches `executor.py:114` has failed the ticket, not completed it.

**Budget ENFORCEMENT is not in WS-6 at all** — see D2/D8, owner **WS-16**.

### Open questions — decide before (or while) building; do not decide silently

Four things this section does not settle. Q1 and Q2 change what a correct
WS-6b/WS-6d implementation looks like; Q3 and Q4 are open by design and are
recorded here so nobody invents an answer in a PR description. Each states the
options and, where this spec can legitimately recommend one, the recommendation.
An implementer who disagrees with a recommendation should say so in the PR and
amend this section — that is a cheap edit; a silent divergence is not.

**Q1 — How does an `llm_call` row get written without putting a Postgres INSERT
in the LLM request path?** (bears on **WS-6d**)
`_emit_usage` is a plain synchronous `def` (`packages/acb_llm/acb_llm/client.py:515-518`)
called on **every** completion, and v1_compat is — this doc's own words —
"THE choke point every agent runtime POSTs through". A naive per-completion
synchronous INSERT there adds a DB round-trip to every model call on the
platform's hottest path, and couples completion latency (and completion
*success*) to Postgres availability. WS-6d says *what* to store and says nothing
about *how* it is written; that gap is big enough to produce two very different
PRs.
- **Options:** (a) synchronous INSERT inline in `_emit_usage`; (b) fire-and-
  forget onto the **thread-pool** executor (`loop.run_in_executor`, not
  `orchestrator/executor.py`), exactly like the existing audit path;
  (c) batch/buffer
  in memory and flush periodically; (d) write from a consumer off the `cc:activity`
  Redis stream (fully decoupled, but adds a consumer we do not have — cf. WS-4).
- **Precedent already in this file — copy it.** The `LLM_USAGE_AUDIT` audit row
  solved this exact problem in this exact function: `acb_llm/client.py:561-582`
  builds a `_persist()` closure (`:566-573`), and rather than calling it inline
  dispatches it with `loop.run_in_executor(None, _persist)` (`:575-582`) —
  because, in the code's own comment, *"record() opens a sync DB session — keep
  it off the event loop"* — with `task.add_done_callback(lambda t: t.exception())`
  to consume the failure, and a synchronous fallback only when no loop is running
  (`:576-578`). The whole block is wrapped in a bare `except Exception: pass`.
- **Recommendation: (b).** Reuse that pattern verbatim. It is proven in-place,
  needs no new infrastructure, and matches the activity bus's stated contract
  ("best-effort + non-blocking + never raises", Phase 5).
- **Failure semantics, non-negotiable whichever option wins:** a dropped or
  failed `llm_call` write **must never fail, delay, or alter the completion**.
  The completion is the product; the cost row is bookkeeping. Concretely: no
  exception may escape the write path, and the response bytes must be identical
  whether the write succeeded, failed, or was never attempted. Pin that.
- **Consequence to accept honestly:** best-effort writes mean `llm_call` can
  under-count. That is why WS-6d's reconciliation done-when is "within rounding
  of `cost_summary()`", and it is why WS-16 must treat a cap as a **floor on
  observed spend**, not a proof of total spend.

**Q2 — What fraction of model calls will the D1 stamp actually cover, and is a
missing-attribution row a bug or the known hole?** (bears on **WS-6b**)
This is documented **above at `observability_e2.md:282-288`** ("Still app-level
(by design)") but §7 never referenced it, so an implementer can read the whole
ticket list without meeting it. Restating it here because it is load-bearing:
agents running on `GitHubCopilotAgent` — **`task-manager` and `apis-config`** —
reach the model through the Copilot SDK's BYOK provider, which exposes **no
client-header hook we control**. Copilot-SDK mutation traffic is in the same
position.
- **Consequence:** those agents' `llm_call` rows will carry **null
  `run_id`/`member_email`/`instance`** (they get `source="chat"` and no agent),
  and WS-6d's `SUM(cost_usd) GROUP BY member_email` reconciliation **may not hold
  for them**. An implementer must **not** treat that as a defect in their own
  work and must not go hunting for it in their diff — and must not "fix" it by
  synthesising a member.
- **The hole is unquantified.** Nobody has measured what share of daily spend
  those two agents represent. Cheap first measurement, using what already ships:
  `GET /observability/cost` returns `by_agent`; the residual between
  `totals.cost` and the sum of attributed agents is an upper bound on the hole.
- **Open:** does WS-6b's done-when ("a chat run through the MAF orchestrator…")
  need a second criterion asserting the SDK agents are *knowably* unattributed
  (e.g. a distinguishable `attribution='unavailable'` marker) rather than merely
  null — so the reconciliation query can exclude them explicitly instead of
  silently mis-summing? **Recommendation: yes**, if it costs one column; the
  alternative is a cost report that is quietly wrong forever. Closing the hole
  itself needs an SDK-level header pass-through upstream and stays deferred.

**Q3 — What is the retention and PII policy for `llm_call`?** (bears on
**WS-6d**) — **genuinely open; do not invent an answer.**
Every other durable surface in this doc carries an explicit policy and `llm_call`
carries none, while growing **one row per completion, forever**:
- `agent_run` has a deliberate, user-chosen policy (`:39-43` runbook summary,
  `:93-96` the Phase-2 statement): metadata + tool summary for ALL runs, the full
  `trace` only for errored/cancelled/flagged runs, explicitly *"to bound storage
  + sensitive-data exposure"*.
- The Redis cost rollup self-expires (~45-day TTL) and the activity stream is
  ~2000 events; both forget by construction.
- `llm_call` as specified has no TTL, no prune job, and no stated policy.
- **The questions:** (i) how long are rows kept — indefinite, or pruned at
  N days like the Redis rollup? (ii) is a prune/partition mechanism part of
  WS-6d or a follow-up? (iii) is `member_email` on every row a PII position we
  are willing to hold indefinitely, given the same doc bounded `agent_run`'s
  exposure for exactly this reason? Note `llm_call` stores **no message
  content** — only counts, cost, and identifiers — which makes it far less
  sensitive than a `trace`, but "who spent what, when, forever" is still a
  personal-data record.
- **No recommendation.** This is a retention/privacy call for the owner, and
  it interacts with whatever WS-16 needs to look back over. An implementer must
  not pick a number in a migration. If the answer is not available at build
  time, ship WS-6d **without** a prune and record the open question in the PR —
  adding retention later is a follow-up migration, whereas a wrong TTL destroys
  data.

**Q4 — What is the read path for `llm_call`?** (bears on **WS-6d**)
As specified, WS-6d ships a table that **nothing exposes**. Its only done-when
is a hand-run SQL reconciliation, so on merge day the durable record is
invisible to the UI, to the API, and to WS-16.
- **Options:** (a) nothing in this slice — SQL only, and WS-16 brings its own
  reader (smallest slice; the durable record still accrues from day one, which
  is the point); (b) `GET /observability/cost` gains a durable mode
  (`?source=durable`, or falls back to `llm_call` beyond the Redis window) so
  the existing Cost tab silently gains history past ~45 days; (c) a new endpoint
  (`GET /observability/cost/durable`, or per-member `GET /observability/cost/members`)
  serving the D1 rollups directly.
- **Recommendation: (a) for this slice, with (b) as the intended successor.**
  Data accrual is what WS-16 is gated on and it starts the moment the table
  exists; the read surface can follow without a migration. (b) is preferred over
  (c) because D1's whole claim is that the views are *rollups of one record* —
  a second cost endpoint invites a second cost number, which is the failure mode
  this workstream exists to end.
- **If (b) is chosen, note the trap:** the Redis rollup and `llm_call` will not
  agree exactly (see Q1's best-effort semantics and Q2's coverage hole). Two
  numbers labelled "cost" that differ is worse than one number labelled
  honestly — whichever is served must say which source it came from.

### Verification (Windows; run these, quote the output)

⚠️ **Never run the full `uv run pytest` suite on this machine — it hangs against
the live DB.** For the same reason, keep `tests/unit/test_debug_routes.py` OUT
of the inner loop: it drives TestClient against a live DB.

```
uv run pytest tests/unit/test_observability.py tests/unit/test_activity_bus.py \
  tests/unit/test_llm_usage_telemetry.py tests/unit/test_v1_compat_telemetry.py -q
```
Baseline confirmed 2026-08-01 on a clean tree: **55 passed in 15.14s**. Any WS-6
PR must keep this green and add to it. **After WS-6a/6c (2026-08-02) the
baseline is 65 passed in 18.04s** — the +10 are the attribution pins listed in
those tickets' done-whens. **After WS-6a repair round 1 (same day) it is
71 passed in 23.44s** — the +6 are the presence-refresh, twin-tuple drift and
cost-dimension pins.

The executor half of WS-6a is pinned separately, because `_bind_run_instance`
lives next to `_resolve_agent_instance`:

```
uv run pytest tests/unit/test_instance_wiring.py -q
```
Baseline confirmed 2026-08-02: **18 passed in 15.96s** (15 before WS-6a; 17
after WS-6a, +1 for the presence-key patch in repair round 1).

⚠️ `tests/unit/test_run_agent_stream_e2e.py` — the one file that drives the real
`run_agent_stream` — **hangs on this Windows box** (>5 min, no output), like the
full suite. It is therefore NOT the place to pin run-boundary behaviour from
here; test the composition (`_resolve_agent_instance` → `_bind_run_instance` →
`get_run_context`) instead, as WS-6a does.

```
uv run pytest tests/unit/test_observability_access.py \
  tests/unit/test_executor_telemetry_killswitch.py \
  tests/unit/test_app_runtime_activity.py -q
```
Baseline confirmed 2026-08-01: **11 passed in 86.9s** (slow — MAF import).

```
uv run ruff check --select F821,F601,F602,F502,F7,B006 \
  apps/services/gateway/gateway/routes/v1_compat.py \
  apps/services/gateway/gateway/run_trace.py \
  packages/acb_common/acb_common packages/acb_llm/acb_llm
```
Baseline confirmed 2026-08-01: **All checks passed!** This is a fast **local
proxy** for CI, narrowed to the paths this slice touches — it is **not** the CI
command. ⚠️ Do **not** use a bare `uv run ruff check <paths>` as a gate: it
reports **39 pre-existing findings** on exactly these paths (1,968 repo-wide)
and is deliberately non-blocking in CI (`pr-check.yml:60`, "style backlog …
non-blocking — see ratchet plan").

**What CI actually blocks on** (`.github/workflows/pr-check.yml:51`) is the same
select-list over the **whole repo**, no path narrowing:

```
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

✅ **This command is GREEN on `main`** — re-verified 2026-08-02 on a clean tree:
`All checks passed!`

*(Corrected 2026-08-02, WS-6a/6c PR. The 2026-08-01 audit recorded this gate as
RED with two pre-existing `F821 Undefined name TurnDecision` errors at
`routes/agent.py:166` / `:225` — string annotations whose type was imported
function-locally. That was fixed separately and the claim is now stale, so it is
removed rather than explained: the gate is green, and **any failure a WS-6
implementer sees is theirs.**)*

Frontend is untouched by this slice; no `next build` gate applies unless the
office/cost views change.

### R4 — what to update when this ships

Any WS-6 PR updates this spec's `## Status` header + changelog in the same PR,
and flips the row in `project-docs/AGENTS.md`'s spec index (which currently
reads "distributed/OTel tracing **dead** → **BO-5**") plus
`FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-5. Sequencing/ownership lives in
`work_plan.md` — that board wins over this spec.

---

## Status
- 2026-07-03 — Phases 1+2 shipped. E2 C+ → B+.
- 2026-07-03 — Phases 3+4 shipped. E2 B+ → A−. `/debug/runs` diagnostics API
  (`routes/debug.py`, registered in main.py) + `scripts/feature_check.py`
  one-command live sweep. 9 debug-route integration tests (TestClient vs live
  DB) + 3 harness unit tests; full suite 667 green. OTLP trace-backend export
  remains the only deferred item (dormant, gated on `OTEL_EXPORTER_OTLP_ENDPOINT`).
- 2026-07-09 — Phase 5 shipped. E2 A− → A. Live cross-app activity feed: global
  `cc:activity` bus (`acb_common.activity`), publish at the executor run
  boundary + `acb_llm._emit_usage`, `/observability` gateway API (recent / SSE
  stream / active) + a new `/observability` Control Plane page. 7 activity-bus
  unit tests; full unit suite 801 green.
- 2026-07-09 — Phase 6 shipped. Universal app coverage (email/tasks + any future
  app via `_infer_app_source`), live per-call cost pricing + daily Redis rollup
  (`/observability/cost`), agent roster (`/observability/roster`), and a redesigned
  3-view `/observability` page (8-bit office · live feed · cost) with per-agent
  run/error drill-down. +9 tests (807 total); `next build` + `tsc` + eslint clean.
  (The durable Postgres cost table deferred here is now **§7 WS-6d** — it was
  a one-clause aside in this changelog, which is why nobody could dispatch it.)
- 2026-07-09 — Phase 6.1 (review + fixes). Traced the full agent→model path:
  chat-agent completions + cost were bypassing instrumentation via v1_compat, and
  the orchestrator wasn't shown as an agent. Instrumented v1_compat (stream +
  non-stream) + copilot_chat lifecycle + roster orchestrator/sub-agent inclusion.
  +4 tests incl. an end-to-end v1_compat drive (811 total green).
- 2026-07-09 — Phase 6.2 shipped. Per-agent model correlation via v1_compat
  headers: `X-CC-Agent` / `X-CC-Source` set as `default_headers` at client
  construction (`orchestrator/agents.py::_make_openai_client`, and in-repo in
  `agent-email-assistant` / `agent-whatsapp-assistant`), read fail-soft by
  `routes/v1_compat.py::_handle_chat_completions`. Copilot-SDK agents stay
  app-level by design. Shadowed duplicate `/v1/chat/completions` in `main.py`
  removed.
- 2026-07-09 — Phase 6.3 shipped. Access fix (live observability views open to
  any AUTHENTICATED caller; full message-content trace stays EXECUTIVE-gated at
  `/debug/runs/{id}`) + durable history `GET /observability/runs` over
  `agent_run` + a History tab. +4 tests (817 total).
- 2026-07-09/10 — Phases 6.4/6.5/6.6 shipped (UI only, no backend contract
  change): procedural pixel-art office → layered configurable scenes → office
  polish (war room, `cost_summary.by_agent` in the agent drawer, Lucide icons).
- 2026-07-10 — §6.7 landscape review recorded. **Recommendation only — no code
  shipped.** Langfuse + OTel remain wired to nothing; see §7.
- 2026-07-10 — Phase 6.8 shipped. Real Pixel Lab sprites + Avatar Studio,
  `agent_avatars` table (migration 64), avatar endpoints on the observability
  router, server-side `PIXELLAB_API_KEY`. +4 unit tests.
- 2026-08-01 — **Doc remediation (no code).** Re-verified this spec against the
  tree: corrected the false "cost/token attribution → `agent_run` token columns"
  claim (those columns are never written — `_persist_row` omits them), fixed
  post-restructure paths (`apps/gateway/…` → `apps/services/gateway/…`;
  `packages/acb_common/_log.py` → `packages/acb_common/acb_common/_log.py`),
  numbered the §6.7 heading `work_plan.md` cites, and added **§7 — the
  dispatchable WS-6 ticket list** (attribution stamp per decision D1, durable
  cost table, `agent_run` token backfill, and the owner-gated Langfuse/OTel
  half), each with a done-when, a gate label, and file anchors. E2 status
  unchanged: the Redis feed is live, deep tracing is still absent (BO-5).
- 2026-08-01 — **§7 repair round (no code).** Verification pass on the above
  returned three decisive findings, now closed. (1) Added **§7 Open questions**
  (Q1 `llm_call` write mechanism — recommend the fire-and-forget pattern already
  proven at `acb_llm/client.py:561-582`, with "a dropped row must never fail the
  completion" as the binding semantics; Q2 the unquantified Copilot-SDK coverage
  hole at `:282-288`, which WS-6b's done-when could otherwise pass right over;
  Q3 `llm_call` has no retention/PII policy while `agent_run` has an explicit
  one — recorded, deliberately unanswered; Q4 nothing exposes `llm_call`).
  (2) **Security:** the proposed `X-CC-User` header is unauthenticated —
  `/v1/chat/completions` is guarded by `require_llm_api_auth`
  (`acb_auth/deps.py:303-327`), a **shared**-token check with no per-user
  identity, and trusting a bare user header was previously a full cross-account
  bypass (`deps.py:239-254`); left as-is it would build WS-16's per-member
  budget caps on a forgeable value. **WS-6b amended**: `member_email` must be
  server-established, any header value is persisted as self-asserted and is
  never a basis for enforcement, plus a done-when pinning a forged header;
  WS-6d's schema carries the same constraint. (3) Corrected the ruff claim —
  `pr-check.yml:51` runs the select-list over the **whole repo**, not the
  narrowed paths. *(Its "red on `main` with 2 pre-existing `F821 TurnDecision`
  errors" reading was true on 2026-08-01 and is no longer: the gate is green as
  of 2026-08-02 — see Verification.)* Also: done-whens written for the
  owner-gated WS-6f/g/h/i so
  the preamble's claim is true of itself; softened the unsupported
  "`work_plan.md` §6 enumerates these flags" citation (it does not — that edit
  belongs to the board's owner); `114_custom_apps.sql:80-91` → `:80-93`;
  restored the elided parentheticals in the D1 quote; and replaced the single
  `main.py:1341` anchor with all four `build_orchestrator_agent(with_history=
  False)` sites (`:366`, `:589`, `:1341`, `:1392`). §7 slice WS-6a–e remains
  AGENT-SAFE and dispatchable; E2 status otherwise unchanged.
- 2026-08-02 — **§7 WS-6a + WS-6c shipped (code).** The D1 attribution stamp
  now exists as a substrate. `_RUN_CONTEXT_KEYS`/`bind_run_context` gained
  `instance` (`''` shared binds nothing, so a shared agent's telemetry is
  unchanged); `executor._bind_run_instance` tops the run context up right after
  `_resolve_agent_instance`, resolving the ordering trap with a **second,
  additive bind** rather than moving the early one; `activity._INHERIT` gained
  `instance`; `_emit_usage` gained `run_id=`/`member=`/`instance=`, deferring to
  the run context. +10 tests (65 in the §7 verification set) plus 2 in
  `test_instance_wiring.py`. **Three corrections to the 2026-08-01 audit:**
  (1) WS-6c was **half-shipped** — `_INHERIT` already carried four of the five
  fields, so "zero call-site changes" was largely pre-existing behaviour, not
  new work; (2) WS-6a's "the other resolve site (`:1728`) needs the same
  treatment" was **wrong** — `run_agent` binds no run context at all, so that
  would add correlation where none existed; recorded as a scope line instead;
  (3) the "ruff gate is RED on `main`" note was stale — it is green, and the
  claim is removed. **No migration, no schema, no wire-protocol change**:
  nothing durable is written and no caller passes the new parameters yet, which
  is exactly the WS-6b/6d boundary.
- 2026-08-02 — **§7 WS-6a repair round 1 (code, same PR).** Review of the above
  found the stamp reached no *reader*. Four fixes, all inside WS-6a's scope —
  still no migration, no schema, no wire-protocol change, nothing durable:
  (1) **The presence key could never carry `instance`.** `_axadd` writes
  `cc:activity:live:{run_id}` from the `phase="start"` body, which predates the
  agent load, so `active_runs()` → `/observability/active` → `/roster` (the
  office view) carried no partition for **any** run. New
  `acb_common.activity.refresh_run_presence(run_id, **fields)` patches the
  existing snapshot (`SET … XX`, TTL refreshed, a miss is a no-op so a
  finished run is never resurrected); `_bind_run_instance(instance, run_id)`
  calls it. Chosen over re-publishing the start event because a duplicate
  `start` **stream** entry is visible to every consumer, while a presence
  snapshot overwrite is invisible and idempotent.
  (2) **The delegation asymmetry is recorded, not papered over.**
  `_run_sub_agent_streaming` resolves no instance and cannot unbind the
  caller's, so a delegated sub-run's events carry the caller's partition while
  its blobs carry `''`. The three places that asserted the stamp is "the SAME
  key the manifest/blob store/state dir use" now say it identifies the
  partition of the run that *resolved* it; WS-6a gained asymmetry #2 and WS-6d
  gained a "not a foreign key onto `agent_blob`" warning. Fixing delegation
  needs `unbind_contextvars` + restore — a separate ticket, deliberately not
  done here.
  (3) **The twin-tuple invariant is enforced, not prose.**
  `test_observability.py::test_inherit_and_run_context_keys_match`.
  (4) **The stamp reaches an aggregate.** `_record_cost` folds
  `instance|<key>|cost`/`|calls` into the existing `cc:cost:{day}` hash and
  `cost_summary()` returns `by_instance` — additive, no route change, same
  shape as `by_agent`. This is the *live* rollup only (45-day TTL, no per-call
  row): **WS-6d is unchanged and still open.**
  +8 tests (89 in the §7 verification set incl. `test_instance_wiring.py`).

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-6 — **Observability wiring + attribution** (BO-5 + decision D1)
**State cell (as of the move):** 🟡 partial
**Narrative (verbatim):** **Docs gate CLEARED** (PR #319 added the numbered §7 with nine lettered tickets WS-6a–i, per-item done-whens and gate labels). **Re-audited 2026-08-02 → GO-NARROWED to WS-6a+WS-6c only.** ✅ **BUILT 2026-08-02, pending review:** D1's attribution stamp exists as a substrate — `instance` joins `_RUN_CONTEXT_KEYS`/`bind_run_context`, resolved once in `run_agent_stream` via a **second additive bind** after `load_agent` (the early bind stays: it is what correlates a failure *during* load; moving it would trade 5 fields for 1), and `_emit_usage` carries the full (run, member, agent, instance) tuple with **zero call-site changes** — it arrives by inheritance via `activity._INHERIT`. Shared agents produce an **absent key, never `''`** (double-guarded + pinned). `refresh_run_presence()` patches `cc:activity:live:{run_id}` after the late bind, so `/observability/active` + `/roster` carry it; interim `by_instance` cost dimension added to the Redis rollup. **Nothing durable is written yet** — logs + Redis feed only. **🔴 WS-6b/6d/6e HELD, still NO-GO:** WS-6b's security amendment names *no workable mechanism* — `bind_run_context` has one call site (`executor.py`), contextvars do not cross the HTTP hop to `v1_compat`, and `agent_run` rows are written at the run *boundary* so a mid-run join finds nothing. **The only mechanism the code supports at request time is the presence key `cc:activity:live:{run_id}`**, which for the orchestrator path carries a server-established `user`; §7 must name it (or name another) before WS-6b dispatches. WS-6e has no token source (`build_run_trace_row` is pure over events+folded) so it sequences *after* WS-6b, not independently; WS-6d additionally waits on the retention/PII answer (Q3). **Two recorded asymmetries** — the `phase="start"` event predates the bind, and **a delegated sub-run inherits the caller's partition** while its blobs key to `''`, so WS-6d must not treat `instance` as a foreign key onto `agent_blob.instance`. **OWNER-GATE:** WS-6f/g/h/i (Langfuse keys, `--profile obs`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `LLM_USAGE_AUDIT`, the MAF telemetry kill switch) — all now listed in §6.

**Corrections applied 2026-08-09:** current as moved.
