"""The email adapter of the narrowing pipeline, and its tool in email-assistant. WS-48 N2.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.2, §3.6, §4, §5 and
§9 N2 (done-when items 1 to 4). The adapter is
``apps/agents/agent-email-assistant/narrow_source.py``.

The tool tests run the REAL tool (``narrow_tool_for``), the REAL adapter on
the REAL agent module, and the REAL decide facade and Console client. Only the
HTTP transport under the Console client is a script (:class:`Door`), and the
agent's ``_get`` is a recorder (:class:`Gateway`). So a body that a test reads
is the body that the Router's ``POST /v1/decide`` would receive.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each one run red before the change:

* NARROW copies ``body_text`` or ``body_html`` into a summary, or drops
  ``light=true`` -> ``test_narrow_sends_no_full_body_on_the_pick_wire`` and
  ``test_the_fixed_parameters_are_light_and_hybrid``;
* a filter maps to a parameter that ``GET /email/search`` does not declare
  (for example ``from_email``, the parameter of ``/email/messages``) ->
  ``test_each_target_is_a_real_parameter_of_the_route``;
* a key maps to the wrong parameter, or a value goes out in the wrong form ->
  ``test_each_filter_key_maps_to_its_parameter``;
* an unknown key, or a bad date or flag, passes in silence ->
  ``test_an_unknown_filter_key_is_refused_by_name`` and
  ``test_a_bad_value_is_refused_by_name_and_sends_nothing``;
* the search ANDs the words of the question, or keeps an operator word ->
  ``test_with_no_words_the_search_is_any_word_of_the_question``;
* READ reads an item that was dropped, more than 25 items, or a body past
  6000 characters -> ``test_read_fetches_only_the_kept_items_within_the_caps``
  and ``test_the_adapter_reads_at_most_the_read_cap``;
* a non-UUID id reaches a request path -> ``test_read_puts_only_a_uuid_in_a_path``;
* more than 200 rows reach PICK -> ``test_at_most_200_candidates_reach_pick``;
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
from collections.abc import Callable, Mapping
from pathlib import Path
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
AGENT_DIR = REPO / "apps" / "agents" / "agent-email-assistant"
AGENT = "email-assistant"
MEMBER = "member@narrow.test"
ORG = "org-email-narrow"
THREAD = "thread-email-narrow-1"
QUERY = "Which customers asked about pricing this month?"
BODY_CANARY = "FULLBODY-CANARY-5521 the whole body of the mail"
HTML_CANARY = "HTMLBODY-CANARY-8812"
#: The real client class, held before any test patches ``httpx.AsyncClient``.
_REAL_CLIENT = httpx.AsyncClient


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agents = _load("ws48_n2_email_agents", "agents.py")
ns = _load("ws48_n2_email_narrow_source", "narrow_source.py")


def mid(n: int) -> str:
    """A stable message id: a UUID, as the route gives."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ws48-n2/{n}"))


def _row(n: int, **extra: Any) -> dict[str, Any]:
    """One row of ``GET /email/search``, with a full body that must not travel."""
    row = {
        "id": mid(n),
        "subject": f"Pricing question {n}",
        "from_address": {"name": f"Buyer {n}", "email": f"buyer{n}@customer.test"},
        "received_at": "2026-10-01T10:00:00+00:00",
        "snippet": f"Could you send pricing for item {n}",
        "highlight": "",
        "body_text": BODY_CANARY,
        "body_html": f"<p>{HTML_CANARY}</p>",
    }
    row.update(extra)
    return row


class Gateway:
    """A recorder at the agent's ``_get``: the search and the message route."""

    def __init__(self, rows: list[dict[str, Any]], *, total: int | None = None) -> None:
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.body = "B" * 9000
        self.fail_read: set[str] = set()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        if path == ns.SEARCH_PATH:
            return {"emails": self.rows, "total": self.total}
        prefix = "/email/messages/"
        if path.startswith(prefix):
            message_id = path[len(prefix):]
            if message_id in self.fail_read:
                raise agents.GatewayError("Email GET failed (404)", 404)
            row = next(r for r in self.rows if r["id"] == message_id)
            return {**row, "body_text": f"{self.body} ({message_id})"}
        raise AssertionError(f"an unexpected route: {path}")

    @property
    def searches(self) -> list[dict[str, Any]]:
        return [p for path, p in self.calls if path == ns.SEARCH_PATH]

    @property
    def reads(self) -> list[str]:
        prefix = "/email/messages/"
        return [path[len(prefix):] for path, _p in self.calls if path.startswith(prefix)]


class Door:
    """The HTTP transport under the Console client. It answers each question
    by the message that it asks about, with :attr:`rule`."""

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
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.email-narrow.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ws48-n2-dummy")
    monkeypatch.delenv("NARROWING_AGENTS", raising=False)
    get_settings.cache_clear()
    narrowing._DROPPED.clear()
    clear_run_context()
    bind_run_context(
        run_id="run-email-narrow-1", thread_id=THREAD, agent=AGENT, user=MEMBER,
        source="chat", app="email", member_verified=True,
    )
    token = bind_tenant(ORG)
    with artifact_context_scope():
        bind_artifact_context(
            session_id=THREAD, agent_name=AGENT, run_id="run-email-narrow-1",
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


# ── Done-when 1: the filters map to the route's real parameters ─────────────


def _route_parameters() -> dict[str, inspect.Parameter]:
    from gateway.routes.email.transport.search import search_messages

    return dict(inspect.signature(search_messages).parameters)


def test_each_target_is_a_real_parameter_of_the_route() -> None:
    """Read from the route's own signature, never from a hand list."""
    route = _route_parameters()
    sent = set(ns.PARAMS.values()) | set(ns.FIXED_PARAMS) | {"q", "folder"}
    assert sent - set(route) == set(), sorted(sent - set(route))
    # The page size is inside the route's own bound.
    bound = [m.le for m in route["page_size"].default.metadata if hasattr(m, "le")]
    assert bound and bound[0] >= narrowing.MAX_CANDIDATES


def test_the_filter_keys_are_the_spec_keys() -> None:
    """§4, and two keys of N2: ``unread`` and the search ``words``."""
    keys = ns.FILTER_KEYS
    assert keys == frozenset({
        "account_id", "folder", "labels", "from", "to", "after", "before",
        "has_attachments", "sender_category", "unread", "words",
    })


def test_the_fixed_parameters_are_light_and_hybrid() -> None:
    params = ns.search_params(QUERY, {})
    assert params["light"] == "true" and params["hybrid"] == "true"
    assert params["page_size"] == "200" and params["page"] == "1"
    assert params["folder"] == ns.DEFAULT_FOLDER == "all"


@pytest.mark.parametrize(("filters", "expected"), [
    ({"account_id": "8C7D3E2A-1B4F-4C5D-9E6F-7A8B9C0D1E2F"},
     {"account_id": "8c7d3e2a-1b4f-4c5d-9e6f-7a8b9c0d1e2f"}),
    ({"folder": "sent"}, {"folder": "sent"}),
    ({"labels": "Customers"}, {"labels": ["Customers"]}),
    ({"labels": ["Customers", "Reply"]}, {"labels": ["Customers", "Reply"]}),
    ({"from": "acme"}, {"from_addr": "acme"}),
    ({"to": "sales@narrow.test"}, {"to_addr": "sales@narrow.test"}),
    ({"after": "2026-09-01"}, {"received_after": "2026-09-01T00:00:00+00:00"}),
    ({"before": "2026-09-30T18:00:00Z"}, {"received_before": "2026-09-30T18:00:00+00:00"}),
    ({"has_attachments": True}, {"has_attachments": "true"}),
    ({"has_attachments": "false"}, {"has_attachments": "false"}),
    ({"unread": True}, {"is_read": "false"}),
    ({"unread": False}, {"is_read": "true"}),
    ({"sender_category": "Marketing"}, {"sender_category": "Marketing"}),
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
    out = await _tool(monkeypatch, gateway)(QUERY, filters=json.dumps({"from_email": "acme"}))
    assert out.startswith("narrow_and_read: unknown filter key: from_email.")
    assert "from" in out and "words" in out
    assert gateway.calls == [] and door.bodies == []
    with pytest.raises(narrowing.FilterRefused, match="from_email"):
        ns.search_params(QUERY, {"from_email": "acme"})


@pytest.mark.parametrize(("filters", "named"), [
    ({"after": "last week"}, "the filter after must be a date"),
    ({"before": "2026-13-01"}, "the filter before must be a date"),
    ({"has_attachments": "yes"}, "the filter has_attachments must be true or false"),
    ({"unread": 1}, "the filter unread must be true or false"),
    ({"account_id": "../../admin/members"}, "the filter account_id must be the id"),
    ({"labels": []}, "the filter labels must be a text"),
    ({"from": ""}, "the filter from must be a text"),
])
async def test_a_bad_value_is_refused_by_name_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, door: Door, filters: dict[str, Any], named: str,
) -> None:
    """The route drops a date that it cannot parse in silence
    (``search._parse_dt``). So the adapter refuses it, and sends nothing."""
    gateway = Gateway([_row(1)])
    out = await _tool(monkeypatch, gateway)(QUERY, filters=json.dumps(filters))
    assert out.startswith(f"narrow_and_read: {named}"), out
    assert gateway.calls == [] and door.bodies == []


# ── The search text (§2.3: recall is lexical) ───────────────────────────────


def test_with_no_words_the_search_is_any_word_of_the_question() -> None:
    assert ns.search_text(QUERY, {}) == (
        "which OR customers OR asked OR about OR pricing OR this OR month"
    )
    # An operator word of the question never joins or negates two words.
    assert ns.search_text("Acme or Beta, and not Gamma", {}) == "acme OR beta OR gamma"
    assert ns.search_text("-- ?", {}) == ""


def test_words_set_the_search_and_an_empty_words_uses_the_filters_only() -> None:
    words = "pricing OR price OR quote OR rates"
    assert ns.search_params(QUERY, {"words": words})["q"] == words
    assert "q" not in ns.search_params(QUERY, {"words": "", "from": "acme"})


# ── Done-when 2 and §3.2: no full body in NARROW, and at most 200 ──────────


async def test_narrow_sends_no_full_body_on_the_pick_wire(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """The route ignores ``light`` here and sends every body. No byte of a
    body reaches a PICK request."""
    gateway = Gateway([_row(n) for n in range(1, 21)])
    door.rule = lambda n: ("no", 0.95)  # drop all: READ reads nothing
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 20 matches. Kept 0, dropped 20. Read 0 in full.")
    assert gateway.searches[0]["light"] == "true"
    wire = json.dumps(door.bodies)
    assert door.bodies and BODY_CANARY not in wire and HTML_CANARY not in wire
    for body in door.bodies:
        for item in body["state"]["items"].values():
            assert set(item) == {"title", "who", "when", "snippet"}
    assert gateway.reads == []


def test_a_summary_reads_no_body_field() -> None:
    c = ns.candidate_of(_row(3, snippet="", highlight="a <mark>pricing</mark> line"))
    assert c.snippet == "a pricing line"
    assert c.who == "Buyer 3 <buyer3@customer.test>"
    for field in (c.id, c.title, c.who, c.when, c.snippet):
        assert BODY_CANARY not in field and HTML_CANARY not in field


async def test_at_most_200_candidates_reach_pick(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(n) for n in range(1, 251)], total=612)
    door.rule = lambda n: ("no", 0.95)
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 200 of 612 matches. Kept 0, dropped 200.")
    assert sum(len(b["questions"]) for b in door.bodies) == 200
    assert max(len(b["questions"]) for b in door.bodies) == 16


# ── §3.6: READ reads only the kept items, within the caps ───────────────────


async def test_read_fetches_only_the_kept_items_within_the_caps(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    rows = [_row(n) for n in range(1, 61)]
    gateway = Gateway(rows)
    kept = {n for n in range(1, 61) if n % 2 == 0}  # 30 kept, 30 dropped
    door.rule = lambda n: ("yes", 0.9) if n in kept else ("no", 0.9)
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 60 matches. Kept 30, dropped 30. Read 25 in full.")
    in_rank = [mid(n) for n in range(1, 61) if n in kept]
    assert gateway.reads == in_rank[:narrowing.READ_CAP]
    for message_id in gateway.reads:
        assert int(next(r for r in rows if r["id"] == message_id)["subject"].split()[-1]) in kept
    # Each body is clipped at 6000 characters.
    assert "B" * narrowing.BODY_CLIP in out and "B" * (narrowing.BODY_CLIP + 1) not in out
    assert out.count("--- item ") == narrowing.READ_CAP


async def test_a_failed_read_is_counted_not_hidden(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    gateway = Gateway([_row(n) for n in range(1, 4)])
    gateway.fail_read = {mid(2)}
    out = await _tool(monkeypatch, gateway)(QUERY)
    assert out.startswith("Checked 3 matches. Kept 3, dropped 0. Read 2 in full.")
    assert "Not read in full: 1 kept items." in out
    assert f"--- item {mid(2)}" not in out


async def test_the_adapter_reads_at_most_the_read_cap() -> None:
    """The tool passes at most 25 ids. The adapter holds the cap too."""
    gateway = Gateway([_row(n) for n in range(1, 41)])
    source = ns.EmailNarrowSource(get=gateway.get)
    got = await source.read([mid(n) for n in range(1, 41)])
    assert gateway.reads == [mid(n) for n in range(1, narrowing.READ_CAP + 1)]
    assert [i.id for i in got] == gateway.reads
    assert all(len(i.text) == narrowing.BODY_CLIP for i in got)


async def test_read_puts_only_a_uuid_in_a_path() -> None:
    gateway = Gateway([_row(1)])
    source = ns.EmailNarrowSource(get=gateway.get)
    got = await source.read(["../../admin/members", mid(1).upper(), "not-an-id"])
    assert gateway.reads == [mid(1)]
    assert [i.id for i in got] == [mid(1).upper()]  # the id the tool asked with
    many = await source.read([mid(1)] * 3 + ["x"] * 40)
    assert len(gateway.reads) == 2 and len(many) == 3


# ── Done-when 3 (R5): no acting member, no gateway call ─────────────────────


async def test_a_run_with_no_member_makes_no_gateway_call(
    monkeypatch: pytest.MonkeyPatch, door: Door,
) -> None:
    """The real ``_get``, ``_request`` and ``_headers``, over a recording
    transport. With no member bound, ``_headers`` refuses before a request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"emails": [_row(1)], "total": 1})

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
    assert out == narrowing.SEARCH_FAILED.format(source="email")
    assert seen == [] and door.bodies == []

    binding = _bind_memory_user_id(MEMBER)
    try:
        await tool(QUERY, filters=json.dumps({"words": ""}))
    finally:
        _unbind_memory_user_id(binding)
    assert seen and all(r.headers["X-User-Email"] == MEMBER for r in seen)
    assert seen[0].url.path == ns.SEARCH_PATH


def test_the_tool_takes_no_member_and_no_org() -> None:
    """§5 item 3: the identity comes from the run, never from an argument."""
    tool = narrowing.make_narrow_tool(ns.EmailNarrowSource(get=Gateway([]).get))
    assert list(inspect.signature(tool).parameters) == ["query", "filters", "dropped_of"]
    assert list(inspect.signature(ns.EmailNarrowSource).parameters) == ["get"]


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
    agent = _built(monkeypatch)
    assert narrowing.TOOL_NAME not in _tool_names(agent)


def test_a_flag_for_another_agent_means_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "crm-assistant, whatsapp-assistant")
    assert agents._narrow_tool() is None
    assert narrowing.TOOL_NAME not in _tool_names(_built(monkeypatch))


@pytest.mark.parametrize("value", [AGENT, f"crm-assistant,{AGENT}", "*"])
def test_the_flag_on_builds_the_tool(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _flag(monkeypatch, value)
    names = _tool_names(_built(monkeypatch))
    assert names.count(narrowing.TOOL_NAME) == 1


def test_the_instructions_name_the_tool_only_when_it_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = _built(monkeypatch).default_options["instructions"]
    assert narrowing.TOOL_NAME not in off and "dropped_of" not in off
    assert "<!--" not in off and "## A question over many emails" not in off
    assert off == agents.INSTRUCTIONS
    assert "## Answering inbox questions" in off  # the rest of the text stays

    _flag(monkeypatch, AGENT)
    on = _built(monkeypatch).default_options["instructions"]
    assert narrowing.INSTRUCTION_LINE in on
    assert "call `narrow_and_read` one time" in on
    assert "Never read many emails one by one." in on
    assert "<!--" not in on
    # Nothing else changed: each line of the text with no tool is in the other.
    assert set(off.splitlines()) <= set(on.splitlines())
    assert len(on) > len(off)


def test_the_instruction_file_holds_the_block_once() -> None:
    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    assert text.count(agents._NARROW_START) == 1 and text.count(agents._NARROW_END) == 1
    block = text[text.index(agents._NARROW_START):text.index(agents._NARROW_END)]
    assert narrowing.INSTRUCTION_LINE in block
    outside = text.replace(block, "")
    assert narrowing.TOOL_NAME not in outside


def test_the_built_tool_is_a_platform_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-236: the tool stays in a ``no_egress`` run, as a platform read."""
    _flag(monkeypatch, AGENT)
    agent = _built(monkeypatch)
    tool = next(t for t in agent.default_options["tools"] if t.name == narrowing.TOOL_NAME)
    assert eg.has_explicit_open_world(tool)
    assert eg.is_egress_tool(tool) is False
    assert eg._platform_owned(tool, narrowing.TOOL_NAME) is True
    risk = getattr(getattr(tool, "func", tool), "__tool_risk__", None)
    assert risk == narrowing.NARROW_RISK


def test_the_scope_names_the_tool() -> None:
    """Else ``_apply_own_tool_scope`` takes the tool away from a run with the
    flag on (``test_own_tool_scope_parity.py`` holds the full rule)."""
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert narrowing.TOOL_NAME in config["own_tool_scope"]


async def test_a_refused_value_is_text_on_the_seam(door: Door) -> None:
    """``FilterRefused`` reaches the model as text, not as "could not search"."""

    class Refusing:
        name = "x"
        filter_keys = frozenset({"after"})

        async def candidates(self, query: str, filters: Mapping[str, Any]) -> Any:
            raise narrowing.FilterRefused("the filter after must be a date.")

        async def read(self, ids: Any) -> list[Any]:
            return []

    out = await narrowing.make_narrow_tool(Refusing())(QUERY, filters='{"after": "x"}')
    assert out == "narrow_and_read: the filter after must be a date."
