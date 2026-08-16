# App Workshop & Custom Apps — Analysis & Implementation Plan

**Status:** Draft / RFC · **Date:** 2026-07-25 · **Owner:** vjvarada

A chat-driven **app builder** ("the Workshop") plus an in-platform **deployment surface**
("Custom Apps") that lets anyone on the team build small HTML/JS/React tools by talking to
an LLM agent — live preview on the left, build chat on the right — and then publish them
*inside Metorite itself* for the rest of the team to use.

This is **small software**: purpose-built tools that will only ever have one or a handful of
users. Every team does things differently, and there is unlimited demand for bespoke tools —
tracking numbers, running workflows, managing checklists, sharing prototypes. Such software
is now easy to *build* but still hard to *deploy and share*: the environment needs
customizing, auth & permissions are hard, and letting non-technical users share arbitrary
code is tricky to do securely. The bar to hit: **sharing a tool with a colleague should be
as easy as sharing a Google Doc.**

Interactive mockups live alongside this doc:

| Mockup | Surface |
|---|---|
| [`mockup-workshop.html`](mockup-workshop.html) | The Workshop builder — preview left, build chat right, code view, publish flow |
| [`mockup-apps-home.html`](mockup-apps-home.html) | Custom Apps gallery — describe-to-create bar, filters, team/mine/drafts, templates |
| [`mockup-app-run.html`](mockup-app-run.html) | A published app running full-page — platform chrome, capability chips, per-use write approval |

---

## 1. TL;DR / Recommendation

Build two thin surfaces over machinery Metorite mostly already has:

1. **The Workshop** (`/build/apps/{slug}/edit`) — a split view: sandboxed live preview on
   the left, a pinned `<AgentChat>` on the right talking to a new **`app-builder`** agent.
   The builder is a Copilot-SDK coding session (the same engine as `code_task` /
   `spawn_copilot_agent`) whose working directory is the app's workspace. It scaffolds,
   edits, builds (esbuild → one self-contained `bundle.html`), and self-checks — every AI
   edit is a git checkpoint you can restore.
2. **Custom Apps** (`/build/apps`) — the gallery + runtime. Publishing snapshots the draft
   as an immutable version; teammates open the app full-page inside Metorite, rendered
   through the existing hardened `SandboxedHtml` iframe. A small injected SDK —
   **`window.cc`** — gives apps identity, shared storage, LLM calls, and *declared*
   integration actions, all brokered by the platform with the **viewer's** identity and
   approval gates. Apps never hold credentials.

The single most important architectural rule (validated by Claude Artifacts, Datasette
Apps, Figma plugins, Airtable extensions, and Google Apps Script alike): **the app is
untrusted; the platform brokers every capability.** User-generated code runs in an
isolated origin with no ambient network access; everything interesting it can do —
read who's viewing, store rows, call an LLM, create a ClickUp task — happens through a
narrow, allowlisted, audited bridge, executed **as the viewing user** by default, with
write actions flowing through the existing Action Broker / approval machinery.

What makes this cheap here: the sidebar already declares the section
(`nav.ts` → `/build/apps` "Custom Apps — User-created applications", currently a 404),
the chat pane is a reusable component (`AgentChat`, already embedded twice), the preview
engine exists (`SandboxedHtml`, opaque-origin iframe + strict CSP + postMessage bridge),
the builder engine exists (`code_session.py`), workspaces are git-tracked and blob-store
durable (`loader.py` local-path machinery), and the credential/permission plumbing exists
(Integration Registry, `permission_policy`, `pending_actions`). What's genuinely new: an
`apps` data model, the build/publish pipeline, the `cc` SDK bridge + App API, and the
sharing/consent UX.

**MVP scope discipline:** apps are **frontend-only** (HTML/JS/React compiled to a single
static bundle). No server-side app code, no Next.js apps in v1 — `cc.storage` /
`cc.ai` / `cc.tools` cover what a server would be for. That keeps the untrusted-code
problem inside the browser sandbox (strong) instead of the host (weak until BO-7 lands).

---

## 2. How the reference systems do it

### 2.1 Chat-to-app builders (open source, surveyed 2026-07)

| Project | License | State | The lesson |
|---|---|---|---|
| **dyad** (~21k★) | Apache-2.0 (+FSL `src/pro`) | very active | Best error/version UX: git **checkpoint per AI edit** with additive restore; auto **tsc-check + ≤2 bounded retries** after each edit; preview error banner with one-click **"Fix error with AI"** |
| **Cloudflare VibeSDK** (~5k★) | MIT | active | The closest full blueprint of this feature: stateful agent session, **phase-wise generation** (blueprint → core → polish), deterministic **lint/typecheck/runtime validators feeding fixes back to the model**, per-app isolated deploy target |
| **E2B Fragments** (~6k★) | Apache-2.0 | active | Canonical server-sandbox reference: LLM streams a **structured fragment object** (schema'd file list, not markdown), sandbox per session, iframe to its URL. Gap: no error loop, no versioning |
| **bolt.diy** (~20k★) | MIT app / **proprietary WebContainers** | stalled | Great workbench UX (attach-error-to-chat, diff view, file locking) but the in-browser Node runtime **requires a commercial license for internal for-profit use** — disqualified |
| **LibreChat artifacts** (~41k★) | MIT | very active | Claude-style artifacts via Sandpack with a **self-hosted bundler** fork; artifacts are chat-scoped only — no publish story (the gap this RFC fills) |
| **Sandpack / Nodebox** | Apache-2.0 / non-OSS | maintenance limbo | Post-acquisition limbo; Nodebox is non-OSS and abandoned. **Avoid.** esbuild (MIT) is the safe bundler in 2026 |
| **app.build** (Neon) | Apache-2.0 | discontinued | **Template-constrained generation** + validation-as-a-pipeline (typecheck/lint/tests to green) — adopt for the pre-publish check; its heaviness per app is the cautionary tale |
| **Onlook** (~26k★) | Apache-2.0 | active | Element↔source mapping (click an element → precise edit prompt) — a v2 idea worth stealing cheaply via injected data-attributes |
| **GitHub Spark** | commercial | preview | The product model for *internal* micro-apps: managed runtime + built-in data + built-in LLM + org-scoped sharing. Our target feature set, self-hosted |

**Convergent patterns adopted:** (a) two preview tiers — client-side sandboxed iframe for
frontend apps, server sandbox only when an app genuinely needs a server (deferred here);
(b) deterministic validators in the loop with bounded auto-repair, human-triggered repair
for runtime errors; (c) checkpoint-per-AI-edit, git-backed, additive restore; (d)
structured file-op streaming (we get this from Copilot SDK tool events); (e) **the
capability bridge** (next section).

### 2.2 Claude Artifacts — the conceptual model

Single-file React/HTML rendered in a **sandboxed iframe on a separate usercontent domain
with a strict CSP that blocks all network egress**; JSX transformed client-side; approved
CDN whitelist. `window.claude.complete()` lets the artifact call the LLM through an
injected parent bridge — **no keys in the artifact, ever**. The sharing economics are the
key insight: a shared artifact runs against the **viewer's** account — viewer-pays,
viewer-authorized. Later versions added declared per-artifact **capabilities** the host
enforces. Translated internally: `window.cc.*` + SSO identity + manifest scopes.

### 2.3 Small-software hosts & ambient auth (what "deploy inside the platform" means)

- **Val Town** (the canonical small-software host) — see also its runtime history below.
- The philosophy this feature implements has a literature: Robin Sloan's
  ["An app can be a home-cooked meal"](https://www.robinsloan.com/notes/home-cooked-app/),
  Maggie Appleton's ["Home-Cooked Software and Barefoot Developers"](https://maggieappleton.com/home-cooked-software)
  (domain insiders with just enough capability building for their own ~10-person team —
  exactly the Fracktal persona), and Val Town's
  ["End-programmer programming"](https://blog.val.town/blog/end-programmer-programming).

- **Google Apps Script** — the OG internal small software. Scopes are declared/detected,
  **consent is keyed to (user, app, scope-set)** — a new version with the same scopes
  inherits consent; widened scopes re-prompt. Deployments = immutable versions behind a
  stable URL; rollback = repoint. `executeAs` picks **run-as-viewer vs run-as-author**.
  Domain trust removes verification friction inside one org. *(per-org under D15 —
  each tenant brings its own domain; the friction argument holds within a tenant,
  not across)*
- **Val Town** — platform **injects a short-lived, down-scoped API token** into the
  running val; std-library wrappers (`std/sqlite`, `std/email`, `std/openai`) use it
  transparently; dangerous scopes are excluded by default. Pure run-as-author.
- **Airtable extensions** — the opposite pole: the extension holds **no token at all**;
  every call flows over an injected bridge into the host page's session, so the app can
  never exceed the viewer's existing permissions — **the platform ACL is the consent**,
  no consent screen exists. SDK exposes `checkPermissionsFor*` purely for good UX.
- **Datasette Apps (2026-06)** — the closest prior art to this exact feature: LLM-built
  HTML apps hosted *inside* Datasette, `sandbox="allow-scripts allow-forms"` iframes (no
  `allow-same-origin`), CSP blocks all external traffic, data access only via injected
  `datasette.query()` bridge functions, per-app CSP relaxations admin-gated.
- **Figma plugins** — the deep security writeup: in-page JS membranes (Realms) proved
  vulnerable; robust isolation = separate origin / separate VM + postMessage. Their final
  architecture is exactly "privileged side holds the API, sandboxed side holds the UI".
- **Serving prior art** (CodePen, Observable, GitHub Enterprise subdomain isolation,
  claudeusercontent.com): user content must never be same-origin with the platform —
  subdomain-per-app on a dedicated hostname, CSP `sandbox` sent as a **response header**
  so even a direct visit is sandboxed, platform cookie locked with the `__Host-` prefix.

### 2.4 Internal-tool platforms (Windmill, ToolJet, Appsmith, Budibase — surveyed 2026-07)

All four were **rejected as embeddable components** (each is a whole second product with
its own auth/tenancy; AGPL/BSL friction; the needed RBAC features are paywalled — ToolJet's
free self-host now caps at 2 apps) but their converged patterns are the spec:

- **Windmill's app-execution model** is the sharpest formulation of deploy-time capability
  freezing: viewers execute runnables **on behalf of the publisher**, and "a policy is
  computed at time of saving of the app which only allows the scripts/flows referred to in
  the app to be called… static parameters are not overridable." Our manifest `scopes` with
  frozen param constraints (§4.1) is the same move — compiled at publish, enforced at the
  gateway. Windmill's **operator** role (consumers whose home surface is the app list) maps
  to our `use` grant.
- **Appsmith's security doc** is the reference text for the credential proxy: creds
  AES-encrypted server-side, "securely appended just prior to forwarding," never sent to
  the browser. **Budibase's 2026 CVEs** are the counter-lesson: redacted secrets
  round-tripped through client-editable datasource config let users repoint the base URL
  and exfiltrate stored auth — so credentials must be **pinned server-side to their
  host/action**, and never appear in any app-editable config.
- **ToolJet's version manager** (named versions, exactly one *released*, rollback =
  re-release) matches the `app_versions` + `live_version` design.
- **Streamlit/Gradio** show the anti-pattern this feature eliminates: app-per-process +
  reverse proxy + bolt-on SSO scales ops burden linearly with app count.
- **Cloudflare Workers for Platforms / Deno Subhosting** define what serious untrusted
  *server-side* hosting takes (per-tenant isolates, dispatch routing, egress interception,
  metering) — disproportionate for ~20 users, and the standing argument for keeping v1
  apps frontend-only.

### 2.5 Val Town's runtime lineage (the T3 answer, when we get there)

Val Town burned through five runtimes for untrusted JS and wrote it up: Node `vm`
(escapable), `vm2` (deprecated after unfixable escapes — *"you can't use JavaScript to
build a JavaScript sandbox"*), Deno Workers (leaks, nested-worker escapes), then the
winner: **a warm pool of Deno subprocesses with explicit permission flags, serving HTTP
over a Unix domain socket, supervised from the host process** (their MIT-licensed
[`deno-http-worker`](https://github.com/val-town/deno-http-worker)). Smallweb (PolyForm
Shield — patterns copyable, code not) applies the same shape per folder-app:
`--allow-read=<appdir> --allow-write=<appdir>/data --allow-net --allow-env=<allowlist>`.
Val Town's other giant idea: a short-lived scoped platform token **injected as an env
var**, with the "std library" (`std/sqlite`, `std/blob`, `std/email`, `std/openai`) being
plain modules that call the platform REST API with it. And from Townie (their builder
agent): keep the system prompt a **minimalist quickstart + one example app**, and *"adapt
APIs to LLM expectations"* rather than teaching the model quirky interfaces.

**Synthesis we adopt:** Airtable's credential-less, proxied, **run-as-viewer** bridge as
the chassis · Val Town's short-lived scoped token as the wire format (only if an app needs
direct fetch; default is pure parent-proxying) · Apps Script's manifest scopes +
first-run consent + **re-consent on scope-set change** + immutable versions/rollback as
the lifecycle · Windmill's deploy-time-compiled action allowlist with frozen params ·
Slack/Retool's admin approval + audit logging as the org guardrails.

---

## 3. Mapping onto Metorite

Metorite already has most of the runtime an app platform needs. The gap is the app
model, the build/publish pipeline, the runtime bridge, and the sharing UX.

| App-platform concern | Already exists | Gap to build |
|---|---|---|
| **Sidebar slot + gallery page** | `nav.ts` → Build → "Custom Apps" (`/build/apps`, currently 404); landing-page tile auto-derives from `NAV_SECTIONS` | `src/app/build/apps/*` pages |
| **Build chat pane** | `<AgentChat>` reusable embed (pattern: `tasks/components/AssistantRail.tsx` — thin wrapper, pinned agent, persona, `onArtifact`) | `app-builder` agent + persona + workshop wrapper |
| **Builder engine** | `orchestrator/code_session.py` (bounded Copilot session in an arbitrary `working_directory`, BYOK via gateway `/v1`, risk-aware permission handler); `/agent/run/stream` + `stream_relay` + `event_translator` for live SSE | An interactive (not one-shot) session bound to the app workspace; build/validate harness contract |
| **App workspace, versioned & durable** | `loader.py` local-path machinery: copy-to-cache, `git init`, baseline commit, workspace `.gitignore`; blob store (`agent_blob`) survives volume wipes | An `apps/` workspace root; checkpoint-per-edit tags; version snapshot rows |
| **Live preview** | `SandboxedHtml` (opaque-origin srcDoc iframe, strict CSP, `ccAction`/`ccSubmit` postMessage bridge, `chromeless` full-page mode); `DocumentPane` already renders workspace `.html` live | Preview toolbar (device sizes, console, Fix-with-AI), bundle build step, error trap → chat |
| **React support** | `skills/upstream/anthropics/web-artifacts-builder` (Vite/Parcel → self-contained `bundle.html`); esbuild is trivially available in the builder session | The standard app template + build contract |
| **Identity** | NextAuth (Entra ID SSO) → `X-User-Email` / `X-User-Role` on every gateway call; `app_user` table | `cc.user()` pass-through into the bridge |
| **Storage for apps** | Postgres + blob store patterns | `app_data` KV/table API, app-scoped |
| **LLM access for apps** | Gateway `/v1` (tiered, metered, cached) | `cc.ai.complete()` proxy route with app attribution + budget |
| **Integrations for apps** | Integration Registry (encrypted keys, per-agent declared scopes, `FIELD_TO_ENV`), MCP registry, tool risk annotations (`TOOL_ANNOTATIONS`), permission policy | The **App Tool Proxy**: manifest-declared actions, scope checks, per-use confirm |
| **Write gating** | `pending_actions` approval inbox (Action Broker; BO-1 wiring in progress) + HITL confirm cards | Route app write-actions through it |
| **Publish approval** | `pending_commit` inbox pattern + Approvals UI | An app-publish review kind |
| **Audit** | `audit_event`, Redis activity feed, `agent_run` traces | Per-app usage + tool-call audit rows |
| **Sharing/permissions** | Roles executive/employee only; `multi_user_organization_research.md` (not implemented) | `app_grants` (private / people / org) — deliberately simple, forward-compatible with the org-research model |
| **Serving user content at real URLs** | Nothing (no StaticFiles mount anywhere; everything is srcDoc) | Phase-3 usercontent subdomain + CSP headers (see §7.4) |

**Two constraints to reconcile up front** (same move the Workflow-Editor RFC made):

1. *"No in-app agent/skill editing" (ADR-014 / root constraint #1).* Custom apps are
   **not agents or skills** — they are end-user artifacts (a workspace + manifest +
   static bundle), authored through chat. ADR-014's actual, load-bearing constraint —
   *platform* code (agents, skills, orchestrator) stays VS-Code-and-Git-only, no in-app
   editor, ever — is untouched. What changed: the RFC originally extended that same
   stance to Custom Apps too ("no code editor; the code view is read-only, edits happen
   through chat"). The Workshop's **Advanced view now ships a real hand-editable Monaco
   IDE** scoped strictly to one app's own workspace (file tree, save, create/delete —
   see §6/roadmap; "Monaco round-trip for developers" landed ahead of the rest of
   Phase 3). Simple view (the default) still stays chat-only, matching the original
   intent for casual use. Draft ADR-027 in §9 scopes this precisely.
2. *`project_plan.md` non-goal: "visual workflow canvas".* Untouched — this is not a
   canvas and not workflows. But the non-goals line should be amended to say what IS in
   scope: *"user-space small software (App Workshop) — see docs/app-workshop/"*.
   The Workflow Editor RFC and this one are complementary siblings: workflows = headless
   automation graphs over agents/tools; apps = interactive UI surfaces. Later they meet
   (an app button can trigger a workflow; a workflow can post into an app's data table).

---

## 4. Architecture

```
┌─ Control Plane ─────────────────────────────────────────────────────────────┐
│  /build/apps            /build/apps/{slug}          /build/apps/{slug}/edit │
│  Gallery + create bar   Run page (app chrome +      Workshop: preview pane  │
│                         SandboxedHtml + cc bridge)  + AgentChat (builder)   │
│         │                        │                          │               │
│  /api/apps/[...path]  (Next BFF proxy — session → X-User-Email headers)     │
└─────────┼────────────────────────┼──────────────────────────┼───────────────┘
          ▼                        ▼                          ▼
┌─ Gateway (FastAPI) ─────────────────────────────────────────────────────────┐
│  routes/apps/  ── app CRUD · files · build · publish · versions · grants    │
│                ── App Runtime API: /me · /data · /ai · /tools  (audited)    │
│         │                │                            │                     │
│  Integration Registry    Action Broker (pending_actions, per-use confirm)   │
│  /v1 LLM routing         audit_event · activity feed                        │
└─────────┼───────────────────────────────────────────────────────────────────┘
          ▼
┌─ Orchestrator ──────────────────────────────────────────────────────────────┐
│  app-builder agent = Copilot-SDK session, working_directory = app workspace │
│  streams AG-UI events → build chat;  esbuild → dist/bundle.html;            │
│  git checkpoint per edit;  blob-store sweep for durability                  │
└─────────────────────────────────────────────────────────────────────────────┘
   App workspace: {apps_root}/{slug}/  (git-inited, blob-mirrored)
     app.json  index.html  src/*.jsx  styles.css  dist/bundle.html  data/seed.json
```

### 4.0 The platform contract (non-negotiable)

Custom apps are **Metorite-native by construction, not by convention**. The
platform is the app's entire backend — identity, data, AI, and integrations all come
from Metorite through `window.cc`, and there is no second path. This is what
keeps every app shareable, auditable, secure, and maintainable by whoever inherits it.
Apps that "bring their own architecture" (direct API calls, external SDKs, their own
backends or keys) are not supported — not as an option, not as an escape hatch.

**The mapping is total** — every need an app has resolves to a platform capability:

| App need | The Metorite way | Never |
|---|---|---|
| Data | `cc.storage` (shared app tables) | localStorage/IndexedDB as store, external DBs (Firebase/Supabase/…) |
| AI | `cc.ai` → gateway `/v1` tiers | provider SDKs, embedded API keys, direct provider calls |
| Identity | `cc.user()` (ambient SSO) | login forms, auth libraries, cookies |
| External services | manifest scopes → `cc.tools` → Integration Registry | direct `fetch()` to any API, keys in code or config |
| UI assets | inline code, platform design tokens | CDN scripts/fonts/styles, external images |
| Automation | platform triggers + agents (Phase 3) | service workers, timers polling external services |

**Enforcement ladder** (defense in depth — each layer holds independently):

1. **The builder refuses and redirects.** When a request specifies an off-architecture
   approach ("call the weather API directly", "add Firebase", "load React from a
   CDN"), the `app-builder` does not comply-with-caveats — it names the deviation,
   explains the platform equivalent, and builds *that*. If the platform genuinely
   lacks the capability, it says so and points at the right extension path (register
   the integration in the Registry → declare the scope), never at a workaround.
2. **Sandbox physics.** The opaque-origin iframe + CSP (`connect-src 'none'`, no
   external `src`) make deviations *non-functional*, not merely discouraged — code
   that bypasses `cc.*` simply cannot reach anything.
3. **Publish conformance scan.** Publishing statically scans the bundle: external
   URL references in code/markup, key-shaped strings, service-worker registration,
   storage-API reliance → hard errors block the publish; softer findings surface as
   warnings in the publish modal.
4. **Runtime gate.** Every capability call is checked against the manifest ∩ grants
   at the gateway; undeclared scope → 403 + audit row. Budgets and Action-Broker
   gating apply regardless of what the code intended.

When an app needs something the platform can't do yet, **the platform grows** (a new
Registry integration, a new `cc.*` capability, eventually T3 server handlers) — the
app never routes around it. That rule is what makes the whole system compound instead
of fragment.

### 4.1 The app model

An app is **a workspace folder + a manifest + immutable published versions**.

`app.json` (the manifest — LLM-drafted, human-reviewed at publish):

```jsonc
{
  "slug": "filament-tracker",
  "name": "Filament Tracker",
  "icon": "🧵",
  "description": "Print-farm spool inventory with low-stock alerts",
  "runtime": "static",                  // v1: static only. later: "container"
  "entry": "dist/bundle.html",
  "run_as": "viewer",                   // "viewer" (default) | "author" (automations, gated)
  "scopes": [
    "identity:read",                    // viewer name + email
    "storage:app",                      // the app's own shared tables
    "ai:tier-1",                        // LLM calls at cheap tier, app-attributed budget
    "tool:clickup.create_task?list=Procurement"   // declared integration action, narrow
  ],
  "storage": { "tables": ["spools", "usage_log"] }
}
```

Scope strings are deliberately **narrow and parameterizable** (`tool:<service>.<action>`
with optional constraints), so the publish-review screen and the runtime check read the
same vocabulary. The effective permission at runtime is always the **intersection**:
`manifest scopes ∩ what the org/admin granted this app ∩ what the viewing user may do`.

**Runtime targets, staged:**

| Tier | What | When |
|---|---|---|
| T1 | Single-file HTML/JS (no build) | Phase 0 — **built** |
| T2 | Multi-file React → esbuild → one self-contained `dist/bundle.html`. Default: vendored deps (react/react-dom/**lucide-react**) via a shared, deploy-provisioned cache, no per-app install, no CDN. Opt-in: per-app `npm install` for anything the vendored set doesn't cover (e.g. `three`/`@react-three/fiber` for 3D) — see below | Phase 1 — **built** (`agent-app-builder/build/build_t2.mjs`; `entry`/`tier` in `app.json`; the builder upgrades an app from T1→T2 mid-conversation when a request calls for it, no separate creation-time picker). Reuses `@cc/ui` — the same 25-component design kit the chat "artifacts" system (`lib/compileArtifact.ts`) bundles into React artifacts — aliased in via an explicit import allowlist, so a T2 app writes `<Stat/>`/`<Table/>` instead of hand-rolled `cc-*` divs. Per-app custom dependencies — **built** (`agent-app-builder/build/install_t2_deps.mjs`: `--ignore-scripts` + a package-spec allowlist + a `node_modules` size cap; `build_t2.mjs` auto-detects an app-local `node_modules` and switches from the vendor cache + import allowlist to standard npm resolution, no per-app container). Real icons for every app, T1 and T2 — **built**: `lucide-react` is now a default-vendored T2 dependency (same version workbench/control_plane itself uses), and T1's `ccIcon('Name')`/`data-cc-icon` bridge — documented in `design.md` since the chat-artifacts system, but never actually wired up for Custom Apps — now gets its `icons` prop pre-resolved (`extractCcIconNames` + `buildIconMap`, in both the Workshop's preview and the published run page), so it isn't silently a no-op |
| T3 | Server-side apps (backend compute), JS/TS runtime | **Scoped, not built.** Deferred until **BO-7** lands (platform-wide sandbox hardening — today's code execution is a bare env-scrubbed subprocess, no container/cgroup/seccomp). Decided-but-unbuilt substrate: **not** Docker-per-app — warm Deno subprocesses with explicit `--allow-*` flags, supervised by the gateway (Val Town's `deno-http-worker` pattern) — chosen for the 4GB VPS. Cron/webhook-triggered functions additionally need BO-20 (durable job queue), currently absent. BO-7 progress so far (2026-07-27, `competitive_hardening_2026-07.md`'s status log): dep-install RCE fix, permission-context/destructive-tool cheap wins, and Copilot-SDK session containerization for both `code_task` and the App Workshop builder's own interactive chat session — but the loader-level agent-import path T3's own gate references is separate and still untouched, so this row's status doesn't move yet |

### 4.2 The builder (Workshop session)

- A new first-party **`app-builder`** agent (Copilot-SDK engine, Tier 1.5, gateway-routed
  BYOK — the runtime already reserved for interactive coworker chat). Its
  `working_directory` is the app's workspace; chat streams over the existing
  `/agent/run/stream` path, so thinking, tool cards, HITL, reconnect, and stop all come
  for free. The Workshop page is a thin wrapper: `<AgentChat compact lockModel
  persona={appPersona(app)} onArtifact={refreshPreview} />` — the `AssistantRail`
  pattern verbatim.
- **Harness contract** (the `_HARNESS_INSTRUCTIONS` idea from `code_session.py`, adapted):
  manifest-first (`app.json` is read first and updated before the session ends); source
  under `src/`; **always finish an edit round by building** (`esbuild` → `dist/bundle.html`)
  and fixing type/build errors — bounded to 2 auto-retries (dyad's number) before
  surfacing; seed demo data so the preview is never empty; never touch files outside the
  workspace (`resolve_in_workspace` guard); no network fetches in generated code — use
  `cc.*`; no committing secrets (there are none to commit — the bridge holds them).
- **Checkpoints:** the workspace is git-inited by the existing loader machinery; the
  executor's post-run commit scan already detects commits. Each completed builder round =
  one commit ("checkpoint vN") surfaced as a chip in chat; *restore* = `git revert`-style
  additive rollback (never history rewrite). Blob-store sweep after every session keeps
  partial work durable.
- **Error loop:** the preview iframe traps `window.onerror` + console via the existing
  postMessage bridge; errors render in the console drawer with **"Fix with AI"**, which
  injects the error text into the build chat (via `agentEvents` / a registered
  `useFrontendTool`). Runtime repair is human-triggered; build repair is automatic and
  bounded — exactly the split the surveyed builders converged on.
- **Describe → generate → refine:** the gallery's create bar seeds the first builder
  message; the builder picks a template shape (tracker / dashboard / form→action /
  report / AI utility) and fills it in. Same blank-canvas-killer as the Workflow RFC §5.4,
  same reason to ship it early.
- **Persona design (Townie's lessons):** keep the builder's system prompt a minimalist
  quickstart for the platform — the `cc` API reference, the workspace contract, ONE
  complete example app — not a maximalist prompt stuffed with examples; and shape the
  `cc` API itself to LLM expectations (boring names, obvious signatures) rather than
  teaching the model quirks. Publish the same doc as the app-workspace `AGENTS.md`, so a
  power user can point Claude Code or Cursor at an exported app folder and get identical
  behavior.

### 4.3 Preview pipeline

- Phase 0/1: the preview pane fetches `dist/bundle.html` bytes through the existing
  workspace file API and renders them in `SandboxedHtml chromeless` (srcDoc, opaque
  origin, zero network). Rebuild-on-change: the builder emits the existing
  `artifact_updated` event → the pane refetches. Device-width toggles are just frame CSS.
- The preview injects the **same `cc` bridge as production** (pointed at the draft app id)
  so integration calls, storage, and identity behave identically in preview and deployed —
  no "works in preview, breaks live" class of bugs. Draft tool-calls always run in
  per-use-confirm mode.
- Later polish: esbuild-wasm in the browser for instant sub-second rebuilds between agent
  rounds (optional; server build remains canonical).

### 4.4 The `cc` SDK — the capability bridge (the differentiator)

A ~200-line `cc-sdk.js` the platform injects into every app frame (preview and
published). All calls are `postMessage`/`MessageChannel` RPC to the parent page; the
parent validates the message schema, checks the app's granted scopes, then calls the App
Runtime API with the **viewer's session**. The frame holds no token, no cookie, no
credential — Airtable-style. Everything is audited as `(user, app, version, scope, action)`.

```ts
await cc.user()                            // { email, name, role }        identity:read
await cc.storage.table("spools").list()    // app-scoped shared Postgres   storage:app
await cc.storage.table("spools").upsert(row)
await cc.storage.kv.get("settings")        // per-user or shared partition
await cc.ai.complete(prompt, {tier})       // gateway /v1, app-attributed  ai:tier-*
await cc.tools.call("clickup.create_task", // declared actions only        tool:*
  { list: "Procurement", title })          //   writes → Action Broker + confirm toast
await cc.agents.run("delivery", msg)       // delegate to a platform agent (Phase 2+)
await cc.fetch(url)                        // proxied egress, per-app allowlist (Phase 3)
```

Design rules:
- **Run-as-viewer by default.** An app can never show user B data user A couldn't see, and
  every write is attributable to the human. `run_as: "author"` is a gated deployment flag
  for automations only (Phase 3, with a persistent "acts as @author" banner).
- **Writes fail closed.** Any `tool:*` action annotated destructive/open-world routes
  through the Action Broker: per-use confirmation toast (mockup 3) until an admin grants
  the scope at publish review; "always allow for this app" is a per-user grant.
- **Tool vocabulary = the existing one.** The proxy exposes the same integration actions
  and risk annotations agents use (`TOOL_ANNOTATIONS`, Integration Registry resolvers,
  MCP registry later) — no second integration surface to maintain.
- **AI is metered per app** (tier + monthly token budget in the manifest), viewer-pays
  semantics for attribution, platform-pays in practice — it all goes through `/v1`
  where cost tracking already lives.
- **Never bake an integration into an app.** When an app needs a service the platform
  doesn't have yet, the fix is registering the service *once*, platform-wide — the
  Integrations page (or the `apis-config` agent, which already exists to discover and
  configure an unfamiliar API) — never a direct `fetch()` from app code, never a key
  pasted into a workspace file. One registration serves every app and every agent that
  declares it; a baked-in call serves nobody but itself and reopens the credential-leak
  surface the whole bridge exists to close. The builder's standing instructions enforce
  this as a refuse-and-redirect rule (§4.0 layer 1), not a suggestion.
- **`cc.tools` growth path: generalize, don't hand-code forever.** Phase 2's registry is
  intentionally a small, explicit, hand-written map (one `ToolSpec` per action) — correct
  for a first tool, wrong as a scaling strategy. The already-designed generalization point
  is `specs/mcp_plugin_integration.md`'s `UnifiedTool` abstraction: once a service is
  registered via the MCP registry (`mcp_servers` table) or the plugin OpenAPI importer
  (`plugins.tools_generated` — currently generated but unwired to any runtime), `cc.tools`
  should resolve actions from *that* registry instead of a bespoke Python function per
  action. Adding tool #2 by hand is fine; adding tool #10 by hand is a sign this
  generalization is overdue — treat it as the Phase 3+ trigger, not a Phase 2 requirement.
- **The design system rides the same frame the SDK does — no second copy.**
  Apps render through `SandboxedHtml.tsx`, the SAME component that backs
  Metorite's own reports and generative-UI cards, so `buildSrcDoc`
  already injects the identical `--cc-*` tokens and `.cc-*` block-kit
  classes (`cc-card`, `cc-btn`, `cc-stats`, `cc-bars`, `cc-donuts`,
  `cc-table`, `cc-callout`/`cc-note`, `cc-chart`, …) into every app frame —
  for free, before the builder writes a line of CSS. The starter scaffold
  (`starter_index_html`) and the builder's own instructions were updated to
  use these directly instead of a hand-duplicated palette, so a fresh app
  is on-brand and token-cheap to extend from round one, and `design.md`
  stays the single source of truth instead of drifting copies.
- **Broker action names are namespaced `app.<service>_<action>`** (e.g.
  `app.clickup_create_task`), distinct from each first-party surface's own action names
  (`clickup.create_task` for the Task Manager, gated by its own per-account credentials).
  `action_broker._HANDLERS` is a single flat dict keyed by action name — an unnamespaced
  collision means the *last-registered* handler silently wins and every subsequent call
  quietly resolves the wrong credential store. Every new `cc.tools` action must add its
  entry under the `app.` prefix; this is a correctness rule, not a style preference.

### 4.5 How apps use AI — and how it rides the existing plumbing (binding)

Every AI capability an app has goes through **one door: the gateway `/v1`** — the same
in-process LiteLLM path every agent already uses. `cc.ai.complete()` → App Runtime API
`POST /apps/{slug}/ai/complete` → `acb_llm.complete()` with the platform's tier aliases.
No SDKs in app code, no provider keys anywhere near an app, no second routing layer.
What that buys automatically, because it's the same choke point:

- **Tiered routing** — the manifest requests a tier alias (`ai:tier-1` etc.), never a
  raw model id; tier→model assignment stays a platform decision (`/settings/models`).
- **Prompt caching, context-window guard, BYOK key store, output-token ceiling** — all
  of `acb_llm` / `v1_compat` behavior applies unchanged.
- **Cost metering** — the existing spend tracking sees app calls like any other caller,
  with the attribution below layered on top.
- Later `cc.ai` sugar (structured output, embeddings via `/v1/embeddings`, a
  `cc.ai.agent()` that delegates to a platform agent) are conveniences over the same
  door — anything that would bypass `/v1` is rejected by design.

### 4.6 Cost attribution & live tracking (extends the observability stack)

Apps are a new **actor class** in the tracking system, not a blind spot — and, as of
this section's implementation, a first-class member of the SAME live office view
agents already get, not a separate/disconnected "app usage" screen.

- **Attribution triple on every AI call:** `(app_slug, app_version, viewer_email)` +
  the calling surface (`app-runtime` vs `app-builder`). Builder-session tokens already
  land in `agent_run` (it's a normal Tier-1.5 chat run of the `app-builder` agent);
  runtime calls write an `app_audit` row (kind=`ai`, tokens, cost, model, latency).
- **Live presence, the same start/end contract agent runs use:** `ai_complete`
  (`gateway/routes/apps/runtime.py`) emits a `kind="app"` start event immediately
  before the provider call and an end event immediately after (success or failure —
  never relying on the 15-minute presence-key TTL self-heal for the common case), with
  a fresh `run_id` per call. `acb_common.activity`'s presence gate
  (`cc:activity:live:{run_id}`) was widened from `kind=="agent"`-only to
  `kind in ("agent", "app")`, so a working app is presence-eligible through the exact
  same mechanism as a working agent — no parallel "is an app busy" system.
- **Per-app cost attribution, not a generic "apps" bucket:** every custom app's AI
  call runs through the SAME `gateway.routes.apps.runtime` module regardless of slug,
  so `acb_llm.context._infer_app_source()`'s stack-walk can only ever resolve the
  shared package name (`"apps"`) — never the individual app. `ai_complete` passes
  `source=f"app:{slug}"` explicitly (a new `acompletion_with_fallback(..., source=)`
  override param) so the existing `kind="model"` cost-rollup event carries the real
  per-app identity; no second event, no double-counted spend.
- **Budgets enforced at the proxy:** per-app monthly token budget + per-user-per-app
  rate limit (manifest fields, platform-clamped defaults). Exceeding budget → 429 with
  a friendly `cc.ai` error the app can render, before any presence/cost event fires
  (a budget-exhausted call never reaches the LLM, so it never appears as "working").
- **Observability UI (`/observability`):**
  - **Office view:** a live app surfaces as its own roster entry (`GET
    /observability/roster`, `kind="app"`, name/icon resolved from the `apps` table) —
    rendered as a small glowing terminal kiosk showing the app's icon (`AppSeat` in
    `office-topdown.tsx`), not a character sprite; there's no human to animate, and no
    "idle app" row — an app only appears while actually consuming tokens, unlike
    agents which list the full registry regardless of status. Apps never join a
    conference-room grouping (that needs a standing/breathing sprite set apps don't
    have).
  - **Feed tab:** `kind="app"` events render with their own "App" badge and the app's
    resolved name, instead of falling through to the generic (and misleading) "Model"
    row shape.
  - **Cost tab:** the existing *by-app* (`by_source`) lens now shows individual app
    slugs instead of one aggregate `"apps"` row, as a direct consequence of the
    explicit `source` override above.
- **Tool calls too:** `cc.tools.call` writes the same audit rows (kind=`tool`) so
  integration usage is attributable per app — one query answers "what is the Filament
  Tracker doing to ClickUp, for whom, how often."

### 4.7 Apps as tools for agents (not just people)

App endpoints are **principal-agnostic**: a "viewer" can be a person *or a platform
agent*. This turns every published app into a potential tool in the agent ecosystem —
the Filament Tracker's data becomes queryable by the delivery agent; the Quote
Calculator's logic becomes callable from a sales chat.

- **Manifest `actions` block** — the agent-facing surface, explicit and typed (nothing
  is exposed implicitly). Four mechanical **v1 kinds**, deliberately no filter/expression
  language (the same small-fixed-vocabulary stance the `tool:` scope grammar already
  takes — `gateway/routes/apps/actions.py`):

  ```jsonc
  "actions": [
    { "name": "get_low_stock", "kind": "storage.list", "table": "spools",
      "description": "List every filament spool" },
    { "name": "get_spool", "kind": "storage.get", "table": "spools",
      "params": { "key": { "type": "string" } } },
    { "name": "log_usage", "kind": "storage.set", "table": "spools",
      "params": { "key": { "type": "string" }, "value": { "type": "object" } } },
    { "name": "reorder", "kind": "tool.call", "tool": "clickup.create_task" }
  ]
  ```

  `storage.list`/`storage.get` read the app's shared `app_data` partition;
  `storage.set` writes it; `tool.call` wraps a tool the app already declared a
  `tool:<name>` scope for (an action can never grant *more* than the app's own scopes
  already allow — reuses `tools.py`'s scope/constraint/broker plumbing wholesale, no
  second ClickUp-calling path). `readonly` is never read from the manifest — it's
  **derived**: hardcoded for the three storage kinds, taken from the wrapped tool's
  real `_TOOL_REGISTRY[...].read_only` for `tool.call`, so a manifest claiming `true`
  for an actually-destructive tool can't skip the broker. When T3 server handlers
  arrive, an action can map to an app HTTP endpoint instead.
- **Grants cover agents:** `app_grants.subject` accepts `agent:<name>` and `agents:*`
  alongside emails/`org` (§4.8's sharing UI — "Specific people…" — currently manages
  person grants; an agent grant is written the same row shape, `role='use'`).
- **Execution identity:** an agent-invoked action runs as the **agent principal**
  (`UserContext(email=<agent_name>, role=AGENT)` — the bare agent name, since
  `can_view`/`can_edit` reconstruct `f"agent:{email}"` themselves to match the stored
  `agent:<name>` grant), never a fabricated human. `app_audit` rows attribute to that
  same identity.
- **Registration into the tool registry (implemented, not `as_tool()`):** granted apps'
  actions are injected as ordinary platform tools on every orchestrator run —
  `orchestrator/app_tools.py`'s `load_app_action_tools(agent_name)` queries
  `app_grants` for LIVE apps granted to `agents:*` or `agent:<name>`, and builds one
  dynamic tool per action named `app_<slug>_<action>` (a synthetic `inspect.Signature`
  from the action's declared `params`, so the LLM sees real per-action parameters, not
  a generic args blob). These are appended into `_inject_agent_tools()`'s own
  `_extra_tools` list — **deliberately not** the `_load_specialist_agents_as_tools()`
  whole-agent-as-delegate path (`orchestrator/agents.py`), which appends tools directly
  to `Agent(tools=...)` *before* injection runs and so never reaches
  `permission_policy.decide()` at all. Routing through `_inject_agent_tools()` instead
  means every dynamic action tool is gated by `_gate_injected_tool()` exactly like
  `web_search` or `write_artifact` — same risk-aware permission check, same audit log.
  Each tool also registers a derived risk annotation into
  `acb_skills.tool_annotations.TOOL_ANNOTATIONS` (read-only storage reads;
  non-destructive, non-open-world for `storage.set`; the wrapped tool's real risk for
  `tool.call`) so the addendum's risk summary stays accurate.
- **No separate execution path.** The injected tool calls
  `gateway.routes.apps.actions.execute_app_action(slug, action, args, user)`
  in-process — the exact function `POST /apps/{slug}/actions/{name}` calls — so an
  agent invoking a tool and a person hitting the API behave identically: same
  authority split, same auditing, same broker queueing. Readonly actions and
  `storage.set` execute immediately for any caller (person or agent) — `storage.set`
  only ever touches the app's own already-publish-reviewed storage. A non-readonly
  `tool.call` auto-applies (`AuthorityTier.AUTONOMOUS`) only for a person who can also
  edit the app (owner/editor testing their own app); every other caller — any other
  person, or **any agent, unconditionally** — proposes at `AuthorityTier.SUGGEST`
  (`NEEDS_APPROVAL`, no bypass): there's no synchronous confirm-toast possible for an
  unattended API/agent caller, so none is offered.

### 4.8 Publish, versions, sharing

- **Publish** = build → snapshot: an immutable `app_versions` row (manifest + bundle
  sha256 + release notes + `scope_set_hash`), bundle bytes into the blob store, git tag.
  The stable app URL always serves the pointed-at version; **rollback = repoint** (Apps
  Script's deployment model).
- **Visibility:** `private` → `specific people` → `org` (Google-Doc mental model, mockup 1's
  publish modal). "Specific people…" is an email-chip picker (`gateway/routes/apps/grants.py`
  — `GET/POST /{slug}/grants`, `DELETE /{slug}/grants/{subject}`, edit-gated, upsert on
  re-share) that writes one `app_grants` row per grantee; the same row shape covers an
  agent grant (`agent:<name>`/`agents:*`, §4.7). Org-wide publish **with write scopes**
  requires admin review — one row in the existing Approvals inbox showing the scope diff.
  Re-review triggers only when the **scope-set hash changes**, not on every version
  (Apps Script's rule — this is what keeps iteration friction near zero while keeping
  consent honest).
- **Consent/disclosure:** a blocking first-open interstitial, platform-rendered (never
  app-rendered), listing the LIVE (published+reviewed) version's scopes in plain
  language before the run page fetches the bundle at all. `GET /{slug}` computes
  `needs_consent`/`live_scopes`/`live_scope_set_hash` for the caller (always `false` for
  an owner/editor — they already know what they built); `POST /{slug}/consent` re-verifies
  the client's claimed hash against the LIVE `app_versions` row server-side and 409s
  `scope_set_changed` on a stale view (a new version published between page load and the
  click) rather than silently recording consent to an outdated scope set — the run page
  re-fetches and re-shows the interstitial on that path. Recorded per `(user, app)` as
  `app_grants.consented_scope_hash`, upserted without ever downgrading an existing
  edit/own grant. For v1 with ~20 users this doubles as *disclosure* — teammates learn
  what LLM-written code can touch.
- **Remix/fork:** any viewer can "fork your own copy" into their workshop (Claude
  Artifacts' remix, Val Town's fork) — the org's library compounds.
- **Suggest a change:** viewers who can't edit open a pre-seeded builder chat on a fork;
  the owner gets a diff-style proposal (Phase 2; the `pending_commit` UX pattern reused).

### 4.9 Testing & evaluation

Mirrors the platform's own `evals/` convention (`evals/README.md`: "one golden case per
behaviour", fixtures over live dependencies, CI-visible) — applied to apps, but
**authored in plain English through chat**, never hand-written YAML, consistent with
"chat is the editor" (§4.0/ADR-027). No new backend route or table: scenarios live in a
workspace file (`tests.json`) served by the existing files API (§4.11).

**The load-bearing design decision: scenarios run against an ephemeral, in-memory `cc`
store — never the real backend.** A test run never creates a real ClickUp task, never
spends real AI budget, never mutates a teammate's actual app data. This is what makes
testing safe to run constantly (on every build, automatically) rather than something a
non-technical owner has to remember to do carefully:

- **In-frame interaction helpers** (`__ccTest` — a small addition to the same injected
  SDK script, always present, zero new capability: the app already fully owns its own
  DOM under `sandbox="allow-scripts"`, this just gives an *outside* runner a channel to
  trigger `click`/`type`/`select` on it, since the opaque origin blocks direct access).
  Real DOM events fire — a scenario catches "the button isn't wired to a handler," not
  just backend logic bugs, which is the difference between testing the app and testing
  a function.
- **A self-contained client-side runner** (`testRunner.ts`, not the visible preview's
  `useCcBridge`): creates its own hidden offscreen `<iframe>` from the current draft
  bundle, seeds an in-memory store from the scenario's `seed`, drives the step sequence,
  answers `cc.storage`/`cc.tools.call`/`cc.ai.complete` from memory (tool calls resolve
  to a canned/generic success payload; AI calls to a canned string) rather than fetching,
  evaluates assertions against the resulting in-memory state (plus any DOM text/existence
  checks), tears the frame down, returns a result. Zero backend load, zero new server
  infrastructure — the same "compute donated by the viewer" property that makes the rest
  of the runtime cheap (§7.6) applies here too.
- **Scenario format** (`tests.json`, small fixed vocabulary — deliberately not
  arbitrary/`eval`'d JS, so an LLM authors it reliably and the runner stays simple):
  ```jsonc
  [{ "id": "log-usage-decreases-stock", "name": "Logging usage decreases stock",
     "seed": { "storage": { "spools": { "spool-1": { "value": { "remaining": 10 } } } } },
     "steps": [
       { "action": "click", "selector": "[data-test=log-usage-spool-1]" },
       { "action": "type", "selector": "#usage-amount", "text": "2" },
       { "action": "click", "selector": "#confirm-usage" }
     ],
     "assertions": [
       { "kind": "storage", "table": "spools", "key": "spool-1", "path": "remaining",
         "op": "lt", "value": 10 }
     ] }]
  ```
  Step actions: `click` | `type` | `select` | `wait` (capped, an escape hatch for async
  UI updates). Assertion kinds: `storage` (dot-path compare: `eq`/`neq`/`lt`/`lte`/`gt`/
  `gte`/`contains`/`exists`/`not-exists`) | `dom-text` | `dom-exists`.
- **Authoring stays conversational.** The builder proposes/updates a scenario whenever
  it ships a testable behavior — the same instinct as its checkpoint discipline — and
  the user reviews it as a plain-English checklist in the Workshop, never the JSON
  directly. Asking "test that logging usage decreases stock" is a normal chat message,
  same as any feature request.
- **Automatic, proactive surfacing** (the non-technical-friendly part): scenarios re-run
  after every builder turn (same trigger as the existing sync/checkpoint refresh), with
  a compact pass/fail badge near Publish — a regression is caught before the owner goes
  looking for it, the same "Fix with AI" one-click loop the Phase 1 console drawer
  already established, seeded with which scenario broke. Publish shows the last known
  result as an informational banner alongside the conformance scan — visible, not a
  hard gate (consistent with how conformance *warnings*, as opposed to *errors*, already
  behave — a failing scenario is the owner's call, not a platform-contract violation).
- **Deliberately out of v1 scope:** a fixture/"test as a brand-new user" toggle on the
  *live* preview (would need to touch the shared `useCcBridge`, not just the isolated
  runner — a real but separable follow-up); screenshots/visual diffing; anything
  resembling a full E2E framework. The assertion vocabulary stays intentionally small.

### 4.10 Data model (new tables, one migration)

```
apps                -- the editable definition (edit-model)
  id uuid pk · slug unique · name · icon · description
  owner_email · visibility enum(private,people,org) · status enum(draft,live,archived)
  manifest jsonb · workspace_path · live_version int
  builder_session_id · created_at · updated_at

app_versions        -- immutable published snapshots (run-model)
  id uuid pk · app_id fk · version int
  manifest jsonb · bundle_sha256 · release_notes
  scope_set_hash · published_by · published_at
  review_status enum(auto,pending,approved,rejected)

app_grants          -- sharing + consent, forward-compatible with org-research roles
  app_id fk · subject text ('org' | email | 'agent:<name>' | 'agents:*')
  role enum(use,edit,own)
  consented_scope_hash · granted_by · created_at

app_data            -- the cc.storage backing store
  app_id fk · table_name · key text · value jsonb
  user_scope text null    -- null = shared row, else per-user partition
  updated_by · updated_at
  pk (app_id, table_name, key, coalesce(user_scope,''))

app_audit           -- every bridge call (cheap, append-only; queryable per app)
  id · app_id · version · user_email · kind enum(open,storage,ai,tool)
  detail jsonb · at
```

Bundle bytes ride the existing blob store; the workspace rides the existing git/blob
machinery. `pending_actions` (writes) and the Approvals UI (publish review) are reused,
not duplicated.

### 4.11 API surface

Gateway `routes/apps/` (new module, same layering as `routes/tasks/`):

```
# lifecycle
GET/POST      /apps                       list (visibility-filtered) / create+scaffold
GET/PATCH/DEL /apps/{slug}
GET/PUT       /apps/{slug}/files[?path=]  workspace passthrough (reuses containment guards)
POST          /apps/{slug}/build          esbuild → bundle + diagnostics
GET           /apps/{slug}/bundle?version=draft|live|N     bytes for the iframe
POST          /apps/{slug}/publish        snapshot + (maybe) review row
POST          /apps/{slug}/rollback       repoint live_version
GET/POST/DEL  /apps/{slug}/grants         sharing

# App Runtime API (what the cc bridge hits — every call scope-checked + audited)
GET           /apps/{slug}/me
GET/PUT/DEL   /apps/{slug}/data/{table}[/{key}]
POST          /apps/{slug}/ai/complete    → gateway /v1, app-attributed + budgeted
POST          /apps/{slug}/tools/{tool}   destructive → pending_actions + confirm
POST          /apps/{slug}/actions/{name} manifest-declared action (people AND agents)
GET           /apps/{slug}/usage          token/cost aggregates for the info popover
```

`tests.json` (§4.9) needs no route of its own — it rides the existing
`GET/PUT /apps/{slug}/files[?path=]` passthrough like any other workspace file.

Frontend: `/api/apps/[...path]` catch-all BFF proxy (copy `api/tasks/[...path]/route.ts`
verbatim — it already handles auth headers, retries, binary passthrough). Pages:
`build/apps/page.tsx` (gallery), `build/apps/[slug]/page.tsx` (run),
`build/apps/[slug]/edit/page.tsx` (workshop). Nav needs zero changes for the section;
Phase 1 adds a fetched-apps merge into the three `NAV_SECTIONS` consumers for pinned apps.

---

## 5. UX walkthrough (see mockups)

**Gallery** (`mockup-apps-home.html`): describe-to-create bar with Fracktal-flavored
hints; filter pills (All / Mine / Shared with me / Team-wide / Drafts); app cards with
scope chips, author, and user avatars; "Your workshop" drafts row; "Start from a shape"
template row (tracker · dashboard · form→action · report · AI utility). Custom Apps also
gets its own sidebar section with pinnable per-app entries.

**Workshop** (`mockup-workshop.html`): topbar = back · name · Draft chip · saved-state ·
Preview/Code segmented toggle · version history · Share · **Publish**. Left: device
toggles, preview address (draft vs live pill), the sandboxed app, console drawer with
build status and **✦ Fix with AI**. Right: build chat — collapsible "working" step
timelines (scaffolded → storage table → files written → bundle built → preview vN),
checkpoint chips with restore, a capability-request card when the agent adds a scope,
suggestion pills, composer with model + granted-scope chips. Code view: read-only file
tree + source with the manifest highlighted; "edits happen through chat" note.
Publish modal: release notes · visibility radio (Only me / Specific people / Everyone) ·
capability review list (AUTO vs ADMIN REVIEW rows) · approval-inbox notice.

**Running app** (`mockup-app-run.html`): platform chrome around the sandboxed frame —
glyph, name, Live·v2, author, scope chips, **"runs as <viewer>"** identity pill, Suggest
a change, pin, info popover (version, owner, usage, plain-language scopes, version
history / open in workshop / report issue). Bottom status strip: sandbox + storage +
audit indicators, rollback, **fork your own copy**. The Action-Broker toast demonstrates
a gated ClickUp write with Approve / Deny / "always allow for this app".

---

## 6. Feature set (prioritized)

**P0 — the loop works (Phases 0–1):**
create from description · builder chat with streaming tool timeline · live sandboxed
preview · single-file + React/esbuild apps · checkpoint/restore per AI edit · bounded
build auto-repair + Fix-with-AI · publish → immutable version → run page · **publish
conformance scan (platform-contract enforcement, §4.0)** · rollback · private/org
visibility · `cc.user` + `cc.storage` + `cc.ai` · gallery + sidebar section ·
per-app audit trail.

**P1 — team-grade (Phase 2):**
`cc.tools` integration actions with manifest scopes, per-use confirm, publish review via
Approvals inbox · **test scenarios (§4.9) — ephemeral-store runner, auto-run + pass/fail
badge, "Fix with AI" on regression, publish-modal status** · share-with-specific-people +
first-open consent screen · **manifest
`actions` + agent grants + `app_<slug>_<action>` tool registration** (risk-annotated,
golden-trajectory-eval'd per harness rules) · by-app cost lens in Observability +
per-app budgets · **usage stats on gallery cards** · **pinned apps in sidebar** ·
**fork/remix** (source files only — never runtime data/sharing/history, fresh git
history, forker becomes sole owner of a new private draft) · **templates gallery**
(`is_template` flag, editor-toggleable from the gallery card; a "Templates" filter
pill over the existing app list; "Use" reuses fork/remix — no new copy mechanism)
· suggest-a-change · app-to-app data reads (quote calculator ← filament costs) —
remaining.

**P2 — power (Phase 3+):**
real URLs on a usercontent subdomain with CSP headers · scoped short-TTL app tokens ·
`cc.agents.run` + app-triggered workflows (ties into the Workflow Editor RFC) · cron/
webhook-triggered `run_as: author` automations · `cc.fetch` egress allowlists ·
element↔source "edit this button" targeting · container runtime for server-side apps
(post-BO-7) · export/import an app as a folder. **Monaco read/edit round-trip for
developers — shipped** (Advanced view, ahead of the rest of P2 — see §3 item 1).

**Explicit non-features:** a drag-and-drop editor (chat is the editor) · a browser IDE
**for platform code** (ADR-014 stands — agents/skills/orchestrator stay VS-Code-and-
Git-only; the Advanced view's Monaco editor is scoped strictly to one Custom App's own
workspace, a fundamentally different surface — see §3, item 1) · customer-facing/
external hosting (internal-only, like everything else) · a second integration/
credential system (the Registry is the only one).

---

## 7. Security model

Threat model: LLM-generated (and occasionally human-pasted) code, authored by
non-security-experts, possibly prompt-injected during generation, running in every
teammate's authenticated browser. The stance: **contain, broker, gate, audit** — never
trust, never sanitize-and-inline (sanitizers neutralize markup; apps *are* scripts).

1. **Containment (browser).** Published + preview apps render via `SandboxedHtml`:
   `sandbox="allow-scripts"` (no `allow-same-origin` → opaque origin: no cookies, no
   storage, no parent DOM), CSP `default-src 'none'; connect-src 'none'; …` — zero
   network egress; the postMessage bridge is the only channel, validated on
   `event.source` + schema, payloads truncated. The platform session cookie should move
   to `__Host-` prefix regardless (BO-2 adjacent).
2. **Brokered capabilities (server).** Scope checks and write gating happen **in the
   gateway**, never in the frame; the SDK's checks are UX sugar (Airtable's split).
   Effective permission = manifest ∩ app grant ∩ viewer. Destructive tools fail closed
   into `pending_actions` (Hermes-style single choke point — CH-2). Undeclared scope →
   hard 403 + audit row.
3. **Gates (humans).** Org-wide publish with write scopes → admin review of the scope
   diff. Scope-set change → re-consent. Per-use confirm until granted. Every version is
   an immutable, diffable snapshot with a named publisher — provenance is the chat
   transcript + git history.
4. **Real-URL serving (Phase 3, deliberate).** When apps get real URLs (deep links,
   per-app localStorage), copy the industry pattern wholesale: dedicated hostname
   (`*.apps-uc.<host>`, wildcard cert + one Caddy rule), **CSP `sandbox` sent as a
   response header** (a direct visit is still sandboxed), `connect-src` pinned to the App
   Runtime API only, `frame-ancestors` pinned to the Control Plane, **no Set-Cookie ever**
   on that host, short-TTL audience-bound JWT delivered over the bridge (never baked into
   HTML). Until then, srcDoc keeps us strictly tighter.
5. **Build-side hygiene.** The builder session runs under the existing permission policy
   (workspace jail, out-of-workspace writes denied, secret-scrubbed env, timeouts). It is
   process-level hygiene, not a container — same honest caveat as `code_task`. **BO-7
   remains the hardening path** and its first consumer should be the app build step;
   T3 server-side apps are blocked on it by design.
   *Credential-handling rule (the Budibase CVE lesson):* integration credentials are
   resolved and pinned to their service/action **server-side only**; no secret — redacted
   or otherwise — ever appears in `app.json`, workspace files, or any app-editable
   config, so there is nothing to repoint or round-trip.
6. **Execution model & dependency isolation — decided, not deferred.** For T1/T2 (every
   app shipped through Phase 2a) there is **no per-app server-side execution to sandbox in
   the first place**: an app's JS never runs on the gateway/orchestrator — it only ever
   runs client-side, in the viewer's own browser tab, contained by layer 1 before it can
   touch anything shared. This is unchanged by per-app npm dependencies (below) — a T2
   app that installs `three` still only ever ships bundled client-side JS to the browser,
   with the exact same CSP/srcDoc containment as the vendored-only default; nothing it
   installs ever runs on the gateway/orchestrator. `cc.tools` handlers are hand-written
   platform Python using dependencies the gateway already has; apps never supply code
   that runs server-side, so no app can ever introduce a package requirement there. This
   is also why the model is cheap, not just safe: zero idle-container memory floor, zero
   per-app image/cold-start cost — the compute for running an app is donated by whoever is
   viewing it, not reserved on the VPS. A container-per-app model would be the *lossy*
   choice here, paying a fixed resource tax per app for isolation the browser already
   provides for free.
   *Per-app build-time dependencies (T2, opt-in):* an app MAY `npm install` its own
   packages beyond the vendored react/react-dom/`@cc/ui` — e.g. `three`/
   `@react-three/fiber` for a 3D viewer — via `install_t2_deps.mjs`, the only sanctioned
   path (never a raw `npm install`). Isolation between apps is structural: each app's
   `node_modules` lives inside that app's own workspace directory, already the
   durability/build/publish unit for exactly one app, so nothing installed for one app is
   ever reachable from another's. The install script is the mitigation for what's
   actually new here — build-time code execution and disk, not runtime app isolation:
   `--ignore-scripts` blocks the standard npm supply-chain RCE vector (a malicious
   package's install/postinstall script running arbitrary code before any of it ships
   anywhere — same rationale as `acb_skills/loader.py`'s wheel-only installs for the
   gateway's own Python deps), a package-spec allowlist regex rejects anything that isn't
   a plain name/version token (no flags, no URLs), and a `node_modules` size cap stops
   one app from eating a meaningful fraction of the VPS's disk. `node_modules` was
   already excluded from the durability mirror and file-tree UI (`WORKSPACE_SKIP_DIRS`)
   before this — a lost/rehydrated workspace recovers `dist/bundle.html` (the durable
   entry-file carve-out) but not `node_modules`, so further edits need a fresh install
   before the next build, an accepted tradeoff for staying container-free per app.
   *The one honest nuance:* the **builder's own coding session** (writing the app, not
   running it) shares the gateway's Python process with every other first-party agent —
   unchanged from how `task-manager`/`apis-config` already work, not something Custom
   Apps introduces or worsens. `agent-app-builder`'s `tool_scope` deliberately excludes
   `install_dependency`/`run_script`/`code_task`, so no Workshop conversation can trigger
   a `pip install` into that shared venv — the theoretical shared-venv risk items 5/`BO-7`
   already track platform-wide are not reachable from an app conversation today. This is
   unrelated to `install_t2_deps.mjs` above: that's `npm`, not `pip`, runs via the same
   native shell the builder's own coding session already uses to run `build_t2.mjs`
   (never the Python `install_dependency` MAF tool), and writes only into the one app's
   own workspace `node_modules` — never the gateway's shared Python venv.
   *Where this actually changes:* **T3** (server-side app code, still deferred) is the one
   point where per-app dependency isolation becomes a real, new question, because app code
   would then execute on the VPS. The answer on record is still **not** Docker-per-app —
   it's Val Town's runtime lineage (§2.5): a warm pool of short-lived, permission-flagged
   subprocesses, sized for a small box, chosen after they proved in production that
   language-level JS sandboxes don't hold and OS-level process isolation does, without
   paying a full container's fixed cost per app.
7. **Known side channels, accepted for v1:** a malicious app can still phish inside its
   own frame (mitigated by the platform chrome making authorship/scopes visible and
   report-issue one click away) and exfiltrate only what the bridge returns to it (bounded
   by scopes; audited). These are the same residuals every surveyed platform carries.

---

## 8. Where this runs / cost

Nothing new to operate in Phases 0–2: apps are rows + blobs + one agent; preview and
runtime are browser-side; builds are esbuild inside the existing builder session (~100 ms
for small apps). LLM cost is the builder session (a normal Tier-1.5 chat) plus
app-attributed `cc.ai` calls, all metered through `/v1`. The 4 GB VPS constraint that
killed container-per-run for agents (permissions spec §9) is exactly why v1 apps are
static + brokered rather than per-app servers.

---

## 9. Key decisions & open questions

1. **Frontend-only apps in v1 — DECIDED (proposed).** `cc.*` replaces the server for
   small software; T3 waits for BO-7. When T3 comes, the leading substrate is **not**
   Docker-per-app but Val Town's proven shape: warm Deno subprocesses with explicit
   permission flags over Unix sockets, supervised by the gateway (`deno-http-worker`
   pattern, ~200 lines of supervisor) — far better fit for the 4 GB VPS than containers.
   Revisit when an app genuinely needs server compute (long jobs, secrets-bearing SDKs,
   websockets).
2. **Builder = Copilot-SDK Tier 1.5 session — DECIDED (proposed).** It is interactive
   coworker chat, squarely inside the runtime policy (root constraint #6/#9); no new
   execution path is introduced.
3. **Draft ADR-027 (scopes ADR-014):** *Custom Apps are end-user artifacts, not platform
   code.* The Control Plane may create/edit **apps** through conversational authoring;
   it still contains no editor for agents, skills, or platform code. Apps are stored as
   workspaces + immutable versions, run sandboxed, and acquire capabilities only via
   manifest scopes brokered by the gateway. (Also: amend the `project_plan.md` non-goals
   line to carve this in explicitly.)
4. **Where app identity lives:** new `apps` tables (§4.6), *not* `dynamic_agents` — an app
   is not an agent even though its builder session is one. The builder session id is a
   normal `chat_session` so history/reconnect just work.
5. **Sharing model now vs org-research later:** ship the minimal
   `app_grants(use/edit/own)`; it maps 1:1 onto the researched role/permission model when
   that lands (apps become a `module` with visibility, grants become role assignments).
6. **`cc.storage` shape:** KV + JSON tables (Postgres jsonb) is enough for v1. SQL-ish
   query? Datasette Apps' answer (parameterized stored queries authored by the builder,
   approved like scopes) is attractive for v2 — decide when a real app hits the wall.
7. **Scope granularity for tools:** start with `service.action` (+ optional param
   constraints). Row/list-level constraints (only the "Procurement" list) are encoded as
   constraints the proxy enforces where the connector supports it — how far to push this
   is a per-connector call.
8. **Preview address bar cosmetics:** srcDoc has no URL. Show a stylized
   `app://{slug}/draft` (mockup does) or stand the preview route up early? Cosmetic;
   defer.
9. **Does the Workshop chat also live in `/chat`?** i.e. can the orchestrator spawn an
   app from any conversation ("make me a tool for this") and hand off to the Workshop?
   Strongly yes eventually (it's one `spawn` + redirect) — but keep it Phase 2 to avoid
   scope creep in the first cut.
10. **Naming.** "Workshop" (builder) / "Custom Apps" (the shelf, already in the nav).
    Alternative: one word — "Apps" — everywhere, with Workshop as the edit mode. Bikeshed
    at implementation.

---

## 10. Phased roadmap

**Phase 0 — Spike the loop (1–2 wk).** `apps` table + migration; `routes/apps` CRUD +
files + bundle passthrough; `/build/apps` gallery (cards from the table) + create-from-
description; `app-builder` agent registered with the workshop harness prompt; Workshop
page = `AgentChat` embed + `SandboxedHtml` preview fed by the workspace file API;
single-file HTML apps; publish = version row + run page. *Goal: describe → build → publish
→ a teammate opens it, end-to-end, in two weeks.*

**Phase 1 — MVP product (2–4 wk).** React template + esbuild build step + bounded
auto-repair; checkpoint/restore chips; console drawer + Fix-with-AI; `cc-sdk` bridge v1
(`user` / `storage` / `ai`) + App Runtime API + `app_data`/`app_audit`; **full publish
conformance scan** (external-URL/key/service-worker/storage-reliance checks — a basic
external-URL scan ships in Phase 0); versions + rollback UI; visibility private/org;
sidebar Custom Apps section (a static nav link at this point — pins landed later, see
Phase 2 note below); blob-store durability sweeps — remaining, only on-demand
sync + lazy rehydrate exist.

**Phase 2 — Capabilities & sharing (3–5 wk).** `cc.tools` proxy over the Integration
Registry with manifest scopes + Action-Broker gating + per-use confirm toast; publish
review row in Approvals for org+write apps; testing & evaluation (§4.9); share-with-people
+ first-open consent (scope-set-hash rule); manifest `actions` + registration as
orchestrator agent tools (§4.7) — **shipped**; usage stats on gallery cards + pinned
apps in the sidebar — **shipped** (a later pass, batched together since both were
cheap and the data/infra — the usage endpoint, the sidebar's dynamic-badge pattern —
already existed); fork/remix — **shipped** (`POST /{slug}/fork`, view access is
enough — copies source files only, resets sharing/runtime data/history, fresh git
history, forker owns the new private draft); templates gallery — **shipped**
(`apps.is_template`, toggled via the existing generic `PATCH /{slug}`; the
gallery's "Templates" filter pill client-side-filters the already-fetched
`GET /apps` list — no new listing endpoint; "Use" on a template card calls the
same `POST /{slug}/fork` and lands in the new copy's editor); mobile design
philosophy — **shipped** (a "Mobile & responsive layout" section in
`agent-app-builder/instructions.md` — keep the viewport meta, reflow over
fixed widths, the block-kit classes already do this; a desktop/phone-width
preview toggle in the Workshop's Preview tab, so it can be checked before a
round ends; a touch-target `min-height` bump on the injected design-token CSS
that every app already gets for free); the Workshop editor's OWN UI is
mobile-responsive too (§4.2, separate from apps-being-built — see the App
Workshop mobile-layout work above); proper icons + a UI/UX quality bar —
**shipped**: `lucide-react` joins the T2 default vendor set (real icon
components, not hand-rolled SVGs), and the `ccIcon`/`data-cc-icon` bridge T1
apps already had documented access to is now actually wired up end to end
(`extractCcIconNames` + `buildIconMap`, both the Workshop preview and the
published run page) — it silently resolved to nothing before this.
`instructions.md` gained a "Quality bar" section translating Apple's HIG
principles (one primary action, hierarchy from size/weight not color alone,
chrome defers to content, generous spacing, purposeful motion) and a finding
from Anthropic's own research on agent harnesses for app-building — a
generator grading its own UI "confidently praises the work even when...
obviously mediocre," so the instruction is to actually LOOK at the preview
(the new desktop/phone toggle) rather than trust the code read, and to judge
against four concrete criteria (coherent, original — avoid "purple gradients
over white cards" — craft, functional) instead of a vague "does it look
good"; two externally-referenced Claude Skills (`ui-styling`,
`ui-ux-pro-max` from `nextlevelbuilder/ui-ux-pro-max-skill`) were evaluated
for portable content — `ui-styling` (shadcn/Radix/Tailwind) doesn't apply,
since T2's esbuild pipeline has no PostCSS/Tailwind step and `@cc/ui`
already covers the component-kit role; `ui-ux-pro-max`'s rule content
surfaced one real, fixable gap: its documented 44×44px (iOS) / 48×48dp
(Android) minimum touch target is Metorite's own established number
too (already used by the app shell's mobile nav bar), but the injected
design-token CSS in `SandboxedHtml` had shipped at 36px — bumped to 44px,
and `instructions.md`'s touch-target line now states the number instead of
"comfortable minimum"; suggest-a-change — remaining.

**Phase 3 — Real URLs & automations (3–5 wk).** Usercontent-subdomain serving with the
full CSP header set + scoped short-TTL tokens; `cc.agents.run`; cron/webhook triggers
with `run_as: author` (persistent banner; joins the Workflow Editor's trigger table
thinking); `cc.fetch` allowlists — remaining (deliberately deferred). Monaco round-trip
for developers — **shipped** ahead of the rest of this phase, as the Workshop's
Advanced view (§3 item 1).

**Phase 4 — Hardening & headroom (ongoing).** BO-7 container for the build step, then the
T3 container runtime for server-side apps; element↔source targeting; MCP-UI/A2UI interop
watch (genUI Phase 3); app analytics; org-research permission model adoption.

---

## 11. References

- Mockups: [`mockup-workshop.html`](mockup-workshop.html) ·
  [`mockup-apps-home.html`](mockup-apps-home.html) ·
  [`mockup-app-run.html`](mockup-app-run.html)
- Sibling RFC: [`docs/workflow-editor/README.md`](../workflow-editor/README.md)
- Metorite internals this builds on:
  `workbench/control_plane/src/lib/nav.ts` (the declared `/build/apps` slot) ·
  `src/components/{AgentChat,SandboxedHtml,DocumentPane,SidePanelEditor}.tsx` ·
  `src/app/tasks/components/AssistantRail.tsx` (embed pattern) ·
  `apps/services/orchestrator/orchestrator/{code_session.py,executor.py,event_translator.py,stream_relay.py}` ·
  `packages/acb_skills/acb_skills/{loader.py,integrations.py,permission_policy.py,code_tools.py,write_artifact.py}` ·
  `apps/services/gateway/gateway/routes/{agent.py,workspace.py,integrations.py,actions.py,v1_compat.py}` ·
  `infra/postgres/` (`66_pending_actions`, `71_agent_blob_store`, `03_pending_commits`)
- Specs: `project-docs/specs/agent_coding_skill.md` ·
  `specs/generative_ui_2.md` · `specs/permissions_sandbox_b6.md` ·
  `specs/multi_user_organization_research.md` · `specs/chat_agent_framework_review_2026-07.md` ·
  `/FOUNDATION_BUILDOUT_CHECKLIST.md` (BO-1/2/7/14)
- Prior art (surveyed 2026-07-25):
  [dyad](https://github.com/dyad-sh/dyad) ·
  [Cloudflare VibeSDK](https://github.com/cloudflare/vibesdk) ·
  [E2B Fragments](https://github.com/e2b-dev/fragments) ·
  [bolt.diy](https://github.com/stackblitz-labs/bolt.diy) ·
  [LibreChat artifacts](https://www.librechat.ai/docs/features/artifacts) ·
  [app.build agent](https://github.com/neondatabase/appdotbuild-agent) ·
  [Onlook](https://github.com/onlook-dev/onlook) ·
  [open-lovable](https://github.com/firecrawl/open-lovable) ·
  [GitHub Spark](https://docs.github.com/en/copilot/concepts/spark) ·
  [Claude-powered artifacts](https://claude.com/blog/claude-powered-artifacts) ·
  [Datasette Apps](https://simonwillison.net/2026/Jun/18/datasette-apps/) ·
  [Val Town std/auth](https://docs.val.town/api/authentication/) ·
  [Val Town: the first four runtimes](https://blog.val.town/first-four-val-town-runtimes) ·
  [deno-http-worker](https://github.com/val-town/deno-http-worker) ·
  [How we built Townie](https://blog.val.town/codegen) ·
  [smallweb](https://github.com/pomdtr/smallweb) ·
  [Windmill app policies](https://www.windmill.dev/docs/apps/app-runnable-panel) ·
  [Appsmith security model](https://docs.appsmith.com/product/security) ·
  [ToolJet version control](https://docs.tooljet.com/docs/development-lifecycle/release/version-control) ·
  [Budibase CVE-2026-48152](https://github.com/Budibase/budibase/security/advisories/GHSA-4q6h-8p4v-67vq) ·
  [Cloudflare Workers for Platforms](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/how-workers-for-platforms-works/) ·
  [Home-cooked software (Appleton)](https://maggieappleton.com/home-cooked-software) ·
  [An app can be a home-cooked meal (Sloan)](https://www.robinsloan.com/notes/home-cooked-app/) ·
  [Google Apps Script scopes/deployments](https://developers.google.com/apps-script/concepts/scopes) ·
  [Airtable Blocks SDK](https://github.com/Airtable/blocks) ·
  [Figma plugin sandbox](https://www.figma.com/blog/how-we-built-the-figma-plugin-system/) ·
  [web.dev: securely hosting user data](https://web.dev/articles/securely-hosting-user-data) ·
  [GitHub Enterprise subdomain isolation](https://docs.github.com/en/enterprise-server@3.16/admin/configuring-settings/hardening-security-for-your-enterprise/enabling-subdomain-isolation) ·
  [Observable security model](https://observablehq.com/documentation/security/data-security-and-privacy) ·
  [Salesforce Canvas signed request](https://developer.salesforce.com/docs/atlas.en-us.platform_connect.meta/platform_connect/canvas_app_signed_req_authentication.htm)
