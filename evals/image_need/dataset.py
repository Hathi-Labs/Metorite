"""The image-need test set (WS-VOX VOX-5, ``projects_ai_chat.md`` §31.5).

The member attached one image in the chat and typed some words. Each case
says if the one assistant must call ``analyze_image`` for those words.

* ``call``: the answer needs what the image shows. The assistant calls
  ``analyze_image`` exactly once, and ``file_name`` names the attached file.
* ``no_call``: a tool moves the file by its name. The assistant calls
  ``analyze_image`` zero times.

The words, names and addresses are made up. Every address ends in ``.example``.
"""
from __future__ import annotations

from dataclasses import dataclass

from evals.routing.dataset import MEMBER, ORG

CALL, NO_CALL = "call", "no_call"

__all__ = ["CALL", "CASES", "MEMBER", "NO_CALL", "ORG", "Case"]


@dataclass(frozen=True)
class Case:
    id: str
    #: The file name in the thread folder, as the upload note shows it.
    file_name: str
    #: The member's typed words.
    words: str
    #: ``call`` or ``no_call``.
    expected: str


CASES: tuple[Case, ...] = (
    # The image moves by its name. No model needs to look at it.
    Case("N1", "screenshot.png", "Attach this to task Q3 launch checklist", NO_CALL),
    Case("N2", "screenshot.png", "Make a task with this screenshot", NO_CALL),
    Case("N3", "diagram.png", "Email this to priya@acme.example", NO_CALL),
    Case("N4", "diagram.png", "Put it in the Files", NO_CALL),
    Case("N5", "site-photo.jpg", "Add this photo to the Warehouse move project", NO_CALL),
    Case("N6", "logo.png", "Forward this to the design team", NO_CALL),
    # The answer needs what the image shows.
    Case("C1", "screenshot.png", "What does this screenshot say?", CALL),
    Case("C2", "invoice.png", "Read the invoice number", CALL),
    Case("C3", "error.png", "What is wrong in this error?", CALL),
    Case("C4", "bug.png", "Make a task that describes this bug", CALL),
    Case("C5", "whiteboard.jpg", "Write down the action items on this whiteboard", CALL),
    Case("C6", "chart.png", "Which month has the highest value in this chart?", CALL),
)
