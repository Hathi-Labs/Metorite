"""The two paths of each question, as known tool sequences (WS-48 N4).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2. The session is
the email eval's own (``evals/email_narrowing/scripted.py``): each turn is ONE
model request, its prompt is the whole conversation so far, and the stub
Router counts each request on its tier.

* **Before** (today): ``search_whatsapp`` once for each search word, in ONE
  request, because the route ANDs the words of one search. Then
  ``read_whatsapp_chat`` for each chat that the searches list,
  :data:`READS_PER_TURN` to a request, then the answer. For a question about
  one group, ``list_whatsapp_chats`` finds the group, and one
  ``read_whatsapp_chat`` reads it.
* **After**: ONE ``narrow_and_read`` call, then the answer.

⚠️ **The before path is an assumption.** Nobody measured what today's model
asks for. :data:`READS_PER_TURN` is 5. The runner also plays every read in
ONE request, the best case for today, and reports that ratio beside the gated
one. Only a live sweep measures what a model does.

⚠️ **Today's tools are weak in two ways that the before path keeps.**
``search_whatsapp`` shows at most 15 matches of each search, and it shows no
date. ``read_whatsapp_chat`` reads the OLDEST messages of a chat, because its
route orders ``sent_at ASC`` and then applies the limit. The eval reports the
recall of the before path, so a reader sees what the cheaper path misses.
"""
from __future__ import annotations

import json
import re

from evals.email_narrowing.scripted import Session, tier_after  # noqa: F401 — one session for both evals
from evals.whatsapp_narrowing.dataset import Dataset, Question

#: The chat reads that today's model asks for in one request (an assumption).
READS_PER_TURN = 5
#: The ``limit`` of ``read_whatsapp_chat`` for a whole group (its default is 20).
GROUP_READ_LIMIT = 100

_CHAT = re.compile(r"chat_id=([0-9a-f-]{36})")
_MESSAGE = re.compile(r"message_id=([0-9a-f-]{36})")
_ITEM = re.compile(r"^--- item ([0-9a-f-]{36}):([0-9a-f-]{36}) \|", re.MULTILINE)


def _answer(ds: Dataset, q: Question, ids: list[str], checked: str) -> str:
    """A scripted answer: the senders and words of the answering messages."""
    lines = []
    for message_id in ids:
        m = ds.by_id(message_id)
        if m and q.id in m.get("answers", []):
            text = m["body_text"] or m["transcript_text"] or ""
            lines.append(f"- {m['sender_name']}: {text}")
    return "\n".join([f"{checked} {len(lines)} of them answer your question.", *lines])


def _unique(found: list[str]) -> list[str]:
    out: list[str] = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


async def play_before(
    session: Session, ds: Dataset, q: Question, *, reads_per_turn: int = READS_PER_TURN,
) -> list[str]:
    """Today's path. Returns the message ids that the search lines showed."""
    shown: list[str] = []
    if q.spec.read_chat:
        name = ds.chat_by_slug(q.spec.read_chat)["name"]
        [listing] = await session.turn([("list_whatsapp_chats", {"stream": "groups"})])
        line = next(ln for ln in listing.splitlines() if name in ln)
        chats = _CHAT.findall(line)[:1]
        await session.turn([("read_whatsapp_chat",
                             {"chat_id": chats[0], "limit": GROUP_READ_LIMIT})])
    else:
        results = await session.turn([("search_whatsapp", {"query": w}) for w in q.spec.searches])
        chats = _unique([c for r in results for c in _CHAT.findall(r)])
        shown = _unique([m for r in results for m in _MESSAGE.findall(r)])
        per_turn = max(1, reads_per_turn if reads_per_turn > 0 else len(chats) or 1)
        for start in range(0, len(chats), per_turn):
            batch = chats[start:start + per_turn]
            await session.turn([("read_whatsapp_chat", {"chat_id": c}) for c in batch])
    session.answer(_answer(ds, q, shown, f"I read {len(chats)} chats."))
    return shown


async def play_after(session: Session, ds: Dataset, q: Question) -> tuple[list[str], str]:
    """The narrowing path. Returns the message ids that it read in full, and
    the tool output."""
    filters = json.dumps(q.narrow_filters())
    [out] = await session.turn([("narrow_and_read", {"query": q.spec.prompt, "filters": filters})])
    ids = [message for _chat, message in _ITEM.findall(out)]
    first = out.splitlines()[0] if out else ""
    session.answer(_answer(ds, q, ids, first))
    return ids, out
