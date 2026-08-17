"""metorite — Self-anneal agent for the Metorite platform.

A GitHub Copilot SDK agent that works on the Metorite repository itself.
Uses the Copilot SDK's native file operations, shell commands, and git tooling
to read, edit, test, and improve the CC codebase.

The executor injects platform tools (call_agent, web_search, write_artifact,
memory, todo, etc.) at runtime — this file only needs to define the agent's
identity and system prompt.

Exports:
    build_agents() -> list[GitHubCopilotAgent]
"""
from __future__ import annotations

from pathlib import Path

AGENT_DIR = Path(__file__).parent.resolve()
PROMPTS_DIR = AGENT_DIR / ".github" / "prompts"
SKILLS_DIR = AGENT_DIR / ".github" / "skills"

_SYSTEM_MD = PROMPTS_DIR / "system.md"
if _SYSTEM_MD.exists():
    SYSTEM_PROMPT = _SYSTEM_MD.read_text(encoding="utf-8", errors="replace")
else:
    # Fallback — load the root AGENTS.md as a minimal system prompt.
    _AGENTS_MD = AGENT_DIR / "AGENTS.md"
    SYSTEM_PROMPT = (
        _AGENTS_MD.read_text(encoding="utf-8", errors="replace")
        if _AGENTS_MD.exists()
        else "You are the Metorite self-anneal agent."
    )


def _skill_description(skill_md: Path) -> str:
    """Extract just the ``description:`` from a SKILL.md's YAML frontmatter.

    We surface the when-to-use description only — NOT the full SKILL.md body
    (task-observer's is ~75KB; inlining every body would bloat every run's
    context). The agent reads the linked file on demand for the full procedure.
    """
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else text[3:]
    # description may be a single line or a folded/multi-line (">") block.
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("description:"):
            val = line.split("description:", 1)[1].strip()
            if val and val not in (">", "|", ">-", "|-"):
                return val
            # folded block: gather subsequent indented lines
            parts: list[str] = []
            for cont in lines[i + 1:]:
                if cont.strip() == "" or cont[:1] in (" ", "\t"):
                    parts.append(cont.strip())
                else:
                    break
            return " ".join(p for p in parts if p)
    return ""


def _append_skills(base: str) -> str:
    """Surface each ``.github/skills/*/SKILL.md`` (name + when-to-use) so the
    agent KNOWS its dev skills exist and reaches for them.

    The Copilot SDK does not auto-inject repo skill files into this agent's
    prompt (unlike the DOE-v2 agents that build them in), so a committed skill
    sits on disk unused unless surfaced here. We inline only the description as
    a compact catalog and link the file for the full procedure. Best-effort —
    a missing/unreadable dir never breaks the agent build.
    """
    if not SKILLS_DIR.is_dir():
        return base
    rows: list[str] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        desc = _skill_description(skill_md)
        rel = skill_md.relative_to(AGENT_DIR)
        rows.append(
            f"- **{skill_md.parent.name}** (`{rel}`): {desc or '(see SKILL.md)'}"
        )
    if not rows:
        return base
    return (
        base
        + "\n\n---\n\n## Available Dev Skills (`.github/skills/`)\n\n"
        "Reach for these when relevant; read the linked SKILL.md for the full "
        "procedure. Use **task-observer** at the START of any multi-step task, "
        "and **impeccable** for Control Plane UI work.\n\n"
        + "\n".join(rows)
    )


SYSTEM_PROMPT = _append_skills(SYSTEM_PROMPT)


def build_agent():
    """Return a GitHubCopilotAgent configured for the Metorite repo."""
    from agent_framework_github_copilot import GitHubCopilotAgent  # type: ignore[import]  # noqa: PLC0415
    from copilot.types import PermissionHandler  # type: ignore[import]  # noqa: PLC0415

    return GitHubCopilotAgent(
        name="metorite",
        description=(
            "Self-anneal agent for the Metorite orchestration platform "
            "— edit code, run tests, debug issues, review agent repos, "
            "and improve the CC platform itself."
        ),
        instructions=SYSTEM_PROMPT,
        tools=[],
        default_options={
            "model": "tier-balanced",
            "on_permission_request": PermissionHandler.approve_all,
        },
    )


def build_agents() -> list:
    """Dynamic Agent Loader entry point. Synchronous, zero-argument, pure."""
    return [build_agent()]


__all__ = ["build_agents", "build_agent", "SYSTEM_PROMPT"]
