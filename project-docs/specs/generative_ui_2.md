# Generative UI 2.0 — Immersive HITL UI for Agents

**Status:** Phase 1 + proactive/cross-runtime parity (§7) shipped 2026-07-23 · **Owner:** Vijay
**Scope:** how agents generate rich, interactive, on-brand UI — inline in chat AND
as immersive side-panel views — with first-class human-in-the-loop interaction.

**Companions:** `chat_ux.md` §12 (AG-UI protocol headroom) ·
`chat_agent_framework_review_2026-07.md` §8 (co-authoring) ·
`workbench/control_plane/DESIGN_SYSTEM.md` (tokens/components).

---

## 1. Review — how it worked before this spec

Three-tier pipeline, all delivered over the AG-UI `CUSTOM`/`generative_ui` event
(`acb_skills/write_artifact.py::emit_generative_ui` → executor queue → SSE →
`useAgentChat` → `MessageBubble` → `GenerativeUINode`):

| Tier | What | Strength | Weakness |
|---|---|---|---|
| 1 | Whitelisted component tree (card/table/badge/…/button/icon) | safe, declarative | verbose `{type,props,children}`; display-only — **no input primitives** |
| 2 | Named React templates (6: weather/stats/bars/spark/comparison/progress) | data-only, on-brand, animated | thin coverage; **no interactive template** |
| 3 | Sandboxed HTML iframe (`SandboxedHtml`, opaque origin, CSP-locked, `ccAction`/`ccSubmit` bridge, `--cc-*` tokens, report kit CSS) | unlimited | hand-written per run — token-heavy, inconsistent |

**The four structural gaps** (verified in code):
1. **No blocking submit.** genUI buttons/`ccSubmit` route through `submitText` →
   a NEW chat message (queued/steered if a run is active). Only
   ElicitationCard/ConfirmationCard could resume a parked run via
   `request_id` → `/agent/respond-input`. A rich form could never pause the turn.
2. **No panel surface.** The side panel (`sidePanelStore`) was file-tab-only; the
   only route to immersive UI was writing an `.html` file to the workspace (a
   filesystem round-trip that abandons Tier 1/2 React entirely).
3. **No form/choice machinery.** Structured input = hand-rolled Tier-3 HTML.
4. **Efficiency.** The agent restates design in every custom-HTML emission;
   templates (data-only) are the cheap path but covered too few scenarios.

## 2. Architecture (the Claude-artifacts model, adapted)

**One payload, two surfaces, two interaction modes** — orthogonal axes on the
same `emit_generative_ui` spec:

```
emit_generative_ui({
  surface: "inline" | "panel",   // WHERE: transcript card vs immersive side panel
  hitl:    true | absent,        // HOW: block-and-return vs fire-a-new-message
  title:   "...",                // panel tab label / chip label
  ...one of: template | tree | html
})
```

- **`surface:"panel"`** → the spec opens as a side-panel tab (`openGenUI` in
  `sidePanelStore`, `kind:"genui"`), rendered natively by `GenerativeUINode` —
  no file round-trip, Tier-2 templates keep their React implementations. The
  transcript shows a compact re-open chip (persisted via `custom_events`, so it
  survives reload). Auto-opens on arrival, artifacts-style.
- **`hitl:true`** → the tool call parks on the SAME Future registry as
  `ask_questions` (`_pending_user_input` + `wait_user_future` heartbeat); the
  spec carries a `request_id`; any interaction resolves it through
  `/agent/respond-input` and the run resumes with the values as the tool
  result. Non-blocking interactions keep the legacy send-a-message path.
  Frontend routing: `MessageBubble`/`SidePanelEditor` → `request_id` present →
  `postRespondInput` (fallback: `submitText` so an answer is never lost).
- **Panel interactions** travel over the global `agentEvents` bus
  (`genui_panel_action`) because the side panel has no direct line to
  AgentChat's send/HITL plumbing.

**Design language:** everything renders from the app's semantic tokens
(`var(--primary)`, `--accent`, `--success`, `--warning`, `--destructive`,
`--card`, `--border`, radius, `--cc-ease` motion) with hex fallbacks; icons are
Lucide-only via `resolveIcon` (bundled, no network — Font Awesome rejected to
keep one icon system, per DESIGN_SYSTEM.md).

## 3. Template library (Phase 1 catalog — 16 templates)

Display: `weatherCard`, `statDashboard`, `barChart`, `sparkTrend`,
`comparison`, `progressTracker` *(pre-existing)* + `recipeCard` (meta chips,
ingredient checklist, numbered steps, tip callout), `flightStatus` (route with
animated plane on a progress line, gates/terminals, status badge),
`trainStatus` (stops timeline, platforms, delay badge).

Input (HITL-first): **`formCard`** — schema-driven fields
(text/number/select/slider/toggle/date/textarea, required-gating, unit
labels) that submit `label — {json}` back; **`optionPicker`** — rich choice
cards (icon/badge/recommended★, single=tap-to-submit, multi=confirm).

The Projects chat's views (WS-27bm S4, 2026-09-23): **`timeline`** (an
activity feed), **`taskBoard`** (a kanban board, a card opens the task),
**`dataGrid`** (a sortable table, a row opens its record), **`reportCard`**
(tiles plus one table per section), and **`planCard`** (an editable project
plan, HITL). Data-only like the rest. A skill tool emits them from its own
reads (`skill_projects/views.py`), so the model never transcribes rows.

Data shapes live in `genUITemplates.tsx::TEMPLATE_CATALOG` (source of truth,
mirrored into the `emit_generative_ui` docstring). Since S4 the lockstep is a
fence: `tests/unit/test_genui_catalog_lockstep.py` holds the catalog, the
registry and the docstring to one set of names.

## 4. Scenario → element mapping (brainstorm; build on demand)

| Scenario | Today | Future template candidates |
|---|---|---|
| Recipe / how-to | `recipeCard` | `stepByStepGuide` (interactive check-off) |
| Weather | `weatherCard` | `weatherWeek` (panel surface) |
| Flight / train info | `flightStatus` / `trainStatus` | `journeyItinerary` (multi-leg, panel), `seatMap` |
| Enter information | `formCard` + hitl | `wizardForm` (multi-step, panel), `fileDropField` |
| Decisions / approvals | `optionPicker`, `confirmation` | `tradeoffMatrix` (weighted scoring) |
| Metrics / status | `statDashboard`, `sparkTrend`, `barChart` | `gauge`, `donut`, `liveTicker` (needs event re-emit) |
| Schedules | — (Tier 3) | `calendarSlotPicker` (hitl), `ganttTimeline` |
| Shopping / orders | — | `productCards`, `orderStatus` (reuse journey pattern) |
| Documents | report kit (`.html` artifact) | genUI panel + `markdown` node already covers light cases |
| Data tables | Tier-1 `table` | `dataGrid` (sort/filter, row actions → ccAction) |
| Projects chat (WS-27bm, `projects_ai_chat.md`) | `confirmation` before every write · per-app tool cards (`ProjectToolCards`) · `statDashboard` for analytics | `formCard` + `hitl` as the W1 plan panel (S4) |

**A consumer, not a new tier (2026-09-22).** The Projects chat adds no
surface and no template. It reuses `request_confirmation` for every write
and the per-app card slot in `MessageBubble` for its receipts, the way the
Tasks and email assistants do. Its W1 plan is a `planCard` with `hitl`
(§2), so the plan is edited and approved in the same turn it is proposed,
and `edit_task` / `edit_project` are a `formCard` with `hitl` over the row's
values (S4, 2026-09-23).

Rule of thumb for adding: a scenario earns a TEMPLATE when agents hit it
repeatedly in Tier 3 (grep run traces for `type":"html` payloads) — promote the
pattern, delete the bespoke HTML.

## 5. Why this is the efficient method

- **Tokens:** a template emission is ~10-20 lines of data vs hundreds of lines
  of HTML/CSS; the design cost is paid once in the repo, not per run.
- **Consistency:** Tier-2 React + tokens render identically every run, both
  themes, reduced-motion respected — no drift between agents.
- **Latency:** no filesystem round-trip for immersive UI; the panel opens from
  the same SSE event the chip renders from.
- **HITL correctness:** blocking submits resume the SAME turn (no queued
  follow-up message, no prompt re-assembly, no "the user will reply later"
  dance) — and they inherit every HITL hardening already shipped (heartbeats,
  cross-worker acks, watchdog suppression).

## 6. Phases

- **Phase 1 — SHIPPED 2026-07-23:** `surface`/`hitl`/`title` on
  `emit_generative_ui` (+ park/resume + cleanup on failure); `openGenUI` +
  `kind:"genui"` side-panel tabs; transcript chip; `genui_panel_action` bus
  route; 5 new templates; ctx-threaded `renderTemplate`; addendum/tool-doc
  updates; backend tests (`tests/unit/test_genui_hitl.py`).
- **Phase 2:** Tier-1 input primitives (`input`/`select`/`slider` nodes) so
  trees can embed fields; `calendarSlotPicker` + `wizardForm`; panel genUI
  re-open from the Files/Artifacts viewer; template gallery page in the
  workbench for eyeballing the library.
- **Phase 3:** live-updating templates (agent re-emits with same `title` →
  in-place update — the store already replaces same-id tabs); typed
  TEMPLATE_CATALOG (zod) generated into the backend docstring to kill the
  hand-mirrored contract; A2UI/MCP-UI payload interop assessment (chat_ux §12).
- **Cleanup backlog:** migrate genUI primitive hardcoded hexes
  (`ICON_TONE`, badge/callout classes, `TONE_COLOR`) to semantic tokens;
  DESIGN_SYSTEM rule says never raw hex.

## 7. Adoption & cross-runtime parity — SHIPPED 2026-07-23

Phase 1 gave agents the *ability* to render UI; agents still weren't *reaching
for it*. A review found two root causes, both on the prompting side (the emit
path and the frontend render are fully runtime-agnostic — one shared
`queue.put({CUSTOM, generative_ui})` via `resolve_run_queue(session_id)`,
rendered on payload alone in `GenerativeUINode`/`renderTemplate`, so nothing
was wrong with *creation*):

1. **No answer-time nudge.** The "reach for it eagerly" guidance lived only in
   the tool docstring (a weak signal the model consults *after* deciding to look
   at tools) and, at system-prompt level, buried mid-addendum among ~15 other
   tools. Nothing told the model, at answer time, to convert a data/status/
   comparison/choice reply into UI.
2. **Native MAF agents saw NONE of it.** `_build_injected_tools_addendum`
   (which carries the genUI guidance + `design.md`) is applied ONLY on the
   GitHub-Copilot `_tools` branch of `_inject_agent_tools`. Native MAF agents
   (e.g. `email-assistant`, which explicitly scopes in `emit_generative_ui`)
   got the tool + its docstring + at most the delegation registry — no addendum,
   no design language. So the strongest UI agent-type had the weakest guidance.

**Fix — a proactive "Rich UI by default" directive delivered to BOTH runtimes**
(`_ui_first_directive`, `_tool_injection.py`):
- Static, byte-stable (KV-cache safe), gated on the agent actually carrying
  `emit_generative_ui`.
- **Template-first ordering** is the token-thrift lever: it names the 11
  templates as the cheapest + on-brand-by-construction path (data only), the
  component tree next, and custom HTML as the costly last resort (with the
  `--cc-*` token hook for when it's unavoidable). This directly answers "don't
  burn tokens on custom generation" and "use the proper design language."
- **Placement:** leads the Copilot addendum (full + compact) instead of being
  buried; and — the parity fix — is appended to native-MAF agents' instructions
  (both the `default_options` and `agent.tools` shapes), marker-guarded for
  idempotency. So MAF and Copilot agents now get the same answer-time rule.
- Pick/set flows are steered to `formCard`/`optionPicker` + `"hitl":true`.

**Design language — now on demand for ALL agents (shipped 2026-07-23).** The
full ~16 KB `design.md` used to be pasted inline into every *Copilot* agent's
prompt every turn (and never reached MAF at all). It is now a tool,
`load_design_system()` (`acb_skills/design_tools.py`), on the core floor for
both runtimes:
- The inline `_design_md_section()` injection is removed; the addendum carries
  only a cheap POINTER ("### Design language — load on demand"). Net effect:
  ~16 KB off every Copilot prompt, and MAF agents finally have design access.
- Agents call it only for the HEAVY cases — a full-page HTML/Markdown report or
  bespoke custom-HTML `emit_generative_ui` card. Named templates stay on-brand
  by construction (no call), and the `--cc-*` tokens named in the UI directive
  cover simple custom HTML. `design.md` gained YAML front matter (`when_to_use`,
  `summary`) documenting the trigger; the tool strips it and returns the body.
- Coverage: `tests/unit/test_design_tools.py` (real doc, front-matter strip,
  floor + read-only annotation) and a drift-eval guard
  (`test_design_system_is_on_demand_not_inlined`) that fails if the whole doc
  is ever re-inlined.

Coverage locked by `evals/trajectories/test_tool_addendum_drift_trajectory.py`
(directive leads both addenda, template-before-html ordering, gated on the tool)
and `tests/unit/test_genui_proactive_directive.py` (native-MAF instructions
injection, idempotency, compact-vs-full variant, byte-stability).
