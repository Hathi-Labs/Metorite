"""Session-history recall for agents — parameterised, never SQL.

MT-0c-1 (``saas_multitenancy.md`` §0.9.3): **no agent ever gets a raw-SQL tool.**
That is not a style preference; it is a stated condition on the pooled tenancy
decision. An agent that can compose SQL can read any table the connection can
reach, and agents here execute model-generated tool calls over content ingested
from email and WhatsApp.

What this module used to be
---------------------------
``query_history(query: str)`` took a **model-generated SQL string** and executed
it via ``acb_graph.get_session()``. Its guard was a keyword-substring check, and
it was wrong in both directions — measured 2026-08-08:

* **False positive.** ``SELECT role, content, created_at FROM chat_message`` —
  *the tool's own documented example* — was **rejected**, because ``CREATED_AT``
  contains the substring ``CREATE``. Any query selecting a ``created_at`` column
  failed, which is most of them.
* **False negative, and this is the one that matters.**
  ``SELECT * FROM provider_keys`` passed the guard cleanly. So did every other
  table in the database. The allowlist constrained *verbs*; nothing constrained
  *tables*.

Under one organization that is a within-org visibility hole. Under the pooled
tenant boundary (D15) it is a cross-tenant read primitive — and it reaches the
database through ``acb_graph``, which is connection path 4 in
``saas_multitenancy.md`` §0.1: the **sync** ``create_engine`` the seam ratchet
never inspected.

What it is now
--------------
A parameterised search over exactly the two tables it always documented. The
model supplies *values*, never syntax; the SQL is a fixed string in this module
with bound parameters. There is no query string to sanitise because there is no
query string.

Scope narrowed at the same time: results are limited to the acting member's own
sessions when the run context names one. The previous tool could read any
member's conversations — its own docstring example did exactly that — which the
visibility ladder (``tenancy_and_visibility.md`` §3.3, chat is
private/people/org) never permitted.
"""
from __future__ import annotations

import json as _json

#: Hard ceilings. The model may ask for less, never more.
_MAX_LIMIT = 20
_CONTENT_CAP = 500

#: The only two tables this tool returns rows from, ever. Not configurable — a
#: table name that arrives as data is the hole this module was rewritten to
#: close.
#:
#: H-201: neither table has a ``thread_id`` column. The thread IS
#: ``chat_session.id``, and ``chat_message.session_id`` points at it. The old
#: join named a column that does not exist, so every call failed. The output
#: key stays ``thread_id``, because the docstring promises it. Each optional
#: criterion carries a CAST, because psycopg 3 sends a NULL with no type and
#: Postgres cannot type a bare ``:x IS NULL``. The R8 suite found both faults.
#:
#: H-201 fix round 1 (D12, chat is private by default). The search covers
#: only the rooms the acting member may read. The rule is
#: ``gateway.rooms.SESSION_VISIBLE_SQL``, the one visibility predicate that
#: the session list uses, so there is no second copy of it here. The tool
#: joins three fixed constants of code. A model input is never part of the
#: statement. It only binds values. The last predicate mirrors the
#: ``since_join`` waterline of ``resolve_room_access``. A member who joined
#: such a room late sees no message older than the join.
_SEARCH_HEAD = """
SELECT m.role,
       m.content,
       m.created_at,
       s.id AS thread_id,
       s.agent_name,
       s.title
  FROM chat_message m
  JOIN chat_session s ON s.id = m.session_id
 WHERE (CAST(:thread_id  AS text) IS NULL OR s.id         = :thread_id)
   AND (CAST(:agent_name AS text) IS NULL OR s.agent_name = :agent_name)
   AND (CAST(:search     AS text) IS NULL OR m.content ILIKE :search_like)
   AND (CAST(:since_days AS int)  IS NULL
        OR m.created_at >= now() - make_interval(days => CAST(:since_days AS int)))
   AND
"""
_SEARCH_TAIL = """
   AND NOT (
       COALESCE(s.history_visibility, 'full') = 'since_join'
       AND EXISTS (
           SELECT 1 FROM chat_session_participant pj
            WHERE pj.session_id = s.id AND pj.subject = :uid
              AND pj.join_message_ts IS NOT NULL
              AND m.timestamp_ms < pj.join_message_ts
       )
   )
 ORDER BY m.created_at DESC
 LIMIT :limit
"""


def _acting_user() -> str | None:
    """The member this run acts for, if the run context names one.

    Returns ``None`` for an unattended run (a webhook, a cron or an email
    run has no member). H-201 fix round 1: the tool then answers ``[]``.
    With no member there is no reader, and under D12 no chat is visible to
    nobody. Before, such a run searched every member's private chats.

    The member must also be VERIFIED (``member_verified``, H-73). That is,
    the name came from the signed-in session and not from a request body or
    an event payload, which an email or a webhook can shape.
    """
    try:
        from acb_common import get_run_context

        ctx = get_run_context() or {}
        if ctx.get("member_verified") != "1":
            return None
        return ctx.get("user") or None
    except Exception:
        return None


async def query_history(
    search: str | None = None,
    thread_id: str | None = None,
    agent_name: str | None = None,
    since_days: int | None = None,
    limit: int = 10,
) -> str:
    """Recall past conversations — what was discussed, decided, or left pending.

    Call this when the user references an earlier conversation, when you need a
    decision that was made before, or when you are resuming work on a thread.

    Args:
        search: Text to look for in message content (case-insensitive,
                substring). Omit to browse rather than search.
        thread_id: Restrict to one conversation thread.
        agent_name: Restrict to conversations with one agent, e.g.
                ``"orchestrator"``.
        since_days: Only messages from the last N days.
        limit: How many messages to return, newest first. Capped at 20.

    Returns:
        A JSON array of ``{role, content, created_at, thread_id, agent_name,
        title}`` objects, newest first. Long content is truncated.

    Notes:
        This tool takes **search criteria, not SQL**. It reads conversation
        history only — no other data is reachable through it.
    """
    try:
        lim = max(1, min(int(limit or 10), _MAX_LIMIT))
    except (TypeError, ValueError):
        lim = 10

    days: int | None = None
    if since_days is not None:
        try:
            days = max(1, int(since_days))
        except (TypeError, ValueError):
            days = None

    term = (search or "").strip() or None

    params = {
        "thread_id": (thread_id or "").strip() or None,
        "agent_name": (agent_name or "").strip() or None,
        "uid": _acting_user(),
        "search": term,
        "search_like": f"%{term}%" if term else None,
        "since_days": days,
        "limit": lim,
    }

    # H-201 (``projects_ai_chat.md`` §21.13). ``chat_message`` and
    # ``chat_session`` are FORCE RLS in production, so an unbound read finds
    # nothing. The run's tenant comes from the executor opener, which reads it
    # on this frame from the run boundary, never from a tool argument (R11).
    # With the bind flag ON and no tenant, the opener is None and the tool
    # answers empty. Nothing falls back to an unbound session.
    # D12: with no acting member there is no reader, so nothing is visible.
    if not params["uid"]:
        return "[]"

    try:
        from gateway.rooms import SESSION_VISIBLE_SQL
        from orchestrator.executor import _graph_session_opener_current

        _open = _graph_session_opener_current()
    except Exception as exc:
        return f"query_history failed: {exc}"
    if _open is None:
        return "[]"

    try:
        from sqlalchemy import text

        with _open() as s:
            result = s.execute(
                text(_SEARCH_HEAD + SESSION_VISIBLE_SQL + _SEARCH_TAIL), params)
            rows = result.fetchmany(lim)
            columns = list(result.keys())
    except Exception as exc:
        return f"query_history failed: {exc}"

    if not rows:
        return "[]"

    output: list[dict] = []
    for row in rows:
        entry: dict = {}
        for i, col in enumerate(columns):
            val = row[i]
            if isinstance(val, str) and len(val) > _CONTENT_CAP:
                val = val[:_CONTENT_CAP] + "..."
            try:
                _json.dumps(val)
            except (TypeError, ValueError):
                val = str(val)
            entry[col] = val
        output.append(entry)

    return _json.dumps(output, indent=2, default=str)
