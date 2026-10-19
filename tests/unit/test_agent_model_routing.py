"""Sonnet does the work, Opus takes the hard cases (owner directive, 2026-10-19).

R7 — the rule names its fence. The routing lives in three places, and each
one fails silently when it drifts:

* an agent file with no ``model:`` falls through to the MAIN session's model,
  which is Opus, so the saving vanishes and nothing says so;
* ``CLAUDE_CODE_SUBAGENT_MODEL`` gone from settings.json puts every ad-hoc
  general-purpose agent back on Opus;
* a ``CLAUDE_CODE_SUBAGENT_MODEL_FORCE`` would override every per-call
  ``model: "opus"``, so a HIGH-tier slice would quietly run on Sonnet.

Policy: CLAUDE.md §4. Table per role: .claude/commands/next-ticket.md step 1c.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_AGENTS = _ROOT / ".claude" / "agents"

#: The model each role runs on by default. A role moves only with the table.
EXPECTED = {
    "spec-auditor": "sonnet",
    "ws-implementer": "sonnet",
    "ws-verifier": "sonnet",
    # Adversarial review is the one job where the stronger model pays.
    "diff-reviewer": "opus",
}


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    assert match, f"{path.name} has no frontmatter"
    return dict(
        (k.strip(), v.strip())
        for k, v in (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    )


def _settings() -> dict:
    return json.loads((_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))


def test_every_agent_file_is_in_the_table():
    on_disk = {p.stem for p in _AGENTS.glob("*.md")}
    assert on_disk == set(EXPECTED), (
        "add the new agent to EXPECTED and to next-ticket.md step 1c, with a model"
    )


@pytest.mark.parametrize("name,model", sorted(EXPECTED.items()))
def test_each_agent_names_its_model(name, model):
    assert _frontmatter(_AGENTS / f"{name}.md").get("model") == model


def test_ad_hoc_subagents_default_to_sonnet():
    assert _settings()["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "sonnet"


def test_nothing_forces_one_model_on_every_subagent():
    assert "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" not in _settings().get("env", {})


def test_the_supervisor_carries_the_table():
    text = (_ROOT / ".claude" / "commands" / "next-ticket.md").read_text(encoding="utf-8")
    assert "Pick the model for each dispatch" in text
    for role in EXPECTED:
        assert re.search(rf"^\| `{role}` \|", text, re.M), f"{role} is missing from step 1c"


def test_claude_md_states_the_rule():
    text = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Sonnet does the work. Opus takes the hard cases." in text
