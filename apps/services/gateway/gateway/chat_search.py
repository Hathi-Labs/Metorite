"""The search of the member's own past chats (WS-FIND HRD-21).

Spec: ``project-docs/specs/find_engine.md`` §3.1 rung 6, §6 and D-FIND-5. The
``chat`` source of ``gateway/retrieval/sources.py`` calls
:func:`search_own_chats` after its gates: ``feature:chat`` and
``CHAT_SEARCH_ORGS``. No route calls it.

**Which chats (D-FIND-5).** Only the member's own solo chats with Mira on the
web, in the bound org, and never the current chat. One statement holds each
rule in its WHERE:

* the bound org (``organization_id``), beside RLS;
* the member's own chat: ``chat_session.user_id`` is the session's address,
  the rule of the chat's own list (``routes/chat.py``);
* solo: ``rooms.SOLO_ROOM_SQL``, the one rule of a solo chat. So a room, a
  shared chat and a chat with another person never answer;
* Mira on the web: the agent ``orchestrator``, no channel, and no row and no
  seat of another agent (:data:`ONLY_AGENT_SQL`, the rule of PZ-33). A run of
  another agent can be covered (H-236), so its chat can be a ``no_egress``
  chat. The orchestrator is never covered (``sandbox_broker.covers``);
* not the current chat: ``s.id <> :current``. With no current chat the search
  reads nothing, so it fails closed.

After the read, :func:`acb_skills.egress.marked_sessions` drops each chat that
holds the egress mark: the mark of an email-assistant run or another untrusted
read. Its own run holds the writes that a held chat holds off. Each run that it
delegates binds ``no_egress``. The SQL does not test the mark, and the mark is the only test. So a chat that read mail with no
mark can show (``find_engine.md`` §6, limit 1). The read of the marks is
read-only, and a fault raises, so no chat shows.

**What it reads, and what it prints.** Each content word of the query
(``find.chat_words``) must be in the title, or in ONE ``user`` or
``assistant`` row of the chat, as a WHOLE word: the stem rule of
``find.coverage`` (``find.whole_word_pattern``). So "AI" never matches inside
"email", and "printers" holds "printer". The end of a cut text is no end of a
word (:data:`CUT`). So a hit holds every content word, and the route counts it
so. A query with no content word, or more than ``find.CHAT_MAX_WORDS``, reads
nothing. It never reads a ``system`` row or the tool events. The snippet comes ONLY from a row that the member typed
(``member_typed``, the member as author, ``typed_text``, not a card answer),
so a reply of Mira that restates a mail, a WhatsApp message or a page never
prints. The statement returns the text of no other row.

**The cost.** The caps bound the word match: the newest :data:`SCAN_SESSIONS`
chats that pass every rule, the newest :data:`SCAN_ROWS` rows of each, and
:data:`MATCH_CHARS` characters of each row. The rules run before that cut, so
the walk can pass more chats, and :data:`ONLY_AGENT_SQL` reads every row of
each chat that it tests. Each word arm is a ``LIKE`` of the stem, then a regular
expression, and no index serves either. So a long chat history can run to the
limit. A LOCAL ``statement_timeout`` of :data:`STATEMENT_MS` bounds the whole
statement. HRD-21 measured no plan on a large chat history.

Fences: ``tests/unit/test_chat_source.py``, ``tests/unit/test_chat_source_r8.py``
(R8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_auth import UserContext
from acb_common import get_logger
from sqlalchemy import text

from gateway.db import tenant_session
from gateway.rooms import SOLO_ROOM_SQL

_log = get_logger("gateway.chat_search")

#: The one agent of a chat that the search reads: Mira (the rule of PZ-33).
AGENT = "orchestrator"
#: The newest chats of the member that one search reads.
SCAN_SESSIONS = 100
#: The newest rows of each chat that one search reads.
SCAN_ROWS = 80
#: The characters of each row that the words must be in.
MATCH_CHARS = 4000
#: The most characters of a title.
TITLE_CHARS = 200
#: The limit of the statement, in ms: a little more than the time that the
#: route gives one source (``company.SOURCE_TIMEOUT_S``, 1.5 s). The route
#: stops waiting at its budget and names the source "timed out". This limit
#: stops the statement too, so a scan never runs on with no one to read it.
STATEMENT_MS = 1600
#: The most chats that one read gives before the marks are read.
POOL_MAX = 20
#: The most characters of the id of the current chat.
CURRENT_MAX = 200
#: The characters before the first word in a snippet.
SNIPPET_LEAD = 60
#: The longest typed row that may give a snippet (``assistant_profile.TYPED_MAX``).
TYPED_MAX = 1500
#: The character that ends a CUT text (review P2-1). It is no word edge
#: (``find.WORD_EDGES``) and no form holds it, so the cut end of a text is no
#: end of a word: «print» of a cut «printers» matches nothing.
CUT = "\u00e9"

#: The bound tenant, named beside RLS, as each store module names it.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
#: ``SET LOCAL``: it ends with the read's transaction.
_LIMIT_SQL = "SELECT set_config('statement_timeout', :ms, true)"

#: True when no agent but :data:`AGENT` is in the chat ``s``: no seat in
#: ``chat_session_agent``, and no agent row of another agent. A NULL kind of
#: an ``assistant`` row reads as an agent, and a NULL author as Mira, as in
#: ``assistant_summaries.ELIGIBLE_SQL``. ``test_chat_source_r8.py`` holds the
#: two rules to the same answer.
ONLY_AGENT_SQL = """(
        NOT EXISTS (
            SELECT 1 FROM chat_session_agent ag
             WHERE ag.session_id = s.id AND ag.agent_name <> :agent)
        AND NOT EXISTS (
            SELECT 1 FROM chat_message g
             WHERE g.session_id = s.id
               AND COALESCE(g.author_kind,
                            CASE g.role WHEN 'assistant' THEN 'agent' END) = 'agent'
               AND COALESCE(NULLIF(g.author_email, ''), :agent) <> :agent)
    )"""

#: True when the row ``r`` is one that the member typed: a ``user`` row with
#: the ONE typed mark (D-PZ-37), a human author that is the member or no one,
#: and short enough to be typed. Only such a row gives a snippet.
MEMBER_ROW_SQL = """(
        r.role = 'user' AND r.member_typed IS TRUE
        AND COALESCE(NULLIF(r.author_kind, ''), 'human') = 'human'
        AND COALESCE(NULLIF(lower(btrim(r.author_email)), ''), :member) = :member
        AND char_length(r.body) <= :typed_max
    )"""


@dataclass(frozen=True)
class ChatHit:
    """One past chat of the member that holds the words."""

    session_id: str
    title: str
    #: The last change of the chat: ``chat_session.updated_at``.
    last_activity: datetime | None
    #: A passage of a row that the member typed, or ``""``.
    snippet: str


def _words_clause(probe: str, count: int) -> str:
    """Each word in one text, as a whole word (review P2-1). *probe* is the
    text in lower case, with :data:`CUT` after a cut. Each arm is a cheap
    ``LIKE`` of the stem (each form starts with it), then the pattern of
    ``find.whole_word_pattern``. Both are bound values, so no word of the
    query is ever part of the statement."""
    return "(" + " AND ".join(
        f"({probe} LIKE :s{i} AND {probe} ~ :rx{i})" for i in range(count)) + ")"


def _probe(column: str) -> str:
    """*column* as the words see it: its first :data:`MATCH_CHARS`
    characters, with :data:`CUT` after them when the text is longer, in
    lower case. So a word cut in half is no whole word."""
    return (f"lower(left({column}, :chars) || CASE WHEN char_length({column}) > :chars "
            f"THEN :cut ELSE '' END)")


def search_sql(count: int) -> str:
    """The one statement of the search, for *count* words."""
    return f"""
WITH mine AS (
    SELECT s.id, left(COALESCE(s.title, ''), :title_chars) AS title, s.updated_at,
           {_probe("COALESCE(s.title, '')")} AS title_probe
      FROM chat_session s
     WHERE s.user_id = :uid
       AND s.organization_id = {_BOUND_ORG}
       AND s.agent_name = :agent
       AND s.channel IS NULL
       AND s.id <> :current
       AND {ONLY_AGENT_SQL}
       AND {SOLO_ROOM_SQL}
     ORDER BY s.updated_at DESC, s.id DESC
     LIMIT :sessions
),
said AS (
    SELECT DISTINCT ON (mine.id) mine.id,
           CASE WHEN {MEMBER_ROW_SQL} THEN r.body END AS said
      FROM mine
      CROSS JOIN LATERAL (
            SELECT m.id, m.role, m.member_typed, m.author_kind, m.author_email,
                   m.timestamp_ms, left(m.content, :chars) AS body,
                   {_probe("m.content")} AS probe
              FROM chat_message m
             WHERE m.session_id = mine.id AND m.role IN ('user', 'assistant')
             ORDER BY m.timestamp_ms DESC, m.id DESC
             LIMIT :rows) AS r
     WHERE {_words_clause("r.probe", count)}
     ORDER BY mine.id, ({MEMBER_ROW_SQL}) DESC, r.timestamp_ms DESC, r.id DESC
)
SELECT mine.id, mine.title, mine.updated_at, said.said
  FROM mine
  LEFT JOIN said ON said.id = mine.id
 WHERE said.id IS NOT NULL OR {_words_clause("mine.title_probe", count)}
 ORDER BY mine.updated_at DESC, mine.id DESC
 LIMIT :pool
"""


def snippet_of(said: Any, words: list[str]) -> str:
    """A passage of the member's own typed row, from the first word, or ``""``.

    The row must pass ``assistant_profile.typed_text``: a paste of more than
    1,500 characters, or a text with a line of a pasted mail, gives nothing.
    A card answer gives nothing, because it quotes the question that the model
    wrote (``chat_recovery.is_card_answer_text``). Pure."""
    from acb_common.assistant_profile import typed_text

    from gateway.chat_recovery import is_card_answer_text

    typed = typed_text(said).strip()
    if not typed or is_card_answer_text(typed):
        return ""
    low = typed.lower()
    found = [p for p in (low.find(w.lower()) for w in words) if p >= 0]
    start = max(0, min(found, default=0) - SNIPPET_LEAD)
    return ("…" + typed[start:].lstrip()) if start else typed


async def search_own_chats(
    user: UserContext, q: str, k: int, *, current: str | None,
) -> list[ChatHit]:
    """At most *k* past chats of the member that hold every word of *q*.

    The member and the org come from *user*, the session's own object (R5).
    *current* is the id of the chat that the search runs in. It only takes a
    chat away. With no current chat, or one longer than :data:`CURRENT_MAX`,
    it reads nothing (fail closed).

    Each hit holds EVERY content word of the query as a whole word, by the
    stem rule of ``find.coverage``, and the route counts it so
    (``find.stamp_first``). ``find.chat_words`` is the ONE rule of the words:
    with no content word, or more than ``find.CHAT_MAX_WORDS``, it reads
    nothing. The route asks the source only when that rule gives a list.
    """
    from acb_skills.egress import marked_sessions

    from gateway.retrieval.find import chat_words, whole_word_pattern, word_stem

    org = user.organization_id
    member = (user.email or "").strip()
    here = (current or "").strip()
    if not org or not member or not here or len(here) > CURRENT_MAX or k < 1:
        return []
    words = chat_words(q)
    if words is None:
        return []
    params: dict[str, Any] = {
        "uid": user.email, "member": member.lower(), "agent": AGENT, "current": here,
        "title_chars": TITLE_CHARS, "chars": MATCH_CHARS, "rows": SCAN_ROWS,
        "sessions": SCAN_SESSIONS, "typed_max": TYPED_MAX, "cut": CUT,
        "pool": max(k, min(POOL_MAX, 3 * k)),
    }
    for i, word in enumerate(words):
        # A stem is letters and digits only, so the LIKE needs no escape.
        params[f"s{i}"] = f"%{word_stem(word)}%"
        params[f"rx{i}"] = whole_word_pattern(word)
    async with tenant_session(org) as db:
        await db.execute(text(_LIMIT_SQL), {"ms": str(STATEMENT_MS)})
        rows = (await db.execute(text(search_sql(len(words))), params)).fetchall()
    marked = await marked_sessions(str(r.id) for r in rows) if rows else frozenset()
    out = [
        ChatHit(session_id=str(r.id), title=str(r.title or ""), last_activity=r.updated_at,
                snippet=snippet_of(r.said, words) if r.said is not None else "")
        for r in rows if str(r.id) not in marked
    ][:k]
    _log.info("chat.search", read=len(rows), marked=len(marked), hits=len(out))
    return out
