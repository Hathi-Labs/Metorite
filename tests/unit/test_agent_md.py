"""Tests for the .github/agents/<name>.agent.md parser (Copilot SDK defs)."""
from __future__ import annotations

from pathlib import Path

from acb_skills import find_agent_md, load_agent_md, parse_agent_md

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parse_real_metorite_agent_md() -> None:
    """The repo's own .github/agents/metorite.agent.md parses fully."""
    spec = load_agent_md(REPO_ROOT, "metorite")
    assert spec is not None
    assert spec.name == "Metorite"
    assert spec.model == "claude-sonnet-4-5"
    # tools frontmatter is the VS Code Copilot vocabulary (advisory).
    assert "editFiles" in spec.tools
    assert "terminal" in spec.tools
    # the markdown body becomes the agent's system prompt.
    assert spec.body.startswith("# Metorite Self-Anneal Agent")
    assert "senior software engineer" in spec.body


def test_parse_agent_md_minimal() -> None:
    spec = parse_agent_md(
        "---\nname: foo\nmodel: m1\ntools:\n  - a\n  - b\n---\nHello body\n"
    )
    assert spec is not None
    assert spec.name == "foo"
    assert spec.model == "m1"
    assert spec.tools == ["a", "b"]
    assert spec.body == "Hello body"


def test_parse_agent_md_scalar_tools() -> None:
    spec = parse_agent_md("---\nname: foo\ntools: just_one\n---\nbody")
    assert spec is not None
    assert spec.tools == ["just_one"]


def test_parse_agent_md_no_frontmatter_uses_body() -> None:
    spec = parse_agent_md("Just a system prompt with no frontmatter.")
    assert spec is not None
    assert spec.body == "Just a system prompt with no frontmatter."
    assert spec.model is None
    assert spec.tools == []


def test_parse_agent_md_empty_returns_none() -> None:
    assert parse_agent_md("") is None
    assert parse_agent_md("---\n---\n") is None


def test_parse_agent_md_malformed_yaml_does_not_raise() -> None:
    # Unparseable frontmatter -> empty mapping, body still recovered.
    spec = parse_agent_md("---\nname: : : bad\n---\nbody text")
    assert spec is not None
    assert spec.body == "body text"


def test_find_agent_md_missing_dir_returns_none(tmp_path: Path) -> None:
    assert find_agent_md(tmp_path, "anything") is None
    assert load_agent_md(tmp_path, "anything") is None


def test_find_agent_md_prefers_name_match(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "aaa.agent.md").write_text(
        "---\nname: aaa\n---\nbody a", encoding="utf-8"
    )
    (agents_dir / "target.agent.md").write_text(
        "---\nname: target\n---\nbody target", encoding="utf-8"
    )
    found = find_agent_md(tmp_path, "target")
    assert found is not None and found.name == "target.agent.md"


def test_find_agent_md_tolerates_agent_prefix(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "sales.agent.md").write_text(
        "---\nname: sales\n---\nbody", encoding="utf-8"
    )
    # registry name is "agent-sales"; file is "sales.agent.md".
    found = find_agent_md(tmp_path, "agent-sales")
    assert found is not None and found.name == "sales.agent.md"


def test_find_agent_md_frontmatter_name_match(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    # filename differs from the frontmatter name; match on frontmatter.
    (agents_dir / "definition.agent.md").write_text(
        "---\nname: MyAgent\n---\nbody", encoding="utf-8"
    )
    found = find_agent_md(tmp_path, "myagent")
    assert found is not None and found.name == "definition.agent.md"
