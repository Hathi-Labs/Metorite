"""The stubs of the email narrowing eval (WS-48 N2): the gateway, the door, the Router.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2.

Three stubs, each one a recorder:

* :class:`MailStub` serves the three email routes that the two paths reach,
  over HTTP on ``127.0.0.1``: ``GET /email/search`` (NARROW),
  ``GET /email/messages`` (``query_inbox``) and ``GET /email/messages/{id}``
  (``read_email`` and READ). The REAL agent tools call it through the agent's
  own ``_request``. It records each request with its parameters and its
  acting member.
* :class:`StubDoor` is the HTTP transport under the REAL Console client of
  ``acb_llm.decide``. It answers each PICK question from
  ``Dataset.verdict``, and it counts the tokens of each request.
* :class:`StubRouter` counts the requests and the tokens on each tier, and
  rates them with the Router's own ``customer_console.credits.rate_call``.

Three rules of the real gateway hold here too:

* The caller is the ``X-User-Email`` of the request. The stub never takes the
  member from the query. A member sees only the mail of their own accounts.
* An unknown query parameter is ignored, as FastAPI ignores it. The stub
  records its name, and the runner fails the run on it, because an ignored
  filter widens a search in silence.
* ``GET /email/messages/{id}`` marks the mail read, as the route does.

⚠️ **The full-text match is an approximation.** :func:`tokens` stems and drops
stop words roughly as ``to_tsvector('english')`` does. It is not Postgres.
The questions use plain words, so the two agree on this fixture. A live run
on a dev box reads the real route.

⚠️ **The token counts are an estimate.** One token is 4 characters, the rule
of the decide door (``customer_console/decide.py``). A live run reads the
Router's own counts.
"""
from __future__ import annotations

import inspect
import json
import math
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from evals.email_narrowing.dataset import Dataset

CHARS_PER_TOKEN = 4
RATE_CARD_PATH = Path(__file__).resolve().parent / "fixtures" / "rate_card.json"


def estimate_tokens(chars: int) -> int:
    """Tokens for *chars* characters, at the decide door's own rule."""
    return math.ceil(max(0, chars) / CHARS_PER_TOKEN)


# ── The full-text approximation ─────────────────────────────────────────────

#: Most of the Postgres ``english`` stop words. A stop word matches nothing.
STOP_WORDS = frozenset(["a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves"])

_WORD = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """A rough English stem: pricing, prices and price meet at ``pric``."""
    for suffix in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in STOP_WORDS]


def _document(m: dict[str, Any]) -> list[str]:
    """The route's FTS vector: subject, body, sender name and sender email."""
    frm = m.get("from_address") or {}
    return tokens(" ".join([
        str(m.get("subject") or ""), str(m.get("body_text") or ""),
        str(frm.get("name") or ""), str(frm.get("email") or ""),
    ]))


def parse_query(q: str) -> list[tuple[list[str], list[str]]]:
    """``websearch_to_tsquery``, roughly: OR groups of AND terms, ``-`` negates."""
    groups: list[tuple[list[str], list[str]]] = []
    for part in re.split(r"\s+OR\s+", q.strip()):
        must: list[str] = []
        never: list[str] = []
        for raw in part.replace('"', " ").split():
            target = never if raw.startswith("-") else must
            target.extend(tokens(raw.lstrip("-")))
        if must or never:
            groups.append((must, never))
    return groups


def match_score(m: dict[str, Any], groups: list[tuple[list[str], list[str]]]) -> float:
    """0 for no match, else a rank: hits over the root of the length."""
    doc = _document(m)
    counts = Counter(doc)
    best = 0.0
    for must, never in groups:
        if not must or any(counts[t] for t in never):
            continue
        if all(counts[t] for t in must):
            best = max(best, sum(min(counts[t], 3) for t in must) / math.sqrt(len(doc) + 1))
    return best


# ── The gateway stub ─────────────────────────────────────────────────────────


def route_parameters() -> dict[str, frozenset[str]]:
    """The query parameters of each real route, read from its signature."""
    from gateway.routes.email.transport.messages import get_message, list_messages
    from gateway.routes.email.transport.search import search_messages

    def names(fn: Callable[..., Any]) -> frozenset[str]:
        return frozenset(n for n in inspect.signature(fn).parameters if n != "user")

    return {
        "search": names(search_messages),
        "messages": names(list_messages),
        "message": names(get_message) - {"message_id"},
    }


@dataclass
class MailRequest:
    method: str
    path: str
    params: dict[str, list[str]]
    member: str
    status: int
    unknown: list[str]
    ids: list[str] = field(default_factory=list)


def _flag(params: dict[str, list[str]], name: str) -> bool | None:
    value = (params.get(name) or [None])[-1]
    if value is None:
        return None
    return value.lower() == "true"


def _one(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[-1] if values else None


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # the route drops what it cannot parse, in silence
        return None


class MailStub:
    """The three email routes over :class:`Dataset`, and a record of each request."""

    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.requests: list[MailRequest] = []
        self.allowed = route_parameters()
        self._lock = threading.Lock()

    def _visible(self, member: str) -> list[dict[str, Any]]:
        return [m for m in self.ds.messages + self.ds.stranger_messages
                if self.ds.owner_of(m) == member]

    def _filter(self, rows: list[dict[str, Any]], p: dict[str, list[str]], *,
                from_param: str, inbox_default: bool) -> list[dict[str, Any]]:
        out = rows
        account = _one(p, "account_id")
        if account:
            out = [m for m in out if m["account_id"] == account]
        folder = _one(p, "folder")
        if folder is None and inbox_default:
            folder = "inbox"
        if folder:
            key = folder.lower()
            if key == "all":
                out = [m for m in out if m["folder"] not in {"junk", "trash"}]
            else:
                out = [m for m in out if m["folder"] == key]
        after, before = _moment(_one(p, "received_after")), _moment(_one(p, "received_before"))
        if after is not None:
            out = [m for m in out if datetime.fromisoformat(m["received_at"]) >= after]
        if before is not None:
            out = [m for m in out if datetime.fromisoformat(m["received_at"]) <= before]
        for name in ("is_read", "has_attachments"):
            flag = _flag(p, name)
            if flag is not None:
                out = [m for m in out if bool(m.get(name)) is flag]
        sender = _one(p, from_param)
        if sender:
            needle = sender.strip().lower()
            out = [m for m in out if needle in m["from_address"]["email"].lower()
                   or (from_param == "from_addr"
                       and needle in m["from_address"]["name"].lower())]
        return out

    def _row(self, m: dict[str, Any], *, light: bool) -> dict[str, Any]:
        row = {k: m[k] for k in (
            "id", "account_id", "folder", "from_address", "to_addresses", "subject",
            "snippet", "has_attachments", "is_read", "received_at",
        )}
        row["body_text"] = "" if light else m["body_text"]
        row["body_html"] = None
        return row

    def search(self, p: dict[str, list[str]], member: str) -> dict[str, Any]:
        rows = self._filter(self._visible(member), p, from_param="from_addr",
                            inbox_default=False)
        q = (_one(p, "q") or "").strip()
        if q:
            groups = parse_query(q)
            score = {m["id"]: match_score(m, groups) for m in rows}
            # rank DESC, then received_at DESC, as the route orders it.
            ranked = sorted((m for m in rows if score[m["id"]] > 0),
                            key=lambda m: m["received_at"], reverse=True)
            ranked.sort(key=lambda m: score[m["id"]], reverse=True)
        else:
            ranked = sorted(rows, key=lambda m: m["received_at"], reverse=True)
        size = min(int(_one(p, "page_size") or 50), 200)
        light = bool(_flag(p, "light"))
        page = [self._row(m, light=light) for m in ranked[:size]]
        return {"emails": page, "total": len(ranked), "page": 1, "page_size": size}

    def messages(self, p: dict[str, list[str]], member: str) -> dict[str, Any]:
        rows = self._filter(self._visible(member), p, from_param="from_email",
                            inbox_default=True)
        q = (_one(p, "query") or "").strip()
        if q:
            groups = parse_query(q)
            rows = [m for m in rows if match_score(m, groups) > 0]
        rows = sorted(rows, key=lambda m: m["received_at"], reverse=True)
        size = min(int(_one(p, "page_size") or 50), 200)
        return {"emails": [self._row(m, light=False) for m in rows[:size]],
                "total": len(rows)}

    def message(self, message_id: str, member: str) -> tuple[int, dict[str, Any]]:
        m = next((x for x in self._visible(member) if x["id"] == message_id), None)
        if m is None:
            return 404, {"detail": "Message not found"}
        m["is_read"] = True  # the route marks the mail read
        row = self._row(m, light=False)
        row["cc_addresses"] = []
        row["attachments"] = []
        return 200, row

    def handle(self, method: str, raw_path: str, member: str) -> tuple[int, Any]:
        url = urlsplit(raw_path)
        params = parse_qs(url.query, keep_blank_values=True)
        path = url.path
        status, body, kind = 404, {"detail": "The eval stub does not serve this route."}, ""
        ids: list[str] = []
        if not member:
            status, body = 401, {"detail": "No acting member."}
        elif method == "GET" and path == "/email/search":
            kind = "search"
            status, body = 200, self.search(params, member)
        elif method == "GET" and path == "/email/messages":
            kind = "messages"
            status, body = 200, self.messages(params, member)
        elif method == "GET" and path.startswith("/email/messages/"):
            kind = "message"
            status, body = self.message(path.rsplit("/", 1)[-1], member)
        if isinstance(body, dict):
            if "emails" in body:
                ids = [e["id"] for e in body["emails"]]
            elif status == 200 and "id" in body:
                ids = [body["id"]]
        unknown = sorted(set(params) - self.allowed.get(kind, frozenset())) if kind else []
        with self._lock:
            self.requests.append(MailRequest(method, path, params, member, status, unknown, ids))
        return status, body


class _Handler(BaseHTTPRequestHandler):
    stub: MailStub

    def do_GET(self) -> None:
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            status, body = 401, {"detail": "No bearer."}
        else:
            status, body = self.stub.handle("GET", self.path, self.headers.get("X-User-Email", ""))
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


@dataclass
class RunningStub:
    stub: MailStub
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
    stub = MailStub(ds)
    handler = type("_BoundHandler", (_Handler,), {"stub": stub})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="email-stub", daemon=True)
    thread.start()
    return RunningStub(stub=stub, server=server, thread=thread)


# ── The Router's counters ────────────────────────────────────────────────────


@dataclass
class TierCount:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class StubRouter:
    """The requests and tokens on each tier, and their credits."""

    def __init__(self) -> None:
        self.tiers: dict[str, TierCount] = {}

    def add(self, tier: str, prompt_tokens: int, completion_tokens: int) -> None:
        count = self.tiers.setdefault(tier, TierCount())
        count.requests += 1
        count.prompt_tokens += prompt_tokens
        count.completion_tokens += completion_tokens

    def table(self) -> dict[str, dict[str, int]]:
        return {t: vars(c).copy() for t, c in sorted(self.tiers.items())}

    def credits(self, card: dict[str, dict[str, str]]) -> Decimal:
        return credits_of(self.table(), card)


def load_card(path: Path = RATE_CARD_PATH) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["tiers"]


def credits_of(table: dict[str, dict[str, int]], card: dict[str, dict[str, str]]) -> Decimal:
    """The credits of a tier table, through the Router's own ``rate_call``."""
    from customer_console.credits import TierRate, TokenUsage, rate_call

    total = Decimal(0)
    for tier, count in table.items():
        rate = card[tier]
        priced = TierRate(
            tier=tier, input_per_1m=Decimal(rate["input_per_1m"]),
            output_per_1m=Decimal(rate["output_per_1m"]), pricing_mode="priced",
        )
        total += rate_call(priced, TokenUsage(
            prompt_tokens=count["prompt_tokens"], completion_tokens=count["completion_tokens"],
        ))
    return total


# ── The decide door ──────────────────────────────────────────────────────────

#: The tier of every PICK request (``acb_llm.decide.DECIDE_TIER``).
DECIDE_TIER = "tier-decide"


class StubDoor:
    """The transport under the Console client. It answers from the dataset.

    It finds the question by the state's ``query``, and each message by its
    title, sender and date: the state holds no id, by design (§3.3).
    """

    def __init__(self, ds: Dataset, router: StubRouter) -> None:
        self.ds = ds
        self.router = router
        self.bodies: list[dict[str, Any]] = []
        self.override: Callable[[str, str], tuple[str, float] | None] | None = None
        self._by_summary: dict[tuple[str, str], str] = {}
        for m in ds.messages + ds.stranger_messages:
            frm = m["from_address"]
            who = f"{frm['name']} <{frm['email']}>" if frm["name"] else frm["email"]
            self._by_summary[(who, m["received_at"])] = m["id"]
        self._by_prompt = {q.spec.prompt: q.id for q in ds.questions}

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
            message_id = self._by_summary.get((item["who"], item["when"]), "")
            choice, p = self._verdict(qid, message_id)
            answers[key] = {"type": "choice", "choice": choice, "confidence": p,
                            "probabilities": {choice: p}}
        payload = {"tier": DECIDE_TIER, "answers": answers,
                   "request_id": f"stub-decide-{len(self.bodies)}"}
        self.router.add(DECIDE_TIER, estimate_tokens(len(raw)),
                        estimate_tokens(len(json.dumps(answers))))
        return httpx.Response(200, json=payload)

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=10.0)
