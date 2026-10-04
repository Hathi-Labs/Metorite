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
   It also owns the run's artifact context, the ContextVar seam that every
   workspace tool reads through `artifact_context()` (H-201 part 4). Bind it
   with `bind_artifact_context`, change it with `derive_artifact_context`, and
   scope it with `artifact_context_scope` or `enter_artifact_context` and
   `reset_artifact_context`. Never add a module-level dict for run state. A
   tool with no context must fail closed, and never fall back to the cwd or a
   temp dir. Fence: tests/unit/test_h201_run_context.py.
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
5c. permission_policy.py -- the B6 risk-aware handler, plus the D85 guard
   `guard_shared_agent_shell`. The guard refuses a shell request when the
   run's artifact context does not say `host_shell_refused=False`, in every
   `AGENT_PERMISSION_MODE`. The orchestrator decides the flag, because this
   package cannot import it. `decide()` reads the SDK 1.0 shapes: a write's
   target is `file_name`, and a `read` request is a read, contained in the
   workspace. Fence: tests/unit/test_shared_agent_shell_tools.py.
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
   The store maps `outputs/` to the thread's own folder and `.run/` to the
   run data, and it mirrors each kept write and delete. WS-43u: the provider
   adds the rules for code to the instructions of the turn, from
   `addendum.render_run_sections` over the tools that the turn holds. Put
   prose for a tool that only a sandboxed run holds in `addendum.RUN_SECTIONS`,
   never in `FULL_SECTIONS`, because an unscoped agent renders every section
   there. Fences:
   tests/unit/test_run_command_tool.py (WS43-F6),
   tests/unit/test_maf_code_session.py (WS43-F7, R8),
   tests/unit/test_projects_sandbox_tools.py (WS43-F21), and for the rules
   for code tests/unit/test_generated_addendum.py and
   tests/unit/test_projects_agent.py.
5f. attachment_text.py + attachment_tools.py -- `read_attachment` (H-229,
   spec: project-docs/specs/projects_ai_chat.md §22). It returns the text of a
   `.docx`, `.pdf`, `.txt`, `.md` or `.csv` file attached in the caller's own
   chat. `attachment_text` parses bytes only: no subprocess, no code, and
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
