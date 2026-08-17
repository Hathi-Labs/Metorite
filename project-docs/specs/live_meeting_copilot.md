# Live Meeting Copilot — architecture plan

> ⚠️ **DEFERRED — FUTURE SCOPE (2026-08-10 consolidation, D26).** No work dispatches from
> this document; no board row exists. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


**Status:** Phases A-D BUILT (presence, console, passive copilot, business context, agenda + standing instructions). Phase E (speaking into the call) still planned. *(status undated when found; last git-touch 2026-07-28 — treat as of that date; not re-verified since. Dated 2026-08-09.)*
**Builds on:** `note_taker_app.md` §3.13 (meeting bot + live-transcript bus),
the browser recorder + live captions, `acb_llm` tiers, the agent/skills/connector
layer, and the notes auth/scoping.

---

## 0. One-paragraph thesis

An **opt-in agent that listens to a live conversation and helps in real time** —
like a moderator sitting beside you. It watches the live transcript (from a
Meet/Teams/Zoom **bot** *or* the in-browser **recorder**), and when a moment
warrants it, surfaces a talking point, a fact from your business systems, or a
question — **privately in Metorite by default**, and (opt-in) **spoken into
the call** via the bot. It pulls context from your existing systems (email,
ClickUp, CRM/Zoho, sales) under *your* permissions, and it can pull *more* context
by asking you questions mid-call, which you answer by typing in Metorite or
by simply saying it out loud. It is token-frugal by construction, isolated so it
can never disrupt recording, and controllable live (on/off, mid-session).

---

## 1. Design principles

1. **Additive & fail-safe.** The copilot is a *consumer* of the transcript, never
   in the capture/record path. If it errors, slows, or is off, recording and
   transcription are unaffected (same posture as today's live captions).
2. **Source-agnostic.** One copilot works for both capture sources because both
   feed the **same live-transcript bus** (`live_transcript.py`). The browser
   recorder starts posting finalized segments to the bus too (small addition).
3. **Token-frugal by construction.** Never "LLM on every sentence." A tiered
   cascade + a rolling compact meeting-state means most audio never reaches a big
   model. (See §4 — this is the crux.)
4. **Private by default, public only on explicit opt-in.** Suggestions land in
   *your* Metorite panel. Speaking into the call is a separate, explicit
   mode/action (consent-sensitive; bot sessions only).
5. **Acts as you, sees what you see.** Business-context retrieval runs under the
   user's identity/scope — the agent can't read anything the user couldn't.
6. **Live-controllable.** Opt-in per session, toggleable mid-session, cheap to
   pause (stops all LLM spend immediately).

---

## 2. System overview

```
              ┌─────────────── capture sources ───────────────┐
  Meet/Teams/Zoom bot worker ─┐                                 │
  (headless Chrome + ASR)     │  finalized live segments        │
                              ├────────────► LIVE-TRANSCRIPT BUS (per meeting)
  Browser recorder ───────────┘              live_transcript.py  │
  (on-page mic + ASR)                         ring + fan-out      │
                                                    │
                        ┌───────────────────────────┼───────────────────────────┐
                        ▼                           ▼                           ▼
                  UI live captions          COPILOT ORCHESTRATOR           (future consumers)
                  (SSE, existing)           subscribe(meeting_id)
                                                    │
                        ┌───────────── tiered cascade (§4) ─────────────┐
                        │  1 gate (cheap)  2 decide (fast)  3 craft (bal) │
                        └───────────────────────────┬───────────────────┘
                             uses ↑ rolling state    │ emits
                             + retrieval tools (§5)   ▼
                                              COPILOT EVENT BUS (per session)
                                              suggestions | questions | status
                                                    │ SSE
                                                    ▼
                                        METORITE — COPILOT CONSOLE
                                   live transcript · suggestions · Q&A · controls
                                     │                         │
                          "speak into call" (opt-in)     answer questions /
                                     │                    steer / give context
                                     ▼                         │ (typed, or spoken → transcript)
                           bot /say → TTS → virtual mic ───────┘  (context loop back into the bus)
```

Two independent buses per session, both fail-safe:
- **Transcript bus** (exists): raw live segments in → captions + copilot out.
- **Copilot event bus** (new): copilot outputs (suggestions/questions/status) →
  the console. Kept separate so copilot chatter never pollutes the transcript,
  and so an agent's output is auditable.

---

## 3. Unifying the two sources

The copilot must not care whether audio came from a bot or the browser.

- **Bot source (built):** the worker already streams ASR segments to
  `POST /notes/meetings/{id}/live/segment`.
- **Browser source (BUILT):** the recorder relays each *finalized* Deepgram
  caption (with its own `start`/`duration` timings and diarized speaker) to
  `POST …/live/browser-segment` — the same bus, user-authenticated rather than
  bot-token'd, sharing one `_resolve_and_publish` path. Best-effort by design: a
  dropped relay never disturbs the recording. Browser captions carry no
  voiceprint, so the registry passes their diarized label through and live name
  binding from self-intros still applies. The batch re-pass on stop remains the
  authoritative transcript in both cases.

Result: `subscribe(meeting_id)` is the single seam the copilot consumes,
regardless of source.

### 3.5 Transcription strategy — one pause-chunked spine, two fidelity levels

Earlier framing called the live transcript a "disposable draft" thrown away once
the batch re-pass runs. That undersells it — and, done naively (fixed-time
windows), it needlessly *compromises accuracy for latency*. The corrected model
is **one pause-chunked pipeline that produces a genuinely good live transcript,
which the batch pass then refines** — not two unrelated pipelines.

**Chunk on pauses, not the clock.** Fixed N-second windows cut mid-word and
mid-utterance, which is exactly where ASR loses context and diarization gets
confused. Instead, the edge (bot worker / browser) uses VAD **endpointing**:
close a chunk when a speaker actually pauses. Each chunk is then a *complete
utterance* — near-batch ASR accuracy for that utterance, and usually a single
speaker's turn (the cleanest input for diarization). Latency tracks the pause
(sub-second to ~2 s), which is fine — the copilot acts on turn boundaries anyway.

**Consistent speakers live, via a running voiceprint gallery.** Per-chunk
diarization alone gives *local* labels that don't line up across chunks. So each
chunk carries a speaker **embedding**; the gateway's live speaker registry
(`live_speakers.py`) matches it (cosine ≥ threshold) against the speakers seen so
far — assigning a **stable global id** or enrolling a new speaker, and updating a
running centroid. This yields consistent identity *incrementally*, with no
end-of-file wait. **Names bind live** from self-introductions (a cheap,
precision-first heuristic — no LLM on the live path); **roles** attach from the
business-context lookup (§5, name → CRM contact). The copilot therefore knows
*who is speaking and their role in real time* — which materially improves its
judgment (coach our rep, not the prospect; tailor a point to who objected).

**The batch pass becomes a refinement, not a redo.** On stop, the authoritative
re-pass (`pipeline.py`: full-file ASR + offline diarization + the LLM speaker-id
pass) still runs — but its job shrinks from "transcribe everything from scratch"
to *correcting* an already-good live transcript: global re-clustering fixes any
online-diarization drift. Overlapping speech and early split/merge errors are the
residual it earns its keep on.

**Reconciliation (built).** The batch re-diarization uses its *own* labels, so
identity learned live would otherwise be thrown away. Instead
`live_speakers.reconcile_labels()` maps batch labels → live speakers by **maximum
time overlap** (greedy *one-to-one*, so a live merge-error can't mislabel two
batch speakers with one name), and `apply_live_names()` merges those names into
`meeting.speaker_names` **before** the LLM speaker-id pass — which then only has
to fill whoever is still anonymous. Non-destructive: a name the user set always
wins. Both clocks share an origin (ffmpeg and the live stream start together when
the bot enters the call), so overlap matching tolerates the small skew. The live
gallery is freed when the pipeline finishes.

Cost note: running live + batch duplicates only the *ASR compute*, which on the
**self-hosted** worker is just CPU (near-free beyond the box). On a **cloud** ASR
(Deepgram) it's paid twice, so there the choice is explicit — stream-only if
live diarization is good enough, or batch-only when real-time isn't needed.

Data flow: `edge VAD-endpoint → per-utterance ASR + embedding → POST …/live/segment
{text, embedding} → registry (stable id + live name/role) → bus → captions +
copilot`. Then, on stop, `full recording → batch re-pass → reconcile/upgrade`.

---

## 4. The moderator policy — WHEN/WHAT to chime in (token efficiency crux)

Running a big model on every utterance is expensive and noisy. Use a **cascade**
where each stage is cheaper and filters for the next. Most segments die at stage 1.

**Stage 0 — Windowing.** Buffer segments into *utterance windows* (close a window
on speaker-turn change, or every ~15 s / ~40 words, whichever first). The copilot
reasons over windows, never raw tokens.

**Stage 1 — Cheap trigger gate (no/low LLM).** For each closed window, decide "is
this even worth a look?" with cheap signals:
- a question was asked (·? + interrogatives), an objection/risk keyword, a
  decision/commitment cue, a number/price/date, a named entity (person/company/
  deal/ticket), a long silence/lull, or an *unanswered* earlier question.
- debounce: enforce a min gap since the last interjection + a per-meeting cap.
- If nothing fires → drop. (Cheapest path; the common case.)

**Stage 2 — Decision (tier-fast, tiny context).** Only for windows that pass
stage 1. Input = **rolling meeting state** (compact, §4.1) + the last window +
any cheaply-retrieved hints. Output = strict JSON:
`{act: "silent"|"suggest"|"ask"|"fact", confidence, topic, need_context?}`.
Low confidence → stay silent.

**Stage 3 — Craft (tier-balanced/powerful, only when acting).** Generate the
actual talking point / question / fact, grounded in retrieved business context
(§5). Dedup against what's already been said this meeting. Emit to the copilot bus.

### 4.1 Rolling meeting state (the token backbone)

Maintain one **compact, continuously-updated summary** per session — topics,
decisions, open questions, action items, entities, and "points already raised by
the copilot." Update it incrementally every K windows with a cheap model
(map-reduce style). The copilot always reasons over *(rolling state + last
window + retrieved context)* — never the full transcript. This is what keeps cost
flat regardless of meeting length.

### 4.2 Cost guardrails
- Per-session **token budget** + per-user rate limit; auto-pause on breach
  (surfaced in the console).
- Backpressure: if a stage is slow, **merge/drop** windows rather than queue
  unboundedly.
- Turning the copilot off unsubscribes it → **zero** further spend instantly.

---

## 5. Business context (email, ClickUp, CRM/Zoho, sales)

Grounding suggestions in real data is what makes this valuable, not a generic LLM.

1. **Pre-meeting context pack.** At session start, assemble a brief for the
   attendees/topic: CRM records + open deals (Zoho), open ClickUp tasks, recent
   email threads, last meeting's notes. Loaded once so mid-call retrieval is rare.
   (Extends `note_taker_app.md` §4 item 9 "pre-meeting brief".)
2. **On-demand retrieval tools.** Stages 2/3 can call scoped tools — `crm_lookup`,
   `tasks_lookup`, `email_search`, `notes_search` — exposed through the existing
   **skills/connector/agent layer**. Results are **cached per session**.
3. **Scoped to the user.** Every retrieval runs under the session owner's identity
   and connector permissions (reuse the notes proxy's `X-User-Email` + role). The
   agent never sees beyond the user's own access.
4. **Token-frugal retrieval.** Trigger retrieval on *entity detection*, not every
   window; summarize retrieved records into the rolling state; never re-fetch a
   cached entity.

Connector availability today (email, ClickUp, WhatsApp) vs. planned (Zoho/CRM,
sales) is a config detail — the tool interface is uniform, so new connectors slot
in without copilot changes.

---

## 6. Bidirectional — the agent asks, the user feeds context

The copilot isn't one-way; it can request what it's missing, and the user can
steer it — mid-call.

- **Agent → user question.** When the decision stage returns `need_context`, the
  copilot emits a **question event** ("Is Acme still on the Enterprise tier?"). It
  appears in the console's Q&A thread and waits (non-blocking; it keeps listening).
- **User answers, two ways:**
  1. **Typed** in the console → posted to the copilot as an answer event.
  2. **Spoken aloud** in the meeting → it arrives on the transcript bus; the
     copilot correlates it to the pending question (recency + intent match) and
     folds it into the rolling state.
- **User → agent steering.** The user can type instructions any time ("focus on
  pricing objections", "don't interrupt for the next 10 min", "remind me to raise
  the SLA"). These adjust the copilot's stage-1/stage-2 behavior live.
- **Deliver an insight publicly (opt-in).** A suggestion in the console has a
  "Speak this into the call" action → `POST /notes/meetings/{id}/say` → bot TTS.
  Only for bot sessions; explicit per line (or an opt-in "auto-speak" mode gated
  hard behind consent).

---

## 7. Metorite presence + console (the control surface)

The user must *see* that a live session is running and *interact* with the agent —
even if they're not personally in the meeting (bot case).

- **Live-session registry.** When a session goes live (bot joins OR browser
  recording starts), register it: `{meeting_id, source, owner, copilot_enabled,
  mode, status, started_at}`. This powers presence + reconnection.
- **Global "live now" presence.** A dock/indicator (same pattern as the recording
  dock + focus-timer dock) shows "● Live — Acme call" across the whole app.
  Clicking opens the console.
- **Copilot console** (the interaction hub):
  - live transcript (from the bus SSE),
  - the copilot's **suggestions stream** + **Q&A thread**,
  - controls: **copilot on/off**, **mode** (private / can-speak), **budget/status**,
    "speak this", answer box, steering box.
- **Reconnection:** the console reattaches to a running session by id (state is in
  the registry + buses), so refreshes/navigation don't drop it.

---

## 8. Opt-in, live toggle, lifecycle

- **Default OFF.** The copilot never runs unless explicitly enabled for a session.
- **Toggle mid-session, both ways.** On → spawn the orchestrator (subscribe +
  cascade). Off → unsubscribe + stop (instant cost stop). State persisted on the
  session so it survives reconnects.
- **Modes:** `listening` (private suggestions only) → `interactive` (asks
  questions) → `speaking` (can talk into the call). Escalating consent/stakes; the
  user picks the ceiling.
- **Auto-stop** when the meeting ends (bus closes) or the budget is exhausted.

---

## 9. Permissions & scoping

Reuse the notes model; extend it for live control.
- **Session ownership:** a live session is owned by `owner_email`; only the owner
  (and, per policy, executives) can view/control its console.
- **Worker ↔ gateway** callbacks authed by the shared `MEETING_BOT_TOKEN`.
- **Console ↔ gateway** authed by the existing session auth (internal bearer +
  `X-User-Email` + role) — same as the rest of `/notes`.
- **Retrieval runs as the user** (§5.3) — the single most important guardrail.
- **Consent:** speaking mode inherits the meeting-bot consent posture
  (`note_taker_app.md` §3.13) — named bot, opt-in, disclosure; a bot that *talks*
  raises the bar further, so it's off unless deliberately enabled.

---

## 10. Data model (additive)

- `live_session` — `id, meeting_id, source(bot|browser), owner_email,
  copilot_enabled bool, mode(listening|interactive|speaking), status, token_spend,
  started_at, ended_at`.
- `copilot_event` — `id, session_id, kind(suggestion|question|answer|status|fact),
  text, refs jsonb (source segments/records), created_at, acted_on` — powers the
  console history + an audit trail of what the agent said/asked.
- `copilot_config` (per user/org) — defaults: enabled, mode ceiling, connectors
  allowed, sensitivity/rate caps.

Rolling state + in-flight windows stay in memory (rebuilt from the bus on
restart); only events worth keeping are persisted.

---

## 11. Where the copilot runs

Options (decision needed — §14):
- **(a) In the gateway** as an async task per enabled session. Simplest; reuses DB,
  auth, connectors in-process. Risk: LLM/agent load on the gateway.
- **(b) A separate `copilot` worker service** subscribing to the bus over the
  network. Better isolation + independent scaling; more moving parts.

Recommendation: **start in-gateway (a)** behind the tiered cascade (load is modest
because most windows die cheaply), with the orchestrator written as a self-
contained module so it can be lifted into a worker (b) later without API changes —
exactly how the STT/bot provider layers were structured.

---

## 12. Reuse map (what already exists)

| Need | Reuse |
|---|---|
| Live transcript in/out | `live_transcript.py` bus + `subscribe()` (built) |
| Live speaker identity + roles | `live_speakers.py` voiceprint gallery + name binding + roster (built) |
| Speak into call | bot `POST /bots/{id}/say` + virtual mic (built) |
| LLM calls + tiers/fallback | `acb_llm` `acompletion_with_fallback` |
| Business context | skills / connectors / agent framework (email, ClickUp, …) |
| Console + presence streaming | SSE pattern (`events.py`, live SSE) |
| "Live now" dock | recording-dock / focus-timer dock pattern |
| Auth/scoping | notes proxy (internal bearer + `X-User-Email` + role) |
| Pre-meeting brief | `note_taker_app.md` §4 item 9 |

Genuinely new: the **copilot orchestrator + cascade**, the **copilot event bus**,
the **live-session registry/presence**, the **console UI**, and the **browser→bus
feed**.

---

## 13. Phasing (each phase shippable + independently valuable)

- **Phase A — Presence + console (read-only), no LLM. BUILT.** `live_session`
  registry (migration 120) with a partial unique index making "begin" an
  idempotent upsert; `GET /notes/live/sessions` presence + `…/live/session`
  reattach; opt-in toggle `POST …/live/copilot` (stored, **off by default** —
  Phase B acts on it). UI: `LiveDock` (shell-level "● Live now", covering what
  the local `RecordingDock` can't see — bot calls and other devices) and the
  console at `/notes/live/[id]` — live transcript over SSE, attributed by the
  speaker roster, for bot **and** browser sources. Sessions begin at bot-join /
  recording-start and end in the pipeline (success *and* failure paths, plus bot
  `failed`/`not_admitted`) so presence can't strand as "live".
  *Exit reached:* watch a live call's transcript, attributed by speaker, in
  Metorite.
- **Phase B — Passive copilot (private suggestions). BUILT.** `copilot_policy.py`
  holds the pure Stage 0/1 (windowing on speaker-turn/length; a no-LLM trigger
  gate for questions, objections, decisions and numbers, with debounce + a
  per-meeting cap checked BEFORE the triggers so a busy meeting can't run up
  cost). `copilot.py` runs Stage 2 (tier-fast decide, strict JSON, confidence
  floor) → Stage 3 (tier-balanced craft) only for what survives, dedups against
  a bounded rolling state, and emits on a **separate** event bus (SSE +
  `copilot_event`, migration 121) so agent chatter never pollutes the
  transcript. Budget exhaustion pauses and says so. The `…/live/copilot` toggle
  starts/cancels the orchestrator — off cancels the task, which unsubscribes it
  from the transcript bus, so spend stops immediately. *Exit reached:*
  cost-bounded talking points appear live in the console.
- **Phase C — Business context. BUILT.** `copilot_context.py` assembles a
  **pre-meeting pack** from three independent layers: (1) `meeting.copilot_brief`
  — a user-written briefing, the highest-value source because it's usually the
  only thing that knows what the meeting is FOR; (2) local history — past
  meetings sharing an attendee email, their summaries and still-open action
  items, needing no connector at all; (3) the business agents, via
  `call_agent("agent-sales-assistant"|"task-manager", …)` fanned out in parallel.
  Assembled **once at session start** and cached (an agent call takes seconds; a
  meeting moves faster), then carried as compact `BACKGROUND` in both the decide
  and craft prompts. Every layer degrades independently — a missing brief,
  no history, an unregistered agent or a timeout each just omit their section.
  Layer 3 is **opt-in per session** (`live_session.deep_context`) since the
  fan-out spends tokens before a word is spoken. Capped on every axis because
  the pack rides in *every* prompt. Migration 122. *Exit reached:* a suggestion
  can cite the brief, a past decision, or a CRM fact.
- **Phase D — Bidirectional (agenda + standing instructions). BUILT.**
  `copilot_agenda.py`: (a) **standing instructions** (`copilot_config`) — a
  per-user system prompt prepended to EVERY run, because the brief changes per
  meeting but how you want the copilot to behave doesn't; (b) **conversational
  agenda building** — describe the meeting in plain language and the copilot
  drafts a structured agenda, refined by talking to it ("drop the demo, add
  pricing"); (c) **live coverage** — because the agenda is a list rather than
  prose it can be *measured*, so the copilot sees which items are still
  outstanding and can nudge. Coverage is a pure token-overlap function, not a
  model call, since it runs on every window. A model failure or an empty/malformed
  response never destroys an agenda the user built. Migration 125.
  *Exit reached:* you plan the meeting by talking to the agent, and it gives
  agenda-aware feedback during the call. Spoken answers back to the agent remain
  future work.
- **Phase E — Speak into the call (opt-in).** Public interjection via bot `/say`,
  explicit consent/gating. *Exit:* with permission, the moderator talks.

---

## 14. Open decisions (need an owner call)

1. **Speaking posture:** per-line approval only, or an opt-in autonomous
   "moderator may speak" mode? (Consent + trust.)
2. **Connector priority** for context: Zoho CRM, ClickUp, email — which first?
   (Is Zoho already connected, or to be added?)
3. **Default mode ceiling:** listening-only, interactive, or allow speaking?
4. **Runtime:** in-gateway (a) vs a separate copilot worker (b) — §11.
5. **Multi-viewer:** can others (e.g., a manager coaching a rep) watch/act on the
   same console, or owner-only?
6. **Cost ceiling** per meeting (token budget) + what happens on breach
   (pause vs. downgrade to cheaper cadence).
7. **Retention:** keep `copilot_event` history (audit/coaching) or ephemeral?
