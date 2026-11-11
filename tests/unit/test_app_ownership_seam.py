"""The app lead has ONE seam: ``authority.py`` (WS-MCP SW-48).

Spec: ``project-docs/specs/agent_coordination.md`` §3.10 rule 1 and §5.10
SW-48. A rule that keys on the lead of a project reads it through
``gateway.routes.projects.authority``. A rule in another module EMBEDS the
public walk ``authority.led_subtree_sql`` and never copies it, and no module
keeps a second owner table.

R7 fences named here:

* ``lead-seam-one-walk``: no module outside the allow-list uses one of the
  forms in ``_LEAD_TEST`` (``lower(lead) =``, ``WHERE lead =``,
  ``pm_projects.lead =``, a Python test on ``.lead``). This is a text check.
  A comparison written in another form, such as ``AND p.lead = :me``, passes
  it. The authority is the review of a change that touches a lead rule.
* ``lead-seam-end-rule-embeds``: ``agent_runs.person_may_end_sql`` holds the
  text of ``led_subtree_sql()``, and ``agent_runs.py`` and
  ``needs_attention.py`` hold no lead walk of their own.

Hermetic. It reads source text only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"

#: The forms that compare the lead column: the address test in SQL, a
#: ``WHERE lead =``, a qualified ``pm_projects.lead =``, and a Python test on a
#: row's ``lead``.
_LEAD_TEST = re.compile(
    r"lower\(\s*(?:\w+\.)?lead\s*\)\s*="
    r"|\bWHERE\s+(?:\w+\.)?lead\s*="
    r"|pm_projects\.lead\s*="
    r"|\.lead\s*(?:==|!=)"
    r"|\[\s*['\"]lead['\"]\s*\]\s*(?:==|!=)"
    r"|get\(\s*['\"]lead['\"]\s*\)\s*(?:==|!=)",
    re.IGNORECASE,
)

#: Each module that may compare the lead column, and the reason.
_ALLOWED: dict[str, str] = {
    # THE seam. It owns the walk (`led_subtree_sql`) and every rule on it.
    "apps/services/gateway/gateway/routes/projects/authority.py": "the seam",
    # `GET /projects/my/led` (WS-39 S6e). A display read of the projects that
    # the member leads. It grants and refuses nothing (D140.2).
    "apps/services/gateway/gateway/routes/projects/personal.py": "display read",
}


def _sources() -> list[Path]:
    found: list[Path] = []
    for base in (_ROOT / "apps", _ROOT / "packages"):
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"tests", "node_modules", ".venv", "__pycache__"}:
                continue
            found.append(path)
    return found


def test_no_module_outside_the_seam_compares_the_lead() -> None:
    """``lead-seam-one-walk``. Mutation: paste ``lower(lead) = :me`` into
    ``agent_runs.py``, and this test names the file."""
    offenders: list[str] = []
    for path in _sources():
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(body.splitlines(), 1):
            if _LEAD_TEST.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == [], (
        "A rule on the project lead reads it through authority.py. Embed"
        " authority.led_subtree_sql, or call a function of authority.py:\n"
        + "\n".join(offenders)
    )


def test_each_allowed_module_exists() -> None:
    """A stale allow-list entry hides nothing, so it fails."""
    for rel in _ALLOWED:
        assert (_ROOT / rel).is_file(), rel


@pytest.mark.parametrize("name", ["agent_runs.py", "needs_attention.py"])
def test_the_run_modules_hold_no_walk_of_their_own(name: str) -> None:
    """``lead-seam-end-rule-embeds``, the source half."""
    body = (_PROJECTS / name).read_text(encoding="utf-8")
    assert "WITH RECURSIVE" not in body, name
    assert not re.search(r"\blead\b\s*=\s*:", body), name


def test_the_end_rule_embeds_the_public_walk() -> None:
    """``lead-seam-end-rule-embeds``, the text half. Mutation: drop the lead
    arm of ``person_may_end_sql``, and this fails."""
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects import agent_runs
    from gateway.routes.projects.authority import led_subtree_sql

    assert led_subtree_sql() in agent_runs.person_may_end_sql("r")
    src = (_PROJECTS / "agent_runs.py").read_text(encoding="utf-8")
    assert "from gateway.routes.projects.authority import led_subtree_sql" in src
