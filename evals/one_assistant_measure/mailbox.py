"""The fake email gateway of the measure: synthetic mail, no customer data.

It EXTENDS the ``Gateway`` of ``tests/unit/test_one_assistant_email.py``,
the fake of the five-turn email chat (H-412: one harness, not a second one).
That fake answers the five turns. This one adds a mailbox of synthetic mail,
so the email tools can list, search, read, read a thread, read a file and
reply. A route that this class does not answer goes to the five-turn fake.

The gateway is the only fake. The email tools, the orchestrator, the
delegation and the cards are the real code. A test fake must answer each
route that a tool reaches, because a live run with real specialists refuses
every host send that is not the Router (``RoutingHarness.allow``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tests.unit.test_one_assistant_email import (
    BOXES,
    DRAFT,
    HOME,
    KIRAN,
    MAIL,
    SAGAR,
    WORK,
    Gateway,
)

__all__ = [
    "BOXES",
    "DRAFT",
    "HOME",
    "KIRAN",
    "MAIL",
    "NO_TEXT_REASON",
    "SAGAR",
    "WORK",
    "Attachment",
    "Mail",
    "Mailbox",
    "draft_id_for",
    "uid",
]

#: The words of the gateway's attachment route for a file with no text
#: (``routes/email/transport/attachments.py``, ``_NO_TEXT_REASON``).
NO_TEXT_REASON = (
    "This file holds no text that I can read. A scanned page is an image, "
    "and I cannot read the text of an image."
)

#: The member's own address, in the Work mailbox.
ME = "vj@hathilabs.test"
_WORK_LABEL = "Work · vj@hathilabs.test"
_HOME_LABEL = "Personal · vjvarada@gmail.test"
_MISS = object()

#: Words that a search drops, as a full-text search does.
_STOP = frozenset({
    "the", "and", "for", "from", "with", "about", "this", "that", "mail", "mails",
    "email", "emails", "any", "all", "what", "did", "does", "send", "sent", "anything",
    "inbox", "my", "me", "in", "of", "to", "a", "an", "on", "or", "is",
})


def uid(n: int) -> str:
    """A fixed UUID for fixture *n*. The tools refuse an id that is not a UUID."""
    return f"7a5e0000-0000-4000-8000-{n:012d}"


def draft_id_for(message_id: str) -> str:
    """The id of the draft that a reply card to *message_id* saves.

    The five-turn mail keeps the five-turn draft, ``DRAFT``."""
    return DRAFT if message_id == MAIL or not message_id.startswith("7a5e") \
        else f"d-{message_id[-6:]}"


@dataclass(frozen=True)
class Attachment:
    """A file of a mail. ``text`` None is a scanned file: it holds no text."""

    id: str
    filename: str
    text: str | None = None
    mime_type: str = "application/pdf"


@dataclass(frozen=True)
class Mail:
    """One synthetic mail. ``days_ago`` places it before the run."""

    id: str
    sender: str
    address: str
    subject: str
    body: str
    days_ago: float
    account_id: str = WORK
    is_read: bool = False
    needs_reply: bool = False
    folder: str = "inbox"
    thread_id: str = ""
    attachments: tuple[Attachment, ...] = ()
    to: tuple[str, ...] = (ME,)

    def received_at(self, now: datetime) -> str:
        return (now - timedelta(days=self.days_ago)).isoformat()

    def first_name(self) -> str:
        return self.sender.split()[0] if self.sender else "there"


def _terms(query: str) -> list[list[str]]:
    """The alternatives of a query (``A OR B``), each a list of words."""
    out: list[list[str]] = []
    for part in re.split(r"\s+OR\s+", query or "", flags=re.IGNORECASE):
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9.@\-]*", part.lower())
                 if len(w) > 1 and w not in _STOP]
        if words:
            out.append(words)
    return out


def _has(word: str, text: str) -> bool:
    """*word* in *text*, with a plural "s" taken off."""
    return word in text or (word.endswith("s") and word[:-1] in text)


class Mailbox(Gateway):
    """The five-turn fake, with a mailbox of synthetic mail."""

    def __init__(self, mails: tuple[Mail, ...] | list[Mail] = ()) -> None:
        super().__init__()
        self.mails = tuple(mails)
        #: Each reply card that the gateway prepared, by message id.
        self.preps: dict[str, dict[str, Any]] = {}
        #: Each draft that a reply card saved, by draft id. The five-turn
        #: draft (``DRAFT``) stays with the five-turn fake.
        self.drafts: dict[str, dict[str, Any]] = {}
        #: The draft of the held card that shows now, and each draft sent.
        self.held_for = ""
        self.sent_drafts: list[str] = []
        self.now = datetime.now(UTC)
        self.by_id = {m.id: m for m in self.mails}
        self.by_attachment = {a.id: (m, a) for m in self.mails for a in m.attachments}

    # ── rows ─────────────────────────────────────────────────────────────────

    def _row(self, m: Mail) -> dict[str, Any]:
        return {
            "id": m.id, "account_id": m.account_id, "subject": m.subject,
            "from_address": {"name": m.sender, "email": m.address},
            "received_at": m.received_at(self.now), "is_read": m.is_read,
            "is_starred": False, "has_attachments": bool(m.attachments),
            "snippet": " ".join(m.body.split())[:80], "thread_id": m.thread_id,
            "folder": m.folder,
        }

    def _full(self, m: Mail) -> dict[str, Any]:
        return {
            **self._row(m),
            "to_addresses": [{"email": a} for a in m.to], "cc_addresses": [],
            "body_text": m.body, "body_trimmed": False,
            "attachments": [{"id": a.id, "filename": a.filename, "mime_type": a.mime_type}
                            for a in m.attachments],
        }

    def _matches(self, m: Mail, p: dict[str, Any]) -> bool:
        if p.get("thread_id"):
            return m.thread_id == p["thread_id"] and (
                not p.get("account_id") or m.account_id == p["account_id"])
        if m.folder != str(p.get("folder") or "inbox"):
            return False
        if p.get("account_id") and m.account_id != p["account_id"]:
            return False
        if str(p.get("is_read") or "") == "false" and m.is_read:
            return False
        if str(p.get("has_attachments") or "") == "true" and not m.attachments:
            return False
        if p.get("from_email") and str(p["from_email"]).lower() not in m.address.lower():
            return False
        if p.get("received_after"):
            try:
                after = datetime.fromisoformat(str(p["received_after"]))
            except ValueError:
                after = None
            if after is not None and self.now - timedelta(days=m.days_ago) < after:
                return False
        query = _terms(str(p.get("query") or ""))
        if query:
            text = f"{m.subject} {m.body} {m.sender} {m.address}".lower()
            return any(all(_has(w, text) for w in alt) for alt in query)
        return True

    def _list(self, p: dict[str, Any]) -> dict[str, Any]:
        found = [m for m in self.mails if self._matches(m, p)]
        oldest_first = bool(p.get("thread_id")) or p.get("sort") == "oldest"
        found.sort(key=lambda m: m.days_ago, reverse=oldest_first)
        size = int(p.get("page_size") or 25)
        page = int(p.get("page") or 1)
        rows = [self._row(m) for m in found[(page - 1) * size: page * size]]
        return {"total": len(found), "emails": rows}

    # ── routes ───────────────────────────────────────────────────────────────

    def _answer_get(self, path: str, p: dict[str, Any]) -> Any:
        if path == "/email/messages":
            return self._list(p)
        if path == "/email/reply-zero":
            threads = [{"message_id": m.id, "subject": m.subject,
                        "from": f"{m.sender} <{m.address}>"}
                       for m in self.mails if m.needs_reply
                       and (not p.get("account_id") or m.account_id == p["account_id"])]
            return {"threads": threads}
        if path == "/email/contacts/suggest":
            q = str(p.get("q") or "").lower()
            people = [("Kiran", KIRAN, "org"), ("Dr Sagar", SAGAR, "sent")]
            people += [(m.sender, m.address, "sent") for m in self.mails if m.folder == "inbox"]
            seen: set[str] = set()
            out = []
            for name, addr, src in people:
                if addr in seen or not q or q not in f"{name} {addr}".lower():
                    continue
                seen.add(addr)
                out.append({"name": name, "email": addr, "source": src})
            return out
        if path == "/email/drafts/seal":
            saved = self.drafts.get(str(p.get("draft_id") or ""))
            if saved is not None:
                return {"subject": saved["subject"], "text_digest": f"digest-{saved['id']}",
                        "body_text": saved["body"]}
            return {"subject": "Re: Quote for the clinic", "text_digest": "digest-1",
                    "body_text": "Dear Dr Sagar,\n\nThank you. We accept.\n\nVJ"}
        if path == "/email/recent-reply":
            return {}
        m = re.fullmatch(r"/email/messages/([^/]+)(/full-body)?", path)
        if m and m.group(1) in self.drafts:
            saved = self.drafts[m.group(1)]
            if m.group(2):
                return {"subject": saved["subject"], "from": ME, "body_text": saved["body"]}
            return {"id": saved["id"], "account_id": saved["account_id"], "folder": "drafts",
                    "subject": saved["subject"], "body_text": saved["body"],
                    "to_addresses": [{"email": a} for a in saved["to"]],
                    "cc_addresses": [{"email": a} for a in saved["cc"]]}
        if m and m.group(1) in self.by_id:
            mail = self.by_id[m.group(1)]
            if m.group(2):
                return {"subject": mail.subject, "from": f"{mail.sender} <{mail.address}>",
                        "body_text": mail.body}
            return self._full(mail)
        return _MISS

    def _attachment_text(self, aid: str) -> dict[str, Any]:
        mail, att = self.by_attachment[aid]
        del mail
        base = {"filename": att.filename, "mime_type": att.mime_type}
        if att.text is None:
            return {**base, "kind": "no_text", "reason": NO_TEXT_REASON}
        return {**base, "kind": "pdf", "text": att.text, "chars": len(att.text)}

    def _prep(self, body: dict[str, Any]) -> dict[str, Any]:
        mid = str(body.get("message_id") or "")
        mail = self.by_id.get(mid)
        name = mail.sender if mail else "Dr Sagar"
        addr = mail.address if mail else SAGAR
        subject = f"Re: {mail.subject}" if mail else "Re: Quote for the clinic"
        if str(body.get("draft") or "").strip():
            text = str(body["draft"])
        elif str(body.get("instruction") or "").strip():
            text = f"Dear {name},\n\n{body['instruction']}\n\nVJ"
        else:
            text = f"Dear {name},\n\nThank you for your mail. We accept.\n\nVJ"
        cc = [str(a) for a in body.get("cc") or []]
        prep = {
            "message_id": mid, "subject": subject, "body": text,
            "mailbox": _HOME_LABEL if mail and mail.account_id == HOME else _WORK_LABEL,
            "draft_id": draft_id_for(mid),
            "recipients": {"reply": {"to": [addr], "cc": cc},
                           "reply_all": {"to": [addr], "cc": cc}},
            "classes": {addr: "known", **dict.fromkeys(cc, "org")},
        }
        self.preps[mid] = prep
        return prep

    def _act(self, body: dict[str, Any]) -> dict[str, Any]:
        mid = str(body.get("message_id") or "")
        prep = self.preps.get(mid) or self._prep({"message_id": mid})
        raw = str(body.get("answer") or "")
        try:
            choice = json.loads(raw.split(" ", 1)[1]).get("reply") or {}
        except (IndexError, ValueError, AttributeError):
            choice = {}
        to = prep["recipients"]["reply"]["to"]
        cc = list(choice.get("cc") or [])
        action = choice.get("action") or "save"
        if action == "save" and prep["draft_id"] != DRAFT:
            mail = self.by_id.get(mid)
            self.drafts[prep["draft_id"]] = {
                "id": prep["draft_id"], "message_id": mid, "subject": prep["subject"],
                "body": str(choice.get("body") or prep["body"]), "to": list(to), "cc": cc,
                "account_id": mail.account_id if mail else WORK,
            }
        return {"action": action, "draft_id": prep["draft_id"], "to": to, "cc": cc,
                "subject": prep["subject"], "mailbox": prep["mailbox"]}

    def _held(self, body: dict[str, Any]) -> dict[str, Any]:
        """The held card of a draft that a reply card saved."""
        saved = self.drafts[str(body.get("draft_id"))]
        self.held_for = saved["id"]
        people = [*saved["to"], *saved["cc"]]
        return {"title": "Send this draft?", "detail": f"To {', '.join(saved['to'])}",
                "context": saved["body"], "card_id": f"c0ffee00{saved['id'][-6:]:0>24}",
                "rows": [{"id": f"r-{i}", "label": a, "checked": True}
                         for i, a in enumerate(people)]}

    # ── the verbs that the email agent's module calls ────────────────────────

    async def get(self, path: str, params: dict | None = None) -> Any:
        found = self._answer_get(path, dict(params or {}))
        if found is _MISS:
            return await super().get(path, params)
        self.calls.append(("GET", path, params))
        return found

    async def post(self, path: str, body: Any) -> Any:
        if path == "/email/reply-card":
            self.calls.append(("POST", path, body))
            return self._prep(dict(body or {}))
        if path == "/email/reply-card/act":
            self.calls.append(("POST", path, body))
            return self._act(dict(body or {}))
        draft = str((body or {}).get("draft_id") or "")
        if path == "/email/held-card" and draft in self.drafts:
            self.calls.append(("POST", path, body))
            return self._held(dict(body))
        if path == "/email/held-card/act" and self.held_for in self.drafts:
            self.calls.append(("POST", path, body))
            saved = self.drafts[self.held_for]
            self.sent_drafts.append(saved["id"])
            return {"to": saved["to"], "cc": saved["cc"]}
        if path == "/email/drafts/send" and draft in self.drafts:
            self.calls.append(("POST", path, body))
            self.sent_drafts.append(draft)
            return {}
        return await super().post(path, body)

    async def delete(self, path: str) -> Any:
        self.calls.append(("DELETE", path, None))
        return {}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The module's ``_request``: the one tool that calls it is the file read."""
        hit = re.fullmatch(r"/email/attachments/([^/]+)/text", path)
        if method == "GET" and hit and hit.group(1) in self.by_attachment:
            self.calls.append(("GET", path, kwargs.get("params")))
            return httpx.Response(200, json=self._attachment_text(hit.group(1)))
        if method == "GET":
            data = await self.get(path, kwargs.get("params"))
        elif method == "POST":
            data = await self.post(path, kwargs.get("json"))
        elif method == "PUT":
            data = await self.put(path, kwargs.get("json"))
        elif method == "PATCH":
            data = await self.patch(path, kwargs.get("json"))
        else:
            data = await self.delete(path)
        return httpx.Response(200, json=data)

    def writes(self, path: str) -> list[Any]:
        """The bodies of each POST to *path*."""
        return self.posts(path)


def install(mp: Any, module: Any, gateway: Mailbox) -> None:
    """Point the email agent's gateway verbs at *gateway*, through *mp*."""
    mp.setattr(module, "_get", gateway.get)
    mp.setattr(module, "_post", gateway.post)
    mp.setattr(module, "_patch", gateway.patch)
    mp.setattr(module, "_put", gateway.put)
    mp.setattr(module, "_delete", gateway.delete)
    mp.setattr(module, "_request", gateway.request)
