# acb_skills -- Agent Loading and Skill Management

## Purpose

acb_skills provides the Dynamic Agent Loader -- the subsystem that clones agent
repos from GitHub, syncs local folders, initialises local git tracking, injects
integration credentials, imports agents.py at runtime, and manages the persistent
clone cache.

## Ownership

- Owner: Metorite Core team
- Path: packages/acb_skills/

## Local Contracts

1. loader.py -- load_agent() is the main entry point. Supports GitHub repo_url and local_path.
2. integrations.py -- build_integrations() resolves credentials from the Integration Registry.
3. agent_tools.py -- call_agent, call_agents_parallel, call_agent_background for cross-agent delegation.
4. web_tools.py -- web_search (DuckDuckGo) and fetch_page (Jina Reader). Zero credential.
5. write_artifact.py -- write_artifact tool for surfacing created files in the UI.
   The chat shows a card for each file. So `write_artifact`, `share_artifact`
   and `_DELIVERED_NOTE` tell the model to name the file and to paste no link
   (owner report, 2026-10-16). The result keeps `download_url`. Fence:
   tests/unit/test_artifact_link_prompts.py.
   It also owns the run's artifact context, the ContextVar seam that every
   workspace tool reads through `artifact_context()` (H-201 part 4). Bind it
   with `bind_artifact_context`, change it with `derive_artifact_context`, and
   scope it with `artifact_context_scope` or `enter_artifact_context` and
   `reset_artifact_context`. Never add a module-level dict for run state. A
   tool with no context must fail closed, and never fall back to the cwd or a
   temp dir. Fence: tests/unit/test_h201_run_context.py.
   H-227 (D12): a shared agent's tenant dir has the store key `o:<org>`.
   There, `write_artifact` and `share_artifact` read `outputs/` and `inputs/`
   as the thread's own folders. `_thread_scoped` does it, over
   `agent_paths.thread_scoped_rel`. A thread id that names no folder writes
   nothing. `share_artifact` shows no loose file, one in `inputs/` or
   `outputs/` but in no thread folder. A directory share checks the
   resolved path and skips a link (`_shareable`). `note_tools.save_note` and
   `recall_notes` apply the same rule (`_notes_target`), and they refuse a
   loose file and the folder of another chat. `agent-data/NOTES.md` stays
   one file for the tenant dir (HANDOFF H-237).
   `agent_paths.is_other_thread_rel` is the ONE rule for another chat's
   folder, and it covers both folders. Fence:
   tests/unit/test_h227_thread_scope.py.
   `package_artifacts.py` zips the files of one chat into one card. With no
   paths, it reads the thread's `outputs/` folder in a tenant dir. In any
   other dir, it reads the files of `acb_memory.session_paths`. A named path
   goes through the `share_artifact` rules. Every read and the write go
   through `safe_open`. `agent_paths.is_blocked_rel` is the ONE secret-name
   rule. The gateway's file routes read it too. A zip is a derived file, so
   it lives on disk only and never in the blob store. It always lies at
   `agent_paths.thread_package_rel`, and the chat delete removes it there.
   Fence: `tests/unit/test_package_artifacts.py`.
   A batch run (no chat, `batch_thread` from the executor) writes the flat
   `outputs/`, never replaces a file, and records the actor `batch`
   (`_is_batch_run`, `BATCH_ACTOR`). Its document is the organization's to
   read (`projects_ai_chat.md` §22.9 rule 8).
5a. skill_families.py -- WS-23 skill-family registry (spec: project-docs/specs/skills_registry.md).
   `SKILL_FAMILIES` maps family slug -> {label, description, tool names} and must
   cover EVERY tool `orchestrator._tool_injection` injects, each in exactly ONE
   family; the `core` family must equal `_CORE_STANDARD_TOOL_NAMES` verbatim
   (tests/unit/test_skills_registry.py is the drift gate — a newly injected tool
   must be registered here or CI fails). `build_catalog()` measures per-family
   token cost (marginal addendum + tool JSON schemas) through dependency-injected
   renderer/tokenizer params — this module never imports orchestrator/gateway;
   the gateway route (`routes/integrations_skills.py`) composes the real ones.
5b. decide_tools.py -- the `decide` core-floor tool (WS-31 CP-13d, spec:
   project-docs/specs/customer_console.md §6A.14). It asks one typed question
   through `acb_llm.decide` and never through the Console client. It sends no
   member (the R11 finding in that section), and it never logs `context`.
   `decide_tool_enabled()` is its ONE switch. The injection chain and
   `addendum.rendered_parts` both ask it, so with `DECIDE_ENABLED` off the
   tool is not injected and no section names it.
   Fence: tests/unit/test_decide_tool.py.
   WS-45 S1 (D90, spec `project-docs/specs/ai_tier_routing.md` §6): the tool
   has TWO engines and ONE name. `decide_tool_for(agent_name)` is the one
   place that picks. An agent that `AI_TIER_ROUTING` covers gets
   `system_one_decide`, whatever `DECIDE_ENABLED` says. Every other agent gets
   the Jev `decide` while `DECIDE_ENABLED` is on. `system_one_decide` sets
   `__tool_risk__` (`open_world=False`) on the function and never calls
   `annotate()`, because `annotate` writes the Jev engine's registry entry by
   name. Its output is data: a fixed lead, an option of the question or
   `unsure`, and a reason with no line break, no URL and no code fence, at
   most 120 characters. Fences: tests/unit/test_system_one_tool.py and
   tests/unit/test_delegation_no_egress.py.
5b-1. system_one.py -- the `system-one` MAF agent. It holds no tools, and it is
   not in `_AGENT_REGISTRY`. `ask()` sends a batch of up to 20 questions in
   ONE request on `tier-fast`, in JSON mode (`{"type": "json_object"}`).
   Never send a JSON schema: the Router answered 400 to it for three days.
   The shape is in INSTRUCTIONS, and `parse_answers` checks it. Each failure
   logs `system_one.failed` with a reason code. The request goes through `acb_llm.attribution.attributed_openai` to the gateway's `/v1`.
   It sends nothing when `acb_llm.routed.routing_is_on()` is false, so it
   never reaches a vendor directly. The 3 s limit is the client's request
   timeout, with no retry. It logs no context, question or option. A caller
   with its own timer passes `timeout_s` and calls `warm()` first, so the
   timer counts no import.
5b-2. tier_policy.py -- the ONE home of the tier policy (WS-45, D90). It holds
   the ONE reader of `AI_TIER_ROUTING` (`tier_routing_on`, fails closed), the
   System-1 tier and thresholds (0.70, 0.80, 0.90 by effort), the table of
   §4.1 (`KIND_TIERS`), `TOOL_HINTS` and the effort mapping (`choose`). No
   other file holds a second copy. `turn_kind` asks System 1 the kind of a
   turn, once, and waits at most 1.5 s. `TierPolicyProvider` adds
   `TierPolicyMiddleware` to ONE run's view of an agent, and it sets only
   `options["model"]` on each main request. It touches no tool, so no egress
   class changes. `ai_route.chosen` and the `ai.route` event hold no tenant
   text. Each `TOOL_HINTS` name must be a real tool.
   Fence: tests/unit/test_tier_policy.py.
5c. permission_policy.py -- the B6 risk-aware handler, plus the D85 guard
   `guard_shared_agent_shell`. The guard refuses a shell request when the
   run's artifact context does not say `host_shell_refused=False`, in every
   `AGENT_PERMISSION_MODE`. The orchestrator decides the flag, because this
   package cannot import it. `decide()` reads the SDK 1.0 shapes: a write's
   target is `file_name`, and a `read` request is a read, contained in the
   workspace. Fence: tests/unit/test_shared_agent_shell_tools.py.
   `decide()` refuses a read of a BINARY file in the workspace with the
   reason `read_binary_file` (incident 2026-10-09). `binary_file_note` reads the
   first 8 KB through `safe_open`, after the containment check. A NUL or a
   known magic number makes the file binary. The model reads the name, the
   kind and the size, and `read_attachment` for a .docx, .xlsx or .pdf file.
   The refusal holds in `enforce` and in `audit`, because an approval only
   gives the model the bytes again. Fence: tests/unit/test_chat_nul_persist.py.
   ⚠️ It never refuses a name with an image suffix (`_CLI_IMAGE_SUFFIXES`:
   png, jpg, jpeg, gif, webp, bmp, ico, tif, tiff, heic, avif, in any case),
   and it does not open that file. The CLI's `view` asks the native
   `imageHelpersIsBinaryImageFile`, which answers by the suffix alone, and
   then sends the file to the model as an image. A refusal would hide a
   screenshot (fix rounds 1 and 2). The list follows CLI 1.0.66, so a CLI
   upgrade must check it again. A file that starts with a UTF-16 or UTF-32
   byte order mark is text. UTF-16 text with no mark stays refused, and the
   sentence asks the member for a UTF-8 copy.
5d. safe_open.py -- the ONE safe opener (WS-43d, spec `maf_coding_engine.md` §7.5 rule B).
   Every host reader and writer of a dir that a sandbox container mounts opens
   its paths here: `openat2` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS` on Linux
   5.6 or later, else a walk that opens each part with `O_NOFOLLOW`. A link at
   any depth fails the call. Callers: the store below, the sweep of
   `code_tools`, the rehydrate of `acb_memory` and the gateway's workspace
   routes. Do not open a mounted dir any other way. Fence:
   tests/unit/test_sandbox_safe_open.py (WS43-F14).
5e. sandbox_tools.py + tenant_file_store.py -- the sandbox tools (WS-43d, D86,
   spec §7.4 and §16.3). `run_command`, MAF's eight file tools over
   `TenantFileStore`, and a `SkillsProvider` over `agent-data/skills/`. Only
   `attach_for_run` hands them out, to ONE run, as a per-run view, and only
   when `sandbox_broker.covers()` is true for the run's own tenant. They are
   never injected (`_collect_injectable_platform_tools` never returns them).
   The boundaries are structural, in every permission mode: the cover, the
   broker exec with no host fallback, and the store's map and safe opener.
   A covered run does not hold the host floor tools that open the dir with
   plain path calls (`WITHHELD_HOST_TOOLS`): a per-run chat middleware hides
   them and a function middleware refuses them. The same set holds the host
   web tools `web_search` and `fetch_page` (`HOST_NETWORK_TOOLS`), so a
   covered run holds no web tool and its container has no network (§16.3).
   Do not write that no data leaves the platform: the owner kept delegation
   on 2026-10-03, and an agent that the run calls runs outside the sandbox.
   H-236 (5g) binds the covered run and every run under it. The store takes only the
   heads agent-data/, inputs/, outputs/ and .run/, and a skill folder is its
   author's alone (`agent_paths.claim_skill`, `refused_write`).
   `decide()` runs too, with the whole command and with the real host path.
   The store maps `outputs/` and `inputs/` to the thread's own folders, and
   `.run/` to the run data. It maps the upload folder since H-227. The
   thread's own slug may lead a path (`inputs/<slug>/x` is `inputs/x`),
   because the upload message and the cards name a file so. The slug of
   another thread is refused (`_own_folder_rest`). It
   mirrors each kept write and delete. `page_file_tools` (2026-10-15) gives
   `file_access_read` and `file_access_read_lines` a window of 400 lines or
   40,000 characters, and `file_access_ls` an `offset` and pages of 200.
   Each answer that holds part of the data says how to get the rest. The
   fence is tests/unit/test_read_tool_paging.py. WS-43u: the provider
   adds the rules for code to the instructions of the turn, from
   `addendum.render_run_sections` over the tools that the turn holds. Put
   prose for a tool that only a sandboxed run holds in `addendum.RUN_SECTIONS`,
   never in `FULL_SECTIONS`, because an unscoped agent renders every section
   there. Fences:
   tests/unit/test_run_command_tool.py (WS43-F6),
   tests/unit/test_maf_code_session.py (WS43-F7, R8),
   tests/unit/test_projects_sandbox_tools.py (WS43-F21),
   tests/unit/test_h227_thread_scope.py (H-227), and for the rules
   for code tests/unit/test_generated_addendum.py and
   tests/unit/test_projects_agent.py.
5f. attachment_text.py + attachment_tools.py -- `read_attachment` (H-229,
   spec: project-docs/specs/projects_ai_chat.md §22). It returns the text of a
   file attached in the caller's own chat. It reads `.docx`, `.xlsx`, `.pdf`,
   `.html`, `.htm`, `.txt`, `.md` and `.csv`. `SUPPORTED_SENTENCE` is the ONE
   sentence that lists the kinds. `attachment_text` parses bytes only: no subprocess, no code, and
   pypdf's `jbig2dec` is off. Every cap is a module constant there. The PDF
   deadline stops a page in the middle (pypdf's `visitor_operand_before`). A
   Word part must be UTF-8, and `pyexpat` refuses a DTD at its first event.
   Before each PDF page it caps the font setup that pypdf runs before the
   first operator: 64 font entries and 2 MB of font program bytes for each
   resource dictionary, the page's and every reachable form's. The parses
   run on a pool of `MAX_PARSES` threads of their own, and the reads on
   another small pool, never on the default executor. A slot is held until
   its worker really ends.
   `attachment_tools` takes the workspace, the thread and the store key from
   `artifact_context()`, never from the `.cc-instance` marker, and only a
   file name from the model. It opens through `safe_open` (5d). In a run that
   `sandbox_broker.covers()`, it holds `broker.host_dir()` during the read,
   so `WITHHELD_HOST_TOOLS` (5e) does not name it.
   `agent_paths.upload_dir_rel` is the ONE rule for where an upload lands:
   `inputs/<thread slug>/` in a shared agent's tenant dir, else `inputs/`.
   It builds on `thread_slug`, the one slug of `outputs/<thread slug>/` too.
   The gateway upload route and this tool both call it. Do not add a second
   reader of an attachment. Fence: tests/unit/test_read_attachment.py.
   Since 2026-10-09 `read_attachment` is a core floor tool and in
   `_FLOOR_KEEP`, because every chat can attach a file. No scope, toggle or
   `floor_opt_out` takes it away. `addendum.ATTACHMENT_FAILURE_RULE` tells
   the agent what to do when the tool cannot read a file. Fence:
   tests/unit/test_chat_upload_every_agent.py.
   `attachment_tools.parse_bounded` is the ONE bounded parse of the process
   (WS-17 EM-T11). `read_attachment` and the gateway's email text route
   (`GET /email/attachments/{id}/text`) both call it. The agent runtime runs
   in the gateway process, so both callers share the pool and the
   `MAX_PARSES` slots. A second pool would double the bound on parse threads.
   `attachment_text.PAGE_UNREADABLE` is the line for a PDF page that did not
   parse. The email text route reads a PDF of only such lines as unreadable.
   Fence: tests/unit/test_email_attachment_text.py.
   WS-17 EM-T11b (spec `email_app_master_plan.md` §10.4.13): a `.xlsx` takes
   the zip and XML helpers of a `.docx`, with no `openpyxl`. Each helper
   takes the format name, so a `.docx` keeps each refusal sentence. The
   reader reads only parts in `xl/`, reads each part once and ignores an
   external target. The `MAX_XLSX_*` caps stop the read and set `stopped`.
   HTML goes through `html.parser` with `convert_charrefs=True`, and it
   checks the deadline before each chunk. The reader cuts a construct that
   the parser holds past `MAX_HTML_HELD`. The cut follows the state of the
   parser (`_cut_held`). It reads at most `MAX_HTML_CHARS` characters.
   No regex may run over a whole line
   of HTML. A quadratic one holds the GIL (round 1, P0). Do not add a third
   reader of HTML (`email/signature.py` and `body_backfill.py` move onto this
   one later).
   Fences: tests/unit/test_attachment_xlsx.py and section 14 of
   tests/unit/test_read_attachment.py.
5g. egress.py -- the network control on a covered run and every run under it
   (H-236, spec `maf_coding_engine.md` §16.3). It FAILS CLOSED. Such a run
   binds `no_egress=True` in its artifact context and holds only the
   platform's own four `DELEGATION_TOOLS`, the sandbox tools and tools whose
   annotation says `open_world=False` explicitly. `is_egress_tool()` is the
   one test: a tool with no annotation, or no `open_world` key, is an egress
   tool, and so is an MCP tool, an MCP-born tool and a `STORE_WRITES` member
   (the four memory writes and `save_note`). An agent's own tool carries its
   annotation on the function (`__tool_risk__`).
   TRUST IS BY IDENTITY (H-236 follow-up). `_platform_owned` reads
   `_PLATFORM_CALLABLES`, a weak map from each platform callable to the tool
   names that it may carry. A tool OBJECT takes a registry entry, or a
   delegation name's exemption, only through that map. Register a new
   platform tool with `_register_platform_callable` where you build it. A
   wrapper that our code makes calls `_register_platform_wrapper(original,
   wrapper)`, as the permission gate and the steer wrap do. Both functions
   are private, and they register nothing for a caller whose module is not in
   `acb_skills` or `orchestrator` (`_caller_is_platform`). Never follow
   `__wrapped__`, or a `functools.wraps` wrapper of another repo becomes
   trusted. The Copilot guard resolves a request's name in the session's own
   tool list (a delegation name too), and fails closed when it finds none.
   `no_egress_for_this_run()` is the one reader: a frame with no run context
   and any value but an explicit `False` read as `no_egress`.
   `EgressGuardProvider` adds the per-run MAF middleware.
   `permission_policy.guard_shared_agent_shell` refuses on the Copilot path
   (`is_egress_request`). The orchestrator decides the flag
   (`_tool_injection._run_no_egress`), because this package cannot import it.
   Fence: tests/unit/test_delegation_no_egress.py (WS43-F24).
   WS-17 EM-G11d: `UNTRUSTED_INPUT_AGENTS` names the agents that read
   stranger text. `mark_untrusted_answer` marks the CHAT SESSION that read
   the answer of one. The key is the bound tenant and the session id of the
   run context, never a request field and never the member (a shared room
   is one session). The mark lives in tenant Redis (namespace `egress-mark`,
   a sliding TTL of 400 days). `session_mark_state` reads a bounded LRU of
   positives, and on a miss it asks Redis. It answers marked, clear or
   unknown. Unknown is a Redis fault in a run that can show a card. An
   egress tool gate reads `session_is_marked`, which FAILS CLOSED on
   unknown, so the call asks the card. A delegation (`is_delegation_tool`)
   asks no card on unknown, and `call_agent` binds `no_egress` only on a
   known mark (`session_is_known_marked`). So a blip never holds a send. A
   batch session never asks Redis. `read_untrusted_answer` is the sync
   reader of the LRU only. Tests and the routing eval use the one in-memory
   store `tests/unit/_egress_mark_store.py`. Then `permission_policy.decide` answers
   `untrusted_answer_confirm` for each egress call: the member's card first,
   and no call with no card channel.
5h. tool_annotations.py -- `annotate()` has NO default for `open_world`
   (H-236). Every tool of every in-repo agent states it, and the WS43-F24
   fence reads the real registry to check. The risk block of the addendum is
   PER AGENT and deterministic: `risk_summary_block(own=)` lists the
   platform's own names (`_PLATFORM_STATIC`, without the sandbox tools) and
   every annotated tool that the agent holds after injection, on all four
   lines. The caller takes them from the agent's own tool list
   (`_tool_injection._own_risk`), never from the process-wide registry, so
   no tool of another agent joins. The fence pins task-manager's block, and
   checks that each live Copilot agent keeps every own line that main had.
5i. tool_guard.py -- the ONE per-run middleware pair (`WithholdTools`,
   `RefuseTools`) that takes a rule. The host-tool control of
   `sandbox_tools` and the egress control of `egress` both use it. Do not
   write a third copy of the pair.
5j. ask_tools.py -- `request_confirmation` is the one HITL card gate, and it
   fails closed. `confirmation_channel_open()` reads the same two channels.
   A tool reads it after a denial, to tell a refusal from a run with no
   live chat (WS-17 EM-T13a). A tool may read it before a card only to
   choose a narrower act that writes less. `draft_reply` of the email
   assistant returns an unsaved draft when no card can show (WS-17
   EM-G11c1). It never approves a write.
   `refuse_cards()` is the scope of a run with no card channel (WS-47
   WAC-3, the WhatsApp bot). Inside it `request_confirmation` denies at
   once, `ask_questions` returns a fixed line, and nothing is pushed. WAC-4 is the one exception. In a WhatsApp run with writes on,
`request_confirmation` first asks `whatsapp_acts.answer_card`. That call can
PARK one `create_task` for the member's Confirm. At that Confirm, it can
answer "yes" once. Every fault there denies. The default is off, so a web run
   is unchanged. Fences: `tests/unit/test_wac_bot_run.py`,
   `tests/unit/test_wac_writes.py`.
5k. agent_site.py -- the ONE builder of an agent install (WS-49 BH-7, spec
   `box_hardening.md` §5 BH-7). `loader._install_agent_deps` and
   `dep_tools.install_dependency` install into
   `/var/lib/acb-gateway/agent-site` with `uv pip install --target`. They
   never install into the shared venv. Each install passes `-c` with a freeze
   of the venv (`--exclude-editable`), at
   `/var/cache/acb-gateway/constraints.txt`.

   `--target` does not see the venv. So `classify()` sorts each declared
   line before any uv call. The venv provides a line when its site dirs hold
   the name at a version that the specifier allows. An editable workspace
   member counts. A line with a false marker is not installed either.

   A venv package at another version is a conflict: the loader does not
   install it, and the dep status is red. The marker holds the conflict, so a
   warm load runs no uv and still reports red. A line with an extra goes to
   uv, so the packages of the extra install.

   When the venv provides all of them, no uv runs.
   `prune_venv_duplicates()` removes each copy of a venv package from
   agent-site, by its `RECORD`. It runs after each install and once for each
   new venv state. When the loader drops a line, it writes the rest to a file
   of its own, with each `-r` or `-c` path made absolute.

   The gateway APPENDS agent-site to `sys.path`, so a venv package wins. A
   child gets `PYTHONPATH` from `acb_common.child_env.AGENT_PATH_VALUES`.
   Never use `site.addsitedir` on agent-site. Never write a `.pth` file or a
   `sitecustomize` there. `scrub()` removes one that a package brings.

   The deps hash holds the deps to install, the target and the constraints
   digest. When the unit's dirs are absent, both callers refuse. Neither
   falls back to the venv. Fence: tests/unit/test_agent_deps_target.py
   (BH-F6).
5l. crm_tools.py -- the 13 CRM read tools (WS-53 CRM-C7, spec
   `project-docs/specs/crm_platform.md` §20.10). It is the ONE copy:
   `crm-assistant` imports them, and the injection chain offers them as the
   `crm` skill family. Each tool reads a `/crm` route as the run's member,
   with the internal bearer and `X-User-Email`. With no member, it refuses.
   An identity with no `@` (a shared room's `room:<thread>`) gets
   `CRM_SHARED_ROOM`, and nothing goes to the gateway.
   It opens no database session and takes no SQL. A 404 from the kill switch
   and a 403 from the access rule each come back as a sentence. An answer
   holds 50 rows at most and names the next page. Each path segment goes
   through a validator, and the result goes into the path as `slug`,
   `record`, `mod`, `report`, `vocab` or (CRM-Q32-8) `question`. The risk of each tool is in
   `tool_annotations.TOOL_ANNOTATIONS` (`open_world=False`), and the risk
   block does not list them. `crm_mode()` is not a tool: the orchestrator
   reads it to drop the record writes in Mirror mode. `crm_list_fields`
   never offers a row with `is_system`, and it marks a retired field
   (WS-53 CRM-C4a). `crm_aggregate` and `crm_list_fields` also take a
   module as `module:<api_name>` (CRM-Q25). `_entity_slug` takes one only
   with `allow_module=True`, so no record tool builds a module path.
   `crm_aggregate` shows INR only for a `currency` field, `sum_amount` and
   `weighted`. `crm_report(make_report=true)` builds a kept report by
   code from the route data (`projects_ai_chat.md` §14.10.9). So it carries
   `read_only=False` and `idempotent=False`, and it still sends only GET.
   `crm_search_notes` (CRM-Q27) reads `GET /crm/notes/search` and gives
   note ids and snippets. Each snippet sits in the frame of `untrusted.py`.
   Its last line says that a number comes from `crm_aggregate`. A
   `degraded` answer gives `SEARCH_OFF`, which names `crm_list_records`.
   `addendum.py` names it in a section of its own, gated on that one
   tool, because the orchestrator holds 12 of the 13 CRM tools.
   `crm_aggregate` says the zone line only for a zoned month group or a
   `datetime` bucket (`group_field_type`).
   `crm_list_modules` reads `GET /crm/catalogue` first (CRM-Q31-1). It
   renders one line for each entry, then the money block. A 404
   `catalogue_off` or a bare 404 makes it answer from `/crm/modules`, as
   before.
   `crm_aggregate` names the label of the real field (CRM-Q31-2). It says
   "the amount" for a key word. A total of 0 on a core entity reads the
   catalogue once more. When the entity holds no records, the answer names
   the modules that hold them.
   On a slot with an approved overlay (CRM-Q20-3a), `crm_aggregate` names
   the module. A refusal of the route is one sentence. Some tools read
   `overlays` of `GET /crm/mode`. `crm_list_pipelines` reads it first. A
   status filter on leads or deals reads it first too. `crm_list_records`,
   `crm_get_record`, `crm_deal_contacts` and `crm_report` read it after an
   empty answer or a 404.
   Fences: `tests/unit/test_crm_agent.py`, `tests/unit/test_skills_registry.py`
   and `tests/unit/test_reports_by_code.py`.
   WS-53 §18.16 (spec `project-docs/specs/crm_platform.md` §18.16):
   - `RunReads` lives in the run's holder (`CrmAsks` or `TextAsks`).
     `_read_tool` wraps each read in `_budgeted`, so a second
     `crm_list_fields` of one module reads nothing. Past 8 aggregates or 20
     reads, a read answers `BUDGET_LINE` and reads nothing.
   - `crm_aggregate(term=)` reads `GET /crm/terms/resolve`, then the total of
     each of the first 2 fields. The confidence rule: a decided or close
     term answers first, and an unsure term asks FIRST with each total
     (`_ask_first_block` on the web, `TEXT_ASK_FIRST` on WhatsApp).
     `CLOSE_SHARE` equals `gateway.crm_sync.terms.CLOSE_SHARE`.
   - `_follow_up` marks each card that comes after an answer
     (`CrmAsks.follow_ups`), and `ask_tools.ask_questions` parks the run on
     it after `FOLLOW_UP_PARK_SECONDS`.
   - `filters_line` states each filter of a module total, with the values
     that it counted. No word of one org goes in this code.
   Fences: `tests/unit/test_crm_answer_consistency.py` and
   `tests/unit/test_crm_answer_consistency_r8.py`.
5l-2. org_facts_tools.py -- the tool `org_facts(app, query)` and the CRM facts block (WS-53 CRM-Q32-5, spec `project-docs/specs/crm_platform.md` §18.14.4). The tool reads `GET /org/facts` as the run's member, with the transport of `crm_tools.py`, and it loads with the `crm` skill family only, so the core floor does not change. `crm_facts_block` builds the block of a run: the answered facts of the money modules, the meaning of revenue and the stages, at most 12 lines. `orchestrator._tool_injection._add_crm_facts_block` reads it as the member and adds it at the END of the prompt of a run that holds a CRM tool. A box with `CRM_QUESTIONS_ORGS` empty sends no request. A run that reads no fact row loses `org_facts` too, and the orchestrator and the CRM Assistant name the tool in their `tool_scope`. With the list empty, the addendum renders neither the `org_facts` section nor the open-question line (`addendum._dark_tools`), and a revenue total reads no catalogue (`crm_tools.questions_box_on`). CRM-Q32-6: `crm_list_modules` writes "an open question" on each open part, and `crm_aggregate` passes on the `href` of a 422 `no_key_field` and adds the line of the default of a revenue total. CRM-Q32-8: a total that depends on an open question reads `GET /crm/questions/ask` as the member and gives a CLARIFY block: what is not clear, why, and the payload of `ask_questions` with the key of each option. A run asks each question once and at most 2, through ONE `CrmAsks` object that `executor.run_agent_stream` opens with `crm_tools.open_asks_for_run()` beside `refuse_cards_for_run`. The default of its ContextVar is `None`, so a run with no holder gives no card. `crm_answer_question` posts the key to `POST /crm/questions/{id}/reply`, the one POST of `crm_tools.py`, and goes with `org_facts` (`FACTS_GATED_TOOLS`). It sends a pick only after this run showed the card of that question (`CrmAsks.shown`), and never with no card channel. A run that does not hold the tool shows no card (`mark_answer_tool`, from `_add_crm_facts_block(mark_run=True)`). No CRM tool waits for an answer. CRM-Q33: for an org on `CRM_ANSWER_FIRST_ORGS`, the read gives `answer_first` and `assume`, and `crm_aggregate` answers first. It reads the total on the assumption, gives a note of how, and asks one question after the answer. A WhatsApp run names that question in `TextAsks` (`open_text_asks`), and the gateway asks it in text. `crm_propose_rule` holds a rule of the member's own words for the confirm, and saves nothing. It goes with `org_facts`, and only an org on the list holds it. A pick counts only when the member clicked it: `ask_tools` hands the gateway's answer to `crm_tools.record_card_answer`, and the gate takes that key once. A card that was only shown opens nothing. Fences: `tests/unit/test_org_facts.py`, `tests/unit/test_org_facts_r8.py`, `tests/unit/test_crm_agent.py` and `tests/unit/test_crm_answer_first.py`.
5l-1. answer_check.py -- the number check of a chat answer that read a CRM
   tool (WS-53 CRM-Q29, spec `project-docs/specs/crm_platform.md` §18.11.2).
   `AnswerCheckProvider` adds two middlewares to ONE run. `RecordToolResults`
   keeps the text of each tool result, and a result of `ARM_TOOLS` arms the
   check. `CheckAnswers` does nothing before the arm. After it, with the mode
   `on`, it holds each model response until it ends, and checks the text with
   `acb_common.number_check.check`. The sources are the run's tool results
   (never a delegated answer), the arguments of its CRM calls (a date gives
   its count of days) and the member's own words, `member_message` of the
   run context. Never the packed prompt, which holds the model's earlier
   answers. The ledger adds each result to its sources when it arrives. It
   also checks the shown text of each `DISPLAY_TOOLS` call (a card, a report,
   a document, a text file, a `whatsapp_ui` element), and a failed card
   fails the answer and never ships. A failed answer gets ONE retry for each run, and
   then `fallback_text`, which shows the results of `FALLBACK_TOOLS` and
   never the draft. A failed step whose calls show nothing loses its text.
   A failed step goes out without its text only when each call is in
   `QUIET_TOOLS` (the CRM reads and the memory reads). Any other call fails
   it. `ask_questions` and `request_confirmation` are display tools. In an
   option of `ask_questions`, a figure after a choice word ("Last 90 days",
   "Top 10", "Above ₹10 lakh") is a choice, and the check skips it. In
   `on` mode a check that raises logs `answer_check.check_failed` and sends
   the answer unchanged, so a failed check never loses the turn. An
   answer that still fails after its one retry goes out with the note of
   `note_text` (fix round 8). The note never lists raw figures alone. A
   sum or a difference of two figures of the answer gets one sentence.
   More than 3 failed figures, or 2 or more that are over 30 percent of
   the figures, get "How I got these numbers", one line for each CRM read
   from the head line that the server wrote (`read_line`). An empty group
   of a grouped total (`empty_group`) whose value the answer states in the
   sentence of a failed figure, or an
   open CRM question of the run, gives ONE question. Else each failed
   figure, 3 at most, gets a reason and an action. `many_failed` is the
   one rule of "many". The log line names the KIND of the note (`note=`),
   in `shadow` too, and `scripts/crm_answer_check_replay.py` counts it.
   The fallback is only for an answer with no text (fix round 7). A
   result of `MODEL_WRITTEN_TOOLS` (a delegation, `code_task`, a script) is
   no source. A result over `RECORD_INLINE_CHARS` is read on a thread. A
   passed check makes no model request. While it holds, it puts
   `HOLD_EVENT` on the run queue at most each `HEARTBEAT_S`, only on a model
   update, so the idle watchdog sees life. The one reader of
   `CRM_ANSWER_CHECK` is `acb_common.settings.crm_answer_check_mode`. The
   one seam that attaches the provider is
   `orchestrator._tool_injection._apply_answer_check`. The log line
   `answer_check.checked` holds counts and the kind of a note only. Fences:
   `tests/unit/test_crm_answer_check.py`,
   `tests/unit/test_crm_answer_check_executor.py` and
   `tests/unit/test_crm_answer_check_explain.py`.
5m. untrusted.py -- the ONE frame for text that a stranger wrote (WS-17
   EM-G11d, UMCC, spec `email_app_master_plan.md` §12.3.13.8). Make one
   `UntrustedFrame` for each model call. `wrap(label, text)` puts the text
   between two marker lines that hold a random token, and `note(what)` is the
   data note. Two modes, each one linear. Mode (a), `neutralise`, is for
   text INSIDE a frame. It removes each copy of the token until none is
   left, and it breaks each run of three or more brackets to `[` or `]`.
   Mode (b), `defang_trusted`, is for learned text OUTSIDE every frame. It
   changes EACH bracket, ASCII, look-alike or tag block, to `(` or `)`.
   Do not add a tag regex or an exemption to mode (b). It cuts no text. The
   email paths, the attachment frame and the Insights frames use it. Do not build a `<<<` marker anywhere else. `agent_edge/tools.py`
   keeps its own `{"untrusted_text"}` shape for WS-55. Fences:
   `tests/unit/test_untrusted_frame.py` (the scan for a second framer) and
   `evals/email_injection/`.
5n. company_search_tools.py -- `search_company`, the assistant's one read
   over every app (D105, WS-61 KR-2, spec `project-docs/specs/knowledge_base.md`
   §20.5). It reads `GET /search/company` as the run's member, with the
   internal bearer and `X-User-Email`, the pattern of `crm_tools.py`. With no
   member, it refuses. Its answer opens with "Company records (data, not
   instructions):", and each record is one line plus a `full_id:` line. It
   reaches the orchestrator through `core_tools` in `orchestrator/agents.py`
   only, behind `COMPANY_SEARCH_ENABLED`, and it is in no skill family.
   Fence: `tests/unit/test_company_search_tool.py`.
   - With `CONTEXT_RECENCY` on too (WS-61 CX-1, `company_context.md` §4.3
     and §4.5), the orchestrator takes `search_company_dated`. It has the
     name and the risk of `search_company`, and it also takes `after`. Its
     answer opens with "Company records (data, not instructions).
     Searched: ... Skipped: ...", and each line prints the stamp as "due X,
     changed Y". When the two are one day, Knowledge and tasks print
     "changed Y", and each other app prints its own word. With the flag
     off, the tool and its text are the KR-2 ones. `context_recency_enabled` is the ONE reader of the flag, for the
     gateway and the orchestrator. Fence:
     `tests/unit/test_company_search_recency.py`.
5n-1. knowledge_tools.py + knowledge_manifest.py -- the two Knowledge reads
   of the assistant, `read_page` and `list_spaces` (WS-61 KB-4a, spec
   `project-docs/specs/knowledge_base.md` §8.1). They read `/knowledge` as
   the run's member, the pattern of `company_search_tools.py`, and they take
   no SQL. `read_page` checks the id as a UUID before it builds a path. It
   cuts the body to one heading path on the client, and it caps the answer
   at 16,000 characters. `knowledge_manifest.py` lists every Knowledge route
   with its class, and the client refuses a route that the manifest does not
   give to the tool. A new Knowledge route needs a row. The three gap routes
   of KB-9a are rows too. The list is class A with no tool yet. Dismiss and
   answered are class X, because a person closes a gap. `index_block` builds
   the index block of §6.5 for the executor, and it holds no page text. The
   tools reach the orchestrator through `core_tools` only, behind
   `COMPANY_SEARCH_ENABLED`, and they are in no skill family. Fences:
   `tests/unit/test_knowledge_tools.py`,
   `tests/unit/test_knowledge_chat_coverage.py` and
   `tests/unit/test_knowledge_index_block.py`.
   - KB-5a adds `propose_page` and `propose_edit` on the same path. Each
     shows one card through `request_confirmation` before it builds the
     request, and sends only on an approve. It sends
     `X-Actor-Via: chat:knowledge`. The manifest gives both tools
     `POST /knowledge/proposals` only, through `Route.also`, and the accept
     and reject routes are class X. Both tools join only while
     `KNOWLEDGE_PROPOSALS` is on (default off). Its one reader is
     `knowledge_tools.knowledge_propose_enabled`. `propose_edit` takes the
     `base_version` that `read_page` prints as `Version: <n>`. Fence:
     `tests/unit/test_kb_proposals.py`.
5n. reports.py -- `make_report`, the ONE report tool of the chat
   (`project-docs/specs/projects_ai_chat.md` §14.10). It checks one spec with
   the checks of `documents.py`, and it keeps no copy of them. It writes
   `outputs/<slug>.report.json` and `outputs/<slug>.pdf`, and draws one
   `report` card through `emit_generative_ui`. `documents.save_outputs` writes
   the files of `make_report` and `make_document` with `safe_open`, under
   `_chat_dir_guard`, so a planted link is never followed (H-236). In a
   WhatsApp run it queues the PDF through `whatsapp_ui.queue_document`, with
   no file and no card. The `documents`
   family holds it, so it is not a floor tool. `by_code` is the ONE gate of
   a report that a tool builds from its own reads (§14.10.9). It asks
   `write_artifact.run_holds_tool`, and `status_report`, `render_report` and
   `crm_report` call it. Do not write a second gate. Fences:
   `tests/unit/test_chat_reports.py` and `tests/unit/test_reports_by_code.py`.
6. artifact_lint.py -- lints agent-generated HTML before it reaches the sandbox.
   The sandbox (SandboxedHtml.tsx) fails SILENTLY: a CDN fetch is CSP-blocked, a
   typo'd `cc-` class renders unstyled, a `cc-bar` without `--v` draws empty. The
   linter turns those into `warnings` on the write_artifact / emit_generative_ui
   result. Advisory only -- it never blocks a write and never raises.
   `CC_CLASSES` mirrors the stylesheet in SandboxedHtml.tsx; the drift test in
   tests/unit/test_artifact_lint.py fails if the two diverge, so a new `cc-`
   block must be registered in BOTH places.

## Work Guidance

### Loading agents
- GitHub agents: git clone (first time) -> git pull --ff-only (subsequent)
- Local agents: _ensure_local_git_repo() syncs source to cache, git init if needed
- Cache at {agents_clone_dir}/repos/{agent_name}/
- agent_dir always points to the cache directory (isolated from source)
- Bot git identity configured automatically (metorite-bot)
- **Pull strategy (ADR-022):** ``_pull_latest()`` returns a dict with
  ``strategy`` and ``conflicts_resolved_by_llm`` fields.  On rebase
  conflicts, ``_resolve_rebase_conflicts()`` calls the tier-3 (powerful
  reasoning) LLM via LiteLLM to intelligently merge conflicts before
  falling back to ``--ours``.  ``_call_llm_for_merge_resolution()``
  handles the LLM prompt, response parsing, and conflict-marker
  sanitisation.

### Adding a new injected tool
1. Define the async function in the appropriate module
2. Add to _extra_tools list in executor.py:_inject_agent_tools()
3. Add tool guidance to _build_injected_tools_addendum()
4. Tool must be async and accept simple types (str, dict) for Copilot SDK compatibility
5. Wrap with normalize_tools() for GitHubCopilotAgent compatibility

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
