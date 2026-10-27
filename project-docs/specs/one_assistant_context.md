# One assistant: context that survives, and tool packs

**Status:** ✅ ACTIVE (2026-10-25). Board: **WS-67**. Decisions: **D116**, **D117**.
OA-1 and OA-2 are BUILT (PR #1341, 2026-10-25, "As built" below). OA-4 is
built, dark behind `CHAT_CONSTRAINTS` (2026-10-25, "As built" below). OA-5
is BUILT (2026-10-25): the report is `docs/one_assistant_measure_2026-10-25.md`.

OA-3 is built, dark behind `ATTACHMENT_OCR`, and §4.1 holds three conditions
for the flip. OA-6 is DECIDED (D117,
2026-10-26): yes, for all apps. §4.2 (OA-7), §4.3 (OA-8) and §6 (OA-9) hold
the design of 2026-10-26, with the owner's answers. Each slice waits for an
audit. OA-7g shipped live (PR #1356, `344cad4fc`). The auditor's gates and narrowings are in §4.5. Verified against code on 2026-10-26 at `1e43fc5c0`. Metorite: epic #445 in *AI Chat Interface*,
with slices #446 (OA-1) to #455 (OA-10).

OA-7a is BUILT, dark (2026-10-26, "As built, OA-7a" below). Since OA-7c the
executor reads its flag.

OA-7b is BUILT, dark (2026-10-26, "As built, OA-7b" below). OA-7c gives the
guarded builder its live caller.

OA-7c is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-7c"
below). The flag is off on each box, so no run loads a pack yet.

OA-7e is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-7e"
below). It adds layer (b), the meta tool `load_skill_tools`.

**As built, OA-7e.** Layer (b) loads a pack that layer (a) missed, in the
middle of a turn.
- **The parts.** `acb_skills/tool_packs.py` holds the tool (`load_tool`),
  its `FunctionMiddleware` (`LoadSkillTools`) and its provider
  (`LoadSkillToolsProvider`). Each provider of the egress guard and the tier
  policy lives in `acb_skills` too. `_tool_injection._offer_pack_loader`
  puts the provider on a view of the run (`agent_with_providers`).
- **The MAF hook.** The middleware calls
  `FunctionInvocationContext.add_tools`. The model sees the new tools on its
  next request. The middleware catches `ValueError` and `RuntimeError`
  (§4.5, "OA-7e facts"), and then the call loads nothing.
- **Where it acts.** `_apply_tool_packs` with `offer_loader` attaches it.
  Only `executor._apply_pack_turn` passes that, so only the run that layer
  (a) serves holds the tool. A shared room, WhatsApp and a delegated run get
  none. With the flag off, no tool and no pack state change.
- **The catalog.** The description of the tool lists each pack that the
  flag allows, that no toggle stops and that the run does not hold. Each
  line is the `summary` of the family. A run that holds each such pack gets
  no tool.
- **A turn that loads no pack.** It still binds a run key and a record of
  the packs, with no pack in it. A load needs both.
- **The same guards.** The load builds through `_pack_tools`, with the run
  context of the call. The build skips each name of the live tool list. A
  load answers the rules of the pack, through the one text of
  `persona_for` (`_rules_block`). A held pack adds its hold note.
- **The same caps.** A load refuses past 3 packs, past 35 tools a pack, or
  past 110 tools on the live list. The live count holds the load tool too.
  A refusal past a cap names `spawn_subagents` with the slug of the pack.
  The cap of layer (a) counts the load tool too, when the run offers it.
  The catalog offers a pack only if it can load. The live count is the
  base, the load tool and the packs of layer (a), less the specialists
  that left. That count plus the pack tools must stay within 110. The run
  must also hold fewer than 3 packs.
- **The run check.** A load refuses unless the frame holds the run key and
  the record of the run that offered the tool. The middleware matches its
  own tool by identity, never by name. One load runs at a time in a run.
- **A pack that the run holds.** A second load of it builds nothing, and
  its answer tells the model to call the tools.
- **Not sticky on load.** A mere load is not use. A pack becomes sticky
  only after one of its tools runs (`note_used` in the pack call). So a
  wrong pack never becomes sticky. A pack that the model loads and calls
  is sticky from the next turn on. The tool list then stays stable for the
  prompt cache.
- **The held answer.** The build reads it once, as layer (a) does. The
  strict hold within a turn stays.
- **Mail beside an MCP server.** A load of a pack of the email assistant
  refuses when the live list holds an MCP tool. A load cannot take the
  server away, and the answer sends the task to the email assistant. This
  keeps the OA-7b rule: a run with a mail pack holds no MCP tool. The
  offer cannot see an MCP server that attaches later, so the catalog line
  of a mail pack says that a connected server stops it. Precedence differs
  by layer. In layer (a) the email pack wins and the MCP server stays out.
  In layer (b) the MCP tool wins and the pack stays out.
- **The refusal acts live.** A load of `email-setup` after `email` gives
  the run every email pack. From that call on, a hand-off to the email
  assistant gets the pack refusal (`PackRun.refusal`). The answer gives the
  line "No email agent exists to hand a task to." back.
- **What stays for the turn.** A load is monotonic, so the
  `email_assistant` tool, the persona and its "Delegatable agents" block
  stay as they are. `PackRun.replaced` does not change. The next turn holds
  the pack by layer (a), and that turn swaps them.
- **Where both email packs load.** The verifier measured 52 tools in the
  live request of a flag-off run. With `email`, the load tool and
  `email-setup`, that request holds 111 tools or more, so the cap refuses
  the second load there. A smaller base can load both.
- **OA-9c.** It must read `PackRun.holds()`, which holds the packs of a
  load too, and not `PackRun.slugs`.
- Fence: `tests/unit/test_load_skill_tools.py`. It runs on
  `tests/unit/_native_maf_harness.py`, and a pin fails when MAF removes or
  renames `add_tools`.

**As built, OA-7c.** Layer (a) and the sticky state put packs in a live run.
Three inputs choose the packs: the sticky packs of the chat, a reference in
the member's words, and the words of a pack. The surface waits for OA-7d.
- **Where packs act.** `executor._start_pack_turn` is the one caller of
  `packs_on`. A pack loads only in the stream run of the orchestrator, in
  the member's own web chat. That run needs a verified member, an org that
  the flag names and a card channel. A shared room, the WhatsApp batch path,
  a delegated run and a run with no chat load no pack.
- **The read and the apply.** The read of the sticky state starts after the
  H-236 bind, beside the load. `_tool_injection._apply_tool_packs` applies
  the choice after `_apply_agent_md_overrides` and before
  `_inject_agent_tools`. The run key and the record of the packs go into the
  run binding after `bind_artifact_context`.
- **The state.** It is a hash in tenant Redis at
  `cc:<org>:tool-packs:<session>:<member hash>`. One field counts the member
  turns with `HINCRBY`. Each other field is one pack, with its first load and
  its last use.
- **Sticky on use.** A pack becomes sticky only after one of its tools ran
  (`tool_packs.note_used`). That write changes one field only (`HSET`), so
  two calls at once lose no update. A sticky pack leaves when no tool of it
  ran in the last 8 member turns.
- **The words.** Each email word needs an email context (fix round 1, P1-B).
  A bare "draft" or "unread" loads no pack, and "draft a reply" does. A run
  with the email pack gets no registry MCP server. So a false match took the
  MCP tools of a Notion or Linear draft away from the member.
- **The cap.** A request holds at most 3 packs and 110 tools. A pack of more
  than 35 tools never loads. Past the cap, the least recently used sticky
  pack leaves first. The words of this turn count as the newest use.
- **Two new packs past the cap.** The pack that replaces its owner's
  specialist leaves first (fix round 1, P1-A). So when both email packs
  match and one fits, `email-setup` stays, and the `email_assistant` tool
  stays for the mail.
- **The base count.** The apply stays before `_inject_agent_tools`, so the
  cap reads an estimate of the base tools (`_pack_base_count`). It reads the
  same scope and the same withheld names as the injection. With no app
  grant it equals the count after the injection, and a fence says so. A tool
  of a granted app is not in it. `_add_crm_facts_block` can only take tools
  away after it. The line `executor.pack_request_tools` logs the true count,
  and it warns past the cap.
- **The measured base.** The orchestrator held 55 tools before its packs on
  2026-10-27. Beside that base the 31 email tools fit, and the two email
  packs together (114 tools) do not.
- **The estimate errs on the safe side.** The live request of a flag-off run
  held 52 tools (the verifier, 2026-10-27). `_add_crm_facts_block` takes CRM
  tools away after the injection, so the cap counts 3 tools too many there.
- **A latent hazard: `own_tool_scope`.** The apply runs before
  `_apply_own_tool_scope`. An `own_tool_scope` on the orchestrator would
  strip each pack tool, because no pack tool is in that list. The
  orchestrator has none today. The pin
  `test_the_orchestrator_has_no_own_tool_scope` fails when a change adds one.
- **The read starts inside the main try.** The read of the sticky packs is
  the first statement of the main try of `run_agent_stream` (fix round 2).
  So a raise before that try starts no read, and its finally cancels a read
  that the run did not await.
- **The order.** Sticky packs keep their first-load order. The pack tools
  follow the core tools. The rules of each pack follow the persona in load
  order, before the cache break.
- **The swap.** With the `email` pack loaded, the `email_assistant` tool
  leaves the run. The email bullets of the persona leave by one exact swap
  (`EMAIL_DELEGATION_BULLETS` in `orchestrator/agents.py`). A swap that does
  not find the text loads no pack, and logs `executor.packs_refused`.
- **The refusal.** `tool_packs.delegation_refusal` holds the rule. It acts
  only while the run holds every pack of the owner (`tool_packs.owner_packs`),
  so both email packs (fix round 1, P1-A). Then the streaming seam
  `executor._run_sub_agent_streaming` refuses a hand-off to `email-assistant`
  from the chat run. That covers each specialist tool and
  `delegate_to_agent`. `agent_tools.call_agent` and each branch of
  `call_agents_parallel` ask the rule first, so the refusal marks no chat.
- **The `email` pack alone.** The run lacks the setup tools, so the hand-off
  stays open. `call_agent` and `delegate_to_agent` reach the email assistant.
  The rules lose the line "No email agent exists to hand a task to." In its
  place, one routing line sends a rule, a setting, the knowledge and the
  writing style to the email assistant (`Pack.handoff_swap`).
- **The registry block.** The "Delegatable agents" block of the injection
  leaves out an agent only while the run refuses the hand-off to it.
- **Where the refusal acts today.** Beside the measured base, the two email
  packs never load together, so the refusal does not act in a live run yet.
  A smaller base, or OA-7e, can load both.
- **What keeps delegation.** `call_agent_background` keeps delegation
  (§4.3.1). A delegated run never inherits the record of its parent.
- **The toggles.** An admin who turns a pack off for the orchestrator stops
  that pack. The catalog text of each pack says that a run loads it only
  where tool packs are on.

The six conditions of OA-7b fix round 1:
1. Each run builds its packs again. No `PackBuild` lives past its run.
2. The apply runs after the tenant bind. A run with no bound tenant loads no
   pack, and a build with no tenant is held.
3. The run check of a pack call keys on `tool_packs.PACK_RUN_KEY`. The
   server mints its value for each run, and never reads it from the request.
4. A lock for each chat covers the read and the write of the email working
   set in `_record_pack_call`.
5. `executor._emit_to_run_or_relay` sends "Now working on <mailbox>" to the
   run queue. A run with no queue gets the event on its relay.
6. A held pack adds `egress.EGRESS_HOLD_NOTE_PACK`, or its cards form, after
   its rules. Neither note names another assistant.

Pack names do not join `run_tools`. The run binds a record of its packs at
`tool_packs.PACKS_KEY` instead. The record holds the slugs and the held
answer of each pack. It also holds the owners that the packs replace, and
the owners that a hand-off may not reach. OA-9c reads that record to give a
child the same packs, and `run_holds_tool` keeps its meaning.

Open: "reply to" is not an email word. It also matches a WhatsApp or a
Linear reply, and a false match removes the MCP tools of the run. A turn
such as "reply to Anil" with no sticky pack keeps delegation, as before.

The probe of an eviction runs with native sessions on. A turn runs on a
stored call of a tool that the run no longer offers, and it finishes. The
probe is hermetic. Open: a live model provider can refuse such a call, and
no test shows what it does.

With the flag on, the turn-kind request lists the pack tools, because it
reads the tool names of the run. OA-7f adds the pack items.

Fences: `tests/unit/test_tool_packs_choice.py`,
`tests/unit/test_tool_packs_off.py`, and the probe in
`tests/unit/test_native_session_persistence.py`. The fences of OA-7b key the
run check on the server key now.

**As built, OA-7b.** `_pack_tools(slug, run_ctx)` in
`orchestrator/_tool_injection.py` builds the tools of one pack for one run.
It is async, and it returns a `PackBuild`: the held answer, the tools, the
dropped names and the skipped names. A slug that is no pack gives None.
- **The held answer at the build.** `_pack_held` reads it when the run
  builds the pack. It is true when `_run_no_egress(owner, run_ctx)` is true,
  or when Redis does not answer "clear" for the chat (`session_mark_state`
  with `fail_closed=True`). So a Redis fault holds the pack. A run context
  that is empty, or that has no tenant or no session, is held too, because
  no mark can be read for it.
- **The hold within a turn (STRICT, confirmed by the owner on 2026-10-27).** The first email
  call of a clean turn marks the chat. Each later call of that turn runs
  held. An egress tool then answers the held answer and does not run. Each
  other tool runs with `no_egress` set. With `EMAIL_HELD_CARD_SEND` on, a
  forward or a new mail goes to the held card.
- **Each call.** `_pack_call` puts `tool_packs.owner_scope` outside the
  permission gate. It marks the chat before the tool body, through the
  executor's `_mark_untrusted_session`. It closes the profile writes after
  the call, also after a raise. It writes the email working set through
  `_email_working_set_for` and `_record_email_working_set`.
- **The held set.** A held pack drops each tool that `_is_egress` names.
  This drop is the only held-set control of a pack run, because a pack run
  gets no `EgressGuardProvider`.
- **The texts of a pack call.** `owner_scope` sets `egress.PACK_CALL_KEY`.
  A held answer of a pack call is `EGRESS_HELD_PACK_ANSWER`, or its cards
  form. It names no other assistant. It does not say that the web is off,
  because the orchestrator's own web tools stay on behind the card.
  The `email` rules name `artifact:orchestrator:<path>` for a file of the
  chat, because a bare path names a file of the email assistant.
- **The marks.** Each pack tool carries `__cc_gated__`, so
  `_gate_own_maf_tools` never gates it twice. `PACK_OWNER_ATTR` names the
  owner. `_inject_mcp_servers` gives no registry MCP server to a run that
  holds a tool of a pack whose owner reads mail. That is both email packs
  (`email` and `email-setup`), and a pack that stays on for a later turn.
- **Three guards that the design did not name.** A pack tool refuses a call
  from another run, or from a frame with no run (`PACK_CALL_REFUSED`). A
  run context with no `run_id` builds no tool. The builder skips a name that
  the run holds (`holds`, and `run_tools` of the run context).
- **One loader.** `_pack_owner_module` loads an owner once for each process,
  and the skills catalog calls it too. It keeps a failed import for 60 s
  (`PACK_OWNER_RETRY_S`), so a fixed owner loads with no restart. A
  `SystemExit` at import is a failed import, and `KeyboardInterrupt` still
  propagates (OA-7a verifier F2 and F3).
- **Fences.** `tests/unit/test_pack_guards.py` holds the ten cases of
  §4.2.8 and the cases of fix round 1. The 13 files that call
  `load_email_agent` run each test twice. `test_email_chat_reply.py` does
  the same through its own loader. One run uses the owner's own tools. The
  other run uses the tools of the builder, in a run of the orchestrator that
  is open on its own frame. A held test becomes a mark of the chat, so only
  the builder and the scope can give the tool body the held answer.
  `test_egress_card_bound_pin.py` passes with no change.

Three facts differ from the design text of §4.2.3:
- `pending_ask.py` does not read `agent_name` from the artifact context. It
  reads the agent of a card row from the run record of the thread. So a card
  of a pack call names the run's agent, as a card of a delegated sub-run
  does today. The fence pins that.
- The hold is stricter than a delegation. A delegation reads a Redis fault
  as no mark, and a pack reads it as held. Within one turn, each call after
  the first email call is held. A second delegation in one turn is held
  only when the process cache holds the mark.
- The scope binds `agent_name`, `no_egress` and `PACK_CALL_KEY`, and no
  `deliver_to`. A pack tool that writes a file would key its blob by the
  owner, in the workspace of the run. No email tool writes a file. OA-8
  must check each pack whose tools write a file.

Open (OA-7a verifier F1): the Agents page adds the cost of each pack to each
agent. No test of that page exists. A later UI slice takes packs out of that
sum, and adds its own Vitest case.

**As built, OA-7a.** A WS-23 skill family with an `owner` key is a tool pack
(`acb_skills/skill_families.py`). Two packs hold the 59 own tools of the email
assistant: `email` (31 tools) and `email-setup` (28 tools). The split follows
the groups of `_TOOLS` in `agent-email-assistant/agents.py`. So
`update_account` and `unsubscribe_sender` are in `email-setup`, and
`sync_account`, `reclassify_reply_zero` and `narrow_and_read` are in `email`.
- `acb_skills/tool_packs.py` holds the rules block, the references, the
  words, `tier_floor` and `keeps_reasoning` of each pack. The `email` rules
  are the `RULES` text of the OA-5 fixture.
- `packs_on(org)` is the one reader of `TOOL_PACKS` and `TOOL_PACKS_ORGS`.
  It fails closed, and nothing calls it yet.
- A pack is not an injected family. The injected scope, the skill index, the
  agent matrix and the catalog total read `injected_families()`, and they
  skip a pack. So a pack toggle changes no run until OA-7c, and its text in
  the Skills catalog says so.
- `build_catalog` gives the cost of a pack as its rules block plus the
  schemas of its owner's tools. The catalog route loads the owner module
  once for each process, from the static registry, and it runs no tool.
- Fences: `tests/unit/test_tool_packs_registry.py` and the owner universe
  in `tests/unit/test_skills_registry.py`.
- Open: the cost meter on the Agents page adds the cost of each pack to
  each agent. A later UI slice must take packs out of that sum.

**As built, OA-5.** `evals/one_assistant_measure` runs 13 email chats (18
turns) through the one harness of the routing eval, two ways. One way
delegates to the email assistant, as today. The other way is an eval-only
tool pack. Fence: `tests/unit/test_one_assistant_measure.py`.

The live run of 2026-10-25 ran 3 times on each arm. Each arm passed 51 of
54 turns, after a re-score with the fixed checks of fix round 1. The median
turn took 22.6 s and 10.0 s, and the pack cost half as much. The Navy
shape passed 6 of 6 on both arms, so no answer named CB6 or CB7 as a part.
Each arm stated a scanned file's name as its content once in 3 runs, so
the shape does not fix cause C2.

The measure did not read `agent.run_timing` or `usage_event`, because the
gateway and the Console write them. It did not read the executor's
`executor.run_setup_ms` either. The harness timed each turn and priced
each call instead.

**As built, OA-1 and OA-2.** `acb_skills/handoff.py` owns what crosses a
handoff. The top-level run binds `member_turns`: the member's typed words of
the chat, newest last, at most 8 and 6000 characters. The current turn comes
from `assistant_profile.MEMBER_TYPED`, never from `payload["message"]`, so a
Continue note cannot pose as the member. A paste is not the member's words.
`head_task()` heads every delegated task with the words, the rule that they
outrank the task, and the reply rules of OA-2. The streaming seam and the
four batch sites call it. The tier question reads the task only
(`task_of`). Fences: `tests/unit/test_handoff_words.py` and
`evals/trajectories/test_handoff_words_trajectory.py`. Open: a steer that
the gateway replays after a run died is in neither the history nor the
typed words, so it does not reach the head. OA-5 measured the live half of
"no CB6" (above). It did not check the reply marks of OA-2.

**As built, OA-4.** `acb_skills/chat_constraints.py` owns the list. It
ships dark behind `CHAT_CONSTRAINTS`, default off.
- Two tools belong to the orchestrator only: `keep_correction` and
  `drop_correction`.
- The list lives in tenant Redis at
  `cc:<org>:chat-limits:<session>:<member hash>`. It holds 10 items at
  most, each of 300 characters at most.
- A keep stores only whole sentences that the member typed in this turn.
  Only ". ! and a line break" end a sentence. An ellipsis and the dot of a
  common abbreviation (`e.g.`) end nothing. A text with a strike mark is
  refused.
- By design, a line break and "!" end a sentence, so a keep can hold part
  of the member's own words. "Use tables! No way." can keep "Use tables!".
  That is the member's text, never a stranger's. The tool's reply line
  names each kept item, so the member sees it and can drop it.
- A drop removes an item only after the member approves a card that shows
  the exact item. A run with no card channel drops nothing.
- The per-turn context of the orchestrator shows the list after the email
  working set. `head_task()` puts it after the reply rules of each task.
- The tools open only for the member's own chat, on the web. They keep
  nothing on the WhatsApp batch path (D115) or in a shared room.
- Fences: `tests/unit/test_chat_constraints.py` and
  `evals/trajectories/test_chat_constraints_trajectory.py`.
- Open: the visible chip in the chat is a later slice.

**OA-3 is built and dark (2026-10-25).** The flag `ATTACHMENT_OCR` turns it
on, and it is off by default. §4.1 records what was built, and the three
conditions to meet before the flag turns on.

**Owner:** WS-67. **Amends:** D99 item 2, for conversational work (D117,
2026-10-26). **Composes with:** `navigation_shell.md` §7.1 (D99, one assistant),
`skills_registry.md` (WS-23, the skill-family registry),
`email_app_master_plan.md` §21 (the held-chat guard),
`multi_agent_orchestration.md` (WS-12, delegation), and the chat-speed
work of #1313 and #1323.

---

## 1. Why this spec exists

On 2026-10-25 the owner asked the assistant about a Navy order in the
Fracktal mailbox. The chat is session `de64d903` in the `fracktalworks`
organization. The assistant named the two parts "CB6 and CB7". That name
came only from the file name of a sample attachment. The owner then said
that the attachments are only references. The assistant still used the
name in its next answer.

The trace shows four causes. Each one is in the design, not in one prompt.

| # | Cause | Evidence |
|---|---|---|
| C1 | The attachment reader has no OCR. A scanned PDF gives no text, so a file name is the only signal about its content. | `routes/email/transport/attachments.py`, `_NO_TEXT_REASON`. All three PDFs answered "no readable text". |
| C2 | A specialist answers in prose. Nothing marks a quote and a guess apart, so a hedge becomes firmer at each hop. | Turn 2: "strongly suggests" (email assistant), then "almost certainly" (orchestrator), then "designated" (turn 3). |
| C3 | The member's words do not reach the specialist. It has no memory of the chat. The rules carry links and draft text, not corrections. | Turn 3: the task held "(e.g. CB6/CB7)" and not the member's note. `orchestrator/agents.py`, the SPECIALIST AGENT TOOLS block. |
| C4 | The chat stores no correction. On the next turn the assistant reads its own earlier claim in the history and trusts it. | Turn 3: the email assistant said that CB6 appears only in a file name. The orchestrator still wrote "designated CB6 and CB7". |

Both runs used `deepseek/deepseek-v4-pro` (the Router's `usage_event`). The
model reads no image. The model is a factor, and it is not the cause.

**A PDF with a text layer already reads.** C1 is about scanned pages and
images only.

## 2. The decision this spec carries (D116)

Owner decision, 2026-10-25, in chat: take the recommendations.

1. Fix the handoff now (OA-1, OA-2). It helps for as long as a handoff
   exists.
2. Add OCR (OA-3). The owner chose a library, not an image model.
3. Store corrections in the chat (OA-4).
4. Measure before any rebuild (OA-5). Then the owner decides (OA-6).
5. The target shape, if OA-5 supports it: one assistant holds the
   conversation and loads each app's tools as a **tool pack**. A
   sub-agent stays only for a heavy job, with a structured handoff.

**OA-6, decided 2026-10-26 (D117).** OA-5 supported it, and the owner said
"Yes, for all apps". The owner then widened it. One chat serves the whole
app, and its tools load per turn, fast first. The main assistant keeps
dynamic sub-agents for parallel work. `work_plan.md` §3 D117 holds the
owner's words and the eight points.

## 3. The target shape

| Part | Now | Target |
|---|---|---|
| Who holds the conversation | The orchestrator, and each specialist sees only a task text | One assistant, with the member's words, corrections and working sets |
| How an app joins a turn | A specialist agent with its own prompt and tools | A tool pack: the app's tools plus a short rules block, loaded when the turn needs it |
| Heavy jobs (many mails, long reads) | The same specialist | A sub-agent with the contract of §6 |
| Guards (held chat, cards) | In the email tools | Unchanged. The guard is in the tools, not in a prompt |

A tool pack uses the WS-23 registry (`acb_skills/skill_families.py`). Today
the email tools are an agent's own tools and are not in that registry. OA-7
moves them.

## 4. Slices

| Slice | What | Risk tier | Gate |
|---|---|---|---|
| **OA-1** | The orchestrator copies the member's words verbatim into each task: the request, and every correction or limit in this chat. It never puts its own guess in a task as an example. | MEDIUM | none |
| **OA-2** | A specialist reply marks each fact as **quoted** (with its source) or **inferred**. The orchestrator never states an inferred item as fact, and never makes a hedge firmer. A file name is a label, never content. A "sample" or "for reference" attachment is never the subject of the order. | MEDIUM | none |
| **OA-3** | OCR for a scanned PDF and an image in `read_attachment`. It runs off the event loop, with a page cap and a time cap. The result says that the text came from OCR. | HIGH (new dependency, untrusted file input) | none |
| **OA-4** | Chat constraints: a correction or a limit that the member states goes into the chat's working set, beside the email working set. The per-turn context shows it. The member can see and remove it. | MEDIUM | none |
| **OA-5** | Measure. Run the email scenarios two ways: email tools loaded into the one assistant, and delegated as now. Compare accuracy, latency and cost. | LOW (eval only) | none |
| **OA-6** | DECIDED 2026-10-26 (D117): yes, for all apps. D99 item 2 changes for conversational work. | — | done |
| **OA-7** | The email tool pack: the email tools join the WS-23 registry, and the email rules become the pack's rules block. The per-turn loader of D117 item 3. Slices OA-7a to OA-7h, §4.2. OA-7g shipped live ahead of the rest. | HIGH (the egress guard moves with the tools) | an audit of §4.2 |
| **OA-8** | The tool packs of every other app that the orchestrator delegates to in conversation (D117 item 1). Slices OA-8a to OA-8e, §4.3. | MEDIUM | OA-7 |
| **OA-9** | Dynamic sub-agents (D117 item 5): the contract of §6, for parallel work and heavy jobs. Read first, writes after. Slices OA-9a to OA-9e, §6.7. | MEDIUM | an audit of §6 |
| **OA-10** | Retire conversational delegation. A specialist agent stays for a scheduled job, a workflow and an external agent. The Email and Projects rails move to the one assistant, with their pack pinned. | HIGH | OA-8e passes, OA-9 |

### 4.1 OA-3 as built (2026-10-25)

**What it does.** With `ATTACHMENT_OCR` on, the one bounded parse
(`attachment_tools.parse_bounded`) reads two kinds of file by OCR. The first
is a PDF whose text layer gives no words. The second is a `.png`, `.jpg` or
`.jpeg` image. The text starts with the OCR marker. A read takes 15 pages at
most, and a capped read says how many it read.

With the flag off, each path is byte-identical.

**Who gets OCR.** Only a caller that can wait for it. A caller with a short
budget reads with `ocr=False`. `TestTheCallers` in the fence fails on a new
caller, and on a short-budget caller that can reach OCR. It finds direct
calls only.

| Caller | Its budget | OCR |
|---|---|---|
| The chat tool `read_attachment` (`attachment_tools.py`) | none: a tool call | yes |
| The email text route `GET /email/attachments/{id}/text` (`attachment_text`), for `read_email_attachment` | 60 s | yes, inside a deadline of 55 s |
| The reply drafter `draft_files._read_text`: the reply card, the composer and a rule draft | 10 s, 8 s and 20 s for all files | no (`ocr=False`) |

A no-OCR read also uses the flag-off cache name. So it never writes a "no
text" answer where an OCR read looks, and it never reads an OCR answer.

**How the child stays apart.** The OCR runs in a child process
(`acb_skills/attachment_ocr_worker.py`), never in the gateway. The child gets
the `child_env` allowlist and no secret. On Linux it confines itself in this
order, before it reads one byte of the file:

1. It counts its threads, and answers "unavailable" for any count but 1.
   Landlock and seccomp bind the calling thread and the threads that it
   starts later. A thread that runs already stays free.
2. Landlock lets it read only the Python and shared library dirs. It may
   write nowhere, and it may not bind or connect a TCP socket (ABI 4).
3. A seccomp filter answers EPERM to each call on its deny list.
4. It loads its libraries and models. The helper thread of onnxruntime
   starts here, so it starts confined.
5. `RLIMIT_NPROC` and `RLIMIT_FSIZE` go to 0. `RLIMIT_AS` and `RLIMIT_CPU`
   cap memory and CPU from the start. `RLIMIT_CORE` is 0 from the start, so
   a crash writes no core file.

A kernel below Landlock ABI 4 makes the child answer "unavailable". So does
another machine than x86-64, and a filter that does not install. It fails
closed. The box runs kernel 6.8 on x86-64, which has ABI 4.

**What the probes proved.** `TestTheConfinement` runs one probe child twice:
unconfined, and confined by the worker's own code. The table holds the
confined result on the dev VPS (kernel 6.8, Landlock ABI 4, x86-64). Each
attack worked unconfined.

| Attack on the gateway or the box | Confined |
|---|---|
| Read a file of the app dir, or the parent's environ | EACCES (Landlock) |
| Write a file | EACCES (Landlock) |
| Connect a TCP socket made before the confinement | EACCES (Landlock) |
| Open a TCP, UDP or Unix socket | EPERM (seccomp) |
| Set up io_uring | EPERM (seccomp) |
| Send a signal to the parent | EPERM (seccomp) |
| Read or set the parent's limits (`prlimit64`) | EPERM (seccomp) |
| Make the kernel signal the parent (`F_SETOWN`, `FIOSETOWN`) | EPERM (seccomp) |
| Set the parent's priority or CPUs | EPERM (seccomp) |
| Fork | EAGAIN (`RLIMIT_NPROC`) |
| Confine while a second thread runs | refused: "unavailable" |

The same calls on the child itself still pass (`prlimit64` on pid 0,
`fcntl` with `F_GETFL`). So the filter refuses by call and by argument, not
in a block.

**Time.** The child writes each page when it ends it. The parent kills the
child's process group at 28 s, on a cancel, and when its answer passes
4 MiB. After a time kill, the parent keeps the pages that arrived and says
how many it read.

The route for the text of an email waits 30 s for the provider fetch, and
answers 504 after that. The fetch goes on in its own session and fills the
bytes cache, so the next read is fast. OCR gets a deadline of 55 s from
the start of the route. With less time left, OCR answers "too slow" and
starts no child. So the route answers inside the 60 s of
`read_email_attachment`.

**The fetch that runs on (fix rounds 4 and 5).** A retry of the same read
joins the running fetch and starts no second one. At most 2 fetches run on
after their route answered. Above that count, the route cancels its fetch,
and a read that joined it answers the same 504. A fetch ends after 120 s in
total. The member's hold stays taken until the fetch ends.

The fetch holds a connection of the member lane, and the limit of 2 caps
the extra connections. It takes no background mark (fix round 5). The H-390
gate has 4 slots for sync, rules, classify and embed, and a read that
waited there answered 504 before the gateway asked the provider.

**Residual risk.**

- The seccomp filter is a DENY list. It refuses each call that it names, and
  it passes each call that nobody named. The probes prove the calls in the
  table, and no more. Condition 3 replaces this with an allow list.
- A confined child can still read the Python and library dirs, which hold no
  secret.
- A Windows dev box runs the child unconfined, because Windows has no
  Landlock and no seccomp.

**Conditions before the flag turns on.** The fix rounds of 2026-10-25 record
these three, and build none of them:

1. **A share for each organization.** One child slot serves the whole
   process. A long scan holds it for up to 30 s. In that time each other
   organization gets "busy". Add a share for each organization, or the
   owner accepts one slot in writing.
2. **The tool descriptions.** The docstring of `read_email_attachment` says
   that it reads no image. The docstring of `read_attachment` names only
   document, sheet and text files. `addendum.ATTACHMENT_FAILURE_RULE` tells
   the model to suggest a .docx or a .pdf. So the model does not try an
   image. The pull request that turns the flag on changes these texts.
3. **An allow list from a trace.** Trace the system calls of a real OCR read
   on the box, for a PDF scan and for an image. Build an allow list from
   that trace. Then review the deny list against it, or replace the deny
   list with it.

Fence: `tests/unit/test_attachment_ocr.py`. Its Linux class
`TestTheConfinement` runs on the Linux CI.

### 4.2 OA-7: the email tool pack, and the pack mechanism

**Status.** The design is from 2026-10-26. The owner decided OA-6 on 2026-10-26: D117 is on main (PR #1353, `e089f39d1`). Each anchor below is a line at `e089f39d1`.

**Owner answers, 2026-10-26.** The owner answered the four open points of the design.
1. **The floor in a mail chat.** A run with the email pack KEEPS `web_search` and `fetch_page`, behind the untrusted-answer card, as today (§4.2.3).
2. **The in-app rails.** The Email and Projects rails keep their specialist until OA-10. At OA-10 they move to the one assistant, with their pack pinned (§4.3.4).
3. **Writes in sub-agents.** Read first, writes after. OA-9b ships read-only children. A batch write goes through the batch tool of the app, in the main assistant. OA-9d then adds children that write, with one batch card for each fan-out (§6.5).
4. **The rule against an unasked draft.** Ship it live now, with no `CHAT_CONSTRAINTS`. It goes in the orchestrator persona and in `handoff._RULES`, both always on. It ships as its own small PR, ahead of OA-7 (§4.2.6).

**Short paths.**
- `orchestrator/` means `apps/services/orchestrator/orchestrator/`.
- `routes/` means `apps/services/gateway/gateway/routes/`.
- `acb_skills/`, `acb_common/` and `acb_llm/` mean `packages/<name>/<name>/`.
- `agent-*/` means `apps/agents/agent-*/`.
- `cp/` means `workbench/control_plane/src/`.
- MAF paths start at `.venv/Lib/site-packages/agent_framework/`.

OA-7 builds the pack mechanism and the email pack. OA-8 adds the packs of the other apps (§4.3). OA-9 adds dynamic sub-agents (§6). The owner answers of 2026-10-26 close the four open points of the design.

#### 4.2.1 What a pack is in code

A pack is a WS-23 skill family with an owner agent. It has three parts.

1. **The registry entry.** It is one row in `SKILL_FAMILIES` (`acb_skills/skill_families.py:52`), with a new key, `owner`. A family with an `owner` is a pack. Its `summary` (`:55-65`) is the index line that the model reads.
2. **The tools.** They are the own tools of the owner, as the owner builds them. These are `_agent_tools()` (`agent-email-assistant/agents.py:8942-8954`) and `_narrow_tool()` (`:8917-8934`). The same function objects run, so each tool keeps its card, its frame and its `__tool_risk__`.
3. **The rules block.** It is a short text in `acb_skills/tool_packs.py` (new), keyed by the slug of the family. The email rules start from `RULES` of the OA-5 fixture (`evals/one_assistant_measure/pack.py:56-91`). That text has about 2,200 characters.

**Where the rules go.** The rules go at the end of the stable persona, in load order, before the cache break. The break follows the persona (`orchestrator/agents.py:1112-1113`), so the rules sit in the cached prefix. A held pack also adds `egress_hold_note()` (`acb_skills/egress.py:740-749`), so the model knows what is held before it plans.

**The delegation bullets.** The delegation bullets of a pack leave the persona by one exact swap. For email, these are `orchestrator/agents.py:90-122`. The swap fails loud when a line moved, as `_orchestrator_instructions` does (`:333-338`).

**The email split.** The email assistant owns 59 tools (`agent-email-assistant/config.json`, `own_tool_scope`). OA-5 loaded 58 of them, which added about 20,800 input tokens to each call (`docs/one_assistant_measure_2026-10-25.md:116-121`). OA-7 splits the tools into two packs:
- `email` (31 tools): read, find, summarise, reply, send, forward, drafts, schedules, follow-ups and inbox acts.
- `email-setup` (28 tools): rules, settings, knowledge, voice, senders and categories.

**The WS-23 drift gate.** `tests/unit/test_skills_registry.py:81-88` fails on a registered tool that injection cannot offer. A pack family gets a second universe: the `own_tool_scope` and the `_agent_tools()` of its owner. The gate then fails on two errors:
- a pack tool that the owner does not hold
- an owner tool in two packs

**Why this closes a gap.** `skills_registry.md` §3 planned an `email` family (`:61-66`). S1 deferred it, because the registry held injected tools only (`:116-122`). The `owner` key closes that gap.

**One seam.** A pack adds no injection path. The builder lives in `orchestrator/_tool_injection.py`, beside `_inject_agent_tools` (`:2169`). It reuses the egress rule, the gate and the scope helpers there. The S2 toggles govern packs too. An admin who disables `email` for the orchestrator stops that pack (`skills_registry.md:34-38`, rule 1).

#### 4.2.2 How a turn loads packs

Three layers choose the packs, in the order that D117 item 3 sets. Layer (a) decides almost every turn. Layers (b) and (c) catch its misses.

**Layer (a): a fixed choice, with no model call.** It runs during setup, from server-side inputs only (R5). It loads the union of four sets:

1. **Sticky packs.** These are the packs that this chat used before, in their first-load order. They live in tenant Redis at `cc:<org>:tool-packs:<session>`. They use the idiom (`acb_skills/email_chat_context.py:22-27`) and the 1.0 s timeout (`:57`) of the email working set. A pack becomes sticky only after one of its tools ran.
2. **The surface.** This is the app that the member is in. Today it reaches the server only as text in `system_context` (`cp/lib/shell/assistant.ts:738-756`, `cp/app/api/agent/chat/route.ts:823`). OA-7d adds a structured `surface` field. The field is a hint. It can add the core pack of the app, and nothing else.
3. **References.** These are an in-app link or a record id in the words of the member, for example `/email?email=<id>`. Each pack registers a pattern for them.
4. **Words.** This is a short word list for each pack. The loader matches it against the typed words of the member in this turn.

**Layer (b): the meta tool `load_skill_tools(family)`.** `skills_scope_out.md` §7.5 designed this tool and did not build it (`:265-309`). Its description lists the index line of each pack that is not loaded. A call adds the tools of the pack to the live run, and returns the rules block of the pack as its text. A load is monotonic: no tool leaves during a run.

**The MAF hook.** MAF 1.x has `FunctionInvocationContext.add_tools` (`agent_framework/_middleware.py:470`). It is an experimental feature, `PROGRESSIVE_TOOLS`. The model sees the run-local tool list on its next request (`agent_framework/_tools.py:5449-5454`). A `FunctionMiddleware` on the view of the run catches the call and adds the tools. `ToolHintRecorder` already sees each call in this way (`acb_skills/tier_policy.py:715`).

**Layer (c): the turn-kind question that already runs.** `tier_policy.turn_kind` sends one System-1 request with one or two items (`acb_skills/tier_policy.py:514-523`). System 1 takes up to 20 items in one request (`acb_skills/system_one.py:65`). OA-7f adds one `yes_no` item for each allowed pack that layer (a) did not load.

A "yes" at or above the threshold of the effort loads the pack (`acb_skills/tier_policy.py:93-97`). The executor applies the answer after its existing wait (`orchestrator/executor.py:5874-5877`), before the first model request. Layer (c) never adds a request.

**When layer (c) is on the critical path.** It is on the path only when the question already goes out:
- the turn has 12 words or more (`acb_skills/tier_policy.py:154`, `:506`)
- the effort is not Max (`:501-503`)
- `AI_TIER_ROUTING` covers the orchestrator (`orchestrator/executor.py:8844-8845`)

The question already starts early, beside setup (`orchestrator/executor.py:5768-5769`). The run already waits for it (`:5874-5885`). A short turn sends no question, so layers (a) and (b) decide it.

**The decide door is not used.** `acb_llm.decide` sends typed questions to `tier-decide`. Its switch `DECIDE_ENABLED` is owner-only and off (`acb_llm/decide.py:16-18`). The turn-kind question stays on `tier-fast` by design (`acb_common/settings.py:806`). A separate decide call would add one request to the turn. The pack items ride in the request that already runs, as D117 item 3 asks.

A move of the whole question to `tier-decide` is a WS-45 decision, not an OA-7 one.

**Added latency, by layer.**

| Layer | When it acts | Added wait on the critical path |
|---|---|---|
| (a) | Each turn with the flag on | One Redis read beside setup, and a regex. A few milliseconds (estimate). |
| (c) | Only when the turn-kind question already goes out | No extra request. A few more answer tokens, inside the existing wait (estimate). |
| (b) | Only when layers (a) and (c) both missed | One more model round of about 3.8 s (report `:121`), and one cold request. |

The first build of a pack in a process imports the owner module once. A delegation pays this cost each time today (`executor.load_agent_ms`, `orchestrator/executor.py:1982-2009`). The builder caches the module for each process.

**A wrong choice.** A missed pack costs one round of layer (b). A wrong pack costs tokens only. It never becomes sticky, because none of its tools ran.

**The cap.**
- A request carries at most 110 tools and at most 3 packs. OA-5 measured 110 tools at 51 of 54 turns (report `:85`, `:121`).
- A pack holds at most 35 tools.
- At the start of a turn, a load past the cap first drops the least recently used pack.
- In the middle of a turn, `load_skill_tools` refuses a load past the cap. Its answer names `spawn_subagents` with that pack (§6).
- A sticky pack leaves at the start of a turn when no tool of it ran in the last 8 member turns.

**Typical tool counts.** These are estimates from the OA-5 base of 53 tools (report `:118`).

| Chat | Tools in each request |
|---|---|
| No app | 53, as today |
| Email | 83: 53, less `email_assistant`, plus 31 |
| Email and CRM | 90 |
| Email and Projects | 109 |
| A third app | The cap drops the least recently used pack |

**The prompt cache.** About 98 percent of the OA-5 input came from the cache (report `:241-244`). A cold pack turn cost about 16 times a warm one: USD 0.079 against USD 0.0048 (`:102-103`). So the tool list stays byte-stable within a chat:
- Sticky packs keep their first-load order. Pack tools always come after the core tools.
- A load at turn start changes the tools and the persona in one request. That request pays one miss.
- A load in the middle of a turn makes the next request miss. The next turn moves its rules into the persona, and that costs one more miss.
- A turn keeps one tier (`acb_skills/tier_policy.py:24-29`), so a load never changes the model.
- On an Anthropic tier, the cache mark sits on the last tool (`llm_caching_memory.md:33`). DeepSeek caches a prefix with no mark (`:46`).

**Native sessions.** `MAF_NATIVE_SESSIONS` is off by default (`acb_common/settings.py:1299`). With it on, a session stores messages and tool calls. It never stores the persona or the per-turn context (`maf_coding_engine.md:4752-4757`). A load changes neither the digest nor the fingerprint (`:4724-4737`, `:4758-4764`), so a load drops no session. A sticky pack keeps the tool of each stored call on offer.

An eviction is the one case where a stored call names a tool that the run no longer offers. OA-7c must probe that case.

**Where packs act.** Packs act only in the stream run of the orchestrator, in the own chat of the member, with a verified member. These runs keep delegation:
- a shared room
- the WhatsApp batch path (D115)
- a delegated run

The tools of OA-4 have the same scope (the "As built, OA-4" notes at the top of this spec).

#### 4.2.3 The guards that move with the tools

**The rule.** A pack tool runs as its owner would run it in a sub-run of this chat. Three parts make the rule hold.

1. **The held answer, once per run.** At run start, the loader computes the held answer for each pack. It is `_run_no_egress(owner, run_ctx)` (`orchestrator/_tool_injection.py:417-429`), or a known mark in Redis (`acb_skills/agent_tools.py:220-227`). It uses the server-side org and thread of the run. A delegation to the owner would bind the same answer now (`orchestrator/executor.py:2085-2087`). For email it is true when the chat read mail before this run (`orchestrator/_tool_injection.py:411-413`). A Redis fault, and a run with no tenant or no session, are held too. The first email call of a clean turn marks the chat. Each later call of that turn runs held (STRICT, confirmed by the owner on 2026-10-27). So the first call of a clean chat is open, and each later call and turn is held.
2. **The scope of the owner, per call.** Each pack call runs in `artifact_context_scope()` with `agent_name=<owner>` and `no_egress=<held>`. This is the idiom of `_delegate_held` (`acb_skills/agent_tools.py:230-243`). The scope is the outermost wrapper, and the permission gate sits inside it.
3. **The held set, at load.** A held pack drops each tool that `_is_egress` names (`orchestrator/_tool_injection.py:432-447`). That is the held set of the owner itself (`tests/unit/test_no_egress_tool_audit.py:61-89`).

In this table, `_tool_injection.py` and `executor.py` are in `orchestrator/`. `egress.py` is in `acb_skills/`. `agents.py` is `agent-email-assistant/agents.py`.

| Guard | Where it lives now | How it holds in a pack call |
|---|---|---|
| The held run (H-236, EM-G11d) | `_run_no_egress` (`_tool_injection.py:417-429`), read by `no_egress_for_this_run` (`egress.py:1110-1126`) | The scope binds the answer of the owner. `_egress_held()` reads it unchanged (`agents.py:81-92`) |
| The held card (§21) | `_held_card_mode()` (`agents.py:95-106`), `HELD_CARD_SEND_TOOLS` (`egress.py:991-1024`) | The same function gives the same answer. The gateway builds and records the card as today |
| The card-bound pin (H-374) | `is_card_bound_send` (`egress.py:1059-1077`) | The wrapper keeps `__wrapped__` and `__tool_risk__`. The name, the mark and the parameters still match |
| The hold note | `EgressGuardProvider` (`egress.py:1162-1171`) | A held pack adds the same note to its rules |
| The held set | `_withhold_egress_from_agent` (`_tool_injection.py:477-508`) | The loader drops the same tools from a held pack |
| The session mark (EM-G11f3) | Before an email sub-run (`egress.py:306-326`, `executor.py:2088-2092`) | The first email call of a run marks the session before the tool body runs |
| The untrusted-answer card | The gate (`_tool_injection.py:1060-1071`) | No change for the tools of the orchestrator. A pack call skips the card, because the scope names the owner (`_tool_injection.py:1168-1183`). No act shows two cards |
| Store writes in a mail run | The gate (`_tool_injection.py:1053-1055`), `run_holds_mail` (`egress.py:407-429`) | After the mark, each store write refuses. The memory extraction after the run skips too |
| The frame on stranger text | Each email tool (`agents.py:39-42`, `_frame_email` at `:1150`) | It is in the tools, so it moves with them |
| No MCP server beside mail | `_inject_mcp_servers` skips an agent that reads stranger text (`_tool_injection.py:2907`) | A run with the `email` pack gets no registry MCP server. Packs load before the MCP step (`executor.py:5636`) |
| Delegations from a mail chat | `_delegated_no_egress` (`_tool_injection.py:388-414`) | After the mark, each run that this run starts is held. That includes each child of OA-9 |
| Confirmation cards | `request_confirmation`, the one card seam (`agents.py:138-198`) | The card is an event of the stream of the orchestrator. That is one hop less than today (`navigation_shell.md:1237-1248`) |
| Profile writes close after a read | Reads call `close_writes()` (`acb_skills/memory_tools.py:224`, `executor.py:2525-2528`) | The wrapper calls it after each pack call |
| Narrowing | `narrow_tool_for(AGENT_NAME, ...)` (`acb_skills/narrowing.py:1276-1286`) | The tool keeps the name of the owner. `NARROWING_AGENTS=email-assistant` keeps its meaning |
| CRM Mirror mode | `_drop_mirror_record_writes` (`_tool_injection.py:1575-1611`) | The CRM pack builder runs the same drop (OA-8a) |
| The CRM number check | `AnswerCheckProvider` (`acb_skills/answer_check.py:10-14`) | The orchestrator already holds CRM tools. In a pack, the check reads raw CRM results, not the relay of a model (`:23-27`) |

**The email working set.** The wrapper writes the working set after each email call. It uses `_email_working_set_for` and `_record_email_working_set` (`orchestrator/executor.py:1872-1979`). The OA-5 fixture does the same (`evals/one_assistant_measure/pack.py:147-179`).

**What does not change.** `UNTRUSTED_INPUT_AGENTS` stays `{"email-assistant"}` (`acb_skills/egress.py:162`). If the orchestrator joined that set, each run of the orchestrator would be held. No guard function changes (§8).

**The floor in a mail chat (owner answer 1).** The own run of the email assistant holds no `web_search` and no `fetch_page` (`agent-email-assistant/config.json`, `floor_opt_out`). A run with the email pack KEEPS both tools, behind the untrusted-answer card. The orchestrator works that way today after an email answer (`orchestrator/_tool_injection.py:1060-1071`).

**The pin test.** `tests/unit/test_egress_card_bound_pin.py` passes unchanged. Packs never enter `build_agents()` or `_inject_agent_tools`, which `_run_tools` calls (`tests/unit/test_no_egress_tool_audit.py:160-168`). The claim at `test_egress_card_bound_pin.py:166-178` is that only the email agent holds a held-card tool. With packs, the run of the orchestrator holds the same function objects, in the scope of the owner. A new fence pins that (OA-7b).

#### 4.2.4 The tier rule

- A pack turn runs on the tier policy of the orchestrator. Its default is `tier-balanced` (`orchestrator/agents.py:884`).
- The turn kind can raise the tier. "A reply or a document that needs judgement" counts as `analysis`, which runs on `tier-powerful` (`acb_skills/tier_policy.py:162-170`, `:106-111`).
- Each pack declares a `tier_floor`. The email pack sets none in OA-7, for three reasons:
  - OA-5 ran email work on the tier of the orchestrator with the same accuracy (report `:64-67`, `:85`).
  - Both tiers served one model then (`:245-247`), so a floor changes no model today.
  - Email packs are sticky. A floor would put each later turn of the chat on the top tier, also a turn about another app.
- Delegations keep their rule. `_sub_agent_tier` still floors each delegated run at its own default (`orchestrator/executor.py:8743-8764`).

**`AI_REASONING_OFF_AGENTS=orchestrator`.** A pack turn is a turn of the orchestrator. So an Auto turn of kind `chat` runs with thinking off (`acb_skills/tier_policy.py:285-304`). A turn under 12 words counts as `chat` (`:506-509`). So "reply to Anil, we pay Friday" can write a draft on `tier-balanced` with thinking off. Today the email sub-run writes it on `tier-powerful` with thinking on.

OA-5 did not measure this state, because tier routing was off in the eval (report `:76-79`). OA-7h runs the measure again with the tier flags of production. If a reply check falls below delegation, set `keeps_reasoning` on the `email` pack. Then `RunTierPolicy.reasoning_effort()` (`acb_skills/tier_policy.py:666-675`) returns None in a run that holds that pack.

#### 4.2.5 The words of the member and the chat constraints

- **OA-1 (`acb_skills/handoff.py`).** A pack turn delegates nothing, so `head_task` heads nothing. The orchestrator reads the words of the member in its own history. `head_task` still heads each delegation that stays (`acb_skills/handoff.py:224-243`): unpacked apps, background calls and the children of OA-9.
- **The reply rules of OA-2 (`acb_skills/handoff.py:61-72`).** In a pack turn, the orchestrator reads each source itself. So the pack rules carry the first-hand form (`evals/one_assistant_measure/pack.py:83-85`). "A file name is a label, not content. Say a fact that you did not read as a guess." OA-5 showed that the shape does not fix cause C2 (report `:188-209`). OA-3 fixes it.
- **OA-4 (`acb_skills/chat_constraints.py`).** The kept corrections already show in the per-turn context of the orchestrator (`orchestrator/executor.py:9213-9219`). The two correction tools already belong to the orchestrator (`orchestrator/agents.py:1002-1007`). Nothing changes.
- **The tier question.** It still reads only the task (`orchestrator/executor.py:8862-8865`).

#### 4.2.6 The rule against an unasked draft (OA-7g)

**Status: shipped live ahead of OA-7, owner 2026-10-26.** It ships as its own small PR, with no flag (owner answer 4).

In OA-5 the member only corrected a date. Both shapes then made a reply card and saved a draft (report `:176-186`). Both shapes failed, so the rule goes in two places, and both are always on:
1. **The persona.** It is a new rule 12 of the orchestrator persona, after `orchestrator/agents.py:171`. It acts in both shapes.
2. **The task head.** It is one more line of `handoff._RULES` (`acb_skills/handoff.py:61-72`). It acts in each delegated task.

The text: "A correction is not a request. When the member only corrects a fact, say what changed. Do not draft, save, send or change anything until the member asks."

OA-5 §7 named OA-4 as the home of the rule (report `:324-326`). The owner chose to ship it live instead. The acceptance stays in OA-7h: the OA-5 check `no_write` passes on `reply_correction` turn 1, 3 of 3, on both arms.

#### 4.2.7 The flag

- **`TOOL_PACKS`** is a comma list of pack slugs, or `*`. Empty means off.
- **`TOOL_PACKS_ORGS`** lists the organization ids that may use packs, or `*`. Empty allows none.
- **The one reader** is `acb_skills.tool_packs.packs_on(org)`. It fails closed.
- **The gate** is OWNER-GATE in production, and `work_plan.md` §6 D4 names it. The flag moves the guards and the spend.

**Off leaves these byte-identical:**
- the tool names of the orchestrator, their order and each specialist tool
- the persona, with rule 12 of OA-7g
- the body of the turn-kind request
- the per-turn context
- each task head

With the flag off, the loader reads and writes no pack state. The fence is `tests/unit/test_tool_packs_off.py`, in the pattern of the off test of OA-4.

#### 4.2.8 The OA-7 slices

Each slice merges alone, in this order.

| Slice | What | Risk |
|---|---|---|
| OA-7g | The rule against an unasked draft. Shipped live ahead of OA-7, owner 2026-10-26 (PR #1356, `344cad4fc`, fence `tests/unit/test_no_unasked_draft.py`) | LOW |
| OA-7a | The pack key in WS-23, the email split, the rules, the flag reader. BUILT, dark, 2026-10-26 | LOW |
| OA-7b | The guarded pack builder, with no live caller. BUILT, dark, 2026-10-26 | HIGH |
| OA-7c | Layer (a) and sticky state: packs in live runs. BUILT, dark, 2026-10-27 | HIGH |
| OA-7d | The structured `surface` field from the dock | LOW |
| OA-7e | Layer (b): `load_skill_tools`. BUILT, dark, 2026-10-27 | MEDIUM |
| OA-7f | Layer (c): pack items in the turn-kind request | MEDIUM |
| OA-7h | The email measure on real code, with the tier flags of production | LOW (eval only) |

**OA-7a. The registry and the flag.**
- **Files.**
  - `acb_skills/skill_families.py`: the `owner` key, and the families `email` and `email-setup`
  - `acb_skills/tool_packs.py` (new): the rules, references, words, `tier_floor`, `keeps_reasoning` and `packs_on`
  - `acb_common/settings.py`: the two settings
  - `tests/unit/test_skills_registry.py`
- **Acceptance.**
  - The two email families together equal `own_tool_scope`, and no tool is in both.
  - `build_catalog` prices each pack from the tools of its owner.
  - No caller reads the flag yet.
- **Fence.**
  - `test_skills_registry.py` gains the owner universe.
  - The new `tests/unit/test_tool_packs_registry.py` fails on a pack with more than 35 tools.

**OA-7b. The guarded builder.**
- **Files.**
  - `orchestrator/_tool_injection.py`: `_pack_tools(slug, run_ctx)`
  - `acb_skills/tool_packs.py`: the scope helper
- **Acceptance.**
  - The builder loads the owner module once for each process.
  - It puts the gate inside and the scope of the owner outside.
  - It drops the held set, and it marks the session before the first email call.
  - It writes the working set, and it closes profile writes.
  - It skips a name that the run already holds.
  - Each held-chat and card test passes with the email tools from the builder.
  - `test_egress_card_bound_pin.py` passes unchanged.
- **Each test runs twice.** `load_email_agent` in `tests/unit/_held_card_fakes.py` gains a pack mode. These tests then run once in each mode: `test_held_send_*.py`, `test_email_chat_reply.py`, `test_held_send_guidance.py` and `test_one_assistant_email_cards.py`.
- **Fence.** The new `tests/unit/test_pack_guards.py`. Each case names its mutation:
  - Open and held pack runs equal the open and held runs of the owner (`EXPECTED_WITHHELD`, `SECTION_21_WITHHELD`).
  - Each held-card tool in the pack is the own function of the email module.
  - The mark comes before the tool body.
  - A pack write asks its own card, and never the untrusted card too.
  - After the mark, the own `fetch_page` of the orchestrator asks the untrusted card.
  - After the mark, each store write refuses.
  - A run with the `email` pack gets no MCP server.
  - Each reader of `agent_name` in `egress.py` and `_tool_injection.py` reads the owner inside a pack call.

**OA-7c. Layer (a) and sticky state.**
- **Conditions from OA-7b (fix round 1, 2026-10-27).**
  - Never keep a `PackBuild` across runs. Build each pack again at the start of each run.
  - Build the packs after the run binds its tenant. A build with no tenant is held, and it holds no send.
  - Key the run check of each pack call on a value that the server mints. Today it is `run_id`, and a client can set it (`gateway/routes/agent.py`, `req.run_id`).
  - Two pack calls at the same time can each read the working set before the other writes it. So one update can be lost.
  - The event "Now working on <mailbox>" goes to the run queue only. On a run with a relay and no queue, the event is lost.
  - The hold note of a held pack must name no other assistant, as `EGRESS_HELD_PACK_ANSWER` does.
- **Files.**
  - `orchestrator/executor.py`: the choice after `:5470`, and the apply after `:5596` and before `:5603`
  - `acb_skills/tool_packs.py`: the Redis state, the persona swap and the rules append
  - `orchestrator/agents.py`: the email bullets become one constant, so that the swap is exact
- **Acceptance.**
  - With the flag on, an email turn holds the `email` pack and not the `email_assistant` tool.
  - A delegation to `email-assistant` from the chat run gets the pack refusal (`evals/one_assistant_measure/pack.py:47-50`). Since fix round 1, the refusal acts only while the run holds both `email` and `email-setup`.
  - With `email` alone, the hand-off stays open, and the rules show the routing line for a rule, a setting, the knowledge and the writing style.
  - Rooms, WhatsApp and delegated runs see no pack.
  - The persona holds the rules in load order.
  - With native sessions on, a turn after an eviction does not fail on its stored calls.
- **Fence.**
  - `tests/unit/test_tool_packs_choice.py`: each input of layer (a), sticky on use, the decay, the cap and the order
  - `tests/unit/test_tool_packs_off.py`

**OA-7d. The surface field.**
- **Files.**
  - `cp/lib/shell/AssistantDock.tsx` and `cp/hooks/useAgentChat.ts`: send `surface`, the app slug of `here.href`
  - `cp/app/api/agent/chat/route.ts:668`: pass it on
  - the payload read in the executor
- **Acceptance.**
  - A turn from `/email` loads the `email` pack with no email words.
  - A forged `surface` only adds a pack that the flag allows. It changes no guard.
- **Fence.** A Vitest case on the request body, and a unit case on the server read.

**OA-7e. `load_skill_tools`.**
- **Files.**
  - `acb_skills/tool_packs.py`: the tool and its `FunctionMiddleware`
  - `orchestrator/_tool_injection.py`: the provider on the view of the run
- **Acceptance.**
  - A scripted run calls `load_skill_tools("email")` and then `compose_reply`. The second call runs with the held answer of the pack.
  - A load past the cap refuses, and its answer names `spawn_subagents`.
- **Fence.**
  - `tests/unit/test_load_skill_tools.py` runs on `tests/unit/_native_maf_harness.py`.
  - A pin fails when MAF removes or renames `add_tools`.

**OA-7f. Pack items in the turn-kind request.**
- **Files.**
  - `acb_skills/tier_policy.py`: `turn_kind` accepts optional pack items
  - `orchestrator/executor.py`: the apply after `:5877`
- **Acceptance.**
  - A long email turn with no email words loads the pack with one System-1 request.
  - A short turn and a Max turn send no request.
  - With the flag off, the request body is byte-identical.
- **Fence.** `tests/unit/test_tier_policy.py` counts the requests and checks the items.

**OA-7g. The rule against an unasked draft.** Shipped live ahead of OA-7, owner 2026-10-26 (PR #1356, `344cad4fc`, fence `tests/unit/test_no_unasked_draft.py`) (§4.2.6).
- **Files.** `orchestrator/agents.py` (rule 12) and `acb_skills/handoff.py` (`_RULES`).
- **Acceptance.** OA-7h checks it: `no_write` passes on `reply_correction` turn 1, 3 of 3, on both arms.
- **Fence.** Unit tests pin both texts, in `tests/unit/test_handoff_words.py` and `tests/unit/test_orchestrator_one_source.py`.

**OA-7h. The email measure on real code.**
- **Files.**
  - `evals/one_assistant_measure/run.py`: the `pack` arm sets the flag, in place of `pack.install`
  - `evals/one_assistant_measure/checks.py`: the new checks `held_card` and `one_card`
- **Settings.** `EMAIL_HELD_CARD_SEND`, `AI_TIER_ROUTING=orchestrator` and `AI_REASONING_OFF_AGENTS=orchestrator` are on, as in production.
- **Acceptance.**
  - Over 18 turns and 3 runs, the pack arm passes at least as many turns as delegation.
  - From turn 2, each email chat sends through the held card.
  - No act shows two cards.
  - `no_write` passes on `reply_correction` turn 1, 3 of 3, on both arms (OA-7g).
- **The refusal against OA-5 (OA-7c fix round 1).**
  - The OA-5 pack arm held 58 email tools. It refused each hand-off to the email assistant with `pack.REFUSED`.
  - The live pack arm holds one email pack at a time beside the base of 55 tools. It refuses a hand-off only with both packs, so this measure sees no `REFUSED` text.
  - With `email` alone, a hand-off for a rule, a setting, the knowledge or the writing style runs. It costs one delegated run, as on the delegation arm.
  - So the check `_no_email_delegation` (`checks.py`) must allow such a hand-off. It must still fail a hand-off of mail work.
- **Fence.** `tests/unit/test_one_assistant_measure.py` runs the scripted arm with the real flag.

OA-7h is the gate before the flag turns on for any organization.

### 4.3 OA-8: every other app

#### 4.3.1 Each specialist: a pack or a sub-agent

The orchestrator offers each registry agent as a tool (`orchestrator/agents.py:731-853`). It skips `orchestrator` and `strategy` (`:753`). It skips each Copilot agent except `app-builder` (`:761`, `acb_skills/loader.py:123-137`). That leaves six specialists in the repo (`tests/unit/test_no_egress_tool_audit.py:98-104`). The database can add agents that users register (`routes/agent.py:868-923`).

| Agent | Today | Target |
|---|---|---|
| `email-assistant` | 59 own tools, on `tier-powerful` | The packs `email` and `email-setup` (OA-7) |
| `crm-assistant` | 21 own tools. The orchestrator already holds 13 of them (`agent-orchestrator/config.json`, `tool_scope`) | The pack `crm-records`: the other 8 tools, 4 reads and 4 writes (OA-8a) |
| `projects-assistant` | 113 own tools (`agent-projects/config.json`) | The core pack `projects`, with about 27 tools. More packs of 35 tools or fewer: planning, structure and bulk acts (OA-8b) |
| `whatsapp-assistant` | 14 own tools. It writes drafts only | The pack `whatsapp` (OA-8c) |
| `apis-config` | No own tools | A rules-only pack `integrations`, if the measure supports it (OA-8d). `install_dependency` stays out (D85) |
| `app-builder` | Copilot runtime, with a workspace for each session (`routes/agent.py:679-696`) | Stays a sub-agent. It is a heavy job on its own runtime |
| `task-manager` | The My Tasks rail only. It is not a specialist (`routes/agent.py:630-635`) | No change |
| `projects-assistant` in a covered org | Sandbox tools attach to its own run only (`agent-projects/agents.py:293-303`) | Stays a sub-agent. A pack never carries sandbox tools |
| Agents that users register | Specialists from the database | Stay sub-agents. They are external, and a held run gets none of their tools (`acb_skills/egress.py:29-31`) |
| Scheduled jobs, workflows, triage, `call_agent_background` | Not conversational | No change. OA-10 keeps them |

**The order.** CRM comes first: it is the smallest, and its reads are already loaded. Projects, WhatsApp and integrations follow. Each app reuses the builder of OA-7b, so no app adds a guard path.

#### 4.3.2 The OA-8 slices

| Slice | What | Risk |
|---|---|---|
| OA-8a | The `crm-records` pack | MEDIUM |
| OA-8b | The Projects packs | MEDIUM |
| OA-8c | The `whatsapp` pack | MEDIUM |
| OA-8d | The `integrations` rules pack (optional) | LOW |
| OA-8e | The measure before OA-10 | LOW (eval only) |

**OA-8a. CRM.**
- **Files.**
  - `acb_skills/skill_families.py`: the family `crm-records`, with the owner `crm-assistant`
  - `acb_skills/tool_packs.py`: the rules, from `agent-crm/instructions.md`
  - `orchestrator/agents.py`: the `crm_assistant` tool leaves when the pack is on
- **Acceptance.**
  - Each write asks its card in the stream of the orchestrator (`agent-crm/agents.py:653-660`).
  - Mirror mode drops the 3 record writes (`orchestrator/_tool_injection.py:1575-1577`).
  - A held pack drops the 4 writes, as the held run of the CRM agent does (`tests/unit/test_no_egress_tool_audit.py:77-82`).
  - A pack read arms the number check.
- **Fence.** CRM cases in `tests/unit/test_pack_guards.py`.

**OA-8b. Projects.**
- **Files.** `acb_skills/skill_families.py`, `acb_skills/tool_packs.py` and `tests/unit/test_projects_agent.py`.
- **Acceptance.**
  - The Projects families together equal `skill_projects.__all__`.
  - Each pack tool keeps the strict-argument wrapper (`agent-projects/agents.py:184-205`).
  - In a covered org, no Projects pack loads, and the work goes to the specialist.
  - A batch tool shows one card with its counts.
- **Fence.**
  - F7 of `test_projects_agent.py`
  - the limit of 35 tools in `tests/unit/test_tool_packs_registry.py`
  - the covered-org case in `tests/unit/test_pack_guards.py`

**OA-8c. WhatsApp.**
- **Acceptance.**
  - The held set of the pack equals the held set of the owner (`tests/unit/test_no_egress_tool_audit.py:86-89`).
  - `narrow_and_read` keeps the name of the owner.
  - The agent stays outside `UNTRUSTED_INPUT_AGENTS`.
- **Fence.** Parity cases in `tests/unit/test_pack_guards.py`.

**OA-8d. Integrations.** It is a rules-only pack, and it is optional. Build it only if OA-8e shows that its turns lose nothing.

#### 4.3.3 The measure before OA-10 (OA-8e)

Extend `evals/one_assistant_measure`. Do not build a second harness (§7 item 2).

**Arms.** All three arms run with the tier flags of production:
- `delegation`, as today
- `packs`, with OA-7 and OA-8 on
- `packs+spawn`, with OA-9 on too

**New scenario sets.**

| Set | Chats | What it covers |
|---|---|---|
| `projects` | 6 | Read, find, create, assign and report |
| `crm` | 4 | The pipeline, a record, a logged call, a moved deal |
| `mixed` | 3 | "Make a task from this email" and "log this email on the deal" |
| `general` | 4 | Company questions, memory and the web, with the email pack sticky. Do 83 to 110 tools hurt a turn that needs none of them? |
| `miss` | 3 | A request for an app, with no surface and no listed word. It must load through `load_skill_tools` |
| `fanout` | 3 | Five companies, each researched with a page read. Five tasks from a list. Ten invoices totalled (§6) |

**New checks.**
- `right_pack`: a tool of the right pack ran.
- `no_delegation`: no specialist ran for a packed app.
- `layer`: the layer that chose each pack.
- `miss_round`: one counted round of layer (b).
- `tools_per_call`: at or under the cap.
- `held_card` and `one_card`, as in OA-7h.
- `spawned` and `not_spawned` (§6.3).
- `items_contract`: the output of each child parses (§6.4).

**Numbers to report.**
- the pass rate
- the first text and the total time, as the median and the 90th percentile
- the model calls per turn
- the input tokens and the cache share
- the cost per turn
- the milliseconds of the loader for each layer
- the cache misses that a load caused
- the tools per request
- the wall time of each fan-out

**What stops OA-10.** Any one of these results stops OA-10:
1. A guard check fails on any arm, in any run.
2. On app turns that are not email, `packs` passes more than 2 points below `delegation`.
3. On `general` turns, the median first text is more than 15 percent slower than with no packs.
4. On `general` turns, the pass rate falls.
5. Layer (b) runs on more than 15 percent of app turns.
6. An unused pack loads on more than 20 percent of turns.
7. The warm cost of a turn that is not email is higher than with delegation.
8. A request carries more tools than the cap allows.
9. `packs+spawn` is slower than `packs` on the chat of multi-step research.
10. The main agent spawns on more than 1 in 9 `not_spawned` turns.

#### 4.3.4 OA-10: retire conversational delegation

OA-10 starts only after OA-8e passes, with OA-8 and OA-9 done. It does four things:
1. It makes packs the default for conversational work. The owner names the organizations and the order.
2. It removes the specialist tools and the persona bullets of each packed app.
3. It moves the Email and Projects rails to the one assistant, with their pack pinned (owner answer 2).
4. It keeps specialists for scheduled jobs, workflows, external agents and covered orgs (D117 item 7).

**The rails until OA-10.** The Email rail and the Projects rail run their specialist directly (`cp/app/email/components/EmailAssistantChat.tsx:5-14`, `cp/app/projects/components/AssistantRail.tsx:7-15`). They keep that specialist until OA-10. At OA-10, a new chat in a rail goes to the one assistant, and the surface of the rail pins its pack. An old rail chat keeps its agent, as an old chat in the dock does (`navigation_shell.md:1224-1226`).

### 4.5 Audit of 2026-10-26: gates, commands and narrowings

A spec auditor checked this design against the code at `1e43fc5c0`. Every
slice below is AGENT-SAFE to build. Each one ships dark or has no live
caller. No flag flip is cleared. `TOOL_PACKS`, `TOOL_PACKS_ORGS` and
`SPAWN_SUBAGENTS_ORGS` are owner-only (`work_plan.md` §6 D4). The §3a
window does not open them, because they move the guards and spend model
tokens. OA-7g shipped live (PR #1356, `344cad4fc`).

**OA-7a. GO, narrowed.**
- Add `apps/services/gateway/gateway/routes/integrations_skills.py`. It
  builds `tools_by_name` for `build_catalog` from injected tools only, so a
  pack has no price without this edit.
- Say how a pack family acts in the skill index. With `SKILLS_INDEX_ONLY`
  on, `skill_index._indexed_families` lists every family, and
  `test_skill_index.py` expects that.
- Put `narrow_and_read` in one family. It is in `own_tool_scope` (59 names)
  and not in `_TOOLS` (58).
- The Skills catalog shows two new toggles at merge. They do nothing until
  OA-7c. Say so in the toggle text.
- Update the WS-23 board row and `skills_registry.md` lines 116 to 122. Both
  still say that own tools join only through the WS-8 manifest.
- Verify: `uv run pytest tests/unit/test_skills_registry.py
  tests/unit/test_tool_packs_registry.py tests/unit/test_skill_index.py
  tests/unit/test_chat_upload_every_agent.py
  tests/unit/test_one_assistant_measure.py -q`.

**OA-7b. GO, narrowed.**
- The "each test runs twice" list was wrong. `load_email_agent` has 13
  callers. `test_email_chat_reply.py` uses its own loader. Three named
  groups never call it. Run the 13 callers in both modes, and the reply
  suite through its own loader.
- The `agent_name` fence must also read `pending_ask.py` (the card row),
  `write_artifact.py`, `system_one.py` and `whatsapp_acts.py`.
- `EgressGuardProvider` adds two middlewares, `WithholdEgressTools` and
  `RefuseEgressTools`. A pack run gets neither. The drop at load is the only
  held-set control in a pack run, and a test must say so.
- The builder calls `_is_egress`, `_run_no_egress`, `_gate_injected_tool`
  and `artifact_context_scope`. It copies none of their bodies. A fence
  patches `_is_egress` and checks that the pack drop follows it.
- Verify: `bash scripts/dev_db.sh`, then `uv run pytest
  tests/unit/test_pack_guards.py tests/unit/test_egress_card_bound_pin.py
  tests/unit/test_no_egress_tool_audit.py
  tests/unit/test_delegation_no_egress.py tests/unit/test_email_chat_reply.py
  tests/unit/test_held_*.py -q`.

**OA-7c. GO, narrowed. Start after OA-7a, OA-7b and OA-7g.**
- Layer (a) takes three inputs here: sticky packs, references and words.
  The surface input waits for OA-7d.
- Name the file that refuses a delegation to `email-assistant`:
  `agent_tools.call_agent`, the streaming seam, or the specialist tool.
  The refusal acts only while the run holds every pack of that owner. As
  built: the streaming seam refuses, and `call_agent` asks the same rule
  first. With `email` alone the hand-off stays open (fix round 1, "As
  built, OA-7c").
- The apply point puts pack tools in the list before `_inject_agent_tools`.
  Then `_gate_own_maf_tools` wraps each tool with no `__cc_gated__` mark. In
  a marked chat that could show two cards. Add a case: a pack send in a
  marked chat, built through the real assembly, shows one card.
- Say whether pack names join `run_tools`. OA-9c depends on the answer.
- The sticky key keeps the member hash, as the email working set key does.
- Verify: `uv run pytest tests/unit/test_tool_packs_choice.py
  tests/unit/test_tool_packs_off.py tests/unit/test_chat_constraints.py
  tests/unit/test_handoff_words.py tests/unit/test_orchestrator_one_source.py
  tests/unit/test_native_session_persistence.py -q`.

**OA-9a. GO for the parser only.** Build `subagent_contract.py` and
`tests/unit/test_subagent_contract.py`. The `worker` half is NO-GO until
these edits land in this spec:
1. Move the OA-9a fence case from `test_tool_packs_off.py` to
   `test_subagent_contract.py`.
2. Name the open and held tool sets of the worker. A `worker` entry turns
   `test_no_egress_tool_audit.py` red until they are pinned.
3. Say if the no-job refusal happens at build or at run. A raise at build
   turns `test_egress_card_bound_pin.py` red.
4. Name the worker `config.json`: `tool_scope`, `floor_opt_out`, `sharing`
   and the tier. Without opt-outs, the worker gets the whole core floor.
5. Say how the picker hides the worker. `_AGENT_REGISTRY` has no internal
   key.

**OA-9b. NO-GO** until the worker half is cleared and these are fixed:
1. `emit_generative_ui` stays in every run, and it is not read-only. Name
   the mechanism that gives a child read tools only. The seam is
   `tool_guard.WithholdTools` and `RefuseTools`.
2. Name how a child stops at 4 model rounds.
3. A job that names `packs` is refused until OA-9c.
4. `spawn_subagents` goes to the main assistant only, not to every agent.
5. The batch-write acceptance line waits for OA-8b. Today the main assistant
   holds no batch write tool.
6. Keep these tests green: `test_delegation_no_egress.py`,
   `test_read_tool_paging.py`, `test_chat_hardening.py`,
   `test_core_tool_floor.py`, `test_floor_opt_out.py`,
   `test_copilot_specialist_refused.py`, `test_background_ai_member.py` and
   `test_tool_schema_diet.py`.

**Build order.** The slices share files: `settings.py`, `_tool_injection.py`
and `orchestrator/agents.py`. Build OA-7a, then OA-7b, then OA-7c. OA-9a
(the parser) can build beside OA-7a.

**OA-7e facts.** `FunctionInvocationContext.add_tools` raises `ValueError`
for a different object with a duplicate name, and `RuntimeError` when the
context has no live tool list. The decorator emits `ExperimentalWarning`.

## 5. Acceptance

- **OA-1.** A recorded replay of session `de64d903` turn 3 sends a task that
  holds the member's note word for word, and holds no "CB6".
  Fence: a trajectory test in `evals/trajectories/`.
- **OA-2.** In the same replay, the answer does not name CB6 or CB7 as the
  parts. A unit test checks the reply format of the email assistant: an
  inferred item carries its mark.
- **OA-3.** A scanned PDF fixture gives its text. A 200-page scan stops at
  the cap with a plain sentence. A malformed file gives the existing
  refusal, never a crash. The OCR text is marked as OCR.
- **OA-4.** A correction in turn N shows in the per-turn context of turn
  N+1 and N+5. Its removal by the member takes it out.
- **OA-5.** A report in `docs/` with the numbers for both shapes: the pass
  rate of the email trajectory suite, the Navy replay, the live-model sweep,
  `agent.run_timing` (first text and total) and `usage_event` cost.
- **OA-7a.** The two email families together equal `own_tool_scope`, and no tool is in both. `build_catalog` prices each pack from the tools of its owner. Fences: `tests/unit/test_skills_registry.py`, with the owner universe, and `tests/unit/test_tool_packs_registry.py`.
- **OA-7b.** Every held-chat and card test passes with the email tools from the pack builder. The egress pin test (`test_egress_card_bound_pin.py`) passes unchanged. The open and held sets of a pack equal those of its owner. A pack write asks its own card, and never the untrusted card too. Fence: `tests/unit/test_pack_guards.py`.
- **OA-7c.** With the flag on, an email turn holds the `email` pack and no `email_assistant` tool. Rooms, WhatsApp and delegated runs see no pack. With the flag off, the tool list, the persona, the turn-kind request and the per-turn context stay byte-identical. Fences: `tests/unit/test_tool_packs_choice.py` and `tests/unit/test_tool_packs_off.py`.
- **OA-7d.** A turn from `/email` loads the `email` pack with no email words. A forged `surface` adds only a pack that the flag allows.
- **OA-7e.** In one run, a scripted model loads the `email` pack and then calls `compose_reply` with the held answer of the pack. A load past the cap refuses. Fence: `tests/unit/test_load_skill_tools.py`.
- **OA-7f.** A long email turn with no email words loads the pack with one System-1 request. A short turn and a Max turn send none. Fence: `tests/unit/test_tier_policy.py`.
- **OA-7g.** Shipped live ahead of OA-7, owner 2026-10-26 (PR #1356, `344cad4fc`, fence `tests/unit/test_no_unasked_draft.py`). The orchestrator persona and `handoff._RULES` carry the rule against an unasked draft, both always on. OA-7h checks it: `no_write` passes on `reply_correction` turn 1, 3 of 3, on both arms.
- **OA-7h.** On the 18 email turns, 3 runs each, with the tier flags of production, the pack arm passes at least as many turns as delegation. From turn 2, each email chat sends through the held card. No act shows two cards.
- **OA-8a.** Each CRM write asks its card in the stream of the orchestrator. Mirror mode drops the 3 record writes. A held pack drops the 4 writes. Fence: `tests/unit/test_pack_guards.py`.
- **OA-8b.** The Projects families together equal `skill_projects.__all__`, and each holds 35 tools or fewer. In a covered org, no Projects pack loads. Fences: `tests/unit/test_projects_agent.py` and `tests/unit/test_pack_guards.py`.
- **OA-8c.** The held set of the `whatsapp` pack equals the held set of its owner. Fence: `tests/unit/test_pack_guards.py`.
- **OA-8d.** Optional. OA-8e shows that the turns of the `integrations` pack lose nothing.
- **OA-8e.** A report in `docs/` gives the numbers of §4.3.3 for the three arms. None of the stop results of §4.3.3 occurs. OA-10 starts only then.
- **OA-9a.** The contract parser accepts good items, and it turns other output into one `inferred` item. The `worker` refuses with no job, and the picker and the specialist list do not show it. Fence: `tests/unit/test_subagent_contract.py`.
- **OA-9b.** Five jobs run at once, and a sixth gets a name and does not run. Stop during a fan-out of 3 cancels all 3. A child holds only read tools, no tool that its parent lacks, and it is held in a marked chat. Fence: `tests/unit/test_spawn_subagents.py`.
- **OA-9c.** A child with the `email` pack gets the same held or open set as the main assistant. Fence: `tests/unit/test_pack_guards.py`.
- **OA-9d.** Three children that write show one card with three rows. A rejected row writes nothing, and Stop writes nothing. Fence: a test in the style of `tests/unit/test_delegated_confirmation_card.py`.
- **OA-9e.** The persona lines of §6.3 show with the flag on, and not with it off. In OA-8e, the main assistant spawns for multi-step research, and not for one read, one task or three searches.

## 6. Dynamic sub-agents and the sub-agent contract (OA-9)

D117 item 5 keeps dynamic sub-agents. The owner wrote on 2026-10-26: "dont loose the ability to spin up a dynamic sub agent". The main assistant does most work itself, with packs. It can also build a sub-agent for one job, from packs or a subset of tools. It can run several of them at once.

### 6.1 What exists, and what to reuse

| Part | Where | Reuse |
|---|---|---|
| The fan-out | `call_agents_parallel` (`acb_skills/agent_tools.py:280-435`). At most 5 tasks (`:52`). One task for each branch (`:425-429`). The tasks past the cap get a name in the answer (`:404-419`) | Yes. Move `_run_one` and the gather into one `_fan_out`, which both tools call |
| The guard for each branch | `_delegation_refusal` (`:59-76`), depth 2 (`:49`), `_delegate_held` (`:230-243`), `_answer_of` (`:256-277`) | Yes, with no change |
| The sub-run | `_run_sub_agent_streaming` (`orchestrator/executor.py:2012-2530`) | Yes, with no change. It stays the one spawn seam |
| The batch fallback | `run_agent` with `head_task` (`acb_skills/agent_tools.py:191-207`) | Yes |
| Stop | A gather cancels its branches (`:427-429`). A background child registers for Stop (`:489-500`) | Yes |

**The gap.** `_run_sub_agent_streaming` runs a registry agent by its name (`orchestrator/executor.py:2048-2060`). A dynamic sub-agent has no fixed persona.

**The fix, with no second seam.** One internal agent, `worker`, is new in `apps/agents/agent-worker/`. The spawn tool binds a job spec on the frame of the parent. The `build_agents()` of the worker reads that spec. It builds the agent from the packs, the tools and the tier of the job, with the contract of §6.4. Each guard of a sub-run then applies, because the worker runs as a sub-run.

The registry marks the worker as internal, so it is never a specialist tool or a chat agent. A call of `call_agent("worker", ...)` refuses, because no job spec is bound.

### 6.2 The spawn tool

```
spawn_subagents(jobs: str) -> str
jobs: a JSON array of 1 to 5 jobs:
  {"job": "<the job, self-contained>",
   "packs": ["email"],                      # optional
   "tools": ["web_search", "fetch_page"],   # optional
   "tier": "fast" | "main"}                 # default "main"
```

- **The result.** The tool returns one block for each job. A block holds the items of §6.4, or the error text of the job.
- **The caps.**
  - 5 children run at once.
  - A turn makes at most 2 spawn calls, so a turn has at most 10 children.
  - A child makes at most 4 model rounds.
  - A job stops at 120 s. A fan-out stops at 180 s.
  - `_SUB_AGENT_TIMEOUT` stays at 900 s for `call_agent` (`acb_skills/agent_tools.py:56`).
  - A timeout gives an error item, never an exception.
- **No more tools than the parent.**
  - Each pack must pass `packs_on(org)`, and no toggle may disable it.
  - Each tool must be in the `run_tools` of the parent (`orchestrator/executor.py:5743-5745`).
  - A child gets no spawn tool and no delegation tool.
- **Stop.**
  - Stop cancels the tool call of the parent, and the gather then cancels each child.
  - The `finally` of each child releases its credentials and its context (`orchestrator/executor.py:2505-2530`).
- **The flag.** `SPAWN_SUBAGENTS_ORGS` is an org list. Empty means off.

### 6.3 When to spawn

With the flag on, the persona gains these lines:

"Call tools that do not depend on each other in one step. Spawn sub-agents only when each job needs several steps, or its reading would fill this chat. Never spawn for one tool call or a short sequence."

The orchestrator can already call several tools in one step (`orchestrator/agents.py:159`, rule 3). The table of §6.6 shows that one step beats a spawn for jobs of one call each.

**Fence.**
- A unit test pins the lines with the flag on, and their absence with the flag off.
- The `fanout` scenarios of OA-8e check `spawned` for multi-step research.
- They check `not_spawned` for one read, for one task and for three searches.

### 6.4 The contract

**Input.** The spawn tool writes each job text. Then `_run_sub_agent_streaming` adds the head (`orchestrator/executor.py:2043-2044`), as for each delegation. The child gets these parts, in this order:
1. the words of the member, verbatim (OA-1, `acb_skills/handoff.py:54`)
2. the reply rules (OA-2, `acb_skills/handoff.py:61-72`)
3. the kept corrections and limits (OA-4, `acb_skills/handoff.py:59`)
4. the ids of the working set (`acb_skills/email_chat_context.py:662`)
5. the job

The child gets no summary of the chat. Each child of one fan-out shares the same prefix, and the job comes last.

**Output.** The worker ends with one JSON object:

```
{"items": [{"value": ..., "kind": "quoted" | "inferred", "source": ...}]}
```

- A source is an email id, a file name with a page, a record id, a URL or a tool.
- `acb_skills/subagent_contract.py` (new) parses the object strictly, as `system_one.parse_answers` holds its shape (`acb_skills/system_one.py:282`).
- Output in any other shape becomes one item of kind `inferred`, with the source `unstructured`.
- The main assistant writes its answer from the items. It states each `inferred` item as a guess.

**The contract and `head_task`.** `head_task` does not change. Its "(quoted: ...)" marks are the prose form of `kind`. The persona of the worker asks for the JSON form.

### 6.5 Guards inside a child: read first, writes after

**The rule (owner answer 3).** Children read first. Writes come after, in two steps:
- OA-9b ships children that hold read tools only. A batch write goes through the batch tool of the app, in the main assistant.
- OA-9d comes later. It adds children that write, with one batch card for each fan-out.

**Held chats.** A child of a held or marked chat is held (`acb_skills/agent_tools.py:220-243`, `orchestrator/_tool_injection.py:388-414`). Its pack tools take the scope of the owner through the builder of OA-7b. So each guard holds in the child as in the main assistant.

**Web research in a mail chat.** A held child holds no `web_search` and no `fetch_page`. So parallel web research works only in a chat that read no mail. That is the rule for each delegation today.

**Cards.** Each write of a child asks the card of the member. The card shows the exact payload (`agent-crm/agents.py:699-720` gives the rule).

**Batch writes in OA-9b.** A child holds only tools that say `read_only=True`, plus `web_search` and `fetch_page`. The main assistant sends a batch write through the batch tool of the app. That tool already shows one card with the counts (`routes/agent.py:758-762`).

**Batch writes in OA-9d.** Children may write. D117 item 5 asks that their writes ask the member once, on one batch card:
- All cards of one fan-out join into ONE card, with one row for each act and its exact payload.
- The member approves or rejects each row.
- A row with no answer before its timeout counts as a rejection.
- Stop writes nothing.
- A card with no group stays byte-identical.

### 6.6 Tier, cost and speed

**Tier.**
- `fast` sends `tier-fast`. `main` sends the resolved tier of the parent (`acb_skills/agent_tools.py:79-91`).
- The default of the worker is `tier-fast`. So `_sub_agent_tier` keeps the choice of the job (`orchestrator/executor.py:8743-8764`).
- A child never runs above its parent.
- `AI_TIER_ROUTING` does not cover the worker. So it has no `RunTierPolicy`, and `reasoning_off` never acts on it (`acb_skills/tier_policy.py:666-675`).

**Cost.** A fan-out makes at most 20 child calls: 5 children, 4 rounds each. In OA-5, a warm call cost about USD 0.0017, and a cold call about USD 0.028. These come from report `:102-103`, divided by 2.85 calls. So a full fan-out costs about USD 0.03 warm. Cold, it costs up to about USD 0.56, and less with the smaller prompt of a child (estimates).

**The cache across children.** The children share the tools in pack order, the persona and the head. The job comes last. They start at the same time, so the first round of each child can miss. Later rounds hit their own prefix. OA-8e reports the cache share.

**Speed for 5 searches.** These numbers are estimates. They use the OA-5 time of 3.8 to 4.9 s for each model call (report `:118-121`). They take 1 to 2 s for each search.

| Path | Model calls on the critical path | Wall time |
|---|---|---|
| Main assistant, one search after another | 6 | 28 to 39 s |
| Main assistant, 5 searches in one step | 2 | 9 to 12 s |
| Spawn: each child searches, then gives items | 4: main 2, child 2 | 16 to 22 s |
| With a page read for each result, one call after another | 11 | 52 to 74 s |
| With a page read, in steps of 5 | 3 | 14 to 19 s, and 5 pages stay in each later call |
| With a page read, spawn | 5: main 2, child 3 | 21 to 29 s, and only the items stay |

- Against one call after another, a spawn cuts the wall time by about 40 to 60 percent.
- Calls in one step are faster still.
- The gain of a spawn is context: the pages stay in the children. That is the reason for the rule of §6.3.

### 6.7 The OA-9 slices

| Slice | What | Risk | Builds beside OA-7? |
|---|---|---|---|
| OA-9a | `subagent_contract.py`, and the internal `worker` with `tools` only | MEDIUM | Yes |
| OA-9b | `spawn_subagents`, read-only children, `_fan_out`, the caps, Stop, the flag | MEDIUM | Yes |
| OA-9c | Packs in children, through OA-7b | MEDIUM | After OA-7b |
| OA-9d | One batch card for each fan-out, and children that write | HIGH | After OA-9c |
| OA-9e | The persona lines and the `fanout` scenarios | LOW | With OA-9b |

**OA-9a.**
- **Files.**
  - `acb_skills/subagent_contract.py`
  - `apps/agents/agent-worker/`: `agents.py`, `config.json` and `instructions.md`
  - `routes/agent.py`: an internal entry
  - `orchestrator/agents.py:753`: skip the worker
- **Acceptance.**
  - The parser accepts good output, and it turns bad output into one `inferred` item.
  - The worker refuses when no job is bound.
  - The picker and the specialist list do not show the worker.
- **Fence.**
  - `tests/unit/test_subagent_contract.py`
  - a case in `tests/unit/test_tool_packs_off.py`

**OA-9b.**
- **Files.**
  - `acb_skills/agent_tools.py`: `spawn_subagents` and `_fan_out`
  - `orchestrator/_tool_injection.py`: inject the tool when the flag is on
  - `acb_common/settings.py`: the flag
- **Acceptance.**
  - Five jobs run at once. The tool names a sixth job, and does not run it.
  - A job past 120 s gives an error item.
  - Stop during a fan-out of 3 cancels all 3, and no child writes after the Stop.
  - A child holds only read tools, and no tool that its parent lacks.
  - A child of a marked chat is held.
  - A batch write goes through the batch tool of the app, in the main assistant.
- **Fence.**
  - `tests/unit/test_spawn_subagents.py`
  - The tests of `call_agents_parallel` that exist today stay green.

**OA-9c.**
- **Acceptance.** A child with `packs=["email"]` gets the held or open set that the main assistant would get.
- **Fence.** A child case in `tests/unit/test_pack_guards.py`.

**OA-9d.**
- **Files.** `acb_skills/ask_tools.py` (the card group) and the card UI.
- **Acceptance.**
  - Three children that write show one card with three rows.
  - A rejected row writes nothing.
  - Stop writes nothing.
- **Fence.** A test in the style of `tests/unit/test_delegated_confirmation_card.py`.

**OA-9e.** The persona lines and the OA-8e scenarios of §6.3.

## 7. Coordination with the chat-speed work

The chat-speed work (#1313, #1323) owns latency. These points touch it:

1. **The delegated path.** #1323 lists "a delegated agent still loads twice
   and streams no text" and "skip the orchestrator rewrite of a delegated
   answer" as next slices. OA-7 can remove delegation for email
   conversation. Build those slices for the paths that stay after OA-10:
   the sub-agent of OA-9, scheduled jobs and workflows.
2. **Fewer model rounds per email run.** #1323 says this needs evals first.
   OA-5's harness is those evals. Share it, and do not build a second one.
3. **The timing lines.** OA-5 reads `agent.run_timing` and
   `executor.run_setup_ms`. Keep their names and fields stable.
4. **OCR.** OA-3 adds time to `read_attachment`. It runs off the event
   loop with a cap, so it must not block other runs.
5. **OA-1 adds tokens.** A task now holds the member's words. Expect a
   small rise in the input tokens of a delegated run.

HANDOFF **H-412** carries these points to that session.

## 8. Out of scope

- An image-reading model for drawings. The owner chose OCR first.
- WhatsApp (D115).
- A change to the held-chat guard itself.
