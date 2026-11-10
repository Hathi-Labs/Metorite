"""The synthetic notes of the note-labels eval (WS-53 CRM-Q26).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q26 item 11.

``notes.json`` holds the notes and their expected labels, and
``scripted_answers.json`` holds the answers that ``--scripted`` replays. An
agent invented each note. Every name and place is made up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
NOTES = HERE / "notes.json"
ANSWERS = HERE / "scripted_answers.json"
_FILLER = "This line is filler text of the long note case, and it holds no label. "


@dataclass(frozen=True)
class Expected:
    """The labels a careful reader gives a note."""

    kind: str
    action: bool
    urgency: str


@dataclass(frozen=True)
class EvalNote:
    """One synthetic note and its expected labels."""

    id: str
    tags: tuple[str, ...]
    subject: str
    body: str
    expected: Expected


def _body(row: dict[str, Any]) -> str:
    body = row["body"]
    pad = int(row.get("pad_chars") or 0)
    if pad:
        body += (_FILLER * (pad // len(_FILLER) + 1))[:pad]
    return body


def load_notes(path: Path = NOTES) -> list[EvalNote]:
    """The notes of the set, in file order."""
    rows = json.loads(path.read_text(encoding="utf-8"))["notes"]
    return [
        EvalNote(
            id=row["id"], tags=tuple(row.get("tags") or ()), subject=row["subject"],
            body=_body(row),
            expected=Expected(kind=row["expected"]["kind"],
                              action=bool(row["expected"]["action"]),
                              urgency=row["expected"]["urgency"]),
        )
        for row in rows
    ]


def load_answers(path: Path = ANSWERS) -> dict[str, dict[str, Any]]:
    """The scripted answers: ``{note id: {"kind", "action", "urgency"}}``."""
    return json.loads(path.read_text(encoding="utf-8"))["answers"]
