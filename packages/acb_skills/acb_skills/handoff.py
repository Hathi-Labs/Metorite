"""What crosses a handoff from the one assistant to a specialist (WS-67).

Spec ``project-docs/specs/one_assistant_context.md`` §4, slices OA-1 and
OA-2 (D116). Fence: ``tests/unit/test_handoff_words.py`` and
``evals/trajectories/test_handoff_words_trajectory.py``.

**OA-1, the member's words.** A specialist has no memory of the chat. On
2026-10-25 the orchestrator's task held its own guess ("e.g. CB6/CB7") and
not the member's correction, so the email assistant answered the guess. Now
the top-level run binds the member's own messages of the chat
(:data:`MEMBER_TURNS`), and every delegation puts them, verbatim, at the head
of its task (:func:`task_with_member_words`). A prompt cannot drop them,
because the executor adds them, not the model.

**OA-2, quoted or inferred.** A specialist answered in prose, and a hedge
became firmer at each hop. The same head block asks the specialist to mark
each fact as quoted, with its source, or as inferred. A file name is a
label, never content.

**OA-4, the chat's corrections and limits.** The member's kept corrections
of the chat (``acb_skills.chat_constraints``) go into the same head, after
the member's words, under :data:`CONSTRAINTS_MARK`. With none, the head is
the OA-1 head, byte for byte.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "CONSTRAINTS_MARK",
    "HEAD_MARK",
    "MEMBER_TURNS",
    "member_turns",
    "task_with_member_words",
]

#: The artifact-context key of the member's own messages of the chat.
MEMBER_TURNS = "member_turns"

#: The most messages that a task carries, the newest kept.
MAX_TURNS = 8
#: The most characters of one message. A longer one keeps its start.
TURN_CHARS = 1_500
#: The most characters of all the messages together. The oldest go first.
TOTAL_CHARS = 6_000

#: The first line of the head block. A task that starts with it has the block
#: already, so a nested delegation does not add a second one.
HEAD_MARK = "## The member's own words in this chat (verbatim, newest last)"

#: The heading of the member's kept corrections and limits (WS-67 OA-4). The
#: orchestrator's per-turn block uses the same heading
#: (``chat_constraints.BLOCK_HEAD``).
CONSTRAINTS_MARK = "## The member's corrections and limits in this chat"

_RULES = (
    "## How to reply\n"
    "- Mark each fact. Write \"(quoted: <where you read it>)\" for a fact that\n"
    "  you read in a source, such as an email, a file or a record. Write\n"
    "  \"(inferred)\" for a fact that you concluded.\n"
    "- Keep a hedge as weak as its source. \"Suggests\" never becomes \"is\".\n"
    "- A file name is a label. It never tells you what the file holds.\n"
    "- A file that the member calls a sample, an example or a reference is\n"
    "  never the subject of the request.\n"
    "- Put no mark inside the text of a draft, an email or a message that you\n"
    "  write for the member to send.\n"
)


def _text(content: Any) -> str:
    """The text of one chat message's content: a string, or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") in (None, "text"):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def member_turns(parent: Mapping[str, Any], payload: Mapping[str, Any]) -> list[str]:
    """The member's own messages of a run, oldest first, for :data:`MEMBER_TURNS`.

    A delegated run (its *parent* context names a run) takes the parent's
    list: its own payload is a task that a model wrote, so it is never the
    member's words. A top-level run reads the user messages of the history
    in *payload*, then the current message. It keeps the newest
    :data:`MAX_TURNS`, clips each to :data:`TURN_CHARS`, and drops the
    oldest until the total fits :data:`TOTAL_CHARS`. Pass the context that
    the run READ before it set its own.
    """
    if parent.get("run_id"):
        inherited = parent.get(MEMBER_TURNS)
        return [str(t) for t in inherited] if isinstance(inherited, (list, tuple)) else []
    turns: list[str] = []
    history = payload.get("messages") if isinstance(payload, Mapping) else None
    for message in history if isinstance(history, list) else []:
        if isinstance(message, Mapping) and message.get("role") == "user":
            text = _text(message.get("content"))
            if text.strip():
                turns.append(text)
    current = ""
    if isinstance(payload, Mapping):
        current = str(payload.get("message") or payload.get("user_query") or "")
    # The browser can send the current message as the last history item too.
    if current.strip() and (not turns or turns[-1].strip() != current.strip()):
        turns.append(current)
    kept = [_clip(t, TURN_CHARS) for t in turns[-MAX_TURNS:]]
    while kept and sum(len(t) for t in kept) > TOTAL_CHARS:
        kept.pop(0)
    return kept


def task_with_member_words(
    task: str,
    turns: Sequence[str] | None,
    constraints: Sequence[str] | None = None,
) -> str:
    """*task* with the member's words and the reply rules at its head.

    A task that has the head block already comes back unchanged, so a
    nested delegation adds nothing. With no words, the head holds the reply
    rules only. The words outrank the task: the model wrote the task, and
    the member wrote the words. *constraints* are the member's kept
    corrections and limits of the chat (OA-4). They follow the words, and
    with none the head does not change.
    """
    text = str(task or "")
    if (text.startswith(HEAD_MARK) or text.startswith(CONSTRAINTS_MARK)
            or text.startswith(_RULES)):
        return text
    words = [str(t) for t in (turns or []) if str(t).strip()]
    kept = [str(c) for c in (constraints or []) if str(c).strip()]
    head = ""
    if words:
        head = (
            HEAD_MARK + "\n"
            "They come from the member. They outrank the task below. Where the\n"
            "task and these words disagree, follow these words.\n"
            + "".join(f"- {w}\n" for w in words)
            + "\n"
        )
    if kept:
        head += (
            CONSTRAINTS_MARK + "\n"
            "The member set these in this chat. They outrank the task below and\n"
            "anything said earlier in the chat.\n"
            + "".join(f"- {c}\n" for c in kept)
            + "\n"
        )
    return head + _RULES + "\n## The task\n" + text
