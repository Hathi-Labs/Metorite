"""The WhatsApp source of the narrowing pipeline. WS-48 N4.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.2, §3.6, §4, §5 and
§9 N4. The tool and its three steps live in ``acb_skills/narrowing.py``. This
file is only the adapter: it says HOW the WhatsApp app narrows and reads. It
follows the email adapter of N2 (``agent-email-assistant/narrow_source.py``).

🔴 **No new client and no database (R5, WS48-F5).** The adapter takes ONE
callable, the agent's own ``_get`` (``agents.py``), which goes through
``_request`` and ``_headers``. ``_headers`` refuses a run with no acting
member, so a run with nobody bound makes no gateway call. The adapter never
takes a member or an org as an argument, and it adds no scope and widens none:
``GET /whatsapp/search`` scopes to the member's own ``wa_accounts``, and
``GET /whatsapp/chats/{id}/messages`` refuses a chat of another member
(``assert_chat_owned``).

🔴 **NARROW sends no thread.** A candidate is ONE message of the search, and
its summary holds only the chat name, the sender, the time and a snippet of
that message (:func:`candidate_of`). NARROW never calls the thread route.

🔴 **A filter maps to a real parameter of the route, or it is refused by
name.** :data:`PARAMS` is the whole map, and ``test_whatsapp_narrow_source.py``
reads the route's own signature to prove each target. A value of the wrong
type raises :class:`acb_skills.narrowing.FilterRefused`.

**Recall is lexical** (§2.3, Q3), and the route's text search has no stems
(``to_tsvector('simple', ...)``), so "price" does not find "prices". With no
``words`` filter, the adapter searches for ANY word of the question, in the
``websearch`` grammar (``a OR b``), with the stop words left out first: the
``simple`` search keeps them, so "the" would match every message. With
``words``, the model names the search words itself, with their other forms.

🔴 **READ changes no state.** READ reads each kept message with a small window
of context around it, on the route of ``read_whatsapp_chat`` with ``around``
(:data:`THREAD_PATH`). That route only reads: it writes no row and sends no
read receipt, so the sender sees no "seen" mark. ``test_whatsapp_read_no_mark.py``
holds it on a real database.

**An item id is ``<chat_id>:<message_id>``.** The thread route needs the
chat, and the adapter keeps no state between NARROW and READ, so the id
carries both. Each half must be a UUID before it goes into a path.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, NamedTuple

from acb_common import get_logger
from acb_skills.narrowing import (
    BODY_CLIP,
    MAX_CANDIDATES,
    READ_CAP,
    SNIPPET_CLIP,
    Candidate,
    FilterRefused,
    FullItem,
    Narrowed,
)

_log = get_logger("agent.whatsapp_assistant.narrow_source")

#: The agent's own gateway GET: ``(path, params) -> JSON``.
Getter = Callable[[str, "dict[str, Any] | None"], Awaitable[Any]]

#: The NARROW route (§4).
SEARCH_PATH = "/whatsapp/search"
#: The READ route: the route of ``read_whatsapp_chat`` (§4), with ``around``.
THREAD_PATH = "/whatsapp/chats/{chat_id}/messages"

#: Filter key -> the query parameter of ``GET /whatsapp/search`` that it sets.
#: ``words`` is the search text, and it sets ``q``.
PARAMS: dict[str, str] = {
    "account_id": "account_id",
    "chat_id": "chat_id",
    "contact": "contact",
    "group": "chat_kind",
    "after": "sent_after",
    "before": "sent_before",
    "from_me": "direction",
    "has_media": "has_media",
}
#: The filter key of the search words. It sets ``q``.
WORDS = "words"
FILTER_KEYS: frozenset[str] = frozenset(PARAMS) | {WORDS}

#: The fixed parameters of every NARROW call (§4: ``hybrid=true``). The
#: ``websearch`` grammar lets one search hold ``a OR b``. The route gives no
#: total, so NARROW asks for ONE row past the cap: a 201st row means that more
#: than 200 matched, and the count line says so (review P1, WS-48 N4).
FIXED_PARAMS: dict[str, str] = {
    "hybrid": "true",
    "websearch": "true",
    "limit": str(MAX_CANDIDATES + 1),
}

#: The messages of context on each side of a kept message (a small window).
READ_WINDOW = 2
#: The clip of one message line inside a READ window.
LINE_CLIP = 1200

#: The most reads of the READ step that run at one time.
READ_IN_FLIGHT = 5

#: H-279. The filter keys that choose WHOLE chats, or a span that ends now.
#: A search on these keys only, with no search words, finds every message of
#: each chat that it names, since ``after``. ``from_me`` and ``has_media``
#: choose a part of a chat, and ``before`` a span that does not end now, so
#: they are not here. ``contact`` is here only when it names the chat itself
#: (:func:`is_whole_chat`), because it also matches a sender's name.
SPAN_KEYS: frozenset[str] = frozenset({"account_id", "chat_id", "contact", "group", "after"})
#: H-279. The most messages that the read of one whole chat takes, the newest.
#: It is the default ``limit`` of the thread route. 100 lines of about 60
#: characters fit in one or two blocks of the body clip.
SPAN_LIMIT = 100
#: H-279. The messages that the next step of a cut span reads past the
#: matches, for the messages that reach the chat between NARROW and READ.
SPAN_SLACK = 20
#: The highest ``limit`` of the thread route (``list_messages``, ``le=500``).
#: The next step of a cut span names it.
THREAD_LIMIT_MAX = 500

#: The words that the ``websearch`` grammar reads as an operator. A question
#: word "or" must not join two words, so the adapter drops these.
_OPERATORS = frozenset({"or", "and", "not"})
#: The Postgres ``english`` stop words. The route's ``simple`` search keeps
#: them, so a derived search leaves them out, or "the" matches every message.
STOP_WORDS = frozenset("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing don down
during each few for from further had has have having he her here hers herself
him himself his how i if in into is it its itself just me more most my myself
no nor not now of off on once only or other our ours ourselves out over own
same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours
yourself yourselves
""".split())
#: The most words of the question that one derived search holds.
MAX_QUERY_WORDS = 16
#: The longest value of ``words`` and ``contact``.
TEXT_LIMIT = 500

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


# ── The values ───────────────────────────────────────────────────────────────


def _text(key: str, value: Any, limit: int = TEXT_LIMIT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FilterRefused(f"the filter {key} must be a text.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise FilterRefused(f"the filter {key} is longer than {limit} characters.")
    return text


def _flag(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise FilterRefused(f"the filter {key} must be true or false.")


def _moment(key: str, value: Any) -> str:
    """A date or a date and time, as ISO 8601 with an explicit offset.

    A date with no time is a UTC day. ``after`` starts at the start of that
    day. ``before`` INCLUDES that day: the route applies ``sent_at <= before``,
    so a date-only ``before`` goes out as the last moment of the day.
    """
    if not isinstance(value, str) or not value.strip():
        raise FilterRefused(f"the filter {key} must be a date, for example 2026-09-01.")
    raw = value.strip()
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            moment = datetime.combine(day, datetime.min.time())
            if key == "before":
                moment = datetime.combine(day, datetime.max.time())
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise FilterRefused(
            f"the filter {key} must be a date, for example 2026-09-01."
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat()


def _id(key: str, value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise FilterRefused(f"the filter {key} must be the id of {what}.") from None


def search_text(query: str, filters: Mapping[str, Any]) -> str:
    """The ``q`` of the search. ``""`` means a search on the filters only.

    With ``words`` set, its text, as the model wrote it in the ``websearch``
    grammar. ``words`` set to ``""`` searches on the filters only. With no
    ``words``, ANY word of *query*, stop words left out.
    """
    if WORDS in filters:
        value = filters[WORDS]
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        return _text(WORDS, value)
    seen: list[str] = []
    for word in _WORD.findall(query or ""):
        low = word.lower()
        if low in _OPERATORS or low in STOP_WORDS or len(low) < 2 or low in seen:
            continue
        seen.append(low)
        if len(seen) == MAX_QUERY_WORDS:
            break
    return " OR ".join(seen)


def search_params(query: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    """The query parameters of ONE NARROW call. Raises :class:`FilterRefused`."""
    unknown = sorted(str(k) for k in filters if k not in FILTER_KEYS)
    if unknown:  # the tool refuses these first; this is the second line
        raise FilterRefused(f"unknown filter key: {', '.join(unknown[:10])}.")
    params: dict[str, Any] = dict(FIXED_PARAMS)
    for key, value in filters.items():
        if key == WORDS:
            continue
        target = PARAMS[key]
        if key == "account_id":
            params[target] = _id(key, value, "a WhatsApp number")
        elif key == "chat_id":
            params[target] = _id(key, value, "a chat")
        elif key in {"after", "before"}:
            params[target] = _moment(key, value)
        elif key == "group":
            # group=true is a group chat, and group=false a chat with one person.
            params[target] = "group" if _flag(key, value) else "dm"
        elif key == "from_me":
            # from_me=true is what the member sent, and false what others sent.
            params[target] = "out" if _flag(key, value) else "in"
        elif key == "has_media":
            params[target] = "true" if _flag(key, value) else "false"
        else:  # contact
            params[target] = _text(key, value, 200)
    text = search_text(query, filters)
    if text:
        params["q"] = text
    elif len(params) == len(FIXED_PARAMS):
        # No search word and no filter: the route would refuse the call, and
        # the model would read "could not search" (review note, WS-48 N4).
        raise FilterRefused(
            "the question holds no search word. Put the search words in words, "
            "or set a filter.")
    return params


# ── The summaries ────────────────────────────────────────────────────────────


def item_id(chat_id: Any, message_id: Any) -> str:
    """The id of one item: ``<chat_id>:<message_id>``."""
    return f"{chat_id}:{message_id}"


def split_id(raw: Any) -> tuple[str, str] | None:
    """The canonical ``(chat_id, message_id)`` of an item id, or ``None``.
    Each half goes into a request path, so each must be a UUID."""
    chat, sep, message = str(raw or "").strip().partition(":")
    if not sep:
        return None
    try:
        return str(uuid.UUID(chat)), str(uuid.UUID(message))
    except (ValueError, AttributeError, TypeError):
        return None


def _who(m: Mapping[str, Any]) -> str:
    if m.get("direction") == "out":
        return "You"
    return str(m.get("sender_name") or "").strip() or "Them"


def _chat_title(m: Mapping[str, Any]) -> str:
    kind = str(m.get("chat_kind") or "")
    label = {"group": "Group", "broadcast": "Broadcast"}.get(kind, "Chat")
    name = str(m.get("chat_name") or "").strip() or "(no name)"
    return f"{label}: {name}"


def message_text(m: Mapping[str, Any]) -> str:
    """The text of ONE message: its body, or the transcript of a voice note
    when the body is empty (§9 N4), or the kind of a media message."""
    body = " ".join(str(m.get("body_text") or "").split())
    if body:
        return body
    transcript = " ".join(str(m.get("transcript_text") or "").split())
    if transcript:
        return f"(voice note) {transcript}"
    kind = str(m.get("kind") or "")
    return f"[{kind}]" if kind and kind != "text" else ""


def _search_words(q: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(q or "") if w.lower() not in _OPERATORS]


def excerpt(text: str, words: Sequence[str], limit: int = SNIPPET_CLIP) -> str:
    """At most *limit* characters of *text*, around the first search word.

    The route gives no highlight of the match. A message that is longer than
    the clip and holds the match near its end would reach PICK with no match
    in its snippet, and a confident ``no`` could drop it. So the snippet starts
    a little before the first search word that the text holds.
    """
    if len(text) <= limit:
        return text
    low = text.lower()
    first: int | None = None
    for word in words:
        hit = re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", low) if word else None
        if hit and (first is None or hit.start() < first):
            first = hit.start()
    if first is None or first < limit - 60:  # the head of the message shows it
        return text[:limit]
    start = first - 80
    return "… " + text[start:start + limit - 2]


def read_size(m: Mapping[str, Any]) -> int:
    """The characters that READ gives for *m*: a window of
    ``2 * READ_WINDOW + 1`` lines (H-276).

    The other lines of the window are not known before the read. The estimate
    takes each one as long as the line of *m* itself. The cost check of PICK
    uses it, and nothing else does.
    """
    return (2 * READ_WINDOW + 1) * (len(_line(m, kept=True)) + 1)


def candidate_of(m: Mapping[str, Any], words: Sequence[str] = ()) -> Candidate:
    """One search row as a short summary of ONE message. Never a thread."""
    return Candidate(
        id=item_id(m.get("chat_id") or "", m.get("id") or ""),
        title=_chat_title(m),
        who=_who(m),
        when=str(m.get("sent_at") or ""),
        snippet=excerpt(message_text(m), words),
        size=read_size(m),
    )


def _line(m: Mapping[str, Any], *, kept: bool) -> str:
    mark = ">> " if kept else "   "
    when = str(m.get("sent_at") or "")[:16].replace("T", " ")
    return f"{mark}[{when}] {_who(m)}: {message_text(m)[:LINE_CLIP]}"


def is_whole_chat(
    filters: Mapping[str, Any], params: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    *, more: bool = False,
) -> bool:
    """True when a NARROW call found every message of ONE chat since ``after``.

    H-279. Then the rows are the newest messages of that chat, and READ takes
    them in one read of the thread route, with no PICK. Each condition holds:

    * the search had no words (``q``), so the filters alone chose the rows;
    * every filter key is in :data:`SPAN_KEYS`;
    * every row is in the same chat;
    * with ``contact``, the name of that chat holds it. The route also matches
      a sender's name, and then the rows are a part of the chat, not all of it;
    * with *more* (the route found more than 200 rows), ``chat_id`` names the
      chat. The 200 rows are the newest only, and an older match can be in a
      second chat that ``contact`` or ``group`` also chose (review P1).
    """
    if params.get("q") or not rows:
        return False
    if more and "chat_id" not in filters:
        return False
    if not set(filters) - {WORDS} <= SPAN_KEYS:
        return False
    chats = {str(r.get("chat_id") or "") for r in rows}
    if len(chats) != 1 or "" in chats:
        return False
    contact = str(params.get(PARAMS["contact"]) or "").lower()
    if contact:
        return all(contact in str(r.get("chat_name") or "").lower() for r in rows)
    return True


def span_further(chat_id: str, found: int, more: bool) -> str:
    """The next step that reads the older messages of a cut span (H-279)."""
    # A few messages can reach the chat between NARROW and READ, and they
    # push the oldest matches out of the newest `found` (review P2).
    limit = THREAD_LIMIT_MAX if more else min(THREAD_LIMIT_MAX, max(found, 1) + SPAN_SLACK)
    return (
        f'To read the older messages, call read_whatsapp_chat with chat_id="{chat_id}" '
        f"and limit={limit}."
    )


def blocks(
    rows: Sequence[Mapping[str, Any]], kept: Mapping[str, str], title: str,
) -> list[FullItem]:
    """The rows of one chat as blocks of whole lines, in reading order (H-279).

    *kept* maps the message id of each kept row to its item id. A kept row
    starts with ``>>``. Each block holds at most :data:`BODY_CLIP` characters,
    so the tool clips no line. A block takes the item id of its first kept
    row, and ``covers`` names the other kept rows. A block with no kept row is
    left out. At most :data:`READ_CAP` blocks remain, the NEWEST, so a cut
    never drops a newer message for an older one.
    """
    out: list[FullItem] = []
    lines: list[str] = []
    ids: list[str] = []
    first: Mapping[str, Any] | None = None
    size = 0

    def close() -> None:
        if ids and first is not None:
            out.append(FullItem(id=ids[0], title=title, who=_who(first),
                                when=str(first.get("sent_at") or ""),
                                text="\n".join(lines), covers=tuple(ids[1:])))

    for r in rows:
        item = kept.get(str(r.get("id") or ""))
        line = _line(r, kept=item is not None)
        if lines and size + 1 + len(line) > BODY_CLIP:
            close()
            lines, ids, first, size = [], [], None, 0
        size += len(line) + (1 if lines else 0)
        lines.append(line)
        if item is not None:
            ids.append(item)
            first = first or r
    close()
    return out[-READ_CAP:]


# ── The windows of READ (H-279) ──────────────────────────────────────────────


class Window(NamedTuple):
    """The rows of one READ window: one kept message and its context. A
    NamedTuple, not a dataclass: ``agents.py`` loads this file by path, with
    no entry in ``sys.modules``, and a dataclass needs one."""

    chat_id: str
    message_id: str
    rows: Sequence[Mapping[str, Any]]


def _order(r: Mapping[str, Any]) -> tuple[str, str]:
    """The reading order of the thread route: ``sent_at``, then ``id``. A row
    with no ``sent_at`` is the oldest, as the route gives it."""
    return str(r.get("sent_at") or ""), str(r.get("id") or "")


def merge_windows(
    windows: Sequence[Window], raw_of: Mapping[tuple[str, str], str],
) -> list[FullItem]:
    """The READ windows as items, with each message once (H-279).

    Two windows of one chat that share a message merge into one block, in
    reading order, and each kept message in it starts with ``>>``. The block
    takes the item id of its kept message of the best rank, and ``covers``
    names the other kept messages. So the tool reads each message once and
    counts each kept item once. A merged block longer than :data:`BODY_CLIP`
    is not merged: its windows stay apart, so the tool cuts no kept line.
    *windows* is in rank order, and *raw_of* maps each ``(chat, message)`` to
    the item id that the tool asked with.
    """
    groups: list[list[Window]] = []
    for w in windows:
        ids = {str(r.get("id")) for r in w.rows}
        hits = [g for g in groups if g[0].chat_id == w.chat_id
                and ids & {str(r.get("id")) for x in g for r in x.rows}]
        if not hits:
            groups.append([w])
            continue
        # The group of the best rank takes this window and every other group
        # that it touches, so a window that joins two groups merges all three.
        head = hits[0]
        head.append(w)
        for g in hits[1:]:
            head.extend(g)
            groups.remove(g)
    rank = {(w.chat_id, w.message_id): n for n, w in enumerate(windows)}
    out: list[FullItem] = []
    for g in groups:
        g.sort(key=lambda w: rank[(w.chat_id, w.message_id)])
        out.extend(_group_items(g, raw_of))
    return out


def _window_item(w: Window, raw: str) -> FullItem:
    anchor = next(r for r in w.rows if str(r.get("id")) == w.message_id)
    body = "\n".join(_line(r, kept=r is anchor) for r in w.rows)
    return FullItem(id=raw, title=_chat_title(anchor), who=_who(anchor),
                    when=str(anchor.get("sent_at") or ""), text=body[:BODY_CLIP])


def _group_items(group: Sequence[Window], raw_of: Mapping[tuple[str, str], str]) -> list[FullItem]:
    raws = [raw_of.get((w.chat_id, w.message_id), item_id(w.chat_id, w.message_id))
            for w in group]
    if len(group) == 1:
        return [_window_item(group[0], raws[0])]
    rows: dict[str, Mapping[str, Any]] = {}
    for w in group:
        for r in w.rows:
            rows.setdefault(str(r.get("id")), r)
    kept = {w.message_id for w in group}
    ordered = sorted(rows.values(), key=_order)
    body = "\n".join(_line(r, kept=str(r.get("id")) in kept) for r in ordered)
    if len(body) > BODY_CLIP:  # never cut a kept line: keep the windows apart
        return [_window_item(w, raw) for w, raw in zip(group, raws, strict=True)]
    anchor = rows[group[0].message_id]
    return [FullItem(id=raws[0], title=_chat_title(anchor), who=_who(anchor),
                     when=str(anchor.get("sent_at") or ""), text=body,
                     covers=tuple(raws[1:]))]


# ── The adapter (§4) ─────────────────────────────────────────────────────────


class WhatsAppNarrowSource:
    """The WhatsApp adapter. ONE gateway getter, the agent's own (R5)."""

    name = "whatsapp"
    filter_keys = FILTER_KEYS

    def __init__(self, get: Getter) -> None:
        self._get = get

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        """NARROW: ``GET /whatsapp/search``, hybrid, at most 200 messages."""
        params = search_params(query, filters)
        data = await self._get(SEARCH_PATH, params)
        rows = [r for r in (data if isinstance(data, list) else []) if isinstance(r, Mapping)]
        more = len(rows) > MAX_CANDIDATES  # the probe row: more matched
        rows = rows[:MAX_CANDIDATES]
        words = _search_words(str(params.get("q") or ""))
        # H-279: the filters chose every message of one chat. READ takes the
        # chat in one read, with no PICK and no windows.
        span = is_whole_chat(filters, params, rows, more=more)
        further = span_further(str(rows[0].get("chat_id")), len(rows), more) if span else ""
        # The route gives no total, so `more` says "more than", not a count.
        return Narrowed(candidates=[candidate_of(r, words) for r in rows],
                        total=len(rows), more=more, span=span, span_further=further)

    async def read_span(self, candidates: Sequence[Candidate]) -> list[FullItem]:
        """READ of one whole chat (H-279): ONE GET of the thread route, the
        route of ``read_whatsapp_chat``, with ``limit`` and no ``around``.

        The route gives the newest ``limit`` messages, oldest first (H-277).
        The limit is the candidate count, at most :data:`SPAN_LIMIT`, so the
        read takes the newest matches of the chat and no older message. A
        candidate that the read does not hold is not in any block, and the
        tool counts it as not read. A GET only: no state changes.
        """
        chats = {split_id(c.id)[0] if split_id(c.id) else "" for c in candidates}
        if len(chats) != 1 or "" in chats:
            return []
        [chat_id] = chats
        limit = min(len(candidates), SPAN_LIMIT)
        rows = await self._get(THREAD_PATH.format(chat_id=chat_id), {"limit": str(limit)})
        rows = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, Mapping)]
        # The message id of each candidate, to its item id as the tool gave it.
        kept = {split_id(c.id)[1]: c.id for c in candidates}  # type: ignore[index]
        canonical = []
        for r in rows:
            try:
                canonical.append({**r, "id": str(uuid.UUID(str(r.get("id"))))})
            except (ValueError, AttributeError, TypeError):
                canonical.append(r)
        return blocks(canonical, kept, candidates[0].title)

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        """READ: each kept message with :data:`READ_WINDOW` messages of context
        on each side, on the thread route with ``around``, in order.

        At most :data:`~acb_skills.narrowing.READ_CAP` ids, each one two UUIDs.
        A read that fails leaves its item out, and the tool's count line says
        how many kept items it did not read. A GET only: no state changes.
        """
        wanted: list[tuple[str, str]] = []
        for raw in list(ids)[:READ_CAP]:
            parts = split_id(raw)
            if parts is not None and parts not in wanted:
                wanted.append(parts)
        gate = asyncio.Semaphore(READ_IN_FLIGHT)

        async def _one(chat_id: str, message_id: str) -> Window | None:
            async with gate:
                try:
                    rows = await self._get(
                        THREAD_PATH.format(chat_id=chat_id),
                        {"around": message_id, "window": str(READ_WINDOW)},
                    )
                except Exception as exc:  # one failed read is not a failed call
                    _log.warning(
                        "narrow_source.read_failed",
                        status=getattr(exc, "status", None), error_type=type(exc).__name__,
                    )
                    return None
            rows = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, Mapping)]
            if not any(str(r.get("id")) == message_id for r in rows):
                return None
            return Window(chat_id, message_id, rows)

        got = await asyncio.gather(*(_one(c, m) for c, m in wanted))
        # The tool matches items by id. Give back the id it asked with.
        raw_of: dict[tuple[str, str], str] = {}
        for raw in list(ids)[:READ_CAP]:
            parts = split_id(raw)
            if parts is not None:
                raw_of.setdefault(parts, str(raw))
        return merge_windows([w for w in got if w is not None], raw_of)


__all__ = [
    "FILTER_KEYS",
    "FIXED_PARAMS",
    "PARAMS",
    "READ_WINDOW",
    "SEARCH_PATH",
    "SPAN_KEYS",
    "SPAN_LIMIT",
    "THREAD_PATH",
    "WORDS",
    "WhatsAppNarrowSource",
    "Window",
    "blocks",
    "candidate_of",
    "excerpt",
    "is_whole_chat",
    "item_id",
    "merge_windows",
    "message_text",
    "read_size",
    "search_params",
    "search_text",
    "span_further",
    "split_id",
]
