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
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext

#: The most characters of matched text a hit carries (§20.3).
SNIPPET_CHARS = 400

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

    def as_dict(self, *, stamp: bool = False) -> dict[str, Any]:
        """The hit for a consumer. *stamp* adds ``stamp`` as the ninth key.

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
        return out


#: ``read(user, q, k)``. A source with ``meaning=True`` also takes
#: ``hybrid=`` by name, one with ``dated=True`` takes ``after=`` and one with
#: ``reviewed=True`` takes ``reviewed=``.
Reader = Callable[..., Awaitable[list[Hit]]]


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

    async def find(
        self, user: UserContext, q: str, k: int, *, hybrid: bool = False,
        after: datetime | None = None, reviewed: bool = False,
    ) -> list[Hit]:
        """At most *k* hits for *q*, as *user*. No feature, no call and no hit.

        *hybrid* asks a source with ``meaning`` for the vector half too. It
        costs one embed call, so it is ``False`` unless the consumer asks.
        *after* asks a source with ``dated`` for records stamped on or after
        it. *reviewed* asks a source with ``reviewed`` for the review factor
        of §4.2. A source ignores each option it does not declare, and the
        reader gets an option only when the consumer sets it.
        """
        if not holds(user, self.feature):
            return []
        options: dict[str, Any] = {}
        if self.meaning:
            options["hybrid"] = hybrid
        if self.dated and after is not None:
            options["after"] = after
        if self.reviewed and reviewed:
            options["reviewed"] = True
        return (await self.read(user, q, k, **options))[:k]


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

async def _email(
    user: UserContext, q: str, k: int, *, after: datetime | None = None,
) -> list[Hit]:
    from gateway.routes.email.transport.search import search_messages

    # ⚠️ Every parameter, by name. The route declares them with `Query(...)`
    # defaults, and a direct call that left one out would pass the `Query`
    # object itself. `test_shell_search.py` fails if the route gains one.
    # `hybrid` stays off: with it on, the route embeds the query, which is a
    # model call, and §20.4 says no model runs.
    answer = await search_messages(
        q=q, account_id=None, folder="all", label=None, labels=None,
        uncategorized=False, from_addr=None, to_addr=None,
        received_after=after.isoformat() if after is not None else None,
        received_before=None, is_read=None,
        is_starred=None, has_attachments=None, sender_category=None,
        importance=None, hybrid=False, light=True, page=1, page_size=k,
        user=user,
    )
    out = []
    for m in (answer.get("emails") or [])[:k]:
        row = _as_dict(m)
        sender = row.get("from_address") or {}
        who = sender.get("name") or sender.get("email") or "someone"
        text = _MARK.sub("", str(row.get("highlight") or "")) or row.get("snippet")
        out.append(Hit(
            app="email", kind="email", id=str(row["id"]),
            title=_title(row.get("subject") or "(no subject)"),
            hint=f"Email · from {who}", href=f"/email?email={quote(str(row['id']))}",
            snippet=_clip(text), when=_when(row.get("received_at")),
            stamp=_when(row.get("received_at")),
        ))
    return out


# ── people ──────────────────────────────────────────────────────────────────

async def _people(user: UserContext, q: str, k: int) -> list[Hit]:
    from gateway.routes.people.directory import list_directory

    answer = await list_directory(
        user=user, q=q, department=None, team=None, status=None, skill=None,
        has_capacity=False,
    )
    out = []
    for p in (answer.rows or [])[:k]:
        what = p.get("title") or p.get("role") or p.get("department") or "Colleague"
        # The bio only. The resume and the skills are HR data (§20.3).
        out.append(Hit(
            app="people", kind="person", id=str(p["id"]),
            title=_title(p.get("preferred_name") or p.get("name")),
            hint=f"Person · {what}", href=f"/people/{quote(str(p['id']))}",
            snippet=_clip(p.get("bio")),
        ))
    return out


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
    out = []
    for kind, row in await records.search_records(user, q, k):
        noun, fields, _tab, _param = CRM_KINDS[kind]
        extra = next((str(row[f]).strip() for f in fields if str(row.get(f) or "").strip()), "")
        out.append(Hit(
            app="crm", kind=kind, id=str(row["id"]), title=_title(_crm_title(kind, row)),
            hint=f"{noun} · {extra}" if extra else noun,
            href=crm_href(kind, row["id"]),
            snippet=_clip(extra), when=_when(row.get("last_activity_at")),
            stamp=_when(row.get("last_activity_at")),
        ))
    return out


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

async def _knowledge(
    user: UserContext, q: str, k: int, *, hybrid: bool = False, reviewed: bool = False,
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
        )
    else:
        # ⚠️ Every parameter, by name: the route declares `Query(...)` defaults.
        answer = await kb_search.search_knowledge(
            q=q, space=None, k=k, hybrid=bool(hybrid), user=user,
        )
    out: list[Hit] = []
    seen: set[str] = set()
    for row in answer.get("hits") or []:
        page_id = str(row["page_id"])
        if page_id in seen:
            continue
        seen.add(page_id)
        out.append(Hit(
            app="knowledge", kind="page", id=page_id, title=_title(row.get("title")),
            hint=f"Knowledge · {row.get('space_name') or 'Knowledge'}",
            href=f"/knowledge/pages/{quote(page_id)}",
            snippet=_clip(row.get("text")), when=_when(row.get("updated_at")),
            stamp=_when(row.get("updated_at")),
        ))
    return out[:k]


#: Every source, in the order the command bar and the route ask them.
REGISTRY: dict[str, Source] = {
    s.key: s
    for s in (
        Source("tasks", "projects", "Tasks", _tasks),
        Source("email", "email", "Email", _email, dated=True),
        Source("people", "people", "People", _people),
        Source("crm", "crm", "CRM", _crm),
        Source("artifacts", "artifacts", "Artifacts", _artifacts),
        Source("whatsapp", "whatsapp", "WhatsApp", _whatsapp, dated=True),
        Source("knowledge", "knowledge", "Knowledge", _knowledge, meaning=True,
               reviewed=True),
    )
}
