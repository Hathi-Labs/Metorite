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
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "HEAD_MARK",
    "MEMBER_TURNS",
    "TASK_MARK",
    "head_task",
    "member_turns",
    "task_of",
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
    """*text* with its line breaks, cut to *limit* characters."""
    lines = [line.rstrip() for line in str(text).strip().splitlines()]
    text = "\n".join(line for line in lines if line)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _typed(text: str) -> str:
    """*text* when it is the member's typed words, else ``""``.

    The one filter is ``acb_common.assistant_profile.typed_text``: a long
    paste, or a text with a line of a pasted mail, is not the member's words.
    A stranger's mail that the member pasted must never "outrank the task".
    """
    try:
        from acb_common.assistant_profile import typed_text
    except ImportError:  # pragma: no cover - acb_common ships with acb_skills
        return text
    return typed_text(text)


def _typed_now() -> str:
    """The member's typed words of THIS turn (``assistant_profile.MEMBER_TYPED``).

    The route binds them before any server rewrite of ``message``. So a
    Continue turn, whose message quotes the assistant's own reply, reads
    ``""``, and so do the supersede note and a replayed steer.
    """
    try:
        from acb_common.assistant_profile import typed_words
    except ImportError:  # pragma: no cover
        return ""
    return typed_words()


def member_turns(
    parent: Mapping[str, Any], payload: Mapping[str, Any], *, typed: str | None = None,
) -> list[str]:
    """The member's own messages of a run, oldest first, for :data:`MEMBER_TURNS`.

    A delegated run (its *parent* context names a run) takes the parent's
    list: its own payload is a task that a model wrote, so it is never the
    member's words. A top-level run reads the user messages of the history
    in *payload*, then *typed*, the member's typed words of this turn
    (default: :func:`_typed_now`). It never reads ``payload["message"]``,
    because the server rewrites it on a Continue turn. It keeps the newest
    :data:`MAX_TURNS`, clips each to :data:`TURN_CHARS`, and drops the oldest
    until the total fits :data:`TOTAL_CHARS`. Pass the context that the run
    READ before it set its own.
    """
    if parent.get("run_id"):
        inherited = parent.get(MEMBER_TURNS)
        return [str(t) for t in inherited] if isinstance(inherited, (list, tuple)) else []
    turns: list[str] = []
    history = payload.get("messages") if isinstance(payload, Mapping) else None
    for message in history if isinstance(history, list) else []:
        if isinstance(message, Mapping) and message.get("role") == "user":
            text = _typed(_text(message.get("content")))
            if text.strip():
                turns.append(text)
    current = _typed_now() if typed is None else _typed(typed)
    # The browser can send the current message as the last history item too.
    if current.strip() and (not turns or turns[-1].strip() != current.strip()):
        turns.append(current)
    kept = [_clip(t, TURN_CHARS) for t in turns[-MAX_TURNS:]]
    while kept and sum(len(t) for t in kept) > TOTAL_CHARS:
        kept.pop(0)
    return kept


def task_with_member_words(task: str, turns: Sequence[str] | None) -> str:
    """*task* with the member's words and the reply rules at its head.

    A task that has the head block already comes back unchanged, so a
    nested delegation adds nothing. With no words, the head holds the reply
    rules only. The words outrank the task: the model wrote the task, and
    the member wrote the words.
    """
    text = str(task or "")
    if text.startswith(HEAD_MARK) or text.startswith(_RULES):
        return text
    words = [str(t) for t in (turns or []) if str(t).strip()]
    head = ""
    if words:
        head = (
            HEAD_MARK + "\n"
            "They come from the member. They outrank the task below. Where the\n"
            "task and these words disagree, follow these words.\n"
            + "".join("- " + w.replace("\n", "\n  ") + "\n" for w in words)
            + "\n"
        )
    return head + _RULES + TASK_MARK + text


#: The line that starts the task under the head block.
TASK_MARK = "\n## The task\n"


def head_task(task: str) -> str:
    """*task* headed with the member's words of the run on this frame.

    THE call of every delegation site: the streaming seam
    (``executor._run_sub_agent_streaming``) and the batch ones
    (``agent_tools.call_agent``, ``call_agents_parallel`` and
    ``call_agent_background``, ``orchestrator.agents.delegate_to_agent``).
    Call it on the PARENT's frame, before the child binds its own context.
    It is idempotent, so a task that one site headed passes the next.
    """
    try:
        from acb_skills.write_artifact import artifact_context

        turns = artifact_context().get(MEMBER_TURNS)
    except Exception:  # a broken binding costs the words, never the task
        turns = None
    return task_with_member_words(task, turns)


def task_of(message: str) -> str:
    """The task under the head block of *message*, or *message* when it has
    no head. The tier question reads this, so the head does not make every
    task look long (``tier_policy.SHORT_MESSAGE_WORDS``)."""
    text = str(message or "")
    if not (text.startswith(HEAD_MARK) or text.startswith(_RULES)):
        return text
    _, mark, task = text.partition(TASK_MARK)
    return task if mark else text
