"""The data narrowing pipeline: PICK, the keep rule and the tool. WS-48 N1.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3, §6 and §9 N1.
Fences WS48-F1, WS48-F2 and WS48-F3 (§8).

The decide requests go through the REAL facade ``acb_llm.decide`` and the
REAL Console client ``console_resolve.decide_on_console``. Only the HTTP
transport under the client is a script (:class:`Door`). So a body a test
reads is the body the Router's ``POST /v1/decide`` would receive. System 1
is a recorder (:class:`SystemOne`) at ``system_one.ask``, the one seam that
the fallback calls. ``test_system_one_tool.py`` proves that seam on its own
wire.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each one run red before the change:

* ``keep`` drops an ``unsure``, a low ``no`` or a missing answer, or keeps a
  confident ``no`` -> ``TestKeep`` and ``test_the_keep_rule_on_the_wire``;
* the threshold moves off 0.70, or stops following the effort ->
  ``test_the_drop_threshold_is_the_auto_threshold`` and
  ``test_a_higher_effort_drops_fewer``;
* the batch is not 16 -> ``test_forty_candidates_are_three_requests_16_16_8``;
* the state holds a full body, an adapter id or a field past its clip ->
  ``test_the_state_holds_the_query_and_the_summaries_only``;
* an instruction copies a value -> ``test_no_instruction_copies_a_value``;
* the bound of 4 goes to 5, or to 1 -> ``test_at_most_four_requests_are_in_flight``;
* the whole step has no bound -> ``test_the_step_bound_keeps_every_item``;
* a failure class does not fall back, or the fallback takes more than its
  batch -> ``TestFallback``;
* a ``DecideRequestInvalid`` logs under ``error`` -> ``test_a_caller_bug_logs_at_error``;
* both engines fail and the items drop -> ``test_both_engines_fail_keeps_the_batch``;
* a ``no_egress`` run, or a frame with no run, asks the decide door ->
  ``TestNoEgress``;
* the candidate cap, the read cap or the body clip goes -> ``TestCaps``;
* a count of the count line is wrong -> ``TestCounts``;
* the dropped list leaks to another org, member or thread, or outlives 15
  minutes -> ``TestDropped``;
* the tool reaches an agent that the flag does not name -> ``TestFlag``;
* an unknown filter key passes in silence -> ``test_an_unknown_filter_key_is_refused_by_name``;
* a log line holds tenant text -> ``test_the_logs_hold_no_tenant_text``;
* the tool reads as an egress tool, or is not the platform's own ->
  ``test_the_tool_is_a_platform_tool_with_no_egress``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
import pytest
import structlog
from acb_auth import console_resolve
from acb_common import bind_run_context, clear_run_context
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_skills import egress as eg
from acb_skills import narrowing, system_one, tier_policy
from acb_skills.narrowing import Candidate, FullItem, Narrowed, Verdict
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

AGENT = "email-assistant"
MEMBER = "member@example.com"
ORG = "org-narrow-a"
THREAD = "thread-narrow-1"
QUERY = "Which customers asked about pricing this month?"
#: Tenant text in a full body. It must never reach a PICK request or a log.
BODY_CANARY = "FULLBODY-CANARY-7731 the whole quote for Acme Corp"
SNIPPET_CANARY = "SNIPPET-CANARY-4410"


# ── Fixtures ────────────────────────────────────────────────────────────────


class Door:
    """The HTTP transport under the Console client. It answers each question
    by the candidate it asks about, with :attr:`rule`."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay = 0.0
        #: candidate id -> (choice, probability). The default is a sure yes.
        self.rule: Callable[[str], tuple[str, float]] = lambda _cid: ("yes", 0.95)
        #: candidate ids whose batch gets this (status, body) instead.
        self.fail: dict[str, tuple[int, dict[str, Any]]] = {}

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        ids = {k: _cid(v) for k, v in body["state"]["items"].items()}
        for cid in ids.values():
            if cid in self.fail:
                status, payload = self.fail[cid]
                return httpx.Response(status, json=payload)
        answers = {}
        for qid in body["questions"]:
            choice, p = self.rule(ids[qid])
            answers[qid] = {
                "type": "choice", "choice": choice, "confidence": p,
                "probabilities": {choice: p},
            }
        return httpx.Response(200, json={
            "tier": "tier-decide", "answers": answers,
            "request_id": f"req-{len(self.bodies)}",
        })

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=5.0)

    @property
    def sizes(self) -> list[int]:
        return sorted(len(b["questions"]) for b in self.bodies)


class SystemOne:
    """A recorder at ``system_one.ask``. It answers like :class:`Door`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[system_one.Item]]] = []
        self.rule: Callable[[str], tuple[str, float]] = lambda _cid: ("yes", 0.95)
        self.error: Exception | None = None

    async def ask(
        self, context: str, items: list[system_one.Item], *, timeout_s: float = 3.0,
    ) -> list[system_one.Answer]:
        self.calls.append((context, list(items)))
        if self.error is not None:
            raise self.error
        state = json.loads(context)
        out = []
        for item in items:
            choice, p = self.rule(_cid(state["items"][item.id]))
            out.append(system_one.Answer(item.id, choice, p, ""))
        return out

    @property
    def sizes(self) -> list[int]:
        return sorted(len(items) for _c, items in self.calls)


def _cid(summary: Mapping[str, Any]) -> str:
    """The candidate id, which the test adapter writes into the title."""
    return str(summary["title"]).split()[-1]


class Adapter:
    """A test source of *n* candidates, ``m1`` to ``mn``, in rank order."""

    name = "test"
    filter_keys = frozenset({"folder", "after"})

    def __init__(self, n: int, *, total: int | None = None, text: str = BODY_CANARY) -> None:
        self.n = n
        self.total = n if total is None else total
        self.text = text
        self.searched: list[tuple[str, dict[str, Any]]] = []
        self.read_ids: list[list[str]] = []

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        self.searched.append((query, dict(filters)))
        return Narrowed(
            candidates=[
                Candidate(
                    id=f"m{i}", title=f"Quote request m{i}", who=f"sender{i}@acme.test",
                    when="2026-10-01", snippet=f"{SNIPPET_CANARY} rates for item {i}",
                )
                for i in range(1, self.n + 1)
            ],
            total=self.total,
        )

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        self.read_ids.append(list(ids))
        return [
            FullItem(id=i, title=f"Quote request {i}", who="sender@acme.test",
                     when="2026-10-01", text=f"{self.text} ({i})")
            for i in ids
        ]


@pytest.fixture
def door(monkeypatch) -> Door:
    fake = Door()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


@pytest.fixture
def s1(monkeypatch) -> SystemOne:
    fake = SystemOne()
    monkeypatch.setattr(system_one, "ask", fake.ask)
    return fake


@pytest.fixture(autouse=True)
def _box(monkeypatch):
    """A box with the decide door on and wired, and one bound open run."""
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.narrow.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    monkeypatch.delenv("NARROWING_AGENTS", raising=False)
    get_settings.cache_clear()
    narrowing._DROPPED.clear()
    clear_run_context()
    bind_run_context(
        run_id="run-narrow-1", thread_id=THREAD, agent=AGENT, user=MEMBER,
        source="chat", app="email", member_verified=True,
    )
    token = bind_tenant(ORG)
    with artifact_context_scope():
        _bind(no_egress=False)
        yield
    release_tenant(token)
    clear_run_context()
    narrowing._DROPPED.clear()
    get_settings.cache_clear()


def _bind(**extra: Any) -> None:
    bind_artifact_context(
        session_id=THREAD, agent_name=AGENT, run_id="run-narrow-1", member=MEMBER, **extra,
    )


def _tool(adapter: Any) -> Callable[..., Any]:
    return narrowing.make_narrow_tool(adapter)


def _first_line(out: str) -> str:
    return out.splitlines()[0]


# ── WS48-F1: the keep rule ──────────────────────────────────────────────────


class TestKeep:
    @pytest.mark.parametrize(("answer", "kept"), [
        (Verdict("yes", 0.01), True),
        (Verdict("yes", 1.0), True),
        (Verdict("unsure", 0.01), True),
        (Verdict("unsure", 1.0), True),
        (Verdict("no", 0.0), True),
        (Verdict("no", 0.69), True),
        (Verdict("no", 0.6999), True),
        (Verdict("no", 0.70), False),
        (Verdict("no", 0.95), False),
        (None, True),
    ])
    def test_only_a_confident_no_drops(self, answer: Verdict | None, kept: bool) -> None:
        assert narrowing.keep(answer) is kept

    def test_the_drop_threshold_is_the_auto_threshold(self) -> None:
        assert narrowing.DROP_THRESHOLD == 0.70
        assert narrowing.DROP_THRESHOLD == tier_policy.SYSTEM_ONE_THRESHOLDS["auto"]

    def test_a_threshold_argument_moves_the_line(self) -> None:
        assert narrowing.keep(Verdict("no", 0.85), threshold=0.90) is True
        assert narrowing.keep(Verdict("no", 0.90), threshold=0.90) is False


async def test_the_keep_rule_on_the_wire(door: Door, s1: SystemOne) -> None:
    rules = {
        "m1": ("yes", 0.90), "m2": ("unsure", 0.99), "m3": ("no", 0.69),
        "m4": ("no", 0.70), "m5": ("no", 0.95), "m6": ("maybe", 0.99),
    }
    door.rule = lambda cid: rules[cid]
    out = await _tool(Adapter(6))(QUERY)
    assert _first_line(out) == (
        "Checked 5 matches. Kept 4, dropped 2, and 1 were not checked (kept). "
        "Read 4 in full."
    )
    assert "(m1)" in out and "(m2)" in out and "(m3)" in out and "(m6)" in out
    assert "(m4)" not in out and "(m5)" not in out
    assert s1.calls == [], "a sure answer fell back"


async def test_a_higher_effort_drops_fewer(door: Door, s1: SystemOne) -> None:
    """§3.4: Max reads 0.90, so a ``no`` at 0.85 stays."""
    door.rule = lambda cid: ("no", 0.85)
    _bind(no_egress=False, think_mode="max")
    out = await _tool(Adapter(3))(QUERY)
    assert _first_line(out).startswith("Checked 3 matches. Kept 3, dropped 0.")
    _bind(no_egress=False, think_mode="auto")
    out = await _tool(Adapter(3))(QUERY)
    assert _first_line(out).startswith("Checked 3 matches. Kept 0, dropped 3.")


# ── WS48-F3: the request on the wire ────────────────────────────────────────


async def test_forty_candidates_are_three_requests_16_16_8(door: Door, s1: SystemOne) -> None:
    """Done-when 1."""
    await _tool(Adapter(40))(QUERY)
    assert door.sizes == [8, 16, 16]
    for body in door.bodies:
        assert body["tier"] == "tier-decide"
        assert set(body["questions"]) == set(body["state"]["items"])
        for q in body["questions"].values():
            assert q["type"] == "choice"
            assert set(q["criteria"]) == {"yes", "no", "unsure"}
    assert s1.calls == []


async def test_the_state_holds_the_query_and_the_summaries_only(
    door: Door, s1: SystemOne,
) -> None:
    long = Adapter(20)
    original = long.candidates

    async def long_fields(query: str, filters: Mapping[str, Any]) -> Narrowed:
        got = await original(query, filters)
        first = got.candidates[0]
        swollen = Candidate(
            id=first.id, title="x" * 500 + " m1", who="w" * 500, when="d" * 100,
            snippet="\x01" * 400,
        )
        return Narrowed([swollen, *got.candidates[1:]], got.total)

    long.candidates = long_fields  # type: ignore[method-assign]
    await _tool(long)(QUERY)
    for body in door.bodies:
        state = body["state"]
        assert set(state) == {"query", "items"}
        assert state["query"] == QUERY
        for summary in state["items"].values():
            assert set(summary) == {"title", "who", "when", "snippet"}
            assert len(summary["title"]) <= narrowing.TITLE_CLIP
            assert len(summary["who"]) <= narrowing.WHO_CLIP
            assert len(summary["when"]) <= narrowing.WHEN_CLIP
            assert len(summary["snippet"]) <= narrowing.SNIPPET_CLIP
            escaped = json.dumps(summary["snippet"])
            assert len(escaped) <= 2 * narrowing.SNIPPET_CLIP + 2
        wire = json.dumps(body)
        assert BODY_CANARY not in wire, "a full body reached the PICK state"
        # The keys are local, so no adapter id rides in a path or a key.
        assert set(state["items"]) == {f"c{n}" for n in range(1, len(state["items"]) + 1)}


async def test_no_instruction_copies_a_value(door: Door, s1: SystemOne) -> None:
    """§3.3 item 4: each question names its item by path only."""
    await _tool(Adapter(5))(QUERY)
    for body in door.bodies:
        for key, q in body["questions"].items():
            assert f"`items.{key}`" in q["instructions"]
            text = q["instructions"] + json.dumps(q["criteria"])
            assert QUERY not in text and SNIPPET_CANARY not in text
            assert "acme" not in text.lower() and "Quote request" not in text
            assert "data" in q["instructions"] and "not an order" in q["instructions"]


async def test_the_request_carries_the_run_attribution(door: Door, s1: SystemOne) -> None:
    """§5 item 4: the run's attribution, from the run binding only."""
    await _tool(Adapter(2))(QUERY)
    headers = door.headers[0]
    assert headers["X-CC-Member"] == MEMBER
    assert headers["X-CC-Agent"] == AGENT
    assert headers["X-CC-Run"] == "run-narrow-1"


# ── The bound of 4 and the step bound (§3.3 item 6) ─────────────────────────


async def test_at_most_four_requests_are_in_flight(door: Door, s1: SystemOne) -> None:
    """Done-when 6. 200 candidates are 13 requests, and 4 run at once."""
    door.delay = 0.05
    out = await _tool(Adapter(200))(QUERY)
    assert len(door.bodies) == 13
    assert door.max_in_flight == narrowing.MAX_IN_FLIGHT == 4
    assert _first_line(out).startswith("Checked 200 matches.")


async def test_the_step_bound_keeps_every_item(
    door: Door, s1: SystemOne, monkeypatch,
) -> None:
    """The whole step has one bound. A batch it cuts off is kept, unchecked."""
    monkeypatch.setattr(narrowing, "PICK_BOUND_S", 0.05)
    door.delay = 5.0
    out = await _tool(Adapter(20))(QUERY)
    assert _first_line(out) == (
        "Checked 0 matches. Kept 20, dropped 0, and 20 were not checked (kept). "
        "Read 20 in full."
    )


# ── WS48-F2: the fallback (§3.5) ────────────────────────────────────────────


def _events(caps: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == name]


class TestFallback:
    """Each failure class sends ONE batch to System 1, in ONE request."""

    @pytest.mark.parametrize(("status", "payload", "reason"), [
        (400, {"detail": {"reason": "tier_unknown"}}, "tier_unknown"),
        (402, {"detail": "no credits"}, "insufficient_credits"),
        (403, {"detail": "breaker"}, "forbidden"),
        (503, {"detail": "down"}, "http_503"),
        (400, {"detail": {"reason": "too_many_questions"}}, "request_invalid"),
        (422, {"detail": [{"loc": ["body"]}]}, "request_invalid"),
    ])
    async def test_a_door_failure_falls_back_for_its_batch_only(
        self, door: Door, s1: SystemOne, status: int, payload: dict[str, Any], reason: str,
    ) -> None:
        """Done-when 3. The batch of m17 fails, and the others stay on decide."""
        door.fail = {"m17": (status, payload)}
        with structlog.testing.capture_logs() as caps:
            out = await _tool(Adapter(40))(QUERY)
        assert door.sizes == [8, 16, 16]
        assert s1.sizes == [16], "the fallback took more or less than its batch"
        assert {_cid(json.loads(c)["items"][i.id]) for c, items in s1.calls for i in items} == {
            f"m{n}" for n in range(17, 33)
        }
        lines = _events(caps, "narrowing.pick_fallback")
        assert [(e["reason"], e["batch_size"], e["request"]) for e in lines] == [
            (reason, 16, 2),
        ]
        assert _first_line(out).startswith("Checked 40 matches. Kept 40, dropped 0.")

    async def test_a_timeout_falls_back(self, door: Door, s1: SystemOne, monkeypatch) -> None:
        monkeypatch.setattr(narrowing, "REQUEST_TIMEOUT_S", 0.05)
        door.delay = 2.0
        with structlog.testing.capture_logs() as caps:
            out = await _tool(Adapter(10))(QUERY)
        assert s1.sizes == [10]
        assert [e["reason"] for e in _events(caps, "narrowing.pick_fallback")] == ["timeout"]
        assert _first_line(out).startswith("Checked 10 matches.")

    async def test_any_other_exception_falls_back(
        self, door: Door, s1: SystemOne, monkeypatch,
    ) -> None:
        import acb_llm

        async def broken(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("a facade fault")

        monkeypatch.setattr(acb_llm, "decide", broken)
        with structlog.testing.capture_logs() as caps:
            await _tool(Adapter(10))(QUERY)
        assert s1.sizes == [10]
        assert [e["reason"] for e in _events(caps, "narrowing.pick_fallback")] == [
            "RuntimeError",
        ]

    async def test_the_switch_off_falls_back_with_no_request(
        self, door: Door, s1: SystemOne, monkeypatch,
    ) -> None:
        monkeypatch.setenv("DECIDE_ENABLED", "false")
        get_settings.cache_clear()
        with structlog.testing.capture_logs() as caps:
            await _tool(Adapter(20))(QUERY)
        assert door.bodies == []
        assert s1.sizes == [4, 16]
        assert {e["reason"] for e in _events(caps, "narrowing.pick_fallback")} == {"disabled"}

    async def test_a_caller_bug_logs_at_error(self, door: Door, s1: SystemOne) -> None:
        """§3.5: a ``DecideRequestInvalid`` is a bug, and the fallback does
        not hide it. Status and reason CODE only."""
        door.fail = {"m1": (400, {"detail": {"reason": "window", "quote": SNIPPET_CANARY}})}
        with structlog.testing.capture_logs() as caps:
            await _tool(Adapter(3))(QUERY)
        bugs = _events(caps, "narrowing.decide_invalid")
        assert len(bugs) == 1
        assert bugs[0]["log_level"] == "error"
        assert (bugs[0]["decide_status"], bugs[0]["decide_reason"]) == (400, "window")
        assert SNIPPET_CANARY not in repr(caps)
        others = [e for e in _events(caps, "narrowing.pick_fallback")]
        assert others and others[0]["log_level"] == "warning"

    async def test_a_tier_unknown_is_not_a_caller_bug(self, door: Door, s1: SystemOne) -> None:
        door.fail = {"m1": (400, {"detail": {"reason": "tier_unknown"}})}
        with structlog.testing.capture_logs() as caps:
            await _tool(Adapter(3))(QUERY)
        assert _events(caps, "narrowing.decide_invalid") == []


async def test_both_engines_fail_keeps_the_batch(door: Door, s1: SystemOne) -> None:
    """Done-when 4: the 16 items of the batch are kept, and not checked."""
    door.fail = {"m1": (503, {})}
    s1.error = system_one.SystemOneUnavailable("router_off")
    out = await _tool(Adapter(40))(QUERY)
    assert _first_line(out) == (
        "Checked 24 matches. Kept 40, dropped 0, and 16 were not checked (kept). "
        "Read 25 in full."
    )


# ── WS48-F1: the no_egress guard (§4, Q4) ───────────────────────────────────


class TestNoEgress:
    async def test_a_no_egress_run_sends_no_decide_request(
        self, door: Door, s1: SystemOne,
    ) -> None:
        """Done-when 5."""
        _bind(no_egress=True)
        out = await _tool(Adapter(40))(QUERY)
        assert door.bodies == [], "a no_egress run reached the decide vendor"
        assert s1.sizes == [8, 16, 16]
        assert _first_line(out).startswith("Checked 40 matches.")

    async def test_a_frame_with_no_run_sends_no_decide_request(
        self, door: Door, s1: SystemOne,
    ) -> None:
        """The reader fails closed: no binding reads as ``no_egress``."""
        bind_artifact_context()
        await _tool(Adapter(5))(QUERY)
        assert door.bodies == []
        assert s1.sizes == [5]

    async def test_the_system_one_requests_hold_no_full_body(
        self, door: Door, s1: SystemOne,
    ) -> None:
        _bind(no_egress=True)
        await _tool(Adapter(5))(QUERY)
        for context, items in s1.calls:
            assert BODY_CANARY not in context
            assert set(json.loads(context)) == {"query", "items"}
            assert all(i.kind == "choice" and i.options == ("yes", "no", "unsure") for i in items)


# ── The caps (§3.2, §3.6, Q5) ───────────────────────────────────────────────


class TestCaps:
    async def test_at_most_200_candidates_are_checked(self, door: Door, s1: SystemOne) -> None:
        out = await _tool(Adapter(250))(QUERY)
        assert sum(len(b["questions"]) for b in door.bodies) == narrowing.MAX_CANDIDATES == 200
        assert _first_line(out).startswith("Checked 200 of 250 matches.")
        assert "More than 200 items matched." in out

    async def test_the_count_says_of_total_when_the_search_found_more(
        self, door: Door, s1: SystemOne,
    ) -> None:
        out = await _tool(Adapter(30, total=212))(QUERY)
        assert _first_line(out).startswith("Checked 30 of 212 matches.")

    async def test_at_most_25_items_are_read_in_rank_order(
        self, door: Door, s1: SystemOne,
    ) -> None:
        adapter = Adapter(40)
        out = await _tool(adapter)(QUERY)
        assert adapter.read_ids == [[f"m{n}" for n in range(1, 26)]]
        assert narrowing.READ_CAP == 25
        assert _first_line(out).endswith("Read 25 in full.")
        assert "Not read in full: 15 kept items." in out

    async def test_each_body_is_clipped_at_6000(self, door: Door, s1: SystemOne) -> None:
        out = await _tool(Adapter(1, text="b" * 10_000))(QUERY)
        assert narrowing.BODY_CLIP == 6000
        assert "b" * 6000 in out and "b" * 6001 not in out


# ── The counts of the answer (§6.1, done-when 7) ────────────────────────────


class TestCounts:
    async def test_all_kept(self, door: Door, s1: SystemOne) -> None:
        out = await _tool(Adapter(40))(QUERY)
        assert _first_line(out) == "Checked 40 matches. Kept 40, dropped 0. Read 25 in full."

    async def test_some_dropped(self, door: Door, s1: SystemOne) -> None:
        door.rule = lambda cid: ("no", 0.9) if int(cid[1:]) % 2 else ("yes", 0.9)
        out = await _tool(Adapter(40))(QUERY)
        assert _first_line(out) == "Checked 40 matches. Kept 20, dropped 20. Read 20 in full."
        assert 'dropped_of="n' in out.splitlines()[-1]

    async def test_one_fallback_keeps_the_counts(self, door: Door, s1: SystemOne) -> None:
        door.fail = {"m33": (400, {"detail": {"reason": "tier_unknown"}})}
        s1.rule = lambda cid: ("no", 0.99)
        out = await _tool(Adapter(40))(QUERY)
        assert _first_line(out) == "Checked 40 matches. Kept 32, dropped 8. Read 25 in full."

    async def test_nothing_matched(self, door: Door, s1: SystemOne) -> None:
        out = await _tool(Adapter(0))(QUERY)
        assert _first_line(out) == "Checked 0 matches. Kept 0, dropped 0. Read 0 in full."
        assert door.bodies == [] and s1.calls == []

    async def test_the_answers_never_reach_the_model(self, door: Door, s1: SystemOne) -> None:
        """§3.7: only the counts and the kept items reach the calling model."""
        out = await _tool(Adapter(3))(QUERY)
        assert out.splitlines()[2] == narrowing.LEAD
        assert "confidence" not in out and "0.95" not in out and "unsure" not in out
        assert "--- item m1 | 2026-10-01 | sender@acme.test ---" in out


# ── The dropped list (§6.3, done-when 8) ────────────────────────────────────


def _call_id(out: str) -> str:
    return out.splitlines()[-1].split('dropped_of="')[1].rstrip('"')


class TestDropped:
    async def _dropped(self, door: Door) -> tuple[Callable[..., Any], str]:
        door.rule = lambda cid: ("no", 0.9) if cid in {"m2", "m3"} else ("yes", 0.9)
        tool = _tool(Adapter(4))
        out = await tool(QUERY)
        return tool, _call_id(out)

    async def test_the_same_org_member_and_thread_get_the_list(
        self, door: Door, s1: SystemOne,
    ) -> None:
        tool, call_id = await self._dropped(door)
        out = await tool("", dropped_of=call_id)
        assert out.splitlines()[0] == narrowing.DROPPED_LEAD
        assert out.splitlines()[1:] == [
            "- m2 | 2026-10-01 | sender2@acme.test | Quote request m2",
            "- m3 | 2026-10-01 | sender3@acme.test | Quote request m3",
        ]
        assert len(door.bodies) == 1, "dropped_of ran the pipeline again"

    @pytest.mark.parametrize("other", ["org", "member", "thread"])
    async def test_any_other_key_finds_nothing(
        self, door: Door, s1: SystemOne, other: str,
    ) -> None:
        tool, call_id = await self._dropped(door)
        token = None
        if other == "org":
            token = bind_tenant("org-narrow-b")
        elif other == "member":
            bind_run_context(user="someone-else@example.com", member_verified=True)
        else:
            bind_artifact_context(session_id="thread-other", no_egress=False)
        try:
            assert await tool("", dropped_of=call_id) == narrowing.DROPPED_GONE
        finally:
            if token is not None:
                release_tenant(token)

    async def test_no_org_keeps_no_list(self, door: Door, s1: SystemOne) -> None:
        token = bind_tenant("")
        try:
            door.rule = lambda cid: ("no", 0.9)
            out = await _tool(Adapter(2))(QUERY)
        finally:
            release_tenant(token)
        assert "dropped_of" not in out
        assert narrowing._DROPPED == {}

    async def test_the_list_is_gone_after_15_minutes(
        self, door: Door, s1: SystemOne, monkeypatch,
    ) -> None:
        tool, call_id = await self._dropped(door)
        real = narrowing.time.monotonic
        monkeypatch.setattr(narrowing.time, "monotonic", lambda: real() + 15 * 60 + 1)
        assert await tool("", dropped_of=call_id) == narrowing.DROPPED_GONE

    async def test_a_bad_id_finds_nothing(self, door: Door, s1: SystemOne) -> None:
        tool, _ = await self._dropped(door)
        assert await tool("", dropped_of="n000000") == narrowing.DROPPED_GONE
        assert await tool("", dropped_of="../etc") == narrowing.DROPPED_GONE


# ── The flag (done-when 9) ──────────────────────────────────────────────────


class TestFlag:
    def test_the_flag_defaults_empty(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["narrowing_agents"].default == ""

    def test_with_the_flag_empty_no_agent_holds_the_tool(self) -> None:
        for name in (AGENT, "whatsapp-assistant", "crm-assistant", "projects-assistant", "*"):
            assert narrowing.narrow_tool_for(name, Adapter(1)) is None

    def test_the_flag_names_agents(self, monkeypatch) -> None:
        monkeypatch.setenv("NARROWING_AGENTS", f" {AGENT} , crm-assistant")
        get_settings.cache_clear()
        assert narrowing.narrow_tool_for(AGENT, Adapter(1)) is not None
        assert narrowing.narrow_tool_for("crm-assistant", Adapter(1)) is not None
        assert narrowing.narrow_tool_for("projects-assistant", Adapter(1)) is None
        assert narrowing.narrow_tool_for("", Adapter(1)) is None

    def test_a_star_names_every_agent(self, monkeypatch) -> None:
        monkeypatch.setenv("NARROWING_AGENTS", "*")
        get_settings.cache_clear()
        assert narrowing.narrowing_on("any-agent") is True
        assert narrowing.narrowing_on("") is False

    def test_a_broken_settings_read_reads_as_off(self, monkeypatch) -> None:
        import acb_common

        def boom() -> Any:
            raise RuntimeError("settings")

        monkeypatch.setattr(acb_common, "get_settings", boom)
        assert narrowing.narrowing_on(AGENT) is False

    async def test_the_tool_reads_the_flag_again_at_each_call(
        self, door: Door, s1: SystemOne, monkeypatch,
    ) -> None:
        monkeypatch.setenv("NARROWING_AGENTS", AGENT)
        get_settings.cache_clear()
        adapter = Adapter(3)
        tool = narrowing.narrow_tool_for(AGENT, adapter)
        assert tool is not None
        monkeypatch.setenv("NARROWING_AGENTS", "")
        get_settings.cache_clear()
        assert await tool(QUERY) == narrowing.OFF_ANSWER
        assert adapter.searched == [] and door.bodies == []


# ── The tool's input, its risk and its logs ─────────────────────────────────


async def test_an_unknown_filter_key_is_refused_by_name(door: Door, s1: SystemOne) -> None:
    adapter = Adapter(3)
    out = await _tool(adapter)(QUERY, filters='{"folder": "inbox", "colour": "red"}')
    assert out == "narrow_and_read: unknown filter key: colour. The keys are: after, folder."
    assert adapter.searched == []
    out = await _tool(adapter)(QUERY, filters='{"folder": "inbox"}')
    assert adapter.searched == [(QUERY, {"folder": "inbox"})]
    assert await _tool(adapter)(QUERY, filters="[1]") == (
        "narrow_and_read: filters must be a JSON object."
    )


async def test_an_adapter_fault_is_text(door: Door, s1: SystemOne) -> None:
    adapter = Adapter(3)

    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(SNIPPET_CANARY)

    adapter.candidates = broken  # type: ignore[method-assign]
    out = await _tool(adapter)(QUERY)
    assert out == narrowing.SEARCH_FAILED.format(source="test")


async def test_the_logs_hold_no_tenant_text(door: Door, s1: SystemOne) -> None:
    door.fail = {"m1": (400, {"detail": {"reason": "x", "quote": SNIPPET_CANARY}})}
    door.rule = lambda cid: ("no", 0.9) if cid == "m20" else ("yes", 0.9)
    with structlog.testing.capture_logs() as caps:
        await _tool(Adapter(20))(QUERY)
    text = repr(caps)
    assert caps, "the pipeline logged nothing"
    for secret in (QUERY, SNIPPET_CANARY, BODY_CANARY, "acme", "Quote request"):
        assert secret not in text, secret
    done = _events(caps, "narrowing.done")
    assert len(done) == 1
    assert done[0]["request_ids"] and all(r.startswith("req-") for r in done[0]["request_ids"])


def test_the_tool_is_a_platform_tool_with_no_egress() -> None:
    """§4: ``open_world=False`` on the function, and the egress control knows
    the callable by identity (H-236)."""
    tool = _tool(Adapter(1))
    assert tool.__name__ == narrowing.TOOL_NAME == "narrow_and_read"
    assert tool.__tool_risk__ == {  # type: ignore[attr-defined]
        "read_only": True, "destructive": False, "idempotent": True, "open_world": False,
    }
    assert eg.is_egress_tool(tool) is False
    assert eg._platform_owned(tool, "narrow_and_read") is True
    assert tier_policy.TOOL_HINTS["narrow_and_read"] == "analysis"


def test_make_narrow_tool_refuses_a_non_adapter() -> None:
    with pytest.raises(TypeError):
        narrowing.make_narrow_tool(object())  # type: ignore[arg-type]
