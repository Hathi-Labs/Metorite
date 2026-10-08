"""The email source of the narrowing pipeline. WS-48 N2.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.2, §3.6, §4, §5 and
§9 N2. The tool and its three steps live in ``acb_skills/narrowing.py``. This
file is only the adapter: it says HOW the email app narrows and reads.

🔴 **No new client and no database (R5, WS48-F5).** The adapter takes ONE
callable, the agent's own ``_get`` (``agents.py``), which goes through
``_request`` and ``_headers``. ``_headers`` refuses a run with no acting
member, so a run with nobody bound makes no gateway call. The adapter never
takes a member or an org as an argument, and it adds no scope and widens none:
``GET /email/search`` scopes to the member's own mailboxes, and
``GET /email/messages/{id}`` joins on the member's own accounts.

🔴 **NARROW sends no full body.** It calls ``GET /email/search`` with
``light=true``, so the route leaves the bodies out. It also reads only the
subject, the sender, the date and the snippet of each row, so a route that
ignored ``light`` still gives no body to the PICK state.

🔴 **A filter maps to a real parameter of the route, or it is refused by
name.** :data:`PARAMS` is the whole map, and ``test_email_narrow_source.py``
reads the route's own signature to prove each target. A value of the wrong
type raises :class:`acb_skills.narrowing.FilterRefused`, because the route
drops a date that it cannot parse in silence (``search._parse_dt``).

**Recall is lexical** (§2.3, Q3). ``hybrid=true`` only re-orders the
full-text matches. The route ANDs bare words, so the member's question as it
stands finds almost nothing. With no ``words`` filter, the adapter searches
for ANY word of the question (an ``OR`` of its words). With ``words``, the
model names the search words itself, with their synonyms.

🔴 **READ changes no read state.** The READ route is the route of
``read_email`` (§4). An open of that route marks the mail read, so READ sends
:data:`READ_PARAMS` (``mark_read=false``). A background read is not the
member opening the mail.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from acb_common import get_logger
from acb_skills.narrowing import (
    BODY_CLIP,
    MAX_CANDIDATES,
    READ_CAP,
    Candidate,
    FilterRefused,
    FullItem,
    Narrowed,
)

_log = get_logger("agent.email_assistant.narrow_source")

#: The agent's own gateway GET: ``(path, params) -> JSON``.
Getter = Callable[[str, "dict[str, Any] | None"], Awaitable[Any]]

#: The NARROW route (§4).
SEARCH_PATH = "/email/search"
#: The READ route: the route of ``read_email`` (§4).
MESSAGE_PATH = "/email/messages/{id}"

#: Filter key -> the query parameter of ``GET /email/search`` that it sets.
#: The keys of §4, and ``unread`` (WS-48 N2). ``words`` is the search text.
PARAMS: dict[str, str] = {
    "account_id": "account_id",
    "folder": "folder",
    "labels": "labels",
    "from": "from_addr",
    "to": "to_addr",
    "after": "received_after",
    "before": "received_before",
    "has_attachments": "has_attachments",
    "unread": "is_read",
    "sender_category": "sender_category",
}
#: The filter key of the search words. It sets ``q``.
WORDS = "words"
FILTER_KEYS: frozenset[str] = frozenset(PARAMS) | {WORDS}

#: The fixed parameters of every NARROW call (§4: ``light=true``, ``hybrid=true``).
FIXED_PARAMS: dict[str, str] = {
    "light": "true",
    "hybrid": "true",
    "page": "1",
    "page_size": str(MAX_CANDIDATES),
}

#: The parameters of every READ call: a read that leaves ``is_read`` alone.
READ_PARAMS: dict[str, str] = {"mark_read": "false"}

#: The folder scope when the filters name none: every folder but junk and
#: trash, sent mail included (``core.folder_scope``).
DEFAULT_FOLDER = "all"

#: The most reads of the READ step that run at one time.
READ_IN_FLIGHT = 5

#: The words that the search grammar reads as an operator. A question word
#: "or" must not join two words, so the adapter drops these from an ``OR``.
_OPERATORS = frozenset({"or", "and", "not"})
#: The Postgres ``english`` stop words. The route drops them, so a derived
#: search leaves them out BEFORE it counts to :data:`MAX_QUERY_WORDS`.
#: Otherwise "can you look through my mail ..." fills the limit with words
#: that match nothing (review, WS-48 N2).
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
#: The longest value of ``words``, ``from``, ``to`` and the other texts.
TEXT_LIMIT = 500
#: The most labels that one call may name.
MAX_LABELS = 10

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_TAG = re.compile(r"<[^>]+>")


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

    The route drops a value that it cannot parse, with no error. So a bad
    value here is refused, and never sent. A date with no time is a UTC day.
    ``after`` starts at the start of that day. ``before`` INCLUDES that day:
    the route applies ``received_at <= before``, so a date-only ``before``
    goes out as the last moment of the day (review P1, WS-48 N2).
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


def _labels(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        raise FilterRefused("the filter labels must be a text or a list of texts.")
    if len(items) > MAX_LABELS:
        raise FilterRefused(f"the filter labels holds more than {MAX_LABELS} labels.")
    return [_text("labels", item, 100) for item in items]


def _account(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise FilterRefused("the filter account_id must be the id of a mailbox.") from None


def search_text(query: str, filters: Mapping[str, Any]) -> str:
    """The ``q`` of the search. ``""`` means a search on the filters only.

    With ``words`` set, its text, as the model wrote it in the search
    grammar. ``words`` set to ``""`` searches on the filters only. With no
    ``words``, ANY word of *query*: the route ANDs bare words, and a
    question holds words that no mail holds.
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
    params["folder"] = DEFAULT_FOLDER
    for key, value in filters.items():
        if key == WORDS:
            continue
        target = PARAMS[key]
        if key == "account_id":
            params[target] = _account(value)
        elif key == "labels":
            params[target] = _labels(value)
        elif key in {"after", "before"}:
            params[target] = _moment(key, value)
        elif key == "has_attachments":
            params[target] = "true" if _flag(key, value) else "false"
        elif key == "unread":
            # unread=true is is_read=false. unread=false is NO filter, as
            # ``query_inbox(unread_only=false)`` is: read mail only would drop
            # every unread answer with no word to the model (review P2).
            if _flag(key, value):
                params[target] = "false"
        else:
            params[target] = _text(key, value, 200)
    text = search_text(query, filters)
    if text:
        params["q"] = text
    return params


# ── The summaries ────────────────────────────────────────────────────────────


def _who(address: Any) -> str:
    if not isinstance(address, Mapping):
        return ""
    name = str(address.get("name") or "").strip()
    email = str(address.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name


def _canonical(value: Any) -> str | None:
    """The canonical form of a UUID, or ``None``. A read id goes into a
    request path, and httpx removes dot segments, so only a UUID may."""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def candidate_of(row: Mapping[str, Any]) -> Candidate:
    """One search row as a short summary. It reads no body field.

    The snippet is the route's ``highlight`` when it has one: the passages
    that matched the search (``ts_headline``, at most two short fragments,
    computed with ``light=true`` too). The provider ``snippet`` is the head
    of the mail, so a match deep in the body would not reach PICK, and a
    confident ``no`` could drop the mail (review P2, WS-48 N2).
    """
    highlight = " ".join(_TAG.sub("", str(row.get("highlight") or "")).split())
    snippet = highlight or row.get("snippet") or ""
    return Candidate(
        id=str(row.get("id") or ""),
        title=str(row.get("subject") or "(no subject)"),
        who=_who(row.get("from_address")),
        when=str(row.get("received_at") or ""),
        snippet=str(snippet or ""),
    )


def _body(message: Mapping[str, Any]) -> str:
    text = str(message.get("body_text") or "").strip()
    if not text:
        html = str(message.get("body_html") or "")
        text = " ".join(_TAG.sub(" ", html).split())
    return text[:BODY_CLIP]


# ── The adapter (§4) ─────────────────────────────────────────────────────────


class EmailNarrowSource:
    """The email adapter. ONE gateway getter, the agent's own (R5)."""

    name = "email"
    filter_keys = FILTER_KEYS

    def __init__(self, get: Getter) -> None:
        self._get = get

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        """NARROW: ``GET /email/search``, light and hybrid, at most 200 rows."""
        params = search_params(query, filters)
        data = await self._get(SEARCH_PATH, params)
        rows = data.get("emails") if isinstance(data, Mapping) else None
        rows = [r for r in (rows or []) if isinstance(r, Mapping)][:MAX_CANDIDATES]
        total = data.get("total") if isinstance(data, Mapping) else 0
        total = total if isinstance(total, int) and not isinstance(total, bool) else 0
        return Narrowed(candidates=[candidate_of(r) for r in rows], total=max(total, len(rows)))

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        """READ: the route of ``read_email``, for the kept ids only, in order.

        At most :data:`~acb_skills.narrowing.READ_CAP` ids, each one a UUID.
        A read that fails leaves its item out, and the tool's count line
        says how many kept items it did not read.
        """
        wanted: list[str] = []
        for raw in list(ids)[:READ_CAP]:
            canonical = _canonical(raw)
            if canonical is not None and canonical not in wanted:
                wanted.append(canonical)
        gate = asyncio.Semaphore(READ_IN_FLIGHT)

        async def _one(message_id: str) -> FullItem | None:
            async with gate:
                try:
                    message = await self._get(
                        MESSAGE_PATH.format(id=message_id), dict(READ_PARAMS))
                except Exception as exc:  # one failed read is not a failed call
                    _log.warning(
                        "narrow_source.read_failed",
                        status=getattr(exc, "status", None), error_type=type(exc).__name__,
                    )
                    return None
            if not isinstance(message, Mapping):
                return None
            return FullItem(
                id=message_id,
                title=str(message.get("subject") or "(no subject)"),
                who=_who(message.get("from_address")),
                when=str(message.get("received_at") or ""),
                text=_body(message),
            )

        got = await asyncio.gather(*(_one(i) for i in wanted))
        # The tool matches items by id. Give back the id it asked with.
        by_canonical = {item.id: item for item in got if item is not None}
        out: list[FullItem] = []
        for raw in list(ids)[:READ_CAP]:
            item = by_canonical.get(_canonical(raw) or "")
            if item is not None:
                out.append(FullItem(id=str(raw), title=item.title, who=item.who,
                                    when=item.when, text=item.text))
        return out


__all__ = [
    "DEFAULT_FOLDER",
    "FILTER_KEYS",
    "FIXED_PARAMS",
    "MESSAGE_PATH",
    "PARAMS",
    "READ_PARAMS",
    "SEARCH_PATH",
    "WORDS",
    "EmailNarrowSource",
    "candidate_of",
    "search_params",
    "search_text",
]
