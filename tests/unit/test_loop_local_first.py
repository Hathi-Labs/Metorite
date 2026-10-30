"""The agent loop runs each change on the local stack before staging (LD-2).

Spec: `specs/engineering_practice.md` §1.2 and §7a · `.claude/commands/next-ticket.md`.

Owner request, 2026-10-30: find UI and function bugs on the PC, and use
staging as a smoke check. These tests fail when an edit drops the local run
from the loop, or lets a HIGH-tier slice skip the spec audit.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEXT_TICKET = (ROOT / ".claude" / "commands" / "next-ticket.md").read_text(encoding="utf-8")
IMPLEMENTER = (ROOT / ".claude" / "agents" / "ws-implementer.md").read_text(encoding="utf-8")
VERIFIER = (ROOT / ".claude" / "agents" / "ws-verifier.md").read_text(encoding="utf-8")


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


def test_staging_still_tests_what_the_pc_cannot():
    assert "Test in full on staging only what the PC cannot" in NEXT_TICKET
    for item in ("deploy path", "OAuth", "webhook", "production-shaped data"):
        assert item in NEXT_TICKET, item
