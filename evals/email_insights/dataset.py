"""The synthetic mails of the Insights eval (WS-17 EM-T14b-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §13.9.2 item 3.

``mails.json`` holds the mails, and ``scripted_answers.json`` holds the
answers that ``--scripted`` replays. Each mail is invented. Every name and
address is made up, and every address ends in ``.example``.

The sources of a mail come from the module of the job,
:mod:`gateway.routes.email.automation.insights_extract`, so the eval reads the
same text that the job frames for the model. A spreadsheet gives no source
(D-EM-40).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from gateway.routes.email.automation import insights_extract as extract

HERE = Path(__file__).resolve().parent
MAILS = HERE / "mails.json"
ANSWERS = HERE / "scripted_answers.json"
#: The source key of the body. Each file uses its own name.
BODY = "body"
_FILLER = "This line is filler text of the long mail case, and it holds no fact. "


@dataclass(frozen=True)
class Expected:
    """One fact that the fin-1 rules must store for a mail."""

    source: str
    fact_type: str
    ref: str | None
    amount: Decimal | None
    currency: str | None
    due_on: date | None


@dataclass(frozen=True)
class Mail:
    """One synthetic mail, its expected screen answer and its expected facts."""

    id: str
    tags: tuple[str, ...]
    subject: str
    sender: str
    date: str
    labels: tuple[str, ...]
    body: str
    files: tuple[tuple[str, str], ...]
    screen: dict[str, bool]
    expected: tuple[Expected, ...]

    def sources(self) -> dict[str, extract.Source]:
        """Each source that the job reads: the body, and each file of a kind
        that the job sends to a model. A ``.xlsx`` or ``.csv`` file gives no
        source (D-EM-40)."""
        out = {BODY: extract.body_source(self.subject, self.sender, self.date, self.body)}
        for name, text in self.files:
            if Path(name).suffix.lower() in extract.EXTRACT_SUFFIXES:
                out[name] = extract.file_source(name, text)
        return out


def _expected(row: dict[str, Any]) -> Expected:
    return Expected(
        source=row["source"], fact_type=row["fact_type"], ref=row.get("ref"),
        amount=Decimal(row["amount"]) if row.get("amount") is not None else None,
        currency=row.get("currency"),
        due_on=date.fromisoformat(row["due_on"]) if row.get("due_on") else None,
    )


def _body(row: dict[str, Any]) -> str:
    body = row["body"]
    pad = int(row.get("pad_chars") or 0)
    if pad:
        body += (_FILLER * (pad // len(_FILLER) + 1))[:pad]
    return body


def load_mails(path: Path = MAILS) -> list[Mail]:
    """The mails of the set, in file order."""
    rows = json.loads(path.read_text(encoding="utf-8"))["mails"]
    return [
        Mail(
            id=row["id"], tags=tuple(row.get("tags") or ()), subject=row["subject"],
            sender=row["sender"], date=row["date"], labels=tuple(row.get("labels") or ()),
            body=_body(row),
            files=tuple((f["name"], f["text"]) for f in row.get("files") or ()),
            screen=dict(row["screen"]),
            expected=tuple(_expected(e) for e in row.get("expected") or ()),
        )
        for row in rows
    ]


def load_answers(path: Path = ANSWERS) -> dict[str, dict[str, Any]]:
    """The scripted answers: ``{mail id: {source key: answer}}``."""
    return json.loads(path.read_text(encoding="utf-8"))["answers"]
