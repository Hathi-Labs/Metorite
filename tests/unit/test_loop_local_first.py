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


def test_github_pr_check_is_the_gate():
    """D218: pr-check gates every slice. ci_local.sh is optional. The local
    staging gate runs only for a migration or a build-path slice."""
    land = _flat(_land())
    assert "The gate is GitHub `pr-check` (D218)" in land
    assert "`bash scripts/ci_local.sh` is optional" in land
    assert "adds a migration or changes the build path" in land
    claude = _flat((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert "GitHub `pr-check` is the gate (D218)" in claude
    assert "Gate on GitHub (D218)" in claude
    assert "The gate (D218)" in PRACTICE


def test_the_preview_gates_the_flag_flip():
    """D218: a slice behind an off flag merges on green, and the owner
    approves the flag flip. A new screen with no flag still waits."""
    preview = _flat(NEXT_TICKET[NEXT_TICKET.index("**4b — Preview"):NEXT_TICKET.index("**5 — Verify.**")])
    assert "Approve the flag flip" in preview
    assert "with no flag still waits" in preview
    assert "A HIGH slice keeps its full loop" in preview
    claude = _flat((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert "A flag flips only after the owner approves (D218)" in claude
    assert "Approve the flag flip" in _flat(PRACTICE)


def test_no_dispatch_follows_a_merge():
    land = _flat(_land())
    assert "Do not dispatch `deploy.yml` after a merge" in land
    # CIV-21 (D194): three release slots a day, so the next slot, not tonight.
    assert "reaches production at the next release slot" in land


def test_a_low_or_standard_slice_hands_over_and_a_high_slice_merges_itself():
    """CIV-28c fix round 1: a LOW or STANDARD session hands its PR to the merge bot,
    then waits for the bot's line and acts on it. Nothing else on the box can write
    to Metorite. The bot never merges HIGH, so a HIGH slice keeps the
    premerge-and-merge command. The wrapper that the step names exists."""
    land = _flat(_land())
    hand = land[
        land.index("**LOW or STANDARD: hand it over.**") : land.index("**HIGH: merge it yourself**")
    ]
    assert "bash scripts/hand_over.sh <PR> <task> <tier>" in hand
    assert "`waiting: {on: ci, until_minutes: 120}`" in hand
    assert "bash scripts/hand_over.sh --wait <PR>" in hand
    done = hand[hand.index("**DONE (exit 0).**") : hand.index("**BACK (exit 1).**")]
    assert "move the task to done" in done and "Watch the batch of `main`" in done
    assert "Remove the worktree" in done and "`finish_agent_run`" in done
    back = hand[hand.index("**BACK (exit 1).**") : hand.index("**No line (exit 3).**")]
    assert "Keep the worktree" in back and "merge it yourself, as for HIGH" in back
    assert "merge it yourself, as for HIGH" in hand[hand.index("**No line (exit 3).**") :]
    # The session never ends at the hand-over.
    assert "end the session" not in hand
    high = land[land.index("**HIGH: merge it yourself**") :]
    assert "bash scripts/premerge.sh > <log> 2>&1 && gh pr merge <n> --squash" in high
    assert "**A stacked slice**" in high
    # The batch watch binds after a bot merge until the automatic revert is on.
    assert "until `AUTO_REVERT_MODE=on`" in land
    assert (ROOT / "scripts" / "hand_over.sh").is_file()


def test_the_spec_names_what_the_pc_cannot_test():
    section = PRACTICE[PRACTICE.index("#### 1.3.2"):PRACTICE.index("## 2. Release")]
    for item in ("OAuth", "model calls", "deploy/*.sh", "smoke"):
        assert item in section, item


def test_a_small_screen_change_exception_is_pinned_everywhere():
    """D200: a small, low-risk screen change merges before the owner's review."""
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "D200" in claude
    assert "D200" in PRACTICE
    preview = NEXT_TICKET[NEXT_TICKET.index("**4b — Preview"):NEXT_TICKET.index("**5 — Verify.**")]
    assert "D200" in preview and "Looks right" in preview
