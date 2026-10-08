"""NS-4b — POST /shell/intent, the command bar's coordinator (navigation_shell.md §6.4-§6.6).

Each case is written so the obvious wrong build fails it:

* done-when 2: the model sees ONLY the jobs the member can open, and a job
  list the client sends is never read;
* done-when 3: each new intent is ONE billed call (`decide`), and a repeat
  within five minutes is none;
* done-when 4: out of credits is the `paused` state with the spec's line;
* done-when 5: the cache key holds the member and the scope, and goes through
  the tenant-prefix wrapper;
* a filled job is a suggestion: only the job's own fields, capped, and only
  when billed completions are on;
* the server's job list and the bar's (`registry.ts`) are one list.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_llm import ChoiceAnswer, DecideUnavailable
from gateway.routes.shell import intent

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_TS = ROOT / "workbench/control_plane/src/lib/shell/registry.ts"
NAV_TS = ROOT / "workbench/control_plane/src/lib/nav.ts"


def member(*features: str, email: str = "m@customer.example", org: str | None = "org-1") -> UserContext:
    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
        organization_id=org,
    )


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.keys: list[object] = []

    async def get(self, k):
        self.keys.append(k)
        return self.store.get(str(k))

    async def setex(self, k, seconds, value):
        self.keys.append(k)
        assert seconds == intent.CACHE_SECONDS
        self.store[str(k)] = value


class World:
    """The decide door, the fill door and the cache, all recorded."""

    def __init__(self) -> None:
        self.decides: list[dict] = []
        self.completions: list[dict] = []
        self.choice = "compose"
        self.confidence: float | None = 0.9
        self.decide_error: BaseException | None = None
        self.routing = False
        self.fill_reply = '{"to": "Priya", "subject": "March invoice", "bcc": "x@evil"}'
        self.redis = FakeRedis()


@pytest.fixture()
def world(monkeypatch) -> World:
    w = World()
    import acb_common.tenant_redis as tr
    import acb_llm
    import acb_llm.routed as routed

    async def fake_decide(state, questions, **kw):
        w.decides.append({"state": state, "questions": questions, **kw})
        if w.decide_error:
            raise w.decide_error
        return {
            "job": ChoiceAnswer(choice=w.choice, probabilities={w.choice: 1.0}, confidence=w.confidence)
        }

    async def fake_completion(**kw):
        w.completions.append(kw)
        msg = SimpleNamespace(content=w.fill_reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)]), "m"

    monkeypatch.setattr(acb_llm, "decide", fake_decide)
    monkeypatch.setattr(routed, "completion_on_router", fake_completion)
    monkeypatch.setattr(routed, "routing_is_on", lambda: w.routing)
    monkeypatch.setattr(tr, "get_tenant_redis", lambda **_k: w.redis)
    monkeypatch.setenv("COMMAND_BAR_AI", "on")
    return w


def ask(q: str, user: UserContext, scope: str | None = None, **extra) -> dict:
    body = intent.IntentRequest(q=q, scope=scope, **extra)
    return asyncio.run(intent.shell_intent(body=body, user=user))


def offered(world: World) -> set[str]:
    return set(world.decides[-1]["questions"]["job"].criteria)


class TestOff:
    def test_off_is_off_and_asks_nothing(self, world, monkeypatch):
        monkeypatch.delenv("COMMAND_BAR_AI")
        assert ask("write to priya", member("email")) == {"kind": "off"}
        assert world.decides == []

    def test_one_word_asks_nothing(self, world):
        assert ask("email", member("email"))["kind"] == "none"
        assert world.decides == []


class TestTheModelSeesOnlyWhatTheMemberCanOpen:
    def test_a_job_behind_a_missing_feature_is_absent(self, world):
        ask("write to priya about march", member("tasks"))
        options = offered(world)
        assert "compose" not in options
        assert "find-person" not in options
        assert {"capture", "plan-day", "edit-profile", "appearance", intent.ASK} <= options

    def test_the_server_ignores_a_job_list_from_the_client(self, world):
        ask("write to priya about march", member("tasks"),
            jobs=[{"id": "compose"}, {"id": "delete-everything"}])
        options = offered(world)
        assert "compose" not in options and "delete-everything" not in options

    def test_a_job_the_member_lacks_is_never_returned_even_if_the_model_names_it(self, world):
        world.choice = "compose"  # the model answers with a job it was not offered
        assert ask("write to priya about march", member("tasks"))["kind"] == "handoff"

    def test_the_member_is_named_as_proven(self, world):
        ask("write to priya about march", member("email", email="who@x.test"))
        call = world.decides[-1]
        assert call["member"] == "who@x.test" and call["member_proven"] is True
        assert call["module_slug"] == "shell"


class TestOneBilledCallAndAFreeRepeat:
    def test_one_decide_call_per_new_intent_and_none_on_a_repeat(self, world):
        user = member("email")
        first = ask("write to priya about march", user, scope="/email")
        second = ask("write to priya about march", user, scope="/email")
        assert len(world.decides) == 1
        assert first == second

    def test_the_key_holds_the_member_and_the_scope(self, world):
        ask("write to priya about march", member("email", email="a@x.test"), scope="/email")
        ask("write to priya about march", member("email", email="b@x.test"), scope="/email")
        ask("write to priya about march", member("email", email="a@x.test"), scope="/tasks")
        assert len(world.decides) == 3

    def test_the_key_goes_through_the_tenant_prefix_wrapper(self, world):
        from acb_common.tenant_redis import TenantKey

        ask("write to priya about march", member("email"))
        assert world.redis.keys and all(isinstance(k, TenantKey) for k in world.redis.keys)

    def test_a_member_with_no_organization_is_never_cached(self, world):
        user = member("email", org=None)
        ask("write to priya about march", user)
        ask("write to priya about march", user)
        assert len(world.decides) == 2 and world.redis.keys == []


class TestPausedAndUnavailable:
    def test_out_of_credits_is_the_paused_state(self, world):
        world.decide_error = DecideUnavailable("insufficient_credits", status=402)
        assert ask("write to priya about march", member("email")) == {
            "kind": "paused", "message": intent.PAUSED_LINE}

    def test_any_other_unavailability_says_nothing(self, world):
        world.decide_error = DecideUnavailable("disabled")
        assert ask("write to priya about march", member("email")) == {"kind": "unavailable"}


class TestTheAnswer:
    def test_not_a_job_hands_the_words_to_the_assistant(self, world):
        world.choice = intent.ASK
        assert ask("who is free on friday", member("email")) == {
            "kind": "handoff", "href": "/chat?q=who+is+free+on+friday"}

    def test_an_unsure_pick_hands_off_too(self, world):
        world.confidence = 0.3
        assert ask("write to priya about march", member("email"))["kind"] == "handoff"

    def test_routing_off_opens_the_job_with_an_empty_form(self, world):
        answer = ask("write to priya about march", member("email"))
        assert answer == {"kind": "job", "job": "compose", "label": "Write an email",
                          "href": "/email?do=compose", "filled": {}}
        assert world.completions == []

    def test_routing_on_fills_only_the_jobs_own_fields_capped(self, world):
        world.routing = True
        world.fill_reply = json.dumps({"to": "Priya", "subject": "x" * 500, "bcc": "x@evil"})
        answer = ask("write to priya about march", member("email"))
        assert set(answer["filled"]) == {"to", "subject"}
        assert len(answer["filled"]["subject"]) == intent.MAX_FIELD_CHARS
        assert "bcc" not in answer["href"] and "fill.to=Priya" in answer["href"]
        assert world.completions[-1]["tier"] == intent.FILL_TIER

    def test_a_reply_that_is_not_json_fills_nothing(self, world):
        world.routing = True
        world.fill_reply = "Sure! I would write to Priya."
        assert ask("write to priya about march", member("email"))["filled"] == {}


class TestOneJobList:
    def _ts_jobs(self) -> dict[str, tuple[str, str]]:
        src = REGISTRY_TS.read_text(encoding="utf-8")
        block = src[src.index("export const JOBS"):src.index("];", src.index("export const JOBS"))]
        out = {}
        for m in re.finditer(r'id: "([^"]+)",.*?app: "([^"]+)",\s*href: "([^"]+)"', block, re.S):
            out[m.group(1)] = (m.group(2), m.group(3))
        return out

    def _nav_features(self) -> dict[str, str | None]:
        src = NAV_TS.read_text(encoding="utf-8")
        out: dict[str, str | None] = {}
        for m in re.finditer(r'href: "([^"]+)",(.*?)launch:', src, re.S):
            f = re.search(r'feature: "([^"]+)"', m.group(2))
            out[m.group(1)] = f.group(1) if f else None
        return out

    def test_the_server_and_the_bar_hold_the_same_jobs(self):
        ts = self._ts_jobs()
        assert ts, "the registry.ts JOBS block was not found"
        py = {j.id: j.href for j in intent.JOBS}
        assert set(py) == set(ts)
        for job_id, (_app, href) in ts.items():
            assert py[job_id] == href, job_id

    def test_each_job_is_gated_on_its_apps_own_feature(self):
        ts, nav = self._ts_jobs(), self._nav_features()
        for job in intent.JOBS:
            app = ts[job.id][0]
            assert app in nav, f"{job.id}: app {app} is not a nav pane"
            assert job.feature == nav[app], f"{job.id}: {job.feature} vs the pane's {nav[app]}"


class TestTheRouteIsMounted:
    def test_the_gateway_serves_shell_intent(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        from fastapi.routing import iter_route_contexts
        from gateway.main import app

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/shell/intent" in paths and "/shell/search" in paths
