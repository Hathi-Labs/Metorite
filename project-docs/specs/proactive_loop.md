# The proactive loop: a member model, and nudges that learn from the member

**Status: PROPOSED, 2026-11-16. This spec builds nothing.** Verified against
the code on 2026-11-16, at `origin/main` `320b04375`. Metorite task 3439, in the
project "Personalization & Memory System".

The owner decides LOOP-Q1 to LOOP-Q10 (§12). The defaults hold until the owner
answers. This spec takes no D-number.

**What it owns.** It owns the loop across the stages, and the learning. It owns
the fields of a finding, which fill the `nudges` and `insights` points of
`building_blocks.md` §3.1. Each stage keeps its owner spec. This spec points to
each owner and copies none of its rules.

| Topic | Owner spec | What this spec adds |
|---|---|---|
| The event bus | `ai_studio.md` row E12 (`gateway/core_events.py`), and `my_day_feed.md` §8.2 | New emit sites for email, CRM and calendar, in a later rung |
| Signals, cards, the order, the card state | `my_day_feed.md` (D100, D101) | The learned `affinity` and the mutes of MD-9 (§7) |
| The interrupt, the budget, quiet hours | `proactive_reach.md` (D103) | A class that the learner lowers for one member (§7.3) |
| The daily brief | `core_brief.md` | The digest outcome, and the turn-on of rung 1 |
| The member profile and "How I work" | `personalization.md` §4, §12 and §19 (D110, D212) | Five layers, a source on each item, more acts in the act record (§5) |
| The project checker | `project_alignment.md` (D160, D192) | The checker as the first detector of a contradiction (rung 4) |
| Waits and decisions | `waiting_on_me.md`, `chat_run_continuity.md` §8 (D171) | Nothing. A finding that needs a decision uses their cards (§4.5) |
| Watches and headless turns | `proactive_agent.md` (D161) | Nothing. A detector on a schedule runs on their engine |
| The manifest envelope | `package_host.md` (HX-0, task 2762) | The fields of a finding and of a kind (§6) |
| Connected MCP servers | `chat_connected_systems.md` (WS-CSY) | The poll of a connected system (§4.1, rung 6) |

**Not the WS-LL loop.** `orchestration_playbook.md` §6c owns the learning loop
of the agent harness, behind `LEARNING_ORGS`. This spec is about what Metorite
tells a member. The two share no table, flag or name.

---

## 0. The answer in one screen

- **The static half of the personality engine is built and live.** The
  profile, "remember", salutations and the briefing block run for every
  organization. The tone presets of `personalization.md` §17 are not built.
- **The learning half does not exist.** No live code reads what the member
  does to change what Metorite shows. The feed writes each card choice, and
  only a dark shadow pass reads it.
- **One loop, eight stages, no new engine.** Intake, detect, the finding, the
  judge, timing, delivery, feedback and learning. Each stage extends a seam
  that exists (§4).
- **One finding contract.** A finding is the My Day signal of
  `my_day_feed.md` §4.1 with five more fields (§6). The fields are evidence, a
  confidence, who may see it, a severity hint and a batch key. An app or a
  connector declares its kinds in the `nudges` and `insights` points of its
  manifest.
- **The member model lives in `personalization.md` §19.** It gets five layers,
  a source on each item, and an act record that also counts card choices
  (§5). An inferred item stays a proposal until the member says yes (D-PZ-2).
- **Learning lowers, and never raises.** A kind that a member keeps marking
  "Not useful" sinks in the feed and waits for the brief. A needs kind never
  leaves the feed. The judge alone never wakes a member (§7).
- **No score for a person (ALN-Q5).** "Off track" is a fact about a
  commitment, never a fact about a person (§8, LOOP-Q1).
- **Seven rungs (§9).** Rung 1 turns on the built brief for HathiLabs. Rung 2
  makes My Day learn from "Not useful". Rung 3 lets the member see and accept
  what Mira learned.

---

## 1. The owner's ask

Task 3439 records the ask of 2026-11-16, made in chat:

```text
(1) The chat depends on the personality engine: make sure it is properly
built. (2) Over time the assistant should have a good view of the member:
research how other personal assistants log interaction and learn preferences,
and improve our architecture from it. (3) Plan the architecture for
proactivity: a persistently learning system that nudges the member when it
matters: important emails, decisions to take, forecasts, something wrong that
the company data says, a team member going off track, an email that
contradicts the project plan. (4) As more apps, integrations and connectors
arrive, the ingest, the reasoning and the nudges must keep improving by
themselves.
```

| Part | Where this spec answers it |
|---|---|
| 1. Is the personality engine built? | §2.1 |
| 2. A good view of the member | §5, with the research of §13 |
| 3. Nudges when it matters | §4, §6 and §7. The "off track" part is LOOP-Q1 |
| 4. It improves as apps arrive | §6.2 (a kind is a manifest entry) and §7.4 (a new kind learns from day one) |

---

## 2. What exists

### 2.1 Is the personality engine built? (part 1 of the ask)

**The static half is built and live. The tone presets are not built. The
learning half does not exist.** The production column reads the production
`.env` of 2026-11-15 UTC, by flag name only.

| Part | Owner | On `main` | Production, 2026-11-15 UTC |
|---|---|---|---|
| The member profile | `personalization.md` §4 (PZ-1, PZ-2) | `acb_common/assistant_profile.py`, migration 294, `/auth/me/assistant-profile` | Live. `ASSISTANT_PROFILE_ENABLED` is on, and an empty `ASSISTANT_PROFILE_ORGS` means every org |
| "Remember this" | `personalization.md` §5.5 (PZ-6) | `profile_remember` in `acb_skills/profile_tools.py`. `save_memory` in `memory_tools.py` writes to Mem0 only | `profile_remember` works. `MEM0_ENABLED` is off, so `save_memory` answers that memory is off (§2.1 there) |
| Salutations and celebrations | `personalization.md` §16 (D184) | `acb_common.today.GREETING_TABLE`, `acb_common/celebrations.py` | Live. `CELEBRATIONS_ORGS` is `*` |
| Tone of voice | `personalization.md` §17 (D189) | Only the formality line "Tone: ..." of `_address_line` | Not built. PZ-45 to PZ-49 are open |
| How I work | `personalization.md` §19 (D212) | Nothing. MM-1 to MM-7 are in Backlog | Not built |
| Recent chats: the briefing block and the summaries | `personalization.md` §15 | Built (PZ-33, PZ-34) | The briefing block is live, because `ASSISTANT_BRIEFING_ORGS` is `*`. The summaries are off, because `ASSISTANT_CHAT_SUMMARY_ORGS` is empty |

**Three stores of a writing style never meet.** They are `email_voice_profiles`
(migration 94), `email_learned_patterns` (migration 28) with
`email_assistant_settings.learned_writing_style` (migration 39), and
`assistant_profile.style` (migration 294). MM-7
and PZ-12 join them (§5.6).

**After Mem0 retires (D132).** KG-11 (task 795) removes the Mem0 client. No
spec says where `save_memory` goes then. LOOP-Q9 asks.

**Nothing learns.** No code infers a habit or a preference. No feedback changes
what the member sees. §2.2 shows where the feedback goes today.

### 2.2 The proactive parts

| Part | Owner | On `main` | Production, 2026-11-15 UTC |
|---|---|---|---|
| My Day feed | `my_day_feed.md` | `routes/shell/feed.py`, `needs.py`, `state.py`, `my_day_loop.py` | Live. `NEXT_PUBLIC_MY_DAY_FEED`, `MY_DAY_LOOP_ENABLED` and `MY_DAY_CACHE` are on. `MY_DAY_PROVIDERS` is `reminders,agent_questions` |
| The card state | `my_day_feed.md` §13.1 | `my_day_card_state` (migration 258) | Written by the live feed. Only `feed_decide._habits` reads it, inside the dark shadow pass |
| `affinity` in the order | `my_day_feed.md` §7 | `feed_rank.affinity()` returns 0 "until MD-9" | No effect |
| Kind mutes | `my_day_feed.md` §6.6 | The key `mutes` of `my_day_prefs` (`routes/shell/prefs.py`) | Stored. No reader until MD-9 |
| `decide` on My Day | `my_day_feed.md` §10 | `feed_decide.py`, `feed_measure.py`, `decide_features.py`, `my_day_judgments` (migration 262) | `DECIDE_ENABLED` is on. `MY_DAY_DECIDE_ORGS` is empty, so My Day asks nothing |
| A waiting chat card on My Day | `chat_run_continuity.md` §8 | The `agent_asks` kind of the `chat` source in `feed.py` | `CHAT_DURABLE_ASKS` is on. `MY_DAY_PROVIDERS` does not list `chat`, so the card does not show |
| Reach | `proactive_reach.md` | `gateway/reach/*`, migrations 266, 271, 318 and 330 | `REACH_ENABLED` is on for 2 orgs and 3 members |
| The rating of an alert | `core_brief.md` §6 | Three columns on `reach_signal_log` (migration 350), and `outbox.rate_alert` | No route calls `rate_alert` yet (RE-6). So no rating exists |
| The brief | `core_brief.md` | `gateway/brief/*`, `brief_runs` (migration 315), `BriefCard.tsx` | Off. Each `BRIEF_*` flag is off |
| The event bus | `ai_studio.md` E12 | `ingestion/event_hooks.emit_event`, `gateway/core_events.py` | `CORE_EVENTS_ORGS` is empty, so the core apps emit nothing |
| The project checker | `project_alignment.md` §6 | `gateway/align/trigger.py`. The `align.check` handler in `align/check.py` is a stub until ALN-7 | Off. `ALIGN_JOBS_ORGS` is empty |
| Number sense | `number_sense.md`, `crm_platform.md` §18.11 | `acb_common/number_sense.py` holds one check, concentration. The CRM aggregate calls it | No flag. It runs wherever the CRM aggregate runs |
| Forecasts | `projects_reports.md` §6.8 (RA-6) | `routes/projects/analytics_forecast.py`, a forecast by count for Projects only | No flag |
| Waits | `waiting_on_me.md` | `routes/shell/waiting.py` | `WAITING_ORGS` is `*` |
| Park and ask | `proactive_agent.md` §4.2 | AUTO-7b | `AUTO_PARK_ORGS` is `*` |
| The graph sweep | `knowledge_graph.md` | `kb_entities/graph.py`, `kb_links` (migration 355) | `GRAPH_SWEEP_ORGS` is `*` |
| Signals in a manifest | `building_blocks.md` §3.1 | None. A provider is code in `feed.py`, with a row in `REACH_CLASS` and a name in `MY_DAY_PROVIDERS` | Planned: the `nudges` and `insights` points (tasks 2762, 2763) |

**Absent:** an act record beyond the card state, inferred preferences,
anomalies beyond the CRM concentration check, a forecast outside Projects, and
any "off track" signal.

### 2.3 Facts in other docs that `main` contradicts

These are findings for the board (LOOP-1). LOOP-0 fixed only the first one,
because it sits in the section that LOOP-0 extends.

1. `personalization.md` §19.3 named migration 38 for `learned_writing_style`.
   The column comes from `39_email_learned_writing_style.sql`. Fixed here.
2. Task 602, "Turn on the My Day feed", waits in Backlog. The flag
   `NEXT_PUBLIC_MY_DAY_FEED` is on, and `my_day_feed.md` records the feed as
   live since 2026-10-14.
3. `chat_run_continuity.md` §8 says that `MY_DAY_PROVIDERS` is empty in
   production. It lists `reminders,agent_questions`. The `agent_asks` card
   still does not show, because `chat` is not on the list.
4. `number_sense.md` says that nothing is built, and NUM-1 plans to create
   `number_sense.py`. The module exists, with the concentration check
   (task 3129). NUM-1 must extend it and not write a second one.

LOOP-1 (task 3441) fixed items 2 to 4 on 2026-11-16: task 602 holds the
evidence, `chat_run_continuity.md` §8 and `number_sense.md` state the fact of
`main`.

---

## 3. Scope and non-goals

### 3.1 In scope

1. The eight stages of the loop, and the owner of each stage (§4).
2. The member model as an extension of `personalization.md` §19 (§5).
3. The fields of a finding and of a kind (§6).
4. The learning: affinity, a threshold for each kind, the cold start, the
   measures and the harm cases (§7).
5. The rules of privacy and visibility for every stage (§8).
6. The rungs and their demos (§9), and the slices (§10).

### 3.2 Non-goals

1. **No new engine, registry, queue or scheduler** (D205, `building_blocks.md`
   §6.1). A detector is a My Day provider, a job on the job queue, or a watch
   of `proactive_agent.md`.
2. **No score for a person** (ALN-Q5, D192). No table, view or report ranks or
   counts people.
3. **No model writes the words of a nudge.** Templates write them
   (`proactive_reach.md` §4, `personalization.md` §17.2). The one bounded
   model site stays `brief/phrase.py`, behind its claim fence.
4. **No automatic act.** A nudge offers an act. The member decides on a card
   (D-PZ-48, D161 item 5).
5. **No second store of member preference** (`personalization.md` §12.6).
6. **No new card kind for a decision.** A decision uses the waiting cards that
   exist (§4.5).
7. **No shared table of findings.** A stored finding lives in the table of its
   app. `pm_alignment_findings` will be the first such table, when ALN-7
   builds it (`project_alignment.md` §4.3). No migration holds it yet.
8. **No push approves anything** (D-RES-5).

---

## 4. The loop: eight stages

```text
 intake            detect              finding            judge             timing               delivery          feedback            learning
 ───────────       ──────────────      ───────────        ──────────        ──────────────       ───────────       ──────────────      ───────────────
 event bus    ─▶   My Day provider ─▶  signal + 5    ─▶   §7 score     ─▶   silent, digest  ─▶   My Day card  ─▶   card state,    ─▶   affinity and a
 + reads           job or watch        fields (§6)        + my_day.nudge    or interrupt         brief, Reach      act record          class per kind
 (§4.1)            (§4.2)              (§4.3)             (§4.4)            (§4.5)               (§4.6)            (§4.7)              (§4.8, §7)
                                                                                                                        │
                                                                                                                        └──── back to the judge and to timing
```

| # | Stage | Owner spec | The new work |
|---|---|---|---|
| 1 | Intake | `ai_studio.md` E12, `my_day_feed.md` §8.2 | Emit sites for email, CRM and calendar. The poll of a connected system (rung 6) |
| 2 | Detectors | Each app's spec, `project_alignment.md` §6, `number_sense.md`, `proactive_agent.md` | ALN-7 makes the checker real. Baselines over time for numbers. A forecast that slips |
| 3 | The finding | This spec §6, with `package_host.md` (HX-0) | Five fields on the signal, and a declaration for each kind |
| 4 | The judge | `my_day_feed.md` §7 and §10, `proactive_reach.md` §5 | `affinity` from the member's acts (MD-9) |
| 5 | Timing and channel | `proactive_reach.md` §6, `core_brief.md` | A class that the learner lowers for one member |
| 6 | Delivery | `my_day_feed.md` §4.2, `core_brief.md` §5, `proactive_reach.md` §9 | Evidence links on the card, and "Why am I seeing this" (MD-9) |
| 7 | Feedback | `my_day_feed.md` §13.1, `personalization.md` §19.4, `core_brief.md` §6 | More acts in `member_action` (MM-1) |
| 8 | Learning | This spec §7 | Affinity, a threshold for each kind, the cold start, the shadow gate |

### 4.1 Intake

The bus is `ingestion/event_hooks.emit_event`. `gateway/core_events.py` holds
the emit sites of the core apps, behind `CORE_EVENTS_ORGS`. Today only People
emits, with `people.person.updated`, and the flag is empty in production.

- **Most detectors need no event.** A My Day provider computes its signals on
  each read, with a bounded query (`my_day_feed.md` §4.1). An event only marks
  the feed dirty (§8.2 there), or starts a detector that a trigger runs.
- **New emit sites.** Email, CRM and calendar emit a fact when a record
  changes. The payload holds ids, the actor and the changed keys, and never a
  value. That is the rule of `core_events.py`.
- **Private sources.** Mail and calendar are private to a member. Their events
  need the `_author_may_hear` branch (E3-S) before a dispatcher hears them.
- **Connected systems.** A connected MCP server feeds the loop by a poll only
  (§4.1.1).

#### 4.1.1 The poll of a connected system (for metorite-f8)

**[f8-1] The proactive read of a connected system.** A connected MCP server
feeds the loop only by a poll. The client loads tools with
`load_prompts=False`, and no code in `orchestrator/_maf_mcp.py` subscribes to
a resource. So each poll is one tool call. The poll is a job that attaches the
server through `attach_mcp_tools` and the `McpPermissionGate`, with no new
client. It runs in its own session, and never in the chat of a member.

The rules of the poll. Rules 2 to 4 are rules of this spec. They come from no
other decision, and LOOP-Q10 asks the owner to confirm them.

1. **The count.** Each call of a poll logs the `mcp.call` line of CSY-2 with
   `source=job`. A call of the chat logs `source=chat`. CSY-2 adds the field
   `source`. The counter of CSY-8 counts the two apart, so a poll never shows
   as chat use on the panel.
2. **The read-only key.** A poll calls a tool only when the admin ticked the
   read-only key of its server (MCP-C2). The read-only hint of a tool is not
   enough. The hint is the server's own claim, and a hostile server can lie
   (`_maf_mcp.py` header, item 6). A server set to "always confirm" refuses
   each call of a poll.
3. **A poll result is untrusted.** The loop frames each result of a poll as
   data from a third party before a model reads it. The run that reads it
   holds no write tool and no other server. The header "The server says" of
   `chat_connected_systems.md` §3.1 frames only the server's instructions
   (CSY-6). It does not frame a tool result.
4. **Who may see a signal.** A signal from a connected server reaches only the
   admin who connected the server. This holds for a server with OAuth and for
   a server with a static key. A wider scope is a D12 subject that the admin
   states on the connection. D98 says only whose identity an OAuth call uses,
   and it sets no scope.
5. **No mail.** The poll run holds no mail pack (OA-7b).
6. **What the poll waits for.** Each server that it polls needs its read-only
   key ticked. For Shaurrya App, that is CSY-R0 (task 3399). The poll also
   waits for CSY-2 (task 3401, the line with `source`) and CSY-8 (task 3408,
   the count). It waits for CSY-1 (task 3400) only if it picks a server with
   `server_brief`. It waits for CSY-6 (task 3406) only if it reads the
   server's instructions.

LOOP-13 builds the poll in rung 6.

### 4.2 Detectors

A detector finds a fact and writes a finding (§6). It never writes the words of
a message. A detector is one of three types, from the cheapest:

1. **Rules in code.** A My Day provider, a number check, a forecast. Examples:
   `overdue`, `deal_quiet`, the concentration check of `number_sense.py`, and
   the Monte Carlo forecast of `analytics_forecast.py`.
2. **The project checker.** ALN-7 replaces the stub `align.check` with the
   checker of `project_alignment.md` §6. It asks one `decide` question for each
   changed task, and the server proves each quote. It runs in shadow first, and
   ALN-8 sets the gate.
3. **A model check, for a fuzzy case only.** A contradiction is the example. A
   model check runs only after a rule in code or a structured extraction picked
   the candidate. It goes through `decide_features.ask` on the decide tier, with
   a cap and a cool-down.

**"Off track" is a finding about a commitment.** A commitment is overdue, or a
planned date slips past the plan. The entity is the task, the promise or the
project, never a person (ALN-Q5). The person whose commitment it is hears
first (ALN-Q1). LOOP-Q1 asks if the owner wants more.

**Where a detector runs.** A provider runs on each feed read. A job runs on
the job queue (`jobs/worker.py`), as `align.check` does. A detector on a
schedule is a workflows trigger or a watch template of `proactive_agent.md`
(D161 items 1 and 4). It runs on `jobs/unattended.py`. No detector gets its
own scheduler.

**The org gate of a workflow run.** H-328 parts (a), (b) and (c) are built. A
workflow run starts only for an org on `WORKFLOW_UNATTENDED_ORGS`
(`routes/workflows/unattended.py`). That list ships empty, and only the owner
adds an org.

### 4.3 The finding

§6 holds the contract. A finding is a My Day signal with more fields, so the
registry, the fold and the order of `my_day_feed.md` serve it unchanged.

### 4.4 The judge

The rule of the judge is an expected value. Show a finding when its value to
this member, now, is more than the cost of the interruption. Metorite has this
rule already, in two places. This spec adds a term, and no new formula.

- **The order in the feed** is `base(kind) + urgency + weight + affinity −
  fatigue` (`my_day_feed.md` §7). Value is `base`, `urgency` and `weight`. The
  chance that the member finds it useful is `affinity`. The cost is `fatigue`.
- **The interrupt** is the gate of `proactive_reach.md` §5.1 and the question
  `my_day.nudge` (§5.2 there). The loop takes `now` only above a confidence of
  0.7.
- **The new term.** MD-9 replaces the stub `affinity()` with the learned value
  of §7.2. The `decide` state already reads counts for each kind
  (`feed_decide._habits`). The member model adds the current focus (§5.1).
- **Model calls.** Each model call of the judge goes through `decide` on the
  decide tier (D100), in the member pass that already runs.

### 4.5 Timing and channel

Reach owns the budget, the caps and quiet hours (D103, `proactive_reach.md`
§6). A finding has three outcomes:

| Outcome | Where it goes | Who decides |
|---|---|---|
| Silent | The feed only. Reach class `none`, or a card that no rule lets through | Code (the reach class), then the learner (§7.3) |
| Digest | The brief, the close-out, or the group "For your information" | `core_brief.md` §4.3, and `my_day.triage` |
| Interrupt now | A Reach message, inside 3 a day and quiet hours | `proactive_reach.md` §5 and §6 |

**Two budgets, one for each place.** A message outside the app counts against
the budget of `proactive_reach.md` §6.1. An offer inside the chat counts
against D-PZ-13: one unprompted question a day across all apps, and 14 days
after a dismiss. The setup coach, the habit question of MM-5 and the save offer
F6a of WS-TOF (task 3160) all count there.

**[a3-2] A nudge that asks for a decision.** A finding that needs the member's
decision adds no card kind, and uses the card of the wait that holds it. A run
that waits shows as the `agent_asks` card of the `chat` source in `feed.py`,
behind `CHAT_DURABLE_ASKS` and `MY_DAY_PROVIDERS` (WAIT-1, task 1550). Approve
and Not now on the `agent_asks` card are RESUME-7 (task 3268), under D-RES-5.
A project decision shows as the `decision` card of the `decisions` source in
`needs.py` (ALN-2b). It answers through `routes/projects/decisions.py`.

The `decisions` source has two gates: `DECISIONS_ORGS`, and `MY_DAY_PROVIDERS`
must list `decisions`. A push never approves the `decision` card. That is a
rule of this spec. Each wait also shows in "Needs you" through the waiting seam
of `waiting_on_me.md`.

### 4.6 Delivery

The card anatomy of `my_day_feed.md` §4.2 holds every part that a nudge needs.
It has the headline, the why line, the acts and the menu. The menu holds
Snooze, Dismiss, "Not useful" and "Why am I seeing this". This spec adds two
things:

- **Evidence.** The card shows 1 to 3 evidence links from the finding (§6.1),
  under the why line. Code builds each link (§12 there).
- **"Why am I seeing this"** names the kind, the judged group and the learned
  reason, in words. An example: "You acted on 4 of the last 5 like this." MD-9
  builds it.

The words "less like this" and "never for this" are not new controls. "Not
useful" is "less like this". The kind mute of §6.6 there is "never for this".

The brief (`core_brief.md` §5) and Reach (`proactive_reach.md` §9) keep their
own shapes. Each reads the same signal, so each surface says the same thing.

### 4.7 Feedback

Three stores already capture feedback. MM-1 plans the fourth.

| Store | What it holds | Reader today |
|---|---|---|
| `my_day_card_state` (migration 258) | Seen, later, acted, snoozed, dismissed, "Not useful", visits | `feed_decide._habits`, in the dark shadow pass |
| `reach_signal_log` (migrations 266 and 350) | The rule of an alert, and its rating | None. No route writes a rating until RE-6 |
| `chat_message.custom_events` (migration 2) | The outcome of a chat card | None |
| `member_action` (MM-1, planned) | The member's own acts, with no text | Planned: `member_habits` (MM-2) |

**One record, not a second.** §5.3 widens MM-1, so the card choices and the
chat card outcomes also reach `member_action`. `my_day_card_state` stays the
state of a card. `member_action` is the history of acts that the learner reads.

**The reward is an act, not a click.** The learner counts an act on the entity
within 24 hours: the card state `acted`, or a `member_action` row on the same
item. An open or a seen counts for nothing.

### 4.8 Learning

§7 holds the rules.

---

## 5. The member model (an extension of `personalization.md` §19)

`personalization.md` §19 stays the home of the member model, and D212 stays
its decision. This section proposes five additions. Each one extends a slice
of §19 and adds no store. When the owner accepts them, the slice of §19 builds
them.

### 5.1 Five layers

| Layer | Example | Write path | Life | State |
|---|---|---|---|---|
| Stable profile | "Call me VJ", the zone, the language | The member's words, or a yes on a card (PZ-6) | Until the member changes it | Built (§4 there) |
| Stated preferences | "Keep it short", a kind mute, a correction line | The member's words, the card menu, `my_day_prefs` | Until the member changes it | Built in part (§4 there, `my_day_feed.md` §10.5) |
| Working patterns | "You forward PO mail from GE to Veena and Kiran" | Counted on read from `member_action` (MM-2). Kept only on a yes (MM-5) | A count: 90 days. A kept habit: until Forget | Planned (MM-2, MM-5) |
| Relationships | "Most of your mail goes to Priya and Kiran" | Counted on read from the `targets` of `member_action`. Shown as a proposal | 90 days | New (LOOP-6) |
| Current focus | "Today: the Acme launch" | Read on each use: the One Thing, the active goals and the projects that the member leads (`my_day_feed.md` §10.4). Never stored | 1 to 7 days | The `decide` state reads it today. LOOP-6 shows it on the page |

### 5.2 Each item

- **A source on each item:** `stated`, `inferred` or `imported`. The page shows
  it. A fact in the profile already shows its source and date (§7 item 2
  there).
- **Evidence, a confidence and a date.** An inferred item names its counts,
  for example "6 of 7 times in 90 days".
- **An inferred item is a proposal.** It writes nothing until the member says
  yes (D-PZ-2, §12.4 there, PZ-15). A count on read is not a write.
- **Sensitive topics.** The learner infers nothing about health, religion,
  caste, politics, sexual life, family or money of a person. A member may
  state such an item. The rule of `celebrations.py` holds: ask, never guess.
- **The newest statement wins.** A new statement replaces the old one. The
  profile keeps the old value for Undo (PZ-15).
- **"Never infer this."** The member can forbid one pattern or one layer. The
  item goes into the profile key `never_infer`, and the learner reads it
  through `acb_common.assistant_profile`.

### 5.3 More acts in the act record (MM-1)

MM-1 (task 2878) builds `member_action` with the columns of §19.4. This spec
adds acts to its list, and no column:

| `app` | New acts | The seam that calls the one writer |
|---|---|---|
| `my_day` | `card_acted`, `card_later`, `card_snooze`, `card_dismiss`, `card_not_useful`, `kind_mute` | The card-state write of MD-2a (`routes/shell/state.py`) and the prefs write |
| `reach` | `alert_useful`, `alert_not_useful` | `outbox.rate_alert`, when RE-6 gives it a route |
| `chat` | `card_approve`, `card_reject`, `card_revise` (in §19.4 already) | The card answer in `ask_tools`. The outcome words come from `ask_tools.CARD_OUTCOMES` (HRD-7), the one vocabulary of card outcomes |
| `email` | `draft_sent`, with an edit distance in `features` | The send route. The distance is one number, and the row holds no text |

The time of each act is `at`. The learner reads the hour of use from it. The
limits of §19.4 hold: 180 days (D-PZ-51), no subject, no body, the learning
switch, and `MEMBER_ACTIONS_ORGS`.

### 5.4 "How I work" is "what Mira knows about me"

The page of §19.9 (MM-6) is the one page. This spec adds no second page.

- It shows the five layers. Each item shows its source, its evidence and a
  "Why" line.
- Each item has Change, Forget and "Never infer this".
- **Forget deletes for real.** It deletes the item, any summary that holds it,
  and any index entry of it. A count on read has no row, so Forget on a habit
  writes `never_infer` and the habit stops.
- The chat answers "What do you know about me" from the same read (§0 item 4
  there).

### 5.5 Who reads the model

- **Only the member** (D-PZ-3). An admin can turn the profile off for the org
  (D-PZ-17) and reads none of it.
- **Each read checks the permission at read time.** An item that points to a
  record shows only when the member can still open that record.
- The member model reaches a model call only through the Router, as clipped facts
  (D100 item 1, `my_day_feed.md` §10.4).

### 5.6 One voice

MM-7 and PZ-12 join the three style stores of §2.1, as §19.8 says. This spec
changes nothing in them. The consent wording D-PZ-50 stays open.

### 5.7 Other readers and inputs (for metorite-a3)

**[a3-1] The member model in the find ranking.** The current focus layer may
add one boost term to the CX-1 score of `GET /search/company`. The term goes
inside the fusion of `retrieval/company.py`: reciprocal rank fusion with
k = 60, and recency behind `CONTEXT_RECENCY`. It adds no second ranker. The
boost acts only when its own flag and `CONTEXT_RECENCY` are both on, and it
reads only the member's own focus items. Past chats stay the `chat` source of
`company_context.md` §8.2 (CX-4), read by HRD-21 (task 3225) under D-FIND-5.

`company_context.md` (CX-1) owns the score. This spec plans the slice, LOOP-15.
Two fences hold the change:

- The misses eval, `uv run python -m evals.search_company.run --fixture`. Its
  `MISSES_BASELINE` must not fall.
- `tests/unit/test_company_search_recency.py`. The answer with the flag off
  stays the same.

**[a3-3] Tool gaps and tool failures as inputs.** The learner may read two
stores that WS-TOF plans, and neither is on `main` today. The first is
`tool_gaps` (SBX-1, `agent_sandbox.md` §7.4). The second is the one tool
failure store if the owner approves it on task 3394, or else the log of task
1793. The learner copies neither, and reads only the member's own rows, as
counts by `category`, `outcome` and `failed_tool`. It never reads `need`, which
a model writes, or any error text (SBX-Q8), and only counts cross a tenant
(LOOP-16).

---

## 6. The finding contract

This section fills the `nudges` and `insights` points of `building_blocks.md`
§3.1. HX-0 (task 2762) writes the envelope of the points in `package_host.md`.
This spec owns the fields.

### 6.1 The fields of one finding

A finding is the signal of `my_day_feed.md` §4.1. Seven of its fields exist.
Five are new.

| Field | Meaning | In the signal today? |
|---|---|---|
| `kind` | The kind. This spec uses "kind" for "detector type" | Yes |
| `entity` | `{type, id, label}`. A task, a deal, a thread, a project, a metric. Never a person as the subject of a fault | Yes |
| `signal_id` with `fingerprint` | The dedupe key. One entity gives one card, and a changed fingerprint brings it back | Yes |
| `facts` | Typed values. The templates and the `decide` state read only these | Yes |
| `expires_at` | The time after which the finding is no longer true | Yes |
| `acts` | The suggested acts, each with its class (§11 there) | Yes |
| `href` | The deep link that code builds | Yes |
| `evidence` | 1 to 3 records, each `{app, type, id, label, href}`, the shape of `related` (`proactive_reach.md` §4.5). A finding with no evidence drops | New |
| `confidence` | 0 to 1. A rule in code gives 1. A `decide` answer gives its own | New |
| `visibility_scope` | Who may see the finding: a list of D12 subjects (`org`, `group:<slug>`, or an email). Required. Delivery checks it again (§8.4) | New |
| `severity_hint` | `now`, `needs_you`, `ready` or `info`. The old-path group of this finding, inside the limits of its kind | New |
| `batch_key` | The key that puts the findings of one hour into one message (`proactive_reach.md` §6.1) | New |

The why line stays a template with an id. That is the "reason" of
`building_blocks.md` §3.1.

The name `visibility_scope` keeps this field apart from the column `visibility`
that ALN-7 plans for `pm_alignment_findings`. That column will hold `shadow` or
`live`.

### 6.2 The declaration of one kind

An app or a connector declares each kind once, in its manifest point.

| Field | Meaning |
|---|---|
| `kind`, `label` | The id, and the name that the member sees in the menu |
| `point` | `nudges` or `insights` (§6.3) |
| `reach_class` | The most the kind may do outside the app: `none`, `brief`, `interrupt` or `urgent`. It joins `REACH_CLASS`. The default is `none` |
| `harm_class` | `normal` or `high`. A `high` kind can hurt a person or a deal when it is wrong. It gets no exploration (§7.5) |
| `prior` | The starting affinity of the kind, from −20 to +20. The cold start reads it (§7.4) |
| `lower_after` | The count of "Not useful" in 30 days that lowers the class of the kind for one member (§7.3). The default is 3 |
| `group` | The old-path group of `my_day_feed.md` §4.3 |
| `why` | The template ids of the why line |
| `source` | The provider id, or the job kind, or the connection |
| `fixtures` | One positive and one negative fixture |

### 6.3 `nudges` and `insights`

- **A `nudges` kind asks the member to act.** Its group is Now, Needs you or
  Ready for you.
- **An `insights` kind states a fact with no act due.** Its group is "For your
  information", and its reach class is `brief` at most. An example is
  `metric_moved`.

### 6.4 The rules of the contract

1. A finding with no evidence, or with no `visibility_scope`, drops before the feed.
2. The declaration check refuses a kind whose entity is a person as the
   subject of a fault (ALN-Q5).
3. A stored finding lives in the table of its app. Its provider reads that
   table.
4. The words come from templates. No model writes the headline or the why line.
5. A kind with no reviewed reach class never leaves the app
   (`proactive_reach.md` §4.2).

### 6.5 Contract fixtures

Each kind ships its two fixtures. A test feeds each fixture to the provider and
checks the finding against §6.1 and §6.4. A new kind with no fixture fails the
test. LOOP-7 builds the test.

---

## 7. Learning and evaluation

### 7.1 The rules that bind every learner

1. **The learner never hides a needs kind.** It may lower a needs card to
   Later, at worst (`my_day_feed.md` §7 rule 2). A mute never changes the bell.
2. **The learner lowers a reach class and never raises one.** The judge alone
   never wakes a member (`proactive_reach.md` §6.1).
3. **Only "Not useful" and the kind mute count against a kind.** A Dismiss or
   a Snooze counts for nothing (`my_day_feed.md` §6.6).
4. **The learner reads counts, never text.**
5. **One reader of the card counts.** `feed_decide._habits` becomes the one
   read, and `affinity` uses it too. Its name says habits, but it counts card
   kinds. The habits of `personalization.md` §19.5 are a different thing.

### 7.2 Affinity for each kind (MD-9)

The formula is in `my_day_feed.md` §7: `(acted − not_useful) / shown × 20`,
from −20 to +20, over 30 days, computed once each day at day start. LOOP-4
builds it. The count read is `feed_decide._habits`. The day value goes into a
Redis key through `acb_common.tenant_redis` (R5).

### 7.3 A threshold for each kind

**One member's dislike of a kind moves it from the interrupt to the digest.**
When a member marks `lower_after` cards of one kind "Not useful" in 30 days,
Reach lowers that kind by one class for that member. The default is 3. So `interrupt` becomes `brief`. A kind
mute makes it `none`.

- **The mute stays a choice.** At the same count, the menu offers "Stop
  showing <kind>" (`my_day_feed.md` §6.6). The lower class needs no tap.
- **The class comes back** when the member acts on a card of the kind. A
  member can also undo it from "Why am I seeing this".
- **A rare kind keeps its prior.** A member who saw a kind fewer than 5 times
  gets the prior of §7.4. So silence on a rare kind never reads as "no value".
- **Where.** The gate of `proactive_reach.md` §5.1 reads the lowered class.
  LOOP-5 builds it.

### 7.4 The cold start

**A new kind learns from day one.** It starts from the `prior` of its
declaration. A member with fewer than 5 shown cards of a kind gets the prior.

- **The org pool.** When 5 or more members of one org saw a kind, the prior of
  that org is the pooled count of those members. The pool is a count with no
  member in it, and it never leaves the org.
- **Across tenants.** No count crosses a tenant before LOOP-Q7. Then only a
  count for each kind crosses, from 10 orgs or more (LOOP-14).

### 7.5 Exploration

- After 14 days at the lower class, one card of the kind shows in the digest.
  An act on it restores the class (§7.3).
- A `high` harm kind gets no exploration.
- Exploration never takes an interrupt.

### 7.6 A new kind goes live through shadow

A new kind uses the mode order `off`, `shadow`, `on` (D75.7, D100.2). In
shadow, its findings reach no member. `feed_measure.py` (MD-6a-3) reports how
often a finding of the kind matched an act by the member within 24 hours. The
owner of the kind writes the gate number in its spec before the shadow week. A
kind that spends credits needs the owner to flip it (CLAUDE.md §3a rule 3).
LOOP-9 builds this.

### 7.7 Measures

`feed_measure.py` is the one admin read of the measures. It gives counts for
each kind and each org, never for a person.

| Measure | Formula |
|---|---|
| Precision | Acts within 24 hours ÷ shown |
| False alarms | "Not useful" ÷ shown |
| Mutes | Kind mutes ÷ members who saw the kind |
| Time to act | The median of `acted_at − first_seen_at` |
| Budget health | Interrupts for each member each day, and the share that waited for the brief |
| Recall | HathiLabs labels one week of its own missed items by hand, each quarter. No other org's data is read |

A pooled measure needs 5 members or more. A smaller count shows as "too few".
A holdout group for incrementality is LOOP-Q8.

### 7.8 Harm cases (zero tolerance)

| Harm | The guard | Fence |
|---|---|---|
| A member sees a record that the member cannot open | The ACL at delivery (§8.4) | R8 test, LOOP-7 |
| A finding accuses a person | Rule 2 of §6.4. The templates state a fact about a commitment | `test_finding_contract.py`, LOOP-7 |
| An interrupt in quiet hours, or over the cap | The budget of `proactive_reach.md` §6 | `test_reach_plan.py` |
| An act happens with no yes | D-RES-5, D161 item 5, D-PZ-48 | `test_my_day_card_approve.py` (RESUME-7) |
| A model writes the words of a nudge | Templates only | PZ-48's planned fence on the send path |

One harm case stops the kind. Its mode goes back to `shadow`, and the board
gets a rung 0 task.

---

## 8. Privacy and visibility

### 8.1 Tenancy

Each new table is tenant-scoped, with FORCE ROW LEVEL SECURITY at the
`get_db()` seam (R5, D15). Each Redis key goes through `acb_common.tenant_redis`.
A detector job takes the org from its own discovery read, never from a payload
(R11). A migration is expand only (R6), and takes the next free number at build
time (R1).

### 8.2 The member model is the member's own

D-PZ-3 holds. No admin and no other member reads a member's model, its counts
or its affinity. An admin can turn the profile off for the org (D-PZ-17).
Affinity is the member's taste in kinds of card. It is not a fact about the
member's work.

### 8.3 No score for a person (ALN-Q5)

No column, index, view or query of this loop groups by a person, except the
member's own read of the member's own model. A pooled count needs 5 members or
more. "Off track" is a finding about a commitment, and the person whose
commitment it is hears first (ALN-Q1, D192).

### 8.4 The ACL at delivery

Each recipient passes the owning app's authorized read when the finding is
delivered, and not only when it is found. A provider already reads with the
member's `UserContext` (`my_day_feed.md` §4.1). A notification uses
`deliverable` (`project_alignment.md` §7.2). A finding whose `visibility_scope` does
not hold the recipient drops in silence.

### 8.5 Data from a connected server

This spec sets the rule (§4.1.1 rule 4, LOOP-Q10). A finding built on the data
of a connected server reaches only the admin who connected the server. That
holds for OAuth and for a static key. The admin can state a wider D12 subject
on the connection. D98 says only that an OAuth call acts as that admin.

### 8.6 Private sources

Mail is private to its mailbox (D-EM-18). Calendar events need the
`_author_may_hear` branch (E3-S). A finding from mail or calendar has the
scope of the owner of the mailbox or the calendar.

---

## 9. The rungs (D211)

Each rung ends in a demo on production. A flag on for one pilot org is enough.
The demo tasks do not exist yet. LOOP-0 makes them with the title
`Proactive · Rung N demo · <what a person can do>`.

| Rung | What the owner sees on production | Slices |
|---|---|---|
| **0** | Nothing new. The board and the docs tell the truth | LOOP-0, LOOP-1 |
| **1. See it work** | The owner opens My Day and reads today's brief, beside the card of agent questions | LOOP-2, LOOP-3 |
| **2. It learns from me** | The owner marks one kind "Not useful" 3 times. The next morning those cards sit lower, and "Why am I seeing this" says why. That kind waits for the brief and sends no interrupt | LOOP-4, LOOP-5 |
| **3. It knows me, and I can see it** | Mira proposes one working pattern on a card. The owner says yes. "How I work" shows it, with its source and its evidence | MM-1, MM-2, PZ-15, MM-5, MM-6, LOOP-6. PZ-45 to PZ-49 if LOOP-Q5 is yes |
| **4. It catches a contradiction** | An email that contradicts a rule of a project gives one nudge, with the quote of the email and the quote of the rule | HX-0, LOOP-7, ALN-7, ALN-8, ALN-9, LOOP-8, LOOP-9 |
| **5. Company signals** | An odd week in the CRM totals shows as one card, with its driver as evidence | NUM rows of `number_sense.md`, LOOP-10, LOOP-11 |
| **6. Connectors join** | A change in a connected system becomes one nudge | CSY-R0, CSY-1, CSY-2, LOOP-12, LOOP-13 |

**Rung 4 depends on LOOP-Q6.** The checker of `project_alignment.md` reads
tasks only. If the owner answers no, the demo shows a task edit that
contradicts a rule.

**Rung 1 stays owner-led.** Reach to a new member, and every flag that spends
credits, stay the owner's. `BRIEF_COMPOSE` stays off, so the brief uses its
template lead. `ALIGN_JOBS_ORGS` stays empty until ALN-7 is real.

**Rungs 4 to 6 are one line each** (`orchestration_playbook.md` §6d rule 8).
After the rung 3 demo, the next plan cuts them again.

---

## 10. The slices

The tier follows `next-ticket.md` step 1b. Each slice ships dark. A row with an
id from another spec is that spec's slice, and this table only places it on a
rung. Each LOOP row names its board task, under task 3439.

| Id | Rung | What | Owner spec | Depends on | Done when | Tier · gate |
|---|---|---|---|---|---|---|
| **LOOP-0** (task 3439) | 0 | This spec, its INDEX row, four pointer lines, the fix of §2.3 item 1. Then the board tasks and the demo tasks | This spec | None | The doc tests of §11.2 pass, and each LOOP row has a task | LOW · AGENT-SAFE |
| **LOOP-1** (task 3441) | 0 | Comment the evidence on task 602, and fix items 2 to 4 of §2.3 in their specs | Each named spec | LOOP-0 | Task 602 holds the evidence. Each spec states the fact of `main` | LOW · AGENT-SAFE |
| **LOOP-2** (task 3442) | 1 | The brief in the app for HathiLabs: `BRIEF_ENABLED`, `BRIEF_ORGS` (the org id), `BRIEF_IN_APP`, and `NEXT_PUBLIC_BRIEF_CARD` in a release. The slice first reads `JOB_QUEUE` and `UNATTENDED_LEDGER` on the box | `core_brief.md` §10 rows 2 and 5 | LOOP-Q4 | `GET /brief/today` answers the owner's run. The `brief_runs` row reads `delivered`. My Day shows the card. The task holds the box, the flags and the log line | STANDARD · AGENT-SAFE after the owner preview, under the `enforcement-flip` grant to 2026-11-30. After that date, OWNER-GATE |
| **LOOP-3** (task 3443) | 1 | `CORE_EVENTS_ORGS` for HathiLabs. It gives a week of event evidence before rung 4 adds emit sites | `ai_studio.md` E12 | LOOP-Q4 | One person edit logs one `people.person.updated` event. The reload line is in the task | LOW · AGENT-SAFE under the `enforcement-flip` grant to 2026-11-30. After that date, OWNER-GATE |
| **LOOP-4** (task 3444) | 2 | MD-9, part 1: `affinity` from the one count read, the `mutes` key on the feed, "Why am I seeing this". Dark behind `MY_DAY_LEARN_ORGS` | `my_day_feed.md` MD-9, §6.6, §7 | None | Three "Not useful" on one kind give the affinity of §7.2. A needs card never leaves the feed. A mute changes the feed and not the bell. One module reads the counts | STANDARD · AGENT-SAFE. The flip is `enforcement-flip` |
| **LOOP-5** (task 3445) | 2 | The class that the learner lowers for one member (§7.3) | `proactive_reach.md` §4.2, §5.1 | LOOP-4 | A member with 3 "Not useful" on a kind in 30 days gets the kind one class lower. Another member keeps the class. No path raises a class | STANDARD · AGENT-SAFE |
| **MM-1** | 3 | The act record (task 2878), with the acts of §5.3 | `personalization.md` §19.4 | None | The fences of §19.10, and each new act has its seam | HIGH · AGENT-SAFE, dark |
| **MM-2**, **PZ-15**, **MM-5** | 3 | Habits (2879), proposals with Undo (309), a habit to a kept habit (2882) | `personalization.md` §12, §19 | MM-1 | Their own rows | Their own tiers · AGENT-SAFE |
| **MM-6** | 3 | The page "How I work" (2883) | `personalization.md` §19.9 | MM-2, PZ-15 | Its own row | STANDARD · owner preview |
| **LOOP-6** (task 3446) | 3 | The five layers on "How I work": source, evidence and "Why" on each item, the relationships and focus layers, and `never_infer` | `personalization.md` §19.9, this spec §5 | MM-6 | Each item shows its layer and source. "Never infer this" stops the pattern. Forget leaves no row and no index entry | STANDARD · owner preview |
| **HX-0** | 4 | The contribution points in `package_host.md` (task 2762) | `package_host.md` | None | Its own row | LOW · AGENT-SAFE |
| **LOOP-7** (task 3447) | 4 | The fields of §6.1, the declaration of §6.2, the rules of §6.4 and the fixture test | This spec §6, `my_day_feed.md` §4.1 | HX-0 | `test_finding_contract.py` passes. An R8 test shows that a recipient outside `visibility_scope` gets nothing | HIGH · AGENT-SAFE |
| **ALN-7**, **ALN-8**, **ALN-9** | 4 | The checker (1381), the eval gate (1382), the person's card (1383) | `project_alignment.md` §6, §10 | Their own rows | Their own rows | HIGH, STD, STD. ALN-8 is OWNER-GATE above USD 0.50 |
| **LOOP-8** (task 3448) | 4 | An email as a subject of the checker, heard by the owner of the mailbox only | `project_alignment.md` | LOOP-Q6, ALN-7 | Its row, written by its owner spec | HIGH · AGENT-SAFE |
| **LOOP-9** (task 3449) | 4 | The shadow gate for a new kind (§7.6) | `my_day_feed.md` MD-6a-3, this spec §7.6 | LOOP-7 | A kind in shadow reaches no member. The measure gives its precision | STANDARD · AGENT-SAFE |
| **LOOP-10** (task 3450) | 5 | `metric_moved` from number baselines, with the driver as evidence | `my_day_feed.md` MD-9, `number_sense.md` | LOOP-7, the NUM baselines | Its row | STANDARD · AGENT-SAFE |
| **LOOP-11** (task 3451) | 5 | A forecast that slips past the plan, as a commitment finding | `projects_reports.md` §6.8, this spec §4.2 | LOOP-7 | Its row | STANDARD · AGENT-SAFE |
| **LOOP-12** (task 3452) | 6 | A connection declares its kinds in the `nudges` and `insights` points | `package_host.md`, this spec §6.2 | LOOP-7 | Its row | STANDARD · AGENT-SAFE |
| **LOOP-13** (task 3453) | 6 | The poll of a connected system (§4.1.1) | `chat_connected_systems.md`, this spec §4.1.1 | CSY-2, CSY-8, the read-only key on each server (CSY-R0 for Shaurrya App), LOOP-Q10 | Its row | HIGH · AGENT-SAFE, dark |
| **LOOP-14** (task 3454) | Later | A prior for each kind across tenants | This spec §7.4 | LOOP-Q7 | Its row | HIGH · OWNER-GATE until LOOP-Q7 |
| **LOOP-15** (task 3455) | Later | The focus boost in the find ranking ([a3-1]) | This spec. `company_context.md` (CX-1) owns the score | LOOP-6 | The two fences of [a3-1] pass | STANDARD · AGENT-SAFE |
| **LOOP-16** (task 3456) | Later | Tool gaps and tool failures as inputs ([a3-3]) | `agent_sandbox.md` | SBX-1, and the failure store of task 3394 or the log of task 1793 | Its row | STANDARD · AGENT-SAFE |

The rows of rungs 4 to 6 and Later say "Its row". The plan after the rung 3
demo writes each "done when" in full, before its slice starts.

**Other slices that the loop waits for.** Decisions (§4.5) wait for WAIT-1
(task 1550) and RESUME-7 (task 3268). The rating of an alert (§4.7) waits for
RE-6.

---

## 11. Fences and verification

### 11.1 Fences (R7)

Each fence is advisory until its slice builds it.

| Rule | Fence |
|---|---|
| A needs card never leaves the feed | `tests/unit/test_feed_rank.py` (rule 2 of §7 there). V |
| One reader of the card counts | LOOP-4 extends `tests/unit/test_feed_rank.py`. A scan fails on a second query that groups `my_day_card_state` by kind |
| The learner never raises a class | LOOP-5 extends `tests/unit/test_reach_plan.py` |
| A finding with no evidence or no `visibility_scope` drops. A person is never the subject of a fault | `tests/unit/test_finding_contract.py` (LOOP-7, new) |
| A recipient outside `visibility_scope` gets nothing | An R8 test of LOOP-7, with the `_DB_GATE` |
| Only the member reads the model | The privacy fence of PZ-9 and the fences of `personalization.md` §19.10 |
| No model writes the words of a nudge | PZ-48's planned fence on the send path (`personalization.md` §17.9) |

### 11.2 Verification commands

Run these from the repository root for LOOP-0:

```bash
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py tests/unit/test_index_union.py tests/unit/test_decision_ids.py tests/unit/test_board_index.py -q
node .claude/hooks/ste-lint.mjs project-docs/specs/proactive_loop.md
```

Run these for LOOP-4 and LOOP-5:

```bash
uv run pytest tests/unit/test_feed_rank.py tests/unit/test_shell_feed.py tests/unit/test_reach_plan.py -q
```

Each later slice names its own test files in its row.

---

## 12. Owner questions

| # | Question | Recommendation | Why |
|---|---|---|---|
| **LOOP-Q1** | The ask names "a team member going off track". ALN-Q5 (D192) says no score for a person. Do you want a signal about a person beyond commitments? | **No.** Keep ALN-Q5. Commitments only, and the person hears first | A score of people turns the assistant into a monitor. A fact about a commitment helps the person fix it |
| **LOOP-Q2** | May an inferred preference save at once? | **No question.** D-PZ-2, §12.4 and D-PZ-48 already say: propose, then save on a yes | A decision stands until the owner reopens it |
| **LOOP-Q3** | D-PZ-51 keeps an act row 180 days. May counts for each kind stay after that, with no item id? | **Yes, later.** Rungs 1 to 3 need no such store | A count with no item keeps the learning and holds no record of a deed |
| **LOOP-Q4** | Rung 1 turns on the brief and the core events for HathiLabs only, after your preview. Agreed? | **Yes** | One pilot org is enough for a demo (D211 rule 1) |
| **LOOP-Q5** | D189 accepted the tone presets of §17. Do PZ-45 to PZ-49 join rung 3? | **Yes** | The member sees the voice on the same page as the other layers |
| **LOOP-Q6** | May the project checker read a member's own mail against the rules of a project? | **Yes, with limits.** Only the member's own mailbox, only for a project with rules, the mail as data, and only the member hears | The ask names this case. `project_alignment.md` §1.2 checks tasks only today |
| **LOOP-Q7** | May a count for each kind cross tenants, as a prior for a new org? | **Later, with limits.** Counts for each kind only, from 10 orgs or more, in a table that holds no org, member or entity | A store outside RLS is a new kind of risk. The prior of a kind and the org pool cover the cold start until then |
| **LOOP-Q8** | May a small holdout group get no nudges of one kind, to measure what the nudges add? | **Not before rung 5.** Then 5% of members at most, for `insights` kinds only, never for a needs kind | A holdout hides real work from a real member |
| **LOOP-Q9** | When KG-11 removes the Mem0 client, does `save_memory` leave with it? | **Yes.** `profile_remember` stays the one write of a fact about the member | D132 item 1 puts what Mira knows about a member in the profile. `personalization.md` §5.5 sends a kept fact there already |
| **LOOP-Q10** | The poll of a connected server (§4.1.1) follows three rules of this spec. A poll needs the admin's read-only key, and a hint is not enough. Its result is untrusted data. Its signal reaches only the admin who connected the server, for OAuth and for a static key. Agreed? | **Yes** | A poll runs on a schedule with no person to see a card. A wider audience needs a statement by the admin |

---

## 13. Sources

**Read in full:**

- The Claude memory support page.
- The CJR article on a study of ChatGPT memory. It reports that the system
  wrote 96% of the memories, and that 28% were sensitive under GDPR.
- The Glean blog on its personal graph.
- The Tableau Pulse document on insight types.
- ProactiveBench (arXiv 2410.12361), the first 100,000 characters.
- The Duolingo post "Hi, it's Duo".
- The LinkedIn post on email volume, part 1.

**Read in part, or from a second source.** The Microsoft 365 Copilot memory
workshop page (a second source). The survey of Liu, 2025 (in part).

**Snippet or abstract only.** The ChatGPT and Gemini help pages (blocked).
Apple, MemGPT, Letta, Mem0, Zep, A-Mem, LongMemEval, PersonaMem and LaMP. The
Microsoft 365 Copilot proactive push, the Outlook briefing, Jira Rovo, and the
LinkedIn WSDM paper.

**Not verified.** The principles of mixed-initiative interfaces of Horvitz
(CHI 1999). Only a snippet was read. This spec cites none of the principles as
a source.

**Not found.** Notion AI, Superhuman, Lindy, Dust, Viva Insights, Google Now,
the proactive features of Gemini, Salesforce Einstein, SpotIQ, Linear, and a
study of notification fatigue.

**Our own design, not tested.** No source describes the detection of a
contradiction or of work that goes off track. §4.2, rung 4 and LOOP-Q1 are our
own design. The shadow week of each kind (§7.6) tests them. The thresholds of
this spec are our own choices too:

- 3 "Not useful" in 30 days lower a class.
- A lowered kind comes back after 14 days.
- A pool needs 5 members.
- A count across tenants needs 10 orgs.

**The code.** Each path and flag of §2 was read on `origin/main` at
`320b04375` on 2026-11-16. The production column comes from the flag names in
the production `.env`, read on 2026-11-15 UTC.
