# TOF-5a

Tier: HIGH

Gate: AGENT-SAFE, dark. Task 3423, epic 3393 (WS-TOF, D224 rung 5). No migration. The flip is OWNER-GATE (`enforcement-flip`), after the owner preview (D145)

## What

🔨 **BUILT, dark (2026-11-18, task 3423): the slice TOF-5a-1.** TOF-5a-2 is not built. **An approved MCP tool can be a skill action.** Today `skill_packs.action_tool` gives None for each id with a colon. So `mcp_actions.py` sets `"tool": None` for an `mcp:<server>.<tool>` row, and `build()` marks it `action_unserved`. A skill can name an MCP row, and no chat tool runs it.

This row is the narrowed slice **TOF-5a-1**, the build-time half. **TOF-5a-2**, the run-time check after the attach and the progressive-disclosure handling, comes after CSY-1 and CSY-5 merge.

**The map.** `action_tool` stays pure, and it stays None for an `mcp:` id in the catalog. `build()` gets one more pure input, in the shape of `chat_tools`. It is a map from an `mcp:<server>.<tool>` id to the local tool name `<server>_<tool>` (MAF builds it from `tool_name_prefix`, `_maf_mcp.py`). An id that the map does not hold gives the reason `action_unserved`, as today.

**Who computes the map.** `_turn_with_skills` in `_tool_injection.py` computes the map once for a turn. It does so only when a skill of the turn names an `mcp:` action. It runs inside the `tenant_session` of the turn. Its inputs are the approvals, the cached tool list and the lookahead of each server (`_mcp_may_attach`).

**"Approved".** The org approved the tool when a row of the B1b-3 table exists AND its hash equals `tool_text_hash` of the current cached tool text (`mcp_actions.tool_changed`). A row alone is not enough. A tool whose text changed since the approval is not approved. A fault of the approval read gives no map, so no `mcp:` action is served (fail closed).

**"Reachable for the member".** The lookahead holds when the org is on `MAF_MCP_ORGS`, the server is enabled, its scope names the agent, its transport is `http` or `http-sse`, and the member's grant `integrations:use:mcp-<server>` passes. The check for the email pack comes after `choose`, so it sits in `_skills_present`, and not in `build()`. A skill that holds an email pack and names an `mcp:` action never runs (OA-7b, no run holds a mail pack and an MCP tool). It gets the refusal reason `mail_and_mcp`.

**The CSY rules.** A member never learns of a server that they cannot use. Each refusal of this slice writes a log line with the server name and the reason, and it adds NO text to the model. The member's chat answers as if the skill did not exist. The email-pack mark stays. A marked chat still asks the card for each MCP call (step 3a of `decide()`).

**The description stays untrusted.** `rules_text` of a skill never holds the description of an MCP tool. The tool text reaches the model only through the existing MCP path, under its own frame (`DESCRIPTION_LABEL`).

**The cards.** This slice adds no card and removes none. The card rules are the rules of `decide()` today: "always confirm" asks, step 3a asks, a run with no member asks. A member's run with no such switch calls a write tool with no card (MCP-NC1, owner, 2026-11-16, `work_plan.md`). The task text says "a write tool keeps its card". It predates MCP-NC1. This row follows MCP-NC1, and it does not reopen it. The owner may say otherwise on task 3423. Then the change is a new rule and an amendment of MCP-NC1.

**The flag.** No new flag. The `mcp:` action is served only when the org is on `STUDIO_CHAT_TOOLS_ORGS` AND `MAF_MCP_ORGS`. With either flag off, an `mcp:` id gives `chat_tools_off`, as a `tool:` id does. Both flags are empty by default, so the slice ships dark.

**When the lookahead passes and the connect fails.** The turn keeps the rules of the skill for a tool that the run lacks. This slice accepts that gap and logs it (`skill_mcp_unattached`). TOF-5a-2 closes it.

**The tool-token budget.** The slice adds no tool schema and no chat tool. The skill rules name the local tool name only, inside `MAX_RULES_TOKENS`. The pinned budgets do not move: `FIND_SCHEMA_TOKENS`, `RUN_SCHEMA_TOKENS`, `SERVER_SCHEMA_TOKENS`, `DIRECT_TOOLS_MAX`, `MAX_PACK_TOOLS` and the Studio cap that `test_crm_email_tool_refusals.py` pins.

**Not in this slice.** `button_tools` keeps `mcp` unserved, because a view button runs through the door and not through the chat. The Studio save check (`routes/studio/skills.py`) is not changed. It accepts any catalog id, so a member can still save a skill that names an unapproved MCP tool. That skill fails at build, with the reason `mcp_not_approved`. The catalog lists MCP rows of the whole org, with no filter by the member's grant on the server. That is a CSY finding, and it goes on the board.

## Done when

With both flags on for an org, a skill that names an approved MCP tool of an attached server runs that tool in the chat. The tool is `<server>_<tool>`, and it passes the gate of `McpPermissionGate` as before. With a tool that is not approved, a server that the member cannot reach, or an email pack in the turn, the skill does not run, and the chat shows no word of it.

Fences, named before the build (R7):
- `tests/unit/test_skill_packs.py`: an unapproved MCP tool fails the build with `mcp_not_approved`. A tool whose text changed after the approval fails too. An id that the map lacks is `action_unserved`. `rules_text` never holds the MCP description. With the flags off, an `mcp:` id gives `chat_tools_off`.
- `tests/unit/test_skill_packs_r8.py`: an approval of org B never serves org A.
- `tests/unit/test_skill_mcp_reach.py` (new): a server that the member's grant refuses drops the skill and adds no text. The email pack gives `mail_and_mcp`. A fault of the approval read gives no map.
- Regression: `test_studio_mcp_approvals.py`, `test_studio_mcp_approvals_r8.py`, `test_studio_mcp_catalog.py`, `test_studio_mcp_tools.py`, `test_maf_mcp_confirm.py`, `test_maf_mcp_injection.py`, `test_maf_mcp_budget.py`, `test_pack_guards.py`, `test_load_skill_tools.py` and `test_crm_email_tool_refusals.py`.

Verify: `bash scripts/dev_db.sh`, then `eval "$(bash scripts/dev_db.sh --export)"`, then `uv run pytest` on the files above.

## Build notes (TOF-5a-1, 2026-11-18)

1. **Two pure inputs.** `build()` takes `mcp_tools` and `mcp_unapproved`. No map (None) means that a flag is off, and an `mcp:` id gives `chat_tools_off`. The map must give the name of `skill_packs.mcp_local_name`, or the id gives `action_unserved`.
2. **The local name.** `mcp_local_name` gives `<server>_<tool>`. MAF trims a `_` or a `-` at the start of a tool name. So such a name gives no local name, and the skill fails closed. A parity test reads the MAF rule.
3. **The order of the reads.** The approvals come first, in the tenant session of the turn, in a savepoint. Then one lookahead (`_mcp_reach_rows`, the rules of `_mcp_may_attach`), then one cache read for the reachable servers only (`mcp_tools.skill_tools`). A tool of a server out of reach gives `action_unserved`, never `mcp_not_approved`.
4. **A tool that the cache does not hold** is not approved, because no current text exists to hash.
5. **No grant check, no server.** A run whose `is_authorized` is None reaches no server for a skill, so the member's grant always counts.
6. **Each fault gives no map**: the approvals read, the lookahead and the cache. `_mcp_may_attach` still reads a lookahead fault as True for its own use.
7. **The email pack.** `_skills_present` reads the agent's own tools (`_holds_untrusted_pack`) and the builds of the turn. A skill can bring the email pack through a `tool:` action and also name an MCP tool. That skill fails with `mail_and_mcp`, and the brought pack leaves.
8. **The log lines.** `executor.skill_mcp_refused` names the server and the reason (`mcp_unreachable`, `mcp_not_approved`, `approvals_unread`, `lookahead_failed`). `executor.skill_refused` keeps its reason. `executor.skill_mcp_unattached` names the skill, the version and the server, after the attach of the stream run.
9. **A first-party name is reserved (review P1).** A local name can equal a first-party tool, for example `crm_update_record` from a server `crm` with a tool `update_record`. MAF then drops the whole server for the clash. The skill rules would then send the model to the first-party tool. So such an id never serves, and the build gives `mcp_name_reserved`. The reserved set is `skill_packs.reserved_names` and the CSY-10 set of the run (`_reserved_tool_names`).
10. **An MCP name brings no pack.** `pack_needs` leaves each MCP local name out. `_log_skill_mcp_unattached` counts only a function of `agent.mcp_tools`.

## Open owner questions

1. **Does a write MCP tool that a skill brings show its card in a member's run?** The default is the rule of MCP-NC1: no card, unless "always confirm", step 3a or a run with no member says so.
2. **Which text counts at run time?** The default is the cached text that the approval hash covers. The live `tools/list` of MAF may differ, and TOF-5a-2 decides it.
