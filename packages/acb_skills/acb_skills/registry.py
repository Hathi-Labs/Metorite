"""Walk the skills/ tree and parse SKILL.md frontmatter+body.

Layout assumption (ADR-013 / WBS 0.5.1):
    skills/<domain>/<skill_id>/SKILL.md     # production skills
    skills/examples/<skill_id>/SKILL.md     # demo/sanity skills

Anything under `upstream/` is excluded from the production registry by default.
The vendored mirror that once sat at `skills/upstream/` was deleted on
2026-10-27, and the filter stays so a copy that comes back cannot run unreviewed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    when_to_use: str | None = None
    domain: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    authority: str = "read"           # read | suggest | suggest_apply | autonomous
    cost_tier: int = 1                # 1 cheap, 2 mid, 3 frontier
    version: str = "0.0.0"
    provenance: str | None = None
    rollout_stage: str = "shadow"     # shadow | canary | live | retired
    success_rate_30d: float | None = None
    cases_seen_30d: int = 0


class Skill(BaseModel):
    """Parsed SKILL.md ready to hand to the orchestrator."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    domain: str
    skill_id: str
    frontmatter: SkillFrontmatter
    body: str

    @property
    def fqid(self) -> str:
        return f"{self.domain}/{self.skill_id}"


_DELIM = "---"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a YAML frontmatter block off the top of a Markdown file."""
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
    fm = yaml.safe_load("\n".join(lines[1:end])) or {}
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return fm, body


def load_skill(skill_md: Path) -> Skill:
    """Parse a single SKILL.md. `domain`/`skill_id` are derived from the path."""
    text = skill_md.read_text(encoding="utf-8-sig")  # strip BOM if present
    fm_raw, body = _split_frontmatter(text)
    fm = SkillFrontmatter(**fm_raw)
    # path layout: .../skills/<domain>/<skill_id>/SKILL.md
    parts = skill_md.resolve().parts
    try:
        idx = parts.index("skills")
        domain = parts[idx + 1]
        skill_id = parts[idx + 2]
    except (ValueError, IndexError):
        domain = fm.domain or "_unknown"
        skill_id = fm.name
    return Skill(
        path=skill_md.resolve(),
        domain=domain,
        skill_id=skill_id,
        frontmatter=fm,
        body=body,
    )


def load_skills(
    root: str | Path = "skills",
    *,
    include_examples: bool = False,
    include_upstream: bool = False,
) -> list[Skill]:
    """Walk `root` and return every SKILL.md as a Skill.

    By default we ignore `examples/` and `upstream/` so the orchestrator never
    accidentally runs a sanity stub or an unreviewed upstream skill.
    """
    base = Path(root).resolve()
    if not base.exists():
        return []
    skills: list[Skill] = []
    for md in base.rglob("SKILL.md"):
        rel_parts = md.resolve().relative_to(base).parts
        if not include_examples and rel_parts[0] == "examples":
            continue
        if not include_upstream and rel_parts[0] == "upstream":
            continue
        try:
            skills.append(load_skill(md))
        except Exception:  # pragma: no cover - surface in CI
            # A broken SKILL.md should never crash the registry.
            continue
    skills.sort(key=lambda s: (s.domain, s.skill_id))
    return skills