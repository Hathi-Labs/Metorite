# acb_skills -- Agent Loading and Skill Management

History of this file is in git (git log -p).

## Purpose

acb_skills provides the Dynamic Agent Loader. It clones agent repos from GitHub,
syncs local folders and initialises local git tracking. It injects integration
credentials, imports agents.py at runtime and manages the persistent clone cache.

## Local Contracts

Each item names a module, its rule and its fence. Where an item says ONE, do
not write a second copy of that seam. A bare test file name is under
`tests/unit/`, and a bare spec name is under `project-docs/specs/`.

1. loader.py -- `load_agent()` is the main entry point. It supports a GitHub `repo_url` and a `local_path`.
2. integrations.py -- `build_integrations()` resolves credentials from the Integration Registry.
3. agent_tools.py -- `call_agent`, `call_agents_parallel` and `call_agent_background` for cross-agent delegation.
4. web_tools.py -- `web_search` (DuckDuckGo) and `fetch_page` (Jina Reader). It needs no credential.
5. write_artifact.py -- the `write_artifact` tool, which surfaces created files in the UI.
   - The chat shows a card for each file, so the tool texts tell the model to paste no link. Fence: `test_artifact_link_prompts.py`.
   - It owns the run's artifact context, the ContextVar seam that every workspace tool reads through `artifact_context()`. Never add a module-level dict for run state.
   - A tool with no context fails closed, and never falls back to the cwd or a temp dir. Fence: `test_h201_run_context.py`.
   - In a shared agent's tenant dir, `outputs/` and `inputs/` are the thread's own folders. `agent_paths.is_other_thread_rel` is the ONE rule for another chat's folder. Fence: `test_h227_thread_scope.py`.
   - `agent_paths.is_blocked_rel` is the ONE secret-name rule. Fence: `test_package_artifacts.py`.
5a. skill_families.py -- the skill-family registry (spec `skills_registry.md`).
   - `SKILL_FAMILIES` must cover EVERY tool that `orchestrator._tool_injection` injects, each in exactly ONE family. The `core` family equals `_CORE_STANDARD_TOOL_NAMES`. Fence: `test_skills_registry.py`.
   - This module never imports the orchestrator or the gateway. `build_catalog()` takes its renderer and tokenizer as parameters.
   - A family with an `owner` key is a TOOL PACK (D117). Injection never offers a pack, so a reader of the injected surface calls `injected_families()` or `injected_tool_names()`.
5a-1. tool_packs.py -- what a tool pack adds to its family.
   - `owner_tools(slug, module)` is the ONE rule for the tools of a pack. `packs_on(org)` is the ONE reader of `TOOL_PACKS` and `TOOL_PACKS_ORGS`, and it fails closed.
   - `packs_on`'s one caller is `orchestrator.executor._start_pack_turn` (OA-7c). `test_one_caller_reads_the_flag` names it. Any value of the flags on a box is OWNER-ONLY (`work_plan.md` §6 D4).
   - `begin_turn` counts the member turn and reads the sticky packs from tenant Redis at `cc:<org>:tool-packs:<session>:<member hash>`. A Redis fault gives no sticky pack and writes nothing.
   - `choose` takes the sticky packs, a reference and the words of the turn. It applies a decay of 8 member turns and a cap of 3 packs, 110 tools, and 35 tools a pack. An email word needs an email context: a bare "draft" or "unread" loads no pack.
   - `note_used` makes a pack sticky after one of its tools ran. It writes one hash field, so two calls at once lose no update.
   - `persona_for` swaps the owner's delegation text for the rules, in load order. It raises when the persona does not hold that text once. A held pack adds `egress.EGRESS_HOLD_NOTE_PACK`.
   - `delegation_refusal` is the ONE refusal rule. A hand-off to an owner gets the pack text only while the run holds every pack of that owner (`owner_packs`). `call_agent_background` does not ask it.
   - Fences: `test_tool_packs_choice.py`, `test_tool_packs_off.py`.
   - OA-7e, layer (b): `load_tool` builds `load_skill_tools` for one run. Its description is the `catalog` of the packs that `loadable` gives. `LoadSkillTools` is its `FunctionMiddleware`: it takes its own tool by identity, builds through the orchestrator's builder, and calls `FunctionInvocationContext.add_tools`. `LoadSkillToolsProvider` adds both to one run.
   - A load obeys the caps of layer (a), and a refusal past a cap names `spawn_subagents`. A load alone makes no pack sticky. A pack becomes sticky when one of its tools runs (`note_used`).
   - MCP rule: a load of a pack of an owner that reads stranger text refuses when the live list holds an MCP tool. `_beside_mcp` fails closed. Precedence differs by layer. In layer (a) the email pack wins and the MCP server stays out. In layer (b) the MCP tool wins and the pack stays out.
   - OA-7i: `plan_loader` is the ONE reachability rule of the loader in a run. Its `LoaderPlan` gives the offer and `via_loader`, the owners whose specialist leaves. A hand-off to such an owner answers `LOADER_HANDOFF_REFUSED`. Fence: `test_loader_replaces_handoff.py`.
   - `PackRun.holds()` gives each pack of the run, loads too. `PackRun.refusal` applies `_refusals_for` to them. `_rules_block` is the ONE rules text of both layers. Fence: `test_load_skill_tools.py`.
   - `owner_scope(owner, held)` binds `no_egress` for any value of `held` but `False`. Fences: `test_tool_packs_registry.py`, `test_pack_guards.py`.
5b. decide_tools.py -- the `decide` core-floor tool (spec `customer_console.md` §6A.14). It sends no member and never logs `context`.
   - `decide_tool_enabled()` is its ONE switch. `decide_tool_for(agent_name)` is the one place that picks `system_one_decide` or the Jev `decide`.
   - Fences: `test_decide_tool.py`, `test_system_one_tool.py`.
5b-1. system_one.py -- the `system-one` MAF agent. It holds no tools, and it is not in `_AGENT_REGISTRY`.
   - `ask()` sends up to 20 questions in ONE request on `tier-fast`, in JSON mode. Never send a JSON schema, because the Router answers 400.
   - It sends nothing when `acb_llm.routed.routing_is_on()` is false. It logs no context, question or option.
5b-2. tier_policy.py -- the ONE home of the tier policy (D90) and the ONE reader of `AI_TIER_ROUTING` (`tier_routing_on`, which fails closed).
   - `TierPolicyMiddleware` sets only `options["model"]`. Each `TOOL_HINTS` name must be a real tool. Fence: `test_tier_policy.py`.
5c. permission_policy.py -- the B6 risk-aware handler and the D85 guard `guard_shared_agent_shell`.
   - The guard refuses a shell request unless the artifact context says `host_shell_refused=False`. Fence: `test_shared_agent_shell_tools.py`.
   - `decide()` refuses a read of a BINARY workspace file (`read_binary_file`). ⚠️ It never refuses a name in `_CLI_IMAGE_SUFFIXES`, and a CLI upgrade must check that list again. Fence: `test_chat_nul_persist.py`.
5d. safe_open.py -- the ONE safe opener (spec `maf_coding_engine.md` §7.5 rule B). Every host reader and writer of a dir that a sandbox container mounts opens its paths here.
   - A link at any depth fails the call. Do not open a mounted dir any other way. Fence: `test_sandbox_safe_open.py`.
5e. sandbox_tools.py + tenant_file_store.py -- the sandbox tools (D86, spec §7.4 and §16.3).
   - Only `attach_for_run` hands them out, to ONE run, when `sandbox_broker.covers()` is true for the run's own tenant. They are never injected.
   - A covered run does not hold `WITHHELD_HOST_TOOLS` or `HOST_NETWORK_TOOLS`. Do not write that no data leaves the platform, because a called agent runs outside the sandbox.
   - The store takes only the heads `agent-data/`, `inputs/`, `outputs/` and `.run/`. Put prose for a sandbox-only tool in `addendum.RUN_SECTIONS`, never in `FULL_SECTIONS`.
   - Fences: `test_run_command_tool.py`, `test_maf_code_session.py` (R8), `test_projects_sandbox_tools.py`, `test_generated_addendum.py`.
5f. attachment_text.py + attachment_tools.py -- `read_attachment` (spec `projects_ai_chat.md` §22).
   - `attachment_text` parses bytes only, with no subprocess and no code. Every cap is a module constant there. No regex may run over a whole line of HTML.
   - `attachment_tools` takes the workspace and the thread from `artifact_context()`, and only a file name from the model. `agent_paths.upload_dir_rel` is the ONE rule for where an upload lands.
   - Do not add a second reader of an attachment. `attachment_tools.parse_bounded` is the ONE bounded parse of the process. `read_attachment` is a core floor tool in `_FLOOR_KEEP`.
   - Fences: `test_read_attachment.py`, `test_chat_upload_every_agent.py`, `test_email_attachment_text.py`, `test_attachment_xlsx.py`.
5f-1. attachment_ocr.py + attachment_ocr_worker.py -- OCR of a scanned file (D116, spec `one_assistant_context.md` §4.1), dark behind `ATTACHMENT_OCR` (reader `ocr_enabled()`).
   - Only a caller that can wait gets OCR. `TestTheCallers` fails on a new caller.
   - The child starts as `python -I <path>`, never with `-m`. It imports nothing from `acb_` packages, and at most one child lives in the process.
   - ⚠️ The order of `main()` is the control: Landlock check, `_confine` while ONE thread runs, `_preload`, `_close_limits`, then the bytes. A new lazy thread starts in `_preload`. Fence: `test_attachment_ocr.py`.
5g. egress.py -- the network control on a covered run and every run under it (spec `maf_coding_engine.md` §16.3). It FAILS CLOSED.
   - `is_egress_tool()` is the one test. A tool with no `open_world` annotation is an egress tool, and so is an MCP tool and a `STORE_WRITES` member.
   - A held run keeps a card-bound send only through `is_card_bound_send`, which pins the exact parameters.
   - TRUST IS BY IDENTITY. Register a platform tool with `_register_platform_callable` and a wrapper with `_register_platform_wrapper`. Never follow `__wrapped__`.
   - `session_is_marked` FAILS CLOSED on unknown. Fences: `test_delegation_no_egress.py`, `test_email_chat_reply.py`, `test_egress_card_bound_pin.py`.
5g-1. held_card.py -- the rules of a held send card. `held_card_send_enabled` is the ONE reader of `EMAIL_HELD_CARD_SEND`, and it fails closed. The client mirrors the marks in `sendCard.ts`. Fence: `test_held_send_classes.py`.
5h. tool_annotations.py -- `annotate()` has NO default for `open_world`. Every tool of every in-repo agent states it. `risk_summary_block(own=)` reads the agent's own tool list, never the process-wide registry.
5i. tool_guard.py -- the ONE per-run middleware pair (`WithholdTools`, `RefuseTools`). Do not write a third copy of the pair.
5j. ask_tools.py -- `request_confirmation` is the one HITL card gate, and it fails closed.
   - A tool reads `confirmation_channel_open()` after a denial, or before a card only to choose a narrower act. It never approves a write.
   - `refuse_cards()` is the scope of a run with no card channel. Fences: `test_wac_bot_run.py`, `test_wac_writes.py`.
   - `responder=` binds a card to the run's own member. Any other answer, and an answer with no responder, is a REJECT. The gateway takes the responder from the session. Fence: `test_profile_pz32.py`.
5k. agent_site.py -- the ONE builder of an agent install (spec `box_hardening.md` §5 BH-7).
   - Installs go into `/var/lib/acb-gateway/agent-site` with `uv pip install --target`, never into the shared venv.
   - The gateway APPENDS agent-site to `sys.path`. Never use `site.addsitedir` on it, and never write a `.pth` file or a `sitecustomize` there. Fence: `test_agent_deps_target.py`.
5l. crm_tools.py -- the 13 CRM read tools, the ONE copy (spec `crm_platform.md` §20.10).
   - Each tool reads a `/crm` route as the run's member, and refuses with no member. It opens no database session and takes no SQL.
   - Each path segment goes through a validator. `CLOSE_SHARE` equals `gateway.crm_sync.terms.CLOSE_SHARE`. No word of one org goes in this code.
   - Fences: `test_crm_agent.py`, `test_crm_answer_consistency.py`, `test_crm_answer_consistency_r8.py`.
5l-1. answer_check.py -- the number check of a chat answer that read a CRM tool (spec `crm_platform.md` §18.11.2).
   - The sources are the run's tool results, its CRM call arguments and `member_message`, never the packed prompt.
   - A failed answer gets ONE retry. A check that raises sends the answer unchanged, so it never loses the turn.
   - `orchestrator._tool_injection._apply_answer_check` is the one seam that attaches it. Fences: `test_crm_answer_check.py`, `test_crm_answer_check_executor.py`.
5l-1a. tool_result_spill.py -- a large tool result as a preview and a handle (CTX-1, Metorite #825, spec `one_assistant_context.md` §7.1). Dark behind `TOOL_RESULT_SPILL`.
   - The store is an in-process LRU with a TTL, keyed by the run's org, member and thread plus the ref. It writes no row, so it is not the WS-60 artifact store.
   - An untrusted entry has a marker shape, an MCP tool, a pack owner of `UNTRUSTED_INPUT_AGENTS`, or a marked or unknown session. A read of it marks the session. It frames each page afresh.
   - `orchestrator._tool_injection._apply_tool_result_spill` is the one seam. It runs BEFORE the egress and number-check steps. So the number check reads the full text.
   - `narrow_and_read` and the delegation tools never spill.
   - Fence: `test_tool_result_spill.py`.
5l-2. org_facts_tools.py -- `org_facts(app, query)` and the CRM facts block (spec `crm_platform.md` §18.14.4). It loads with the `crm` skill family only.
   - `crm_answer_question` sends a pick only when this run showed that card and the member clicked it. Fences: `test_org_facts.py`, `test_org_facts_r8.py`, `test_crm_answer_first.py`.
5m. untrusted.py -- the ONE frame for text that a stranger wrote (spec `email_app_master_plan.md` §12.3.13.8).
   - Make one `UntrustedFrame` for each model call. Do not add a tag regex or an exemption to `defang_trusted`.
   - Do not build a `<<<` marker anywhere else. Fences: `test_untrusted_frame.py`, `evals/email_injection/`.
5n. company_search_tools.py -- `search_company`, the assistant's one read over every app (D105, spec `knowledge_base.md` §20.5).
   - It reads as the run's member through `core_tools` only, behind `COMPANY_SEARCH_ENABLED`. Fence: `test_company_search_tool.py`.
5n-a. handoff.py + chat_constraints.py -- what crosses a handoff, and the member's limits of one chat (D116, spec `one_assistant_context.md` §4).
   - `handoff.head_task` is the ONE call of every delegation site. `chat_constraints_enabled` is the one reader of `CHAT_CONSTRAINTS`.
   - A keep stores only whole sentences that the member typed in this turn. Never use `member_message` for it. A drop needs the member's approval on a card.
   - Fences: `test_chat_constraints.py`, `test_handoff_words.py`.
5n-b. subagent_contract.py -- the contract parser of a dynamic sub-agent (WS-67 OA-9a, spec `one_assistant_context.md` §6). `spawn_subagents` (`agent_tools`, dark behind `SPAWN_SUBAGENTS_ORGS`) is its caller, and it also holds the job binding, `worker_refusal` and the `child_pin` pair. The parsers do no I/O. `parse_items` turns other output into one `inferred` item. `parse_jobs` checks the jobs against the parent's `run_tools`. `MAX_JOBS` equals `agent_tools._PARALLEL_MAX`. Fence: `test_subagent_contract.py`.
   - OA-9c: a job names only a pack that `PackRun.holds()` of the parent names, and no record means no pack. `agent_tools._child_pack_tools` builds it through `_tool_injection._pack_tools` on the parent frame, with a fresh run key for each child. `child_may_hold` is the ONE test of a pack tool for a child: the read-only mark, less the SUB-1 names and `DISPLAY_TOOLS`. Do not add a second read-only list. A new tool that draws a card or moves the page joins `DISPLAY_TOOLS`, or the display scan in `test_pack_guards.py` fails. Fences: `test_pack_guards.py`, `test_spawn_subagents.py`.
5n-0. profile_tools.py -- `resolve_person` (spec `personalization.md` §5.4), as the run's member, behind `ASSISTANT_PROFILE_ENABLED`. Fence: `test_resolve_person.py`.
   - PZ-32 (D-PZ-21): `_write_gate` runs first. Then a value that the typed words do not hold asks the ONE card of the write, in the web chat only. `_still_open` checks the run again after the approve.
   - The offer (D-PZ-22): `profile_remember(offer=true)` shows one card, once a chat. `_claim_offer` takes one `SET NX` key in tenant Redis, and a fault offers nothing. Fence: `test_profile_pz32.py`.
5n-1. knowledge_tools.py + knowledge_manifest.py -- `read_page` and `list_spaces` (spec `knowledge_base.md` §8.1). They read `/knowledge` as the run's member and take no SQL.
   - A new Knowledge route needs a row in `acb_common/app_manifests/knowledge.py` (MCP-P1a, D136). `knowledge_manifest.py` re-exports those rows, so the chat and the MCP door read one table. The row names a chat tool or the slice that plans one. It also names a door op, or why the door does not serve the row. `propose_page` and `propose_edit` show one card before they send.
   - Fences: `test_knowledge_tools.py`, `test_knowledge_chat_coverage.py`, `test_kb_proposals.py`.
5o. reports.py -- `make_report`, the ONE report tool of the chat (spec `projects_ai_chat.md` §14.10). It keeps no copy of the checks of `documents.py`.
   - `by_code` is the ONE gate of a report that a tool builds from its own reads. Fences: `test_chat_reports.py`, `test_reports_by_code.py`.
6. artifact_lint.py -- lints agent-generated HTML before it reaches the sandbox. It is advisory only: it never blocks a write and never raises.
   - `CC_CLASSES` mirrors the stylesheet in `SandboxedHtml.tsx`, so register a new `cc-` block in BOTH places. Fence: `test_artifact_lint.py`.

## Work Guidance

### Loading agents
- GitHub agents: git clone (first time) -> git pull --ff-only (later)
- Local agents: _ensure_local_git_repo() syncs source to cache, git init if needed
- Cache at {agents_clone_dir}/repos/{agent_name}/
- agent_dir always points to the cache directory (isolated from source)
- Bot git identity configured automatically (metorite-bot)
- Pull strategy (ADR-022): on rebase conflicts, `_resolve_rebase_conflicts()` asks the tier-3 LLM to merge, then falls back to `--ours`.

### Adding a new injected tool
1. Define the async function in the appropriate module
2. Add it to `_extra_tools` in `orchestrator/_tool_injection.py:_inject_agent_tools()`
3. Add tool guidance to `_build_injected_tools_addendum()`
4. Register it in one family of `SKILL_FAMILIES` (5a), or `test_skills_registry.py` fails
5. Tool must be async and accept simple types (str, dict) for Copilot SDK compatibility
6. Wrap with normalize_tools() for GitHubCopilotAgent compatibility

### Local git tracking
- _ensure_local_git_repo() handles source->cache sync and git init
- _sync_source_to_cache() copies only changed files (timestamp+size check)
- Files in cache not in source are preserved (agent-generated improvements)
- Initial commit serves as rollback baseline

## Verification

- pytest tests/unit/test_acb_skills.py
- Agent loading must work with both GitHub and local_path agents
- Mutation sandbox must be able to mount cache directories

## Child DOX Index

None -- leaf package.
