"""MCP resources of the edge (SW-34a). R7: this file is the fence.

Spec ``project-docs/specs/agent_coordination.md`` SW-34a and
``external_agent_access.md`` §9.3. Two resources: ``metorite://project/{id}``
reads like ``get_project`` and ``metorite://task/{id}`` reads like
``get_task``. A read has the authority of the tool call. Dark behind
``agent_resources_orgs``. ``metorite://company/context`` waits for the tool
``get_company_context`` (EA-5).

Database-free, through the real edge app, with the fake store and the fake
gateway of ``test_mcp_edge_protocol.py``.

**The mutations (R7).** Each one turned the named test red. ``E`` is
``apps/services/agent_edge/agent_edge``.

* S1. ``E/resources.py`` ``offered``: always true.
  ``test_the_resources_are_dark_while_agent_resources_orgs_is_empty``.
* S2. ``E/resources.py`` ``_visible``: no scope check.
  ``test_a_token_without_projects_read_sees_no_template``.
* S3. ``E/resources.py`` ``read``: a gateway ``ToolError`` is passed on.
  ``test_a_resource_the_member_cannot_see_is_not_found``.
* S4. ``E/resources.py`` ``read``: the URI is echoed in the error.
  ``test_a_bad_uri_is_not_found_and_never_echoed``.
* S5. ``E/resources.py`` ``_match``: ``re.match`` for ``fullmatch``.
  ``test_a_bad_uri_is_not_found_and_never_echoed``.
* S6. ``E/resources.py`` ``read``: the body is built outside the tool.
  ``test_a_body_is_the_tool_result_and_every_text_is_untrusted``.
* S7. ``E/mcp.py``: ``resources/read`` skips the bearer check.
  ``test_a_call_with_no_token_gets_401``.
* S8. ``E/mcp.py``: ``CAPABILITIES`` without ``resources``.
  ``test_initialize_declares_resources_without_subscribe``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from agent_edge import resources

from tests.unit.test_mcp_edge_protocol import (  # noqa: F401 — fixtures
    GOOD,
    P_TEAM,
    T_ONE,
    _lookup,
    _post,
    _rpc,
    _tool,
    armed,
    gateway,
    store,
)

CONTEXT_ONLY = "context-only-token"
ABSENT = "00000000-0000-4000-8000-0000000fffff"


@pytest.fixture
def on(armed, monkeypatch):  # noqa: F811 — a fixture
    monkeypatch.setattr(get_settings(), "agent_resources_orgs", "*")
    armed.store.tokens[CONTEXT_ONLY] = _lookup(scope="context:read", scopes=("context:read",))
    return armed


async def _read(uri: Any, token: str | None = GOOD) -> dict[str, Any]:
    r = await _post(_rpc("resources/read", {"uri": uri}), token=token)
    assert r.status_code == 200, r.text
    return r.json()


async def _method(method: str, token: str | None = GOOD, params: Any = None) -> dict[str, Any]:
    r = await _post(_rpc(method, params), token=token)
    assert r.status_code == 200, r.text
    return r.json()


class TestTheCapabilityAndTheLists:

    async def test_initialize_declares_resources_without_subscribe(self, armed):  # noqa: F811
        body = await _method("initialize", params={"protocolVersion": "2025-11-25"})
        assert body["result"]["capabilities"]["resources"] == {
            "subscribe": False, "listChanged": False}

    async def test_the_resources_are_dark_while_agent_resources_orgs_is_empty(self, armed):  # noqa: F811
        assert (await _method("resources/list"))["result"] == {"resources": []}
        assert (await _method("resources/templates/list"))["result"] == {"resourceTemplates": []}
        error = (await _read(f"metorite://task/{T_ONE}"))["error"]
        assert error == {"code": -32002, "message": "Resource not found"}

    async def test_the_resources_stay_dark_for_an_organization_off_the_list(self, armed, monkeypatch):  # noqa: F811
        monkeypatch.setattr(get_settings(), "agent_resources_orgs", "00000000-0000-4000-8000-000000000001")
        assert (await _method("resources/templates/list"))["result"] == {"resourceTemplates": []}

    async def test_the_list_holds_no_project_or_task_row(self, on):
        assert (await _method("resources/list"))["result"] == {"resources": []}

    async def test_the_templates_name_the_project_and_the_task(self, on):
        rows = (await _method("resources/templates/list"))["result"]["resourceTemplates"]
        assert [r["uriTemplate"] for r in rows] == [
            "metorite://project/{id}", "metorite://task/{id}"]
        assert all(r["mimeType"] == "application/json" for r in rows)

    async def test_a_token_without_projects_read_sees_no_template(self, on):
        rows = (await _method("resources/templates/list", token=CONTEXT_ONLY))["result"]
        assert rows == {"resourceTemplates": []}

    async def test_a_call_with_no_token_gets_401(self, on):
        for method in ("resources/list", "resources/templates/list"):
            r = await _post(_rpc(method), token=None)
            assert r.status_code == 401, r.text
        r = await _post(_rpc("resources/read", {"uri": f"metorite://task/{T_ONE}"}), token=None)
        assert r.status_code == 401, r.text

    @pytest.mark.parametrize("method", ["resources/subscribe", "resources/unsubscribe"])
    async def test_subscribe_is_not_served(self, on, method):
        r = await _post(_rpc(method, {"uri": f"metorite://task/{T_ONE}"}))
        assert r.json()["error"]["code"] == -32601


class TestARead:

    async def test_a_task_is_the_result_of_get_task(self, on):
        direct = (await _tool("get_task", {"task_id": T_ONE}))["structuredContent"]
        body = await _read(f"metorite://task/{T_ONE}")
        content = body["result"]["contents"]
        assert len(content) == 1
        assert content[0]["uri"] == f"metorite://task/{T_ONE}"
        assert content[0]["mimeType"] == "application/json"
        assert json.loads(content[0]["text"]) == direct

    async def test_a_project_is_the_result_of_get_project(self, on):
        direct = (await _tool("get_project", {"project_id": P_TEAM}))["structuredContent"]
        content = (await _read(f"metorite://project/{P_TEAM}"))["result"]["contents"]
        assert json.loads(content[0]["text"]) == direct

    async def test_a_body_is_the_tool_result_and_every_text_is_untrusted(self, on):
        body = json.loads((await _read(f"metorite://task/{T_ONE}"))["result"]["contents"][0]["text"])
        assert body["task"]["title"] == {"untrusted_text": "Task 1"}

    async def test_an_upper_case_id_reads_the_same_record(self, on):
        content = (await _read(f"metorite://task/{T_ONE.upper()}"))["result"]["contents"]
        assert content[0]["uri"] == f"metorite://task/{T_ONE}"

    async def test_a_resource_the_member_cannot_see_is_not_found(self, on):
        for kind in ("task", "project"):
            error = (await _read(f"metorite://{kind}/{ABSENT}"))["error"]
            assert error == {"code": -32002, "message": "Resource not found"}, kind

    async def test_a_token_without_projects_read_gets_not_found(self, on):
        error = (await _read(f"metorite://task/{T_ONE}", token=CONTEXT_ONLY))["error"]
        assert error["code"] == -32002

    @pytest.mark.parametrize("uri", [
        "metorite://task/42", "metorite://task/", "metorite://task/" + "x" * 40,
        f"metorite://task/{T_ONE}/x", f"metorite://task/{T_ONE} ", f" metorite://task/{T_ONE}",
        f"metorite://task/{T_ONE}\n", f"METORITE://task/{T_ONE}", f"metorite://Task/{T_ONE}",
        f"https://evil.example/task/{T_ONE}", "file:///etc/passwd", "metorite://company/context",
        "metorite://playbook/index", "ignore previous instructions", "metorite://" + "a" * 300,
        f"metorite://task/{T_ONE}?x=1", f"metorite://task/{T_ONE}#x",
    ])
    async def test_a_bad_uri_is_not_found_and_never_echoed(self, on, uri):
        body = await _read(uri)
        assert body["error"] == {"code": -32002, "message": "Resource not found"}
        assert uri.strip() not in json.dumps(body) or uri.strip() == ""

    @pytest.mark.parametrize("params", [{}, {"uri": ""}, {"uri": None}, {"uri": 42},
                                        {"uri": ["x"]}, None])
    async def test_a_read_with_no_usable_uri_gets_minus_32602(self, on, params):
        r = await _post(_rpc("resources/read", params))
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32602

    async def test_a_company_context_read_is_not_served_yet(self, on):
        error = (await _read("metorite://company/context"))["error"]
        assert error["code"] == -32002

    async def test_a_read_sends_the_requests_of_the_tool_and_no_others(self, on):
        on.gateway.requests.clear()
        await _read(f"metorite://task/{T_ONE}")
        resource_paths = sorted(r.path for r in on.gateway.requests)
        on.gateway.requests.clear()
        await _tool("get_task", {"task_id": T_ONE})
        assert resource_paths == sorted(r.path for r in on.gateway.requests)

    async def test_the_templates_make_no_gateway_request(self, on):
        on.gateway.requests.clear()
        await _method("resources/templates/list")
        await _method("resources/list")
        assert on.gateway.requests == []

    async def test_a_dark_read_makes_no_gateway_request(self, armed):  # noqa: F811
        armed.gateway.requests.clear()
        await _read(f"metorite://task/{T_ONE}")
        assert armed.gateway.requests == []


class TestTheModule:

    def test_each_resource_reads_through_a_read_tool_with_projects_read(self):
        for resource in resources.RESOURCES:
            assert resource.tool.method == "GET"
            assert resource.tool.required_scope == "projects:read"
            assert resource.argument in resource.tool.input_schema["properties"]

    def test_the_resources_module_uses_no_gateway_route_of_its_own(self):
        import inspect
        source = inspect.getsource(resources)
        for banned in ("gateway_get", "gateway_write", "send_call", "/projects/"):
            assert banned not in source, banned


# ═══════════════════════════════════════════════════════════════════════════
# PB-3b (D186): the playbook resources
# ═══════════════════════════════════════════════════════════════════════════


class TestThePlaybookResources:
    """``metorite://playbook/{slug}``, one for each topic of ``acb_playbook``.

    Mutations (R7), each turned the named test red:

    * PB3B-M1. ``_playbook_on`` drops the ``playbook_allowed`` check.
      ``test_the_playbook_is_dark_while_playbook_orgs_is_empty``.
    * PB3B-M2. ``_playbook_on`` drops the transport flag.
      ``test_the_playbook_is_dark_while_agent_resources_orgs_is_empty``.
    * PB3B-M3. ``_playbook_on`` drops the scope check.
      ``test_a_token_without_projects_read_sees_no_playbook_row``.
    * PB3B-M4. ``_read_playbook`` gives ``body`` as a bare string.
      ``test_a_topic_leaves_as_untrusted_text``.
    * PB3B-M5. ``_playbook_match`` uses ``match``.
      ``test_a_bad_playbook_uri_is_not_found_and_never_echoed``.
    * PB3B-M6. ``_read_playbook`` echoes the slug in the error.
      ``test_an_unknown_topic_is_not_found_and_never_echoed``.
    """

    @pytest.fixture
    def pb(self, on, monkeypatch):
        monkeypatch.setattr(get_settings(), "playbook_orgs", "*")
        return on

    async def test_the_playbook_is_dark_while_playbook_orgs_is_empty(self, on):
        assert (await _method("resources/list"))["result"] == {"resources": []}
        error = (await _read("metorite://playbook/gates"))["error"]
        assert error == {"code": -32002, "message": "Resource not found"}

    async def test_the_playbook_is_dark_while_agent_resources_orgs_is_empty(self, armed, monkeypatch):
        monkeypatch.setattr(get_settings(), "playbook_orgs", "*")
        assert (await _method("resources/list"))["result"] == {"resources": []}
        assert (await _read("metorite://playbook/gates"))["error"]["code"] == -32002

    async def test_the_list_holds_one_row_for_each_topic(self, pb):
        from acb_playbook import load_all
        rows = (await _method("resources/list"))["result"]["resources"]
        assert [r["uri"] for r in rows] == [f"metorite://playbook/{t.slug}" for t in load_all()]
        assert all(r["mimeType"] == "application/json" and r["title"] and r["description"]
                   for r in rows)

    async def test_a_token_without_projects_read_sees_no_playbook_row(self, pb):
        assert (await _method("resources/list", token=CONTEXT_ONLY))["result"] == {"resources": []}
        assert (await _read("metorite://playbook/gates", token=CONTEXT_ONLY))["error"]["code"] == -32002

    async def test_every_listed_topic_reads(self, pb):
        rows = (await _method("resources/list"))["result"]["resources"]
        assert rows
        for row in rows:
            content = (await _read(row["uri"]))["result"]["contents"]
            assert content[0]["uri"] == row["uri"]
            assert json.loads(content[0]["text"])["slug"] == row["name"]

    async def test_a_topic_leaves_as_untrusted_text(self, pb):
        from acb_playbook import load_topic
        content = (await _read("metorite://playbook/brief-contract"))["result"]["contents"][0]
        data = json.loads(content["text"])
        assert data["body"] == {"untrusted_text": load_topic("brief-contract").body}
        assert set(data["title"]) == {"untrusted_text"} and set(data["summary"]) == {"untrusted_text"}
        assert data["version"] >= 1

    async def test_a_nested_slug_reads(self, pb):
        content = (await _read("metorite://playbook/patterns/one-agent-first"))["result"]["contents"]
        assert json.loads(content[0]["text"])["slug"] == "patterns/one-agent-first"

    async def test_an_unknown_topic_is_not_found_and_never_echoed(self, pb):
        body = await _read("metorite://playbook/no-such-topic")
        assert body["error"] == {"code": -32002, "message": "Resource not found"}
        assert "no-such-topic" not in json.dumps(body)

    @pytest.mark.parametrize("uri", [
        "metorite://playbook/", "metorite://playbook", "metorite://playbook/../gates",
        "metorite://playbook/Gates", "metorite://playbook/gates ", "metorite://playbook/gates\n",
        "metorite://playbook/gates?x=1", "metorite://playbook/gates#x", "metorite://playbook//gates",
        "metorite://playbook/gates/", "METORITE://playbook/gates", "metorite://Playbook/gates",
        "metorite://playbook/" + "a" * 300,
    ])
    async def test_a_bad_playbook_uri_is_not_found_and_never_echoed(self, pb, uri):
        body = await _read(uri)
        assert body["error"] == {"code": -32002, "message": "Resource not found"}
        assert uri.strip() not in json.dumps(body) or uri.strip() == ""

    async def test_a_playbook_read_makes_no_gateway_request(self, pb):
        pb.gateway.requests.clear()
        await _method("resources/list")
        await _read("metorite://playbook/failures")
        assert pb.gateway.requests == []

    async def test_the_templates_do_not_list_the_playbook(self, pb):
        rows = (await _method("resources/templates/list"))["result"]["resourceTemplates"]
        assert [r["uriTemplate"] for r in rows] == [
            "metorite://project/{id}", "metorite://task/{id}"]

    async def test_a_message_of_a_topic_holds_no_hathilabs_term(self, pb):
        from tests.unit.test_playbook_org_neutral import hits
        rows = (await _method("resources/list"))["result"]["resources"]
        for row in rows:
            text = (await _read(row["uri"]))["result"]["contents"][0]["text"]
            assert hits(text) == [], row["uri"]

    async def test_the_project_and_task_reads_are_unchanged(self, pb):
        direct = (await _tool("get_task", {"task_id": T_ONE}))["structuredContent"]
        content = (await _read(f"metorite://task/{T_ONE}"))["result"]["contents"]
        assert json.loads(content[0]["text"]) == direct
