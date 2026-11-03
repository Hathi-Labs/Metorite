"""RX-7 item 5 — the Projects agent does not set a reminder twice.

The create route answers ``already: true`` for a repeat, and the receipt head
reads "Reminder already set". The agent's instructions must say what to do.

Mutation: delete the "already exists" bullet from ``instructions.md``, and both
tests fail.
"""
from __future__ import annotations

from pathlib import Path

TEXT = (Path(__file__).resolve().parents[2] / "apps" / "agents" / "agent-projects"
        / "instructions.md").read_text(encoding="utf-8")


def test_the_instructions_name_the_already_set_receipt() -> None:
    assert "Reminder\n  already set" in TEXT.replace("\r\n", "\n")


def test_the_instructions_forbid_a_second_set_reminder() -> None:
    flat = " ".join(TEXT.split())
    assert "Do not call `set_reminder` again" in flat
