"""The CRM and email tools answer a gateway refusal as text (R7 fence).

Production, 2026-10-17: a member asked the chat for the outstanding
payments in the CRM. ``crm_aggregate`` got four 422s in a row, and the model
read each one as "Error: Function failed." (MAF's answer to a tool that
raises). It never read the route's detail, so it guessed again, and at the
end it wrote its tool call as plain text. Over seven days ``query_inbox``
raised 83 times and ``crm_aggregate`` 17 times in the same way.

The Projects tools had the fix already (``test_projects_agent_refusals.py``,
F4). This file holds the same fence for the other two families, on the ONE
seam that all three share, ``acb_common.tool_refusals``:

1. **One seam.** ``skill_projects`` re-exports the shared refusal, fence and
   safe detail. No module defines a second ``GatewayRefusal`` or a second
   ``safe_detail``.
2. **Every tool is wrapped.** Each CRM tool (``acb_skills.crm_tools`` and
   ``agent-crm``) and each email tool, as its agent registers it, carries
   ``refusals_as_text``. A tool that joins unwrapped fails here.
3. **A 4xx becomes text with the route's detail**, and a 5xx body never
   reaches the model.
4. **Any other exception still raises**, so no stack, query or host reaches
   the model.
5. **``crm_aggregate`` takes no group**, and prints one line.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="agent_framework not installed")

import acb_skills.crm_tools as _T
from acb_common import tool_refusals as shared
from acb_common.tool_refusals import REFUSED, WRAPPED_ATTR

from tests.unit._crm_agent_fakes import FakeClient, FakeResponse, load_agent_module

REPO = Path(__file__).resolve().parents[2]
_CRM = load_agent_module("crm_assistant_agent_refusals")


def _load_email() -> Any:
    path = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"
    spec = importlib.util.spec_from_file_location("email_assistant_agent_refusals", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_EMAIL = _load_email()

MEMBER = "sales@fracktal.in"


def _registered(module: Any) -> dict[str, Any]:
    """The tools as the built agent holds them, by name: the callable MAF
    runs (``FunctionTool.func``), or the callable itself."""
    agent = module.build_agents()[0]
    held: dict[str, Any] = {}
    for t in agent.default_options["tools"]:
        fn = getattr(t, "func", t)
        name = getattr(t, "name", None) or getattr(fn, "__name__", "")
        held[name] = fn
    return held


class _Refusing(FakeClient):
    """A gateway that answers every call with one status and body."""

    status = 422
    body: Any = None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self._calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        return FakeResponse(self.body, self.status)


def _refusing(module: Any, monkeypatch, status: int, body: Any) -> list[dict]:
    calls: list[dict] = []
    cls = type("_R", (_Refusing,), {"status": status, "body": body})
    monkeypatch.setattr(
        module, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: cls(calls, None), URL=httpx.URL),
    )
    monkeypatch.setattr(module, "_current_user_email", lambda: MEMBER)
    return calls


def _broken(module: Any, monkeypatch) -> None:
    """A transport that fails before any response: not a gateway refusal."""

    class _Down(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> Any:
            raise httpx.ConnectError("connect to 10.0.0.5:8080 refused")

    monkeypatch.setattr(
        module, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: _Down([], None), URL=httpx.URL),
    )
    monkeypatch.setattr(module, "_current_user_email", lambda: MEMBER)


# ── 1. One seam ──────────────────────────────────────────────────────────────


def test_skill_projects_uses_the_shared_seam() -> None:
    """The Projects client re-exports the shared objects. It holds no copy."""
    import skill_projects.client as client
    import skill_projects.refusals as refusals

    assert client.GatewayRefusal is shared.GatewayRefusal
    assert client.safe_detail is shared.safe_detail
    assert client.data is shared.data
    assert refusals.REFUSED == shared.REFUSED
    assert refusals.WRAPPED_ATTR == shared.WRAPPED_ATTR


def test_no_module_defines_a_second_refusal_or_safe_detail() -> None:
    """A second ``GatewayRefusal`` class or ``safe_detail`` function is a
    parallel seam: its refusals would slip past the shared wrapper."""
    seam = REPO / "packages" / "acb_common" / "acb_common" / "tool_refusals.py"
    offenders = []
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if path == seam or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "GatewayRefusal" not in text and "safe_detail" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ClassDef) and node.name == "GatewayRefusal":
                    offenders.append(f"{path.relative_to(REPO)}: class GatewayRefusal")
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "safe_detail"):
                    offenders.append(f"{path.relative_to(REPO)}: def safe_detail")
    assert offenders == [], offenders


def test_the_email_error_is_the_shared_refusal() -> None:
    """``GatewayError`` keeps its old shape, and the wrapper catches it."""
    exc = _EMAIL.GatewayError("Email GET /email/x failed (404)", 404)
    assert isinstance(exc, shared.GatewayRefusal) and isinstance(exc, RuntimeError)
    assert exc.status == 404 and str(exc) == "Email GET /email/x failed (404)"


# ── 2. Every tool is wrapped (the unwrapped-registration fence) ─────────────


def test_every_shared_crm_tool_is_wrapped() -> None:
    """The orchestrator injects ``CRM_TOOLS`` into every agent as they are."""
    bare = [fn.__name__ for fn in _T.CRM_TOOLS if not getattr(fn, WRAPPED_ATTR, False)]
    assert bare == [], f"CRM tools without refusals_as_text: {bare}"


def test_every_tool_the_crm_agent_registers_is_wrapped() -> None:
    held = _registered(_CRM)
    assert set(held) == {fn.__name__ for fn in _CRM._TOOLS}
    bare = [name for name, fn in held.items() if not getattr(fn, WRAPPED_ATTR, False)]
    assert bare == [], f"crm-assistant tools without refusals_as_text: {bare}"


def test_every_tool_the_email_agent_registers_is_wrapped() -> None:
    held = _registered(_EMAIL)
    names = {fn.__name__ for fn in _EMAIL._TOOLS}
    assert names <= set(held)
    bare = [n for n in names if not getattr(held[n], WRAPPED_ATTR, False)]
    assert bare == [], f"email-assistant tools without refusals_as_text: {bare}"


@pytest.mark.parametrize("module", [_CRM, _EMAIL], ids=["crm", "email"])
def test_the_wrapper_keeps_the_name_the_doc_and_the_risk(module: Any) -> None:
    """MAF reads the name, the docstring and the signature. The egress rule
    reads ``__tool_risk__`` (H-236)."""
    import inspect

    held = _registered(module)
    for raw in module._TOOLS:
        fn = held[raw.__name__]
        assert fn.__doc__ == raw.__doc__, raw.__name__
        assert inspect.signature(fn) == inspect.signature(raw), raw.__name__
        assert getattr(fn, "__tool_risk__", None) == getattr(raw, "__tool_risk__", None)


# ── 3. A 4xx becomes text with the route's detail ───────────────────────────


async def test_a_crm_aggregate_422_reaches_the_model_with_its_detail(monkeypatch) -> None:
    """The prod case: ``group_field=Circle`` on a text field."""
    said = ("'Circle' is a text field. A group takes a field of type select, "
            "boolean, lookup, or a date with a bucket.")
    calls = _refusing(_T, monkeypatch, 422, {"detail": {"code": "unfit_field", "message": said}})
    out = await _T.crm_aggregate("module:Invoices", group_field="Circle")
    assert out.startswith(f"{REFUSED} The CRM refused an argument (422).")
    assert f"Gateway said: «{said}»" in out
    assert "\nNext: Fix the argument" in out
    assert calls, "the tool never reached the gateway"


async def test_a_crm_422_list_detail_names_the_field(monkeypatch) -> None:
    """FastAPI's own 422 names the field, and drops the input."""
    _refusing(_T, monkeypatch, 422, {"detail": [
        {"loc": ["query", "page_size"], "msg": "Input should be less than 101",
         "input": "5000", "url": "https://errors.pydantic.dev/x"},
    ]})
    out = await _T.crm_list_records("deals")
    assert "page_size: Input should be less than 101" in out
    assert "Fix the value for page_size" in out
    assert "5000" not in out and "pydantic.dev" not in out


async def test_a_crm_agent_tool_422_is_text(monkeypatch) -> None:
    """The crm-assistant's own tools go through the same seam."""
    _refusing(_CRM, monkeypatch, 422, {"detail": "Unknown sort key 'nope'."})
    out = await _registered(_CRM)["search_crm"]("acme")
    assert out.startswith(REFUSED) and "«Unknown sort key 'nope'.»" in out


@pytest.mark.parametrize(("tool", "call"), [
    ("query_inbox", lambda t: t("acc-1", query="invoice")),
    ("read_email", lambda t: t("msg-1")),
    ("list_senders", lambda t: t("acc-1")),
])
async def test_an_email_422_reaches_the_model_with_its_detail(tool, call, monkeypatch) -> None:
    said = "received_after must be an ISO date."
    _refusing(_EMAIL, monkeypatch, 422, {"detail": said})
    out = await call(_registered(_EMAIL)[tool])
    assert out.startswith(f"{REFUSED} The email app refused an argument (422)."), out
    assert f"Gateway said: «{said}»" in out and "\nNext: " in out


async def test_an_email_404_points_at_a_read_tool(monkeypatch) -> None:
    _refusing(_EMAIL, monkeypatch, 404, {"detail": "Email not found"})
    out = await _registered(_EMAIL)["read_email"]("msg-1")
    assert "(404)" in out and "«Email not found»" in out
    assert "query_inbox" in out and "Do not guess an id" in out


@pytest.mark.parametrize("status", [429, 503])
async def test_a_busy_or_down_gateway_says_try_again(status: int, monkeypatch) -> None:
    _refusing(_T, monkeypatch, status, {"detail": "Try again in a moment."})
    out = await _T.crm_list_pipelines()
    assert out.startswith(REFUSED) and f"({status})" in out
    assert "Try again" in out or "try again" in out


@pytest.mark.parametrize("module", ["crm", "email"])
async def test_a_5xx_body_never_reaches_the_model(module: str, monkeypatch) -> None:
    leak = "relation crm_deals: SELECT * FROM crm_deals at postgresql://acb:pw@10.0.0.5/acb"
    if module == "crm":
        _refusing(_T, monkeypatch, 500, {"detail": leak})
        out = await _T.crm_list_pipelines()
    else:
        _refusing(_EMAIL, monkeypatch, 500, {"detail": leak})
        out = await _registered(_EMAIL)["query_inbox"]("acc-1")
    assert out.startswith(REFUSED) and "(500)" in out
    assert "Gateway said" not in out
    assert "SELECT" not in out and "postgresql" not in out and "10.0.0.5" not in out


async def test_another_members_address_is_masked(monkeypatch) -> None:
    _refusing(_EMAIL, monkeypatch, 409, {
        "detail": f"{MEMBER} and raj@fracktal.in both hold this thread."})
    out = await _registered(_EMAIL)["read_email"]("msg-1")
    assert MEMBER in out and "raj@fracktal.in" not in out


async def test_a_refusal_writes_one_warning_with_no_detail(monkeypatch) -> None:
    import structlog

    _refusing(_T, monkeypatch, 404, {"detail": "Deal «Secret plan» not found."})
    record = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    with structlog.testing.capture_logs() as caps:
        await _T.crm_get_record("deals", record)
    rows = [c for c in caps if c.get("event") == "crm.tool_refused"]
    assert len(rows) == 1, caps
    assert rows[0]["tool"] == "crm_get_record" and rows[0]["status"] == 404
    assert rows[0]["route"] == "/crm/deals/{id}"
    assert "Secret plan" not in repr(rows[0]) and record not in repr(rows[0])


async def test_a_bad_value_of_a_crm_tool_is_text_and_sends_nothing(monkeypatch) -> None:
    """A validator's refusal is our own words, and the model reads them."""
    calls = _refusing(_T, monkeypatch, 200, {})
    out = await _T.crm_aggregate("invoices", group_by="owner")
    assert out.startswith(f"{REFUSED} Unknown CRM record type 'invoices'.")
    assert "module:<api_name>" in out
    assert calls == []


# ── 4. Any other exception still raises ─────────────────────────────────────


async def test_a_crm_transport_fault_still_raises(monkeypatch) -> None:
    _broken(_T, monkeypatch)
    with pytest.raises(httpx.ConnectError):
        await _T.crm_list_pipelines()


async def test_an_email_transport_fault_still_raises(monkeypatch) -> None:
    _broken(_EMAIL, monkeypatch)
    with pytest.raises(httpx.ConnectError):
        await _registered(_EMAIL)["query_inbox"]("acc-1")


async def test_a_plain_runtime_error_still_raises() -> None:
    async def broken() -> str:
        raise RuntimeError("SELECT secret FROM crm_deals")

    with pytest.raises(RuntimeError):
        await shared.refusals_as_text(broken, _T.CRM_VOICE)()


# ── 5. crm_aggregate with no group ──────────────────────────────────────────


async def test_the_aggregate_with_no_group_prints_one_line(monkeypatch) -> None:
    calls: list[dict] = []

    def answer(_call: dict) -> dict:
        return {
            "entity": "module:Invoices", "measure": "sum", "group_by": None,
            "group_field": None, "field": "Total_Invoice_Amount",
            "field_type": "currency", "groups": [], "total": 123456.0,
            "values": 40, "records": 42, "zone": "Asia/Kolkata",
        }

    monkeypatch.setattr(_T, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: FakeClient(calls, answer)))
    monkeypatch.setattr(_T, "_current_user_email", lambda: MEMBER)
    out = await _T.crm_aggregate(
        "module:Invoices", measure="sum", field="Total_Invoice_Amount",
        field_filters={"Invoice_Date.from": "2026-10-01", "Invoice_Date.to": "2026-10-31"},
    )
    assert out == "Invoices, sum of Total_Invoice_Amount: INR 123,456 over 42 records."
    sent = next(c["params"] for c in calls if c["path"] == "/crm/aggregate")
    assert "group_by" not in sent and "group_field" not in sent
    assert sent["field.Invoice_Date.from"] == "2026-10-01"


async def test_the_aggregate_with_no_group_counts(monkeypatch) -> None:
    def answer(_call: dict) -> Any:
        if _call["path"] != "/crm/aggregate":
            return []
        return {"entity": "deals", "measure": "count", "groups": [],
                "total": 7.0, "records": 7}

    monkeypatch.setattr(_T, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: FakeClient([], answer)))
    monkeypatch.setattr(_T, "_current_user_email", lambda: MEMBER)
    assert await _T.crm_aggregate("deals") == "Deals, count: 7."


async def test_the_aggregate_with_no_group_says_an_empty_field_has_no_values(
    monkeypatch,
) -> None:
    def answer(_call: dict) -> Any:
        if _call["path"] != "/crm/aggregate":
            return []
        return {"entity": "module:Invoices", "measure": "sum", "groups": [],
                "total": None, "values": 0, "records": 3, "field_type": "currency"}

    monkeypatch.setattr(_T, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: FakeClient([], answer)))
    monkeypatch.setattr(_T, "_current_user_email", lambda: MEMBER)
    out = await _T.crm_aggregate("module:Invoices", measure="sum", field="Total")
    assert out == "Invoices, sum of Total: no values recorded for Total."


def test_the_docstring_says_the_group_is_optional() -> None:
    """The model reads the docstring. It must not say "Give group_by or
    group_field" as a rule any more."""
    doc = " ".join((_T.crm_aggregate.__doc__ or "").split())
    assert "The group is optional" in doc
    assert "Give group_by or group_field." not in doc


def test_the_route_check_takes_no_group_and_refuses_both() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import aggregate as agg

    found, module = agg._check("deals", "count", None, None, None, None)
    assert found is not None and module is None
    assert agg._check("module:Invoices", "sum", None, None, "Amount", None) == (None, "Invoices")
    with pytest.raises(HTTPException) as exc:
        agg._check("deals", "count", "owner", "Tier", None, None)
    assert exc.value.status_code == 422 and "not both" in str(exc.value.detail)
    with pytest.raises(HTTPException):
        agg._check("deals", "count", None, None, None, "month")  # a bucket, no field


def test_a_422_of_the_field_scope_names_the_choices() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    scope = fs.FieldScope(column="fields", owner="module:Invoices", fields={
        "Circle": fs.FieldRef("Circle", "text", "Circle"),
        "Status": fs.FieldRef("Status", "select", "Status"),
        "Invoice_Date": fs.FieldRef("Invoice_Date", "date", "Invoice Date"),
        "Total_Invoice_Amount": fs.FieldRef("Total_Invoice_Amount", "currency", "Total"),
    })
    with pytest.raises(HTTPException) as exc:
        fs.check_group(scope, "Circle", None)
    message = exc.value.detail["message"]
    assert "can group are: Invoice_Date, Status" in message
    assert "field.Circle=<value>" in message
    with pytest.raises(HTTPException) as exc:
        fs.check_measure(scope, "Circle")
    assert "number fields of module:Invoices are: Total_Invoice_Amount" in (
        exc.value.detail["message"])
    with pytest.raises(HTTPException) as exc:
        fs.check_measure(scope, "Nope")
    assert exc.value.detail["code"] == "unknown_field"
    assert "Its fields are: Circle, Invoice_Date, Status, Total_Invoice_Amount" in (
        exc.value.detail["message"])
    with pytest.raises(HTTPException) as exc:
        fs.module_where(scope, "Invoices", [("Circle.from", "2026-10-01")], zone="UTC")
    assert "date fields of module:Invoices are: Invoice_Date" in exc.value.detail["message"]


def test_the_choices_are_capped() -> None:
    from gateway.routes.crm import field_sql as fs

    fields = {f"F{i:03d}": fs.FieldRef(f"F{i:03d}", "text", "x") for i in range(30)}
    scope = fs.FieldScope(column="fields", owner="module:Big", fields=fields)
    text = scope.choices()
    assert text.endswith(", and 10 more") and text.count(", ") == fs.CHOICES_MAX


def test_an_unknown_module_names_the_mirrored_ones() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm.modules import _require_module

    with pytest.raises(HTTPException) as exc:
        _require_module({"Invoices": {}, "Quotes": {}}, "Outstanding")
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "unknown_module"
    assert "The mirrored modules are: Invoices, Quotes." in exc.value.detail["message"]
