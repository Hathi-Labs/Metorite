# One assistant: context that survives, and tool packs

**Status:** ✅ ACTIVE (2026-10-25). Board: **WS-67**. Decisions: **D116**, **D117**.
OA-1 and OA-2 are BUILT (PR #1341, 2026-10-25, "As built" below). OA-4 is
built, dark behind `CHAT_CONSTRAINTS` (2026-10-25, "As built" below). OA-5
is BUILT (2026-10-25): the report is `docs/one_assistant_measure_2026-10-25.md`.

OA-8a is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-8a" in §4.3.2). The flag flip is OWNER-GATE.

OA-8b is BUILT, dark behind `TOOL_PACKS` (2026-10-28, "As built, OA-8b" in §4.3.2). The flag flip is OWNER-GATE.

OA-8f is BUILT, dark behind `ORCHESTRATOR_FLOOR_TRIM` (2026-10-28, "As built, OA-8f" in §4.3.2). The count fixes and the pack cues need no flag. The flag flip is OWNER-GATE.

OA-8g is BUILT (2026-10-29, "As built, OA-8g" in §4.3.2). It repairs the pack rules, two load word lists and the spawn cue that OA-8e run 2 found. The Projects packs and the sub-agents stay dark. The CRM rule change rides with the CRM pack.

OA-8h is BUILT (2026-10-29, "As built, OA-8h" in §4.3.2). It repairs the Projects pack rules and two harness words that OA-8e run 3 found. The Projects packs stay dark.

OA-8i is BUILT (2026-10-30, "As built, OA-8i" in §4.3.2). In one turn it refuses a hand-off to an owner after a write tool of the owner's pack, and the reverse. Both directions hold only while the owner's specialist tool stays in the run, so today they bind the Projects owner only. It has no flag of its own and acts only in a run that `TOOL_PACKS` serves. The OA-8e report counts a refused hand-off on its own row.

OA-8e RAN LIVE and is NOT MET (2026-10-28, `docs/one_assistant_measure_oa8e_2026-10-28.md`, harness at `9912eee5a`). On the app turns that are not email, the pack arm passed 38 of 70 and delegation 52 of 70 (CRM 14 of 24 against 19 of 24, Projects 21 of 38 against 29 of 38). Stop results 1, 2 and 11 occur. The pack arm is 2.6 times faster per turn and 32 percent cheaper. Causes: the model loads a pack it lacks only 3 times in 70 turns and never in the miss chats, it skips the bulk pack for several new tasks, it hands off after it loaded the plan or structure pack, and on a Projects page the plan and structure packs do not fit beside the core pack. The run also shows harness defects to repair first: fixture gaps, checks that count a refused call, the `crm_report` annotation, and dev-database reach through the memory tools. The fan-out chats and the `packs+spawn` arm were skipped. OA-10 must not start. Phase 3 (2026-10-29) wires the fan-out chats and the `packs+spawn` arm in the harness (fixtures for the web tools, a worker plan, ten invoices, the `spawned`, `not_spawned` and `items_contract` checks, stop results 9 and 10). No second live run yet. Run 2 (2026-10-29, `docs/one_assistant_measure_oa8e_2026-10-29.md`, base trim on for both arms) is still NOT MET. Pass counts: packs 74 of 105 against delegation 70 of 105 on app turns (CRM 28 of 36 against 23, Projects 41 of 57 each). Stop results 1, 5 (the model loads a pack itself on 21 percent of app turns) and 11 (7 model-choice hand-offs) occur. The main assistant never spawned on the research chats (0 of 6), so sub-agents are not measured. OA-10 must not start.

OA-9b and the OA-9a worker half are BUILT, dark behind `SPAWN_SUBAGENTS_ORGS` (2026-10-28, "As built, OA-9b" in §4.5). The flag flip is OWNER-GATE.

OA-9b hardening is BUILT (2026-10-28, "As built, OA-9b hardening" in §4.5). It closes the seven faults of the Opus review. The flag flip stays OWNER-GATE.

OA-9c is BUILT, dark behind `SPAWN_SUBAGENTS_ORGS` (2026-10-29, "As built, OA-9c" in §4.5). A child can hold the read tools of a pack that the main assistant holds. No email tool says `read_only=True`, so an email pack gives a child no tool yet. The flag flip stays OWNER-GATE.

OA-9f is BUILT, dark behind `SPAWN_SUBAGENTS_ORGS` (2026-10-30, "As built, OA-9f" in §4.5). It changes the rule of §6.3 from a count to a kind of job. A child reads long material or runs a chain of steps. The main assistant makes light reads itself, in parallel. The flag flip stays OWNER-GATE.

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
below, PR #1383). The flag is off on each box, so no run loads a pack yet.

OA-7e is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-7e"
below, PR #1389). It adds layer (b), the meta tool `load_skill_tools`.

OA-7h is PARTLY BUILT (2026-10-28): the gate is NOT met, by one turn.
Run 4 (`docs/one_assistant_measure_2026-10-28.md`, with the OA-7j rules and 5
repeats) passed pack 78 of 90 and delegation 79 of 90. Without the hand-off
check, both passed 79 of 90. `no_write` on `reply_correction` turn 1 passed 5 of
5 on both arms. The pack arm handed off mail work in 1 of 90 turns, and it ran in
a median of 12.3 s against 33.9 s. The three run-3 failure kinds are fixed
(2 of 3, 2 of 3 and 1 of 3 became 0 of 5, 1 of 5 and 0 of 5). The owner decides
whether one mail hand-off after a pack load fails the gate. Run 3 (46 of 54
against 47 of 54) and run 2 (the flag `email` alone) are in the same report.
Run 1 is `docs/one_assistant_measure_2026-10-27.md`.

OA-7d is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-7d"
below). The dock sends `surface`, and a turn from `/email` loads the `email`
pack with no email word.

**As built, OA-7d.** The surface is input 2 of layer (a). It is a hint, and it
adds the core pack of the app and nothing else.
- **The client.** `lib/shell/surface.ts` holds `surfaceOfPane` (the first path
  segment of `here.href`), `cleanSurface` and `surfaceField`. The dock passes
  `surface` to `AgentChat`, and `AgentChat` passes it to `useAgentChat`. The
  hook puts `{ surface }` in the body of the live send and of the `buildBody`
  send. The reconnect replays a run that has begun, so it sends none. A page
  that belongs to no app sends no key. Only the dock sets it.
- **The route.** `api/agent/chat/route.ts` cleans the value and puts it in the
  gateway payload. The gateway pops it, as it pops `card_answer`, and binds it
  with `tool_packs.surface_scope` in the same `with` as the typed words. A
  room, the WhatsApp path and a scheduled run bind none, so they read "".
- **The check, twice.** `tool_packs.clean_surface` is the allow-list. A value
  passes only when it is a string of at most 32 characters, a slug, and the
  `surface` of a pack. Today only `email` passes. Anything else reads "".
  `current_surface` and `begin_turn` clean it again.
- **The choice.** `Pack.surface` names the app whose core pack it is. `email`
  names `email`. `email-setup` names none, so the surface never adds it.
  `choose` loads a pack with a matching surface after the sticky packs, with
  the layer `surface`, and the cap and `allowed` still apply. So the flag
  decides what a forged value adds.
- **No new guard path.** `_start_pack_turn` reads the surface after
  `packs_on`, so a flag-off run reads no surface and no pack state. The held
  chat, `no_egress`, the card rules and the MCP skip are all downstream of the
  choice and do not read the surface. A room, a batch run and a delegated run
  start no read, so a bound surface loads no pack there.
- **Fence.** Section 8 of `tests/unit/test_tool_packs_choice.py` and
  `workbench/control_plane/src/lib/shell/surface.test.ts`. Each case names its
  mutation.
- **Not built.** The pack of any other app. Each new pack sets its own
  `Pack.surface`, and the allow-list grows with it.

OA-7f is BUILT, dark behind `TOOL_PACKS` (2026-10-27, "As built, OA-7f"
below). It adds layer (c), the pack items of the turn-kind request.

OA-7i is BUILT, dark behind `TOOL_PACKS` (2026-10-28, "As built, OA-7i"
below). With packs on, the loader replaces the specialist hand-off.

OA-7j is BUILT, dark behind `TOOL_PACKS` (2026-10-28, "As built, OA-7j"
below). It tunes three rules of the `email` pack.

**As built, OA-7f.** Layer (c) loads a pack that layer (a) missed, from the
System-1 request that already runs. It adds no model call.
- **The items.** `tier_policy.turn_kind(..., packs=...)` adds one `yes_no`
  item for each pack in `packs`, with the id `pack:<slug>`. The request
  stays one request. The two items of before keep their ids, and a request
  holds at most 20 items. A slug that is no pack gives no item.
- **What the items name.** `executor._pack_loader_offer` gives the packs. They are
  `LoadSkillTools.offered`. The items name a pack only when four things
  hold. Layer (a) did not load it. The flag allows it. No admin toggle stops
  it. It fits the caps.
  A run with no loader asks for none. That covers a shared room, WhatsApp, a
  delegated run, a sub-agent, the batch path and a run with the flag off.
  With the flag off the call to `turn_kind` has no `packs` argument, so the
  request is byte for byte the request of before. No pack state is read.
- **When no item goes out.** The items ride in the request that goes out
  anyway. A turn under 12 words, a Max turn and a turn with a pinned default
  tier send no request, so they send no item.
- **The answer.** A `yes` at or above the threshold of the effort puts the
  slug in `TurnKind.packs`, in the order asked. A timeout, a failure, a
  garbled reply, an unknown id, an off-scale confidence and a choice outside
  the options load nothing. The pack items never change the kind, its source,
  the effort or the tier. `RunTierPolicy.packs` only carries the slugs. The
  tier choice does not read them.
- **The apply.** The executor hands the answer to the loader after the tier
  wait (`executor._apply_turn_pack_items`). The line numbers `:5874-5877` in
  the design moved, and the call now sits right after
  `_tier_run = await ...`. `LoadSkillToolsProvider.before_run` then loads
  the packs, so the first model request holds the tools. The rules of the
  pack ride as an instruction of the turn, in place of the answer of a call.
- **The same builder and guards.** `LoadSkillTools._admit` holds the caps
  (3 packs, 35 tools a pack, 110 tools), the MCP rule and the call of
  `_pack_tools`. Both `_load` (layer (b)) and `load_early` (layer (c)) call
  it. A refused pack loads nothing and the run goes on. A retry of the turn
  adds the same tools and builds nothing again.
- **Sticky.** Unchanged. A load by the turn kind is not sticky. The pack
  becomes sticky when one of its tools runs.
- **The MCP rule.** MAF keeps an MCP server in `agent.mcp_tools` and adds
  its functions after `before_run`. So `load_early` passes `mcp_tools` to
  `_admit` as `extra_live`, for the MCP rule only. No email pack loads
  beside a server. The run key in the build context comes from the loader.
  A frame without the key gives the same build.
- **MAF import.** `_agent_shape` uses the private `normalize_tools`. If the
  import fails, the pack does not load. A pin test fails when MAF renames it.
- **Open.** The persona does not swap the specialist text in layer (c), as
  in layer (b), because a load adds no owner to `PackRun.replaced`. The
  cost of a longer System-1 request (a few answer tokens for each item) is
  an estimate. OA-8 should measure it.
- **Fences.** `tests/unit/test_tier_policy.py` (`TestTurnKindPackItems`) and
  `tests/unit/test_turn_kind_pack_items.py`. Each case names its mutation.

**As built, OA-7j.** Dark behind `TOOL_PACKS`. `_EMAIL_RULES` in
`tool_packs.py` grew by 323 characters (2,362 to 2,685).
- **Search first.** The reply bullet ends with the search rule.
- **Send it.** The old line "call compose_reply or send_draft at once" is
  gone. The new line sends the draft of the working set with `send_draft`.
  It allows `compose_reply` only when no draft exists.
- **No unasked draft.** A new bullet carries the OA-7g rule in the pack.
- **Run 4.** OA-7h must run again to confirm the gate.

**As built, OA-7i.** With the flag on, the loader replaces the specialist
hand-off, dark behind `TOOL_PACKS`. The rule is in the OA-7i block of §4.2.8.
- **The plan.** `tool_packs.plan_loader` is the one reachability rule. Its
  answer (`LoaderPlan`) gives the offer of `load_skill_tools` and the owners
  whose specialist leaves (`via_loader`). `_tool_injection._plan_pack_loader`
  asks it once for each run, and `_offer_pack_loader` only reads the plan.
- **The cap.** An owner is reached only when each of its packs that the run
  does not hold fits 110 tools with the specialist gone. So the discount is
  the owner's own specialist tools in the pool. At a base of 79 the owner is
  reached. At a base of 80 it is not.
- **The MCP rule.** The MCP injection runs after the packs.
  `_tool_injection._mcp_may_attach` looks ahead, with the early exits and the
  row filters of `_inject_mcp_servers`. It lists the registry rows and
  refreshes no token. A fault reads as "a server may attach", so the
  specialist stays. `MAF_MCP_ORGS=*` is on in production, so an org with a
  server in scope for the orchestrator keeps the hand-off.
- **The record.** `PackRun.via_loader` holds the owners. They join `replaced`.
  `PackRun.refusal` answers `LOADER_HANDOFF_REFUSED` while the run does not
  hold each pack of such an owner.
- **A refused load opens the hand-off.** The plan counts no granted Custom
  App action, because those join later with no cap. So the plan can reach an
  owner that the live list cannot serve. A refused load of a pack of such an
  owner answers `LOAD_NO_PATH`. The text names `call_agent` and the owner.
  The owner joins `PackRun.via_failed`, and `PackRun.refusal` gives None for
  it. So the hand-off runs for the rest of the run. The fallback is the guard,
  and the plan does not read the app grants.
- **The edge case.** A load of `email` can leave no room for `email-setup`.
  That refusal is `LOAD_NO_PATH` too, so the model hands the setup work to the
  email assistant. Next turn `email` is sticky and `email-setup` cannot load.
  The plan reaches no owner, and the rules route the setup work to the owner.
- **The logs.** `executor.packs_loaded` is logged only when a pack built, as
  before OA-7i, because OA-7h counts it as a load. The plan rides on
  `executor.pack_plan`.
- **The MCP read.** `_mcp_may_attach` calls `gateway.connections.mcp.enabled_meta`.
  It reads the rows with no header blob and no token refresh.
- **The CRM assistant.** `crm-assistant` is the second owner (pack
  `crm-records`, 9 tools). OA-8a gives it no persona bullet, and
  `tool_packs.NO_PERSONA_BULLETS` lists it. So the plan accepts that owner with
  no bullet, and the persona stays as it was. Any other owner with no bullet is
  not reached. The CRM rules say that no CRM agent exists, and the refusal
  names `load_skill_tools` and `crm-records`, so the two texts agree.
- **A held chat.** Nothing in `_start_pack_turn` or the offer excludes a held
  chat. A no-egress run and a marked chat keep the replacement. The pack loads
  held, with its sends dropped, as layer (a) does. A no-egress run gets no MCP
  server.
- **Unchanged.** `call_agent_background`, scheduled jobs, workflows, the app
  rails, registry quick actions and the Copilot path.
- **Open.** The base count is an estimate before the injection. A granted app
  tool is not in it. A tool count past 110 in a run that the plan reached
  shows in the `executor.pack_request_tools` line. OA-8e can measure the gap.
- **Fences.** `tests/unit/test_loader_replaces_handoff.py`,
  `tests/unit/test_load_skill_tools.py` and `tests/unit/test_tool_packs_off.py`.
  Each case names its mutation.

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

The OA-9a parser is BUILT, dark (2026-10-27, "As built, OA-9a parser"
below). `spawn_subagents` calls it since OA-9b ("As built, OA-9b" below).
The narrowings of §4.5 stay as the record. The policy SUB-1 (Metorite #822) is in §6.8.

**As built, OA-9a parser.** `acb_skills/subagent_contract.py` holds the two
parsers of the contract of §6. No module calls them, and no flag reads them.
OA-9b builds `spawn_subagents` on them.
- **The items out (§6.4).** `parse_items` reads the one JSON object of a
  child. That object has the one key `items`. Each item has exactly the
  keys `value`, `kind` and `source`. Any other output becomes one item of
  kind `inferred`, with the source `unstructured` and the whole text.
- **The parse is strict.** An extra key, a code fence or one bad item makes
  the whole answer into that one item. So the parser loses no text of a
  child. No `quoted` mark stays beside text that the parse dropped.
- **The jobs in (§6.2 and §6.5).** `parse_jobs` checks the `jobs` argument
  against the `run_tools` of the parent. It checks the first 5 jobs. Each
  job after the fifth gets its name in one line and does not run.
- **The refusals.** A job does not run when it names a pack (§4.5, OA-9b
  item 3). It also does not run when it names a delegation tool or the
  spawn tool. A tool that the parent does not have stops it too, and so
  does a tool that is not read-only. Each refusal is one line that tells
  the model why.
- **Fix round 1 (verifier).** A repeated key in the JSON of a child makes
  the answer another shape. `json.loads` keeps the last value, and that
  would drop a quoted fact. `packs` must be absent or an empty list. So
  `0`, `false` and `{}` get a refusal. Bytes decode, and no value is stored as
  a `repr`. A call past the cap names 10 jobs and counts the rest.
- **The member tools.** `ask_questions`, `ask_user`, `request_confirmation`
  and `share_artifact` say `read_only=True`, but a child has no member to
  ask. The parser refuses them (`MEMBER_TOOLS`). `remember` stays a read.
- **The specialist tools.** `parse_jobs` takes `delegating`, the names of
  the specialist tools of the parent. They get the hand-off reason.
- **OA-9b must enforce the child tool set.** The parser only checks the
  request. Floor tools reach every run, so OA-9b must withhold and refuse
  all other tools at `tool_guard.WithholdTools` and `RefuseTools`. §4.5,
  OA-9b narrowing 1 names the mechanism: an allow-list pair.
- **Read-only.** The default check reads `tool_annotations`. A tool with no
  entry is not read-only. OA-9b can give its own check.
- **The caps.** The module holds the caps of §6.2 as constants. A fence
  makes sure that `MAX_JOBS` equals `agent_tools._PARALLEL_MAX`, the cap of
  the one fan-out.
- **The fence.** `tests/unit/test_subagent_contract.py` holds 58 cases, and
  16 mutations each made it fail. One case fails when a module of the
  platform imports the contract. OA-9b replaces that case with its one
  caller.
- **§4.5, OA-9a item 1.** `test_tool_packs_off.py` holds no OA-9a case, so
  no case moved. The worker cases wait for the worker half: the refusal with
  no job, and the worker out of the picker.

**As built, OA-9b.** The OA-9a worker half and OA-9b are BUILT, dark behind
`SPAWN_SUBAGENTS_ORGS` (2026-10-28). Nothing sets the flag. The flip is
OWNER-GATE.
- **The worker.** `apps/agents/agent-worker/` holds `agents.py`,
  `config.json`, `instructions.md` and `pyproject.toml`. The worker is in
  `gateway.routes.agent._INTERNAL_AGENTS`, and in no other list. The executor
  reads `_registry_entries()`, which puts the internal list first. The
  worker refuses a run with no job at three callers: `call_agent`,
  `_run_sub_agent_streaming` and `run_agent`. `build_agents()` never raises.
- **The pair.** `subagent_contract.child_pin(allowed)` returns the
  `WithholdTools` and `RefuseTools` pair. The worker passes it as
  `middleware=`. `allowed` is the tools of the job. This is the layer that
  counts. A model call has 4 rounds at most: `max_iterations` 3 and the final
  call with `tool_choice` `none`.
- **The tool.** `agent_tools.spawn_subagents` is an own tool of the
  orchestrator. `build_orchestrator_agent` adds it for an org on the flag.
  `_tool_injection._drop_spawn_tool` drops it at the injection seam for any
  other org. It joined `egress.DELEGATION_TOOLS`, and the orchestrator
  registers it as a platform callable. `call_agents_parallel` and
  `spawn_subagents` share `_run_branch` and `_fan_out`.
- **The caps.** 5 jobs at once, 2 spawn calls for each run (`take_spawn`,
  keyed by the `run_id` of the run binding), a job clock of 120 s and a
  fan-out clock of 180 s. Stop cancels every child, and each child has ended
  before the Stop goes on. The parent reads `parse_items` output only.
- **Where the job lives.** `bind_job` puts the `Job` on the frame of one
  child task. `executor._without_job_key` takes it out of any sub-run that
  is not the worker. `executor._job_tool_scope` makes the tools of the job
  the injection scope of the worker.
- **How a child meets OA-7i, the packs and CTX-1.** A child is a sub-run, so
  it never goes through `_apply_pack_turn`. It gets no pack, no
  `load_skill_tools` and no loader, and `PackRun.via_failed` is never read
  for it. The sub-run drops the pack keys from its context. A pack tool is not
  in `run_tools`, so a job cannot name one, and a job that names `packs` waits
  for OA-9c. CTX-1 (`tool_result_spill`) is a function middleware that
  attaches to the native agent. With `TOOL_RESULT_SPILL` `on`, it attaches
  only to an agent that holds `read_tool_result`. A child never holds it, so
  its results do not spill. The result of `spawn_subagents` never spills,
  because the tool is a delegation tool.
- **Spec drift found.** `profile_tools.WRITE_TOOLS` holds six tools since
  `profile_interview`, not five. §6.8 now says so, and the memory table has 11
  tools. A held chat that asks the untrusted-answer card also asks it for
  `spawn_subagents`, as for `call_agents_parallel`.
- **Not built.** The packs in a child (OA-9c, built 2026-10-29, below) and
  the writes in a child (OA-9d). The persona lines (OA-9e) are built, dark
  (§6.9).
- **Fences.** `test_subagent_worker.py`, `test_spawn_subagents.py` and
  `test_subagent_blocked_tools.py`. The pin
  `test_nothing_calls_the_contract_before_oa_9b` is gone on purpose. A pin on
  the known callers replaces it. 19 of 20 mutations fail the fences. The
  survivor takes out the egress filter of the injection only, and
  `_apply_no_egress` holds the same set as a second layer.

**As built, OA-9b hardening.** The Opus review of OA-9b found seven faults. None
was a P1 or a P2. Note 1 was a gate for the flag flip. All seven are closed,
and the flag stays off. Fences: `tests/unit/test_spawn_hardening.py`,
`workbench/control_plane/src/lib/spawnHandoff.test.ts` and
`tests/unit/test_chat_fold_handoffs.py`.
- **1. The chat UI.** `spawn_subagents` and `delegate_to_agent` are hand-offs.
  `chatStream.isHandoffTool` and `takeHandoff` decide it, for the live
  reducer and for the checkpoint that `route.ts` saves. The gateway fold
  writes the final stored row at the run end, and it replaces the checkpoint.
  So `gateway/chat_fold.py` holds the same rule (`take_handoff`). A unit test
  reads the names out of `isHandoffTool` and compares them with
  `HANDOFF_TOOLS`, as sets, in both directions. Each child step stays on its
  own spawn row. The spawn row keeps no child text, because that text is item
  JSON for the parent model. A later specialist row or `call_agent` row gets
  its own steps. None of them are labelled `worker`. In the live view, an
  error of one spawn child adds a failed step for that child. The step key is
  its run id. The row stays active. The stored row does not hold that step.
  The spawn result holds the failure as an item.
- **2. Nested spawns.** A run that is a sub-run never spawns, at any depth.
  The test reads `_delegation_chain` and the `sub_run` key that the executor
  binds on a delegated run. The spawn calls of one run run one after the other
  (`subagent_contract.spawn_lock`). So at most 5 children of a run run at once,
  and §6.2 holds.
- **3. Tier routing.** `tier_routing_on` returns False for an internal agent,
  also with `AI_TIER_ROUTING=*`. §6.6 holds.
- **4. The clock of a child.** The MAF loop budget is
  `subagent_contract.CHILD_LOOP_BUDGET_S` (90 s). The job clock stays 120 s.
  So the final call with no tools runs before `wait_for` stops the child.
- **5. Calls past 4.** A worker run never self-anneals and never mutates a
  repo, in the generic failure clause and in the `AgentLoadError` clause. The CRM answer check skips the worker. A job that names `decide` does
  not run (`EXTRA_CALL_TOOLS`).
- **6. Untrusted items.** The spawn result is one block of
  `UntrustedFrame` (the label `SUB AGENT ITEMS`), with the data note before it.
  `parse_items` checks the source of each item against an allow list
  (`source_allowed`). A source may be an ASCII http or https URL, a tool name
  that the job holds, or `inferred`. Any other source becomes
  `sub-agent (unverified)`, and the kind becomes `inferred`. So `member` and
  each look-alike of it cannot pass.
- **7. The loader.** `loader._ensure_local_git_repo` takes the `_repo_locks`
  lock of the cache dir name around the copy and the git seed.
- **Also.** `register_agent` refuses the name `worker` (HTTP 409). The tool
  docstring says that a job with no `tools` key holds no tool.
- **Not built.** A child that shows its own name in place of `Worker` in the
  row title. The row reads "Asking Worker". Copy is a product call.

**As built, OA-9c.** Packs in a child are BUILT, dark behind
`SPAWN_SUBAGENTS_ORGS` (2026-10-29). Nothing sets the flag. The flip is
OWNER-GATE. The OA-9a refusal of each `packs` value is gone. The parser now
checks each pack against the parent. An email child holds no tool today,
because no email tool says `read_only=True`. So OA-9c serves CRM and
Projects reads only.
- **Naming.** A job names its packs with the `packs` key. A child inherits no
  pack. `parse_jobs` accepts a slug only when `PackRun.holds()` of the parent
  names it, so a child never holds a pack that its parent lacks (SUB-1 rule 1).
  The record holds only the packs that passed `packs_on` and the admin
  toggles (§6.2), so the spawn reads no flag of its own.
- **Fail closed.** `agent_tools._parent_packs` reads the record at
  `tool_packs.PACKS_KEY` on the parent frame. With no record, or a read that
  fails, a job that names a pack does not run. A job with no `packs` key runs
  as in OA-9b.
- **The build.** `agent_tools._child_pack_tools` calls
  `_tool_injection._pack_tools`, the one builder of OA-7b, on the parent
  frame. The builder decides the held answer and drops the egress tools of a
  held build. The spawn copies no part of that rule.
- **Which tools.** The child keeps a pack tool only when
  `subagent_contract.child_may_hold` lets it through. That test reads the
  read-only mark of the risk registry (`annotated_read_only`), less the SUB-1
  names. So a child holds no write in an open chat either. The registry keys
  on the tool name, and the last module that names a tool wins. A fence keeps
  the own mark of each pack tool equal to its registry mark.
- **No card from a child (review fix).** Several read tools draw a card or
  move the member's page. A child copies the run queue of its parent, so its
  card would reach the member's chat and miss `parse_items`.
  `subagent_contract.DISPLAY_TOOLS` names them, beside `MEMBER_TOOLS`, and
  `child_may_hold` and `parse_jobs` refuse each one. No single marker names
  them, so a fence scans each pack tool and each platform tool for a call to
  `emit_generative_ui`, `emit_frontend_tool` or `resolve_run_queue`. A
  read-only tool that reaches one and is not in the set fails the fence.
- **The run key.** The spawn mints one key for each child with
  `secrets.token_urlsafe(24)`. It is never the key of the parent and never the
  key of a sibling. `Job.pack_run` holds it, and `repr` does not show it.
- **Where the key goes.** `executor._without_pack_keys` gives the worker the
  key of its job and never the record. The worker runs as a batch run, and
  `bind_artifact_context` replaces its context. So the batch run binds the key
  again, through `executor._job_pack_keys`. The key ends with the run of the
  child, when the context scope of the child closes.
- **The pin.** The pack tools are the worker's own tools (`tools=`).
  `subagent_contract.child_allowed` gives the pair the tools of the job and
  the pack tool names. The injection scope stays the tools of the job,
  because it narrows only the platform tools.
- **The cap.** The tools of the job plus the pack tools must fit
  `tool_packs.MAX_REQUEST_TOOLS` (110). Layer (b) counts the same way: the
  live list plus the load. A job past the cap does not run.
- **A pack with no read tool.** Such a pack stops its job with one line. A
  call with no job left uses no spawn.
- **Stranger text.** The held answer of a call is true when a job that runs
  holds a pack of an owner in `UNTRUSTED_INPUT_AGENTS`. The spawn computes it
  once and uses it for the build, the mark and the start of each child. With
  it, each pack of the call builds held, the spawn marks the chat on the
  parent frame before any child starts (EM-G11f3), and each child starts with
  `no_egress` bound. So an email child holds no web tool, and no pack call of
  a child clears `no_egress`. When the job with such a pack stops at the
  build, the jobs that run build again open, and the chat stays clear.
- **A difference from the main assistant.** The main assistant builds the
  email pack open in a clean chat, and its first email call runs open. A
  child builds it held. The tool set is the same today, because no read tool
  of a pack is an egress tool.
- **The items.** A pack tool that the child held is a source that its item
  may name (`source_allowed`).
- **The docstring.** The docstring of `spawn_subagents` names the `packs` key
  in one line. With the flag off, the persona stays byte-identical.

| Pack | The tools that a child holds |
|---|---|
| `email` | None. No email tool says `read_only=True` |
| `email-setup` | None, for the same reason |
| `crm-records` | 5: `crm_search_notes`, `get_pipeline`, `get_record`, `get_timeline`, `search_crm` |
| `projects` | 10: `calendar`, `find_tasks`, `list_tasks`, `my_task`, `my_work`, `people_for`, `project_summary`, `projects_tree`, `task_detail`, `vocabulary` |
| `projects-plan` | 16: `analytics_finished`, `analytics_load`, `analytics_outlook`, `analytics_stuck`, `analytics_throughput`, `find_conflicts`, `fit_for_task`, `needs_attention`, `rebalance`, `recurrence`, `report_list`, `report_recipients`, `report_render`, `task_dataset`, `team_capacity`, `waiting_agent_requests` |
| `projects-structure` | 7: `intake_queue`, `my_areas`, `my_contexts`, `notifications`, `project_access`, `project_views`, `watchers` |
| `projects-bulk` | 2: `list_reminders`, `my_led_projects` |

- **Open.**
  - The email packs give a child no tool. A fix marks the email read tools
    `read_only=True`. That mark is an input of the permission gate of the
    email assistant, so the fix is a slice of its own.
  - A batch sub-run binds its own minted thread. So the email call of a child
    marks that thread and writes its working set there. The mark on the
    parent frame holds the chat. This path runs only after an email tool is
    read-only.
- **Fences.** `test_pack_guards.py` holds the child cases, the real builder,
  the table above and the scan of the display tools. `test_spawn_subagents.py`
  holds the spawn rules and one run from end to end through the real sub-run
  seam, with the permission gate off and on.
  `test_subagent_contract.py`, `test_subagent_worker.py` and
  `test_subagent_blocked_tools.py` hold the parser, the pin and SUB-1.

**As built, OA-9f (2026-10-30, dark).** The spawn cue follows the evidence of
OA-8e run 3 (§6.3). Nothing sets `SPAWN_SUBAGENTS_ORGS`. The flip is OWNER-GATE.
- **The cue.** `_SPAWN_BLOCK` no longer counts reads. It says to spawn when each
  job reads long material that would flood the chat, or needs its own chain of
  dependent steps. It says not to spawn for independent short reads or searches,
  at any count. The block is 84 words. The "when" lines of the docstring of
  `spawn_subagents` say the same.
- **The light chats.** `fan_companies` and `fan_invoices` are `not_spawned`
  chats now. They keep `names_each_company` and `totals_the_invoices`. An
  invoice page sits on the public site of its supplier
  (`fanout.invoice_url`), so no model reads the host as a login portal.
  The refusal of `.example` hosts stays.
- **The heavy chat.** `fan_contracts` asks for the payment terms and the
  late-delivery penalty of five vendor contracts. Each contract is built in
  code (`contracts.py`) from fixed clauses, about 40,000 characters. The
  payment term sits after character 20,000, and the penalty after character
  30,000, at a different place for each vendor. A plain `fetch_page` cuts at
  8,000 characters, so it reads neither fact. The reader must call it with a
  larger `max_chars`. The real tool has no upper cap. The fixture cuts the
  same way, with a floor of 500 and a marker at the cut. Turn 1 expects `spawned` and `items_contract`.
  It has one `terms_of_<vendor>` check for each vendor.
  Turn 2 asks for the vendor with the strictest penalty.
  It also asks what a 10-day delay on a 5 lakh order costs.
  The answer is Kaveri, one full week, Rs 15,000.
  On the pack arm the parent carries five full contracts into turn 2.
  On the spawn arm it carries five short items. The input tokens (`model_calls[].prompt`) and the time of
  turn 2 show the difference. It is the research chat of stop result 9, because that mark is the
  `spawned` check. A URL with no fixture still fails and shows in `web_unknown`.
- **"No job block" was a harness bug.** The result of `spawn_subagents` is a
  data note, then the item blocks between two marker lines, then the notes. The
  first header follows the open marker line with no blank line. The check split
  on blank lines, so it lost the first block. A call with one job read as "no
  job block". The product renders the header on every path. `checks.job_blocks`
  now reads the frame. The rows of run 3 do not keep the result text, so the
  exact call is not proved. Only a call with one job gives the error.
  So the model sent fewer jobs than five in those two turns. Three other paths each
  have a test. They are a child that fetched a URL with no fixture, a child that
  returned prose and a child that timed out. None of them reads as "no job
  block".
- **Not changed.** No colour, no flag, no deploy.
- **Fence.** `tests/unit/test_spawn_persona.py` and
  `tests/unit/test_one_assistant_measure_oa8e.py` (OA-9f cases).

**As built, OA-7c.** Layer (a) and the sticky state put packs in a live run.
Three inputs choose the packs: the sticky packs of the chat, a reference in
the member's words, and the words of a pack. The surface came with OA-7d.
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
  so both email packs (fix round 1, P1-A). OA-7i adds a second case: it also
  acts while every pack of the owner is held or reachable by the loader. The
  text then names `load_skill_tools` and the packs not held (OA-7i, below).
  Then the streaming seam
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
reads the tool names of the run. OA-7f adds the pack items (built).

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
| CRM Mirror mode | `_drop_mirror_record_writes` and `MIRROR_RECORD_WRITES` (`_tool_injection.py`, about :1586-1625) | The CRM pack builder runs the same drop inside `_pack_tools`, so each layer gets it (OA-8a) |
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
| OA-7d | The structured `surface` field from the dock. BUILT, dark, 2026-10-27 | LOW |
| OA-7e | Layer (b): `load_skill_tools`. BUILT, dark, 2026-10-27 | MEDIUM |
| OA-7f | Layer (c): pack items in the turn-kind request. BUILT, dark, 2026-10-27 | MEDIUM |
| OA-7i | With packs on, the loader replaces the specialist hand-off. BUILT, dark, 2026-10-28 | HIGH |
| OA-7j | The `email` pack rules: search first, "send it" is `send_draft`, no unasked draft. BUILT, dark, 2026-10-28 | MEDIUM |
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

**OA-7j. The email pack rules.**
- **Why.** OA-7h run 3 (2026-10-28) passed 46 of 54 pack-arm email turns and
  47 of 54 for delegation. The gate is short by one turn.
  - `no_write` on `reply_correction` turn 1 passed 2 of 3. The gate needs 3 of 3.
  - `five_turns` turn 3 failed twice. For "send it" the model called
    `compose_reply` again, not `send_draft`.
  - `exact_reply` failed twice in run 2. The model called `compose_reply`
    for a named person before it found the address.
  - The pack arm was 2.4 times faster at the median (11.2 s against 27.3 s).
    It made no mail hand-off.
- **Rule.** The `email` pack rules say three things. Find the address of a
  named person with `find_contact` or `query_inbox` before `compose_reply`.
  After a draft in this chat, "send it" means `send_draft` on that draft.
  Draft only when the member asks for a draft or a reply.
- **Fence.** `tests/unit/test_email_pack_rules_oa7j.py`. The rules ride only
  with the pack, so the flag-off persona does not change.

**OA-7i. The loader replaces the specialist hand-off.**
- **Why.** OA-7h run 1 (`docs/one_assistant_measure_2026-10-27.md`) ran 54
  email turns for each arm with the real `TOOL_PACKS` flag.
  - The email pack loaded in 12 turns, all by words. In those turns the model
    handed off mail work 0 times. The median time was 7.4 s.
  - In the other 42 turns no pack loaded. The model handed the work to
    `email_assistant` in 39 of them. It never called `load_skill_tools`. The
    median time was 17.6 s. In production a hand-off turn takes 78 to 259 s.
  - Layer (c) cannot close the gap. The turn-kind request skips a short turn,
    and 66 of 108 turns were short.
  - Quality was equal. Without the hand-off check the pack arm passed 50 of
    54 turns, and delegation passed 48 of 54.
  - D117 retires conversational delegation.
- **The rule.** It acts in a run where `packs_on` allows packs and the loader
  is offered. Take each owner with packs (today `email-assistant`). Suppose
  the run holds every pack of the owner, or the loader can load the rest on
  its own. Then the specialist tool of the owner leaves, and the owner joins
  `PackRun.replaced`. The persona swaps the delegation bullets as `persona_for`
  does. A hand-off by `call_agent` or `delegate_to_agent` is refused. The text
  names `load_skill_tools` and the packs. "On its own" means the admission of
  `LoadSkillTools._admit`. The run holds fewer than 3 packs. The count stays
  within 110 tools with the specialist gone. The MCP rule and the flag allow
  it. Else the specialist stays, as in OA-7c fix round 1.
- **Files.**
  - `acb_skills/tool_packs.py`: `plan_loader`, `LoaderPlan`, `PackRun.via_loader`,
    `persona_for` and `_rules_block` take `via_loader`, `Pack.loader_route`
  - `orchestrator/_tool_injection.py`: `_plan_pack_loader`, `_mcp_may_attach`,
    `_apply_tool_packs`, `_offer_pack_loader`
  - `orchestrator/executor.py`: `_apply_pack_turn` passes the grant check
- **Acceptance.**
  - Flag off: the tool list, the persona and the turn-kind request are byte-identical.
  - Flag on, no email pack held, loader offered: no `email_assistant` tool and no email delegation bullets. `call_agent` answers a refusal that names `load_skill_tools`.
  - `email` held and `email-setup` over the cap: the hand-off stays open, as before.
  - Beside an MCP server: `email_assistant` stays.
  - A held chat keeps the replacement. The pack loads held.
  - The offer and the removal read one function.
  - `call_agent_background` to `email-assistant` still runs.
  - A refused load of a reached owner opens the hand-off, and the refusal names `call_agent` and the owner.
  - The two-turn edge case: the setup load refuses, the hand-off is open, and the next turn keeps it open.
  - A turn that builds no pack logs no `executor.packs_loaded`.
- **Fence.** `tests/unit/test_loader_replaces_handoff.py`.

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
| `crm-assistant` | 21 own tools. The orchestrator already holds 12 of the 13 shared `crm_*` reads (`agent-orchestrator/config.json`, `tool_scope`). It lacks `crm_search_notes` (`tests/unit/test_crm_agent.py:1405-1412`) | The pack `crm-records`: 9 tools. They are 4 reads, 4 writes and `crm_search_notes` (OA-8a) |
| `projects-assistant` | 120 own tools (`agent-projects/config.json`) | Four packs: the core pack `projects` (23 tools), `projects-plan` (35), `projects-structure` (34) and `projects-bulk` (28) (OA-8b) |
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

**OA-8a. CRM.** Build AGENT-SAFE. The flag flip is OWNER-GATE.
- **Files.**
  - `acb_skills/skill_families.py`: the family `crm-records`, with the owner `crm-assistant` and 9 tools
  - `acb_skills/tool_packs.py`: the rules, from `agent-crm/instructions.md`. The `persona_for` skip for an owner with no bullets
  - `orchestrator/_tool_injection.py`: the per-owner pre-filter slot of `_pack_tools`, with the Mirror drop
  - The `crm_assistant` tool leaves through the generic `delegation_refusal` path. `orchestrator/agents.py` does not change
- **Acceptance.**
  - Each write asks its card in the stream of the orchestrator (`agent-crm/agents.py` :812, :897, :963, :1023).
  - Mirror mode drops the 3 record writes, in each layer (`_tool_injection.py`, `MIRROR_RECORD_WRITES`, about :1586-1625).
  - A held pack drops the 4 writes, as the held run of the CRM agent does (`tests/unit/test_no_egress_tool_audit.py:77-82`).
  - A pack read arms the number check.
- **Fence.** CRM cases in `tests/unit/test_pack_guards.py`, `test_tool_packs_choice.py`, `test_tool_packs_registry.py`, `test_turn_kind_pack_items.py` and `test_crm_answer_check.py`.
- **Verify.** `uv run pytest tests/unit/test_pack_guards.py tests/unit/test_tool_packs_registry.py tests/unit/test_tool_packs_choice.py tests/unit/test_tool_packs_off.py tests/unit/test_load_skill_tools.py tests/unit/test_turn_kind_pack_items.py tests/unit/test_tier_policy.py tests/unit/test_skills_registry.py tests/unit/test_crm_agent.py tests/unit/test_crm_answer_check.py tests/unit/test_no_egress_tool_audit.py -q`.

**As built, OA-8a (2026-10-27).**
- The pack has 9 tools: `search_crm`, `get_pipeline`, `get_record`, `get_timeline`, `create_lead`, `update_deal_status`, `log_activity`, `convert_lead` and `crm_search_notes`. `crm_search_notes` is also in the injected `crm` family. `_pack_tools` skips a name that the run holds already, so the overlap is safe. The drift gate `test_every_injected_tool_registered_in_exactly_one_family` now counts the families with no `owner`, because a pack has its own gate (`test_an_owner_tool_is_in_one_pack_only`).
- The Mirror drop lives in `_pack_tools`, in a map of pre-filters by owner (`_PACK_OWNER_FILTERS`). Layer (a) is covered by order alone, but layers (b) and (c) build after the executor drop, so each reads `crm_mode()` as the member. A held build reads nothing. A failed read keeps the tools.
- The base persona has no CRM text. `persona_for` skips the swap for an owner that is absent from `PACK_DELEGATION_BULLETS`, and still raises for an owner that is present and moved. The CRM rules carry the useful rules of `agent-crm/instructions.md`. They leave out the `call_agent` line about email and WhatsApp.
- `crm-records` is last in `PACKS`. The base is 55 tools. Base, `email` and `crm-records` give 95. All three packs give 123, past 110, so `choose` drops `crm-records` first.
- The words are whole words: `crm`, `zoho`, `sales pipeline`, `deal pipeline`, `pipeline stage`, `deals`, `leads`, `new lead`, `create a lead`, `convert the lead`, `log a call`, `lost reason`. The surface is `crm`.
- The hand-off is refused whenever the pack loads, because it is the only pack of the owner. `call_agent_background`, scheduled jobs, workflows, the CRM app rail, registry quick actions and an OA-9 child stay delegated.
- A held CRM pack names its real reason. The hold note takes the session of the run. So a chat that read mail gets the mail reason, in the persona and in the answer of a load. `egress.py` holds the CRM wording for an owner that does not read mail itself: `EGRESS_HOLD_NOTE_PACK_OTHER` and `EGRESS_HELD_PACK_ANSWER_OTHER`. The email owner keeps the email wording. `NO_PERSONA_BULLETS` names the owners with no persona text, and any other owner with no bullet raises.
- Redis can mark a chat after the pack built open. The gate of the pack then asks the untrusted card besides the card of the write, because the CRM owner does not read mail itself. This fails safe. A chat marked before the build holds the pack, with no write tool.

**OA-8b. Projects.** Build AGENT-SAFE. The flag flip is OWNER-GATE.
- **Files.**
  - `acb_skills/skill_families.py`: four families with the owner `projects-assistant` and 120 tools
  - `acb_skills/tool_packs.py`: four `Pack` entries with rules, words and a `delegation_refusal`. `Pack.surface` takes a tuple. `owner_tools` reads the name of a `FunctionTool`. `NO_PERSONA_BULLETS` names the owner
  - `orchestrator/_tool_injection.py`: `_pack_tools` and `_pack_call` accept a `FunctionTool`. A cache of the owner's tools. The covered-org pre-filter
  - `gateway/routes/integrations_skills.py`: the catalog price reads the name of a `FunctionTool`
  - `workbench/control_plane/src/lib/shell/surface.test.ts`: the slugs `tasks` and `calendar`
- **Acceptance.**
  - The four Projects families together equal `skill_projects.__all__`, and each holds 35 tools or fewer.
  - Each pack tool keeps the strict-argument class `_StrictTool` (`agent-projects/agents.py:110-181`). The schema says `additionalProperties: false`, and the tool answers an unknown argument by name.
  - In a covered org, no Projects pack loads, and the work goes to the specialist.
  - A batch tool shows one card with its counts, and the assembly gates it once.
  - The core pack loads beside the `email` pack at the measured base.
- **Fence.** `tests/unit/test_projects_packs.py`, and the Projects cases of `test_tool_packs_registry.py`, `test_tool_packs_choice.py` and `test_skills_registry.py`.
- **Verify.** `uv run pytest tests/unit/test_projects_packs.py tests/unit/test_tool_packs_registry.py tests/unit/test_tool_packs_choice.py tests/unit/test_skills_registry.py tests/unit/test_tool_packs_off.py tests/unit/test_load_skill_tools.py tests/unit/test_turn_kind_pack_items.py tests/unit/test_pack_guards.py tests/unit/test_projects_agent.py tests/unit/test_projects_agent_refusals.py tests/unit/test_no_egress_tool_audit.py tests/unit/test_delegation_no_egress.py -q`, and `npx vitest run src/lib/shell/surface.test.ts` in `workbench/control_plane`.

**As built, OA-8b (2026-10-28).**
- There are 120 tools, not 113. The four families are `projects` (23 tools), `projects-plan` (35), `projects-structure` (34) and `projects-bulk` (28). No tool is in two packs. The union is `skill_projects.__all__` and the `own_tool_scope` of the owner.
- The core pack holds 23 tools, not 27. The base is 55 tools, because it counts the four specialist tools. The request also carries `load_skill_tools` (1) and the `email` pack (31). That leaves 23 for the core pack, and the total is 110, the cap. `choose` does not credit the specialist tool that a pack removes, so a larger core pack would drop out beside `email`. The fence is `test_the_core_pack_loads_beside_the_email_pack_at_the_measured_base`. It fails when the base grows or when the core pack grows. `edit_task`, `watch`, `create_project`, `needs_attention`, `show_view` and `my_led_projects` moved out of the core pack, and the summary of its new family names each one.
- The owner builds `FunctionTool` objects of the class `_StrictTool`. The old builder skipped every tool that was no coroutine function, so each Projects pack was empty. `_pack_func` reads `fn.func`. `_pack_call` returns a shallow copy of the tool with `func` swapped, as `_gate_own_maf_tools` does. The class and the input model stay, so the schema keeps `additionalProperties: false`. `_as_agent_tools` already keeps a `FunctionTool` as it is. `owner_tools` and the catalog price read the name through `tool_guard.tool_name`.
- `_owner_pack_tools` builds the 120 tools once for each owner module. The entry holds while the module and its `_agent_tools` are the same objects, so a patched owner builds again. The pack call never changes a cached tool.
- The covered-org check is the second entry of `_PACK_OWNER_FILTERS`. It calls `_run_covered("projects-assistant")` on the run binding. A covered run builds an empty pack, so the specialist stays. The filter runs for each layer.
- `Pack.surface` takes a tuple. The core pack names `projects`, `tasks` and `calendar`, the three lenses of one task store (D52 to D54). `surface_slugs` gives the tuple for a single string too. The browser sends the first path segment already, so `surface.ts` does not change.
- The words are whole words. The core words are `task`, `tasks`, `subtask`, `backlog`, `sprint`, `milestone`, `overdue`, `due date`, `my work`, `kanban`, `project board`, `assign` and `assignee`. Bare `project`, `calendar`, `plan`, `report`, `delete`, `reply`, `deal` and `note` are out. The 18 email turns of OA-5 and the CRM turns of OA-8a load no Projects pack. "Make a task from this email" loads `email` and `projects`.
- The four packs come after `crm-records` in `PACKS`. Every pack sets a `delegation_refusal`, because without it the specialist stays and the model keeps handing work off (OA-7h, run 1: 42 of 54 email turns). The two persona lines that send task work to `projects_assistant` are the constant `PROJECTS_DELEGATION_BULLETS`, in `PACK_DELEGATION_BULLETS`. The persona with the flag off stays byte for byte as it was. A run that removes the specialist takes those lines out by one exact swap (fix round 1, P2-1).
- The Projects specialist stays beside a loaded Projects pack, unless the loader reaches the owner (`via_loader`) or the run holds every Projects pack (`SPECIALIST_STAYS_UNTIL_REACHED`). Email and CRM keep the older rule, where one loaded pack removes the specialist. The cause: a core load alone would strand plan and structure work, which cannot join it. The rules of each pack carry the line "No Projects agent exists to hand a task to." While the hand-off is open, `handoff_swap` replaces it with a route that names `call_agent` and `projects-assistant` for the work of a pack that did not load.
- When a turn matches a specific Projects pack and its core pack, and the request cannot hold both, `choose` loads the specific pack and drops the core pack (`Pack.replaces_core`). So "Who has spare team capacity?" on /projects loads `projects-plan`. Ten words of the plan and structure lists also match the core word "task", so they needed this rule. The bulk pack (28) fits beside the core pack.
- `plan_loader` offers no Projects pack to an owner that cannot load, such as a covered org. `GET /skills` reads the per-owner cache of the run path.
- OA-7i reaches the owner (`plan_loader`) when each unheld Projects pack fits under 110 tools with the specialist slot freed. With no pack held, all four fit, so the `projects_assistant` tool leaves and a hand-off answers the loader refusal. The measured base is 56 with the loader, so `projects-plan` (35) and `projects-structure` (34) never fit beside a held Projects pack. A covered org is not reached, because `_available` returns false for it. A load that refuses at run time sets `via_failed`, and the hand-off opens again. Each pack sets `loader_route`, the rules line that names the loader. Fences: the OA-8b loader cases of `test_projects_packs.py`.
- The held set of a Projects pack is empty. No Projects tool is an egress tool, so a held pack keeps all its tools, as the owner's held run does.
- **Open gap (found by the OA-8e harness, 2026-10-28).** On a Projects, Tasks or Calendar page the core pack takes the room. At the base of the harness (58 tools) the plan pack (35), the structure pack (34) and the `email` pack (31) do not fit beside it under the cap of 110. The loader does not offer them, and the Projects specialist stays. So plan and structure work on those pages still hands off to `projects_assistant`, unless a word of the pack loads it in place of the core pack. OA-10 must decide this before it retires the hand-off for Projects. The OA-8e report counts these turns by owner and reason (`app_handoff_turns`, stop result 11). The live base may differ: the line `executor.pack_request_tools` gives it.

**As built, OA-8f (2026-10-28).**
- **Why.** The live run of OA-8e logged `base_tools=65` on staging, and the run sent 59 tools. In seven days of production, 220 orchestrator tool calls used 23 of the 64 base tools. Ten floor tools had no call. On 2026-10-29 the journal of 8 days found `search_company` 50 times and `email_assistant` 41 times with the same pattern. It found 0 calls of `load_design_system`, `load_artifact_kit`, `decide` and `manage_todo_list`.
- **Floor trim.** The flag `ORCHESTRATOR_FLOOR_TRIM` (default off, one reader: `tool_packs.floor_trim_enabled`) makes the orchestrator opt out of ten tools. They are `list_integrations`, `get_errors`, `run_diagnostics`, `save_note`, `recall_notes`, `load_artifact_kit`, `load_design_system`, `manage_todo_list`, `call_agents_parallel` and `decide`. The set is `tool_packs.FLOOR_TRIM_TOOLS`. It rides the seam `_floor_opt_out` through `_floor_trim`, so the estimate, the scope and the final list agree. `call_agent` stays, because `LOAD_NO_PATH` and the Projects route line name it. With the flag off, the tool list and the persona are byte for byte as before. Only the orchestrator trims.
- **Text check.** No persona line, pack rule or persona bullet names a trimmed tool. The orchestrator is a native MAF agent, so it gets no injected-tools addendum. That addendum goes to Copilot SDK agents only. So no text needed a condition on the flag. The fence scans the persona and the rules.
- **Exact count.** The estimate `_pack_base_count` now leaves out the three CRM facts tools that `_add_crm_facts_block` removes for an org off `CRM_QUESTIONS_ORGS` (`_facts_removed`). `choose` takes a new argument `specialists`, and it subtracts the specialist tool that a chosen pack replaces. `_apply_tool_packs` counts the load tool only when an allowed pack stays unloaded, and then it chooses again with that tool counted. The log `executor.pack_plan` gives `planned_tools`, the count after the packs and the specialists that leave. The log `executor.pack_request_tools` counts the load tool, which a context provider holds. A new log, `executor.request_tools`, reads each model request. It gives the count sent, the count of different names and a short hash of the names. It includes provider and MCP tools. A granted app tool and an MCP tool stay outside the estimate, because their read needs the database or the network.
- **The staging shape.** The estimate was 65 (55, the 7 profile tools, search, `decide` and the loader). It is 62 now. The loader then reaches three owners and takes their three specialist tools out, so the run sends 59, and `planned_tools` says 59. The fence is `test_the_estimate_equals_the_sent_count_on_the_staging_shape`.
- **Pairs under 110.** With the trim off, six of the 21 pairs of packs fit on the staging shape. With the trim on, 14 fit at the staging base of 52, so eight pairs are new. Five are `email` with `projects`, `email-setup` with `projects` and with `projects-bulk`, `projects` with `projects-structure`, and `projects` with `projects-bulk` (104 tools). Three more fit at 110: `email` with `email-setup`, `email` with `projects-bulk`, and `projects` with `projects-plan`. For an org on the CRM list, which keeps the three facts tools, the numbers are six and nine.
- **The misses of OA-8e.** `pm_new_tasks` loaded the core pack from the Projects page. The model then called `create_task` three times (three cards) and never loaded `projects-bulk`. The core rules now say: for two or more new tasks at once, make the set in one step. Load `projects-bulk` when the loader offers it, and else give the set to `projects-assistant` with `call_agent`. `pm_miss` and `pm_capacity_open` loaded no pack in one repeat. In the other repeats they loaded `projects` or `projects-plan`, and then handed the work to the Projects assistant (`model_choice`). `crm_miss` was no product miss. The model answered with the CRM reads that the base holds. The check expected the pack `crm-records`, which holds the writes and the timeline. That is a check problem for the harness.
- **Review fixes (round 1).** The core bulk rule names no tool of another pack. The real bases are 61, and 64 for an org on the CRM list. There the plan does not offer `projects-bulk` beside the core pack. So the rule gives the set to `projects-assistant` with `call_agent`. With the trim on (base 52) the plan offers it. The two email `when` lines keep the cues of OA-7h run 4. The flag is in the owner-only list of `env_guard.py`.
- **Catalog.** The tool `load_skill_tools` gives each pack one line in the member's words, from `Pack.when`. The catalog uses it in place of the long summary. The `crm-records` line says that plain reads need no load. The fence is `tests/unit/test_pack_cues.py`.
- **Not done.** The removal of `spawn_copilot_agent`, `search_timeline`, `recall_timeline` and `save_episode`, a move of the CRM reads, a `profile` pack and a change of `MAX_REQUEST_TOOLS` (110).

**As built, OA-8g (2026-10-29, dark).**
- **Status.** Built. It changes rule text and load words only. The Projects packs, the sub-agent block and `TOOL_PACKS` stay dark. The CRM rule change rides with the CRM pack.
- **Why.** The OA-8e run 2 rows. Six hand-offs went by model choice. Four came after the model loaded `projects-plan` and called `analytics_load`. The old route bullet said that the plan pack "cannot fit" and sent that work to `call_agent`. The model loaded 18 packs itself. Eleven loads were for work that a word could have loaded: "overloaded" (6), "add tasks" and "a task for each" (5). Two were waste. In `crm_mirror` turn 2 the model loaded the Projects and email packs to move a deal that Mirror mode locks. Five were real misses: three renames of a status, and two `pm_miss` turns with no cue.
- **Projects rules.** Each of the four packs gains one bullet: "While a Projects pack is held, do its work with its tools. Do not call call_agent for work that a held or offered pack covers." The core route bullet now says to load the plan or structure pack when the loader offers it, and else to call `call_agent`. The private-task bullet says: for "remind me", "my own task" or "a private task", call `create_personal_task` at once and do not ask which project. A new task with no project named and no open project in the context is private too.
- **Load words.** The plan pack gains "overloaded" and "overbooked". A bare "capacity" stays out, because mail and CRM text use it too. The bulk pack gains "add tasks", "create tasks", "make tasks", "task for each" and "tasks for each". They are phrases and whole words. No bare "task" or "add" joins them.
- **CRM rules.** `convert_lead`: call it at once with a proposed `deal_name`, the lead's company. The card confirms the name, and the model does not ask in text. Mirror mode: do not load another pack to move the deal.
- **Spawn cue.** `_SPAWN_BLOCK` said to spawn for "four or more independent reads or searches". OA-9f replaced it with a rule by kind of job (§6.3, "As built, OA-9f").
- **Size.** The characters of each rules block, before and after, are as follows. CRM 2835 to 3062. Core 3001 to 3369. Plan 2089 to 2220. Structure 1889 to 2020. Bulk 2292 to 2423. The size pin on the core block moves from 3300 to 3400.
- **Mixed turns.** Three causes show in the rows. Two were a refused `create_task` in a space and a question about the project. The private-task bullet covers the second. The refused-create card count is a harness defect. One was a hand-off and a timeout, which the hand-off bullet covers.
- **Fence.** `tests/unit/test_pack_rules_oa8g.py` and `tests/unit/test_spawn_persona.py`. Run 3 of OA-8e measures the effect.

**As built, OA-8h (2026-10-29, dark).**
- **Status.** Built. It changes rule text, one load line and one scenario. The Projects packs and `TOOL_PACKS` stay dark. No check changed.
- **Why.** The OA-8e run 3 rows. The rows give the causes, and the report labels do not.
- **Private task by place.** `pm_private_task` (surface `tasks`) failed 2 of 3. The model asked which project, or asked nothing and made no task. `mix_task_from_mail` turn 2 failed 0 of 6 on both arms. The model read the project tree, then asked, or made a team task with `create_task`. The per-turn context names the place as "The member is in the My Tasks app (/tasks)." The label comes from `nav.ts`. The core rule now says: in the My Tasks or Calendar app, a new task with no project named is private. A task made from a mail, a call or a chat is private too. The model calls `create_personal_task` at once and does not ask which project. This follows D52 to D54: Tasks and Calendar are the member's own lenses.
- **Archive in one step.** `pm_archive` turn 2 failed twice. In r1 the model asked "archive or delete?" before any card. In r3 the model called `archive_task` six times. The row shows 3 cards and 3 gateway writes. So the member saw three cards for three tasks, not six. The other three calls wrote nothing. The real defect is three cards where one `bulk_update` card was wanted. The old bulk rule listed `archive_task` as "never batch" and gave no word on many tasks. The bulk rules now say: archive means archive, so do not ask about delete. For two or more tasks, call `bulk_update` once with every id. The question "archive or delete?" stays for "remove", "get rid of" and "clean up".
- **A named project with a vague cue.** `pm_miss` loaded no pack in r1 and r3. The model made two rounds of base-tool calls and said that "Mobile App" does not exist. In r2 the model tried `call_agent`, was refused, then loaded `projects`. The `when` line of `projects` now ends: "Also for a question on a named project or task." The line is 128 characters, under the 130 pin. No bare word such as "up to" joins the load words. The persona has no load cue line, so it is unchanged.
- **Size.** The core block is 3393 characters, under the 3400 pin. The last route bullet lost its repeated "Else call call_agent" sentence to make room.
- **`fan_tasks`.** The words named no project, so the product rule makes the tasks private and the expected `POST /projects/tasks` is undefined. The words now read "Make a task for each of these in Website Relaunch: ...". `Website Relaunch` is the name of `NODE_WEB`. The checks and the plan are the same.
- **Check review.** No check changed. For `pm_archive` turn 2, `archived` and `no_unasked_write` fail for a right reason: three single writes landed where one bulk write was wanted. For `one_card_per_write`, the row does not store the result text of the three silent calls, so the row cannot prove the check wrong. For `task_made_from_the_mail`, all six rows end in a question or a team task, never in a private task. The check needs "Priya" in the body, and no row tests that part.
- **Specialist gap (found here, repaired the same day, see the next bullet).** The Projects assistant instructions (`apps/agents/agent-projects/instructions.md`, lines 15 to 21) say "Otherwise ask the member which project". They have no private default for a task from a mail. The delegation arm of `mix_task_from_mail` fails 0 of 3 on that gap. The same file says "A member who wants five projects archived answers five cards" and had no rule to use `bulk_update` for many tasks.
- **`projects_ai_chat.md`.** §3.2 lines 180 and 206 to 208 do not conflict. "Add a task: call the vendor" is a team task in the Projects app. A personal task and a capture with no project are already the member's own project.
- **Fence.** `tests/unit/test_pack_rules_oa8h.py` and `tests/unit/test_pack_cues.py`. Run 4 of OA-8e measures the effect.
- **Specialist repaired (2026-10-29, owner-approved).** The Projects assistant instructions now say: a new task with no project named is private, so call `create_personal_task` at once. The specialist may not see the page, so the rule uses the request and the open project. An open project keeps its tasks. For two or more tasks, call `bulk_update` once. The fence is `tests/unit/test_projects_assistant_private_default.py`.

**As built, OA-8i (2026-10-30).**
- **Status.** Built. It has no flag of its own. It acts only in a run that holds a pack record, so it rides `TOOL_PACKS`. The guard only refuses. With the flag off, the run holds no record, and nothing changes.
- **Why.** OA-8e run 4, `mix_task_from_mail` turn 2, repeats 2 and 3. The run held `email` and `projects`, and the Projects specialist stayed (the open gap above). The model made the task with `create_personal_task` or `create_task`. Then it handed the same task to `projects_assistant`, and the specialist made it again. Each row shows two cards and two writes.
- **The record.** `PackRun` gains two fields. `wrote` holds, for each owner, the first write tool of its packs that the run called. `handed` holds each owner of a pack that a hand-off of the run reached.
- **A write.** A pack tool is a write when the risk registry does not mark it read-only. The test is `subagent_contract.annotated_read_only`, the one that OA-9c uses. A tool with no entry counts as a write. A call counts when it passes the run check and the held check of `_pack_call`.
- **A cancelled card.** A call that the member cancels on its card counts as a write. The Projects tool answers `Cancelled — nothing was changed.` as a plain result, so the seam cannot tell it from a write. The seam records the write before the gate, so the model cannot try the same work again through the specialist.
- **Forward.** `PackRun.refusal` adds one step at its end. When no older rule refuses, a hand-off to an owner in `wrote` answers `WROTE_HANDOFF_REFUSED`. The text names the tool and tells the model to give its result to the member. Like the other refusals, it names no other assistant. The streaming seam, `call_agent` and each branch of `call_agents_parallel` ask it through `delegation_refusal`. `call_agent_background` does not ask it, as before.
- **Reverse.** A seam that starts a hand-off asks `delegation_refusal` with `starts=True`. A None answer then records the owner in `handed`, with no await between the two. `tool_packs.pack_write_refusal` runs in `_pack_call` before its first await. A write tool of an owner in `handed` answers `HANDED_WRITE_REFUSED`, and it shows no card. A read still runs, and the hand-off stays open for the step.
- **A hand-off that did not finish (review, P2).** The streaming seam tells the record in its `finally` whether the sub-run ended (`handoff_ended`). A hand-off that failed, timed out or stopped joins `handed_failed`. It may have written part of the work, so a later write tool still refuses. The answer is then `HANDED_FAILED_WRITE_REFUSED`. It tells the model to say that the work did not finish and what was done, and not to try again in this turn. So the answer does not send the model back to the failed hand-off. The seam reads the mail mark and the working set inside its `try`. So a time-out before the sub-run starts still reaches the `finally`.
- **Why both directions are narrow (review, P1).** Each direction holds only while the specialist tool of the owner stays in the run (the owner is not in `PackRun.replaced`). Where a pack took that tool away, a hand-off reaches the owner only for the work of a pack that the run cannot hold. Examples are the setup route of the email rules and `call_agent` after `LOAD_NO_PATH`. That work cannot repeat a pack write. A pack call after such a hand-off must run (OA-7i, acceptance 8), and the hand-off after a pack call must stay open.
- **One model step.** MAF runs the calls of one response at once. Each side checks and records with no await between the two. So of a write and a hand-off in one step, the call that starts first runs, and the other refuses.
- **Where it fires: Projects.** It fires when a Projects pack is held and the specialist stays, which is the open gap above.
- **Where it fires: email.** At layer (a) it never fires. A held email pack takes the email specialist tool away, and so does an owner that the loader reaches. So after `query_inbox`, the hand-off of a rule to the email assistant stays open. One rare case is left. When the loader cannot reach the owner, for example because an admin turned one email family off, the specialist tool stays. A mid-turn load of the other email pack then binds both directions. In that case a mail read refuses a later email hand-off in the same turn.
- **Where it fires: CRM.** In practice it never fires. `crm-records` is the only CRM pack, so a run that calls a CRM pack tool holds every CRM pack. The older pack refusal then answers first.
- **A read that counts as a write.** No email tool says `read_only=True`, and a read such as `query_inbox` carries the same hints as the write `snooze_email` (`open_world=False` and nothing else). So no rule over the annotations can tell an email read from an email write, and the guard keeps the one read-only mark.
- **A tool that writes only a chat file (review, round 3).** `status_report`, `render_report` and `export_tasks` write a report card, its PDF or a CSV into the chat, and never a Projects record. `tool_packs.CHAT_FILE_TOOLS` names them, and `pack_write_refusal` skips them as it skips a read-only tool. So a structure step after `status_report` still reaches the specialist. A fence keeps the set equal to `CHAT_FILE_WRITERS` of `test_projects_chat_coverage.py` and to the class A Projects tools that are not read-only.
- **The harness count.** `checks.refused_handoffs_of` reads the result of each hand-off call. A result that is a refusal text of `tool_packs` in full counts in `handoffs_refused`, not in `handoffs`. Such a try fails no `no_*_delegation` check, and stop result 11 does not count it. The report shows it on its own row, and stop result 11 names the count. A hand-off that ran still fails. `no_email_delegation` reads the tasks, so a hand-off of mail work that the product refused still fails it.
- **Not done.** The email read tools stay without a read-only mark, because that mark also lets an OA-9c child hold them. The specialist tool with no run queue goes to the batch path, and that path asks no pack rule, as before.
- **Known gaps.** The batch fallback of `call_agent` and `call_agents_parallel` runs `run_agent` when the run has no queue and no relay. It records no end of a hand-off, so a failed batch hand-off reads as finished. A pack chat run always has a relay, so it does not take that path. `handed_failed` is never cleared, so a later hand-off to the same owner that finishes keeps the text of the failed one. Both only refuse.
- **Fence.** `tests/unit/test_pack_no_double_write.py`, and the OA-8i cases of `tests/unit/test_one_assistant_measure_oa8e.py`.

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
| `fanout` | 6 | Five companies and ten invoices, read without a spawn. Five long contracts, with a spawn. Five tasks from a list. One read. Three searches (§6.3) |

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

**OA-8a. GO, narrowed.**
- The count is 9, not 8. The orchestrator `tool_scope` holds 12 of the 13
  shared `crm_*` reads. Add `crm_search_notes` to the pack. Do not add it
  to the orchestrator `tool_scope`, because that changes the tool list
  with the flag off.
- Put no CRM text in the base persona. `persona_for` raises unless the
  persona holds the owner's text once. A new line breaks the byte-identical
  fence of `test_tool_packs_off.py`. Skip the swap for an owner with no
  bullets. Keep the raise for an owner that is present and moved. Put
  the "no CRM agent" line in the CRM rules.
- Run the Mirror drop inside `_pack_tools`. Layers (b) and (c) build after
  the executor drop. Reuse `MIRROR_RECORD_WRITES`.
- Words are whole words. Leave out bare `pipeline`, `deal`, `lead` and `stage`.
- Put `crm-records` after the email packs in `PACKS`, so the cap drops it first.
- The hand-off is refused when the pack loads. Background runs, workflows and
  the CRM app rail stay delegated. The CRM rules carry the specialist's rules.
- Test helpers keyed on `agent-{owner}` map the owner to its directory through
  the registry `local_path`.
- Verify: the command in §4.3.2, "OA-8a".

**OA-8b. GO, narrowed.**
1. The audit checked `6c0f5a83a`. Build after OA-8a. Re-check each anchor on the
   new main. The tool count is 120, and the union of the families is
   `skill_projects.__all__`.
2. Four families, with the owner `projects-assistant`. Each holds 35 tools or
   fewer. The core pack has the surface and the words. The other three packs
   name only their own tools in their rules. The core rules route the rest of
   the work to `load_skill_tools` by pack slug.
3. The first audit kept the specialist tool and set no `delegation_refusal`,
   because a pack with a refusal removes the specialist as soon as one pack
   loads. OA-7i changes that. It reaches an owner only when its packs carry a
   refusal, and it decides for each run. So every Projects pack sets a
   refusal. The persona lines for the owner become the bullet
   `PROJECTS_DELEGATION_BULLETS`. The core rules say that no Projects agent
   exists to hand work to when the loader reaches the owner.
4. The owner builds `FunctionTool` objects (`_StrictTool`). Make the builder
   accept them, keep the class, and cache the 120 tools. Keep
   `additionalProperties: false` and the unknown-argument answer.
5. `Pack.surface` takes a tuple. The core pack serves `/projects`, `/tasks`
   and `/calendar`.
6. A covered org loads no Projects pack. Put the check in the pre-filter slot
   of `_pack_tools`. `_run_covered` fails closed.
7. The words are whole words. Leave out bare `project`, `calendar`, `plan`,
   `report`, `delete`, `reply`, `deal` and `note`.
8. The cap math uses the measured base. The core pack must fit beside `email`
   (31) and the loader (1). Measure it with `_pack_base_count`.
9. Batch tools show one card each, raised inside the tool body. The pack
   wrapper carries `__cc_gated__`, so the assembly gates it once.
10. Non-goals. No sandbox tool joins a pack. The own run of `agent-projects`
    does not change. No flag flips.
11. Verify: the command in §4.3.2, "OA-8b".

**OA-9a. The parser is BUILT (PR #1388). The `worker` half is GO,
narrowed (2026-10-27).** The parser files are `subagent_contract.py` and
`tests/unit/test_subagent_contract.py`. The first audit held the worker half
back until five items had an answer. Each narrowing below answers the item
of the same number. The code anchors are those of `6c0f5a83a`.
1. **The fence file.** PR #1388 moved no case. `test_tool_packs_off.py`
   holds no OA-9a case, and a search of it for `OA-9`, `worker` and `spawn`
   finds nothing. The worker cases go in a new file,
   `tests/unit/test_subagent_worker.py`. §6.7 no longer names
   `test_tool_packs_off.py`.
2. **The open and held sets of the worker.** The worker has no tool of its
   own. Its open set is exactly the tools of its job. Each of them is a
   read tool of the parent run (§6.2). Its held set is the open set minus
   every egress tool. So a held child loses `web_search` and `fetch_page`
   and keeps the rest (`_tool_injection._is_egress`, `:436`). The worker is
   not in `_AGENT_REGISTRY` (narrowing 5). So `_in_repo_agents()` in
   `test_no_egress_tool_audit.py` does not list it, and `EXPECTED_WITHHELD`
   gets no `worker` row. The pin is a case in `test_subagent_worker.py`. It
   builds the worker with jobs of `web_search`, `fetch_page`, `remember` and
   `recall_timeline`. It runs the worker open and held. It asserts that the
   held set equals the open set minus `{web_search, fetch_page}`, and that
   the open set equals the job.
3. **The no-job refusal happens at run, never at build.** `build_agents()`
   of the worker never raises. With no job on the frame it returns an agent
   that has no tool. Two folder fences build every agent of `apps/agents/`
   with no job: `test_agent_voice.py:262` and
   `test_chat_upload_every_agent.py:161`. A raise at build turns both red.
   Each of them also lists the folders of `apps/agents/`, so each gets one
   `worker` row. The refusal is `subagent_contract.worker_refusal(name)`. It
   returns text when the name is internal and the frame binds no job. Three
   callers ask it before any load: `agent_tools.call_agent` (as
   `_pack_refusal` does, `:79`), `_run_sub_agent_streaming`
   (`executor.py:2190`) and `run_agent` (`executor.py:4397`). The batch
   fallback of `call_agent` goes straight to `run_agent` (`agent_tools.py:206-213`).
   So the streaming seam alone is not enough.
4. **The `config.json` of the worker.**
   - `"name": "worker"`, `"runtime": "maf"` and `"model_tier": "tier-fast"`.
   - `"app": "chat"`. `test_usage_attribution.py:366` needs a slug that
     the navigation shows, and the orchestrator uses `chat`.
   - `"sharing"`: `shared`, `organization`, not shareable, outputs at the
     instance. As for `projects-assistant`. D85 then withholds the shell
     tools (`_withheld_shell_tools`, `_tool_injection.py:239`).
   - `"floor_opt_out"`: 20 names. These are `FLOOR_OPT_OUT_ALLOWED` (22
     names) without `web_search` and `fetch_page`. The two web tools stay
     in the floor. An opted-out name leaves the scope even when the job
     names it (`_tool_injection.py:2315`). The 20: `call_agent`,
     `call_agent_background`, `call_agents_parallel`, `code_task`, `decide`,
     `get_errors`, `get_workflow_run`, `list_integrations`,
     `list_workflows`, `load_artifact_kit`, `load_design_system`,
     `manage_todo_list`, `package_artifacts`, `recall_notes`,
     `run_diagnostics`, `run_script`, `run_workflow`, `save_note`,
     `share_artifact` and `write_artifact`.
   - `"tool_scope"`: `["web_search", "fetch_page"]` as the safe default.
     The scope that counts is the job's tools. The executor reads the job
     at each place that reads `tool_scope` for a sub-run or a batch run
     (`executor.py:2333`, `:4371` and `:4925`). ⚠️ An empty list does not
     work, because `loaded.config.get("tool_scope") or None` reads `[]` as
     "inject every tool".
   - No `own_tool_scope`.
   - Three floor tools stay in every run: `ask_questions`,
     `emit_generative_ui` and `read_attachment` (`_FLOOR_KEEP`,
     `_tool_injection.py:115-126`). No opt-out removes them. OA-9b
     narrowing 1 removes them.
5. **How the picker hides the worker.** The worker is in a new list,
   `_INTERNAL_AGENTS`, beside `_AGENT_REGISTRY` in
   `gateway/routes/agent.py:610`. It is in no other list. A flag on the
   registry entry would need a filter in each reader. There are more than
   14 readers, among them `list_agents` (`:1640`), the specialist tools
   (`orchestrator/agents.py:775-781`), `observability.py`, `workspace.py`
   and the workflow catalog. A reader that forgets the filter would show the
   worker. A list that no reader reads cannot do that. Only the three
   executor lookups that load an agent by name read it: `executor.py:2205`,
   `:4675` and `:4760`. They search `_INTERNAL_AGENTS` first. So a dynamic
   agent named `worker` cannot take its place. `_KNOWN_AGENTS` does not
   name it. §6.7 needs no skip in the specialist list.
   Fence: `test_subagent_worker.py`. It asserts that the name is in none of
   `_AGENT_REGISTRY`, `_KNOWN_AGENTS`, the output of `list_agents` and the
   specialist tools. It also asserts that a dynamic `worker` entry does not win.

**OA-9b. GO, narrowed (2026-10-27). Start after the worker half.** Each
narrowing answers the item of the same number.
1. **Read tools only, by an allow-list pair.** `emit_generative_ui` says
   `read_only=False` (`tool_annotations.py:124`). `ask_questions` says
   `read_only=True` (`:60`), but it asks the member. Neither can leave the
   floor (`_FLOOR_KEEP`). The pair of `tool_guard` keeps them out:
   `WithholdTools(foreign)` and `RefuseTools(foreign, answer)`. The test
   `foreign` is "the tool name is not in the job's tools". The model never
   sees such a tool, and a call to it gets the answer and does not run.
   The code session uses the same shape (`code_session.py:349-366`,
   `_session_tool_pin`). The worker's `build_agents()` passes the pair as
   `middleware=` to its agent, as `build_code_task_agent` does (`:381`).
   The pair is the control that counts. It does not depend on the parser or
   on the opt-out. It also covers what arrives after both. Examples are a
   granted app tool (`agents:*`), an MCP tool and a tool that `add_tools` adds. The
   parser (`parse_jobs`) and the opt-out stay as the first and second
   layer. `read_attachment` is also outside the allow-list. A child has no
   attachment, and its job is self-contained.
2. **Four model rounds.** The worker's client takes
   `function_invocation_configuration={"max_iterations": 3,
   "max_duration_seconds": 120.0}` (the same argument as
   `code_session.py:345`). MAF defaults to 40 rounds
   (`DEFAULT_MAX_ITERATIONS`, `agent_framework/_tools.py:104`). After
   `max_iterations` rounds, MAF makes one more call with `tool_choice` set to
   `none` (`_tools.py:4990-5010`). So 3 rounds and the final call make 4
   model calls. The constant is `MAX_CHILD_ROUNDS - 1`. The hard stop is
   `asyncio.wait_for(..., JOB_TIMEOUT_S)`, because MAF checks the duration only
   after a batch of tool calls. Fence: a scripted child that always calls a
   tool makes 4 model calls, and the fourth has no tools.
3. **A job that names `packs`.** `parse_jobs` already refuses it
   (`subagent_contract.py:251-254`), with the case
   `test_a_job_that_names_a_pack_does_not_run_until_oa_9c`. OA-9b calls
   `parse_jobs`, runs only `plan.jobs` and adds `plan.notes()` to its
   answer. The worker ignores `packs`. The case stays green.
4. **The main assistant only.** `spawn_subagents` is an own tool of the
   orchestrator, in `core_tools` (`orchestrator/agents.py:1046`). The
   precedent is `show_in_app` (`:1045`). It is not in
   `_collect_injectable_platform_tools`, which feeds every agent. So §6.7
   does not list `_tool_injection.py` for the injection.
   `SPAWN_SUBAGENTS_ORGS` is a list of orgs. So the executor drops the tool
   from a run of another org, with `_drop_own_tools`
   (`_tool_injection.py:1830`). The tool joins `egress.DELEGATION_TOOLS`.
   `orchestrator/agents.py:757` also registers it as a platform callable,
   as it does for `delegate_to_agent`. Then a held chat
   keeps it, and each child it starts binds `no_egress`. Without the entry,
   the fail-closed rule would withhold it in a held chat
   (`egress.is_egress_tool`, `:1158`). Fence: no file of
   `apps/agents/*/agents.py` names `spawn_subagents`. The orchestrator
   held set of `test_no_egress_tool_audit.py` is the same with the flag on.
5. **The batch-write line waits for OA-8b.** The orchestrator holds no
   batch write tool today (`core_tools`, `orchestrator/agents.py:1046-1056`).
   Its batch tools are in `projects-assistant` and `email-assistant`. The
   line "A batch write goes through the batch tool of the app, in the main
   assistant" moves to OA-8b. OA-9b asserts only that a child cannot write.
6. **Keep these tests green:** `test_delegation_no_egress.py`,
   `test_read_tool_paging.py`, `test_chat_hardening.py`,
   `test_core_tool_floor.py`, `test_floor_opt_out.py`,
   `test_copilot_specialist_refused.py`, `test_background_ai_member.py` and
   `test_tool_schema_diet.py`. Add `test_no_egress_tool_audit.py`,
   `test_egress_card_bound_pin.py`, `test_show_in_app.py`,
   `test_agent_voice.py`, `test_chat_upload_every_agent.py` and
   `test_usage_attribution.py`.
7. **SUB-1.** A child cannot write memory, send out, create an automation,
   ask the member or delegate. §6.8 holds the policy and its fence,
   `tests/unit/test_subagent_blocked_tools.py`.

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
- **OA-7i.** With the flag on and the loader offered, a turn with no pack held has no `email_assistant` tool. A hand-off to the email assistant answers a refusal that names `load_skill_tools`. A pack that cannot load keeps the specialist. So does an MCP server that may attach. Fence: `tests/unit/test_loader_replaces_handoff.py`.
- **OA-7h.** On the 18 email turns, 3 runs each, with the tier flags of production, the pack arm passes at least as many turns as delegation. From turn 2, each email chat sends through the held card. No act shows two cards.
- **OA-8a.** Each CRM write asks its card in the stream of the orchestrator. Mirror mode drops the 3 record writes. A held pack drops the 4 writes. Fence: `tests/unit/test_pack_guards.py`.
- **OA-8b.** The four Projects families together equal `skill_projects.__all__`, and each holds 35 tools or fewer. A pack tool keeps its strict arguments. In a covered org, no Projects pack loads. A batch tool shows one card with its counts. The core pack fits beside the `email` pack at the measured base. Fences: `tests/unit/test_projects_packs.py` and `tests/unit/test_tool_packs_registry.py`.
- **OA-8c.** The held set of the `whatsapp` pack equals the held set of its owner. Fence: `tests/unit/test_pack_guards.py`.
- **OA-8d.** Optional. OA-8e shows that the turns of the `integrations` pack lose nothing.
- **OA-8e.** A report in `docs/` gives the numbers of §4.3.3 for the three arms. None of the stop results of §4.3.3 occurs. OA-10 starts only then. Phase 1 (the harness) is built, and phase 2 ran live (not met, see the header): fence `tests/unit/test_one_assistant_measure_oa8e.py`. The live run is phase 2.
- **OA-9a.** The contract parser accepts good items, and it turns other output into one `inferred` item. The `worker` refuses with no job, and the picker and the specialist list do not show it. Its open set is its job, and its held set is the job without the egress tools. Fences: `tests/unit/test_subagent_contract.py` for the parser, and `tests/unit/test_subagent_worker.py` for the worker.
- **OA-9b.** Five jobs run at once, and a sixth gets a name and does not run. Stop during a fan-out of 3 cancels all 3. A child holds only read tools, no tool that its parent lacks, and it is held in a marked chat. A child stops after 4 model calls. Only the main assistant holds `spawn_subagents`. Fence: `tests/unit/test_spawn_subagents.py`.
- **SUB-1.** A child cannot call a memory write, a send, an automation tool, a tool that asks the member or a delegation tool. Each of them gets its own case. Fence: `tests/unit/test_subagent_blocked_tools.py` (§6.8). It is part of the OA-9b gate.
- **OA-9c.** A child with the `email` pack gets the same held or open set as the main assistant, read tools only. A pack of an owner that reads stranger text builds held for a child (§4.5, "As built, OA-9c"). Fence: `tests/unit/test_pack_guards.py`.
- **OA-9d.** Three children that write show one card with three rows. A rejected row writes nothing, and Stop writes nothing. Fence: a test in the style of `tests/unit/test_delegated_confirmation_card.py`.
- **OA-9e.** The persona lines of §6.3 show with the flag on, and not with it off. In OA-8e, the main assistant spawns for jobs that read long material (OA-9f), and not for light reads, one task or three searches.

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

The registry marks the worker as internal, so it is never a specialist tool or a chat agent. A call of `call_agent("worker", ...)` refuses, because no job spec is bound. §4.5, OA-9a narrowings 3 and 5 say how. The worker sits in `_INTERNAL_AGENTS` and not in `_AGENT_REGISTRY`, and `worker_refusal` answers at run. Several line anchors in §6.1 to §6.6 have moved since 2026-10-26. §6.9 lists the current ones.

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

**The rule (OA-9f, 2026-10-30).** A spawn pays off for one of two kinds of job.
- Each job reads long material, such as documents, contracts, reports or long pages. The text would flood this chat and slow every later turn.
- Each item needs its own chain of several dependent steps.

Light reads are different. The main assistant makes independent short reads or searches itself, in parallel, in one step. This holds at any count. Five pages and ten pages are the same case.

With the flag on, the persona gains `_SPAWN_BLOCK` (`orchestrator/agents.py`). It says to spawn for the two kinds of job. It says not to spawn for independent short reads or searches, at any count. It keeps "one narrow aim" and the items as data. It stays within 90 words.

**The evidence (OA-8e run 3, `docs/one_assistant_measure_oa8e_run3_2026-10-29.md` §4).**
- `fan_companies`: five companies, one page read each. With the old cue (four or more reads) the model spawned in 3 of 3 turns. The turn took 30.7, 33.9 and 32.9 s. The parent spent 7 to 10 s to write five job specs (550 to 950 output tokens). Each child took 2 or 3 model rounds. The parent then spent about 10 s to join the items. Without a spawn the parent fetched the five pages in one parallel step and answered in 11 to 21 s.
- `fan_invoices`: ten invoices, one read each. The model never spawned. When it read the pages itself, the whole turn took 9 to 10 s.
- The fixture pages are small (a child prompt is about 2.3k tokens). So those chats cannot show the gain of a spawn, which is to keep large source material out of the main chat. The chat `fan_contracts` has long text for that purpose.

The orchestrator can already call several tools in one step. The docstring of `spawn_subagents` says it, and the table of §6.6 shows that one step beats a spawn for jobs of one call each.

**Fence.**
- `tests/unit/test_spawn_persona.py` pins the lines with the flag on, their absence with the flag off, and the match with the docstring.
- The `fanout` scenarios of OA-8e check `spawned` for `fan_contracts`, the research chat.
- They check `not_spawned` for five company pages, ten invoice pages, one read, one task and three searches.

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

**The schema check (SUB-1 rule 2).** The parent never reads the raw text of a child. It reads the output of `parse_items`. That parser accepts only the keys `value`, `kind` and `source`, with a `kind` of `quoted` or `inferred`. Any other output becomes one `inferred` item. `SUB_AGENT_MAX_RESULT_CHARS` caps the length of one result (`executor.py:2481`).

**The contract and `head_task`.** `head_task` does not change. Its "(quoted: ...)" marks are the prose form of `kind`. The persona of the worker asks for the JSON form.

### 6.5 Guards inside a child: read first, writes after

**The rule (owner answer 3).** Children read first. Writes come after, in two steps:
- OA-9b ships children that hold read tools only. A batch write goes through the batch tool of the app, in the main assistant.
- OA-9d comes later. It adds children that write, with one batch card for each fan-out.

**Held chats.** A child of a held or marked chat is held (`acb_skills/agent_tools.py:220-243`, `orchestrator/_tool_injection.py:388-414`). Its pack tools take the scope of the owner through the builder of OA-7b. So each guard holds in the child as in the main assistant.

**Web research in a mail chat.** A held child holds no `web_search` and no `fetch_page`. So parallel web research works only in a chat that read no mail. That is the rule for each delegation today.

**Cards.** Each write of a child asks the card of the member. The card shows the exact payload (`agent-crm/agents.py:699-720` gives the rule).

**Batch writes in OA-9b.** A child holds only tools that say `read_only=True`, plus `web_search` and `fetch_page`. Three layers enforce that, and the third is the one that counts:
1. `parse_jobs` refuses a job that names any other tool.
2. The injection gives the worker the tools of the job. The worker config removes the rest of the floor (§4.5, OA-9a narrowing 4).
3. An allow-list pair, `tool_guard.WithholdTools` and `RefuseTools`, takes each tool that is not in the job out of every model request. It answers a call to one and does not run it (§4.5, OA-9b narrowing 1). Three floor tools stay in every run, and the pair holds them back. They are `emit_generative_ui`, `ask_questions` and `read_attachment`.

The main assistant sends a batch write through the batch tool of the app. That tool already shows one card with the counts (`routes/agent.py:758-762`). The main assistant holds no such tool today, so the line waits for OA-8b (§4.5, OA-9b narrowing 5).

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
| OA-9c | Packs in children, through OA-7b | HIGH | After OA-7b. Built 2026-10-29, dark |
| OA-9d | One batch card for each fan-out, and children that write | HIGH | After OA-9c |
| OA-9e | The persona lines and the `fanout` scenarios | LOW | With OA-9b. Persona lines built 2026-10-29, dark. Scenarios not built (§6.9) |

**OA-9a.**
- **Files.** The parser is built (PR #1388). The worker half is:
  - `acb_skills/subagent_contract.py`: add `worker_refusal`
  - `apps/agents/agent-worker/`: `agents.py`, `config.json` and `instructions.md`
  - `routes/agent.py:610`: the new `_INTERNAL_AGENTS` list, with the one `worker` entry
  - `orchestrator/executor.py`: the three lookups by name search `_INTERNAL_AGENTS` first, and the refusal runs in `_run_sub_agent_streaming` and `run_agent`
  - `acb_skills/agent_tools.py`: `call_agent` asks `worker_refusal`
  - `tests/unit/test_agent_voice.py` and `tests/unit/test_chat_upload_every_agent.py`: one `worker` row each
- **Acceptance.**
  - The parser accepts good output, and it turns bad output into one `inferred` item.
  - The worker refuses when no job is bound, at run and not at build.
  - The picker and the specialist list do not show the worker, because it is in no list that they read.
  - The open set of the worker is its job. The held set is the job without the egress tools.
- **Fence.**
  - `tests/unit/test_subagent_contract.py` (the parser)
  - `tests/unit/test_subagent_worker.py` (new). It checks the refusal with no job, the picker lists, the pinned sets and the `config.json`
  - No case in `tests/unit/test_tool_packs_off.py`. It holds no OA-9a case.

**OA-9b.**
- **Files.**
  - `acb_skills/agent_tools.py`: `spawn_subagents` and `_fan_out`
  - `orchestrator/agents.py:1046`: the tool joins `core_tools` of the orchestrator only. `_tool_injection.py` gets no edit for the injection. The executor drops the tool from a run of an org that the flag does not name.
  - `acb_skills/egress.py`: `spawn_subagents` joins `DELEGATION_TOOLS`
  - `apps/agents/agent-worker/agents.py`: the allow-list pair and the round cap
  - `acb_common/settings.py`: the flag
- **Acceptance.**
  - Five jobs run at once. The tool names a sixth job, and does not run it.
  - A job past 120 s gives an error item.
  - Stop during a fan-out of 3 cancels all 3, and no child writes after the Stop.
  - A child holds only read tools, and no tool that its parent lacks.
  - A child of a marked chat is held.
  - A child stops after 4 model calls, and the fourth has no tools.
  - A child cannot call a memory write, a send, an automation tool, a tool that asks the member or a delegation tool (SUB-1, §6.8).
  - No line about a batch write. That line moves to OA-8b, because the main assistant holds no batch write tool today.
- **Fence.**
  - `tests/unit/test_spawn_subagents.py`
  - `tests/unit/test_subagent_blocked_tools.py` (SUB-1, §6.8)
  - The existing tests of `call_agents_parallel` stay green.
  - The test `test_nothing_calls_the_contract_before_oa_9b` in `test_subagent_contract.py` becomes a pin on the one caller, `spawn_subagents`.

**OA-9c. AGENT-SAFE to build (audit of 2026-10-29). The flag flip is OWNER-GATE (`work_plan.md` §6 D4).** Risk HIGH: it touches the egress hold, the untrusted-mail mark and the tool set of a child. Built 2026-10-29 ("As built, OA-9c" in §4.5).
- **Decisions.**
  - **Naming.** The job names its packs with the `packs` key. A child inherits no pack.
  - **Which packs.** Only a slug in `PackRun.holds()` of the parent record at `tool_packs.PACKS_KEY`. The spawn reads it on the parent frame before any child starts. With no record, a job that names a pack does not run.
  - **Which tools.** The result of `_pack_tools`, then only the tools that the risk registry marks read-only. The spawn copies no part of the held rule. So a child holds no write in an open chat either.
  - **The run key.** The spawn mints a fresh key for each child. It is never the key of the parent or of a sibling.
  - **The pin.** The pack tool names join the allow-list pair. The injection scope stays the tools of the job.
  - **The cap.** The tools of the job plus the pack tools must fit `MAX_REQUEST_TOOLS` (110). A child makes at most 4 model rounds.
  - **The hand-off.** A child still holds no delegation tool and no `load_skill_tools`.
- **Done when.**
  - A child of a held chat with `packs=["email"]` holds the held set, less each tool that is not read-only.
  - A child of an open chat holds the open set, read tools only, and no send.
  - A job that names a pack that the parent lacks does not run.
  - A pack tool that is not read-only is absent.
  - A child past 110 tools does not run.
  - A job with no `packs` key runs as in OA-9b.
  - The pin `test_a_job_that_names_a_pack_does_not_run_until_oa_9c` has its replacement.
- **Command.** `uv run pytest tests/unit/test_pack_guards.py tests/unit/test_spawn_subagents.py tests/unit/test_subagent_blocked_tools.py tests/unit/test_subagent_contract.py tests/unit/test_subagent_worker.py -q`, then `uv run ruff check` and `uv run mypy` on the changed files. R8 does not apply, because no SQL changes.
- **Fence.** The child cases in `tests/unit/test_pack_guards.py`, and the spawn cases in `tests/unit/test_spawn_subagents.py`.

**OA-9d.**
- **Files.** `acb_skills/ask_tools.py` (the card group) and the card UI.
- **Acceptance.**
  - Three children that write show one card with three rows.
  - A rejected row writes nothing.
  - Stop writes nothing.
- **Fence.** A test in the style of `tests/unit/test_delegated_confirmation_card.py`.

**OA-9e.** The persona lines and the OA-8e scenarios of §6.3.

### 6.8 SUB-1: the sub-agent policy (Metorite #822)

SUB-1 is the policy that a child obeys in every fan-out. It has three rules. Rules 1 and 2 bind each child. Rule 3 binds a fan-out. The rules sit on the layers of §6.5 and on the caps of the parser. The table says which rule uses which existing part, and which part is new.

| SUB-1 rule | Existing part | New in OA-9b |
|---|---|---|
| 1. A child never writes memory, sends out, creates an automation, asks the member or delegates | `parse_jobs` refuses a tool that is not read-only (`annotated_read_only`), a `MEMBER_TOOLS` name and a delegation name. A delegated run closes the profile writes (`executor.py:2230`) | The allow-list pair of §4.5, OA-9b narrowing 1. It stops the tools that the parser cannot see |
| 2. The parent gets a summary that validates against a schema | `parse_items` (`subagent_contract.py:396`) and the keys `value`, `kind` and `source` | None. `spawn_subagents` returns only parsed items |
| 3. A fan-out cap of 5, one shared step budget, an idle timeout | `MAX_JOBS` 5 (equal to `_PARALLEL_MAX`), `MAX_SPAWNS_PER_TURN` 2, `MAX_CHILD_ROUNDS` 4, `JOB_TIMEOUT_S` 120 and `FANOUT_TIMEOUT_S` 180 | The enforcement of each cap: the spawn counter on the parent frame, `max_iterations` of 3 plus the final call, and `asyncio.wait_for` |

**What each blocked class covers.** The tools below come from the code of `6c0f5a83a`. A child cannot call any of them, in any chat.
- **Memory writes.** The five store writes of `egress.STORE_WRITES` (`egress.py:132`): `save_memory`, `save_episode`, `save_agent_memory`, `save_org_memory` and `save_note`. Also the six profile writes of `profile_tools.WRITE_TOOLS` (`:1111`): `profile_remember`, `profile_forget`, `profile_set`, `profile_alias`, `profile_undo` and `profile_interview`. A read such as `remember` or `recall_timeline` stays open.
- **Egress.** Any tool that sends or writes outside Metorite. The email sends `send_email`, `forward_email`, `unsubscribe_sender`, `send_draft` and `schedule_send`. The CRM writes `create_lead`, `update_deal_status`, `log_activity` and `convert_lead`. The web reads `web_search` and `fetch_page` are not in this class, because they only read. A held chat still withholds them from a child (§6.5).
- **Automation.** `run_workflow` (`workflow_tools.py:94`). The email rule tools `create_rule`, `update_rule`, `create_rules_from_prompt`, `install_default_rules`, `learn_rule_pattern`, `run_rules` and `delete_rule`. The CRM tool `crm_propose_rule`. The Projects tools `set_report_schedule` and `set_recurrence`.
- **Ask the member.** The four tools of `MEMBER_TOOLS`: `ask_questions`, `ask_user`, `request_confirmation` and `share_artifact`. Also `emit_generative_ui`, which blocks the run when it sets `hitl`.
- **Delegation.** The four tools of `egress.DELEGATION_TOOLS`, and `spawn_subagents`. Also `spawn_copilot_agent` and the specialist tools of the orchestrator: `apis_config`, `app_builder`, `crm_assistant`, `email_assistant`, `projects_assistant` and `whatsapp_assistant`.

**Rule 3, as decided.**
- **Fan-out cap.** 5 jobs for each call. This is the cap of `call_agents_parallel` today.
- **Shared step budget.** A bound and not a counter. One child makes at most 4 model calls. One call makes at most 20. One turn makes at most 40, because a turn makes at most 2 spawn calls. No counter pools the steps across children. Each child can use all 4 of its calls.
- **Idle timeout.** The wall clock of the job (120 s) and of the fan-out (180 s). A child is a batch run and streams no event (`executor.py:2587-2622`), so an idle timer would measure the same time. A fan-out that streams its children needs a new timer, and OA-9b does not build it.

**Fence (R7).** `tests/unit/test_subagent_blocked_tools.py`. The file gives a child each blocked tool through the real worker pair, and the child cannot call it. Each case counts the runs of the tool body and asserts the count is 0.
- `test_a_child_cannot_call_a_memory_write`: one case for each of the 11 tools above.
- `test_a_child_cannot_call_a_send`: one case for each of the 9 tools above.
- `test_a_child_cannot_call_an_automation_tool`: one case for each of the 11 tools above.
- `test_a_child_cannot_ask_the_member`: one case for each of the 5 tools above.
- `test_a_child_cannot_delegate`: one case for each of the 12 tools above.
- `test_the_parser_refuses_each_blocked_tool`: layer 1 alone, with the same tool lists.
- `test_the_pair_stops_each_blocked_tool_with_the_parser_off`: layer 3 alone. The parser accepts the tool, the parent holds it, and the pair still answers the call.
- `test_the_floor_tools_that_cannot_be_opted_out_are_withheld`: `ask_questions`, `emit_generative_ui` and `read_attachment` are in the injected set, and the pair takes them out of the request.
- `test_a_job_tool_still_runs`: the control case. `remember` runs, so the pair does not block everything.
- Four mutations must fail the file. The first removes the pair from the worker. The second adds a floor tool to the allow-list. The third empties `MEMBER_TOOLS`. The fourth drops one name from a tool table.

### 6.9 Anchors checked on 2026-10-27

The anchors of §6.1 to §6.6 were set on 2026-10-26, and some have moved. Each one names a symbol, so search by the symbol. These are the current lines at `6c0f5a83a`.

| Symbol | Was | Now |
|---|---|---|
| `_run_sub_agent_streaming` | `executor.py:2012-2530` | `executor.py:2161-2686` |
| The registry lookup by name | `:2048-2060` | `:2204-2216` |
| `head_task` in the sub-run | `:2043-2044` | `:2199-2200` |
| The `finally` of the sub-run | `:2505-2530` | `:2664-2686` |
| `_sub_agent_tier` | `:8743-8764` | `:8945-8966` |
| `run_tools` bound on the parent | `:5743-5745` | `:5938` |
| `call_agents_parallel` | `agent_tools.py:280-435` | `:296-457` |
| `_delegate_held` | `:230-243` | `:246-259` |
| `_answer_of` | `:256-277` | `:272-293` |
| The batch fallback with `head_task` | `:191-207` | `:206-213` |

The cite `orchestrator/agents.py:159`, "rule 3" in §6.3, matches no text in that file now. Look for the rule that says to call independent tools in one step before OA-9e pins the persona lines.

**As built, OA-9e (2026-10-29, dark).**
- **Status.** Built. The persona block shows only with `SPAWN_SUBAGENTS_ORGS` naming the org. No box sets the flag.
- **The cite.** The persona never held the line "call independent tools in one step". Only the docstring of `spawn_subagents` says it (`agent_tools.py`). The new block now says it too, in one clause.
- **Where.** `_SPAWN_BLOCK` in `orchestrator/agents.py`. `_orchestrator_instructions` takes `spawn=True` and puts the block before "THE SHAPE OF A ROUTINE REPLY." The agent build reads `_spawn_subagents_enabled(organization_id)` once, so the block and the tool agree. With the flag off the persona is `_PULL_INSTRUCTIONS`, byte for byte.
- **Split.** The persona carries the when. The docstring carries the how (the job keys, the tools, the tiers). The block does not copy the docstring.
- **Fence.** `tests/unit/test_spawn_persona.py`. It pins the lines on, the byte-identical persona off, the length cap of 90 words, and the match with the docstring.
- **Not built.** The `fanout` scenarios of OA-8e, which need a live model.

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
   SPD-1 (Metorite #1126) adds `executor.setup_steps_ms`. A native streamed
   run logs it once, just before its stream opens. Each field is the ms of
   one setup step, measured from the step before, and `total_ms` is their
   sum. So the line also counts `session_load` and `compose`, which come
   after `run_setup_ms`. Fence: `tests/unit/test_setup_laps.py`.
4. **OCR.** OA-3 adds time to `read_attachment`. It runs off the event
   loop with a cap, so it must not block other runs.
5. **OA-1 adds tokens.** A task now holds the member's words. Expect a
   small rise in the input tokens of a delegated run.
6. **Early setup reads (SPD-1 part 2).** In production, 53% of chat turns
   came more than 5 minutes after the turn before. So three setup reads were
   cold, and they ran one after another. A streamed run now starts them
   together, after the access check and beside the agent load
   (`executor._EarlyReads`). They are the knowledge index, the mailbox list
   and the CRM facts. Each read keeps its own rule, and each task reads in
   the run's own context. The run awaits the two cache fills before the
   first reader of the caches. `_add_crm_facts_block` uses the early facts
   in place of its own read, and only when its rule says to read. The
   `finally` cancels a read that the run did not await. Fence:
   `tests/unit/test_early_reads.py`.

HANDOFF **H-412** carries these points to that session.

### 7.1 CTX-1: a large tool result as a preview and a handle

Metorite #825. **Status:** BUILT, dark behind `TOOL_RESULT_SPILL` (default `off`).
Risk: MEDIUM. The untrusted-mail guard and the number check must keep working.

**Why.** A large tool result goes into the prompt in full. A run makes 4 to 9
model calls, and each call sends that result again. Production has
`MAF_NATIVE_SESSIONS=true`, so the stored session also carries the result into
each later turn. A preview cuts the input tokens, so each call is faster.

**Design.**

1. One per-run `FunctionMiddleware`, `SpillToolResults`, runs for every tool of
   a native MAF run (core, injected, pack and MCP). The seam is
   `_auto_invoke_function` in MAF. The Copilot path and the Tier-2 shim are out
   of scope.
2. The store is an in-process LRU with a TTL of 30 minutes and 128 entries. It
   is NOT the WS-60 artifact store, because a lasting copy of mail breaks the
   rule that `test_mail_copies_in_chat_stores.py` fences. The key is the run's
   org, member and thread plus a ref such as `tr_3f9c0a1b`. The run binding
   gives all three. No tool argument gives any of them. An expired ref answers
   "not found" and tells the model to call the tool again.
3. A result above `TOOL_RESULT_SPILL_CHARS` (24000) reaches the model as a
   preview. The preview is the first `TOOL_RESULT_PREVIEW_CHARS` (4000)
   chars, cut at a line end, and never less than 2000 chars. Then come the
   END line of each open frame and one footer line. The footer holds the
   ref, the size and the next offset.
4. `read_tool_result(ref, offset=0)` returns pages of 16000 chars. With the
   setting `on`, it joins the core list of the orchestrator for the whole
   run. So the tool list stays the same across the turns of a chat. With
   `off` and `shadow`, the run gets no new tool. In `on`, an agent that
   does not hold the tool gets no spill.
5. `shadow` changes nothing that the model sees. It logs one
   `tool_result.size` line per result. The line holds the tool name, chars,
   tokens (chars/4), the source (`mcp`, `pack` or `core`), the untrusted flag
   and `would_spill`. It holds no text.

**Guards.**

- **Untrusted mail.** An entry counts as untrusted in four cases. Its text
  has a marker shape. Its tool is an MCP tool. Its pack owner is in
  `UNTRUSTED_INPUT_AGENTS`. Or `session_is_marked()` is true. A failed check
  counts as marked. A read of an untrusted entry first calls
  `mark_untrusted_answer()`, so the chat stays on hold. Then the read puts
  each page in a fresh `UntrustedFrame`.
- **Number check.** `_apply_tool_result_spill` runs before `_apply_no_egress`
  and `_apply_answer_check`, so the spill middleware is outermost.
  `RecordToolResults` still reads the full text before the spill replaces it.
- **Exempt.** `narrow_and_read` (it bounds its own output), the delegation
  tools (they have their own cap), `ask_questions`, `request_confirmation` and
  `read_tool_result` never spill.
- **UI.** The stream cuts each tool result to 2000 chars. The preview keeps
  those chars as they were, so no card changes.

**Settings** (`acb_common/settings.py`, one reader for each):
`TOOL_RESULT_SPILL` (`off`, `shadow` or `on`), `TOOL_RESULT_SPILL_CHARS` and
`TOOL_RESULT_PREVIEW_CHARS`. A value on a box is the grant `enforcement-flip`.

**Acceptance.**

1. `off`: the request bytes do not change. The run gets no tool and no middleware.
2. `shadow`: one `tool_result.size` line per result, with no text. The request
   bytes do not change.
3. `on`: a 100K MCP result reaches the model as 4.3K chars or less.
   It carries the ref. Each later call of the run is 24K chars smaller.
4. `read_tool_result` returns the next page. Another org, member or thread
   gets "not found".
5. A framed result: the preview closes its frame. Each page has a fresh
   frame. A read marks the session.
6. No new `agent_blob` or `artifacts` row.
7. A CRM result above the cap still feeds the number check.
8. `narrow_and_read` and delegation results never spill.
9. The first 2000 chars of `TOOL_CALL_RESULT` do not change.

**Fences.** `tests/unit/test_tool_result_spill.py` holds each item above. It
must stay green with `test_crm_answer_check.py`,
`test_crm_answer_check_executor.py`, `test_untrusted_frame.py`,
`test_mail_copies_in_chat_stores.py`, `test_delegation_no_egress.py`,
`test_narrowing_one_seam.py`, `test_native_session_persistence.py`,
`test_read_tool_paging.py`, `test_sub_agent_result_cap.py`,
`test_tool_packs_off.py`, `test_core_tool_floor.py` and
`test_tool_schema_diet.py`.

## 8. Out of scope

- An image-reading model for drawings. The owner chose OCR first.
- WhatsApp (D115).
- A change to the held-chat guard itself.
