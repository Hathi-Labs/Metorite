# TOF-1a

Tier: HIGH

Gate: AGENT-SAFE. Task 3404, epic 3393 (WS-TOF, D224 rung 1). No migration. Dark behind `STUDIO_CHAT_TOOLS_ORGS`

## What

🔨 **BUILT, dark (2026-11-16, task 3404).** **The catalog serves the chat tools of the packs.** Before this slice, only Knowledge reached the catalog. CRM, Projects, Email and Calendar gave 0 rows. So the skill offer of F6a could not save a turn that used them (`agent_sandbox.md` §0b, rung 1).

**As built.** A new catalog source `tool` gives one row for each chat tool of the CRM, Email, Projects and Calendar packs. The row id is `tool:<name>`, `source` is `tool`, and `tool` is the chat tool name. `acb_skills.skill_packs.chat_tools` is the ONE scope. It reads `skill_families` at run time, so a new tool of a pack in scope joins with no edit. `skill_packs.action_tool` maps `tool:<name>` to `<name>` for a name in that scope, and to None for each other name.

**Where it lives.** `gateway/studio/chat_tool_actions.py` builds the rows and holds the one class rule. `routes/studio/actions._chat_tool_rows` adds them to `catalog_rows` only for an org on the flag. The org is the bound tenant. No frontend change: `CatalogAction.source` is a string.

**Decision 1, the class rule (fail closed).** No row shows a class lower than the real gate of its tool.

- C for a send of `egress.CARD_BOUND_SEND_TOOLS` or `egress.HELD_CARD_SEND_TOOLS`, whatever its annotation says.
- C when `__tool_risk__` says `destructive`, or when `open_world` is not `False`. A missing key counts as open world (H-236).
- C when the annotation cannot be read. That is an owner that does not load, or a tool with no `__tool_risk__`. It is also a tool that the owner did not build, such as `narrow_and_read` while `NARROWING_AGENTS` does not name the owner.
- A only when `read_only` is `True` and no C condition holds.
- B for each other tool. The email reads say `read_only=False`, so they are B. This slice changes no annotation and no guard.

**How the rule reads.** It reads `__tool_risk__` from the tool object and from its `func`. The egress rule and `pack_tool_guard` read it the same way. It never falls back to a name in `TOOL_ANNOTATIONS`, and it keeps no class table. It does not use `base.tool_class` or `skill_projects.manifest.tool_class`, because they give the LOWEST class.

**The check against the routes.** A test holds each Projects row at or above the highest class of its routes in `skill_projects.manifest`. On main at the build, 203 rows gave 58 A, 89 B and 56 C. No Projects row was below its routes.

**The read of the annotations.** The rows take the tools from THE loader of a pack owner in `orchestrator/_tool_injection.py`. Its two functions are `_pack_owner_module` and `_owner_pack_tools`. The skills catalog (`routes/integrations_skills.py`) uses the same loader. That loader keeps the module and the built tools for the process, and a failed import for 60 seconds. So this slice adds no second cache and no second tool registry.

**A load fault.** An owner that does not load gives class C rows, and a later read tries again. The set of rows comes from the scope alone, so a load fault never adds or drops a row. It only raises a class. So a skill that a member saved earlier still passes the catalog check.

**Decision 2, the pack switch of the org.** The catalog adds no second reader of the pack switch (`test_tool_packs_registry.py::test_one_caller_reads_the_flag`). It lists each tool of the packs in scope for an org on the flag. The refusal stays in the run (TOF-1b). A skill brings the pack of a `tool:` action only when the allow list of `tool_packs.choose` holds that pack. That list is the pack switch of the org less the admin toggles, and a pack outside it gives `pack_off`.

**Decision 3, a dark tool.** A name that no pack in scope holds gives no `tool:` row, and `action_tool` gives None. So `skill_packs.build` answers `action_unserved`, and the skill gives Mira no rule.

**Decision 4, the scope.** The scope is each pack whose owner is the CRM, Email or Projects assistant (`skill_packs.CHAT_TOOL_OWNERS`, an allow list). The Calendar tools are the `calendar-day` pack of the Projects assistant. The slice leaves out `email-setup` (`CHAT_TOOL_LEFT_OUT`), each `studio_` name, and each pack of the Studio builder. That includes `studio-workflows`. A skill must not hold a tool that changes Studio.

**Decision 4, one row for each tool.** A tool that a door op maps already keeps its door op row only. A name that two packs hold gets no row. The fence reads the unfiltered builder and holds exactly one row for each tool in scope, with no golden count.

**Decision 5, security.** The `uses` grammar (`studio_manifest._USES_RE`) has no `tool:` form. So no package `uses` can name a row, `invoke._catalog_class` never reads one, and the action door never runs one. A skill adds no tool of its own. Since TOF-1b, a skill brings the code pack of a `tool:` action, and each tool keeps its own card and guard.

**Known cost.** The first read with the flag on builds the tools of each pack that no run has built yet. On this PC that took about 8 s for the Projects owner. It runs on the event loop, as the price read of the skills catalog does. A later read takes about 25 ms for the 203 rows.

**Not built.** A tool that a skill brings into the run: TOF-1b builds it. A match on the words of the member (TOF-1c). The offer for each org (TOF-1d). The input schema of a row is null. The builder tool `studio_list_actions` lists the rows too, and an app cannot use them.

## Done when

With `STUDIO_CHAT_TOOLS_ORGS` empty, `GET /studio/actions` has no `tool` row. For a listed org, the catalog has one `tool:<name>` row for each tool in scope. Each row has class A, B or C by the rule above, and `tool` equals `action_tool` of the id. A dark tool gives `action_unserved`. No row id parses as a `uses`.

Fences: `tests/unit/test_studio_catalog.py` (`TestTheChatToolSource`), `tests/unit/test_skill_packs.py` and `tests/unit/test_flag_reload.py`. Flag reload: `docs/flag_reload.md`. A flip in production is OWNER-GATE (`enforcement-flip`)

## Before the flip

The Opus review of 2026-11-16 approved the dark merge. These items block the flip of `STUDIO_CHAT_TOOLS_ORGS` or TOF-1b:

1. **Cold build on the event loop.** A cold read took 20 s on the PC review. Warm the owners at start, or give `invoke._catalog_class` and the publish check a catalog with no `tool` source. A thread is not a drop-in repair, because the loader changes process dicts.
2. **Core CRM reads have no row.** The core `crm` family has no owner, so 15 reads (`crm_list_records` and the like) are out of scope. The owner must decide whether they join. TOF-1d needs the answer.
3. **The builder lists the rows.** Filter `source == "tool"` out of `studio_list_actions` for apps.
4. **Closed by TOF-1b. The flag is no kill switch for the load.** `action_tool` reads no flag. `_turn_with_skills` reads the flag and passes it to `build`. With the flag off, a `tool:` action fails with `chat_tools_off`.
5. **Fence gaps.** No test covers a name that two packs hold. The route floor check covers Projects only.
