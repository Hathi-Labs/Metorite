"""Skill registry (v1) + Dynamic Agent Loader (v2) + Integration Registry."""
from acb_skills.agent_tools import call_agent, call_agent_background
from acb_skills.web_tools import fetch_page, web_search
from acb_skills.write_artifact import (
    write_artifact, share_artifact, artifact_context,
)
from acb_skills.todo_tools import manage_todo_list
from acb_skills.ask_tools import ask_questions
from acb_skills.error_tools import get_errors
from acb_skills.note_tools import save_note, recall_notes
from acb_skills.history_tools import query_history
from acb_skills.github_tools import github_search, github_repo_search
from acb_skills.integrations import (
    IntegrationMisconfiguredError,
    IntegrationNotFoundError,
    build_integrations,
    list_registered,
)
from acb_skills.tool_annotations import (
    TOOL_ANNOTATIONS,
    annotate,
    get_annotations,
    is_destructive,
    risk_summary_block,
)
from acb_skills.agent_md import AgentMd, find_agent_md, load_agent_md, parse_agent_md
from acb_skills.loader import AgentLoadError, LoadedAgent, load_agent
from acb_skills.registry import Skill, SkillFrontmatter, load_skill, load_skills

__all__ = [
    # v1 — SKILL.md registry
    "Skill",
    "SkillFrontmatter",
    "load_skill",
    "load_skills",
    # v2 — Dynamic Agent Loader (ADR-013)
    "load_agent",
    "AgentLoadError",
    "LoadedAgent",
    # Copilot SDK agent definition (.github/agents/<name>.agent.md)
    "AgentMd",
    "parse_agent_md",
    "find_agent_md",
    "load_agent_md",
    # v2 — Integration Registry
    "build_integrations",
    "list_registered",
    "IntegrationNotFoundError",
    "IntegrationMisconfiguredError",
    # Agent delegation tools (auto-injected; also importable explicitly)
    "call_agent",
    "call_agent_background",
    # Zero-credential web tools (auto-injected; also importable explicitly)
    "web_search",
    "fetch_page",
    # File-writing tool (auto-injected; also importable explicitly)
    "write_artifact",
    "share_artifact",
    "artifact_context",
    # Todo-list management tool (auto-injected; also importable explicitly)
    "manage_todo_list",
    # HITL elicitation tool (auto-injected)
    "ask_questions",
    # Tool risk annotations (HH-2)
    "TOOL_ANNOTATIONS",
    "annotate",
    "get_annotations",
    "is_destructive",
    "risk_summary_block",
    # Code error-checking tool (auto-injected)
    "get_errors",
    # Repo-scoped note tools (auto-injected)
    "save_note",
    "recall_notes",
    # Session history query tool (auto-injected)
    "query_history",
    # GitHub code search tools (auto-injected)
    "github_search",
    "github_repo_search",
]