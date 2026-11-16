# TOF-1b

Tier: HIGH

Gate: AGENT-SAFE. Task 3411, epic 3393 (WS-TOF, D224 rung 1). Demo task 3414. Stacked on TOF-1a (PR 2430). No migration. Dark behind `STUDIO_CHAT_TOOLS_ORGS`, `STUDIO_ORGS` and the pack gate of `tool_packs`

## What

🔨 **BUILT, dark (2026-11-16, task 3411).** **A skill brings the pack of its chat tool.** TOF-1a put the chat tools of the packs in the catalog. A skill could name `tool:create_lead`, but it loaded only in a run that held `create_lead` already. So the skill did nothing in a turn that loaded no CRM pack. Now the skill brings the pack of each chat tool that the run does not hold.

**Decision 1, the map.** `skill_packs.pack_needs(skills, held)` is the ONE map from a skill to the packs that it brings. A tool brings its pack when `chat_tools()` maps it to that pack and the run base (`_pack_base_names`) does not hold it. A door op tool, `run_workflow` and a `studio_record_*` tool keep the F3 rule: the run must hold the tool. A name that two packs hold has no entry in the scope, so it brings no pack. The packs keep the order of the tools, with no repeat.

**Decision 2, the flag.** `_tool_injection._chat_tools_open` reads `STUDIO_CHAT_TOOLS_ORGS` through `flags.chat_tools_allowed`, for the org of the run. It fails closed. `_turn_with_skills` reads it once, and only when the turn has a skill to build. `choose` and the rest of `tool_packs` read no flag.

`skill_packs.build` takes the answer as `chat_tools`, and the default is False. With False, a `tool:` action gives the reason `chat_tools_off`, so the skill fails to load and brings no pack.

**Decision 3, the order.** The packs of a skill come last. They join after the cap (step 4) and the step-aside (step 5) of `choose`, in the room that is left. They never move a sticky pack or a new pack out. The skills go in the order of their slug, as in F3. A skill gets all of its packs or none.

**Decision 4, the cap.** A skill with a missing pack fits when three tests pass:

- The packs of the turn, the missing packs, the skills that fit and the skill itself take at most `MAX_PACKS` places.
- The tools of the request stay at or below `MAX_REQUEST_TOOLS`, with the specialist tools that the packs replace taken out.
- The loader keeps its reach of the Projects owner (decision 11).

A missing pack that the allow list of `choose` does not hold gives `pack_off`, and that test comes first. A skill that does not fit gives `pack_cap`. A skill with no missing pack keeps the F3 rule and takes one place, with one change of decision 11.

What one skill can bring:

- In an org where the loader does not reach the Projects owner before the skill, a skill brings at most 2 packs. That is an org whose allow list does not hold each Projects pack.
- In an org that allows every pack, the loader reaches the owner, and the reach test lowers the limit. Two brought packs and the skill place fill the 3 places, and a brought pack is never spare. So the skill brings at most 1 pack.
- In that org, the one pack fits only in a turn with no code pack, or where each code pack is spare. A spare pack needs `HELD_CARD_WRITES_ORGS` on, and it is a pack of the words or an old sticky pack that no skill needs.
- In that org, the turn must not match a second skill with a lower slug. Such a second skill, of rules only, takes a place first, and the skill that brings the pack then gets `pack_cap`. The `rules_first` case of `test_a_later_skill_never_takes_the_reach_after_a_brought_pack` pins this.

The fence `test_on_a_full_org_a_skill_brings_one_pack_at_most` pins this limit. "Before the flip" holds the decision.

**Decision 5, the layer.** A pack that comes for a skill has the layer `skill` in `Choice.layers`. It is not `words`, so `PackRun.spare` never gives it away for a load. `PackRun.needs` keeps it in the run. It becomes sticky only when one of its tools runs (`note_used`).

**Decision 6, the log.** `_apply_tool_packs` writes one `executor.skill_refused` line for each skill that `choose` refused. The line holds `skill` (the id), `version`, `reason` (`pack_cap` or `pack_off`) and `packs`, the packs that it lacked. It never holds the name, the description, the instructions or the rules. `Choice.skills_refused` carries the refusals, and the apply writes the lines after the last `choose`, before each early return. A refused skill is not in `skills_skipped`, so the golden of the matrix does not change.

**Decision 7, a skill that fails later.** `_skills_present` can still refuse a skill with `tool_absent`. For example, the guarded build can drop its tool. Then the apply removes each build of the layer `skill` that no kept skill needs, by the full map of decision 1. So the run is as it was without that skill.

Known cost: `choice.slugs` still names the removed pack for that turn, so `find_tools` treats it as held. The loader plan reads the builds, so `load_skill_tools` can still offer the pack.

A removed pack that a skill brought back out of the sticky state goes back to `evicted` (`_evict_unheld_skill_packs`). Then `drop_sticky` drops it, and `PackRun.gone` names it, so `left_line` tells the model not to call its tools.

**Decision 8, the pack switch.** The allow list of `choose` already holds the refusal: the packs of the org less the admin toggles, widened by `FOLLOWS`. This slice adds no reader of the pack switch.

**Decision 9, the tool count.** A skill adds no tool of its own. A pack that it brings counts at its full size, less the specialist tools that the pack replaces. A tool that the base holds brings no pack. The skill keeps its one place of `MAX_PACKS`.

**Decision 10, the input.** `choose` takes `skill_packs`, a map from the slug of a skill to its packs. With None or an empty map, the choice is the same as before, byte for byte. The fence of that is the golden matrix, `test_studio_split_matrix.py::test_the_matrix_equals_the_golden_split`. Both calls of `choose` in `_apply_tool_packs` take the same map. The apply makes the map only when the turn names a skill.

**Decision 11, the reach (review P1).** The packs of a skill never make the loader lose an owner of `SPECIALIST_STAYS_UNTIL_REACHED`, today the Projects owner. When `plan_loader` reaches the owner before the skill, it must still reach it with the missing packs and the place of the skill. Else `_skills_of` refuses the skill with `pack_cap`. The test is the one of `_step_aside`, with or without the spare packs that no skill needs. So the step-aside of PJP-2 holds, and the fault of 2026-11-02 does not come back.

Two turns show the rule:

- The chat is docked in Projects, so the core pack loads by the surface. A skill that brings `projects-plan` is refused.
- Both Projects packs are sticky, and the step-aside moves the core out. A skill that needs the core is refused while `HELD_CARD_WRITES_ORGS` is off. With it on, the sticky plan pack is spare, so the core comes back with the layer `skill`.

Once a skill brought a pack, a later skill with no missing pack takes the reach test too. When its place would lose the owner, `_skills_of` leaves it out (`skills_skipped`). So the result keeps the reach in each order of the slugs. With no brought pack, such a skill keeps the F3 rule, and a turn with no map is as before.

A brought pack that left the sticky state at steps 1 to 5 leaves `evicted`. So `drop_sticky` keeps the state of a pack that the run holds. `skipped` does not change, because only two log lines read it, and a brought pack cannot come out of it. A pack that the cap or the step-aside skipped fails the same tests again for the skill.

**Not built.** A match on the words of the member (TOF-1c). The offer for each org (TOF-1d). A trajectory eval of a skill that brings a pack (root `AGENTS.md`, harness rule 3).

## Done when

Take a turn with the flag on and no other pack. The allow list of the org holds `crm-records` and `email`, and not each Projects pack. A skill of the turn names `tool:create_lead` and `tool:send_email`. The turn loads `crm-records` and `email` with the layer `skill`. The persona ends with the rules of the skill, the record names the skill, and `PackRun.needs` holds both packs.

In an org that allows every pack, `choose` refuses the same skill with `pack_cap`. There, a skill with one of the two tools loads its one pack (decision 4).

When the code packs fill the 3 places or the tool cap, the choice is the same as with no skill. Then one `executor.skill_refused` line says `pack_cap`. A pack outside the allow list never builds through a skill, and the line says `pack_off`. With the flag off, the skill fails with `chat_tools_off`. A tool that the base holds brings no pack. A skill that fails with `tool_absent` leaves no pack that came only for it.

Fences:

- `tests/unit/test_skill_packs.py`, the TOF-1b section: one test for each item of "Done when". Items 1 and 6 run with `offer_loader` True and a pack left to load, so the second call of `choose` is the one that counts. A mutation that drops the map from that call fails both.
- The reach fences of decision 11, in `test_skill_packs.py`: `test_a_skill_never_takes_the_reach_on_the_projects_surface`, `test_a_skill_keeps_the_reach_when_the_core_came_by_the_words` (switch off and on) and `test_a_skill_that_brings_back_a_pack_that_stepped_aside` (switch off and on). `test_a_later_skill_never_takes_the_reach_after_a_brought_pack` runs both orders of the slugs.
- The revived pack of decision 7: `test_a_revived_pack_the_run_does_not_hold_leaves_the_sticky_state`.
- The full-org limit of decision 4: `test_on_a_full_org_a_skill_brings_one_pack_at_most`, at a base of 57 and of 40.
- The find tools fence: `test_skill_packs.py::test_find_tools_never_offers_a_pack_that_a_skill_brought`. A pack of the layer `skill` is in `choice.slugs`, so `find_candidates` treats it as loaded and offers none of its tools. The apply passes that slug to `_search_setup` as loaded.
- The matrix fence: `tests/unit/test_studio_split_matrix.py::test_a_skill_pack_has_its_own_layer_outside_the_golden`. It gives the layer `skill` its own matrix row with inline values. The golden file and the old rows do not change.
- The telemetry check: `executor.packs_loaded` carries the layer `skill` in `layers`. No enum, type or validator reads that value. The parser `_layers_of` of `evals/one_assistant_measure/run.py` takes each value of `[a-z_]*`. No golden holds that log line.
- The byte-equal fences, with no skill in the turn: the golden matrix `test_studio_split_matrix.py::test_the_matrix_equals_the_golden_split` holds the choice of F3 when there is no map. `test_exact_tool_count.py` and `test_tool_search_off.py` hold their goldens too.

A flip in production is OWNER-GATE (`enforcement-flip`). Flag reload: `docs/flag_reload.md`

## Before the flip

Items 1, 2, 3 and 5 of "Before the flip" of TOF-1a still block the flip of `STUDIO_CHAT_TOOLS_ORGS`. TOF-1b closes item 4: the flag now gates the load of a `tool:` action.

**The full-org limit (review P1, an owner decision).** In an org that allows every pack, a skill brings at most 1 pack. It brings it only in a turn with no code pack, or where each code pack is spare (decision 4). A skill with `tool:create_lead` and `tool:send_email` gets `pack_cap` there. This blocks the rung 1 demo (task 3414) in a full org.

The demo also needs that no second skill with a lower slug matches in the turn. Such a second skill, of rules only, takes a place first. The skill with the pack then gets `pack_cap`. The `rules_first` case of `test_a_later_skill_never_takes_the_reach_after_a_brought_pack` pins this.

The owner picks one option:

- (a) Accept the limit. A skill then names the tools of one pack only.
- (b) Let a pack that a skill brings count as spare in the reach test, or raise the cap of places for skill packs. This changes the caps of WS-67, so the owner of WS-67 must agree.

The fence `test_on_a_full_org_a_skill_brings_one_pack_at_most` pins the limit, so a change to it must change that test.

**Decision of the owner (2026-11-16, on epic 3393).** The owner accepted option (a). A skill brings at most 1 pack in an org that allows every pack. The rung 1 demo (task 3414) uses a skill with one pack, for example the reads of `crm-records` only. Option (b) stays open as a later slice.
