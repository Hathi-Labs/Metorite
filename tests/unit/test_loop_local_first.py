"""The agent loop runs each change on the local stack (LD-2), and the owner
sees it before the gates run (LD-7, D145).

Spec: `specs/engineering_practice.md` §1.2, §1.3 and §7a ·
`.claude/commands/next-ticket.md`.

Owner request, 2026-10-30: find UI and function bugs on the PC. Owner
directive, 2026-10-31 (D145): preview, owner approval, the local staging gate,
merge, and the daily release. These tests fail when an edit drops the local
run, the preview or the gate from the loop, lets a HIGH-tier slice skip the
spec audit, or brings back a dispatch after each merge.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEXT_TICKET = (ROOT / ".claude" / "commands" / "next-ticket.md").read_text(encoding="utf-8")
IMPLEMENTER = (ROOT / ".claude" / "agents" / "ws-implementer.md").read_text(encoding="utf-8")
VERIFIER = (ROOT / ".claude" / "agents" / "ws-verifier.md").read_text(encoding="utf-8")
PRACTICE = (ROOT / "project-docs" / "specs" / "engineering_practice.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """One space between words, so a line break never hides a phrase."""
    return " ".join(text.split())


def _land() -> str:
    return NEXT_TICKET[NEXT_TICKET.index("**7 — Land.**"):NEXT_TICKET.index("## Refusals")]


def test_the_implementer_runs_the_change_locally():
    assert "bash scripts/dev_up.sh" in IMPLEMENTER
    assert "LOCAL RUN:" in IMPLEMENTER


def test_the_verifier_checks_behaviour_on_the_local_stack():
    assert "bash scripts/dev_up.sh --check" in VERIFIER
    assert "LOCAL:" in VERIFIER


def test_the_loop_names_the_local_run_in_build_and_verify():
    build = NEXT_TICKET[NEXT_TICKET.index("**4 — Build.**"):NEXT_TICKET.index("**6 — Review.**")]
    assert "bash scripts/dev_up.sh" in build
    assert "--check" in build


def test_only_a_small_slice_skips_the_audit():
    audit = NEXT_TICKET[NEXT_TICKET.index("**2 — Audit.**"):NEXT_TICKET.index("**3 — Branch.**")]
    assert "AUDIT: skipped (small slice)" in audit
    assert "A HIGH-tier slice, and the first slice of a spec, always get the audit." in audit


def test_the_owner_sees_a_preview_before_the_gates():
    preview = NEXT_TICKET[NEXT_TICKET.index("**4b — Preview"):NEXT_TICKET.index("**5 — Verify.**")]
    assert "ask_on_task" in preview
    assert "**Approve**" in preview and "**Change it**" in preview
    assert "AWAITING-OWNER" in preview
    assert "nothing to see" in preview


def test_the_local_gate_runs_before_the_merge():
    land = _land()
    assert "bash scripts/stage_local.sh" in land
    assert land.index("stage_local.sh") < land.index("gh pr merge")
    assert "local-staging" in land


def test_no_dispatch_follows_a_merge():
    land = _flat(_land())
    assert "Do not dispatch `deploy.yml` after a merge" in land
    assert "reaches production at tonight's release" in land


def test_the_spec_names_what_the_pc_cannot_test():
    section = PRACTICE[PRACTICE.index("#### 1.3.2"):PRACTICE.index("## 2. Release")]
    for item in ("OAuth", "model calls", "deploy/*.sh", "smoke"):
        assert item in section, item
