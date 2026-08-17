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
5a. skill_families.py -- WS-23 skill-family registry (spec: project-docs/specs/skills_registry.md).
   `SKILL_FAMILIES` maps family slug -> {label, description, tool names} and must
   cover EVERY tool `orchestrator._tool_injection` injects, each in exactly ONE
   family; the `core` family must equal `_CORE_STANDARD_TOOL_NAMES` verbatim
   (tests/unit/test_skills_registry.py is the drift gate — a newly injected tool
   must be registered here or CI fails). `build_catalog()` measures per-family
   token cost (marginal addendum + tool JSON schemas) through dependency-injected
   renderer/tokenizer params — this module never imports orchestrator/gateway;
   the gateway route (`routes/integrations_skills.py`) composes the real ones.
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
