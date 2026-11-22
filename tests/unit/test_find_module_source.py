"""FIND-11 (task 3710, ``find_engine.md`` §9.2): the mirrored CRM modules are a
source of the find engine.

Hermetic: the two functions of ``routes/crm/modules.py`` are fakes, and so is
each app's reader but the CRM's. The real SQL, the module gate and the
privacy of org B are in ``test_find_module_source_r8.py``.

The claims, each written so that a wrong build fails it:

* **One source.** The module records join the ``crm`` source, so the registry,
  the order of the apps and the 422 of an unknown app stay as they were.
* **Dark.** With ``FIND_ORGS`` or ``CRM_MODULE_FIND_ORGS`` empty, no module
  function runs, and the answer and the tool text are those of before.
* **A module hit** says how it matched (``matched_by``) and prints its kind
  label, name, module and link.
* **The names.** The near lookup and ``resolve_name`` read the record names.
* **No growth of any tool description or schema** (task 3725).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from acb_common import get_settings
from acb_common.name_match import NearName
from acb_skills import company_search_tools as cst
from gateway.retrieval import company, names, sources

from tests.unit._crm_fakes import crm_switch_on
from tests.unit.test_company_search_recency import recency  # noqa: F401
from tests.unit.test_company_search_sources import ALL, member
from tests.unit.test_company_search_zero import World, _install

MODULE_ROW = {
    "id": "5f0a1b2c-0000-4000-8000-000000000001", "module": "Operator_Associates",
    "name": "Operator Associates Pvt Ltd", "label": "Operator Associate",
    "modified": datetime(2026, 11, 10, tzinfo=UTC),
}


class Modules:
    """Stand-ins for the two functions of ``routes/crm/modules.py``."""

    def __init__(self) -> None:
        self.found: list[dict[str, Any]] = [MODULE_ROW]
        self.near: dict[str, list[NearName]] = {}
        self.calls: list[tuple[str, Any]] = []

    async def find_module_names(self, user: Any, q: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("find", q))
        words = q.lower().split()
        return [r for r in self.found if all(w in r["name"].lower() for w in words)][:limit]

    async def near_module_names(self, user: Any, words: list[str], k: int) -> dict[str, Any]:
        self.calls.append(("near", list(words)))
        return {w: list(self.near.get(w, [])) for w in words}


@pytest.fixture()
def flags(monkeypatch, recency):  # noqa: F811
    """``FIND_ORGS`` and ``CRM_MODULE_FIND_ORGS`` on the cached settings, both
    off. The CRM switch is on, as the CRM source needs it."""
    crm_switch_on(monkeypatch, "*")
    settings = get_settings()

    def _set(find: str = "", module: str = "") -> None:
        monkeypatch.setattr(settings, "find_orgs", find, raising=False)
        monkeypatch.setattr(settings, "crm_module_find_orgs", module, raising=False)

    _set()
    recency(True)
    return _set


@pytest.fixture()
def modules(monkeypatch) -> Modules:
    from gateway.routes.crm import access, records
    from gateway.routes.crm import modules as crm_modules

    fake = Modules()
    searched: list[str] = []

    async def search(user: Any, q: str, k: int) -> list[Any]:
        searched.append(q)
        return []

    async def no_names(user: Any, words: list[str], k: int) -> dict[str, list[NearName]]:
        return {}

    async def let_in(user: Any) -> None:
        return None

    monkeypatch.setattr(records, "search_records", search)
    monkeypatch.setattr(records, "near_names", no_names)
    monkeypatch.setattr(access, "require_crm_access", let_in)
    monkeypatch.setattr(crm_modules, "find_module_names", fake.find_module_names)
    monkeypatch.setattr(crm_modules, "near_module_names", fake.near_module_names)
    return fake


def _run(q: str, user: Any = None, k: int = 5) -> dict[str, Any]:
    return asyncio.run(company.company_search(q=q, apps=None, k=k, user=user or member(*ALL)))


@pytest.fixture()
def route(monkeypatch, modules, flags):
    """The company route with fake readers for each app but the CRM, whose
    reader is the real ``_crm`` over the two fakes of ``modules``."""
    world = World({key: [] for key in sources.REGISTRY})
    _install(monkeypatch, world)
    crm = sources.REGISTRY["crm"]
    monkeypatch.setitem(sources.REGISTRY, "crm", dataclasses.replace(
        crm, read=sources._crm, gate=sources._crm_open, near=sources._crm_near))
    return world


class TestOneSource:
    def test_the_registry_keeps_its_keys_and_its_order(self):
        assert list(sources.REGISTRY) == [
            "tasks", "email", "people", "crm", "artifacts", "whatsapp", "knowledge",
            "studio", "chat"]

    def test_the_crm_source_names_the_module_kind_and_never_relates_or_opens_it(self):
        crm = sources.REGISTRY["crm"]
        assert sources.MODULE_KIND in crm.named_extra
        assert sources.MODULE_KIND not in crm.names
        assert sources.MODULE_KIND not in crm.kinds
        assert sources.MODULE_KIND not in crm.takes
        assert crm.near is sources._crm_near
        assert crm.read is sources._crm

    def test_the_link_opens_the_module_record_of_the_crm_app(self):
        assert sources.crm_module_href("Operator_Associates", "abc") == (
            "/crm?tab=module&module=Operator_Associates&mrec=Operator_Associates%3Aabc")


class TestDark:
    @pytest.mark.parametrize(("find", "module"), [("", ""), ("*", ""), ("", "*")])
    def test_one_flag_empty_reads_no_module(self, flags, modules, find, module):
        flags(find, module)
        user = member("crm")
        assert asyncio.run(sources._crm(user, "operator associates", 5)) == []
        assert asyncio.run(sources._crm_near(user, ["operator"], 5)) == {}
        assert modules.calls == []

    def test_both_flags_open_the_module_read(self, flags, modules):
        flags("*", "*")
        hits = asyncio.run(sources._crm(member("crm"), "operator associates", 5))
        assert [h.kind for h in hits] == [sources.MODULE_KIND]
        assert modules.calls == [("find", "operator associates")]

    def test_a_name_that_is_not_the_org_reads_no_module(self, flags, modules):
        flags("other-org", "other-org")
        assert asyncio.run(sources._crm(member("crm"), "operator", 5)) == []
        assert modules.calls == []

    def test_dark_the_route_answer_is_the_one_of_before(self, route, flags, modules):
        flags("", "")
        before = json.dumps(_run("operator associates"), sort_keys=True)
        # Module flag on, FIND_ORGS empty: byte for byte the answer of before.
        flags("", "*")
        assert json.dumps(_run("operator associates"), sort_keys=True) == before
        assert modules.calls == []
        # FIND_ORGS on, module flag empty: the answer of the engine with no
        # module record in the world, whatever the module holds.
        flags("*", "")
        with_data = json.dumps(_run("operator associates"), sort_keys=True)
        modules.found = []
        assert json.dumps(_run("operator associates"), sort_keys=True) == with_data
        assert modules.calls == []

    def test_dark_the_tool_text_is_the_one_of_before(self, route, flags, modules):
        flags("", "")
        before = cst.format_answer(_run("operator associates"))
        flags("", "*")
        assert cst.format_answer(_run("operator associates")) == before
        assert "module_record" not in before


class TestAModuleHit:
    def test_it_says_how_it_matched(self, route, flags, modules):
        flags("*", "*")
        answer = _run("operator associates")
        hits = answer["hits"]
        assert len(hits) == 1
        hit = hits[0]
        assert hit["kind"] == "module_record"
        assert hit["title"] == "Operator Associates Pvt Ltd"
        assert hit["hint"] == "Operator Associate"
        assert hit["href"].startswith("/crm?tab=module&module=Operator_Associates&mrec=")
        assert hit["matched_by"] in ("exact", "words")
        assert (hit["covers"], hit["of"]) == (2, 2)

    def test_the_text_is_short_and_holds_the_kind_name_module_and_link(self, route, flags, modules):
        flags("*", "*")
        text = cst.format_answer(_run("operator associates"))
        assert "«Operator Associates Pvt Ltd»" in text
        assert "(Operator Associate" in text
        assert "/crm?tab=module&module=Operator_Associates" in text
        assert "full_id: module_record:5f0a1b2c" in text
        line = next(x for x in text.splitlines() if "«Operator Associates" in x)
        assert len(line) < 230

    def test_a_typo_finds_it_by_the_near_spelling(self, route, flags, modules):
        flags("*", "*")
        modules.near = {"operater": [NearName("Operator Associates Pvt Ltd", 0.9, "Operator")]}
        answer = _run("operater associates")
        hit = answer["hits"][0]
        assert hit["title"] == "Operator Associates Pvt Ltd"
        assert hit["matched_by"] == "near"
        assert (hit["word"], hit["as"]) == ("operater", "Operator")

    def test_a_full_page_of_records_leaves_room(self, flags, modules, monkeypatch):
        from gateway.routes.crm import records

        full = [("deal", {"id": f"d{i}", "name": f"Deal {i}"}) for i in range(5)]

        async def search(user: Any, q: str, k: int) -> list[Any]:
            return full[:k]

        monkeypatch.setattr(records, "search_records", search)
        flags("*", "*")
        hits = asyncio.run(sources._crm(member("crm"), "operator associates", 5))
        assert len(hits) == 5
        assert [h.kind for h in hits].count("module_record") == 1


class TestTheNames:
    def test_the_near_names_join_the_record_names_of_the_crm(self, flags, modules, monkeypatch):
        from gateway.routes.crm import records

        async def near(user: Any, words: list[str], k: int) -> dict[str, list[NearName]]:
            return {"operater": [NearName("Operator Lee", 0.8, "Operator")]}

        monkeypatch.setattr(records, "near_names", near)
        modules.near = {"operater": [NearName("Operator Associates", 0.9, "Operators")]}
        flags("*", "*")
        got = asyncio.run(sources._crm_near(member("crm"), ["operater"], 5))
        assert [n.spelling for n in got["operater"]] == ["Operators", "Operator"]

    def test_a_named_record_carries_its_module(self, flags, modules):
        flags("*", "*")
        found, _more = asyncio.run(sources.REGISTRY["crm"].named_records(
            member("crm"), "operator associates", 5))
        assert [(n.kind, n.module) for n in found] == [("module_record", "Operator Associate")]

    def test_the_row_of_resolve_name_holds_the_module_and_the_tool_prints_it(self):
        record = sources.Named("module_record", "5f0a1b2c-0000-4000-8000-000000000001",
                               "Operator Associates Pvt Ltd", "/crm?tab=module", module="Products")
        row = names._row("crm", record, "exact")
        assert row["kind"] == "module_record" and row["module"] == "Products"
        text = "\n".join(cst._name_lines(row))
        assert "(CRM record, Products)" in text
        assert "full_id: module_record:5f0a1b2c" in text

    def test_a_row_of_another_kind_has_no_module_key(self):
        record = sources.Named("lead", "5f0a1b2c-0000-4000-8000-000000000001", "Sharat", "/crm")
        assert "module" not in names._row("crm", record, "exact")

    def test_the_closed_list_and_its_422_do_not_change(self):
        assert names.KINDS == ("person", "contact", "lead", "company", "sender")
        assert names.KINDS_RULE == "The kinds are person, contact, lead, company and sender."
        assert cst.NAMES_KINDS == "The kinds are person, contact, lead, company and sender."



class TestNoGrowthOfAnyToolText:
    """Task 3725: the Studio chat sits at its token budget. FIND-11 changes
    result text only, never a description or a schema. The lengths are those
    of the base before FIND-11. A longer description fails here."""

    @pytest.mark.parametrize(("tool", "most"), [("search_company", 911), ("resolve_name", 1082)])
    def test_a_description_is_no_longer_than_before(self, tool, most):
        import inspect

        text = inspect.getdoc(getattr(cst, tool)) or ""
        assert len(text) <= most
        assert "module_record" not in text

    def test_the_kinds_line_of_the_tool_is_the_one_of_before(self):
        assert cst.NAMES_KINDS == "The kinds are person, contact, lead, company and sender."
