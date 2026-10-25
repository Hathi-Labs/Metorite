"""WS-61 KR-1 — company retrieval: the sources and ``GET /search/company``.

Spec: ``project-docs/specs/knowledge_base.md`` §20.3, §20.4 and §20.9.

Each source calls its app's OWN search. Those functions have their own
suites, R8 ones included. So this file fences only what retrieval adds, and
each case is written so a wrong build fails it:

* **The app's feature gate, again** (one negative case per source). The gate
  sits on each app's router, so a direct call skips it unless the source
  checks. A member without the app gets no hit, and the app's function is
  never called.
* **No data path of its own.** No module of ``gateway/retrieval/`` imports
  ``sqlalchemy``, ``get_db`` or a session opener.
* **No tenant from input.** The route takes ``q``, ``apps``, ``k`` and
  ``after``, and the member it passes on is the session's own object.
  ``after`` is a date (WS-61 CX-1, ``company_context.md`` §4.5).
* **One source at a time**, and a failing source is named in ``skipped``.
* **The command bar keeps its contract.** Five apps in its order, and an item
  of exactly four keys. Knowledge joined the bar at KB-12 (2026-10-18), when
  its pane went live. Artifacts and WhatsApp are sources and not in the bar.
* **Knowledge applies its kill switch** (KR-3). Every member holds
  ``feature:*``, so the feature alone opens nothing. ``KNOWLEDGE_ENABLED`` and
  ``KNOWLEDGE_ORGS`` must allow the org, and the source calls the route with
  ``hybrid=False``, so no model runs.

Mutations this file catches (R7):

* the feature check leaves ``Source.find``, or a source names the wrong
  feature -> ``TestTheFeatureGate``;
* the route takes an org or a member from the query -> ``TestTheRouteTakesNoTenant``;
* a source module imports a session opener -> ``TestNoDataPathOfItsOwn``;
* knowledge leaves ``BAR_APPS``, or artifacts or whatsapp joins it -> ``TestTheRegistry``;
* the knowledge source drops its kill switch -> ``TestTheKnowledgeSwitch``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_auth import get_current_user
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.retrieval import company, sources
from gateway.routes.artifacts import list_artifacts as _real_list_artifacts
from gateway.routes.email.transport.search import search_messages as _real_email_search
from gateway.routes.knowledge.search import search_knowledge as _real_kb_search
from gateway.routes.whatsapp.transport.messages import search_messages as _real_wa_search

ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL = ROOT / "apps/services/gateway/gateway/retrieval"

ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"
OTHER_ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"

#: source key → the feature its router demands.
FEATURES = {
    "tasks": "projects",
    "email": "email",
    "people": "people",
    "crm": "crm",
    "artifacts": "artifacts",
    "whatsapp": "whatsapp",
    "knowledge": "knowledge",
}

#: The REAL route signatures, read at import time, before any fixture swaps them.
REAL_PARAMS = {
    "email": frozenset(inspect.signature(_real_email_search).parameters),
    "artifacts": frozenset(inspect.signature(_real_list_artifacts).parameters),
    "whatsapp": frozenset(inspect.signature(_real_wa_search).parameters),
    "knowledge": frozenset(inspect.signature(_real_kb_search).parameters),
}


def member(*features: str, org: str | None = ORG) -> UserContext:
    return UserContext(
        email="member@customer.example",
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
        organization_id=org,
    )


ALL = tuple(FEATURES.values())


class Apps:
    """Stand-ins for the seven app functions. They record who called and when."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        self.active = 0
        self.max_active = 0
        self.fail: dict[str, BaseException] = {}
        self.delay = 0.0

    async def _enter(self, name: str, user: object, kwargs: dict) -> None:
        self.calls.append((name, user, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01 + self.delay)
        self.active -= 1
        if name in self.fail:
            raise self.fail[name]

    def called(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    async def tasks(self, **kw):
        await self._enter("tasks", kw["user"], kw)
        return {"rows": [
            {"id": "t-1", "title": "Ship the extruder", "project_name": "Hardware",
             "due_at": "2026-10-20T09:00:00+00:00", "snippet": "calibrate the extruder"},
            {"id": "t-2", "title": "Second task", "project_name": None, "snippet": None},
        ]}

    async def email(self, **kw):
        await self._enter("email", kw["user"], kw)
        return {"emails": [
            {"id": "m-1", "subject": "Invoice March",
             "from_address": {"name": "Priya", "email": "p@x.test"},
             "highlight": "the <mark>invoice</mark> for March", "snippet": "plain",
             "received_at": "2026-10-01T08:00:00+00:00"},
            {"id": "m-2", "subject": "  ", "from_address": {"email": "q@x.test"},
             "highlight": "", "snippet": "x" * 900, "received_at": None},
        ], "total": 2}

    async def people(self, **kw):
        await self._enter("people", kw["user"], kw)
        return SimpleNamespace(rows=[
            {"id": "p-1", "name": "Priya Rao", "title": "Firmware lead",
             "bio": "Builds the firmware.", "skills": ["secret-skill"],
             "resume_summary": "secret resume"},
        ])

    async def crm(self, user, q, limit):
        await self._enter("crm", user, {"q": q, "limit": limit})
        return [
            ("deal", {"id": "d-1", "name": "Acme renewal", "organization_name": "Acme Corp",
                      "last_activity_at": "2026-10-02T10:00:00+00:00"}),
            ("contact", {"id": "c-1", "first_name": "Meera", "last_name": "S",
                         "email": "meera@acme.test"}),
        ]

    async def crm_access(self, user):
        await self._enter("crm_access", user, {})

    async def artifacts(self, **kw):
        await self._enter("artifacts", kw["_user"], kw)
        return {"rows": [
            {"id": "a-1", "title": "Q3 deck", "kind": "pdf", "session_id": "s 1",
             "last_activity_at": "2026-10-03T00:00:00+00:00"},
            {"id": "a-2", "title": None, "path": "outputs/report.md", "kind": "document",
             "session_id": None},
        ], "limit": 5, "offset": 0}

    async def whatsapp(self, **kw):
        await self._enter("whatsapp", kw["user"], kw)
        return [
            {"id": "w-1", "chat_id": "c", "wa_message_id": "x", "sender_name": "Ravi",
             "chat_name": "Acme buyers", "body_text": "price is 42L",
             "sent_at": "2026-10-04T00:00:00+00:00"},
            {"id": "w-2", "chat_id": "c", "wa_message_id": "y", "sender_name": "",
             "chat_name": None, "body_text": "", "transcript_text": "voice words"},
        ]

    async def knowledge(self, **kw):
        await self._enter("knowledge", kw["user"], kw)
        return {"query": kw["q"], "degraded": True, "hits": [
            {"page_id": "k-1", "space_id": "s-1", "space_name": "Company",
             "title": "Returns policy", "heading_path": "Returns policy > Refund window",
             "text": "Refunds within 30 days.", "score": 0.03,
             "updated_at": "2026-10-05T00:00:00+00:00", "href": "/knowledge/pages/k-1"},
            # A second chunk of the same page: the source keeps the first.
            {"page_id": "k-1", "space_id": "s-1", "space_name": "Company",
             "title": "Returns policy", "heading_path": "Returns policy > Exceptions",
             "text": "second chunk", "score": 0.02,
             "updated_at": "2026-10-05T00:00:00+00:00", "href": "/knowledge/pages/k-1"},
            {"page_id": "k-2", "space_id": "s-2", "space_name": "Ops",
             "title": "", "heading_path": None, "text": "y" * 900, "score": 0.01,
             "updated_at": None, "href": "/knowledge/pages/k-2"},
        ]}


@pytest.fixture()
def apps(monkeypatch) -> Apps:
    import acb_memory.artifacts as registry
    import gateway.routes.artifacts as artifacts_route
    import gateway.routes.email.transport.search as email_search
    import gateway.routes.knowledge.search as kb_search
    import gateway.routes.people.directory as directory
    import gateway.routes.projects.search as projects_search
    import gateway.routes.whatsapp.transport.messages as wa_messages
    from acb_common import get_settings
    from gateway.routes.crm import access, records

    f = Apps()
    monkeypatch.setattr(projects_search, "search_tasks", f.tasks)
    monkeypatch.setattr(email_search, "search_messages", f.email)
    monkeypatch.setattr(directory, "list_directory", f.people)
    monkeypatch.setattr(records, "search_records", f.crm, raising=False)
    monkeypatch.setattr(access, "require_crm_access", f.crm_access)
    monkeypatch.setattr(artifacts_route, "list_artifacts", f.artifacts)
    monkeypatch.setattr(registry, "enabled", lambda: True)
    monkeypatch.setattr(wa_messages, "search_messages", f.whatsapp)
    monkeypatch.setattr(kb_search, "search_knowledge", f.knowledge)
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "crm_orgs", ORG, raising=False)
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", ORG, raising=False)
    return f


def find(key: str, user: UserContext, q: str = "acme", k: int = 5) -> list[sources.Hit]:
    return asyncio.run(sources.REGISTRY[key].find(user, q, k))


def run(user: UserContext, q: str = "acme", apps: str | None = None, k: int = 5) -> dict:
    return asyncio.run(company.company_search(q=q, apps=apps, k=k, user=user))


# ── The registry ────────────────────────────────────────────────────────────

class TestTheRegistry:
    def test_seven_sources_each_with_its_routers_feature(self):
        assert {k: s.feature for k, s in sources.REGISTRY.items()} == FEATURES
        assert list(sources.REGISTRY) == list(FEATURES)
        for key, source in sources.REGISTRY.items():
            assert source.key == key and source.label

    def test_the_bar_is_a_projection_of_five_sources_in_its_order(self):
        from gateway.routes.shell import search as shell

        # Five since 2026-10-18: Knowledge joined at KB-12, last.
        assert list(shell.PROVIDERS) == ["tasks", "email", "people", "crm", "knowledge"]
        for key, (feature, label, _fn) in shell.PROVIDERS.items():
            assert (feature, label) == (sources.REGISTRY[key].feature,
                                        sources.REGISTRY[key].label)

    def test_a_bar_item_keeps_exactly_four_keys(self, apps):
        from gateway.routes.shell import search as shell

        answer = asyncio.run(shell.shell_search(q="acme", scope=None, user=member(*ALL)))
        apps_seen = [g["app"] for g in answer["groups"]]
        assert apps_seen == ["tasks", "email", "people", "crm", "knowledge"]
        for group in answer["groups"]:
            for item in group["items"]:
                assert set(item) == {"kind", "title", "hint", "href"}
        # Artifacts and WhatsApp are sources, and the bar never asks them.
        for key in ("artifacts", "whatsapp"):
            assert key not in apps.called()

    def test_knowledge_is_in_the_bar_since_kb12(self):
        """KB-12 (2026-10-18): the pane is live, so Knowledge is a group of the bar.

        It was a source only from KR-3 to KB-12. The bar asks it like the
        other apps, and the scope token of the app puts it first.
        """
        from gateway.routes.shell import search as shell

        assert "knowledge" in sources.REGISTRY
        assert "knowledge" in shell.BAR_APPS
        assert "knowledge" in shell.PROVIDERS
        assert shell.SCOPE_APPS["/knowledge"] == "knowledge"
        assert shell._order("/knowledge")[0] == "knowledge"

    def test_the_bar_asks_knowledge_with_no_model(self, apps):
        """A bar search spends no credit: the source sends ``hybrid=False``."""
        from gateway.routes.shell import search as shell

        answer = asyncio.run(shell.shell_search(q="refund", scope=None, user=member("knowledge")))
        assert [g["app"] for g in answer["groups"]] == ["knowledge"]
        kw = next(kw for n, _, kw in apps.calls if n == "knowledge")
        assert kw["hybrid"] is False
        assert kw["k"] == shell.PER_APP
        assert answer["groups"][0]["items"][0] == {
            "kind": "page", "title": "Returns policy", "hint": "Knowledge · Company",
            "href": "/knowledge/pages/k-1"}


# ── The feature gate ────────────────────────────────────────────────────────

class TestTheFeatureGate:
    @pytest.mark.parametrize("key", list(FEATURES))
    def test_a_member_without_the_app_gets_no_hit_and_no_call(self, apps, key):
        user = member(*[f for f in ALL if f != FEATURES[key]])
        assert find(key, user) == []
        assert apps.calls == [], f"the {key} source called its app without the feature"

    @pytest.mark.parametrize("key", list(FEATURES))
    def test_a_member_with_only_the_app_gets_its_hits(self, apps, key):
        # Non-vacuity: the negative case above passes on a source that never calls.
        hits = find(key, member(FEATURES[key]))
        assert hits and {h.app for h in hits} == {key}

    @pytest.mark.parametrize("key", list(FEATURES))
    def test_the_route_leaves_out_an_app_the_member_lacks(self, apps, key):
        user = member(*[f for f in ALL if f != FEATURES[key]])
        answer = run(user)
        assert key not in {h["app"] for h in answer["hits"]}
        assert key not in answer["skipped"]
        assert key not in apps.called()

    def test_a_member_with_no_app_gets_nothing(self, apps):
        assert run(member()) == {"query": "acme", "hits": [], "skipped": []}
        assert apps.calls == []

    def test_the_crm_kill_switch_answers_before_the_access_rule(self, apps, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
        assert find("crm", member("crm")) == []
        assert apps.calls == []

    def test_an_org_off_the_crm_list_gets_no_crm_hit(self, apps):
        assert find("crm", member("crm", org=OTHER_ORG)) == []
        assert apps.calls == []

    def test_the_crm_access_rule_runs_before_the_search(self, apps):
        apps.fail["crm_access"] = HTTPException(status_code=403, detail="no access")
        answer = run(member("crm", "projects"))
        assert answer["skipped"] == ["crm"]
        assert "crm" not in apps.called()
        assert {h["app"] for h in answer["hits"]} == {"tasks"}

    def test_with_the_artifact_registry_off_no_artifact_hit(self, apps, monkeypatch):
        import acb_memory.artifacts as registry

        monkeypatch.setattr(registry, "enabled", lambda: False)
        assert find("artifacts", member("artifacts")) == []
        assert apps.calls == []


class TestTheKnowledgeSwitch:
    """KR-3. Every member holds ``feature:*``, so the switch is the gate."""

    def test_with_knowledge_off_no_hit_and_no_call(self, apps, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "knowledge_enabled", False, raising=False)
        assert find("knowledge", member("knowledge")) == []
        assert apps.calls == []

    def test_an_org_off_the_knowledge_list_gets_no_hit(self, apps):
        assert find("knowledge", member("knowledge", org=OTHER_ORG)) == []
        assert apps.calls == []

    def test_no_org_gets_no_hit_also_with_star(self, apps, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "knowledge_orgs", "*", raising=False)
        assert find("knowledge", member("knowledge", org=None)) == []
        assert apps.calls == []

    def test_the_source_runs_no_model(self, apps):
        find("knowledge", member("knowledge"))
        kw = next(kw for n, _, kw in apps.calls if n == "knowledge")
        assert kw["hybrid"] is False

    def test_one_hit_for_each_page(self, apps):
        hits = find("knowledge", member("knowledge"))
        assert [h.id for h in hits] == ["k-1", "k-2"]


# ── No data path of its own ─────────────────────────────────────────────────

#: Names that open a database session or carry SQL. A source may call an app
#: function that opens one, and it may never open one itself.
FORBIDDEN = frozenset({
    "sqlalchemy", "get_db", "get_session", "get_session_factory", "get_engine",
    "tenant_session", "_tenant_session", "organization_scope", "text",
    "create_engine", "create_async_engine", "asyncpg", "psycopg",
})


class TestNoDataPathOfItsOwn:
    def test_no_module_imports_sql_or_a_session_opener(self):
        files = sorted(RETRIEVAL.glob("*.py"))
        assert {p.name for p in files} >= {"sources.py", "company.py"}  # non-vacuity
        found = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                    names |= {a.asname for a in node.names if a.asname}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                    names |= {a.name for a in node.names} | {a.asname for a in node.names if a.asname}
                else:
                    continue
                for name in names & FORBIDDEN:
                    found.append(f"{path.name}:{node.lineno} imports {name}")
        assert found == []


# ── No tenant from input ────────────────────────────────────────────────────

class TestTheRouteTakesNoTenant:
    def test_the_route_takes_the_words_the_apps_k_and_after_and_nothing_else(self):
        # CX-1 added `after`, a date (company_context.md §4.5). It names no
        # org and no member. KB-9a added `background`, the route's own
        # BackgroundTasks, which FastAPI fills and no request can set.
        params = inspect.signature(company.company_search).parameters
        assert set(params) == {"q", "apps", "k", "after", "verified", "user", "background"}
        assert params["user"].default.dependency is get_current_user
        assert params["background"].annotation in (BackgroundTasks, "BackgroundTasks")

    def test_an_org_or_a_member_in_the_request_changes_nothing(self, apps):
        session = member(*ALL)
        app = FastAPI()
        app.include_router(company.router)
        app.dependency_overrides[get_current_user] = lambda: session
        client = TestClient(app)
        res = client.get(
            "/search/company",
            params={"q": "acme", "organization_id": OTHER_ORG, "org": OTHER_ORG,
                    "tenant": OTHER_ORG, "user": "boss@other.example",
                    "email": "boss@other.example"},
            headers={"X-Organization-Id": OTHER_ORG},
        )
        assert res.status_code == 200, res.text
        assert apps.calls, "non-vacuity: the sources ran"
        assert all(u is session for _, u, _ in apps.calls)
        assert session.organization_id == ORG
        assert session.email == "member@customer.example"

    def test_a_signed_out_caller_is_refused(self, apps):
        with pytest.raises(HTTPException) as e:
            run(UserContext(email=None, role=UserRole.EMPLOYEE))
        assert e.value.status_code == 401
        assert apps.calls == []


# ── The answer ──────────────────────────────────────────────────────────────

class TestTheAnswer:
    def test_each_hit_has_the_eight_fields(self, apps):
        for hit in run(member(*ALL))["hits"]:
            assert list(hit) == ["app", "kind", "id", "title", "hint", "snippet", "when", "href"]

    def test_the_hits_interleave_by_rank(self, apps):
        order = [(h["app"], h["id"]) for h in run(member(*ALL))["hits"]]
        firsts = order[:7]
        assert [a for a, _ in firsts] == list(FEATURES)
        assert [i for _, i in firsts] == ["t-1", "m-1", "p-1", "d-1", "a-1", "w-1", "k-1"]
        assert [a for a, _ in order[7:]] == [
            "tasks", "email", "crm", "artifacts", "whatsapp", "knowledge"]

    def test_a_named_app_goes_first(self, apps):
        hits = run(member(*ALL), apps="crm, email")["hits"]
        assert [h["app"] for h in hits[:3]] == ["crm", "email", "tasks"]
        assert apps.called()[:3] == ["crm_access", "crm", "email"]

    def test_an_unknown_app_is_a_422(self, apps):
        with pytest.raises(HTTPException) as e:
            run(member(*ALL), apps="tasks,ledger")
        assert e.value.status_code == 422 and "ledger" in e.value.detail
        assert apps.calls == []

    @pytest.mark.parametrize("q", ["a", " a ", "x" * 301])
    def test_the_words_must_be_2_to_300_characters(self, apps, q):
        with pytest.raises(HTTPException) as e:
            run(member(*ALL), q=q)
        assert e.value.status_code == 422
        assert apps.calls == []

    def test_k_caps_each_app(self, apps):
        hits = run(member(*ALL), k=1)["hits"]
        assert len(hits) == 7
        assert [kw.get("limit") for n, _, kw in apps.calls if n == "tasks"] == [1]

    def test_the_sources_never_overlap(self, apps):
        run(member(*ALL))
        assert apps.max_active == 1

    @pytest.mark.parametrize("error", [HTTPException(status_code=422, detail="too short"),
                                       RuntimeError("db down"), TimeoutError()])
    def test_a_failing_source_is_skipped_and_the_rest_answer(self, apps, error):
        apps.fail["email"] = error
        answer = run(member(*ALL))
        assert answer["skipped"] == ["email"]
        assert "email" not in {h["app"] for h in answer["hits"]}
        assert {h["app"] for h in answer["hits"]} == set(FEATURES) - {"email"}

    def test_a_slow_source_is_skipped(self, apps, monkeypatch):
        monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 0.001)
        answer = run(member("projects", "people"))
        assert answer["hits"] == [] and answer["skipped"] == ["tasks", "people"]

    def test_one_budget_for_all_sources(self, apps, monkeypatch):
        monkeypatch.setattr(company, "TOTAL_BUDGET_S", 0.08)
        apps.delay = 0.04
        answer = run(member(*ALL))
        assert len(apps.calls) < 7
        assert answer["skipped"], "a source past the budget is named"

    def test_each_source_reads_its_own_row(self, apps):
        hits = {h["id"]: h for h in run(member(*ALL))["hits"]}
        assert hits["t-1"] == {
            "app": "tasks", "kind": "task", "id": "t-1", "title": "Ship the extruder",
            "hint": "Task · Hardware", "snippet": "calibrate the extruder",
            "when": "2026-10-20T09:00:00+00:00", "href": "/projects?task=t-1"}
        assert hits["t-2"]["hint"] == "Task · Projects" and hits["t-2"]["snippet"] == ""
        # Email: the highlight with no <mark>, else the snippet, clipped.
        assert hits["m-1"]["snippet"] == "the invoice for March"
        assert hits["m-1"]["when"] == "2026-10-01T08:00:00+00:00"
        assert hits["m-1"]["href"] == "/email?email=m-1"
        assert hits["m-2"]["title"] == "(untitled)"
        assert len(hits["m-2"]["snippet"]) == sources.SNIPPET_CHARS
        # People: the bio and nothing of the HR half.
        assert hits["p-1"]["snippet"] == "Builds the firmware."
        assert "secret" not in str(hits["p-1"])
        assert hits["p-1"]["when"] is None and hits["p-1"]["href"] == "/people/p-1"
        # CRM: the kind's field, the activity, the sheet link.
        assert hits["d-1"]["kind"] == "deal" and hits["d-1"]["snippet"] == "Acme Corp"
        assert hits["d-1"]["when"] == "2026-10-02T10:00:00+00:00"
        assert hits["d-1"]["href"] == "/crm?tab=deals&deal=d-1"
        assert hits["c-1"]["title"] == "Meera S"
        # Artifacts: no text, the session link when the row has one.
        assert hits["a-1"]["snippet"] == "" and hits["a-1"]["href"] == "/artifacts?session=s%201"
        assert hits["a-2"]["title"] == "outputs/report.md" and hits["a-2"]["href"] == "/artifacts"
        # WhatsApp: the body, else the transcript. The app takes no record yet.
        assert hits["w-1"]["snippet"] == "price is 42L" and hits["w-1"]["href"] == "/whatsapp"
        assert hits["w-2"]["snippet"] == "voice words"
        # Knowledge: the chunk text, the page's update, the page link.
        assert hits["k-1"] == {
            "app": "knowledge", "kind": "page", "id": "k-1", "title": "Returns policy",
            "hint": "Knowledge · Company", "snippet": "Refunds within 30 days.",
            "when": "2026-10-05T00:00:00+00:00", "href": "/knowledge/pages/k-1"}
        assert hits["k-2"]["title"] == "(untitled)" and hits["k-2"]["when"] is None
        assert len(hits["k-2"]["snippet"]) == sources.SNIPPET_CHARS


class TestEveryRouteParameterIsNamed:
    """A direct call that leaves out a `Query(...)` parameter passes the
    `Query` object itself, so each source names every one."""

    @pytest.mark.parametrize(("key", "name"), [
        ("email", "email"), ("artifacts", "artifacts"), ("whatsapp", "whatsapp"),
        ("knowledge", "knowledge")])
    def test_every_parameter_is_named(self, apps, key, name):
        find(key, member(FEATURES[key]))
        passed = set(next(kw for n, _, kw in apps.calls if n == name))
        assert passed == REAL_PARAMS[key], (
            f"the route has {sorted(REAL_PARAMS[key] - passed)} that the source does not pass"
        )


class TestTheRouteIsMounted:
    def test_the_gateway_serves_search_company(self, monkeypatch):
        # `main.py` mounts every router inside `try/except: pass`, so an import
        # error would hide the route. This finds it.
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        from fastapi.routing import iter_route_contexts
        from gateway.main import app

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/search/company" in paths
        assert "/shell/search" in paths  # non-vacuity: the walk sees mounted routes


# ── KB-3c: the consumer picks meaning ───────────────────────────────────────

class TestMeaningIsTheConsumers:
    """WS-61 KB-3c (``knowledge_base.md`` §20.4). The assistant's route asks
    the knowledge source for ``hybrid`` when ``KNOWLEDGE_EMBED`` names the
    org. The command bar never asks, so a keystroke costs no embed.

    Mutations this class catches (R7): the bar's provider passes
    ``hybrid=True`` -> ``test_the_bar_stays_lexical``; the route stops
    passing it -> ``test_the_route_asks_for_meaning_when_allowed``; the route
    ignores the flag -> ``test_the_route_stays_lexical_when_not_allowed``.
    """

    @staticmethod
    def _embed(monkeypatch, orgs: str) -> None:
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "knowledge_embed", orgs, raising=False)

    @staticmethod
    def _hybrid(apps: Apps) -> list[bool]:
        return [kw["hybrid"] for n, _, kw in apps.calls if n == "knowledge"]

    def test_only_knowledge_has_meaning(self):
        assert [k for k, s in sources.REGISTRY.items() if s.meaning] == ["knowledge"]

    def test_find_defaults_to_lexical(self, apps, monkeypatch):
        self._embed(monkeypatch, ORG)
        find("knowledge", member("knowledge"))
        assert self._hybrid(apps) == [False]

    def test_the_route_asks_for_meaning_when_allowed(self, apps, monkeypatch):
        self._embed(monkeypatch, ORG)
        run(member(*ALL))
        assert self._hybrid(apps) == [True]

    def test_the_route_stays_lexical_when_not_allowed(self, apps, monkeypatch):
        self._embed(monkeypatch, OTHER_ORG)
        run(member(*ALL))
        assert self._hybrid(apps) == [False]

    def test_the_route_stays_lexical_with_knowledge_off(self, apps, monkeypatch):
        from acb_common import get_settings

        self._embed(monkeypatch, "*")
        monkeypatch.setattr(get_settings(), "knowledge_orgs", OTHER_ORG, raising=False)
        run(member(*ALL))
        assert self._hybrid(apps) == []  # the switch is closed: no call at all

    def test_a_source_without_meaning_gets_no_hybrid(self, apps, monkeypatch):
        self._embed(monkeypatch, "*")
        run(member(*ALL))
        for name in ("email", "whatsapp"):
            kw = next(kw for n, _, kw in apps.calls if n == name)
            assert kw["hybrid"] is False, f"{name} embedded the query"

    def test_the_bar_stays_lexical(self, apps, monkeypatch):
        """The bar's provider for knowledge, as KB-12 projects it. With the
        flag on, it still asks for the lexical half only."""
        from gateway.routes.shell import search as shell

        self._embed(monkeypatch, ORG)
        provider = shell._projected("knowledge")
        items = asyncio.run(provider(member("knowledge"), "refund"))
        assert items, "non-vacuity: the provider ran"
        assert self._hybrid(apps) == [False]
