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
            with run_context_scope():
                bind_run_context(user=who, app="projects", member_verified=True)
                await client.get_response([Message(role="user", contents=["hi"])])
            return seen["x-cc-member"]

        assert asyncio.run(call_as("dana@acme.com")) == "dana@acme.com"
        assert asyncio.run(call_as("ravi@acme.com")) == "ravi@acme.com"
        assert seen["x-cc-module"] == "projects"
        assert "x-cc-member-proof" in seen

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
        """🔴 The fence for the sixth agent.

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
