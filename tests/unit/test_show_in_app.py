"""``show_in_app``: the chat opens a page and says so (WS-62 CF-3).

Spec: ``project-docs/specs/chat_first_surface.md`` §4.4 and CF-3. The tool
is the orchestrator's own (``orchestrator/agents.py``), and the check is
``orchestrator/in_app_path.py``.

Three layers:

1. **The pure checks.** ``pane_of`` finds the pane as ``paneAt`` does, and
   ``PANES`` equals the literal ``href`` entries of ``nav.ts``, each with the
   feature that gates it. ``refusal`` refuses a bad shape, an unknown pane,
   and a pane the member does not hold.
2. **The tool, with the run queue faked.** The refusals send nothing. A good
   call sends ONE ``CUSTOM`` event ``navigate`` and returns ``{"opened"}``.
   A second call in the same turn returns an error and sends nothing. A new
   turn (a new queue) may open a page again.
3. **The real orchestrator run.** Through ``evals.routing.harness``, as
   ``test_delegated_confirmation_card.py`` runs it: a scripted model calls
   ``show_in_app`` twice, and the parent's stream carries exactly one
   ``navigate`` event. The second call's result says "already opened".

The member's features come from ``acb_auth.resolve_access``, which needs the
database. Layers 2 and 3 replace ``in_app_path.member_features`` with a
fixed set, and layer 1 drives ``refusal`` with the set as an argument.

Run::

    uv run pytest tests/unit/test_show_in_app.py -q
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from orchestrator import in_app_path as iap

REPO = Path(__file__).resolve().parents[2]
NAV_TS = REPO / "workbench" / "control_plane" / "src" / "lib" / "nav.ts"
HELD = frozenset({"projects", "tasks", "email", "chat"})


# ── 1. The pure checks ──────────────────────────────────────────────────────

def _nav_panes() -> dict[str, str | None]:
    """The LIVE literal ``href`` entries of ``nav.ts`` and their ``feature``.

    ``launch_surface.md`` §2 is the allowlist of record, and ``nav.ts``
    mirrors it (``nav.test.ts``). A preview pane is left out, as ``PANES``
    leaves it out (review of #1197, P2). So is an admin page: the admin-only
    pane and every ``/settings/…`` page, which the tool never opens. A
    template href (the Centers) is not literal, so it is not here (D49).
    """
    text = NAV_TS.read_text(encoding="utf-8")
    out: dict[str, str | None] = {}
    for part in re.split(r"\bhref:\s*", text)[1:]:
        href = re.match(r'"([^"]+)"', part)
        if href is None:
            continue
        end = part.find("\n      },")
        body = part[:end] if end > 0 else part
        if not re.search(r'\blaunch:\s*"live"', body):
            continue
        if re.search(r"adminOnly:\s*true", body) or href.group(1).startswith("/settings"):
            continue
        feature = re.search(r'\bfeature:\s*"([^"]+)"', body)
        out[href.group(1)] = feature.group(1) if feature else None
    assert len(out) >= 10, "nav.ts parse found too few live panes"
    return out


def test_panes_mirror_nav_ts_live_entries() -> None:
    """One list, on both sides. A pane that goes live or dark in ``nav.ts``
    must join or leave ``PANES`` in the same PR."""
    ours = {p.href: p.feature for p in iap.PANES if p.href != "/"}
    assert ours == _nav_panes()


def test_no_preview_or_admin_pane_is_in_the_allowlist() -> None:
    hrefs = {p.href for p in iap.PANES}
    for dark in ("/dashboard", "/notes", "/memory", "/workflows", "/build/apps", "/build/agents",
                 "/agents", "/observability", "/settings/organization", "/settings/appearance"):
        assert dark not in hrefs, dark


def test_the_root_is_a_pane_every_member_holds() -> None:
    assert iap.pane_of("/") == iap.Pane("/", None)
    assert iap.pane_of("/?x=1") == iap.Pane("/", None)


@pytest.mark.parametrize(("path", "href"), [
    ("/projects", "/projects"),
    ("/projects?task=42", "/projects"),
    ("/projects/abc/def#x", "/projects"),
    ("/people", "/people"),
    ("/people/me", "/people/me"),
    ("/people/me/edit", "/people/me"),
    ("/tasks#today", "/tasks"),
    ("/%70rojects", "/projects"),
])
def test_pane_of_takes_the_longest_href(path: str, href: str) -> None:
    pane = iap.pane_of(path)
    assert pane is not None and pane.href == href


@pytest.mark.parametrize("path", ["/nowhere", "/project", "/centers/sales",
                                  "/settings/organization", "/settings/appearance",
                                  "/peoplex", "/build/apps", "/connect/authorize",
                                  "/signup", "/_next/static/x.js", "/projects/../crm"])
def test_pane_of_knows_no_other_path(path: str) -> None:
    assert iap.pane_of(path) is None


@pytest.mark.parametrize(("path", "reason"), [
    ("https://evil.example/x", "not a path inside this app"),
    ("//evil.example", "not a path inside this app"),
    ("/\\evil.example", "not a path inside this app"),
    ("/projects\tx", "not a path inside this app"),
    ("/nowhere", "no page of this app at that path"),
    ("/crm", "the member does not hold /crm"),
    ("/settings/organization", "no page of this app at that path"),
    ("/settings/appearance", "no page of this app at that path"),
    ("/connect/authorize", "no page of this app at that path"),
    ("/signup", "no page of this app at that path"),
    ("/_next/static/x.js", "not a path inside this app"),
    ("/api/projects", "not a path inside this app"),
])
def test_refusal_names_the_reason(path: str, reason: str) -> None:
    assert iap.refusal(path, HELD) == reason


#: The P0 of the review of #1197: each one resolved to another page in the
#: browser, and ``refusal`` let it through because ``pane_of`` read the raw
#: prefix. Seen red before the fix: every row returned None.
DOT_SEGMENT_PATHS = (
    "/projects/../crm",
    "/projects/../../settings/organization",
    "/projects/%2e%2e/crm",
    "/projects/%2E%2E/crm",
    "/projects/.%2e/crm",
    "/projects/..%2fcrm",
    "/projects/%252e%252e/crm",
    "/projects/../centers/sales",
    "/projects/../api/x",
    "/people/me/../../crm",
)


@pytest.mark.parametrize("path", DOT_SEGMENT_PATHS)
def test_a_dot_segment_never_leaves_the_pane(path: str) -> None:
    assert iap.refusal(path, frozenset({"projects", "crm"})) == "not a path inside this app"
    assert iap.pane_of(path) is None


def test_path_segments_decodes_once_and_refuses_the_tricks() -> None:
    assert iap.path_segments("/") == []
    assert iap.path_segments("/projects/caf%C3%A9") == ["projects", "café"]
    for bad in ("/projects/", "/projects//x", "/projects/%2fx", "/projects/%5cx",
                "/projects/%09x", "/projects/%ff", "/projects/%zz", "/a/./b", "/a/../b"):
        assert iap.path_segments(bad) is None, bad


@pytest.mark.parametrize("path", ["/", "/projects?task=42", "/tasks#today",
                                  "/email?message=x", "/people/me", "/calendar"])
def test_a_held_pane_is_not_refused(path: str) -> None:
    assert iap.refusal(path, HELD) is None


def test_nobody_known_holds_nothing_gated() -> None:
    """A run with no verified member opens no gated pane, and still the root."""
    assert iap.refusal("/projects", None) == "the member does not hold /projects"
    assert iap.refusal("/", None) is None


# ── 2. The tool, with the run queue faked ───────────────────────────────────

class _Queue:
    """The run's event queue: it records each event."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """A run with a queue and a member who holds ``HELD``. Returns the queue."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from orchestrator import executor

    queue = _Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: queue)

    async def _features() -> frozenset[str]:
        return HELD

    monkeypatch.setattr(iap, "member_features", _features)
    return queue


def _call(path: str, why: str = "Here it is.") -> dict[str, Any]:
    from orchestrator.agents import show_in_app

    return asyncio.run(show_in_app(path, why))


def _navigates(queue: _Queue) -> list[dict[str, Any]]:
    return [e for e in queue.events if e.get("type") == "CUSTOM" and e.get("name") == "navigate"]


@pytest.mark.parametrize("path", [
    "https://evil.example/login", "//evil.example", "/\\evil.example/login",
    "/projects\tx", "javascript:alert(1)", "", "projects",
])
def test_a_bad_shape_is_refused_and_sends_nothing(run: _Queue, path: str) -> None:
    assert _call(path) == {"error": "not a path inside this app"}
    assert run.events == []


def test_an_unknown_pane_is_refused_and_sends_nothing(run: _Queue) -> None:
    assert _call("/nowhere") == {"error": "no page of this app at that path"}
    assert run.events == []


def test_a_pane_the_member_lacks_is_refused_and_sends_nothing(run: _Queue) -> None:
    assert _call("/crm") == {"error": "the member does not hold /crm"}
    assert run.events == []


#: The result of a good call. The note makes the model hedge: the client
#: moves nothing while the assistant is closed or another chat is in front.
NOTE = "the page opens only while the member watches this chat"


def _opened(path: str) -> dict[str, str]:
    return {"opened": path, "note": NOTE}


def test_a_good_call_sends_one_navigate_and_returns_opened(run: _Queue) -> None:
    out = _call("/projects?task=42", "Here is task #42.")
    assert out == _opened("/projects?task=42")
    assert run.events == [{
        "type": "CUSTOM", "name": "navigate",
        "value": {"path": "/projects?task=42", "why": "Here is task #42."},
    }]


@pytest.mark.parametrize("path", DOT_SEGMENT_PATHS)
def test_a_dot_segment_is_refused_and_sends_nothing(run: _Queue, path: str) -> None:
    """The P0 of the review of #1197, at the tool."""
    assert _call(path) == {"error": "not a path inside this app"}
    assert run.events == []


@pytest.mark.parametrize("path", ["/connect/authorize", "/signup", "/settings/appearance",
                                  "/settings/organization", "/centers/sales", "/build/apps"])
def test_a_path_off_the_allowlist_is_refused_and_sends_nothing(run: _Queue, path: str) -> None:
    assert _call(path) == {"error": "no page of this app at that path"}
    assert run.events == []


def test_a_second_call_in_the_turn_is_refused_and_sends_nothing(run: _Queue) -> None:
    assert _call("/projects") == _opened("/projects")
    assert _call("/email") == {"error": "already opened a page this turn"}
    assert _call("/projects") == {"error": "already opened a page this turn"}
    assert len(_navigates(run)) == 1


def test_a_refused_call_does_not_spend_the_turn(run: _Queue) -> None:
    """A bad path first, then a good one: the good one still opens."""
    assert _call("//evil.example")["error"]
    assert _call("/crm")["error"]
    assert _call("/projects") == _opened("/projects")
    assert len(_navigates(run)) == 1


def test_a_nested_run_opens_nothing(run: _Queue) -> None:
    """Review of #1197, P2: a specialist calls the orchestrator, and that run
    sees the parent's queue. The delegation chain names an ancestor, so the
    tool refuses and sends nothing."""
    from acb_skills.agent_tools import _delegation_chain

    token = _delegation_chain.set(("email-assistant",))
    try:
        assert _call("/projects") == {"error": "only the member's own assistant opens a page"}
    finally:
        _delegation_chain.reset(token)
    assert run.events == []
    # The member's own run, with no ancestor, still opens it.
    assert _call("/projects") == _opened("/projects")
    assert len(_navigates(run)) == 1


def _hermetic_tools(monkeypatch: pytest.MonkeyPatch, chat_first: bool) -> list[str]:
    """The names of the orchestrator's own tools, as its factory builds them."""
    from acb_common import get_settings
    from orchestrator import agents as pkg

    mod = pkg._mod

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    monkeypatch.setattr(get_settings(), "chat_first_tools", chat_first, raising=False)
    agent = mod.build_orchestrator_agent(with_history=False)
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


def test_the_tool_is_dark_until_chat_first_tools_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #1197, P1: CHAT_FIRST_TOOLS off (the default) offers no
    show_in_app. On, it joins after search_timeline, and nothing else moves."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    off = _hermetic_tools(monkeypatch, False)
    on = _hermetic_tools(monkeypatch, True)
    assert "show_in_app" not in off
    assert "show_in_app" in on
    assert [n for n in on if n != "show_in_app"] == off
    assert on.index("show_in_app") == on.index("search_timeline") + 1


def test_the_flag_reader_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_first_tools", False, raising=False)
    assert iap.chat_first_tools_enabled() is False
    monkeypatch.setattr(get_settings(), "chat_first_tools", True, raising=False)
    assert iap.chat_first_tools_enabled() is True
    monkeypatch.setattr(get_settings(), "chat_first_tools", "yes", raising=False)
    assert iap.chat_first_tools_enabled() is False


def test_a_new_turn_may_open_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mark lives on the queue, and a new turn has a new queue."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from orchestrator import executor

    async def _features() -> frozenset[str]:
        return HELD

    monkeypatch.setattr(iap, "member_features", _features)
    first, second = _Queue(), _Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: first)
    assert _call("/projects") == _opened("/projects")
    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: second)
    assert _call("/email") == _opened("/email")
    assert len(_navigates(first)) == 1 and len(_navigates(second)) == 1


def test_no_run_queue_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch run, or WhatsApp: no stream to send to, so nothing moves."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from orchestrator import executor

    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: None)
    assert _call("/projects") == {"error": "no page to open from here"}


def test_the_tool_is_the_orchestrators_and_no_specialists(monkeypatch: pytest.MonkeyPatch) -> None:
    """§9.2 Q3: only ``orchestrator`` holds it. The tool is in its core list,
    annotated read-only and inside the platform, and no specialist's
    ``agents.py`` names it."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from acb_skills import tool_annotations as ta
    from orchestrator import agents

    assert ta.TOOL_ANNOTATIONS["show_in_app"] == {
        "read_only": True, "destructive": False, "idempotent": True, "open_world": False,
    }
    assert agents.show_in_app.__tool_risk__ == ta.TOOL_ANNOTATIONS["show_in_app"]
    src = (REPO / "apps" / "services" / "orchestrator" / "orchestrator" / "agents.py").read_text(encoding="utf-8")
    assert "page_tools: list[Any] = [show_in_app] if chat_first_tools_enabled() else []" in src
    assert re.search(r"core_tools: list\[Any\] = \[[^\]]*\*page_tools,", src, re.S)
    for path in (REPO / "apps" / "agents").glob("*/agents.py"):
        assert "show_in_app" not in path.read_text(encoding="utf-8"), path


def test_member_features_without_a_member_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No verified member on the frame: nobody is known, and the caller refuses."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from orchestrator import executor

    monkeypatch.setattr(executor, "_verified_run_member", lambda: "")
    assert asyncio.run(iap.member_features()) is None


def test_member_features_reads_the_members_access(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    import acb_auth
    from orchestrator import executor

    monkeypatch.setattr(executor, "_verified_run_member", lambda: "member@example.com")
    seen: list[str] = []

    class _Access:
        is_active = True

        def allowed_features(self) -> tuple[str, ...]:
            return ("projects", "tasks")

    async def _resolve(email: str, **_kw: Any) -> _Access:
        seen.append(email)
        return _Access()

    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)
    assert asyncio.run(iap.member_features()) == frozenset({"projects", "tasks"})
    assert seen == ["member@example.com"]


def test_member_features_of_an_inactive_member_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    import acb_auth
    from orchestrator import executor

    monkeypatch.setattr(executor, "_verified_run_member", lambda: "gone@example.com")

    class _Access:
        is_active = False

        def allowed_features(self) -> tuple[str, ...]:
            return ("projects",)

    async def _resolve(email: str, **_kw: Any) -> _Access:
        return _Access()

    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)
    assert asyncio.run(iap.member_features()) is None


# ── 3. The real orchestrator run ────────────────────────────────────────────

def _real_run(words: str, path: str,
              member_holds: frozenset[str]) -> tuple[Any, Any, list[tuple[str, str]]]:
    """One turn of the real orchestrator. Its model calls ``show_in_app``
    twice, with the same *path*, then answers. Returns the record, the
    orchestrator's model and the relay lines ``(thread id, line)``."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from evals.routing import dataset as ds
    from evals.routing import shell
    from evals.routing.harness import (
        ORCHESTRATOR,
        ReplyModel,
        RoutingHarness,
        text_turn,
        tool_turn,
    )

    with RoutingHarness() as harness:

        async def _features() -> frozenset[str]:
            return member_holds

        harness._mp.setattr(iap, "member_features", _features)
        model = ReplyModel([
            tool_turn("show_in_app", {"path": path, "why": "Here is the board."}, "call_1"),
            tool_turn("show_in_app", {"path": path, "why": "Here it is again."}, "call_2"),
            text_turn("You see the board for Website relaunch."),
        ])
        harness.models = {ORCHESTRATOR: model}
        record = asyncio.run(harness.drive(
            words, shell.system_context(None, ()),
            member=ds.MEMBER, org=ds.ORG, timeout=45.0,
        ))
        relayed = list(harness.relayed)
    return record, model, relayed


def _tool_results(model: Any) -> list[str]:
    return [str(m.get("content")) for b in model.bodies for m in b.get("messages") or []
            if m.get("role") == "tool"]


def test_the_navigate_event_is_in_the_parents_stream_once() -> None:
    """Done-when 2 and 5 through the real run: one event, and the second
    call's result is the error."""
    record, model, relayed = _real_run(
        "Show me the board for Website relaunch.",
        "/projects?project=6d000000-0000-4000-8000-000000000001",
        frozenset({"projects", "tasks", "chat"}),
    )
    assert record.error is None, record.error
    navigates = [e for e in record.events if e.get("type") == "CUSTOM" and e.get("name") == "navigate"]
    assert [e["value"] for e in navigates] == [{
        "path": "/projects?project=6d000000-0000-4000-8000-000000000001",
        "why": "Here is the board.",
    }]
    results = _tool_results(model)
    assert any('"opened"' in r for r in results), results
    assert any("already opened a page this turn" in r for r in results), results
    # The orchestrator was offered the tool, and routed to no specialist.
    offered = {t["function"]["name"] for t in model.bodies[0].get("tools") or []}
    assert "show_in_app" in offered
    assert record.routed == [] and record.reached == []
    # The relay copy is the parent thread's, so a reconnect replays it to
    # the same chat. The client's replay loop does not act on it.
    threads = {tid for tid, line in relayed if '"navigate"' in line}
    assert threads <= {record.thread_id}, threads


def test_a_pane_the_member_lacks_moves_nothing_in_the_real_run() -> None:
    """Done-when 1 through the real run: the member holds no Projects."""
    record, model, _relayed = _real_run("Show me the board.", "/projects", frozenset({"email"}))
    assert record.error is None, record.error
    assert not [e for e in record.events if e.get("type") == "CUSTOM" and e.get("name") == "navigate"]
    results = _tool_results(model)
    assert all("does not hold /projects" in r for r in results), results


def test_a_specialist_that_calls_the_orchestrator_moves_nothing() -> None:
    """Review of #1197, P2, through the real runs. projects-assistant is the
    member's run. It holds call_agent, and calls the orchestrator. That
    nested orchestrator sees the parent's queue, and its show_in_app refuses:
    no navigate event reaches the stream."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    pytest.importorskip("skill_projects", reason="skill-projects not installed")
    from evals.routing import dataset as ds
    from evals.routing import shell
    from evals.routing.harness import (
        ORCHESTRATOR,
        ReplyModel,
        RoutingHarness,
        text_turn,
        tool_turn,
    )

    pa = "projects-assistant"
    words = "Show me the board for Website relaunch."
    with RoutingHarness() as harness:

        async def _features() -> frozenset[str]:
            return frozenset({"projects", "tasks", "chat"})

        harness._mp.setattr(iap, "member_features", _features)
        orchestrator = ReplyModel([
            tool_turn("show_in_app", {"path": "/projects", "why": "Here is the board."}),
            text_turn("The board is in Projects."),
        ])
        harness.models = {
            pa: ReplyModel([tool_turn("call_agent", {"agent_name": ORCHESTRATOR, "message": words}),
                            text_turn("Done.")]),
            ORCHESTRATOR: orchestrator,
        }
        record = asyncio.run(harness.drive(
            words, shell.system_context("/projects", ds.case("P1").facts),
            member=ds.MEMBER, org=ds.ORG, timeout=45.0, agent=pa,
        ))
    assert record.error is None, record.error
    assert orchestrator.bodies, "the nested orchestrator never ran"
    assert not [e for e in record.events if e.get("type") == "CUSTOM" and e.get("name") == "navigate"]
    results = _tool_results(orchestrator)
    assert results and all("only the member's own assistant opens a page" in r for r in results), results
