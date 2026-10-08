"""The WhatsApp adapter of the narrowing pipeline, and its tool in whatsapp-assistant. WS-48 N4.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.2, §3.6, §4, §5 and
§9 N4 (done-when items 1 to 4 of N2, for ``GET /whatsapp/search``). The
adapter is ``apps/agents/agent-whatsapp-assistant/narrow_source.py``. It
follows the email adapter of N2 and ``test_email_narrow_source.py``.

The tool tests run the REAL tool (``narrow_tool_for``), the REAL adapter on
the REAL agent module, and the REAL decide facade and Console client. Only the
HTTP transport under the Console client is a script (:class:`Door`), and the
agent's ``_get`` is a recorder (:class:`Gateway`). So a body that a test reads
is the body that the Router's ``POST /v1/decide`` would receive.

The route tests run the REAL route functions on a recording session
(:class:`Session`). They prove which SQL each route sends. They do not prove
that Postgres accepts it: ``test_whatsapp_read_no_mark.py`` runs the same
routes on a real database under FORCE RLS (R8).

Mutations this file catches (R7), each one run red before the change:

* NARROW copies a whole message, or a thread, into a summary, or calls the
  thread route -> ``test_narrow_sends_no_thread_on_the_pick_wire``;
* a filter maps to a parameter that ``GET /whatsapp/search`` does not declare
  -> ``test_each_target_is_a_real_parameter_of_the_route``;
* a key maps to the wrong parameter, or a value goes out in the wrong form ->
  ``test_each_filter_key_maps_to_its_parameter``;
* an unknown key, or a bad date, flag or id, passes in silence ->
  ``test_an_unknown_filter_key_is_refused_by_name`` and
  ``test_a_bad_value_is_refused_by_name_and_sends_nothing``;
* the search ANDs the words of the question, or keeps a stop word ->
  ``test_with_no_words_the_search_is_any_word_of_the_question``;
* READ reads a message that was dropped, more than 25 items, a body past 6000
  characters, or a window wider than the constant ->
  ``test_read_fetches_only_the_kept_items_within_the_caps`` and
  ``test_the_adapter_reads_at_most_the_read_cap``;
* a non-UUID half of an id reaches a request path ->
  ``test_read_puts_only_uuids_in_a_path``;
* READ calls anything but the GET of the thread route, or the thread route
  writes or marks anything -> ``test_read_is_only_the_get_of_the_thread_route``
  and ``test_the_thread_route_sends_only_a_select`` (and the R8 half,
  ``test_whatsapp_read_no_mark.py``);
* a voice note with no body reaches PICK with no text ->
  ``test_a_voice_note_uses_its_transcript``;
* a match deep in a long message is cut off before PICK ->
  ``test_a_deep_match_reaches_the_pick_state``;
* a failed read hides the id of its item -> ``test_a_failed_read_is_counted_not_hidden``;
* more than 200 rows reach PICK -> ``test_at_most_200_candidates_reach_pick``;
* the search route drops its member scope when a filter is set, or a bad
  filter value reaches the SQL -> ``test_the_search_route_keeps_the_member_scope``
  and ``test_the_search_route_refuses_a_bad_value``;
* a run with no acting member reaches the gateway ->
  ``test_a_run_with_no_member_makes_no_gateway_call``;
* the tool exists with the flag off, or for another agent ->
  ``test_the_flag_off_means_no_tool`` and
  ``test_a_flag_for_another_agent_means_no_tool``;
* the instructions name the tool when it is absent, or lose the count line
  when it is present -> ``test_the_instructions_name_the_tool_only_when_it_is_held``;
* the built tool reads as an egress tool -> ``test_the_built_tool_is_a_platform_read``.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth import console_resolve
from acb_common import bind_run_context, clear_run_context
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_skills import egress as eg
from acb_skills import narrowing, system_one
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "apps" / "agents" / "agent-whatsapp-assistant"
AGENT = "whatsapp-assistant"
MEMBER = "member@narrow.test"
ORG = "org-whatsapp-narrow"
THREAD = "thread-whatsapp-narrow-1"
QUERY = "Which dealers asked for the price of the pump this month?"
#: A text past the snippet clip. It must never reach a PICK request.
TAIL_CANARY = "TAILBODY-CANARY-7731"
#: A message of the thread around a match. It must never reach PICK either.
THREAD_CANARY = "THREAD-CANARY-4410"
#: The real client class, held before any test patches ``httpx.AsyncClient``.
_REAL_CLIENT = httpx.AsyncClient


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agents = _load("ws48_n4_whatsapp_agents", "agents.py")
ns = _load("ws48_n4_whatsapp_narrow_source", "narrow_source.py")


def cid(n: int) -> str:
    """A stable chat id: a UUID, as the route gives."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ws48-n4/chat/{n}"))


def mid(n: int) -> str:
    """A stable message id: a UUID, as the route gives."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ws48-n4/message/{n}"))


def iid(n: int) -> str:
    """The item id of message *n*: ``<chat_id>:<message_id>``."""
    return f"{cid(n)}:{mid(n)}"


def _row(n: int, **extra: Any) -> dict[str, Any]:
    """One row of ``GET /whatsapp/search``. Message *n* is in chat *n*."""
    row = {
        "id": mid(n),
        "chat_id": cid(n),
        "wa_message_id": f"wamid.{n}",
        "direction": "in",
        "kind": "text",
        "sender_name": f"Dealer {n}",
        "body_text": f"What is the price of the pump for order {n}?",
        "transcript_text": None,
        "sent_at": "2026-10-01T10:00:00+00:00",
        "chat_name": f"Dealer {n}",
        "chat_kind": "dm",
    }
    row.update(extra)
    return row


class Gateway:
    """A recorder at the agent's ``_get``: the search and the thread route."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_read: set[str] = set()
        self.body = "B" * 900

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        if path == ns.SEARCH_PATH:
            # The route applies its limit, and gives no total.
            return self.rows[:int((params or {}).get("limit") or 50)]
        prefix, suffix = "/whatsapp/chats/", "/messages"
        if path.startswith(prefix) and path.endswith(suffix):
            chat = path[len(prefix):-len(suffix)]
            around = (params or {}).get("around")
            if around in self.fail_read:
                raise RuntimeError("WhatsApp GET failed (404): Message not found")
            row = next(r for r in self.rows if r["id"] == around and r["chat_id"] == chat)
            window = int((params or {}).get("window") or 0)
            before = [{**row, "id": f"before-{k}", "body_text": f"{THREAD_CANARY} {k}",
                       "direction": "out"} for k in range(window)]
            anchor = {**row, "body_text": f"{self.body} ({row['id']})"}
            after = [{**row, "id": f"after-{k}", "body_text": f"reply {k}"}
                     for k in range(window)]
            return [*before, anchor, *after]
        raise AssertionError(f"an unexpected route: {path}")

    @property
    def searches(self) -> list[dict[str, Any]]:
        return [p for path, p in self.calls if path == ns.SEARCH_PATH]

    @property
    def reads(self) -> list[tuple[str, dict[str, Any]]]:
        return [(path, p) for path, p in self.calls if path != ns.SEARCH_PATH]


class Door:
    """The HTTP transport under the Console client. It answers each question
    by the number at the end of the item's title, with :attr:`rule`."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.rule: Callable[[int], tuple[str, float]] = lambda _n: ("yes", 0.95)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        answers = {}
        for key, item in body["state"]["items"].items():
            choice, p = self.rule(int(str(item["title"]).split()[-1]))
            answers[key] = {"type": "choice", "choice": choice, "confidence": p,
                            "probabilities": {choice: p}}
        return httpx.Response(200, json={
            "tier": "tier-decide", "answers": answers, "request_id": f"req-{len(self.bodies)}",
        })

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return _REAL_CLIENT(transport=httpx.MockTransport(self._handle), timeout=5.0)


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> Door:
    fake = Door()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


@pytest.fixture(autouse=True)
def _no_system_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decide door answers here. A fallback is a test failure."""

    async def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("PICK fell back to System 1")

    monkeypatch.setattr(system_one, "ask", refuse)


@pytest.fixture(autouse=True)
def _box(monkeypatch: pytest.MonkeyPatch):
    """A box with the decide door on and wired, one bound open run, and the
    flag off."""
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.whatsapp-narrow.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ws48-n4-dummy")
    monkeypatch.delenv("NARROWING_AGENTS", raising=False)
    get_settings.cache_clear()
    narrowing._DROPPED.clear()
    clear_run_context()
    bind_run_context(
        run_id="run-whatsapp-narrow-1", thread_id=THREAD, agent=AGENT, user=MEMBER,
        source="chat", app="whatsapp", member_verified=True,
    )
    token = bind_tenant(ORG)
    with artifact_context_scope():
        bind_artifact_context(
            session_id=THREAD, agent_name=AGENT, run_id="run-whatsapp-narrow-1",
            member=MEMBER, no_egress=False,
        )
        yield
    release_tenant(token)
    clear_run_context()
    narrowing._DROPPED.clear()
    get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NARROWING_AGENTS", value)
    get_settings.cache_clear()


def _tool(monkeypatch: pytest.MonkeyPatch, gateway: Gateway) -> Callable[..., Any]:
    """The tool as the agent builds it, on a recorder at the agent's ``_get``."""
    _flag(monkeypatch, AGENT)
    monkeypatch.setattr(agents, "_get", gateway.get)
    tool = agents._narrow_tool()
    assert tool is not None
    return tool


def _built(monkeypatch: pytest.MonkeyPatch) -> Any:
    return agents.build_agents()[0]


def _tool_names(agent: Any) -> list[str]:
    return [getattr(t, "name", "") for t in agent.default_options["tools"]]


def _pick_items(door: Door) -> list[dict[str, Any]]:
    return [i for b in door.bodies for i in b["state"]["items"].values()]


# ── Done-when 1: the filters map to the route's real parameters ─────────────


def _search_parameters() -> dict[str, inspect.Parameter]:
    from gateway.routes.whatsapp.transport.messages import search_messages

    return dict(inspect.signature(search_messages).parameters)


def test_each_target_is_a_real_parameter_of_the_route() -> None:
    """Read from the route's own signature, never from a hand list."""
    route = _search_parameters()
    sent = set(ns.PARAMS.values()) | set(ns.FIXED_PARAMS) | {"q"}
    assert sent - set(route) == set(), sorted(sent - set(route))
    # The page size is inside the route's own bound.
    bound = [m.le for m in route["limit"].default.metadata if hasattr(m, "le")]
    # One row past the cap, so NARROW sees that more matched (review P1).
    assert bound and bound[0] >= narrowing.MAX_CANDIDATES + 1

    from gateway.routes.whatsapp.transport import messages as messages_mod

    thread = dict(inspect.signature(messages_mod.list_messages).parameters)
    assert {"around", "window"} <= set(thread)
    assert 0 < ns.READ_WINDOW <= messages_mod.MAX_WINDOW


def test_the_filter_keys_are_the_keys_of_n4() -> None:
    """§4 names account_id, chat_id, after and before. N4 adds contact,
    group, from_me, has_media and the search words."""
    assert frozenset({
        "account_id", "chat_id", "contact", "group", "after", "before",
        "from_me", "has_media", "words",
    }) == ns.FILTER_KEYS


def test_the_fixed_parameters_are_hybrid_websearch_and_200() -> None:
    params = ns.search_params(QUERY, {})
    assert params["hybrid"] == "true" and params["websearch"] == "true"
    assert params["limit"] == "201"  # 200 and one probe row (review P1)


@pytest.mark.parametrize(("filters", "expected"), [
    ({"account_id": "8C7D3E2A-1B4F-4C5D-9E6F-7A8B9C0D1E2F"},
     {"account_id": "8c7d3e2a-1b4f-4c5d-9e6f-7a8b9c0d1e2f"}),
    ({"chat_id": cid(1).upper()}, {"chat_id": cid(1)}),
    ({"contact": "  Dealers   North "}, {"contact": "Dealers North"}),
    ({"group": True}, {"chat_kind": "group"}),
    ({"group": "false"}, {"chat_kind": "dm"}),
    ({"after": "2026-09-01"}, {"sent_after": "2026-09-01T00:00:00+00:00"}),
    ({"before": "2026-09-30T18:00:00Z"}, {"sent_before": "2026-09-30T18:00:00+00:00"}),
    # A date-only before INCLUDES its day: the route applies <=.
    ({"before": "2026-09-30"}, {"sent_before": "2026-09-30T23:59:59.999999+00:00"}),
    ({"from_me": True}, {"direction": "out"}),
    ({"from_me": False}, {"direction": "in"}),
    ({"has_media": True}, {"has_media": "true"}),
    ({"has_media": "false"}, {"has_media": "false"}),
])
def test_each_filter_key_maps_to_its_parameter(
    filters: dict[str, Any], expected: dict[str, Any],
) -> None:
    params = ns.search_params(QUERY, filters)
    for key, value in expected.items():
        assert params[key] == value, key


async def test_an_unknown_filter_key_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(1)])
    out = await _tool(monkeypatch, gateway)(QUERY, filters=json.dumps({"unread": True}))
    assert out.startswith("narrow_and_read: unknown filter key: unread.")
    assert "from_me" in out and "words" in out
    assert gateway.calls == [] and door.bodies == []
    with pytest.raises(narrowing.FilterRefused, match="unread"):
        ns.search_params(QUERY, {"unread": True})


@pytest.mark.parametrize(("filters", "named"), [
    ({"after": "last week"}, "the filter after must be a date"),
    ({"before": "2026-13-01"}, "the filter before must be a date"),
    ({"group": "yes"}, "the filter group must be true or false"),
    ({"from_me": 1}, "the filter from_me must be true or false"),
    ({"has_media": None}, "the filter has_media must be true or false"),
    ({"account_id": "../../admin/members"}, "the filter account_id must be the id"),
    ({"chat_id": "chat-1"}, "the filter chat_id must be the id of a chat"),
    ({"contact": ""}, "the filter contact must be a text"),
    ({"contact": "x" * 201}, "the filter contact is longer than 200"),
])
async def test_a_bad_value_is_refused_by_name_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, door: Door, filters: dict[str, Any], named: str,
) -> None:
    gateway = Gateway([_row(1)])
    out = await _tool(monkeypatch, gateway)(QUERY, filters=json.dumps(filters))
    assert out.startswith(f"narrow_and_read: {named}"), out
    assert gateway.calls == [] and door.bodies == []


# ── The search text (§2.3: recall is lexical, and `simple` keeps stop words) ─


def test_with_no_words_the_search_is_any_word_of_the_question() -> None:
    assert ns.search_text(QUERY, {}) == "dealers OR asked OR price OR pump OR month"
    # An operator word of the question never joins or negates two words.
    assert ns.search_text("Ravi or Sunil, and not Amit", {}) == "ravi OR sunil OR amit"
    # Hinglish keeps its words: the stop list is English only.
    assert ns.search_text("kal AWB bhej dunga", {}) == "kal OR awb OR bhej OR dunga"
    assert ns.search_text("-- ?", {}) == ""


def test_words_set_the_search_and_an_empty_words_uses_the_filters_only() -> None:
    words = "price OR prices OR rate OR quote"
    assert ns.search_params(QUERY, {"words": words})["q"] == words
    assert "q" not in ns.search_params(QUERY, {"words": "", "contact": "Dealers North"})


# ── Done-when 2 and §3.2: one message per summary, never a thread ──────────


async def test_narrow_sends_no_thread_on_the_pick_wire(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """A message longer than the clip, and its thread, never reach PICK. NARROW
    calls the search route only."""
    long = "What is the price of the pump? " + "x" * 400 + f" {TAIL_CANARY}"
    gateway = Gateway([_row(n, body_text=long) for n in range(1, 21)])
    door.rule = lambda n: ("no", 0.95)  # drop all: READ reads nothing
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 20 matches. Kept 0, dropped 20. Read 0 in full.")
    assert [path for path, _p in gateway.calls] == [ns.SEARCH_PATH]
    wire = json.dumps(door.bodies)
    assert door.bodies and TAIL_CANARY not in wire and THREAD_CANARY not in wire
    for item in _pick_items(door):
        assert set(item) == {"title", "who", "when", "snippet"}
        assert len(item["snippet"]) <= narrowing.SNIPPET_CLIP
    assert gateway.reads == []


def test_a_summary_names_the_chat_the_sender_and_the_time() -> None:
    c = ns.candidate_of(_row(3, chat_name="Dealers North", chat_kind="group",
                             sender_name="Sunil"))
    assert (c.id, c.title, c.who, c.when) == (
        iid(3), "Group: Dealers North", "Sunil", "2026-10-01T10:00:00+00:00")
    assert c.snippet == "What is the price of the pump for order 3?"
    mine = ns.candidate_of(_row(4, direction="out"))
    assert mine.who == "You" and mine.title == "Chat: Dealer 4"


async def test_a_voice_note_uses_its_transcript(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """§9 N4: a voice note has an empty body. PICK sees its transcript."""
    voice = _row(1, kind="voice", body_text="", transcript_text="kal pump ka rate bhejo")
    photo = _row(2, kind="image", body_text="")
    gateway = Gateway([voice, photo])
    await _tool(monkeypatch, gateway)(QUERY)
    items = _pick_items(door)
    assert items[0]["snippet"] == "(voice note) kal pump ka rate bhejo"
    assert items[1]["snippet"] == "[image]"


async def test_a_deep_match_reaches_the_pick_state(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """The route has no highlight. A long message with the match near its end
    reaches PICK with the match in its snippet."""
    deep = _row(1, body_text="Good morning. " + "The stock count is done. " * 30
                + "Also, what is your price for the pump?")
    gateway = Gateway([deep])
    await _tool(monkeypatch, gateway)(QUERY, filters=json.dumps({"words": "price"}))
    [item] = _pick_items(door)
    assert "what is your price for the pump" in item["snippet"]
    assert item["snippet"].startswith("… ")
    # The head of a message that holds the match stays as it is.
    assert ns.excerpt("price " + "y" * 400, ["price"]).startswith("price ")


async def test_at_most_200_candidates_reach_pick(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """The route gives no total. The probe row says that more than 200
    matched, and the count line says "more than", never a false total."""
    gateway = Gateway([_row(n) for n in range(1, 251)])
    door.rule = lambda n: ("no", 0.95)
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 200 of more than 200 matches. Kept 0, dropped 200.")
    assert "More than 200 items matched. Narrow the filters to check the rest." in out
    assert sum(len(b["questions"]) for b in door.bodies) == 200
    assert max(len(b["questions"]) for b in door.bodies) == 16


async def test_a_full_page_of_exactly_200_is_not_an_overflow(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(n) for n in range(1, 201)])
    door.rule = lambda n: ("no", 0.95)
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 200 matches. Kept 0, dropped 200.")
    assert "More than" not in out


async def test_a_question_of_stop_words_only_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """With no search word and no filter the route would refuse the call. The
    model gets a reason it can act on, not "could not search"."""
    gateway = Gateway([_row(1)])
    out = await _tool(monkeypatch, gateway)("What did they do about it?")
    assert out.startswith("narrow_and_read: the question holds no search word.")
    assert gateway.calls == [] and door.bodies == []
    # A filter alone is a real search.
    assert "q" not in ns.search_params("What did they do about it?", {"group": True})


# ── §3.6: READ reads only the kept items, within the caps ───────────────────


async def test_read_fetches_only_the_kept_items_within_the_caps(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(n) for n in range(1, 61)])
    gateway.body = "B" * 9000
    kept = {n for n in range(1, 61) if n % 2 == 0}  # 30 kept, 30 dropped
    door.rule = lambda n: ("yes", 0.9) if n in kept else ("no", 0.9)
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 60 matches. Kept 30, dropped 30. Read 25 in full.")
    in_rank = [n for n in range(1, 61) if n in kept][:narrowing.READ_CAP]
    assert [p["around"] for _path, p in gateway.reads] == [mid(n) for n in in_rank]
    assert [path for path, _p in gateway.reads] == [
        ns.THREAD_PATH.format(chat_id=cid(n)) for n in in_rank]
    assert all(p == {"around": p["around"], "window": str(ns.READ_WINDOW)}
               for _path, p in gateway.reads)
    # Each item is clipped at 6000 characters, and its kept message is marked.
    assert out.count("--- item ") == narrowing.READ_CAP
    assert "B" * (narrowing.BODY_CLIP + 1) not in out
    assert f"--- item {iid(2)} |" in out and f"--- item {iid(1)} |" not in out


async def test_a_read_shows_the_window_with_the_kept_message_marked(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(1)])
    out = await _tool(monkeypatch, gateway)(QUERY)
    lines = out.split(f"--- item {iid(1)} |", 1)[1].splitlines()
    marked = [line for line in lines if line.startswith(">> ")]
    assert len(marked) == 1 and "B" * 50 in marked[0]
    context = [line for line in lines if line.startswith("   [")]
    assert len(context) == 2 * ns.READ_WINDOW
    assert "Title: Chat: Dealer 1" in out


async def test_a_failed_read_is_counted_not_hidden(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """A chat of another member is a 404 on the thread route. The item is
    counted, and its id reaches the model."""
    gateway = Gateway([_row(n) for n in range(1, 4)])
    gateway.fail_read = {mid(2)}
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 3 matches. Kept 3, dropped 0. Read 2 in full.")
    assert "The read failed for 1 kept items. Read them with your other tools." in out
    assert f"- {iid(2)} | 2026-10-01T10:00:00+00:00 | Dealer 2" in out
    assert f"--- item {iid(2)}" not in out


async def test_read_is_only_the_get_of_the_thread_route(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """READ changes no state. It calls ONE callable, the agent's GET, and only
    the thread route with ``around`` and ``window``. The adapter holds no
    other client (it takes only ``get``), and its module names no write."""
    gateway = Gateway([_row(n) for n in range(1, 4)])
    await _tool(monkeypatch, gateway)(QUERY)
    assert len(gateway.reads) == 3
    for path, params in gateway.reads:
        assert path.startswith("/whatsapp/chats/") and path.endswith("/messages")
        assert set(params) == {"around", "window"}
    assert list(inspect.signature(ns.WhatsAppNarrowSource).parameters) == ["get"]
    source = (AGENT_DIR / "narrow_source.py").read_text(encoding="utf-8")
    for word in ("_post", "mark_read", "/read", "/transcribe", "/draft", "/send"):
        assert word not in source, word


async def test_the_adapter_reads_at_most_the_read_cap() -> None:
    """The tool passes at most 25 ids. The adapter holds the cap too."""
    gateway = Gateway([_row(n) for n in range(1, 41)])
    source = ns.WhatsAppNarrowSource(get=gateway.get)
    got = await source.read([iid(n) for n in range(1, 41)])
    assert [p["around"] for _path, p in gateway.reads] == [
        mid(n) for n in range(1, narrowing.READ_CAP + 1)]
    assert [i.id for i in got] == [iid(n) for n in range(1, narrowing.READ_CAP + 1)]


async def test_read_puts_only_uuids_in_a_path() -> None:
    gateway = Gateway([_row(1)])
    source = ns.WhatsAppNarrowSource(get=gateway.get)
    got = await source.read([
        "../../admin/members", f"{cid(1)}:../../x", mid(1), iid(1).upper(), "a:b:c",
    ])
    assert [path for path, _p in gateway.reads] == [ns.THREAD_PATH.format(chat_id=cid(1))]
    assert [i.id for i in got] == [iid(1).upper()]  # the id the tool asked with
    assert ns.split_id(iid(1)) == (cid(1), mid(1))
    assert ns.split_id(mid(1)) is None


# ── The routes: the search keeps its scope, and the thread route only reads ──


class Session:
    """A recording session: each statement, and canned rows."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.rows = rows or []

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        self.statements.append((sql, dict(params or {})))
        if "FROM wa_chats c" in sql and "JOIN wa_accounts a" in sql:  # assert_chat_owned
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(account_id="acct"))
        return SimpleNamespace(fetchall=lambda: list(self.rows))


def _db_row(n: int, side: int, sent: str) -> Any:
    from datetime import datetime

    return SimpleNamespace(
        id=mid(n), chat_id=cid(1), wa_message_id=f"wamid.{n}", direction="in", kind="text",
        sender={"name": "Dealer"}, body_text=f"m{n}", transcript_text=None,
        quoted_wa_message_id=None, categories=[], intent=None, send_regime=None,
        sent_at=datetime.fromisoformat(sent), chat_name="Dealer", chat_kind="dm", side=side,
    )


def _member() -> Any:
    from acb_auth.roles import UserContext, UserRole

    return UserContext(email=MEMBER, role=UserRole.EMPLOYEE, organization_id=ORG)


async def test_the_thread_route_sends_only_a_select(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``around`` read checks the owner, then sends ONE read, and nothing
    else: no INSERT, no UPDATE, no DELETE and no receipt. Its rows come back
    oldest first, whatever order the UNION gives."""
    from gateway.routes.whatsapp.transport import messages as messages_mod

    rows = [
        _db_row(3, 1, "2026-10-01T10:03:00+00:00"),
        _db_row(1, -1, "2026-10-01T10:01:00+00:00"),
        _db_row(2, 0, "2026-10-01T10:02:00+00:00"),
        _db_row(0, -1, "2026-10-01T10:00:00+00:00"),
    ]
    session = Session(rows)
    monkeypatch.setattr(messages_mod, "_tenant_session", lambda: session)
    got = await messages_mod.list_messages(cid(1), around=mid(2), window=2, user=_member())
    assert [m.id for m in got] == [mid(0), mid(1), mid(2), mid(3)]
    assert len(session.statements) == 2
    for sql, _params in session.statements:
        head = sql.strip().split(None, 1)[0].upper()
        assert head in {"SELECT", "WITH"}, sql
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE"):
            assert verb not in sql.upper(), verb
    owner_sql, owner_params = session.statements[0]
    assert "a.user_id = :uid" in owner_sql and owner_params["uid"] == MEMBER
    source = inspect.getsource(messages_mod)
    assert "mark_read" not in source.replace("``mark_read``", "")


async def test_the_thread_route_refuses_an_anchor_of_another_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's own 404 path, on a session with no anchor row. This fake
    agrees with any SQL, so it proves only the Python half. The SQL half, an
    anchor of another chat that the CTE must not find, is
    ``test_whatsapp_read_no_mark.py::test_the_around_read_keeps_the_owner_scope``
    on a real database (review note)."""
    from fastapi import HTTPException
    from gateway.routes.whatsapp.transport import messages as messages_mod

    monkeypatch.setattr(messages_mod, "_tenant_session", lambda: Session([]))
    for around in (mid(9), "not-a-uuid"):
        with pytest.raises(HTTPException) as caught:
            await messages_mod.list_messages(cid(1), around=around, window=2, user=_member())
        assert caught.value.status_code == 404


async def test_the_search_route_keeps_the_member_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.routes.whatsapp.transport import messages as messages_mod

    session = Session([])
    monkeypatch.setattr(messages_mod, "_tenant_session", lambda: session)
    await messages_mod.search_messages(
        q="price or rate", limit=200, hybrid=False, websearch=True, chat_id=cid(1),
        contact="50%_off", chat_kind="group", sent_after=None, sent_before=None,
        direction="out", has_media=True, user=_member(),
    )
    [(sql, params)] = session.statements
    assert "m.account_id IN (SELECT id FROM wa_accounts WHERE user_id = :uid)" in sql
    assert params["uid"] == MEMBER
    assert "websearch_to_tsquery('simple', :q)" in sql and "plainto" not in sql
    assert params["cid"] == cid(1) and params["kind"] == "group" and params["dir"] == "out"
    assert params["contact"] == "%50\\%\\_off%"
    assert "m.kind IN ('image'" in sql

    # No filter keeps the old SQL shape: plainto and no chat filter.
    session.statements.clear()
    await messages_mod.search_messages(
        q="price", limit=50, hybrid=False, user=_member(),
    )
    [(sql, params)] = session.statements
    assert "plainto_tsquery('simple', :q)" in sql and "cid" not in params


@pytest.mark.parametrize(("kwargs", "detail"), [
    ({}, "Give q or a filter."),
    ({"chat_id": "chat-1"}, "chat_id must be a chat id."),
    ({"chat_kind": "channel"}, "chat_kind must be dm, group or broadcast."),
    ({"direction": "sideways"}, "direction must be in or out."),
])
async def test_the_search_route_refuses_a_bad_value(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], detail: str,
) -> None:
    from fastapi import HTTPException
    from gateway.routes.whatsapp.transport import messages as messages_mod

    session = Session([])
    monkeypatch.setattr(messages_mod, "_tenant_session", lambda: session)
    with pytest.raises(HTTPException) as caught:
        await messages_mod.search_messages(q=None, limit=50, hybrid=False, user=_member(),
                                           **kwargs)
    assert caught.value.status_code == 422 and caught.value.detail == detail
    assert session.statements == []


# ── Done-when 3 (R5): no acting member, no gateway call ─────────────────────


async def test_a_run_with_no_member_makes_no_gateway_call(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """The real ``_get``, ``_request`` and ``_headers``, over a recording
    transport. With no member bound, ``_headers`` refuses before a request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row(1)])

    def client(*_a: Any, **kw: Any) -> httpx.AsyncClient:
        return _REAL_CLIENT(
            transport=httpx.MockTransport(handler), timeout=kw.get("timeout", 5.0),
        )

    monkeypatch.setattr(agents.httpx, "AsyncClient", client)
    _flag(monkeypatch, AGENT)
    tool = agents._narrow_tool()
    assert tool is not None
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id

    binding = _bind_memory_user_id("")
    try:
        out = await tool(QUERY)
    finally:
        _unbind_memory_user_id(binding)
    assert out == narrowing.SEARCH_FAILED.format(source="whatsapp")
    assert seen == [] and door.bodies == []

    door.rule = lambda n: ("no", 0.95)
    binding = _bind_memory_user_id(MEMBER)
    try:
        await tool(QUERY)
    finally:
        _unbind_memory_user_id(binding)
    assert seen and all(r.headers["X-User-Email"] == MEMBER for r in seen)
    assert seen[0].url.path == ns.SEARCH_PATH


def test_the_tool_takes_no_member_and_no_org() -> None:
    """§5 item 3: the identity comes from the run, never from an argument."""
    tool = narrowing.make_narrow_tool(ns.WhatsAppNarrowSource(get=Gateway([]).get))
    assert list(inspect.signature(tool).parameters) == ["query", "filters", "dropped_of"]


# ── Done-when 4 (WS48-F5): no session, and the scan sees this adapter ───────


def test_the_adapter_opens_no_session_and_the_scan_reads_it() -> None:
    from tests.unit import test_narrowing_one_seam as seam

    adapter = AGENT_DIR / "narrow_source.py"
    assert adapter in seam._py_files(REPO)
    assert seam.adapters_with_a_session(REPO) == []
    assert seam.direct_builds(REPO) == []
    assert seam.holders_without_the_line(REPO) == []


# ── The flag, the instructions and the egress control ───────────────────────


def test_the_flag_off_means_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    assert agents._narrow_tool() is None
    assert narrowing.TOOL_NAME not in _tool_names(_built(monkeypatch))


def test_a_flag_for_another_agent_means_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "crm-assistant, email-assistant")
    assert agents._narrow_tool() is None
    assert narrowing.TOOL_NAME not in _tool_names(_built(monkeypatch))


@pytest.mark.parametrize("value", [AGENT, f"email-assistant,{AGENT}", "*"])
def test_the_flag_on_builds_the_tool(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _flag(monkeypatch, value)
    assert _tool_names(_built(monkeypatch)).count(narrowing.TOOL_NAME) == 1


async def test_the_tool_refuses_when_the_flag_goes_off(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(1)])
    tool = _tool(monkeypatch, gateway)
    _flag(monkeypatch, "")
    assert await tool(QUERY) == narrowing.OFF_ANSWER
    assert gateway.calls == [] and door.bodies == []


def test_the_instructions_name_the_tool_only_when_it_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = _built(monkeypatch).default_options["instructions"]
    assert narrowing.TOOL_NAME not in off and "dropped_of" not in off
    assert "<!--" not in off and "## A question over many messages" not in off
    assert off == agents.INSTRUCTIONS
    assert "## Hard rules" in off  # the rest of the text stays

    _flag(monkeypatch, AGENT)
    on = _built(monkeypatch).default_options["instructions"]
    assert narrowing.INSTRUCTION_LINE in on
    assert "call\n`narrow_and_read` one time" in on
    assert "Never read many chats one by one." in on
    assert "<!--" not in on
    assert set(off.splitlines()) <= set(on.splitlines())
    assert len(on) > len(off)


def test_the_instruction_file_holds_the_block_once() -> None:
    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    assert text.count(agents._NARROW_START) == 1 and text.count(agents._NARROW_END) == 1
    block = text[text.index(agents._NARROW_START):text.index(agents._NARROW_END)]
    assert narrowing.INSTRUCTION_LINE in block
    # Each filter key that the block teaches is a real key of the adapter.
    for key in ("after", "before", "chat_id", "contact", "group", "from_me",
                "has_media", "words"):
        assert f"`{key}`" in block or f'"{key}"' in block, key
        assert key in ns.FILTER_KEYS
    assert narrowing.TOOL_NAME not in text.replace(block, "")


def test_the_built_tool_is_a_platform_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-236: the tool stays in a ``no_egress`` run, as a platform read."""
    _flag(monkeypatch, AGENT)
    agent = _built(monkeypatch)
    tool = next(t for t in agent.default_options["tools"] if t.name == narrowing.TOOL_NAME)
    assert eg.has_explicit_open_world(tool)
    assert eg.is_egress_tool(tool) is False
    assert eg._platform_owned(tool, narrowing.TOOL_NAME) is True
    assert getattr(getattr(tool, "func", tool), "__tool_risk__", None) == narrowing.NARROW_RISK


def test_the_scope_names_the_tool() -> None:
    """Else ``_apply_own_tool_scope`` takes the tool away from a run with the
    flag on (``test_own_tool_scope_parity.py`` holds the full rule)."""
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert narrowing.TOOL_NAME in config["own_tool_scope"]


async def test_a_no_egress_run_sends_no_decide_request(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """Q4: a covered run asks System 1 only, never the decide door."""
    asked: list[int] = []

    async def fast(_context: str, items: list[Any], **_k: Any) -> list[Any]:
        asked.append(len(items))
        return [SimpleNamespace(id=i.id, choice="yes", confidence=0.9) for i in items]

    monkeypatch.setattr(system_one, "ask", fast)
    gateway = Gateway([_row(n) for n in range(1, 4)])
    tool = _tool(monkeypatch, gateway)
    with artifact_context_scope():
        bind_artifact_context(session_id=THREAD, agent_name=AGENT,
                              run_id="run-whatsapp-narrow-2", member=MEMBER, no_egress=True)
        out = await tool(QUERY)
    assert out.startswith("Checked 3 matches. Kept 3, dropped 0. Read 3 in full.")
    assert door.bodies == [] and asked == [3]
