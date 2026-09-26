"""Every model call says WHO made it and WHICH APP it served. Usage slice 1.

Spec: ``ai_metering_and_analytics.md`` §5 · D66 · H-73.

🔴 **Measured on production, 2026-09-24.** The first real Projects chat after
the Router went live made five calls. Each ``usage_event`` row named the agent
and left the member, the app and the run empty. The per-person and per-app
pages had nothing to group by, and a per-member cap had nobody to cap.

The Router side was already built. The gateway reads ``X-CC-Member``, its
signed proof, ``X-CC-Module`` and ``X-CC-Run``. The agents never sent them.

Run::

    uv run pytest tests/unit/test_usage_attribution.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path

import httpx
import pytest
from acb_common import bind_run_context, get_run_context, run_context_scope
from acb_llm.attribution import attribution_headers

ROOT = Path(__file__).resolve().parents[2]
SECRET = "test-session-secret"


@pytest.fixture(autouse=True)
def _clean_context():
    """Every test starts and ends with no run bound. A leaked `user` would
    make the next test's attribution pass for the wrong reason."""
    import structlog

    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def secret(monkeypatch):
    """The box's session secret, which signs a member proof."""
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "gateway_session_secret", SECRET, raising=False)
    return SECRET


# ── What a request carries ──────────────────────────────────────────────────


class TestTheHeaders:
    def test_a_VERIFIED_member_is_named_AND_signed(self, secret):
        from acb_auth.member_proof import verify_member

        out = attribution_headers({
            "user": "dana@acme.com", "member_verified": "1",
            "app": "projects", "run_id": "run-1", "agent": "projects-assistant",
        })
        assert out["X-CC-Member"] == "dana@acme.com"
        assert verify_member(out["X-CC-Member-Proof"], secret) == "dana@acme.com"
        assert out["X-CC-Module"] == "projects"
        assert out["X-CC-Run"] == "run-1"
        assert out["X-CC-Agent"] == "projects-assistant"

    def test_a_CLAIMED_member_is_named_and_NEVER_signed(self, secret):
        """🔴 H-73 in one assertion.

        A member the request body chose is good enough to REPORT and not good
        enough to ENFORCE. Signing it would hand whoever wrote the body a
        proof for any address they typed, and a cap keys on the proof.
        """
        out = attribution_headers({"user": "dana@acme.com"})
        assert out["X-CC-Member"] == "dana@acme.com"
        assert "X-CC-Member-Proof" not in out

    def test_no_proof_when_the_box_has_NO_SECRET(self, monkeypatch):
        """An empty secret must sign nothing. The gateway already refuses to
        VERIFY against one, and a proof it cannot check is noise."""
        from acb_common.settings import get_settings

        monkeypatch.setattr(get_settings(), "gateway_session_secret", "", raising=False)
        out = attribution_headers({"user": "dana@acme.com", "member_verified": "1"})
        assert "X-CC-Member-Proof" not in out

    @pytest.mark.parametrize("who", ["anonymous", "default", "svc-cron", "", "  "])
    def test_a_name_that_is_not_an_ADDRESS_is_not_a_member(self, who):
        """⚠️ Automations bind placeholders. Billing one as a person would pile
        every automation's spend onto one fake name on the usage page."""
        assert "X-CC-Member" not in attribution_headers({"user": who})

    def test_an_absent_field_is_OMITTED_not_sent_empty(self):
        """The Console records an empty string as a member, which reads as an
        attribution somebody made."""
        assert attribution_headers({}) == {}

    def test_it_reads_the_CURRENT_RUN_when_given_nothing(self):
        bind_run_context(user="dana@acme.com", app="crm", run_id="r-7")
        out = attribution_headers()
        assert (out["X-CC-Member"], out["X-CC-Module"], out["X-CC-Run"]) == (
            "dana@acme.com", "crm", "r-7")


class TestTheGatewayAcceptsWhatWeSign:
    def test_the_gateway_VERIFIES_the_proof_this_module_mints(self, secret):
        """🔴 Both ends in one test, because each alone is a fake.

        The stamp signs with ``gateway_session_secret``. The gateway's
        ``_member_for`` verifies with the same setting. If either side ever
        reads a different setting, every proof fails in silence and every
        member reads as unproven. That is attribution that works and a cap
        that never engages.
        """
        pytest.importorskip("fastapi")
        from gateway.routes.v1_compat import _member_for

        headers = attribution_headers({"user": "dana@acme.com", "member_verified": "1"})

        class _Req:
            def __init__(self, h):
                self.headers = {k.lower(): v for k, v in h.items()}

        assert _member_for(_Req(headers)) == ("dana@acme.com", True)


# ── Through the REAL framework client ───────────────────────────────────────


def _response() -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


class TestThroughTheAgentFramework:
    """⚠️ Not a stub of the client. The real ``OpenAIChatCompletionClient``
    makes the call, and only the network is replaced. The headers asserted are
    the ones that would leave the process."""

    def _client_seeing(self, monkeypatch, seen: dict):
        import openai
        from acb_llm.attribution import attributed_openai
        from agent_framework.openai import OpenAIChatCompletionClient

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update({k.lower(): v for k, v in request.headers.items()})
            return _response()

        real = openai.DefaultAsyncHttpxClient
        monkeypatch.setattr(
            openai, "DefaultAsyncHttpxClient",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )
        return OpenAIChatCompletionClient(
            model="tier-balanced",
            async_client=attributed_openai(
                base_url="http://gateway/v1", api_key="k",
                default_headers={"X-CC-Agent": "projects-assistant",
                                 "X-CC-Source": "chat"},
            ),
        )

    def test_one_client_bills_EACH_person_who_uses_it(self, monkeypatch, secret):
        """🔴 The reason a fixed header could never work.

        One client is built per agent and serves everyone who chats with it.
        Two people, one client: each request must name its own person.
        """
        from agent_framework import Message

        seen: dict = {}
        client = self._client_seeing(monkeypatch, seen)

        async def call_as(who: str) -> str:
            # Reset per call. Without it the proof assertion below could pass
            # on the FIRST call's header alone (found by review).
            seen.clear()
            with run_context_scope():
                bind_run_context(user=who, app="projects", member_verified=True)
                await client.get_response([Message(role="user", contents=["hi"])])
            return seen["x-cc-member"]

        from acb_auth.member_proof import verify_member

        assert asyncio.run(call_as("dana@acme.com")) == "dana@acme.com"
        assert verify_member(seen["x-cc-member-proof"], secret) == "dana@acme.com"
        assert asyncio.run(call_as("ravi@acme.com")) == "ravi@acme.com"
        assert verify_member(seen["x-cc-member-proof"], secret) == "ravi@acme.com"
        assert seen["x-cc-module"] == "projects"

    def test_the_agents_OWN_header_is_never_overwritten(self, monkeypatch):
        """A delegated sub-agent's run context can still carry its parent's
        agent name. The client's own header knows better."""
        from agent_framework import Message

        seen: dict = {}
        client = self._client_seeing(monkeypatch, seen)

        async def call() -> None:
            with run_context_scope():
                bind_run_context(agent="orchestrator", user="dana@acme.com")
                await client.get_response([Message(role="user", contents=["hi"])])

        asyncio.run(call())
        assert seen["x-cc-agent"] == "projects-assistant"


# ── Who the run is for ──────────────────────────────────────────────────────


class TestWhoTheRunIsFor:
    def test_the_SESSION_beats_the_request_body(self):
        """🔴 The executor used to bind `user_email` from the body, and the
        Router bills and caps on that user. So whoever wrote the body chose
        whose budget paid."""
        from orchestrator.executor import _run_member

        body = {"user_email": "someone-else@acme.com"}
        assert _run_member(body, "dana@acme.com") == ("dana@acme.com", True)

    def test_a_body_claim_alone_is_kept_but_NOT_verified(self):
        from orchestrator.executor import _run_member

        assert _run_member({"user_email": "dana@acme.com"}) == ("dana@acme.com", False)
        assert _run_member({"user_id": "dana@acme.com"}) == ("dana@acme.com", False)

    def test_a_body_CANNOT_forge_the_session(self):
        """⚠️ The event route spreads a caller's payload into the run. A
        reserved payload key would be one any caller could write, which is why
        the session travels as a keyword argument instead."""
        from orchestrator.executor import _run_member

        forged = {"_session_user": "ceo@acme.com", "session_user": "ceo@acme.com",
                  "user_email": "dana@acme.com"}
        assert _run_member(forged) == ("dana@acme.com", False)

    def test_nothing_at_all_is_nobody(self):
        from orchestrator.executor import _run_member

        assert _run_member(None) == ("", False)
        assert _run_member({}) == ("", False)

    def test_the_gateway_names_only_a_REAL_ADDRESS(self):
        pytest.importorskip("fastapi")
        from gateway.routes.agent import _session_member

        class U:
            def __init__(self, email):
                self.email = email

        assert _session_member(U("dana@acme.com")) == "dana@acme.com"
        for placeholder in ("anonymous", "", None, "default"):
            assert _session_member(U(placeholder)) is None

    def test_EVERY_person_route_passes_the_session(self):
        """The three routes that start a run for a signed-in person. A fourth
        that forgets the keyword bills its runs to the body's claim again, and
        nothing else would notice."""
        src = (ROOT / "apps/services/gateway/gateway/routes/agent.py").read_text(
            encoding="utf-8")
        assert src.count("session_user=_session_member(user)") == 3


class TestANestedRunRestoresItsParent:
    def test_the_parent_keeps_its_member_after_a_child_run(self):
        """🔴 A sub-agent awaits `run_agent` on its parent's task. Clearing on
        the way out would wipe the parent's member, so every call the parent
        made AFTER the child returned would bill nobody."""
        bind_run_context(run_id="parent", user="dana@acme.com", app="projects",
                         member_verified=True)
        with run_context_scope():
            bind_run_context(run_id="child", app="crm")
            assert get_run_context()["app"] == "crm"
        after = get_run_context()
        assert after["run_id"] == "parent"
        assert after["app"] == "projects"
        assert after["member_verified"] == "1"

    def test_a_child_that_names_ANOTHER_person_is_NOT_verified(self, secret):
        """🔴 The H-73 hole review found in the first version.

        The flag was bound only when True, so a nested run that rebound
        `user` to a request body's claim kept its PARENT's "1". The stamp then
        signed an address the body chose. The flag now belongs to the user
        bound beside it: a new user without verification clears it.
        """
        bind_run_context(user="dana@acme.com", member_verified=True)
        with run_context_scope():
            bind_run_context(user="attacker-chosen@acme.com")
            assert "member_verified" not in get_run_context()
            out = attribution_headers()
            assert out["X-CC-Member"] == "attacker-chosen@acme.com"
            assert "X-CC-Member-Proof" not in out, "a body's claim was signed"
        assert get_run_context()["member_verified"] == "1", "parent not restored"

    def test_a_child_acting_for_the_SAME_person_keeps_the_verification(self):
        """The common case: a sub-agent binds no user at all, so it acts for
        its parent's member and must keep the parent's standing."""
        bind_run_context(user="dana@acme.com", member_verified=True)
        with run_context_scope():
            bind_run_context(run_id="child", agent="crm-assistant")
            assert get_run_context()["member_verified"] == "1"

    def test_a_flag_with_NO_user_beside_it_verifies_nothing(self):
        bind_run_context(member_verified=True)
        assert "member_verified" not in get_run_context()

    def test_a_child_ADDS_nothing_that_outlives_it(self):
        """The reverse leak: a key the child bound and the parent never had."""
        bind_run_context(run_id="parent")
        with run_context_scope():
            bind_run_context(app="crm", user="x@acme.com")
        assert "app" not in get_run_context()
        assert "user" not in get_run_context()


# ── The in-process path ─────────────────────────────────────────────────────


class TestTheInProcessPath:
    def test_the_APP_wins_over_the_surface(self):
        """🔴 ``source`` is the surface that started the run. Mapping it as the
        app bills an app called "chat" for every agent run."""
        from acb_llm.routed import _attribution

        bind_run_context(source="chat", app="projects", user="dana@acme.com",
                         member_verified=True)
        out = _attribution()
        assert out["module_slug"] == "projects"
        assert out["member_proven"] is True

    def test_the_surface_is_still_the_FALLBACK_outside_an_agent(self):
        """An email automation binds ``source="email"`` and runs no agent, so
        its source IS its app."""
        from acb_llm.routed import _attribution

        bind_run_context(source="email")
        assert _attribution()["module_slug"] == "email"

    def test_an_unverified_member_is_NOT_proven(self):
        from acb_llm.routed import _attribution

        bind_run_context(user="dana@acme.com")
        out = _attribution()
        assert out["member"] == "dana@acme.com"
        assert out["member_proven"] is False


# ── The fences ──────────────────────────────────────────────────────────────


def _nav_slugs() -> set[str]:
    nav = (ROOT / "workbench/control_plane/src/lib/nav.ts").read_text(encoding="utf-8")
    return set(re.findall(r'href:\s*"/([a-z-]+)"', nav))


class TestEveryAgentDeclaresItsApp:
    def test_every_agent_config_names_an_app_the_PRODUCT_shows(self):
        """🔴 An agent with no ``app`` falls back to its SURFACE, and bills an
        app called "chat". The slug must be one the navigation shows, so the
        usage page names apps the way the person clicking them does."""
        slugs = _nav_slugs()
        assert slugs, "could not read the navigation"
        missing, unknown = [], []
        for cfg in sorted((ROOT / "apps/agents").glob("*/config.json")):
            app = json.loads(cfg.read_text(encoding="utf-8")).get("app")
            if not app:
                missing.append(cfg.parent.name)
            elif app not in slugs:
                unknown.append(f"{cfg.parent.name}={app}")
        assert not missing, f"agents with no app: {missing}"
        assert not unknown, f"apps the navigation does not show: {unknown}"


class TestEveryClientUsesTheSeam:
    def test_no_agent_builds_a_client_that_cannot_attribute(self):
        """🔴 The fence for the sixth agent — on the FRAMEWORK path.

        The Copilot SDK path has its own fence, ``TestTheCopilotPathCarriesTheRun``
        below (H-181).

        Every ``OpenAIChatCompletionClient(`` in the tree must take its
        ``async_client`` from :func:`attributed_openai`. A client built the
        old way sends the agent's name and nothing else, and its spend reads
        as nobody's.
        """
        offenders = []
        for py in list((ROOT / "apps").rglob("*.py")):
            if "node_modules" in py.parts or "tests" in py.parts:
                continue
            src = py.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"OpenAIChatCompletionClient\(", src):
                call = src[m.end(): m.end() + 900]
                depth, end = 1, 0
                for i, ch in enumerate(call):
                    depth += (ch == "(") - (ch == ")")
                    if depth == 0:
                        end = i
                        break
                if "attributed_openai(" not in call[:end]:
                    line = src[: m.start()].count("\n") + 1
                    offenders.append(f"{py.relative_to(ROOT)}:{line}")
        assert not offenders, f"clients that cannot attribute: {offenders}"


# ── The Copilot SDK path (H-181) ────────────────────────────────────────────


class _WireRpc:
    """The JSON-RPC connection to the Copilot CLI, and nothing else.

    ⚠️ Not a stub of the SDK. The REAL ``CopilotClient.create_session`` /
    ``resume_session`` and the REAL ``CopilotSession.send`` build their
    payloads, and only the pipe to the CLI process is replaced. So the params
    asserted are the ones the CLI would receive.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def request(self, method: str, params: dict | None = None, **_kw):
        self.calls.append((method, params or {}))
        if method in ("session.create", "session.resume"):
            return {"sessionId": params["sessionId"]}
        if method == "session.send":
            return {"messageId": "m-1"}
        return {}

    def sent(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]


def _wire_client():
    from copilot import CopilotClient

    client = CopilotClient()
    rpc = _WireRpc()
    client._client = rpc  # connected, as far as the SDK can tell
    return client, rpc


def _copilot_agent(client):
    """A task-manager-shaped agent: the provider dict is built ONCE, with no
    headers, exactly as ``agent-task-manager/agents.py`` builds it."""
    from agent_framework_github_copilot import GitHubCopilotAgent
    from orchestrator.copilot_agent import MetoriteCopilotAgent

    agent = GitHubCopilotAgent(
        instructions="x", client=client,
        default_options={
            "model": "tier-balanced", "mcp_servers": {},
            "provider": {"type": "openai", "base_url": "http://gw/v1",
                         "api_key": "k"},
        },
    )
    agent._started = True
    # The executor's streaming path rebinds these (executor.py, "GitHub
    # Copilot path"). Rebinding here runs the production methods.
    for name in ("_create_session", "_resume_session", "_stream_updates"):
        setattr(agent, name,
                getattr(MetoriteCopilotAgent, name).__get__(agent, type(agent)))
    return agent


def _run_as(who: str, run_id: str, app: str = "tasks") -> None:
    bind_run_context(user=who, member_verified=True, app=app, run_id=run_id)


class TestTheCopilotPathCarriesTheRun:
    """🔴 H-181. Three agents run on the Copilot SDK: task-manager,
    app-builder and apis-config. Once the box serves AI with the deployment
    key (H-152), the Router refuses a model call that names no member.

    The CLI, not our code, makes the model call. Two things reach it:

    - ``provider.headers`` on ``session.create`` / ``session.resume``. It
      holds for the session, and every run creates or resumes its session.
    - ``requestHeaders`` on ``session.send``. It holds for ONE TURN.

    Both are stamped from the run bound on this task, never at build time.
    Mutation-checked 2026-09-26: delete the provider stamp in
    ``session_kwargs_for_this_run`` and the four session tests fail. Delete
    ``request_headers=`` in ``_stream_updates`` and the turn test fails.
    """

    @staticmethod
    def _headers(payload: dict) -> dict:
        return payload["provider"]["headers"]

    def test_session_create_carries_the_RUNS_member_app_and_run(self, secret):
        from acb_auth.member_proof import verify_member

        client, rpc = _wire_client()
        agent = _copilot_agent(client)

        async def go():
            with run_context_scope():
                _run_as("dana@acme.com", "run-1")
                await agent._create_session(True, None)

        asyncio.run(go())
        h = self._headers(rpc.sent("session.create")[0])
        assert h["X-CC-Member"] == "dana@acme.com"
        assert verify_member(h["X-CC-Member-Proof"], secret) == "dana@acme.com"
        assert (h["X-CC-Module"], h["X-CC-Run"]) == ("tasks", "run-1")

    def test_one_agent_bills_EACH_run_and_never_freezes(self):
        """🔴 One agent object serves every person. A provider stamped when
        the agent was built would bill the first person forever."""
        client, rpc = _wire_client()
        agent = _copilot_agent(client)

        async def as_(who, run_id):
            with run_context_scope():
                _run_as(who, run_id)
                await agent._create_session(True, None)

        asyncio.run(as_("dana@acme.com", "run-1"))
        asyncio.run(as_("ravi@acme.com", "run-2"))
        first, second = (self._headers(p) for p in rpc.sent("session.create"))
        assert (first["X-CC-Member"], first["X-CC-Run"]) == ("dana@acme.com", "run-1")
        assert (second["X-CC-Member"], second["X-CC-Run"]) == ("ravi@acme.com", "run-2")
        assert "headers" not in agent._default_options["provider"], (
            "the shared provider dict was written into, so runs race on it")

    def test_a_RESUMED_session_bills_the_run_that_resumed_it(self):
        """A thread reopened later, or by someone else, resumes the SAME CLI
        session. The resume must carry the new run, not the one that made
        the session."""
        client, rpc = _wire_client()
        agent = _copilot_agent(client)

        async def go():
            with run_context_scope():
                _run_as("ravi@acme.com", "run-9")
                await agent._resume_session("sess-1", True)

        asyncio.run(go())
        h = self._headers(rpc.sent("session.resume")[0])
        assert (h["X-CC-Member"], h["X-CC-Run"]) == ("ravi@acme.com", "run-9")

    def _send_one_turn(self, *, provider: bool):
        client, rpc = _wire_client()
        agent = _copilot_agent(client)
        if not provider:
            agent._default_options.pop("provider", None)

        async def go():
            with run_context_scope():
                _run_as("dana@acme.com", "run-3")
                session = await agent._create_session(True, None)

                async def fake_get_or_create(*_a, **_kw):
                    return session

                agent._get_or_create_session = fake_get_or_create
                stream = agent._stream_updates(messages="hi")
                # No CLI answers, so no update ever comes. Stop once the turn
                # has been sent.
                task = asyncio.ensure_future(stream.__anext__())
                for _ in range(100):
                    if rpc.sent("session.send"):
                        break
                    await asyncio.sleep(0.01)
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

        asyncio.run(go())
        sent = rpc.sent("session.send")
        assert sent, "the turn was never sent"
        return sent[0]

    def test_each_TURN_carries_the_run_as_request_headers(self):
        """The per-turn stamp on ``session.send``. It covers a CLI session
        that outlived the run that created it."""
        h = self._send_one_turn(provider=True)["requestHeaders"]
        assert (h["X-CC-Member"], h["X-CC-Module"], h["X-CC-Run"]) == (
            "dana@acme.com", "tasks", "run-3")

    def test_a_NATIVE_session_sends_github_no_member(self):
        """🔴 Review of PR #490: with no BYOK provider the session talks to
        api.githubcopilot.com. The member's email and signed proof must not
        go there."""
        turn = self._send_one_turn(provider=False)
        assert not turn.get("requestHeaders"), turn.get("requestHeaders")

    def test_the_BATCH_and_SUB_AGENT_path_is_stamped_too(self):
        """The batch run and a delegated sub-agent keep the WRAPPER's own
        ``_create_session``. The interceptor that tool injection installs
        (``_apply_copilot_infinite_sessions``) stamps them."""
        from agent_framework_github_copilot import GitHubCopilotAgent
        from orchestrator._copilot_session import _apply_copilot_infinite_sessions

        client, rpc = _wire_client()
        agent = GitHubCopilotAgent(
            instructions="x", client=client,
            default_options={"provider": {"type": "openai",
                                          "base_url": "http://gw/v1",
                                          "api_key": "k"},
                             # think-mode keys ride here, and SDK 1.0 would
                             # raise TypeError on them
                             "thinking": {"type": "enabled"},
                             "model_params": {"reasoning_effort": "high"}},
        )
        assert _apply_copilot_infinite_sessions(agent) is True

        async def go():
            with run_context_scope():
                _run_as("dana@acme.com", "run-4", app="app-workshop")
                await agent._create_session(True, None)

        asyncio.run(go())
        h = self._headers(rpc.sent("session.create")[0])
        assert (h["X-CC-Member"], h["X-CC-Module"], h["X-CC-Run"]) == (
            "dana@acme.com", "app-workshop", "run-4")

    def test_a_stale_stamp_never_outlives_its_run(self):
        """A provider that already carries X-CC-* for one run must not leak
        that member into a run bound to nobody."""
        from acb_llm.attribution import attributed_copilot_provider

        stale = {"type": "openai", "headers": {"X-CC-Member": "dana@acme.com",
                                               "X-Other": "keep"}}
        out = attributed_copilot_provider(stale)
        assert out["headers"] == {"X-Other": "keep"}
        assert stale["headers"]["X-CC-Member"] == "dana@acme.com", "input mutated"


# ── The session member on DIRECT AI routes (2026-09-24) ─────────────────────

class TestTheGatewayNamesTheSessionMember:
    """🔴 Production: Tasks clarify calls reached the meter with no member,
    because a route that calls `acb_llm` directly is not an agent run."""

    async def _resolve(self, monkeypatch, **headers):
        import structlog
        from acb_auth import deps

        monkeypatch.setattr(deps, "_get_internal_token", lambda: "tok")

        async def _as_is(ctx):
            return ctx

        monkeypatch.setattr(deps, "_with_resolved_access", _as_is)
        structlog.contextvars.clear_contextvars()
        await deps.get_current_user(**headers)
        return structlog.contextvars.get_contextvars()

    async def test_a_session_member_is_bound_and_verified(self, monkeypatch):
        ctx = await self._resolve(
            monkeypatch, x_user_email="a@example.com", x_user_role=None,
            authorization="Bearer tok",
        )
        assert ctx.get("user") == "a@example.com"
        assert ctx.get("member_verified") == "1"

    async def test_a_bare_internal_call_binds_nobody(self, monkeypatch):
        ctx = await self._resolve(
            monkeypatch, x_user_email=None, x_user_role=None,
            authorization="Bearer tok",
        )
        assert "user" not in ctx and "member_verified" not in ctx

    async def test_an_unverified_email_header_binds_nobody(self, monkeypatch):
        """No Bearer, so nobody vouched for the header (H-73)."""
        ctx = await self._resolve(
            monkeypatch, x_user_email="a@example.com", x_user_role=None,
            authorization=None,
        )
        assert ctx.get("member_verified") != "1"

    async def test_the_bound_member_reaches_the_Router_headers(self, monkeypatch):
        from acb_llm.attribution import attribution_headers

        await self._resolve(
            monkeypatch, x_user_email="a@example.com", x_user_role=None,
            authorization="Bearer tok",
        )
        assert attribution_headers().get("X-CC-Member") == "a@example.com"
