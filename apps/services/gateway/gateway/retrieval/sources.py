"""The sources of company retrieval (WS-61 KR-1, ``knowledge_base.md`` §20.3).

A **source** is one app: its key, the feature its router demands, the name a
member reads, and ``find(user, q, k)``. ``find`` checks the feature first,
and then calls the app's OWN search function with this request's member. So:

* **No new data path.** No source writes SQL, opens a connection or builds a
  visibility rule. The app's function does that, under the tenant that the
  request bound. ``tests/unit/test_company_search_sources.py`` fails if a
  module here imports ``sqlalchemy``, ``get_db`` or a session opener.
* **The app's gate, again, here.** Each app checks its feature on its ROUTER,
  not inside the search function. Called from here, the function would skip
  that gate. So ``Source.find`` checks ``feature:<app>`` first, and a member
  without the app gets no hit and no call. The CRM source also applies the
  CRM router's kill switch and access rule, the artifacts source asks
  whether the registry is on, and the knowledge source applies the Knowledge
  router's kill switch (``KNOWLEDGE_ENABLED`` and ``KNOWLEDGE_ORGS``), because
  every member holds ``feature:*``.
* **Each app function is imported when the source runs**, never at import
  time. The suites replace a function on its module, and a name bound here
  at import would keep the real one.
* **The consumer picks meaning, not the source** (WS-61 KB-3c). A source
  with ``meaning=True`` takes ``hybrid`` from ``Source.find``, and the
  default is ``False``. ``GET /search/company`` asks for it when
  ``KNOWLEDGE_EMBED`` names the org, so the assistant finds a paraphrase.
  The command bar calls ``find`` with no ``hybrid``, so a keystroke never
  costs an embed. Only the knowledge source has ``meaning=True``.

A **hit** has one shape for every consumer: ``app``, ``kind``, ``id``,
``title``, ``hint``, ``snippet``, ``when`` and ``href``. Code builds the
link, never a model. The snippet comes from the app's own row, and it holds
at most :data:`SNIPPET_CHARS` characters.

**The stamp** (WS-61 CX-1, ``company_context.md`` §3). Each hit also
carries ``stamp``, the date its record last changed, from the app's own row.
``when`` keeps its meaning, such as the due date of a task. People have no
stamp. ``Hit.as_dict`` emits ``stamp`` only when the consumer asks, so with
``CONTEXT_RECENCY`` off the route's hit keeps its eight keys.

**Two more abilities, ``related`` and ``open``** (WS-FIND FIND-3,
``find_engine.md`` §4.3, ``knowledge_graph.md`` §6). The CRM, People and Email
sources declare them. ``related`` gives triples of ids, edges and keys (an
address, a web domain), never text. ``open`` gives the hits of the refs that
the member can open, and checks the app's gates itself. Each calls a function
of its app's own module, as ``find`` does, with ids checked as UUIDs first.

**One more ability, ``named``** (WS-FIND FIND-5, ``find_engine.md`` §4.2
``names`` and §5.2). The CRM, People and Email sources declare it, with the
kinds of record whose names ``resolve_name`` reads. ``named`` calls the app's
own search, and gives each record as a :class:`Named`: its kind, id, name as
stored and link. Email gives each mail sender once, with the link of the
newest mail, through its own read ``search.named_mail``. People and Email
match one phrase (``names_word``), so the route asks them with one word.
``GET /search/names`` tests the name itself.

**Two more options of ``find``** (CX-1, §4.2 and §4.5), each for a source
that declares it, like ``meaning``. A source with ``dated=True`` takes
``after`` and filters in its app's own SQL. A source with ``reviewed=True``
takes ``reviewed`` and ranks a page past its review date lower. Only
``GET /search/company`` passes them, and only with ``CONTEXT_RECENCY`` on.

**The past chats** (WS-FIND HRD-21, ``find_engine.md`` §3.1 rung 6 and §6).
The ``chat`` source reads the member's own solo chats with Mira through
``gateway.chat_search``, after ``feature:chat`` and ``CHAT_SEARCH_ORGS``. It
declares ``current``, so it takes the id of the chat that the search runs in,
and it never answers that chat.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext
from acb_common.name_match import NearName
from fastapi import HTTPException

#: The most characters of matched text a hit carries (§20.3).
SNIPPET_CHARS = 400

logger = logging.getLogger(__name__)

#: Seconds kept back from the source timeout, so the vector half ends before
#: the route's own deadline (KG-3b).
VECTOR_MARGIN_S = 0.2
#: The least time worth a vector half. Under it, the lexical answer goes at once.
VECTOR_FLOOR_S = 0.15

_MARK = re.compile(r"</?mark>", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Hit:
    """One record that matched, in the shape every consumer reads."""

    app: str
    kind: str
    id: str
    title: str
    hint: str
    href: str
    snippet: str = ""
    when: str | None = None
    #: The date the record last changed in substance, as ISO text (CX-1, §3).
    stamp: str | None = None
    #: The care flag of a Knowledge page (KB-AI-4): ``"expired"`` or
    #: ``"unowned"``, from a closed list in code, or ``None``.
    flag: str | None = None
    #: The review date of a flagged page, as ISO text.
    review_at: str | None = None
    #: How the hit matched, from the closed list of ``retrieval.find.RUNGS``
    #: (FIND-2). ``None`` unless ``FIND_ORGS`` names the org.
    matched_by: str | None = None
    #: The content words that the hit holds, and how many the query has.
    covers: int | None = None
    of: int | None = None
    #: The one word that a ``word`` hit matched alone, or the asked word of a
    #: ``near`` hit.
    word: str | None = None
    #: The near spelling that a ``near`` hit matched in place of ``word``
    #: (HRD-18b): one token of letters and digits, from a name that the member
    #: can open. The dict names it ``as``.
    as_: str | None = None
    #: The hit of the answer that a ``related`` hit came from, as its ref
    #: ``<kind>:<id>`` (FIND-3, ``find_engine.md`` §3.5). The dict names it
    #: ``from``.
    from_: str | None = None
    #: The edge of a ``related`` hit, from the closed list ``find.EDGES``. It
    #: holds no text of either record.
    edge: str | None = None
    #: The record edge that led to the key of a related mail, from the same
    #: closed list, or ``None``. So "mail from the lead of deal:…" is
    #: ``edge`` ``mail_from`` and ``via`` ``lead_of``.
    via: str | None = None
    #: The conversation of a mail that Email's ``open`` gives: its thread id,
    #: or its own id when it has none (FIND-3 fix round, P2-1). Internal: the
    #: related step takes one place for each thread. :meth:`as_dict` never
    #: emits it, so no answer and no text holds it.
    thread: str | None = None

    def as_dict(self, *, stamp: bool = False, find: bool = False) -> dict[str, Any]:
        """The hit for a consumer. *stamp* adds ``stamp`` as the ninth key.

        A hit with a ``flag`` (KB-AI-4) also adds ``flag`` and ``review_at``.
        A hit without one keeps its keys.

        ``GET /search/company`` asks for it with ``CONTEXT_RECENCY`` on. Off,
        the dict is the one of KR-1, byte for byte.
        """
        out = {
            "app": self.app, "kind": self.kind, "id": self.id,
            "title": self.title, "hint": self.hint, "snippet": self.snippet,
            "when": self.when, "href": self.href,
        }
        if stamp:
            out["stamp"] = self.stamp
        if self.flag is not None:
            out["flag"] = self.flag
            out["review_at"] = self.review_at
        if find and self.matched_by is not None:
            # FIND-2. Off, no key is added, so the dict is the one of KR-ZERO.
            out["matched_by"] = self.matched_by
            out["covers"] = self.covers
            out["of"] = self.of
            if self.word is not None:
                out["word"] = self.word
            if self.as_ is not None:
                out["as"] = self.as_
            # FIND-3. Only a related hit carries them, so no other hit changes.
            if self.from_ is not None:
                out["from"] = self.from_
            if self.edge is not None:
                out["edge"] = self.edge
            if self.via is not None:
                out["via"] = self.via
        return out


#: ``read(user, q, k)``. A source with ``meaning=True`` also takes
#: ``hybrid=`` by name, one with ``dated=True`` takes ``after=`` and one with
#: ``reviewed=True`` takes ``reviewed=``.
Reader = Callable[..., Awaitable[list[Hit]]]

#: ``near(user, words, k)`` (HRD-18b, ``find_engine.md`` §4.3): for each word,
#: at most *k* ``name_match.NearName`` from rows that the member can open. One
#: call reads the app's names once for every word of the lookup.
NearReader = Callable[..., Awaitable[dict[str, list[NearName]]]]

#: ``related(user, refs)`` (FIND-3, ``find_engine.md`` §4.3): triples of a ref,
#: an edge of ``find.EDGES`` or a key of ``find.KEYS``, and a linked ref, from
#: stored ids and addresses only. No text of a record.
RelatedReader = Callable[..., Awaitable[list[tuple[str, str, str]]]]

#: ``open(user, refs)`` (FIND-3, ``knowledge_graph.md`` §6): the hits of the
#: refs that the member can open, and nothing for any other ref.
OpenReader = Callable[..., Awaitable[list[Hit]]]


@dataclass(frozen=True)
class Named:
    """One record that an app's own search found for a name (FIND-5).

    *name* is the name as the record holds it, and *href* is the link that
    code built. *forms* are the stored forms of the name that the name test
    reads, EACH ON ITS OWN (FIND-5 round 3, P1). Empty means one form,
    *name*. A person with a preferred name has two: the full name, and the
    full name with its first word replaced by the preferred name. A sender
    has the display name and the part of its address before the ``@``. The
    route never puts a form in an answer. *state* is the record's state, such
    as a person's status. The route puts it in the row only from a closed
    list.
    """

    kind: str
    id: str
    name: str
    href: str
    forms: tuple[str, ...] = ()
    state: str = ""
    #: The label of the module of a mirrored record (FIND-11), or empty.
    module: str = ""


#: ``named(user, name, k)`` (FIND-5): at most *k* records that the app's own
#: search finds for *name*, and True when the app gave a full page, so more
#: records may match.
NamedReader = Callable[..., Awaitable[tuple[list[Named], bool]]]

#: The kind of a record of a mirrored CRM module (FIND-11, ``find_engine.md``
#: §9.2). It is no ref kind: ``related`` and ``open`` never take it.
MODULE_KIND = "module_record"

#: The kinds of record of the CRM source, as a ref names them.
CRM_REF_KINDS: tuple[str, ...] = ("deal", "lead", "contact", "company")


def _ids_of(refs: list[str], kinds: tuple[str, ...]) -> dict[str, list[str]]:
    """The ids of each kind of *kinds* in *refs*, each a UUID, in order.

    A ref of another kind, or an id that is no UUID, is left out before any
    SQL, so the app's read never sees one."""
    out: dict[str, list[str]] = {}
    for ref in refs:
        kind, _sep, ident = str(ref).partition(":")
        if kind not in kinds:
            continue
        try:
            clean = str(uuid.UUID(ident))
        except ValueError:
            continue
        mine = out.setdefault(kind, [])
        if clean not in mine:
            mine.append(clean)
    return out


def holds(user: UserContext, feature: str) -> bool:
    """True when *user* holds ``feature:<feature>``, the gate of its router."""
    return bool(user.has_permission(f"feature:{feature}"))


@dataclass(frozen=True)
class Source:
    """One app that company retrieval can ask."""

    key: str
    feature: str
    label: str
    read: Reader
    #: True when the app can also search by meaning. Its reader then takes
    #: ``hybrid``, and the consumer decides it (KB-3c).
    meaning: bool = False
    #: True when the app's search takes a date. Its reader then takes
    #: ``after``, and filters in the app's own SQL (CX-1, §4.5).
    dated: bool = False
    #: True when the app ranks by review state. Its reader then takes
    #: ``reviewed`` (CX-1, §4.2).
    reviewed: bool = False
    #: The app's switch for the member's org, beyond the feature, or
    #: ``None``. A closed switch gives no hit. The route then does not name
    #: the app as searched (CX-1). The reader still checks it itself.
    gate: Callable[[UserContext], bool] | None = None
    #: True when the app's reader takes ``vectors=``: it fuses a vector half
    #: with its lexical half (KG-3b). The route sets it with ``CONTEXT_RECENCY``.
    vectors: bool = False
    #: The near ability of ``find_engine.md`` §4.3 (HRD-18b), or ``None``. The
    #: app's own function reads the names of rows that the member can open,
    #: and ``acb_common.name_match`` scores them. :meth:`near_names` calls it.
    near: NearReader | None = None
    #: True when the app's reader takes ``sender=``: only the mail from a
    #: sender whose name or address holds it (HRD-18b). Only Email.
    senders: bool = False
    #: The related ability of ``find_engine.md`` §4.3 (FIND-3), or ``None``.
    #: :meth:`related_refs` calls it with the refs of the kinds of
    #: :attr:`takes`.
    related: RelatedReader | None = None
    #: The kinds of ref that :attr:`related` takes: record kinds, or the keys
    #: ``address`` and ``domain`` (Email).
    takes: tuple[str, ...] = ()
    #: The open ability of ``knowledge_graph.md`` §6 (FIND-3), or ``None``.
    #: It checks the app's gates itself, so a caller that skips
    #: :meth:`open_refs` still gets nothing that the member cannot open.
    open: OpenReader | None = None
    #: The kinds of ref that :attr:`open` opens: the kinds of hit it gives.
    kinds: tuple[str, ...] = ()
    #: True when the app's reader takes ``current=``: the id of the chat that
    #: the search runs in, which the reader never answers (HRD-21, D-FIND-5).
    #: Only the ``chat`` source. The id only takes a chat away.
    current: bool = False
    #: The kinds of record whose names ``resolve_name`` reads (FIND-5,
    #: ``find_engine.md`` §4.2 ``names``).
    names: tuple[str, ...] = ()
    #: The named ability of FIND-5, or ``None``. :meth:`named_records` calls it.
    named: NamedReader | None = None
    #: True when the app's own search reads its text as ONE phrase (People and
    #: Email). ``resolve_name`` then asks it with one word, the longest, and
    #: its name test checks the other words. The CRM matches word by word.
    names_word: bool = False
    #: Kinds that :meth:`named_records` keeps beside :attr:`names` (FIND-11).
    #: They are no closed kind of ``resolve_name``: the module record of the
    #: CRM. ``names`` stays the one list of the kinds that the tool declares.
    named_extra: tuple[str, ...] = ()
    #: True when a zero of this app may prove that none of its records holds
    #: a word (FIND-4b, ``find_engine.md`` §3.6). Its search must read EVERY
    #: field that its hits print, and every field that holds a word that a
    #: member can type. Only then can the zero of that word alone, with the
    #: three parts of ``find.proves_absence``, prove that no record holds it.
    #: ``find.proves_absence`` reads the declaration of each app that answered.
    #: One app that answered with False stops every claim. No source declares
    #: True yet, because no slice has audited a source. The slice that audits
    #: a source and sets True must name its evidence in §3.6. Fence:
    #: ``test_find_absence_declared.py``.
    proves_absence: bool = False

    def reaches(self, user: UserContext) -> bool:
        """True when *user* holds the feature and the app's switch is open."""
        return holds(user, self.feature) and (self.gate is None or self.gate(user))

    async def near_names(
        self, user: UserContext, words: list[str], k: int,
    ) -> dict[str, list[NearName]]:
        """The names near each of *words*, as *user* (HRD-18b).

        No near ability, no feature or a closed switch: no call and no name.
        The function checks the rest of the app's gates itself, as the reader
        does.
        """
        if self.near is None or not words or not self.reaches(user):
            return {}
        return await self.near(user, list(words), k)

    async def named_records(
        self, user: UserContext, name: str, k: int,
    ) -> tuple[list[Named], bool]:
        """The records of the kinds of :attr:`names` that the app's own search
        finds for *name*, as *user*, and True when the app gave a full page
        (FIND-5). No ability, no feature or a closed switch: no call and no
        record. The function checks the rest of the app's gates itself."""
        if self.named is None or not self.names or not name or not self.reaches(user):
            return [], False
        found, more = await self.named(user, name, k)
        return [n for n in found if n.kind in self.names or n.kind in self.named_extra][:k], bool(more)

    async def related_refs(self, user: UserContext, refs: list[str]) -> list[tuple[str, str, str]]:
        """The triples of the refs of :attr:`takes` in *refs*, as *user*
        (FIND-3). No ability, no such ref, no feature or a closed switch: no
        call and no triple."""
        mine = [r for r in refs if str(r).partition(":")[0] in self.takes]
        if self.related is None or not mine or not self.reaches(user):
            return []
        return list(await self.related(user, mine))

    async def open_refs(self, user: UserContext, refs: list[str]) -> list[Hit]:
        """The hits of the refs of :attr:`kinds` in *refs* that *user* can
        open (FIND-3). A hit of a ref that was not asked is dropped."""
        mine = [r for r in refs if str(r).partition(":")[0] in self.kinds]
        if self.open is None or not mine or not self.reaches(user):
            return []
        asked = set(mine)
        return [h for h in await self.open(user, mine) if f"{h.kind}:{h.id}" in asked]

    async def find(
        self, user: UserContext, q: str, k: int, *, hybrid: bool = False,
        after: datetime | None = None, reviewed: bool = False,
        verified_only: bool = False, vectors: bool = False,
        budget_s: float | None = None, sender: str | None = None,
        sender_words: str | None = None, current: str | None = None,
    ) -> list[Hit]:
        """At most *k* hits for *q*, as *user*. No feature, no call and no hit.

        *hybrid* asks a source with ``meaning`` for the vector half too. It
        costs one embed call, so it is ``False`` unless the consumer asks.
        *after* asks a source with ``dated`` for records stamped on or after
        it. *reviewed* asks a source with ``reviewed`` for the review factor
        of §4.2. *verified_only* (KB-AI-4) asks such a source for pages with an
        owner and a review date in the future. A source ignores each option it does not declare, and the
        reader gets an option only when the consumer sets it. *sender*
        (HRD-18b) asks a source with ``senders`` for the mail from a near
        sender only. That source then reads *sender_words* in place of *q*:
        the other words of the query, which still filter the mail (round 3,
        P2-1). The sender stands for the near word, so the words leave it out.
        *current* (HRD-21) gives a source with ``current`` the id of the chat
        that the search runs in, which it never answers.
        """
        if not holds(user, self.feature):
            return []
        options: dict[str, Any] = {}
        if self.meaning:
            options["hybrid"] = hybrid
        if self.dated and after is not None:
            options["after"] = after
        if self.senders and sender:
            options["sender"] = sender
            q = sender_words or ""
        if self.vectors and vectors:
            options["vectors"] = True
            # The time the route really left this source (KG-3b). Set only with
            # the flag on, so the vector half never outlives the outer deadline.
            if budget_s is not None:
                options["budget_s"] = budget_s
        if self.reviewed and reviewed:
            options["reviewed"] = True
            if verified_only:
                options["verified_only"] = True
        if self.current:
            options["current"] = current
        return (await self.read(user, q, k, **options))[:k]


# ── The switches beyond the feature (CX-1) ──────────────────────────────────
# Each reads its app's own switch at call time, the rule its reader applies.

def _crm_open(user: UserContext) -> bool:
    from gateway.routes.crm import flags

    return bool(flags.crm_org_allowed(user.organization_id))


def _artifacts_open(user: UserContext) -> bool:
    from acb_memory import artifacts as registry

    return bool(registry.enabled())


def _knowledge_open(user: UserContext) -> bool:
    from gateway.routes.knowledge import flags

    return bool(flags.knowledge_org_allowed(user.organization_id))


def _studio_open(user: UserContext) -> bool:
    from gateway.routes.studio import flags

    org = user.organization_id
    return bool(flags.studio_allowed(org) and flags.search_allowed(org))


def _chat_open(user: UserContext) -> bool:
    """The switch of the past chats: ``CHAT_SEARCH_ORGS`` (HRD-21)."""
    from gateway.retrieval import find

    return bool(find.chat_search_named(user.organization_id))


# ── Small helpers, one rule each ────────────────────────────────────────────

def _title(value: Any) -> str:
    """The title a member reads. The command bar's ``_item`` applies the same rule."""
    return str(value or "").strip() or "(untitled)"


def _clip(value: Any) -> str:
    """The text on one line, at most :data:`SNIPPET_CHARS` characters."""
    flat = _SPACE.sub(" ", str(value or "")).strip()
    if len(flat) <= SNIPPET_CHARS:
        return flat
    return flat[: SNIPPET_CHARS - 1].rstrip() + "…"


def _when(value: Any) -> str | None:
    """A date or an instant as ISO text, or ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _as_dict(row: Any) -> dict[str, Any]:
    return row if isinstance(row, dict) else row.model_dump()


# ── tasks ───────────────────────────────────────────────────────────────────

async def _tasks(user: UserContext, q: str, k: int) -> list[Hit]:
    from gateway.routes.projects.search import search_tasks

    answer = await search_tasks(
        q=q, limit=k, exclude_relatives_of=None, include_triage=False, user=user,
    )
    return [
        Hit(
            app="tasks", kind="task", id=str(r["id"]), title=_title(r.get("title")),
            hint=f"Task · {r.get('project_name') or 'Projects'}",
            href=f"/projects?task={quote(str(r['id']))}",
            snippet=_clip(r.get("snippet")), when=_when(r.get("due_at")),
            stamp=_when(r.get("updated_at")),
        )
        for r in (answer.get("rows") or [])[:k]
    ]


# ── email ───────────────────────────────────────────────────────────────────

def email_meaning_on() -> bool:
    """True when ``EMAIL_SEMANTIC_SEARCH_ENABLED`` is on. Fails closed.

    It is the gate of Email's vector half, and ``email_embeddings.embed_query``
    reads the same setting. The route reads it before it starts the one embed
    of a turn (KR-ZERO), and the email source reads it before it takes the
    vector of that task.
    """
    try:
        from acb_common.settings import get_settings

        return getattr(get_settings(), "email_semantic_search_enabled", False) is True
    except Exception:
        return False


def _email_hit(m: Any, *, thread: bool = False) -> Hit:
    """One mail as a hit. *thread* (``open``, FIND-3) also sets
    :attr:`Hit.thread`. The search leaves it unset, so its hits are as before."""
    row = _as_dict(m)
    sender = row.get("from_address") or {}
    who = sender.get("name") or sender.get("email") or "someone"
    text = _MARK.sub("", str(row.get("highlight") or "")) or row.get("snippet")
    return Hit(
        app="email", kind="email", id=str(row["id"]),
        title=_title(row.get("subject") or "(no subject)"),
        hint=f"Email · from {who}", href=f"/email?email={quote(str(row['id']))}",
        snippet=_clip(text), when=_when(row.get("received_at")),
        stamp=_when(row.get("received_at")),
        thread=str(row.get("thread_id") or row["id"]) if thread else None,
    )


async def _email(
    user: UserContext, q: str, k: int, *, after: datetime | None = None,
    vectors: bool = False, budget_s: float | None = None, sender: str | None = None,
) -> list[Hit]:
    """The member's mail. *sender* (HRD-18b) keeps the mail from a sender
    whose name or address holds it, through the route's own ``from_addr``
    filter. *q* still filters and ranks that mail: the near step gives the
    other words of the query there (round 3, P2-1). An empty *q* gives the
    sender's newest mail, for a query of the one near word. A full-text word
    never matches inside an address, so "neuropeek" finds the mail from
    ``neuropeek.com`` only this way."""
    from gateway.routes.email.transport.search import search_messages

    started = time.monotonic()

    # ⚠️ Every parameter, by name. The route declares them with `Query(...)`
    # defaults, and a direct call that left one out would pass the `Query`
    # object itself. `test_shell_search.py` fails if the route gains one.
    # `hybrid` stays off: with it on, the route embeds the query, which is a
    # model call, and §20.4 says no model runs. `with_total` is off: a hit
    # list needs no count, and the count doubled the time (WS-KG KG-3a).
    answer = await search_messages(
        q=q, account_id=None, folder="all", label=None, labels=None,
        uncategorized=False, from_addr=sender or None, to_addr=None,
        received_after=after.isoformat() if after is not None else None,
        received_before=None, is_read=None,
        is_starred=None, has_attachments=None, sender_category=None,
        importance=None, hybrid=False, light=True, page=1, page_size=k,
        with_total=False, user=user,
    )
    lexical = [_email_hit(m) for m in (answer.get("emails") or [])[:k]]
    if not vectors:
        return lexical
    # The budget of the vector half is measured from the START of this source:
    # what the route left (or SOURCE_TIMEOUT_S), less the time the lexical half
    # took, less a margin. Too little left: the lexical answer, no embed call.
    from gateway.kb_index.query import QUERY_EMBED_TIMEOUT_S
    from gateway.retrieval.company import SOURCE_TIMEOUT_S

    total = SOURCE_TIMEOUT_S if budget_s is None else min(SOURCE_TIMEOUT_S, budget_s)
    left = total - (time.monotonic() - started) - VECTOR_MARGIN_S
    if left < VECTOR_FLOOR_S:
        return lexical
    vector_budget = min(QUERY_EMBED_TIMEOUT_S, left)
    # KG-3b: the vector half, fused with the lexical one by reciprocal rank
    # fusion. A failure of the vector half leaves the lexical answer.
    try:
        from gateway.kb_index.query import rrf_scores
        from gateway.retrieval import turn_vector
        from gateway.routes.email.transport.search import semantic_messages

        async def embed(words: str) -> list[float] | None:
            # KR-ZERO: the route started the one embed of this turn. The gate of
            # Email still binds, and the wait_for below bounds the wait.
            if turn_vector.started(user.organization_id, words):
                if not email_meaning_on():
                    return None
                return await turn_vector.wait(user.organization_id, words)
            kept = turn_vector.recall(user.organization_id, words)
            if kept is not None:
                return kept
            from email_ingestion.email_embeddings import embed_query

            vector = await embed_query(words, user.organization_id)
            turn_vector.remember(user.organization_id, words, vector)
            return vector

        # The lexical hits are held above. The vector half has its own budget,
        # below SOURCE_TIMEOUT_S, so a slow embed or read never drops them.
        found = await asyncio.wait_for(
            semantic_messages(
                user=user, words=q, k=k, embed=embed,
                received_after=after.isoformat() if after is not None else None,
            ),
            vector_budget,
        )
    except TimeoutError:
        logger.info("search.company email vectors timed out")
        return lexical
    except Exception as exc:
        logger.warning("search.company email vectors failed",
                       extra={"error": type(exc).__name__})
        return lexical
    semantic = [_email_hit(m) for m in found[:k]]
    if not semantic:
        return lexical
    scores, hits = rrf_scores((lexical, semantic), key=lambda h: h.id)
    order = sorted(hits, key=lambda ident: -scores[ident])  # stable: first seen wins a tie
    return [hits[ident] for ident in order][:k]


# ── people ──────────────────────────────────────────────────────────────────

async def _people(user: UserContext, q: str, k: int) -> list[Hit]:
    from gateway.routes.people.directory import list_directory

    answer = await list_directory(
        user=user, q=q, department=None, team=None, status=None, skill=None,
        has_capacity=False,
    )
    return [_person_hit(p) for p in (answer.rows or [])[:k]]


def _person_hit(p: dict[str, Any]) -> Hit:
    """One person of the directory as a hit. The search and ``open`` both
    build it here, so the two give one shape."""
    what = p.get("title") or p.get("role") or p.get("department") or "Colleague"
    # The bio only. The resume and the skills are HR data (§20.3).
    return Hit(
        app="people", kind="person", id=str(p["id"]),
        title=_title(p.get("preferred_name") or p.get("name")),
        hint=f"Person · {what}", href=f"/people/{quote(str(p['id']))}",
        snippet=_clip(p.get("bio")),
    )


# ── crm ─────────────────────────────────────────────────────────────────────

#: Each CRM kind → (its word in the hint, the fields that may follow it, the
#: list tab, the parameter that opens its record sheet). ``crm_platform.md``
#: §19.6.
CRM_KINDS: dict[str, tuple[str, tuple[str, ...], str, str]] = {
    "lead": ("Lead", ("organization_name", "email"), "leads", "lead"),
    "deal": ("Deal", ("organization_name",), "deals", "deal"),
    "contact": ("Contact", ("email",), "contacts", "contact"),
    "company": ("Company", ("industry",), "organizations", "organization"),
}


def crm_href(kind: str, record_id: Any) -> str:
    """The link that opens one CRM record: ``/crm?tab=<tab>&<param>=<id>``.

    ``kind`` is a key of ``CRM_KINDS`` (``lead``, ``deal``, ``contact`` or
    ``company``). The ONE builder of this link. The search sources and the
    My Day feed (WS-58 MD-3e, ``my_day_feed.md`` §5.9 rule 10.4) both call
    it, so the two cannot open a record in two ways."""
    _noun, _fields, tab, param = CRM_KINDS[kind]
    return f"/crm?tab={tab}&{param}={quote(str(record_id))}"


def crm_module_href(module: str, record_id: Any) -> str:
    """The link that opens one record of a mirrored module (FIND-11):
    ``/crm?tab=module&module=<Module>&mrec=<Module>:<id>``, the view state
    of ``app/crm/lib/urlState.ts``. The ONE builder of this link."""
    # Not an f-string: the fence of ``crm_href`` counts one f-string link.
    return ("/crm?tab=module&module=" + quote(str(module))
            + "&mrec=" + quote(str(module) + ":" + str(record_id)))


def crm_reports_href() -> str:
    """The link that opens the Reports tab of the CRM (WS-53 CRM-Q28 item
    14). The My Day card ``digest_ready`` calls it, so the CRM links of the
    gateway stay in this one module."""
    return "/crm?tab=reports"


def crm_settings_href(section: str) -> str:
    """The link that opens one section of CRM settings:
    ``/crm?tab=settings&section=<section>`` (WS-53 CRM-Q32-4). ``crm_href``
    links a record only. The My Day card ``crm_open_questions`` and the 422
    of the aggregate call it, so the CRM links of the gateway stay in this
    one module."""
    return "/crm?tab=settings&section=" + quote(str(section))


def _crm_title(kind: str, row: dict[str, Any]) -> str:
    if kind == "lead":
        return row.get("lead_name") or ""
    if kind == "contact":
        return " ".join(str(p) for p in (row.get("first_name"), row.get("last_name")) if p)
    return row.get("name") or ""


async def _crm(user: UserContext, q: str, k: int) -> list[Hit]:
    """The CRM's records (WS-53 CRM-X2, ``crm_platform.md`` §19.6).

    The CRM router has two gates more than its feature. This source applies
    them in the order of the router: the kill switch, then the access rule. A
    closed kill switch gives no hit and no error, as its 404 tells nothing on
    the router. The access rule's 403 is an ``HTTPException``, and the caller
    leaves the source out. ``search_records`` runs only after both, and it
    writes the SQL, not this source.
    """
    from gateway.routes.crm import access, flags, records

    if not flags.crm_org_allowed(user.organization_id):
        return []
    await access.require_crm_access(user)
    found = [_crm_hit(kind, row) for kind, row in await records.search_records(user, q, k)]
    if not _module_find_open(user):
        return found
    # FIND-11: the mirrored modules, through the module list's own predicate.
    # The module records share the page, so a full page of deals leaves room.
    modules = await _module("gateway.routes.crm.modules")
    mine = [_module_hit(row) for row in await modules.find_module_names(user, q, k)]
    room = min(len(mine), max(1, k // 2))
    return found[:max(0, k - room)] + mine[:room]


def _module_find_open(user: UserContext) -> bool:
    """True when the mirrored modules are a source of the find engine for
    *user*'s org (FIND-11): ``FIND_ORGS`` (``find.find_allowed``, the one
    reader) AND ``CRM_MODULE_FIND_ORGS`` (``flags.module_find_allowed``, which
    also needs the CRM switch). Either empty: no read of a module, and the
    answer of before, byte for byte. The org comes from the session."""
    from gateway.retrieval import find
    from gateway.routes.crm import flags

    return bool(find.find_allowed(user.organization_id)
                and flags.module_find_allowed(user.organization_id))


def _module_hit(row: dict[str, Any]) -> Hit:
    """One mirrored record as a hit: its name, the module's label and a link.
    No snippet, so the answer text stays short."""
    return Hit(
        app="crm", kind=MODULE_KIND, id=str(row["id"]), title=_title(row.get("name")),
        hint=_title(row.get("label")) or "Record", href=crm_module_href(row["module"], row["id"]),
        when=_when(row.get("modified")), stamp=_when(row.get("modified")),
    )


def _crm_hit(kind: str, row: dict[str, Any]) -> Hit:
    """One CRM record as a hit. The search and ``open`` both build it here."""
    noun, fields, _tab, _param = CRM_KINDS[kind]
    extra = next((str(row[f]).strip() for f in fields if str(row.get(f) or "").strip()), "")
    return Hit(
        app="crm", kind=kind, id=str(row["id"]), title=_title(_crm_title(kind, row)),
        hint=f"{noun} · {extra}" if extra else noun,
        href=crm_href(kind, row["id"]),
        snippet=_clip(extra), when=_when(row.get("last_activity_at")),
        stamp=_when(row.get("last_activity_at")),
    )


# ── artifacts ───────────────────────────────────────────────────────────────

async def _artifacts(user: UserContext, q: str, k: int) -> list[Hit]:
    """The member's artifacts, only when the registry is on (WS-60 AM-1).

    With the registry off the route answers 404, so this source gives no hit.
    The row holds no text until AM-7, so the snippet is empty.
    """
    from acb_memory import artifacts as registry

    from gateway.routes.artifacts import list_artifacts

    if not registry.enabled():
        return []
    # ⚠️ Every parameter, by name: the route declares `Query(...)` defaults.
    answer = await list_artifacts(
        q=q, kind=None, made_by=None, via=None, session_id=None, scope=None,
        status="active", sort="recent", limit=k, offset=0, _user=user,
    )
    out = []
    for row in (answer.get("rows") or [])[:k]:
        session = row.get("session_id")
        out.append(Hit(
            app="artifacts", kind="artifact", id=str(row["id"]),
            title=_title(row.get("title") or row.get("path")),
            hint=f"Artifact · {row.get('kind') or 'file'}",
            href=f"/artifacts?session={quote(str(session))}" if session else "/artifacts",
            when=_when(row.get("last_activity_at")),
            stamp=_when(row.get("last_activity_at")),
        ))
    return out


# ── whatsapp ────────────────────────────────────────────────────────────────

async def _whatsapp(
    user: UserContext, q: str, k: int, *, after: datetime | None = None,
) -> list[Hit]:
    from gateway.routes.whatsapp.transport.messages import search_messages

    # ⚠️ Every parameter, by name: `limit` and `hybrid` are `Query(...)`
    # defaults. `hybrid` stays off for the reason of the email source.
    rows = await search_messages(
        q=q, account_id=None, limit=k, hybrid=False, websearch=False,
        chat_id=None, contact=None, chat_kind=None, sent_after=after,
        sent_before=None, direction=None, has_media=None, user=user,
    )
    out = []
    for m in list(rows or [])[:k]:
        row = _as_dict(m)
        sender = row.get("sender_name") or ""
        # The app reads no record parameter yet, so the link opens the app.
        out.append(Hit(
            app="whatsapp", kind="whatsapp", id=str(row["id"]),
            title=_title(row.get("chat_name") or sender or "WhatsApp message"),
            hint=f"WhatsApp · from {sender}" if sender else "WhatsApp",
            href="/whatsapp",
            snippet=_clip(row.get("body_text") or row.get("transcript_text")),
            when=_when(row.get("sent_at")),
            stamp=_when(row.get("sent_at")),
        ))
    return out


# ── knowledge ───────────────────────────────────────────────────────────────

def _care_flag(row: dict[str, Any]) -> str | None:
    """``"expired"``, ``"unowned"`` or None for one page hit (KB-AI-4).

    Expired wins. A row with no ``owner_email`` key (an older answer) is never unowned. The value comes from a closed list here, never from page text.
    """
    due = row.get("review_at")
    if isinstance(due, str) and due:
        try:
            due = datetime.fromisoformat(due)
        except ValueError:
            due = None
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if due <= datetime.now(UTC):
            return "expired"
    if "owner_email" in row and not str(row.get("owner_email") or "").strip():
        return "unowned"
    return None


async def _knowledge(
    user: UserContext, q: str, k: int, *, hybrid: bool = False, reviewed: bool = False,
    verified_only: bool = False,
) -> list[Hit]:
    """The published Knowledge pages the member can see (WS-61 KR-3, §20.3).

    The Knowledge router answers 404 unless ``KNOWLEDGE_ENABLED`` is on and
    the org is on ``KNOWLEDGE_ORGS``. Called from here, the route function
    skips its router, so this source applies the same switch first. A closed
    switch gives no hit and no call. One hit for each page, its best chunk.

    *hybrid* comes from the consumer (KB-3c). ``GET /search/company`` asks
    for it when ``KNOWLEDGE_EMBED`` names the org: the route then embeds the
    words once, with its 0.8 s limit, and answers the lexical half when the
    door fails. The command bar never asks, so a keystroke costs no embed.

    *reviewed* comes from ``GET /search/company`` with ``CONTEXT_RECENCY``
    on (CX-1, ``company_context.md`` §4.2). The source then calls
    ``search_pages``, the route's inner function, with the review factor in
    place of the age decay. ``/knowledge/search`` and the command bar keep
    today's ``fuse``.
    """
    from gateway.routes.knowledge import flags
    from gateway.routes.knowledge import search as kb_search

    if not flags.knowledge_org_allowed(user.organization_id):
        return []
    if reviewed:
        answer = await kb_search.search_pages(
            q=q, space=None, k=k, hybrid=bool(hybrid), user=user, reviewed=True,
            verified_only=verified_only,
        )
    else:
        # ⚠️ Every parameter, by name: the route declares `Query(...)` defaults.
        answer = await kb_search.search_knowledge(
            q=q, space=None, k=k, hybrid=bool(hybrid), user=user,
            author=None, updated_after=None, title_only=False,
        )
    out: list[Hit] = []
    seen: set[str] = set()
    for row in answer.get("hits") or []:
        page_id = str(row["page_id"])
        if page_id in seen:
            continue
        seen.add(page_id)
        flag = _care_flag(row) if reviewed else None
        out.append(Hit(
            app="knowledge", kind="page", id=page_id, title=_title(row.get("title")),
            hint=f"Knowledge · {row.get('space_name') or 'Knowledge'}",
            href=f"/knowledge/pages/{quote(page_id)}",
            snippet=_clip(row.get("text")), when=_when(row.get("updated_at")),
            stamp=_when(row.get("updated_at")),
            flag=flag, review_at=_when(row.get("review_at")) if flag else None,
        ))
    return out[:k]


# ── studio ──────────────────────────────────────────────────────────────────

async def _studio(user: UserContext, q: str, k: int) -> list[Hit]:
    """The records of Studio apps (WS-STU C5a, ``ai_studio.md`` §14 C5).

    The source applies the two switches, ``STUDIO_ORGS`` and
    ``STUDIO_SEARCH_ORGS``, and then calls ``gateway.studio.search``. That
    module reads only the objects that a manifest names in ``shell.search``,
    through the one record reader, as the member. A hit has ``kind`` of
    ``studio`` and an ``id`` of ``<package id>/<object>/<record id>``. So a
    follow-up read goes through the action door of that package.
    """
    from gateway.studio import search

    if not _studio_open(user):
        return []
    out = []
    for f in await search.search_records(user, q, k):
        out.append(Hit(
            app="studio", kind="studio", id=f"{f.package_id}/{f.object_name}/{f.record_id}",
            title=_title(f.title),
            hint=f"{f.package_name} · {f.object_name}" if f.package_name else f.object_name,
            href=f"/apps/{quote(f.package_id, safe='')}",
            snippet=_clip(f.snippet), when=_when(f.updated_at), stamp=_when(f.updated_at),
        ))
    return out


# ── chat ────────────────────────────────────────────────────────────────────

async def _chat(user: UserContext, q: str, k: int, *, current: str | None = None) -> list[Hit]:
    """The member's own past chats (WS-FIND HRD-21, ``find_engine.md`` §3.1
    rung 6, §6 and D-FIND-5).

    The source applies its switch, ``CHAT_SEARCH_ORGS``, before any read, as
    the studio source does. Then ``gateway.chat_search`` reads the member's
    own solo chats with Mira on the web, as the member, with each rule in its
    WHERE, and drops each chat with the egress mark. *current* is the chat that the
    search runs in: it never answers, and with none the read gives nothing.

    A hit names the title, the date of the last change and the link that
    opens the chat (``feed._chat_href``, the chat app's ``open-chat`` link).
    Its snippet comes only from a row that the member typed, or it is empty.
    """
    if not _chat_open(user):
        return []
    search = await _module("gateway.chat_search")
    feed = await _module("gateway.routes.shell.feed")
    out = []
    for c in await search.search_own_chats(user, q, k, current=current):
        out.append(Hit(
            app="chat", kind="chat", id=c.session_id, title=_title(c.title),
            hint="Your chat", href=feed._chat_href(c.session_id),
            snippet=_clip(c.snippet), when=_when(c.last_activity),
            stamp=_when(c.last_activity),
        ))
    return out


# ── near (HRD-18b, ``find_engine.md`` §4.3 and §6) ──────────────────────────
# Each calls its app's own function, which reads the names of rows that the
# member can open and scores them with ``acb_common.name_match``.

async def _module(name: str) -> Any:
    """The app module *name*, read when the lookup runs, as each reader does.

    A first import runs in a thread, so the event loop and the time limit of
    the near lookup keep running. The gateway imports each router when it
    starts, so in production this is a read of ``sys.modules``.
    """
    import importlib
    import sys

    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    return await asyncio.to_thread(importlib.import_module, name)


async def _email_near(user: UserContext, words: list[str], k: int) -> dict[str, list[NearName]]:
    """The senders of the mailboxes that the member can read: each name and
    each address. The mailbox of another member never gives a name."""
    contacts = await _module("gateway.routes.email.transport.contacts")
    return await contacts.near_senders(user, words, k)


async def _people_near(user: UserContext, words: list[str], k: int) -> dict[str, list[NearName]]:
    """The names of the People directory. ``Source.near_names`` checks
    ``feature:people`` first."""
    directory = await _module("gateway.routes.people.directory")
    return await directory.near_names(user, words, k)


async def _crm_near(user: UserContext, words: list[str], k: int) -> dict[str, list[NearName]]:
    """The names of the CRM's records, after the CRM router's gates in their
    order, as :func:`_crm` applies them: the kill switch, then the access
    rule. Its 403 reaches the caller, which reads it as no name."""
    flags = await _module("gateway.routes.crm.flags")
    if not flags.crm_org_allowed(user.organization_id):
        return {}
    access = await _module("gateway.routes.crm.access")
    await access.require_crm_access(user)
    records = await _module("gateway.routes.crm.records")
    found = await records.near_names(user, words, k)
    if not _module_find_open(user):
        return found
    # FIND-11: the record names of the mirrored modules. One list for each
    # word: the nearest first, one entry for each spelling.
    modules = await _module("gateway.routes.crm.modules")
    more = await modules.near_module_names(user, words, k)
    merged: dict[str, list[NearName]] = {}
    for word in words:
        kept: list[NearName] = []
        for near in sorted([*found.get(word, []), *more.get(word, [])],
                           key=lambda n: (-n.score, n.spelling)):
            if all(near.spelling.lower() != n.spelling.lower() for n in kept):
                kept.append(near)
        merged[word] = kept[:k]
    return merged


# ── named (FIND-5, ``find_engine.md`` §4.2 ``names`` and §5.2) ──────────────
# Each calls the same app function as its ``find``, as the member. The route
# of ``resolve_name`` tests each name itself, so a record that the search
# matched by another field (an address, a company, a linked contact) is no row.

#: The kinds of CRM record that have a name of their own. A deal has a title.
CRM_NAME_KINDS: tuple[str, ...] = ("lead", "contact", "company")

#: An address that a sender row may carry: one ``@``, a dot after it, and no
#: space, quote or angle bracket. A sender writes the header, so any other
#: value gives no row.
_SENDER_ADDRESS = re.compile(r"[^\s@<>«»\"'`]{1,64}@[^\s@<>«»\"'`]{1,189}\.[^\s@<>«»\"'`.]{1,63}")
_ADDRESS_MAX = 254


async def _crm_named(user: UserContext, name: str, k: int) -> tuple[list[Named], bool]:
    """The CRM records of *name*, through :func:`_crm`: the kill switch, the
    access rule, then ``records.search_records``. Its 403 reaches the caller.
    ``search_records`` cuts its four kinds to *k* together, so a full page may
    hide a record."""
    hits = await _crm(user, name, k)
    return ([Named(h.kind, h.id, h.title, h.href, module=h.hint if h.kind == MODULE_KIND else "")
             for h in hits if h.kind in CRM_NAME_KINDS or h.kind == MODULE_KIND],
            len(hits) >= k)


async def _people_named(user: UserContext, name: str, k: int) -> tuple[list[Named], bool]:
    """The people of *name*, through the directory's own read. The row names
    the person as the directory does, and :attr:`Named.state` is the status.
    A person with a preferred name has two :attr:`Named.forms`: the full
    name, and the full name with its first word replaced by the preferred
    name ("Gitanjali Rao" and "Gita Rao"). The directory matches *name* as one
    phrase in the full name (never the preferred name), so ``resolve_name``
    asks it with one word (:attr:`Source.names_word`)."""
    from gateway.routes.people.directory import list_directory

    answer = await list_directory(
        user=user, q=name, department=None, team=None, status=None, skill=None,
        has_capacity=False,
    )
    rows = list(answer.rows or [])
    out = []
    for p in rows[:k]:
        full = " ".join(str(p.get("name") or "").split())
        preferred = " ".join(str(p.get("preferred_name") or "").split())
        out.append(Named("person", str(p["id"]), _title(preferred or full),
                         f"/people/{quote(str(p['id']))}",
                         forms=person_forms(full, preferred),
                         state=str(p.get("status") or "")))
    return out, len(rows) > k


def person_forms(full: str, preferred: str) -> tuple[str, ...]:
    """The forms of a person's name (FIND-5 round 3, P1): the full name FIRST,
    then the full name with its first word replaced by the preferred name.
    No preferred name, or one equal to the full name: no form, so the name
    itself is the one form. Pure.

    ⚠️ The second form guesses that the first word is the given name. When the
    full name starts with an initial, a title or a surname («K Sharath
    Kumar»), it holds two spellings of one given name. So the name test reads
    it only for an ask with no initial (``names.holds_name``, round 4)."""
    if not full or not preferred or preferred == full:
        return ()
    return (full, " ".join([preferred, *full.split()[1:]]))


def sender_address(value: Any) -> str:
    """*value* as the address of a sender row, in lower case, or ``""`` when
    it is no plain address (FIND-5). An address that holds a hidden character
    gives ``""`` too, because the answer prints the address outside the
    quotes: a character that ``hidden_text`` strips, and each character of
    Unicode category C. The second test also refuses a joiner (ZWJ, ZWNJ),
    which ``hidden_text`` keeps between two visible characters of a name."""
    import unicodedata

    from acb_skills.hidden_text import strip_hidden

    raw = str(value or "").strip()
    if strip_hidden(raw) != raw or any(unicodedata.category(ch).startswith("C") for ch in raw):
        return ""
    address = raw.lower()
    if len(address) > _ADDRESS_MAX or not _SENDER_ADDRESS.fullmatch(address):
        return ""
    return address


async def _email_named(user: UserContext, name: str, k: int) -> tuple[list[Named], bool]:
    """The senders whose name or address holds *name*, in the mailboxes that
    the member can read, through the Email app's own ``search.named_mail``:
    the newest mails first, with its own statement limit, and no outbound
    mail. One row for each sender, with the link of the newest mail. The
    member's own address gives no row. The match is one phrase, so
    ``resolve_name`` asks it with one word (:attr:`Source.names_word`)."""
    search = await _module("gateway.routes.email.transport.search")
    mails = list(await search.named_mail(user, name, k))[:k]
    own = str(user.email or "").strip().lower()
    out: list[Named] = []
    seen: set[str] = set()
    for mail in mails:
        address = sender_address(mail.get("email"))
        if not address or address == own or address in seen:
            continue
        seen.add(address)
        who = str(mail.get("name") or "").strip()
        local = address.partition("@")[0]
        # Two forms, each tested on its own: the display name and the part
        # of the address before the `@` (round 3, P1). Never the domain.
        out.append(Named("sender", address, who or address,
                         f"/email?email={quote(str(mail['id']))}",
                         forms=(who, local) if who else (local,)))
    return out, len(mails) >= k


# ── related and open (FIND-3, ``find_engine.md`` §4.3 and §6) ───────────────
# Each calls its app's own function, which reads ids and addresses as the
# member, in the bound tenant. Each ``open`` checks the app's gates itself
# first, as ``find`` does, so a ref that the member cannot open gives nothing:
# no hit, no count and no error text.

#: The most links of one edge that a related read gives for one ref, and the
#: most mails (one for each thread) of one key and one edge.
RELATED_PER_EDGE = 3


async def _crm_gates(user: UserContext) -> bool:
    """True when the CRM router would let *user* in: ``feature:crm``, the
    kill switch, then the access rule, in the order of the router. A refusal
    is ``False``, never an error that names the CRM."""
    if not holds(user, "crm"):
        return False
    flags = await _module("gateway.routes.crm.flags")
    if not flags.crm_org_allowed(user.organization_id):
        return False
    access = await _module("gateway.routes.crm.access")
    try:
        await access.require_crm_access(user)
    except HTTPException:
        return False
    return True


async def _crm_related(user: UserContext, refs: list[str]) -> list[tuple[str, str, str]]:
    """The links of CRM records, after the CRM gates: the contacts, the lead
    and the company of a deal, the deals of a company, the address of a lead
    or a contact, and the web domain of a company (``records.related_links``)."""
    ids = _ids_of(refs, CRM_REF_KINDS)
    if not ids or not await _crm_gates(user):
        return []
    records = await _module("gateway.routes.crm.records")
    return await records.related_links(user, ids, RELATED_PER_EDGE)


async def _crm_opened(user: UserContext, refs: list[str]) -> list[Hit]:
    """The CRM records of *refs*, after the CRM gates, read by the CRM's own
    list contract (``records.open_records``)."""
    ids = _ids_of(refs, CRM_REF_KINDS)
    if not ids or not await _crm_gates(user):
        return []
    records = await _module("gateway.routes.crm.records")
    return [_crm_hit(kind, row) for kind, row in await records.open_records(user, ids)]


async def _people_related(user: UserContext, refs: list[str]) -> list[tuple[str, str, str]]:
    """The work address of each person (``people.email``, a directory field),
    for a member who holds ``feature:people``."""
    ids = _ids_of(refs, ("person",)).get("person", [])
    if not ids or not holds(user, "people"):
        return []
    directory = await _module("gateway.routes.people.directory")
    return await directory.related_addresses(user, ids)


async def _people_opened(user: UserContext, refs: list[str]) -> list[Hit]:
    """The people of *refs*, as the directory shows them to the member."""
    ids = _ids_of(refs, ("person",)).get("person", [])
    if not ids or not holds(user, "people"):
        return []
    directory = await _module("gateway.routes.people.directory")
    return [_person_hit(p) for p in await directory.open_people(user, ids)]


async def _email_related(user: UserContext, refs: list[str]) -> list[tuple[str, str, str]]:
    """The mail from and to each address, and from each web domain, in the
    mailboxes that the member can read (``search.related_mail``). One mail for
    each thread. A free-mail domain gives no mail."""
    if not holds(user, "email"):
        return []
    addresses = [r.partition(":")[2] for r in refs if r.startswith("address:")]
    domains = [r.partition(":")[2] for r in refs if r.startswith("domain:")]
    if not addresses and not domains:
        return []
    search = await _module("gateway.routes.email.transport.search")
    return await search.related_mail(user, addresses, domains, RELATED_PER_EDGE)


async def _email_opened(user: UserContext, refs: list[str]) -> list[Hit]:
    """The mails of *refs* in the mailboxes that the member can read."""
    ids = _ids_of(refs, ("email",)).get("email", [])
    if not ids or not holds(user, "email"):
        return []
    search = await _module("gateway.routes.email.transport.search")
    return [_email_hit(m, thread=True) for m in await search.open_mail(user, ids)]


#: Every source, in the order the command bar and the route ask them.
REGISTRY: dict[str, Source] = {
    s.key: s
    for s in (
        Source("tasks", "projects", "Tasks", _tasks),
        Source("email", "email", "Email", _email, dated=True, vectors=True,
               near=_email_near, senders=True, related=_email_related,
               takes=("address", "domain"), open=_email_opened, kinds=("email",),
               names=("sender",), named=_email_named, names_word=True),
        Source("people", "people", "People", _people, near=_people_near,
               related=_people_related, takes=("person",), open=_people_opened,
               kinds=("person",), names=("person",), named=_people_named,
               names_word=True),
        Source("crm", "crm", "CRM", _crm, gate=_crm_open, near=_crm_near,
               related=_crm_related, takes=CRM_REF_KINDS, open=_crm_opened,
               kinds=CRM_REF_KINDS, names=CRM_NAME_KINDS, named=_crm_named,
               named_extra=(MODULE_KIND,)),
        Source("artifacts", "artifacts", "Artifacts", _artifacts, gate=_artifacts_open),
        Source("whatsapp", "whatsapp", "WhatsApp", _whatsapp, dated=True),
        Source("knowledge", "knowledge", "Knowledge", _knowledge, meaning=True,
               reviewed=True, gate=_knowledge_open),
        Source("studio", "studio", "Studio apps", _studio, gate=_studio_open),
        # HRD-21: last, and never in the command bar (BAR_APPS). The route asks
        # it only when its switch is open and the request names the current chat.
        Source("chat", "chat", "Past chats", _chat, gate=_chat_open, current=True),
    )
}
