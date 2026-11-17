"""The field search by code (WS-53 CRM-Q34-6, task 3699).

Database-free, so it never skips. The behaviour against a real Postgres is in
``test_crm_fields_search_r8.py``.

1. The flag: ``CRM_MODULE_FIND_ORGS`` has one reader, ``flags.module_find_allowed``,
   in the grammar of ``CRM_ORGS``. The default is empty.
2. The route: an org outside the list gets 404 ``module_find_off`` for each
   request, before any check of ``q`` or ``module``, and with no session.
3. The word rule: each word of ``q`` hits a word of the label or of the key.
4. The flag-off identity: with the list empty, ``crm_list_fields`` with a
   ``query`` answers byte for byte as the same call with no ``query``.
5. The answer of a search, and the key beside each label of a module record.
"""

from __future__ import annotations

import asyncio
from typing import Any

import acb_skills.crm_tools as T
import pytest
from fastapi import HTTPException
from gateway.crm_sync.terms import words
from gateway.routes.crm import fields, flags

from tests.unit._crm_agent_fakes import FakeClient, FakeResponse, fake_gateway
from tests.unit._crm_fakes import crm_switch_on

_ORG = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_RID = "0f8fad5b-d9cb-469f-a165-70867728950e"


# ── 1. The flag ──────────────────────────────────────────────────────────────


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_module_find_orgs", value, raising=False)


def test_the_settings_default_is_empty() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["crm_module_find_orgs"].default == ""


def test_an_empty_list_allows_no_org(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "")
    assert flags.module_find_allowed(_ORG) is False


def test_a_listed_org_is_allowed_and_another_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, f" {_ORG.upper()} ")
    assert flags.module_find_allowed(_ORG) is True
    assert flags.module_find_allowed(_OTHER) is False


def test_star_needs_an_org_and_the_crm_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    assert flags.module_find_allowed(_ORG) is True
    assert flags.module_find_allowed(None) is False
    crm_switch_on(monkeypatch, _OTHER)
    assert flags.module_find_allowed(_ORG) is False


def test_the_flag_has_one_reader() -> None:
    """Only ``flags.py`` names the setting. The chat tool asks the route."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    readers = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for base in ("apps", "packages")
        for p in (root / base).rglob("*.py")
        if "crm_module_find_orgs" in p.read_text(encoding="utf-8", errors="ignore")
    )
    assert readers == [
        "apps/services/gateway/gateway/routes/crm/flags.py",
        "packages/acb_common/acb_common/settings.py",
    ]


# ── 2. The route, dark ───────────────────────────────────────────────────────


def _user(org: str = _ORG) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="member@crm-find.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm"]), organization_id=org,
    )


def _no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> Any:
        raise AssertionError("a dark org opened a session")

    monkeypatch.setattr(fields, "_tenant_session", refuse)


@pytest.mark.parametrize(("q", "module"), [
    ("unit price", None), (None, None), ("", "Products"), ("x" * 500, "../x"),
])
def test_a_dark_org_gets_404_for_each_request(monkeypatch, q, module) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "")
    _no_session(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fields.search_fields(q=q, module=module, user=_user()))
    assert exc.value.status_code == 404
    assert exc.value.detail == {
        "code": fields.MODULE_FIND_OFF,
        "message": "The field search is off for this organization.",
    }


@pytest.mark.parametrize("q", [None, "", "  ", "&&", "x" * 201])
def test_a_listed_org_needs_a_word_in_q(monkeypatch, q) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    _no_session(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fields.search_fields(q=q, module=None, user=_user()))
    assert exc.value.status_code == 422


def test_the_route_declares_no_typed_argument() -> None:
    """FastAPI checks a typed argument before the handler. Each argument is
    an optional text, so the flag check comes first for each request."""
    import inspect

    params = inspect.signature(fields.search_fields).parameters
    assert params["q"].default is None and params["module"].default is None


def test_q_never_reaches_the_sql_text() -> None:
    assert ":q" not in fields._SEARCH_SQL
    assert "organization_id = CAST(:org AS uuid)" in fields._SEARCH_SQL
    assert "retired_at IS NULL" in fields._SEARCH_SQL


# ── 3. The word rule ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("q", "label", "key", "hit"), [
    ("unit price", "Unit Price", "Unit_Price", True),
    ("price unit", "Unit Price", "Unit_Price", True),
    ("prices", "Unit Price", "Unit_Price", True),
    ("pri", "Unit Price", "Unit_Price", True),
    ("pr", "Unit Price", "Unit_Price", False),
    ("unit cost", "Unit Price", "Unit_Price", False),
    # The camel-case words of a key, with a label that holds neither word.
    ("install date", "Commissioned On", "InstallDate", True),
    ("deals", "Deal Name", "Deal_Name", True),
    ("GST", "GST Number", "cf_gst_no", True),
    ("id", "Ident", "cf_x", False),
])
def test_each_word_must_hit_a_word_of_the_label_or_the_key(q, label, key, hit) -> None:
    assert fields.field_matches(words(q), label, key) is hit


def test_no_word_matches_nothing() -> None:
    assert fields.field_matches([], "Unit Price", "Unit_Price") is False


# ── 4. The flag-off identity of the tool ─────────────────────────────────────


#: The defs of a core entity and of a module, with fill numbers: a fill-rank
#: org gets them on the plain list, and the tool must print none of them.
_CORE_DEFS = [
    {"key": "cf_region", "label": "Region", "type": "select", "fill": 0.5, "distinct": 3,
     "options": [{"label": "North", "value": "N"}]},
    {"key": "cf_po", "label": "PO Number", "type": "text", "fill": 0.1, "distinct": 9},
]
_MODULE_DEFS = [
    {"key": "Unit_Price", "label": "Unit Price", "type": "currency", "fill": 0.75,
     "distinct": 4},
    {"key": "InstallDate", "label": "Commissioned On", "type": "date", "fill": None,
     "distinct": None},
]


class _Routed(FakeClient):
    """A gateway whose responder may answer ``(status, payload)``."""

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1]
        self._calls.append({"method": method, "path": path,
                            "params": dict(kwargs.get("params") or {})})
        answer = self._responder({"path": path, "params": kwargs.get("params") or {}})
        if isinstance(answer, tuple):
            return FakeResponse(answer[1], answer[0])
        return FakeResponse(answer)


def _gateway(monkeypatch, search: Any) -> list[dict]:
    def answer(call: dict) -> Any:
        path = call["path"]
        if path == T.FIELDS_SEARCH_PATH:
            return search
        if path == "/crm/fields/deals":
            return _CORE_DEFS
        if path == "/crm/fields/module:Products":
            return _MODULE_DEFS
        if path == "/crm/modules/Products/records/" + _RID:
            return {"record": {"id": _RID, "name": "Widget", "fields": {
                "Unit_Price": 12.5, "InstallDate": "2026-01-02"}},
                "field_defs": _MODULE_DEFS}
        return (500, {"detail": f"no route {path}"})

    return fake_gateway(T, monkeypatch, answer, client_class=_Routed)


#: The 404 body of the route for a dark org, from the route's own helper.
_OFF = (404, {"detail": fields._find_off().detail})
#: A gateway from before this slice has no route: a bare 404.
_NO_ROUTE = (404, {"detail": "Not Found"})


@pytest.mark.parametrize("search", [_OFF, _NO_ROUTE], ids=["off", "no-route"])
@pytest.mark.parametrize("entity", ["deals", "module:Products", "module:Nope", "", "bogus"])
def test_a_query_with_the_search_off_answers_as_today(monkeypatch, search, entity) -> None:
    calls = _gateway(monkeypatch, search)
    plain = asyncio.run(T.crm_list_fields(entity))
    plain_calls = [c["path"] for c in calls]
    calls.clear()
    found = asyncio.run(T.crm_list_fields(entity, query="unit price"))
    assert found == plain
    # The search reads only the route of the search, once, before the plain list.
    extra = [c["path"] for c in calls if c["path"] not in plain_calls]
    if entity == "deals" or entity == "bogus":
        # A record type, and an entity that the tool refuses, never ask.
        assert extra == []
    else:
        assert extra == [T.FIELDS_SEARCH_PATH]
    assert "fill" not in found and "75%" not in found


def test_a_blank_query_never_asks_the_search(monkeypatch) -> None:
    calls = _gateway(monkeypatch, (500, {"detail": "must not be read"}))
    plain = asyncio.run(T.crm_list_fields("module:Products"))
    calls.clear()
    assert asyncio.run(T.crm_list_fields("module:Products", query="  ")) == plain
    assert [c["path"] for c in calls] == ["/crm/fields/module:Products"]


def test_the_plain_list_keeps_its_words(monkeypatch) -> None:
    """The plain list of a module, byte for byte, with fill on its defs."""
    _gateway(monkeypatch, _OFF)
    assert asyncio.run(T.crm_list_fields("module:Products")) == "\n".join([
        "Products fields (2):",
        "- Unit Price (key Unit_Price, currency) [measure: sum, avg, min, max]",
        "- Commissioned On (key InstallDate, date) [group by month, quarter or year]",
    ])


def test_a_refusal_of_the_search_reaches_the_model(monkeypatch) -> None:
    """A mirrored module check of a listed org is no reason to fall back."""
    _gateway(monkeypatch, (404, {"detail": {
        "code": "unknown_module", "message": "This module is not mirrored."}}))
    out = asyncio.run(T.crm_list_fields("module:Nope", query="price"))
    assert out.startswith("Refused:") and "This module is not mirrored." in out


# ── 5. The answer of a search ────────────────────────────────────────────────


_HITS = {"total": 3, "hits": [
    {"module": "Products", "module_label": "Products", "key": "Unit_Price",
     "label": "Unit Price", "type": "currency", "fill": 0.75},
    {"module": "Products", "module_label": "Products", "key": "InstallDate",
     "label": "Commissioned On", "type": "date", "fill": None},
    {"module": "Quotes", "module_label": "Quotes", "key": "Is_Final",
     "label": "Final", "type": "boolean", "fill": 0.0},
]}


def test_each_hit_names_its_module_key_type_filter_and_fill(monkeypatch) -> None:
    calls = _gateway(monkeypatch, _HITS)
    out = asyncio.run(T.crm_list_fields(query="price"))
    assert calls[0]["params"] == {"q": "price"}
    assert out.splitlines() == [
        "Fields of the mirrored modules that match 'price' (3):",
        "- Products: Unit Price (key Unit_Price, currency) [measure: sum, avg, min, max]"
        " filter: one exact number; fill 75%",
        "- Products: Commissioned On (key InstallDate, date) [group by month, quarter or"
        " year] filter: a day YYYY-MM-DD, or a range as key.from and key.to",
        "- Quotes: Final (key Is_Final, boolean) [group by value] filter: true or false;"
        " fill 0%",
        T.FIELDS_SEARCH_HINT,
    ]


def test_a_module_search_sends_the_module(monkeypatch) -> None:
    calls = _gateway(monkeypatch, {"total": 0, "hits": []})
    out = asyncio.run(T.crm_list_fields("module:Products", query="colour"))
    assert calls[0]["params"] == {"q": "colour", "module": "Products"}
    assert out.startswith("No field of module:Products matches 'colour'.")


def test_a_cut_search_says_so(monkeypatch) -> None:
    hit = {"module": "M", "module_label": "M", "key": "k", "label": "K", "type": "text"}
    _gateway(monkeypatch, {"total": 250, "hits": [hit] * 200})
    first = asyncio.run(T.crm_list_fields(query="k"))
    assert "Showing 1-50 of 200. Call again with page=2 for the rest." in first
    assert "Only 200 of 250 fields show. Give more words." in first
    assert "filter: a value, in any case" in first


def test_a_module_record_prints_the_key_beside_each_label(monkeypatch) -> None:
    _gateway(monkeypatch, _OFF)
    out = asyncio.run(T.crm_get_module_record("Products", _RID))
    assert out.splitlines() == [
        f"Products: Widget (id={_RID})",
        "- Unit Price (key Unit_Price): 12.5",
        "- Commissioned On (key InstallDate): 2026-01-02",
    ]


def test_a_core_record_keeps_its_custom_lines_without_a_key() -> None:
    assert T._custom_lines({"cf_region": "N"}, _CORE_DEFS) == ["- Region: North"]


@pytest.mark.xfail(strict=True, reason=(
    "CRM-Q34-6 BLOCKER of the flag flip: the run cache keys crm_list_fields on "
    "(entity, page) and ignores query (_fields_key). Return None there for a "
    "non-blank query, then drop this mark."))
def test_the_run_cache_never_serves_a_plain_list_for_a_search(monkeypatch) -> None:
    _gateway(monkeypatch, _HITS)

    async def run() -> tuple[str, str]:
        token = T.open_asks_for_run()
        try:
            plain = await T.crm_list_fields("module:Products")
            return plain, await T.crm_list_fields("module:Products", query="price")
        finally:
            T.release_asks(token)

    plain, found = asyncio.run(run())
    assert found != f"{plain}\n{T.FIELDS_CACHED_NOTE}"
