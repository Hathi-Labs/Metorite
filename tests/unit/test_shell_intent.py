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
        from acb_llm.routed import run_attribution

        # Who the routed call would bill, read the way the real one reads it.
        w.completions.append({**kw, "_bills": run_attribution()})
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

    def test_the_fill_bills_the_same_member(self, world):
        world.routing = True
        ask("write to priya about march", member("email", email="who@x.test"))
        bills = world.completions[-1]["_bills"]
        assert bills["member"] == "who@x.test" and bills["member_proven"] is True

    def test_the_run_context_is_put_back_after_the_request(self, world):
        from acb_llm.routed import run_attribution

        ask("write to priya about march", member("email", email="who@x.test"))
        assert run_attribution()["member"] is None


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

    def test_a_revoked_app_is_a_new_key_so_a_cached_job_never_outlives_it(self, world):
        ask("write to priya about march", member("email", "tasks"))
        answer = ask("write to priya about march", member("tasks"))
        assert len(world.decides) == 2
        assert answer["kind"] == "handoff", "the cached compose job outlived the email grant"

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

    def test_a_fill_outage_opens_the_job_empty_and_caches_it(self, world, monkeypatch):
        import acb_llm.routed as routed

        world.routing = True

        async def outage(**_kw):
            raise ConnectionError("console away")

        monkeypatch.setattr(routed, "completion_on_router", outage)
        user = member("email")
        first = ask("write to priya about march", user)
        assert first["kind"] == "job" and first["filled"] == {}
        ask("write to priya about march", user)
        assert len(world.decides) == 1, "the pick was billed again after an outage"

    def test_a_slow_pick_is_cut_off_and_says_unavailable(self, world, monkeypatch):
        import acb_llm

        monkeypatch.setattr(intent, "PICK_TIMEOUT_S", 0.01)

        async def slow(*_a, **_k):
            await asyncio.sleep(1)

        monkeypatch.setattr(acb_llm, "decide", slow)
        assert ask("write to priya about march", member("email")) == {"kind": "unavailable"}

    def test_a_slow_fill_opens_the_job_empty(self, world, monkeypatch):
        import acb_llm.routed as routed

        world.routing = True
        monkeypatch.setattr(intent, "FILL_TIMEOUT_S", 0.01)

        async def slow(**_kw):
            await asyncio.sleep(1)

        monkeypatch.setattr(routed, "completion_on_router", slow)
        answer = ask("write to priya about march", member("email"))
        assert answer["kind"] == "job" and answer["filled"] == {}

    def test_the_internal_service_caller_is_no_member_and_is_never_billed(self, world):
        internal = UserContext(email="system:internal", role=UserRole.EMPLOYEE,
                               access=EffectiveAccess(role_granted=frozenset({"*"})))
        assert ask("write to priya about march", internal) == {"kind": "unavailable"}
        assert world.decides == []

    def test_a_reply_that_is_not_json_fills_nothing(self, world):
        world.routing = True
        world.fill_reply = "Sure! I would write to Priya."
        assert ask("write to priya about march", member("email"))["filled"] == {}


class TestOneJobList:
    def _ts_jobs(self) -> dict[str, tuple[str, str]]:
        src = REGISTRY_TS.read_text(encoding="utf-8")
        block = src[src.index("export const JOBS"):src.index("];", src.index("export const JOBS"))]
        out = {}
        for m in re.finditer(
            r'id: "([^"]+)",\s*label: "([^"]+)",.*?app: "([^"]+)",\s*href: "([^"]+)"', block, re.S,
        ):
            out[m.group(1)] = (m.group(3), m.group(4), m.group(2))
        return out

    def _nav_gates(self) -> dict[str, tuple[str | None, bool]]:
        """Each pane's two gates in `nav.ts`: its feature, and `adminOnly`."""
        src = NAV_TS.read_text(encoding="utf-8")
        out: dict[str, tuple[str | None, bool]] = {}
        for m in re.finditer(r'href: "([^"]+)",(.*?)launch:', src, re.S):
            f = re.search(r'feature: "([^"]+)"', m.group(2))
            admin = re.search(r"adminOnly: true", m.group(2)) is not None
            out[m.group(1)] = (f.group(1) if f else None, admin)
        return out

    def test_the_server_and_the_bar_hold_the_same_jobs(self):
        ts = self._ts_jobs()
        assert ts, "the registry.ts JOBS block was not found"
        py = {j.id: j.href for j in intent.JOBS}
        assert set(py) == set(ts)
        labels = {j.id: j.label for j in intent.JOBS}
        for job_id, (_app, href, label) in ts.items():
            assert py[job_id] == href, job_id
            assert labels[job_id] == label, f"{job_id}: the bar says {label!r}, the coordinator {labels[job_id]!r}"

    def test_each_job_is_gated_on_its_apps_own_feature(self):
        ts, nav = self._ts_jobs(), self._nav_gates()
        for job in intent.JOBS:
            app = ts[job.id][0]
            assert app in nav, f"{job.id}: app {app} is not a nav pane"
            feature, admin_only = nav[app]
            assert job.feature == feature, f"{job.id}: {job.feature} vs the pane's {feature}"
            # An `adminOnly` pane has no feature, so the feature gate alone
            # offered its job to EVERY member (§6.4 rule 1).
            assert job.admin == admin_only, f"{job.id}: admin {job.admin} vs the pane's adminOnly {admin_only}"

    def test_the_parser_sees_the_admin_pane(self):
        # The fence above is empty if the regex never reads `adminOnly`.
        assert self._nav_gates()["/settings/organization"] == (None, True)

    def test_the_admin_gate_is_the_one_auth_me_reports(self):
        me = (ROOT / "apps/services/gateway/gateway/routes/admin/me.py").read_text(encoding="utf-8")
        assert f'"is_admin": access.has("{intent.ADMIN_PERMISSION}")' in me


def admin_member(*features: str) -> UserContext:
    granted = {f"feature:{f}" for f in features} | {intent.ADMIN_PERMISSION}
    return UserContext(
        email="admin@customer.example",
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(granted)),
        organization_id="org-1",
    )


class TestAnAdminJobReachesOnlyAnAdmin:
    def test_a_member_who_is_not_an_admin_does_not_hold_invite(self, world):
        # Every feature a job names, and still no admin permission.
        everything = [j.feature for j in intent.JOBS if j.feature]
        assert "invite" not in {j.id for j in intent.held_jobs(member(*everything))}
        ask("invite priya to the company", member(*everything))
        assert "invite" not in offered(world)

    def test_an_admin_holds_invite(self, world):
        assert "invite" in {j.id for j in intent.held_jobs(admin_member())}
        ask("invite priya to the company", admin_member())
        assert "invite" in offered(world)

    def test_the_model_naming_invite_for_a_member_is_a_handoff(self, world):
        world.choice = "invite"
        assert ask("invite priya to the company", member("tasks"))["kind"] == "handoff"


class TestTheRouteIsMounted:
    def test_the_gateway_serves_shell_intent(self):
        # ⚠️ In a FRESH interpreter. This module imports `intent` itself, which
        # registers the route whatever `routes/shell/__init__.py` does, so an
        # in-process check passed with the registering import deleted
        # (verifier of NS-4b, 2026-10-08).
        import subprocess
        import sys

        probe = (
            "import os; os.environ.setdefault('GATEWAY_INTERNAL_TOKEN', 't');"
            "from fastapi.routing import iter_route_contexts;"
            "from gateway.main import app;"
            "p = {getattr(c, 'path', None) for c in iter_route_contexts(app.routes)};"
            "print('/shell/intent' in p, '/shell/search' in p)"
        )
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                             timeout=300, cwd=str(ROOT))
        assert out.stdout.strip().splitlines()[-1] == "True True", out.stderr[-2000:]
