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

**Two more options of ``find``** (CX-1, §4.2 and §4.5), each for a source
that declares it, like ``meaning``. A source with ``dated=True`` takes
``after`` and filters in its app's own SQL. A source with ``reviewed=True``
takes ``reviewed`` and ranks a page past its review date lower. Only
``GET /search/company`` passes them, and only with ``CONTEXT_RECENCY`` on.

**The read by id** (WS-KG KG-6, ``knowledge_graph.md`` §6). Each source
also has ``open(user, ids, kind=...)``. It checks the feature and the app's
switch first, as ``find`` does, and then calls the app's OWN read by id
with this request's member. It answers only the records that the member can
open, as hits, with no snippet. A hidden record is left out with no trace.
``kinds`` names the node kinds of the graph that the source opens. A kind
that no source opens (``entity``, ``meeting``) never shows.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext

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

    def as_dict(self, *, stamp: bool = False) -> dict[str, Any]:
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
        return out


#: ``read(user, q, k)``. A source with ``meaning=True`` also takes
#: ``hybrid=`` by name, one with ``dated=True`` takes ``after=`` and one with
#: ``reviewed=True`` takes ``reviewed=``.
Reader = Callable[..., Awaitable[list[Hit]]]

#: ``open(user, kind, ids)``: the records of *ids* that the member can open,
#: as hits. It calls the app's own read by id (KG-6).
Opener = Callable[[UserContext, str, list[str]], Awaitable[list[Hit]]]

#: The most ids that one call of ``open`` takes.
MAX_OPEN = 50


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
    #: The node kinds of the graph that this source opens (KG-6, §3.1).
    kinds: tuple[str, ...] = ()
    #: The app's read by id (KG-6, §6). ``None`` opens nothing.
    opener: Opener | None = None

    def reaches(self, user: UserContext) -> bool:
        """True when *user* holds the feature and the app's switch is open."""
        return holds(user, self.feature) and (self.gate is None or self.gate(user))

    async def find(
        self, user: UserContext, q: str, k: int, *, hybrid: bool = False,
        after: datetime | None = None, reviewed: bool = False,
        verified_only: bool = False, vectors: bool = False,
        budget_s: float | None = None,
    ) -> list[Hit]:
        """At most *k* hits for *q*, as *user*. No feature, no call and no hit.

        *hybrid* asks a source with ``meaning`` for the vector half too. It
        costs one embed call, so it is ``False`` unless the consumer asks.
        *after* asks a source with ``dated`` for records stamped on or after
        it. *reviewed* asks a source with ``reviewed`` for the review factor
        of §4.2. *verified_only* (KB-AI-4) asks such a source for pages with an
        owner and a review date in the future. A source ignores each option it does not declare, and the
        reader gets an option only when the consumer sets it.
        """
        if not holds(user, self.feature):
            return []
        options: dict[str, Any] = {}
        if self.meaning:
            options["hybrid"] = hybrid
        if self.dated and after is not None:
            options["after"] = after
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
        return (await self.read(user, q, k, **options))[:k]

    async def open(
        self, user: UserContext, ids: list[str], *, kind: str | None = None,
    ) -> list[Hit]:
        """The records of *ids* that *user* can open, as hits (KG-6, §6).

        No feature, or a closed switch: no call and no hit. *kind* is one of
        :attr:`kinds`, and the first one by default. A kind the source does
        not open gives nothing. The app's read applies the app's own rule,
        and this method keeps only hits of that kind among *ids*, so a
        reader can never widen the answer.
        """
        if self.opener is None or not self.reaches(user):
            return []
        want = kind or (self.kinds[0] if self.kinds else "")
        if want not in self.kinds:
            return []
        asked = list(dict.fromkeys(str(i) for i in ids or [] if str(i or "")))[:MAX_OPEN]
        if not asked:
            return []
        wanted = set(asked)
        hits = await self.opener(user, want, asked)
        return [h for h in hits if h.kind == want and h.id in wanted]


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

def _task_hit(r: dict[str, Any]) -> Hit:
    return Hit(
        app="tasks", kind="task", id=str(r["id"]), title=_title(r.get("title")),
        hint=f"Task · {r.get('project_name') or 'Projects'}",
        href=f"/projects?task={quote(str(r['id']))}",
        snippet=_clip(r.get("snippet")), when=_when(r.get("due_at")),
        stamp=_when(r.get("updated_at")),
    )


async def _tasks(user: UserContext, q: str, k: int) -> list[Hit]:
    from gateway.routes.projects.search import search_tasks

    answer = await search_tasks(
        q=q, limit=k, exclude_relatives_of=None, include_triage=False, user=user,
    )
    return [_task_hit(r) for r in (answer.get("rows") or [])[:k]]


async def _open_tasks(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """``resolve_visibility`` and the search's own clauses (KG-6, §6)."""
    from gateway.routes.projects.search import open_tasks

    return [_task_hit(r) for r in await open_tasks(user, ids)]


# ── email ───────────────────────────────────────────────────────────────────

def _email_hit(m: Any) -> Hit:
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
    )


async def _email(
    user: UserContext, q: str, k: int, *, after: datetime | None = None,
    vectors: bool = False, budget_s: float | None = None,
) -> list[Hit]:
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
        uncategorized=False, from_addr=None, to_addr=None,
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


async def _open_email(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """The owner scope of a read by mail id (D-EM-30), through the app's
    own ``message_summaries``. Another member's mail is left out (KG-6)."""
    import uuid

    from gateway.routes.email.transport.messages import (
        MessageSummariesRequest,
        message_summaries,
    )

    wanted: list[str] = []
    for raw in ids:
        try:
            wanted.append(str(uuid.UUID(str(raw))))
        except (ValueError, TypeError):
            continue
    if not wanted:
        return []
    answer = await message_summaries(req=MessageSummariesRequest(ids=wanted), user=user)
    out = []
    for row in answer.get("summaries") or []:
        who = row.get("from") or "someone"
        out.append(Hit(
            app="email", kind="email", id=str(row["id"]),
            title=_title(row.get("subject") or "(no subject)"),
            hint=f"Email · from {who}", href=f"/email?email={quote(str(row['id']))}",
            when=_when(row.get("received_at")), stamp=_when(row.get("received_at")),
        ))
    return out


# ── people ──────────────────────────────────────────────────────────────────

async def _people(user: UserContext, q: str, k: int) -> list[Hit]:
    from gateway.routes.people.directory import list_directory

    answer = await list_directory(
        user=user, q=q, department=None, team=None, status=None, skill=None,
        has_capacity=False,
    )
    return [_person_hit(p) for p in (answer.rows or [])[:k]]


def _person_hit(p: dict[str, Any], *, bio: bool = True) -> Hit:
    what = p.get("title") or p.get("role") or p.get("department") or "Colleague"
    # The bio only. The resume and the skills are HR data (§20.3).
    return Hit(
        app="people", kind="person", id=str(p["id"]),
        title=_title(p.get("preferred_name") or p.get("name")),
        hint=f"Person · {what}", href=f"/people/{quote(str(p['id']))}",
        snippet=_clip(p.get("bio")) if bio else "",
    )


async def _open_people(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """Active people only, with the directory's projection (KG-6, §6)."""
    from gateway.routes.people.directory import open_people

    return [_person_hit(p, bio=False) for p in await open_people(user, ids)]


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
    return [_crm_hit(kind, row) for kind, row in await records.search_records(user, q, k)]


def _crm_hit(kind: str, row: dict[str, Any], *, snippet: bool = True) -> Hit:
    noun, fields, _tab, _param = CRM_KINDS[kind]
    extra = next((str(row[f]).strip() for f in fields if str(row.get(f) or "").strip()), "")
    return Hit(
        app="crm", kind=kind, id=str(row["id"]), title=_title(_crm_title(kind, row)),
        hint=f"{noun} · {extra}" if extra else noun,
        href=crm_href(kind, row["id"]),
        snippet=_clip(extra) if snippet else "", when=_when(row.get("last_activity_at")),
        stamp=_when(row.get("last_activity_at")),
    )


async def _open_crm(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """The kill switch, then ``require_crm_access``, then the app's read by
    id (KG-6, §6). The access rule's 403 reaches the caller, which drops the
    kind. ``Source.open`` checked the switch, and this checks it again, as
    :func:`_crm` does."""
    from gateway.routes.crm import access, flags, records

    if kind not in CRM_KINDS or not flags.crm_org_allowed(user.organization_id):
        return []
    await access.require_crm_access(user)
    return [_crm_hit(kind, row, snippet=False)
            for row in await records.open_records(user, kind, ids)]


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
    return [_artifact_hit(row) for row in (answer.get("rows") or [])[:k]]


def _artifact_hit(row: dict[str, Any]) -> Hit:
    session = row.get("session_id")
    return Hit(
        app="artifacts", kind="artifact", id=str(row["id"]),
        title=_title(row.get("title") or row.get("path")),
        hint=f"Artifact · {row.get('kind') or 'file'}",
        href=f"/artifacts?session={quote(str(session))}" if session else "/artifacts",
        when=_when(row.get("last_activity_at")),
        stamp=_when(row.get("last_activity_at")),
    )


async def _open_artifacts(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """The registry switch and its owner rule, ``can_read`` (KG-6, §6)."""
    from acb_memory import artifacts as registry

    if not registry.enabled():
        return []
    return [_artifact_hit(row) for row in await registry.open_artifacts(ids, user.email or "")]


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
    return [_whatsapp_hit(_as_dict(m)) for m in list(rows or [])[:k]]


def _whatsapp_hit(row: dict[str, Any], *, snippet: bool = True) -> Hit:
    sender = row.get("sender_name") or ""
    # The app reads no record parameter yet, so the link opens the app.
    return Hit(
        app="whatsapp", kind="whatsapp", id=str(row["id"]),
        title=_title(row.get("chat_name") or sender or "WhatsApp message"),
        hint=f"WhatsApp · from {sender}" if sender else "WhatsApp",
        href="/whatsapp",
        snippet=_clip(row.get("body_text") or row.get("transcript_text")) if snippet else "",
        when=_when(row.get("sent_at")),
        stamp=_when(row.get("sent_at")),
    )


async def _open_whatsapp(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """The chat access of the app: the member's own accounts (KG-6, §6)."""
    from gateway.routes.whatsapp.transport.messages import open_messages

    return [_whatsapp_hit(_as_dict(m), snippet=False) for m in await open_messages(user, ids)]


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


async def _open_knowledge(user: UserContext, kind: str, ids: list[str]) -> list[Hit]:
    """The Knowledge kill switch, then ``visible_pages_clause`` (KG-6, §6)."""
    from gateway.routes.knowledge import flags
    from gateway.routes.knowledge import search as kb_search

    if not flags.knowledge_org_allowed(user.organization_id):
        return []
    return [
        Hit(
            app="knowledge", kind="page", id=str(row["page_id"]),
            title=_title(row.get("title")),
            hint=f"Knowledge · {row.get('space_name') or 'Knowledge'}",
            href=f"/knowledge/pages/{quote(str(row['page_id']))}",
            when=_when(row.get("updated_at")), stamp=_when(row.get("updated_at")),
        )
        for row in await kb_search.open_pages(user, ids)
    ]


#: Every source, in the order the command bar and the route ask them.
REGISTRY: dict[str, Source] = {
    s.key: s
    for s in (
        Source("tasks", "projects", "Tasks", _tasks, kinds=("task",), opener=_open_tasks),
        Source("email", "email", "Email", _email, dated=True, vectors=True,
               kinds=("email",), opener=_open_email),
        Source("people", "people", "People", _people, kinds=("person",),
               opener=_open_people),
        Source("crm", "crm", "CRM", _crm, gate=_crm_open,
               kinds=("lead", "deal", "contact", "company"), opener=_open_crm),
        Source("artifacts", "artifacts", "Artifacts", _artifacts, gate=_artifacts_open,
               kinds=("artifact",), opener=_open_artifacts),
        Source("whatsapp", "whatsapp", "WhatsApp", _whatsapp, dated=True,
               kinds=("whatsapp",), opener=_open_whatsapp),
        Source("knowledge", "knowledge", "Knowledge", _knowledge, meaning=True,
               reviewed=True, gate=_knowledge_open, kinds=("page",),
               opener=_open_knowledge),
    )
}


def source_of_kind(kind: str) -> Source | None:
    """The source that opens the node kind *kind*, or ``None`` (KG-6)."""
    return next((s for s in REGISTRY.values() if kind in s.kinds), None)
