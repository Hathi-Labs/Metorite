"""The stubs of the WhatsApp narrowing eval (WS-48 N4): the gateway and the door.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N4.

* :class:`ChatStub` serves the three WhatsApp routes that the two paths reach,
  over HTTP on ``127.0.0.1``: ``GET /whatsapp/search`` (NARROW and
  ``search_whatsapp``), ``GET /whatsapp/chats`` (``list_whatsapp_chats``) and
  ``GET /whatsapp/chats/{id}/messages`` (``read_whatsapp_chat``, and READ with
  ``around``). The REAL agent tools call it through the agent's own
  ``_request``. It records each request with its method, its parameters and
  its acting member.
* :class:`StubDoor` is the HTTP transport under the REAL Console client of
  ``acb_llm.decide``. It answers each PICK question from ``Dataset.verdict``.
* The Router's counters and the eval card are the email eval's own
  (``evals/email_narrowing/stub_api.py``), so both evals price one way.

The rules of the real gateway hold here too:

* The caller is the ``X-User-Email`` of the request. A member sees only the
  messages of their own accounts, and a chat of another member is a 404.
* An unknown query parameter is ignored, as FastAPI ignores it. The stub
  records its name, and the runner fails the run on it, because an ignored
  filter widens a search in silence.
* Every route is a read. A request with any other method is recorded and
  refused, and the runner fails the run on it: READ must change no state.
* ``GET /chats/{id}/messages`` with no ``around`` gives the NEWEST ``limit``
  messages, oldest first, as the route does since H-277.

⚠️ **The full-text match is an approximation** of ``to_tsvector('simple')``:
lower-case whole words, no stems, no stop words. The ``websearch`` grammar is
``OR`` groups, a ``-`` negates, and quotes are dropped. It is not Postgres.
``test_whatsapp_read_no_mark.py`` runs the real route on a real database.

⚠️ **The order is ``sent_at`` newest first.** The real route ranks by the
embedding when the semantic search is on. Recall is the same either way.
"""
from __future__ import annotations

import inspect
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from evals.email_narrowing.stub_api import (
    DECIDE_TIER,
    StubRouter,
    credits_of,
    estimate_tokens,
    load_card,
)
from evals.whatsapp_narrowing.dataset import Dataset, message_text

MEDIA_KINDS = frozenset({"image", "video", "audio", "voice", "document", "sticker"})

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    """``to_tsvector('simple')``, roughly: lower-case whole words."""
    return [w.lower() for w in _WORD.findall(text or "")]


def _document(m: dict[str, Any]) -> set[str]:
    """The route's FTS vector: the body, the transcript and the sender name."""
    return set(tokens(" ".join([
        str(m.get("body_text") or ""), str(m.get("transcript_text") or ""),
        str(m.get("sender_name") or ""),
    ])))


def parse_query(q: str, *, websearch: bool) -> list[tuple[list[str], list[str]]]:
    """``plainto_tsquery``: one group of AND terms. ``websearch_to_tsquery``:
    OR groups of AND terms, and ``-`` negates."""
    if not websearch:
        return [(tokens(q), [])]
    groups: list[tuple[list[str], list[str]]] = []
    for part in re.split(r"\s+or\s+", q.strip(), flags=re.IGNORECASE):
        must: list[str] = []
        never: list[str] = []
        for raw in part.replace('"', " ").split():
            target = never if raw.startswith("-") else must
            target.extend(tokens(raw.lstrip("-")))
        if must or never:
            groups.append((must, never))
    return groups


def matches(m: dict[str, Any], groups: list[tuple[list[str], list[str]]]) -> bool:
    doc = _document(m)
    return any(must and all(t in doc for t in must) and not any(t in doc for t in never)
               for must, never in groups)


def route_parameters() -> dict[str, frozenset[str]]:
    """The query parameters of each real route, read from its signature."""
    from gateway.routes.whatsapp.transport.chats import list_chats
    from gateway.routes.whatsapp.transport.messages import list_messages, search_messages

    def names(fn: Callable[..., Any], *drop: str) -> frozenset[str]:
        return frozenset(n for n in inspect.signature(fn).parameters
                         if n not in {"user", *drop})

    return {
        "search": names(search_messages),
        "chats": names(list_chats),
        "thread": names(list_messages, "chat_id"),
    }


@dataclass
class ChatRequest:
    method: str
    path: str
    params: dict[str, list[str]]
    member: str
    status: int
    unknown: list[str]
    ids: list[str] = field(default_factory=list)


def _one(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[-1] if values else None


def _flag(params: dict[str, list[str]], name: str) -> bool | None:
    value = _one(params, name)
    return None if value is None else value.lower() == "true"


def _moment(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class ChatStub:
    """The three WhatsApp routes over :class:`Dataset`, and a record of each request."""

    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.requests: list[ChatRequest] = []
        self.allowed = route_parameters()
        self._lock = threading.Lock()

    def _visible(self, member: str) -> list[dict[str, Any]]:
        return [m for m in self.ds.messages + self.ds.stranger_messages
                if self.ds.owner_of(m) == member]

    def _row(self, m: dict[str, Any], *, with_chat: bool) -> dict[str, Any]:
        row = {k: m[k] for k in ("id", "chat_id", "wa_message_id", "direction", "kind",
                                 "sender_name", "body_text", "transcript_text", "sent_at")}
        row.update({"quoted_wa_message_id": None, "categories": [], "intent": None,
                    "send_regime": None, "chat_name": None, "chat_kind": None})
        if with_chat:
            chat = self.ds.chat(m["chat_id"]) or {}
            row["chat_name"], row["chat_kind"] = chat.get("name"), chat.get("kind")
        return row

    def search(self, p: dict[str, list[str]], member: str) -> tuple[int, Any]:
        rows = self._visible(member)
        q = (_one(p, "q") or "").strip()
        account = _one(p, "account_id")
        if account:
            rows = [m for m in rows if m["account_id"] == account]
        chat_id = _one(p, "chat_id")
        if chat_id:
            rows = [m for m in rows if m["chat_id"] == chat_id]
        contact = (_one(p, "contact") or "").strip().lower()
        if contact:
            rows = [m for m in rows if contact in str((self.ds.chat(m["chat_id"]) or {}).get(
                "name", "")).lower() or contact in m["sender_name"].lower()]
        kind = _one(p, "chat_kind")
        if kind:
            rows = [m for m in rows if (self.ds.chat(m["chat_id"]) or {}).get("kind") == kind]
        after, before = _moment(_one(p, "sent_after")), _moment(_one(p, "sent_before"))
        if after is not None:
            rows = [m for m in rows if datetime.fromisoformat(m["sent_at"]) >= after]
        if before is not None:
            rows = [m for m in rows if datetime.fromisoformat(m["sent_at"]) <= before]
        direction = _one(p, "direction")
        if direction:
            rows = [m for m in rows if m["direction"] == direction]
        media = _flag(p, "has_media")
        if media is not None:
            rows = [m for m in rows if (m["kind"] in MEDIA_KINDS) is media]
        filtered = bool(chat_id or contact or kind or after or before or direction
                        or media is not None)
        if not q and not filtered:
            return 422, {"detail": "Give q or a filter."}
        if q:
            groups = parse_query(q, websearch=bool(_flag(p, "websearch")))
            rows = [m for m in rows if matches(m, groups)]
        rows = sorted(rows, key=lambda m: m["sent_at"], reverse=True)
        limit = min(int(_one(p, "limit") or 50), 200)
        return 200, [self._row(m, with_chat=True) for m in rows[:limit]]

    def chats(self, p: dict[str, list[str]], member: str) -> tuple[int, Any]:
        stream = _one(p, "stream")
        out = []
        for chat in self.ds.chats:
            if self.ds.owner_of_account(chat["account_id"]) != member:
                continue
            if stream == "groups" and chat["kind"] != "group":
                continue
            out.append({"id": chat["id"], "account_id": chat["account_id"],
                        "wa_chat_id": chat["wa_chat_id"], "kind": chat["kind"],
                        "name": chat["name"], "last_snippet": "", "window_open": True})
        return 200, out

    def thread(self, chat_id: str, p: dict[str, list[str]], member: str) -> tuple[int, Any]:
        chat = self.ds.chat(chat_id)
        if chat is None or self.ds.owner_of_account(chat["account_id"]) != member:
            return 404, {"detail": "Chat not found"}
        rows = sorted((m for m in self._visible(member) if m["chat_id"] == chat_id),
                      key=lambda m: (m["sent_at"], m["id"]))
        around = _one(p, "around")
        if around is None:
            limit = min(int(_one(p, "limit") or 100), 500)
            newest = rows[-limit:] if limit > 0 else []  # H-277: the newest, in order
            return 200, [self._row(m, with_chat=False) for m in newest]
        at = next((i for i, m in enumerate(rows) if m["id"] == around), None)
        if at is None:
            return 404, {"detail": "Message not found"}
        window = min(int(_one(p, "window") or 2), 10)
        return 200, [self._row(m, with_chat=True)
                     for m in rows[max(0, at - window):at + window + 1]]

    def handle(self, method: str, raw_path: str, member: str) -> tuple[int, Any]:
        url = urlsplit(raw_path)
        params = parse_qs(url.query, keep_blank_values=True)
        path = url.path
        status, body, kind = 404, {"detail": "The eval stub does not serve this route."}, ""
        thread = re.fullmatch(r"/whatsapp/chats/([^/]+)/messages", path)
        if not member:
            status, body = 401, {"detail": "No acting member."}
        elif method != "GET":
            status, body = 405, {"detail": "The eval stub serves reads only."}
        elif path == "/whatsapp/search":
            kind = "search"
            status, body = self.search(params, member)
        elif path == "/whatsapp/chats":
            kind = "chats"
            status, body = self.chats(params, member)
        elif thread:
            kind = "thread"
            status, body = self.thread(thread.group(1), params, member)
        ids = [r["id"] for r in body] if isinstance(body, list) and kind != "chats" else []
        unknown = sorted(set(params) - self.allowed.get(kind, frozenset())) if kind else []
        with self._lock:
            self.requests.append(ChatRequest(method, path, params, member, status, unknown, ids))
        return status, body


class _Handler(BaseHTTPRequestHandler):
    stub: ChatStub

    def _answer(self, method: str) -> None:
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            status, body = 401, {"detail": "No bearer."}
        else:
            status, body = self.stub.handle(method, self.path, self.headers.get("X-User-Email", ""))
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._answer("GET")

    def do_POST(self) -> None:
        self._answer("POST")

    def do_PATCH(self) -> None:
        self._answer("PATCH")

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


@dataclass
class RunningStub:
    stub: ChatStub
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def serve(ds: Dataset) -> RunningStub:
    """Start the gateway stub on a free port of ``127.0.0.1``."""
    stub = ChatStub(ds)
    handler = type("_BoundHandler", (_Handler,), {"stub": stub})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="whatsapp-stub", daemon=True)
    thread.start()
    return RunningStub(stub=stub, server=server, thread=thread)


# ── The decide door ──────────────────────────────────────────────────────────


def who_of(m: dict[str, Any]) -> str:
    """The ``who`` of the adapter's summary: ``You`` or the sender's name."""
    return "You" if m["direction"] == "out" else (m["sender_name"] or "Them")


class StubDoor:
    """The transport under the Console client. It answers from the dataset.

    It finds the question by the state's ``query``, and each message by its
    sender and time: the state holds no id, by design (§3.3).
    """

    def __init__(self, ds: Dataset, router: StubRouter) -> None:
        self.ds = ds
        self.router = router
        self.bodies: list[dict[str, Any]] = []
        self.override: Callable[[str, str], tuple[str, float] | None] | None = None
        self._by_summary = {(who_of(m), m["sent_at"]): m["id"]
                            for m in ds.messages + ds.stranger_messages}
        self._by_prompt = {q.spec.prompt: q.id for q in ds.questions}

    def message_of(self, item: dict[str, Any]) -> str:
        return self._by_summary.get((item.get("who"), item.get("when")), "")

    def _verdict(self, qid: str, message_id: str) -> tuple[str, float]:
        if self.override is not None:
            forced = self.override(qid, message_id)
            if forced is not None:
                return forced
        return self.ds.verdict(qid, message_id)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode("utf-8")
        body = json.loads(raw)
        self.bodies.append(body)
        qid = self._by_prompt.get(body["state"]["query"], "")
        answers = {}
        for key, item in body["state"]["items"].items():
            choice, p = self._verdict(qid, self.message_of(item))
            answers[key] = {"type": "choice", "choice": choice, "confidence": p,
                            "probabilities": {choice: p}}
        payload = {"tier": DECIDE_TIER, "answers": answers,
                   "request_id": f"stub-decide-{len(self.bodies)}"}
        self.router.add(DECIDE_TIER, estimate_tokens(len(raw)),
                        estimate_tokens(len(json.dumps(answers))))
        return httpx.Response(200, json=payload)

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=10.0)


__all__ = [
    "ChatRequest", "ChatStub", "RunningStub", "StubDoor", "StubRouter", "credits_of",
    "estimate_tokens", "load_card", "matches", "message_text", "parse_query",
    "route_parameters", "serve", "tokens", "who_of",
]
