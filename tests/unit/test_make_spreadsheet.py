"""``make_spreadsheet``: the chat tool (DOC-6, ``chat_parity.md`` §DOC).

The rows of a record never pass through the model, so this suite holds the
tool's side: every agent gets it, a WhatsApp run does not, a query goes to the
gateway as the member, a refusal writes no file, and ``rows`` the model holds
becomes one safe ``.xlsx`` card. The access rules against a real database are
in ``test_make_spreadsheet_r8.py``.
"""

from __future__ import annotations

import importlib
import io
import json
from typing import Any

import httpx
import pytest
from acb_skills import spreadsheets
from acb_skills import whatsapp_ui as wui
from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
from openpyxl import load_workbook

wa = importlib.import_module("acb_skills.write_artifact")

ROWS = {"title": "Team numbers", "sheets": [{"source": {"type": "rows", "rows": [
    {"city": "Pune", "visits": 3}, {"city": "Delhi", "visits": 4},
    {"city": "=HYPERLINK(1)", "visits": 1}]}, "totals": True}]}


@pytest.fixture
def chat(tmp_path, monkeypatch: pytest.MonkeyPatch):
    async def _quiet(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(wa, "_notify", _quiet)
    monkeypatch.setattr(wa, "mirror_to_blob_store", _quiet)
    identity = _bind_memory_user_id("a@fracktal.in")
    with wa.artifact_context_scope():
        wa.bind_artifact_context(workspace_root=str(tmp_path), session_id="s",
                                 agent_name="orchestrator")
        yield lambda: sorted(tmp_path.rglob("*.xlsx"))
    _unbind_memory_user_id(identity)


def _gateway(monkeypatch: pytest.MonkeyPatch, answer: httpx.Response) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    monkeypatch.setattr(spreadsheets.httpx, "AsyncClient",
                        lambda **_k: real(transport=httpx.MockTransport(handle)))
    return seen


def _crm(**source: Any) -> dict[str, Any]:
    return {"title": "Customers", "sheets": [
        {"source": {"type": "crm", "module": "contacts", **source}}]}


async def test_rows_become_one_safe_workbook_card(chat) -> None:
    out = await spreadsheets.make_spreadsheet(ROWS)
    assert out["ok"] is True, out
    assert out["path"] == "outputs/Team numbers.xlsx" and out["rows"] == 3
    (file,) = chat()
    sheet = load_workbook(io.BytesIO(file.read_bytes())).worksheets[0]
    assert sheet["B5"].value == 8
    assert sheet["A4"].data_type == "s" and sheet["A4"].quotePrefix


async def test_a_query_goes_to_the_gateway_as_the_member(
    chat, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpyxl import Workbook

    book = io.BytesIO()
    Workbook().save(book)
    seen = _gateway(monkeypatch, httpx.Response(
        200, content=book.getvalue(),
        headers={"X-Export-Rows": "2000", "X-Export-Summary": json.dumps(
            [{"sheet": "Sheet1", "rows": 2000, "columns": ["Name"], "groups": 0}])}))
    spec = {**_crm(q="acme"), "member": "b@evil.test"}
    spec["sheets"][0]["source"]["module"] = "People"
    out = await spreadsheets.make_spreadsheet(spec)
    assert out["ok"] is True and out["rows"] == 2000, out
    assert out["filters"] == ["contacts: contacts, q=acme"]
    (request,) = seen
    assert request.method == "GET" and request.url.path == "/crm/export/workbook.xlsx"
    assert request.headers["X-User-Email"] == "a@fracktal.in"
    sent = json.loads(request.url.params["spec"])
    assert sent == {"sheets": [{"source": {"type": "crm", "module": "contacts", "q": "acme"}}]}
    assert len(chat()) == 1


async def test_tasks_go_to_the_projects_route(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _gateway(monkeypatch, httpx.Response(422, json={"detail": "10001 rows match"}))
    out = await spreadsheets.make_spreadsheet({"sheets": [{"source": {"type": "tasks"}}]})
    assert seen[0].url.path == "/projects/export/workbook.xlsx"
    assert out == {"ok": False, "error": "10001 rows match Nothing was exported."}
    assert chat() == []


@pytest.mark.parametrize(("status", "says"), [
    (403, "CRM is not open to you"), (404, "CRM is off"), (500, "Nothing was exported"),
])
async def test_a_refusal_of_the_gateway_writes_nothing(
    chat, monkeypatch: pytest.MonkeyPatch, status: int, says: str,
) -> None:
    _gateway(monkeypatch, httpx.Response(status, json={"detail": "Not Found"}))
    out = await spreadsheets.make_spreadsheet(_crm())
    assert out["ok"] is False and says in out["error"], out
    assert chat() == []


async def test_a_shared_room_reads_nothing(chat, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _gateway(monkeypatch, httpx.Response(200))
    room = _bind_memory_user_id("room:abc")
    try:
        out = await spreadsheets.make_spreadsheet(_crm())
    finally:
        _unbind_memory_user_id(room)
    assert out["ok"] is False and "no one member" in out["error"]
    assert seen == [] and chat() == []


@pytest.mark.parametrize(("spec", "says"), [
    ({}, 'needs "sheets"'),
    ({"sheets": [{"source": {"type": "sql"}}]}, 'needs "type"'),
    ({"sheets": [_crm()["sheets"][0], ROWS["sheets"][0]]}, "Make one file for each"),
    ({"sheets": [{"source": {"type": "rows", "rows": [{}] * 501}}]}, "at most 500 rows"),
    ({"sheets": [{"source": {"type": "rows", "rows": []}, "chart": 1}]}, "unknown keys"),
])
async def test_a_bad_spec_writes_nothing(
    chat, monkeypatch: pytest.MonkeyPatch, spec: dict, says: str,
) -> None:
    seen = _gateway(monkeypatch, httpx.Response(200))
    out = await spreadsheets.make_spreadsheet(spec)
    assert out["ok"] is False and says in out["error"], out
    assert seen == [] and chat() == []


async def test_on_whatsapp_it_writes_nothing(chat) -> None:
    with wui.whatsapp_run("orchestrator"):
        out = await spreadsheets.make_spreadsheet(ROWS)
    assert out["ok"] is False and chat() == []


def test_every_agent_holds_it_and_whatsapp_withholds_it() -> None:
    """The floor (owner 2026-11-28: a core tool), so no ``tool_scope`` strips
    it. It reaches no host outside Metorite."""
    from acb_skills.skill_families import SKILL_FAMILIES
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    from orchestrator import _tool_injection as ti

    assert "make_spreadsheet" in ti._CORE_STANDARD_TOOL_NAMES
    assert "make_spreadsheet" in SKILL_FAMILIES["core"]["tools"]
    assert "make_spreadsheet" in [t.__name__ for t in ti._collect_injectable_platform_tools()]
    hints = TOOL_ANNOTATIONS["make_spreadsheet"]
    assert hints["open_world"] is False and hints["destructive"] is False
    assert "make_spreadsheet" in wui.WITHHELD_TOOLS
    scope = ti._resolve_injected_scope(["ask_questions"])
    assert "make_spreadsheet" in scope
