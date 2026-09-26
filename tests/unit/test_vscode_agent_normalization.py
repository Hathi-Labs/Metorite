"""VS Code-authored Copilot agents must work as first-class MAF citizens.

An agent imported from VS Code Copilot (`.github/agents/*.agent.md`) is authored
for an IDE that isn't here: it declares VS Code tool ids (`editFiles`,
`terminal`, `codebase`, …), and its MCP servers live in `.vscode/mcp.json` /
`.mcp.json`. Before this (2026-07-17):

  * `tools:` was parsed as ADVISORY METADATA ONLY — never mapped to anything, so
    the model saw IDE names in its prompt that exist in no tool schema and had
    to improvise onto the CLI's native bash/create/edit.
  * `.vscode/mcp.json` / `.mcp.json` were NEVER read — the runtime only looked at
    config.json + the DB table — so an agent depending on a server (e.g. a
    diagramming agent declaring draw.io) silently lost it.
  * MCP config was merged into `agent._default_options["mcp_servers"]`, which the
    1.0.0b wrapper never read (it resolved `runtime_options.get("mcp_servers")
    or self._mcp_servers`), so servers never reached a session at all.
    ⚠️ The 2.0 wrapper (H-181) reversed this: `_default_options` is now the
    field it reads, and `_mcp_servers` is gone.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from acb_skills.agent_md import (
    derive_tool_scope,
    load_repo_mcp_servers,
    runtime_note_for,
    uses_vscode_tool_vocabulary,
)

# The real technical-project-planner frontmatter.
_REAL_TOOLS = [
    "codebase", "changes", "editFiles", "fetch",
    "search", "runCommands", "terminal", "problems",
]


# ---------------------------------------------------------------------------
# VS Code tools -> real platform tools
# ---------------------------------------------------------------------------

def test_vscode_vocabulary_is_detected() -> None:
    assert uses_vscode_tool_vocabulary(_REAL_TOOLS) is True
    # An agent authored for THIS runtime needs no correction.
    assert uses_vscode_tool_vocabulary(["web_search", "write_artifact"]) is False
    assert uses_vscode_tool_vocabulary([]) is False


def test_declared_ide_tools_map_to_real_tools() -> None:
    scope = derive_tool_scope(_REAL_TOOLS)
    # `codebase`/`search` are a code-search capability → real search tools.
    assert "github_search" in scope
    assert "github_repo_search" in scope
    # `editFiles` → the robust file-write tool (not shell heredocs).
    assert "write_artifact" in scope
    # `problems` → diagnostics.
    assert "run_diagnostics" in scope
    # `fetch` → web access.
    assert "fetch_page" in scope


def test_ide_only_names_contribute_nothing() -> None:
    # terminal/runCommands are served by the CLI's native bash — no platform
    # tool needed; extensions/vscodeAPI have no equivalent at all.
    assert derive_tool_scope(["terminal", "runCommands", "extensions",
                              "vscodeAPI", "changes"]) == []
    assert derive_tool_scope(["totally-unknown-tool"]) == []


def test_derive_is_deduped_and_stable() -> None:
    # codebase and search both map to github_search — no duplicates.
    scope = derive_tool_scope(["codebase", "search", "usages"])
    assert len(scope) == len(set(scope))


# ---------------------------------------------------------------------------
# Runtime note (additive — never rewrites the author's prose)
# ---------------------------------------------------------------------------

def test_runtime_note_only_for_vscode_agents() -> None:
    note = runtime_note_for(_REAL_TOOLS)
    assert "write_artifact" in note and "bash" in note
    assert "not running inside vs code" in note.lower()
    # Our own agents get nothing appended.
    assert runtime_note_for(["web_search"]) == ""


def test_runtime_note_steers_away_from_shell_heredocs() -> None:
    """The truncation bug came from authoring files via shell heredocs."""
    note = runtime_note_for(["editFiles"])
    assert "heredoc" in note.lower()


# ---------------------------------------------------------------------------
# Repo-declared MCP servers
# ---------------------------------------------------------------------------

def _mk_repo(tmp_path: Path, *, vscode: bool = False, dotmcp: bool = False) -> Path:
    if vscode:
        (tmp_path / ".vscode").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".vscode" / "mcp.json").write_text(json.dumps({
            "servers": {"drawio": {
                "type": "stdio", "command": "npx", "args": ["-y", "@drawio/mcp"],
            }},
        }))
    if dotmcp:
        (tmp_path / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"drawio": {
                "type": "stdio", "command": "npx", "args": ["-y", "@drawio/mcp"],
            }},
        }))
    return tmp_path


def test_vscode_mcp_json_is_recovered(tmp_path: Path) -> None:
    """The exact file that silently lost this agent its draw.io server."""
    servers = load_repo_mcp_servers(_mk_repo(tmp_path, vscode=True))
    assert "drawio" in servers
    entry = servers["drawio"]
    # Shaped for the SDK's MCPLocalServerConfig: `type` (NOT `transport`),
    # command + args split, and tools ["*"] (the SDK treats [] as "expose none").
    assert entry["type"] == "stdio"
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@drawio/mcp"]
    assert entry["tools"] == ["*"]
    assert "transport" not in entry


def test_dot_mcp_json_is_recovered(tmp_path: Path) -> None:
    servers = load_repo_mcp_servers(_mk_repo(tmp_path, dotmcp=True))
    assert servers["drawio"]["command"] == "npx"


def test_missing_or_malformed_files_are_safe(tmp_path: Path) -> None:
    assert load_repo_mcp_servers(tmp_path) == {}
    (tmp_path / ".mcp.json").write_text("{not json")
    assert load_repo_mcp_servers(tmp_path) == {}


def test_remote_server_shape(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"remote": {"url": "https://x/mcp",
                                  "headers": {"A": "b"}}},
    }))
    entry = load_repo_mcp_servers(tmp_path)["remote"]
    assert entry["url"] == "https://x/mcp"
    assert entry["type"] in ("http", "sse")
    assert entry["headers"] == {"A": "b"}


# ---------------------------------------------------------------------------
# MCP must land where the SDK actually reads it
# ---------------------------------------------------------------------------

def test_merge_targets_the_field_the_sdk_reads() -> None:
    """🔴 Asked of the REAL wrapper, because the field has moved once already.

    The 1.0.0b wrapper read ``self._mcp_servers``. The 2.0 wrapper (H-181) has
    no such attribute and forwards ``_default_options`` to ``create_session``.
    A fake agent would agree with whichever field the helper wrote, so this
    asks the installed wrapper what it would send.
    """
    from agent_framework_github_copilot import GitHubCopilotAgent
    from orchestrator._tool_injection import merge_mcp_servers

    agent = GitHubCopilotAgent(instructions="x", default_options={"mcp_servers": {}})
    merge_mcp_servers(agent, {"drawio": {"type": "stdio"}}, override=False)
    sent = agent._build_session_kwargs(True, None)
    assert sent["mcp_servers"] == {"drawio": {"type": "stdio"}}


def test_db_registry_outranks_repo_files() -> None:
    from orchestrator._tool_injection import merge_mcp_servers

    agent = SimpleNamespace(_default_options={})
    merge_mcp_servers(agent, {"s": {"command": "repo"}}, override=False)
    merge_mcp_servers(agent, {"s": {"command": "db"}}, override=True)
    assert agent._default_options["mcp_servers"]["s"]["command"] == "db"


def test_repo_file_does_not_clobber_existing() -> None:
    from orchestrator._tool_injection import merge_mcp_servers

    agent = SimpleNamespace(
        _default_options={"mcp_servers": {"s": {"command": "already"}}})
    merge_mcp_servers(agent, {"s": {"command": "repo"}}, override=False)
    assert agent._default_options["mcp_servers"]["s"]["command"] == "already"


# ---------------------------------------------------------------------------
# Scope merging: widen, never narrow
# ---------------------------------------------------------------------------

def test_agent_md_widens_an_existing_scope() -> None:
    import orchestrator.executor as ex

    spec = SimpleNamespace(tools=_REAL_TOOLS)
    merged = ex._merged_tool_scope(["web_search", "write_artifact"], spec)
    assert merged is not None
    # Original entries kept ...
    assert "web_search" in merged and "write_artifact" in merged
    # ... plus the capabilities the .agent.md declared.
    assert "github_search" in merged
    assert len(merged) == len(set(merged)), "no duplicates"


def test_unscoped_agent_is_never_narrowed_by_agent_md() -> None:
    """config.json with no tool_scope means 'inject everything'. An .agent.md
    tools list must not silently turn that into a restriction."""
    import orchestrator.executor as ex

    spec = SimpleNamespace(tools=_REAL_TOOLS)
    assert ex._merged_tool_scope(None, spec) is None
    assert ex._merged_tool_scope([], spec) is None


def test_no_agent_md_leaves_scope_untouched() -> None:
    import orchestrator.executor as ex

    assert ex._merged_tool_scope(["web_search"], None) == ["web_search"]
