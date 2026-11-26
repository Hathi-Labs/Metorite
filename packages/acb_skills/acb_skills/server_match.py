"""The words of a connected MCP server, and the servers that a turn names.

CSY-4 (task 3403), spec ``project-docs/specs/chat_connected_systems.md`` §3.4.

A server's words come from its label and its name, and from nothing else. The
admin's own match words wait for D-CSY-4, so the CSY-7 ``words`` field is not
read here. The grammar:

- The text splits on each character that is not a letter or a digit, in any
  case. So a hyphen, a space and an apostrophe each end a word.
- A trailing ``s`` leaves a word (a plural, and the ``'s`` of a possessive,
  whose ``s`` the split already cut off). A word that ends in ``ss`` keeps it.
- A word needs :data:`MIN_LETTERS` letters, and no word of
  :data:`STOP_WORDS` counts.
- A turn names a server when one whole word of the turn, read by the same
  grammar, is one of the server's words. A part of a word never matches.

The module is pure: no I/O, no flag and no tenant. ``tool_packs.choose`` and
``orchestrator._tool_injection`` import it. It lives apart from
``server_brief`` on purpose, so the brief and the match change on their own.

Fence: ``tests/unit/test_pack_choose_server_words.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: The generic words that never name a server. A pinned list.
STOP_WORDS: frozenset[str] = frozenset(
    {"app", "mcp", "server", "api", "the", "data", "tool", "tools"})
#: The fewest letters of a server word. A pinned number.
MIN_LETTERS = 4

_WORD = re.compile(r"[^\W_]+")


def _stem(word: str) -> str:
    """*word* less a trailing ``s``, unless it ends in ``ss``."""
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def words_of(text: object) -> frozenset[str]:
    """The match words of *text* by the grammar of this module."""
    out: set[str] = set()
    for raw in _WORD.findall(str(text or "").lower()):
        word = _stem(raw)
        if raw in STOP_WORDS or word in STOP_WORDS:
            continue
        if sum(ch.isalpha() for ch in word) >= MIN_LETTERS:
            out.add(word)
    return frozenset(out)


def server_words(label: object, name: object) -> frozenset[str]:
    """The words of one server: the words of its *label* and its *name*."""
    return words_of(label) | words_of(name)


def named_servers(words: object, servers: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """The names of *servers* that the member's *words* name, in name order.

    *servers* maps a server name to its words (:func:`server_words`)."""
    said = words_of(words)
    if not said:
        return ()
    return tuple(sorted(name for name, own in servers.items() if said & frozenset(own)))
