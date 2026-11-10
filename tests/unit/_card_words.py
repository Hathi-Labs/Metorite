"""The words of a confirmation card, checked on every card a test draws.

Owner report, 2026-10-07: a member read ``tags_add → Bug`` on a card, and
``unarchive_task`` in its undo line. A card is the member's, and the model
never reads it (``ask_tools.request_confirmation`` sends it to the client
only). So two rules hold for every card:

1. **Every key has a label.** The card's body is ``key: value`` lines, and the
   client turns each key into product words through ONE map,
   ``CARD_FIELDS`` in ``workbench/control_plane/src/lib/cardFields.ts``. This
   module READS that map out of the TypeScript, as
   ``test_seed_status_colours_match_the_shared_vocabulary`` reads its hues,
   because a mirror goes stale and then lies.
2. **No wire name in the prose.** Outside a «fenced» member value, the title,
   the detail and every value hold no ``snake_case`` word: not a field key,
   not a tool name.

The shared fakes (``_projects_agent_fakes.py``, ``_crm_agent_fakes.py``) call
:func:`assert_card_words` on every card, so every card test is the fence. A
card that prints a new key fails its own tests until the key has a line in
``CARD_FIELDS``. Fence of this module: ``test_card_words.py``.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CARD_FIELDS_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench"
    / "control_plane"
    / "src"
    / "lib"
    / "cardFields.ts"
)

#: The client's field grammar (``ConfirmationCard.tsx`` ``FIELD``).
FIELD = re.compile(r"^([^:«»]{1,48}):\s(.*)$")
FENCED = re.compile(r"«[^«»\n]*»")
SNAKE = re.compile(r"(?<![\w@./:-])[a-z]+(?:_[a-z]+)+(?![\w@/-])")


@lru_cache(maxsize=1)
def card_keys() -> frozenset[str]:
    """The keys of ``CARD_FIELDS``, read out of the TypeScript."""
    source = CARD_FIELDS_TS.read_text(encoding="utf-8")
    start = source.index("export const CARD_FIELDS")
    end = source.index("\n};", start)
    keys = re.findall(r'^\s*"([^"]+)":\s*\{', source[start:end], flags=re.M)
    assert keys, f"no keys parsed out of {CARD_FIELDS_TS}"
    return frozenset(keys)


def card_key(raw: str) -> str:
    """The map key of a card key, as ``cardFields.ts`` ``cardKey`` reads it."""
    key = raw.strip()
    if key in card_keys():
        return key
    if re.match(r"^field\s+\S", key):
        return "field *"
    return re.sub(r"\d+", "N", key)


def raw_words(text: str) -> list[str]:
    """The snake_case words of *text* outside its fenced values."""
    return SNAKE.findall(FENCED.sub("", text or ""))


def card_problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with one card's words, or nothing."""
    problems: list[str] = []
    for part in ("title", "detail"):
        for word in raw_words(str(card.get(part) or "")):
            problems.append(f"{part} names a wire word {word!r}: {card.get(part)!r}")
    for line in str(card.get("context") or "").split("\n"):
        m = FIELD.match(line.strip())
        if not m:
            continue
        key = card_key(m.group(1))
        if key not in card_keys():
            problems.append(
                f"card key {m.group(1).strip()!r} has no label: add it to CARD_FIELDS "
                f"in {CARD_FIELDS_TS.name}"
            )
        for word in raw_words(m.group(2)):
            problems.append(f"the value of {m.group(1).strip()!r} names a wire word {word!r}")
    return problems


def assert_card_words(card: dict[str, Any]) -> None:
    """Fail the test that drew *card* when its words break a rule above."""
    problems = card_problems(card)
    assert not problems, "the confirmation card a member reads:\n  " + "\n  ".join(problems)
