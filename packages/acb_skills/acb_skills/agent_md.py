"""Parse ``.github/agents/<name>.agent.md`` — the Copilot SDK agent definition.

GitHub Copilot Chat (and the Copilot SDK) author an agent's identity in a
single Markdown file under ``.github/agents/``:

    ---
    name: Metorite
    description: >
      Self-anneal agent for the Metorite platform...
    model: claude-sonnet-4-5
    tools:
      - runCommands
      - editFiles
      - terminal
    ---
    # Metorite Self-Anneal Agent
    You are a senior software engineer...        <- inline system prompt

Metorite wraps Copilot SDK agents inside MAF, so historically the
runtime built each agent from ``agents.py`` / ``instructions.md`` and the
``.agent.md`` file was only consumed by VS Code — never by the deployment.

This module gives the runtime a defensive reader for that file so a live
chat (or any agent run) honours the agent's authored instructions, model,
and (advisory) tool list.  The ``tools`` field uses VS Code Copilot's
vocabulary (``editFiles``/``terminal``/...) which does not map 1:1 onto
Metorite's platform-injected tools, so it is surfaced as advisory
metadata only — it never restricts what the agent can actually call.

Parsing mirrors :mod:`acb_skills.registry`'s frontmatter conventions:
``---``-delimited YAML, UTF-8-SIG tolerant, never raises on malformed input.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DELIM = "---"


@dataclass
class AgentMd:
    """Parsed ``.github/agents/<name>.agent.md`` definition."""

    name: str
    description: str = ""
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    body: str = ""          # the inline system prompt (markdown after frontmatter)
    path: Path | None = None


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a ``---``-delimited YAML frontmatter block off the top of *text*.

    Returns ``({}, text)`` when no frontmatter is present.  Never raises on
    malformed YAML — a bad block yields an empty mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIM:
            end = i
            break
    if end is None:
        return {}, text
    try:
        fm = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return fm, body


def _coerce_tools(raw: Any) -> list[str]:
    """Normalise the ``tools`` frontmatter value to a list of strings."""
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def parse_agent_md(text: str, *, path: Path | None = None) -> AgentMd | None:
    """Parse the raw contents of an ``.agent.md`` file into an :class:`AgentMd`.

    Returns ``None`` when the file has no usable identity (no ``name`` and no
    body) so callers can treat "nothing to apply" uniformly.
    """
    fm, body = _split_frontmatter(text)
    name = str(fm.get("name") or "").strip()
    body = (body or "").strip()
    if not name and not body:
        return None
    model = fm.get("model")
    return AgentMd(
        name=name or (path.stem.replace(".agent", "") if path else ""),
        description=str(fm.get("description") or "").strip(),
        model=str(model).strip() if model else None,
        tools=_coerce_tools(fm.get("tools")),
        body=body,
        path=path,
    )


# ── VS Code vocabulary → this runtime ─────────────────────────────────────
# A `.agent.md` is authored for VS Code Copilot, but here the agent runs
# headless inside MAF on a BYOK model. Its `tools:` names are VS Code IDE tool
# ids that exist in NO tool schema we build, so the model either calls a tool
# that isn't there or improvises onto the Copilot CLI's native bash/create/edit.
# Map each to the platform tool that actually does that job. Names mapping to
# () are already covered by the CLI's own native tools (bash/create/edit), so
# they need no platform tool — they only need the prompt note below.
_VSCODE_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "codebase": ("github_search", "github_repo_search"),
    "search": ("github_search", "github_repo_search"),
    "usages": ("github_search",),
    "githubRepo": ("github_repo_search",),
    "fetch": ("fetch_page", "web_search"),
    "openSimpleBrowser": ("fetch_page",),
    "problems": ("run_diagnostics", "get_errors"),
    "testFailure": ("run_diagnostics", "get_errors"),
    "editFiles": ("write_artifact", "share_artifact"),
    "new": ("write_artifact",),
    "runCommands": (),   # → the CLI's native bash
    "runTasks": (),      # → bash
    "terminal": (),      # → bash
    "changes": (),       # → bash (git)
    "extensions": (),    # no equivalent — IDE-only
    "vscodeAPI": (),     # no equivalent — IDE-only
}

# What to tell the model, in its own prompt, about the gap between the tool
# names its .agent.md advertises and the ones it actually has.
_RUNTIME_NOTE = """
---
## Runtime note (Metorite)

You are NOT running inside VS Code — you run headless in the Metorite
runtime. Your definition lists VS Code tool names; use these instead:

- `editFiles` / `new` → **write_artifact(path, content)** — pass the file's
  content directly. Do NOT build files with shell heredocs / echo / printf /
  base64: that quoting is fragile and large writes get truncated mid-file.
- `runCommands` / `terminal` / `runTasks` / `changes` → your native **bash**
  tool (use it to RUN things, not to author file content).
- `codebase` / `search` / `usages` → **github_search** / **github_repo_search**.
- `problems` / `testFailure` → **run_diagnostics**.
- `fetch` / `openSimpleBrowser` → **fetch_page** / **web_search**.

There is no Problems panel, no `#codebase` index, no editor UI, and no VS Code
extension host. Never ask the user to run a VS Code command or open a panel.
"""


def derive_tool_scope(tools: list[str]) -> list[str]:
    """Platform tool names implied by a `.agent.md` VS Code ``tools:`` list.

    Turns the advisory VS Code vocabulary into real, injectable tool names so a
    declared capability (``codebase`` → code search) actually exists at run
    time. Unknown/IDE-only names simply contribute nothing.
    """
    scope: list[str] = []
    for t in tools:
        for mapped in _VSCODE_TOOL_MAP.get(str(t).strip(), ()):
            if mapped not in scope:
                scope.append(mapped)
    return scope


def uses_vscode_tool_vocabulary(tools: list[str]) -> bool:
    """True if any declared tool is a VS Code IDE tool id (not one of ours)."""
    return any(str(t).strip() in _VSCODE_TOOL_MAP for t in tools)


def runtime_note_for(tools: list[str]) -> str:
    """The prompt note reconciling VS Code tool names with this runtime.

    Returned only for agents that actually speak the VS Code vocabulary — an
    agent authored for Metorite needs no such correction. Additive: it
    never rewrites the author's prose, it just stops the prose from pointing at
    affordances that don't exist here.
    """
    return _RUNTIME_NOTE if uses_vscode_tool_vocabulary(tools) else ""


def _coerce_mcp_entry(raw: Any) -> dict[str, Any] | None:
    """Normalise one MCP server entry to the Copilot SDK's MCPServerConfig.

    Accepts the VS Code / Claude file shapes and emits what the SDK's
    ``MCPLocalServerConfig`` / ``MCPRemoteServerConfig`` expect (note ``type``,
    not ``transport``). ``tools: ["*"]`` because a file-declared server is
    declared to be used — the SDK treats ``[]`` as "expose none".
    """
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if url:
        entry: dict[str, Any] = {
            "type": raw.get("type") if raw.get("type") in ("http", "sse") else "http",
            "url": str(url),
            "tools": raw.get("tools") or ["*"],
        }
        if isinstance(raw.get("headers"), dict):
            entry["headers"] = {str(k): str(v) for k, v in raw["headers"].items()}
        return entry
    command = raw.get("command")
    if not command:
        return None
    entry = {
        "type": "stdio",
        "command": str(command),
        "args": [str(a) for a in (raw.get("args") or [])],
        "tools": raw.get("tools") or ["*"],
    }
    if isinstance(raw.get("env"), dict):
        entry["env"] = {str(k): str(v) for k, v in raw["env"].items()}
    if raw.get("cwd"):
        entry["cwd"] = str(raw["cwd"])
    return entry


def load_repo_mcp_servers(agent_dir: Path | str) -> dict[str, dict[str, Any]]:
    """MCP servers an agent repo declares in its own files.

    A VS Code-authored agent declares its MCP servers in ``.vscode/mcp.json``
    (``{"servers": {...}}``) or ``.mcp.json`` (``{"mcpServers": {...}}``).
    Neither was ever read here — the runtime only looked at ``config.json``
    and the ``mcp_servers`` DB table — so an agent that depends on an MCP
    server (e.g. a diagramming agent declaring draw.io) silently lost it and
    then failed at the task it was built for.

    Both files are read (``.mcp.json`` wins on conflict, being the
    runtime-agnostic one). Fully defensive: missing/malformed files yield {}.
    """
    base = Path(agent_dir)
    out: dict[str, dict[str, Any]] = {}
    for rel, key in ((".vscode/mcp.json", "servers"), (".mcp.json", "mcpServers")):
        try:
            raw = json.loads((base / rel).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        # Tolerate either top-level key in either file.
        servers = raw.get(key) or raw.get("servers") or raw.get("mcpServers") or {}
        if not isinstance(servers, dict):
            continue
        for name, cfg in servers.items():
            entry = _coerce_mcp_entry(cfg)
            if entry:
                out[str(name)] = entry
    return out


def find_agent_md(agent_dir: Path | str, agent_name: str | None = None) -> Path | None:
    """Locate the best ``.agent.md`` for *agent_name* under *agent_dir*.

    Search order inside ``<agent_dir>/.github/agents/``:
      1. ``<agent_name>.agent.md`` (exact filename match)
      2. a file whose frontmatter ``name`` matches *agent_name* (case-insensitive)
      3. the sole ``*.agent.md`` file if exactly one exists
      4. the first ``*.agent.md`` file alphabetically

    Returns ``None`` when the directory or any matching file is absent.
    """
    agents_dir = Path(agent_dir) / ".github" / "agents"
    if not agents_dir.is_dir():
        return None
    candidates = sorted(agents_dir.glob("*.agent.md"))
    if not candidates:
        return None

    norm = (agent_name or "").strip().lower()
    if norm:
        # 1. exact filename (<name>.agent.md), tolerant of an "agent-" prefix.
        for stem in (norm, norm.removeprefix("agent-")):
            exact = agents_dir / f"{stem}.agent.md"
            if exact in candidates:
                return exact
        # 2. frontmatter name match.
        for cand in candidates:
            try:
                fm, _ = _split_frontmatter(cand.read_text(encoding="utf-8-sig"))
            except OSError:
                continue
            if str(fm.get("name") or "").strip().lower() == norm:
                return cand

    # 3 & 4. sole file, else first alphabetically.
    return candidates[0]


def load_agent_md(agent_dir: Path | str, agent_name: str | None = None) -> AgentMd | None:
    """Load and parse the ``.agent.md`` for *agent_name*, or ``None``.

    Fully defensive: a missing directory, unreadable file, or malformed
    frontmatter all yield ``None`` so the caller never has to guard a run.
    """
    md_path = find_agent_md(agent_dir, agent_name)
    if md_path is None:
        return None
    try:
        text = md_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    return parse_agent_md(text, path=md_path)
