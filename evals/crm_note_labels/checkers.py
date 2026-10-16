"""The scoring of the note-labels eval (WS-53 CRM-Q26).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q26 item 11.

:func:`score` compares each stored row with the expected labels of its note.
Five bars:

* ``kind``: the stored kind equals the expected kind on :data:`KIND_BAR` of
  the notes or more.
* ``action``: the stored probability of "needs action", read at
  :data:`ACTION_AT`, equals the expected boolean on :data:`ACTION_BAR` or
  more.
* ``urgency``: the stored urgency equals the expected level on
  :data:`URGENCY_BAR` or more.
* ``shape``: each request holds 5 notes or fewer and 16 questions or fewer,
  each body in the state is 2,000 characters or fewer, and each question
  names a note of its own state.
* ``no_text``: each label column holds one of our keys, a number or NULL,
  so no text of a note or of the vendor lands in a row.

A scripted run replays answers that an agent wrote, so each label must be
exact there: with ``scripted=True`` the three label bars need a rate of 1.0.
The model sweep uses the bars above.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.crm_note_labels.dataset import EvalNote

KIND_BAR = 0.80
ACTION_BAR = 0.85
URGENCY_BAR = 0.70
#: The probability at which "needs action" reads as true.
ACTION_AT = 0.5

#: ``{note id: row}``. A row is what ``note_labels.reader`` makes.
Produced = dict[str, dict[str, Any]]


@dataclass
class Score:
    """The counts of one run, and the result of each bar."""

    scripted: bool = False
    notes: int = 0
    kind_ok: int = 0
    action_ok: int = 0
    urgency_ok: int = 0
    misses: list[str] = field(default_factory=list)
    shape_problems: list[str] = field(default_factory=list)
    text_leaks: list[str] = field(default_factory=list)

    def _rate(self, ok: int) -> float:
        return ok / self.notes if self.notes else 1.0

    def bars(self) -> dict[str, bool]:
        need = (1.0, 1.0, 1.0) if self.scripted else (KIND_BAR, ACTION_BAR, URGENCY_BAR)
        return {
            "kind": self._rate(self.kind_ok) >= need[0],
            "action": self._rate(self.action_ok) >= need[1],
            "urgency": self._rate(self.urgency_ok) >= need[2],
            "shape": not self.shape_problems,
            "no_text": not self.text_leaks,
        }

    def passed(self) -> bool:
        return all(self.bars().values())

    def summary(self) -> dict[str, Any]:
        return {
            "notes": self.notes,
            "kind_rate": round(self._rate(self.kind_ok), 3),
            "action_rate": round(self._rate(self.action_ok), 3),
            "urgency_rate": round(self._rate(self.urgency_ok), 3),
            "bars": self.bars(), "passed": self.passed(), "misses": self.misses,
            "shape_problems": self.shape_problems, "text_leaks": self.text_leaks,
        }


def _leaks(note: EvalNote, row: dict[str, Any]) -> list[str]:
    """Each label column that holds anything but one of our keys or a number.
    A row holds keys and numbers only, so any other string is text that came
    from the note or from the vendor."""
    from gateway.crm_sync.note_labels import KIND_OPTIONS, URGENCY_LEVELS

    out = []
    if row.get("kind") not in (None, *KIND_OPTIONS):
        out.append(f"{note.id}/kind")
    if row.get("urgency") not in (None, *URGENCY_LEVELS):
        out.append(f"{note.id}/urgency")
    for column, keys in (("kind_probabilities", tuple(KIND_OPTIONS)),
                         ("urgency_probabilities", URGENCY_LEVELS)):
        value = row.get(column)
        if value is None:
            continue
        if (not isinstance(value, dict) or not set(value) <= set(keys)
                or not all(isinstance(v, int | float) for v in value.values())):
            out.append(f"{note.id}/{column}")
    return out


def score(notes: list[EvalNote], produced: Produced, *, scripted: bool = False,
          shape_problems: list[str] | None = None) -> Score:
    """Score each note of the set against the rows of one run."""
    result = Score(scripted=scripted, shape_problems=list(shape_problems or ()))
    for note in notes:
        result.notes += 1
        row = produced.get(note.id)
        if row is None:
            result.misses.append(f"{note.id}/no_row")
            continue
        exp = note.expected
        if row["kind"] == exp.kind:
            result.kind_ok += 1
        else:
            result.misses.append(f"{note.id}/kind:{row['kind']}!={exp.kind}")
        probability = row["needs_action"]
        if probability is not None and (probability >= ACTION_AT) == exp.action:
            result.action_ok += 1
        else:
            result.misses.append(f"{note.id}/action:{probability}!={exp.action}")
        if row["urgency"] == exp.urgency:
            result.urgency_ok += 1
        else:
            result.misses.append(f"{note.id}/urgency:{row['urgency']}!={exp.urgency}")
        result.text_leaks.extend(_leaks(note, row))
    return result
