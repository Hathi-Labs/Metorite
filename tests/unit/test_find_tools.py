"""WS-FIND FIND-6 (task 3243): the chat's family of find tools.

Spec: ``project-docs/specs/find_engine.md`` §5.3, §5.4 and the row FIND-6 of
§9.2. Everything here is dark behind ``FIND_ORGS`` (with
``COMPANY_SEARCH_ENABLED`` on). An org off the list keeps the text and the
tool objects of main at the time of the slice, byte for byte. The pins hold
for ``d7970121a`` and for ``d99180486`` too.

1. **The core list.** With ``FIND_ORGS`` on, it holds ``search_company`` and
   ``resolve_name``, with or without ``CONTEXT_RECENCY``. ``resolve_person``
   stays while the profile is on: the parity of D-FIND-3 does not hold
   (§5.3, as built). The three parts that ``resolve_name`` lacks are pinned
   below, so the slice that adds them must come back here.
2. **The standing rule** of §5.4 closes the grounding paragraph, only with
   ``FIND_ORGS`` and ``CONTEXT_RECENCY`` on. The route runs the ladder, and
   the dated tool prints how a hit matched and "Where else", only then.
3. **The zero line** of ``search_crm``, ``query_inbox`` and ``find_tasks``
   ends with the pointer of §5.3, only in a pack call of an org on the list
   with ``CONTEXT_RECENCY`` on, and only on a zero. A failure is no zero.
4. **Failure text.** Each core find tool of an org on the list answers a
   failure through the HRD-3 guard (``pack_tool_guard.guard``): the class of
   the error, never its message, and never "Error: Function failed.".

Each case names, in its docstring, the mutation that turns it red (R7).
Run: ``uv run pytest tests/unit/test_find_tools.py -q``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.tool_refusals import FAILED, GatewayRefusal, is_refusal
from acb_skills import company_search_tools as cst
from acb_skills import pack_tool_guard as ptg
from acb_skills import profile_tools as pt
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "project-docs" / "specs" / "find_engine.md"
ORG = "f1d6f1d6-0000-4000-8000-0000000000f6"
OTHER = "0b0b0b0b-0000-4000-8000-0000000000bb"
MEMBER = "jane@acme.test"
SECRET = "jane.secret@acme.test"
FIND_NAMES = ("search_company", "resolve_name", "resolve_person")

CRM_DIR = "apps/agents/agent-crm"
EMAIL_DIR = "apps/agents/agent-email-assistant"

#: The persona of main (``d7970121a``, and ``d99180486`` too) for each mix of
#: (company_search, recency, proposals, profile, constraints, spawn,
#: card_writes), as sha256. A later change of the persona must update it,
#: as the goldens of ``test_native_session_persistence.py`` are.
MAIN_PERSONA: dict[tuple[bool, ...], str] = {
    (False,) * 7: "96d235c809480a11f072eabcd3f8ce462884ec7b18f4ba754dd44b03b2e80b6f",
    (True, False, False, False, False, False, False):
        "9afea873ad3a4ccf19d62db5f7307b06da3fa5ade962b7594b18e25313e19359",
    (True, True, False, False, False, False, False):
        "9f8b22cb8196605e64c486dfb3ecc01f57c2784fc72795fce11adcef887eae91",
    (True, False, False, True, False, False, False):
        "8fa698e9c431ca55c598fdd318e97de056e8c2a2a2744783c660641766fc7032",
    (True, True, False, True, False, False, False):
        "cd3df9458126ad855b9eecaa03013574c577322ab66d03dfae6018dd169a997b",
    (True,) * 7: "60411bc0d2539d51c691442051640e95df556b2aa3dc50bfb61858c80aad270a",
}


def _orch() -> Any:
    import orchestrator.agents as pkg

    return pkg._mod


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _words(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture()
def flags(monkeypatch):
    """``COMPANY_SEARCH_ENABLED`` on, and ``FIND_ORGS`` and ``CONTEXT_RECENCY``
    as the test sets them. ``CONTEXT_RECENCY`` starts off, and a call that
    does not name it keeps its value."""
    settings = get_settings()
    monkeypatch.setattr(settings, "company_search_enabled", True, raising=False)
    monkeypatch.setattr(settings, "context_recency", False, raising=False)

    def _set(find: str, *, company_search: bool = True, recency: bool | None = None) -> None:
        monkeypatch.setattr(settings, "company_search_enabled", company_search, raising=False)
        monkeypatch.setattr(settings, "find_orgs", find, raising=False)
        if recency is not None:
            monkeypatch.setattr(settings, "context_recency", recency, raising=False)

    return _set


class _FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture()
def build(monkeypatch, flags):
    """The orchestrator as ``build_orchestrator_agent`` assembles it."""
    mod = _orch()
    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])

    def _build(find: str, *, profile: bool = False, org: str | None = ORG,
               company_search: bool = True, recency: bool = False) -> _FakeAgent:
        flags(find, company_search=company_search, recency=recency)
        monkeypatch.setattr(get_settings(), "assistant_profile_enabled", profile, raising=False)
        return mod.build_orchestrator_agent(with_history=False, organization_id=org)

    return _build


def _names(agent: _FakeAgent) -> list[str]:
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


def _find_tools(agent: _FakeAgent) -> list[str]:
    return [n for n in _names(agent) if n in FIND_NAMES]


def _tool(agent: _FakeAgent, name: str) -> Any:
    return next(t for t in agent.kwargs["tools"] if getattr(t, "__name__", "") == name)


# ── 1. The core list ────────────────────────────────────────────────────────


class TestTheCoreList:
    @pytest.mark.parametrize("recency", [False, True], ids=["plain", "recency"])
    def test_on_it_holds_search_company_and_resolve_name(self, build, recency):
        """The DONE WHEN of FIND-6. ``GET /search/names`` reads no
        ``CONTEXT_RECENCY``, so ``resolve_name`` joins either way. Mutation:
        drop the insert of ``resolve_name``, or gate it on recency → red."""
        lit = build(ORG, recency=recency)
        assert _find_tools(lit) == ["search_company", "resolve_name"]
        assert _names(lit)[:4] == [
            "search_company", "resolve_name", "read_page", "list_spaces"]
        raw = cst.search_company_dated if recency else cst.search_company
        assert _tool(lit, "search_company").__wrapped__ is raw

    def test_off_the_list_is_the_list_of_main(self, build):
        """An org off the list, and an empty list, keep the find tools of
        main, as the same objects. Mutation: guard the tools for every org →
        red."""
        for find in ("", OTHER):
            dark = build(find)
            assert _find_tools(dark) == ["search_company"]
            assert _tool(dark, "search_company") is cst.search_company
            lit_profile = build(find, profile=True)
            assert _find_tools(lit_profile) == ["search_company", "resolve_person"]
            assert _tool(lit_profile, "resolve_person") is pt.resolve_person

    def test_no_org_fails_closed(self, build):
        assert _find_tools(build("*", org=None)) == ["search_company"]

    def test_without_company_search_nothing_joins(self, build):
        agent = build(ORG, company_search=False)
        assert not set(_names(agent)) & {"search_company", "resolve_name"}
        assert agent.kwargs["instructions"] == _orch()._PULL_INSTRUCTIONS

    def test_resolve_person_stays_until_parity(self, build):
        """D-FIND-3: ``resolve_person`` leaves the core list one release
        after ``resolve_name`` passes its cases. It does not yet: no source
        gives a member, and a person row prints no address. Mutation: take
        ``resolve_person`` out for an org on the list → red."""
        lit = build(ORG, profile=True)
        assert _find_tools(lit) == ["search_company", "resolve_name", "resolve_person"]
        # The only change to the rest of the list is the one new tool.
        dark = build("", profile=True)
        assert [n for n in _names(lit) if n != "resolve_name"] == _names(dark)

    def test_the_gaps_of_parity_are_still_open(self):
        """The three reasons that ``resolve_person`` stays (§5.3, as built):
        no member, no saved alias and no address. The slice that closes them
        must update this case and §5.3."""
        from gateway.retrieval import names, sources
        from gateway.routes.admin import assistant_profile_people as people

        assert "member" not in names.KINDS, (
            "resolve_name now gives members: revisit D-FIND-3 and the core list")
        # The aliases: resolve_person reads the member's saved aliases first
        # (`_alias_tier`, through the profile store). The name route reads no
        # profile store, and it has no kind for an alias.
        assert callable(people._alias_tier)
        assert "alias" not in names.KINDS
        for module in (names, sources):
            body = Path(module.__file__).read_text(encoding="utf-8")
            assert "stored_profile" not in body and "assistant_profile" not in body, (
                "the name route now reads the saved aliases: revisit D-FIND-3")
        text = cst.format_names({"verdict": "one", "rows": [{
            "app": "people", "kind": "person", "match": "exact", "name": "Raj Rao",
            "id": "6d000000-0000-4000-8000-000000000001", "href": "/people/x",
            "status": "active", "email": "raj@acme.test"}]})
        assert "raj@acme.test" not in text, (
            "resolve_name now prints an address: revisit D-FIND-3 and the core list")


# ── 2. The standing rule ────────────────────────────────────────────────────


def _rule_from_spec() -> str:
    section = _spec().split("### 5.4 The standing rule", 1)[1]
    return section.split("```text", 1)[1].split("```", 1)[0]


class TestTheStandingRule:
    def test_the_rule_is_the_words_of_the_spec(self):
        """Mutation: change one word of ``FIND_STANDING_RULE`` → red."""
        assert _words(_orch().FIND_STANDING_RULE) == _words(_rule_from_spec())

    def test_it_closes_the_grounding_paragraph(self):
        """The rule sits once, right before rule 2, after the CX-1
        sentences. Mutation: anchor it on rule 1 → red."""
        mod = _orch()
        on = mod._orchestrator_instructions(True, True, find=True)
        assert on.count(mod.FIND_STANDING_RULE) == 1
        assert mod.FIND_STANDING_RULE + "2. For specialist work:" in on
        assert on.index(mod._RECENCY_SWAP[1]) < on.index(mod.FIND_STANDING_RULE)

    def test_without_recency_there_is_no_rule(self):
        """With ``CONTEXT_RECENCY`` off, the route runs no ladder and the
        plain tool prints neither how a hit matched nor "Where else". So the
        rule would promise a line that never prints. Mutation: drop
        ``recency`` from the gate of ``_FIND_SWAP`` → red."""
        mod = _orch()
        assert (mod._orchestrator_instructions(True, False, find=True)
                == mod._orchestrator_instructions(True, False))

    @pytest.mark.parametrize("mix", sorted(MAIN_PERSONA), ids=str)
    def test_on_is_the_persona_of_main_plus_the_rule(self, mix):
        """With company search and recency on, the rule and nothing else.
        Else the persona of main. Mutation: a second change rides with the
        swap → red."""
        mod = _orch()
        cs, rec, prop, prof, cons, spawn, card = mix
        kw = dict(profile=prof, constraints=cons, spawn=spawn, card_writes=card)
        on = mod._orchestrator_instructions(cs, rec, prop, find=True, **kw)
        off = mod._orchestrator_instructions(cs, rec, prop, find=False, **kw)
        if cs and rec:
            assert on.replace(mod.FIND_STANDING_RULE, "", 1) == off
            assert mod.FIND_STANDING_RULE in on
        else:
            # The rule names search_company and the lines of the dated tool.
            assert on == off
            assert _sha(on) == MAIN_PERSONA[mix]

    @pytest.mark.parametrize("mix", sorted(MAIN_PERSONA), ids=str)
    def test_off_is_the_persona_of_main_byte_for_byte(self, mix):
        """The hashes of main. Mutation (b) of the report: add the rule, or
        any other line, to the persona of an org off the list → red."""
        cs, rec, prop, prof, cons, spawn, card = mix
        off = _orch()._orchestrator_instructions(
            cs, rec, prop, profile=prof, constraints=cons, spawn=spawn, card_writes=card)
        assert _sha(off) == MAIN_PERSONA[mix]
        assert "Where else" not in off

    def test_the_build_follows_the_org(self, build):
        """With recency on. Mutation: read ``FIND_ORGS`` for the persona from
        another org → red."""
        lit = build(ORG, recency=True)
        dark, other = build("", recency=True), build(OTHER, recency=True)
        assert _orch().FIND_STANDING_RULE in lit.kwargs["instructions"]
        assert dark.kwargs["instructions"] == other.kwargs["instructions"]
        assert _sha(dark.kwargs["instructions"]) == MAIN_PERSONA[
            (True, True, False, False, False, False, False)]
        assert (lit.kwargs["instructions"].replace(_orch().FIND_STANDING_RULE, "", 1)
                == dark.kwargs["instructions"])

    def test_the_build_without_recency_keeps_the_persona_of_main(self, build):
        """An org of ``FIND_ORGS`` with recency off gets ``resolve_name`` and
        the persona of main, byte for byte. Mutation: pass ``find`` with no
        recency gate → red."""
        lit = build(ORG, recency=False)
        assert "resolve_name" in _names(lit)
        assert _sha(lit.kwargs["instructions"]) == MAIN_PERSONA[
            (True, False, False, False, False, False, False)]


# ── 3. The zero line of the app search tools ────────────────────────────────


def _pointer_from_spec() -> str:
    row = next(line for line in _spec().splitlines() if line.startswith("| `search_crm`"))
    found = re.search(r'A zero ends with "([^"]+)"', row)
    assert found is not None, "the §5.3 row of search_crm lost its zero line"
    return found.group(1)


RUN_KEY = "key-r-1"


def _bind(*, pack: str | None) -> contextlib.AbstractContextManager[None]:
    """The run of the member's own chat in ``ORG``, with the run key that the
    server mints (``tool_packs.PACK_RUN_KEY``). With *pack*, the call is a
    pack call of that owner (``tool_packs.owner_scope``), as
    ``_tool_injection._pack_call`` makes it."""

    @contextlib.contextmanager
    def _cm() -> Iterator[None]:
        from acb_common.db import bind_tenant, release_tenant
        from acb_skills.tool_packs import PACK_RUN_KEY, owner_scope

        token = bind_tenant(ORG)
        try:
            with artifact_context_scope():
                bind_artifact_context(run_id="r-1", session_id="chat-1",
                                      agent_name="orchestrator", member=MEMBER,
                                      workspace_root="/tmp/find-6", no_egress=False,
                                      **{PACK_RUN_KEY: RUN_KEY})
                if pack is None:
                    yield
                else:
                    with owner_scope(pack, False):
                        yield
        finally:
            release_tenant(token)

    return _cm()


class _Apps:
    """The three tools, each with a fake read that answers *answer* or raises."""

    def __init__(self, monkeypatch) -> None:
        import skill_projects.reads as reads

        from tests.unit._native_maf_harness import load_agent_module

        self.crm = load_agent_module(CRM_DIR)
        self.email = load_agent_module(EMAIL_DIR)
        self.reads = reads
        self.raise_with: BaseException | None = None
        self.answer: dict[str, Any] = {}

        async def fake(path: str, params: dict[str, Any] | None = None) -> Any:
            if self.raise_with is not None:
                raise self.raise_with
            return dict(self.answer)

        async def no_labels() -> dict[str, str]:
            return {}

        monkeypatch.setattr(self.crm, "_get", fake)
        monkeypatch.setattr(self.email, "_get", fake)
        monkeypatch.setattr(self.email, "_account_labels", no_labels)
        monkeypatch.setattr(reads, "get", fake)

    def call(self, tool: str) -> str:
        if tool == "search_crm":
            self.answer = {"rows": [], "total": 0}
            return asyncio.run(self.crm.search_crm("zzq"))
        if tool == "query_inbox":
            self.answer = {"emails": [], "total": 0}
            return asyncio.run(self.email.query_inbox(query="zzq"))
        self.answer = {"rows": []}
        return asyncio.run(self.reads.find_tasks("zzq"))


#: The zero answers of main, byte for byte, and the owner of each tool.
ZERO_OF_MAIN = {
    "search_crm": ("Nothing in the CRM matches 'zzq'.", "crm-assistant", "crm-records"),
    "query_inbox": ("No emails matched those filters.", "email-assistant", "email"),
    "find_tasks": ("No task matches «zzq». Try a shorter fragment.", "projects-assistant",
                   "projects"),
}
TOOLS = sorted(ZERO_OF_MAIN)


@pytest.fixture()
def apps(monkeypatch, flags):
    """The org on ``FIND_ORGS``, with ``CONTEXT_RECENCY`` on."""
    flags(ORG, recency=True)
    return _Apps(monkeypatch)


class TestTheZeroLine:
    def test_the_pointer_is_the_words_of_the_spec(self):
        """Mutation: change one word of ``FIND_POINTER`` → red."""
        assert _pointer_from_spec() == cst.FIND_POINTER

    @pytest.mark.parametrize("tool", TOOLS)
    def test_on_a_zero_ends_with_the_pointer(self, apps, tool):
        """Mutation: drop the ``with_zero_pointer`` call of one tool → red."""
        zero, owner, _slug = ZERO_OF_MAIN[tool]
        with _bind(pack=owner):
            out = apps.call(tool)
        assert out == f"{zero}\n{cst.FIND_POINTER}"
        assert out.count(cst.FIND_POINTER) == 1

    @pytest.mark.parametrize("find", ["", OTHER])
    @pytest.mark.parametrize("tool", TOOLS)
    def test_off_the_zero_is_the_zero_of_main(self, apps, flags, tool, find):
        """Mutation: add the pointer for every org → red."""
        flags(find)
        zero, owner, _slug = ZERO_OF_MAIN[tool]
        with _bind(pack=owner):
            assert apps.call(tool) == zero

    @pytest.mark.parametrize("tool", TOOLS)
    def test_without_company_search_no_pointer(self, apps, flags, tool):
        """The pointer names a tool that the core list must hold."""
        flags(ORG, company_search=False)
        zero, owner, _slug = ZERO_OF_MAIN[tool]
        with _bind(pack=owner):
            assert apps.call(tool) == zero

    @pytest.mark.parametrize("tool", TOOLS)
    def test_without_recency_no_pointer(self, apps, flags, tool):
        """With ``CONTEXT_RECENCY`` off, the route runs no ladder, so
        ``search_company`` tries no near spelling and no linked record. The
        zero is the zero of main. Mutation: drop the recency gate of
        ``zero_pointer`` → red."""
        flags(ORG, recency=False)
        zero, owner, _slug = ZERO_OF_MAIN[tool]
        with _bind(pack=owner):
            assert apps.call(tool) == zero

    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_specialist_run_gets_no_pointer(self, apps, tool):
        """A specialist's sub-run holds no search_company. Mutation: drop
        the pack-call gate of ``zero_pointer`` → red."""
        zero, _owner, _slug = ZERO_OF_MAIN[tool]
        with _bind(pack=None):
            assert apps.call(tool) == zero

    @pytest.mark.parametrize("error", [RuntimeError(f"boom {SECRET}"),
                                       GatewayRefusal("refused", status=503)],
                             ids=["raise", "refusal"])
    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_failure_is_no_zero(self, apps, monkeypatch, tool, error):
        """Through the ONE pack seam, ``_tool_injection._pack_call``. A read
        that fails answers the guard or the refusal text, with no pointer
        and no message of the error. Mutation (a) of the report: answer a
        failure with the zero line → red."""
        from orchestrator import _tool_injection as ti

        monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
        _zero, owner, slug = ZERO_OF_MAIN[tool]
        fn = {"search_crm": apps.crm.search_crm, "query_inbox": apps.email.query_inbox,
              "find_tasks": apps.reads.find_tasks}[tool]
        apps.raise_with = error
        with _bind(pack=None):
            wrapped = ti._pack_call(fn, owner, False, RUN_KEY, slug)
            out = asyncio.run(wrapped(query="zzq"))
        assert isinstance(out, str) and out
        assert out.startswith(FAILED), out
        assert cst.FIND_POINTER not in out
        assert SECRET not in out
        assert "Function failed" not in out

    def test_the_seam_gives_the_pointer_on_a_zero(self, apps, monkeypatch):
        """The same seam with an answer: the pack call itself opens the gate.
        Non-vacuity of the case above."""
        from orchestrator import _tool_injection as ti

        monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
        apps.answer = {"rows": [], "total": 0}
        with _bind(pack=None):
            wrapped = ti._pack_call(apps.crm.search_crm, "crm-assistant", False,
                                    RUN_KEY, "crm-records")
            out = asyncio.run(wrapped(query="zzq"))
        assert out == f"Nothing in the CRM matches 'zzq'.\n{cst.FIND_POINTER}"

    def test_a_page_past_the_end_is_no_zero(self, apps):
        """``query_inbox`` with a total of 3 and a page past them. Mutation:
        read ``not emails`` as the zero → red."""
        apps.answer = {"emails": [], "total": 3}
        with _bind(pack="email-assistant"):
            out = asyncio.run(apps.email.query_inbox(query="zzq", offset=50))
        assert cst.FIND_POINTER not in out

    @pytest.mark.parametrize("answer", [
        {"emails": []},
        {"emails": [], "total": None},
        {"emails": [], "total": "0"},
        {"emails": [], "total": True},
        {"emails": [], "total": False},
        {"emails": [], "total": 0.0},
    ], ids=["missing", "none", "text", "true", "false", "float"])
    @pytest.mark.parametrize("offset", [0, 50])
    def test_only_an_integer_total_of_zero_is_a_zero(self, apps, answer, offset):
        """``query_inbox`` gives the pointer only when the route sends the
        integer 0 as the total. A missing total, None, a text, a bool or a
        float is no proof of a zero. A total that ``page_line`` cannot read
        raises, and the guard of the pack call answers it, with no pointer.
        Mutation: read ``not total`` as the zero → red."""
        apps.answer = dict(answer)
        with _bind(pack="email-assistant"):
            try:
                out = asyncio.run(apps.email.query_inbox(query="zzq", offset=offset))
            except TypeError:
                out = ""
        assert cst.FIND_POINTER not in out

    def test_the_integer_zero_is_a_zero(self, apps):
        """Non-vacuity of the case above."""
        apps.answer = {"emails": [], "total": 0}
        with _bind(pack="email-assistant"):
            out = asyncio.run(apps.email.query_inbox(query="zzq"))
        assert out == f"No emails matched those filters.\n{cst.FIND_POINTER}"

    def test_a_near_only_crm_answer_is_no_zero(self, apps):
        """Near rows are rows that the member sees. Mutation: give the
        pointer to the near answer → red."""
        apps.answer = {"rows": [{"id": "6d000000-0000-4000-8000-000000000002",
                                 "lead_name": "Sharat", "near": True}], "total": 1}
        with _bind(pack="crm-assistant"):
            out = asyncio.run(apps.crm.search_crm("Sharath"))
        assert "Sharat" in out
        assert cst.FIND_POINTER not in out

    def test_hits_get_no_pointer(self, apps):
        apps.answer = {"rows": [{"id": "6d000000-0000-4000-8000-000000000003",
                                 "title": "zzq", "full_id": "task:1"}], "total": 1}
        with _bind(pack="projects-assistant"):
            out = asyncio.run(apps.reads.find_tasks("zzq"))
        assert cst.FIND_POINTER not in out

    def test_the_gates_of_the_helper(self, flags):
        """Each gate alone closes it. Mutation: drop one gate → red."""
        flags(ORG, recency=True)
        with _bind(pack="crm-assistant"):
            assert cst.zero_pointer() == cst.FIND_POINTER
            assert cst.with_zero_pointer("x") == f"x\n{cst.FIND_POINTER}"
        with _bind(pack=None):
            assert cst.zero_pointer() == ""
            assert cst.with_zero_pointer("x") == "x"
        flags(ORG, recency=False)
        with _bind(pack="crm-assistant"):
            assert cst.zero_pointer() == ""
        flags("", recency=True)
        with _bind(pack="crm-assistant"):
            assert cst.zero_pointer() == ""
        flags(ORG, company_search=False)
        with _bind(pack="crm-assistant"):
            assert cst.zero_pointer() == ""
        # No run at all: no tenant, no pack call.
        flags("*")
        assert cst.zero_pointer() == ""


# ── 4. Failure text of the core find tools ──────────────────────────────────


@pytest.fixture()
def failing(monkeypatch):
    """Each read of the three core find tools raises, with an address in its
    message."""

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"Company search failed (500) for {SECRET}")

    monkeypatch.setattr(cst, "_get", boom)
    monkeypatch.setattr(cst, "_names_get", boom)
    monkeypatch.setattr(cst, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(pt, "_get", boom)
    monkeypatch.setattr(pt, "_current_user_email", lambda: MEMBER)


_CALLS: dict[str, dict[str, str]] = {
    "search_company": {"query": "neuropeak printer"},
    "resolve_name": {"name": "Sharat"},
    "resolve_person": {"name": "Sharat"},
}


class TestTheFailureText:
    @pytest.mark.parametrize("name", FIND_NAMES)
    def test_on_a_failure_is_fixed_text(self, build, failing, name):
        """Mutation: drop the guard of one core find tool → red."""
        tool = _tool(build(ORG, profile=True), name)
        assert getattr(tool, ptg.GUARDED_ATTR, False) is True
        out = asyncio.run(tool(**_CALLS[name]))
        assert out == ptg.failure_text(RuntimeError(), read_only=True)
        assert out.startswith(FAILED) and is_refusal(out)
        assert SECRET not in out and "500" not in out
        assert "Function failed" not in out

    @pytest.mark.parametrize("name", ["search_company", "resolve_person"])
    def test_off_the_tool_raises_as_on_main(self, build, failing, name):
        """An org off the list keeps the tool of main, so MAF answers it as
        before (``resolve_name`` is not in its list). Mutation: guard every
        org → red."""
        tool = _tool(build("", profile=True), name)
        assert not getattr(tool, ptg.GUARDED_ATTR, False)
        with pytest.raises(RuntimeError):
            asyncio.run(tool(**_CALLS[name]))

    def test_a_run_with_no_member_is_text_too(self, build, monkeypatch):
        """``search_company`` raises with no member, and its fence pins that
        (``test_company_search_tool.py``). In the list of an org on
        ``FIND_ORGS`` the guard answers it."""
        monkeypatch.setattr(cst, "_current_user_email", lambda: "")
        out = asyncio.run(_tool(build(ORG), "search_company")(query="neuropeak"))
        assert out.startswith(FAILED) and "No acting user" not in out

    @pytest.mark.parametrize("name", FIND_NAMES)
    def test_the_guard_keeps_the_schema(self, build, name):
        """MAF reads the name, the docstring and the signature through
        ``functools.wraps``. Mutation: a guard that loses ``__wrapped__`` →
        red."""
        from agent_framework._tools import normalize_tools

        lit = _tool(build(ORG, profile=True), name)
        raw = {"search_company": cst.search_company, "resolve_name": cst.resolve_name,
               "resolve_person": pt.resolve_person}[name]
        assert lit.__wrapped__ is raw
        new, old = normalize_tools([lit])[0], normalize_tools([raw])[0]
        assert (new.name, new.description) == (old.name, old.description)
        assert new.parameters() == old.parameters()
        assert lit.__tool_risk__ == raw.__tool_risk__

    @pytest.mark.parametrize("name", FIND_NAMES)
    def test_a_held_chat_keeps_the_guarded_tool(self, build, name):
        """The egress rule reads the tool OBJECT (H-236). The guard copies
        ``__tool_risk__``, so a held chat of an org on the list keeps each
        find tool, as it keeps the raw one. Mutation: a guard that drops the
        risk → the guarded tool reads as egress → red."""
        from acb_skills import egress
        from orchestrator import _tool_injection as ti

        lit = _tool(build(ORG, profile=True), name)
        raw = lit.__wrapped__
        assert ti._is_egress(lit) is ti._is_egress(raw) is False
        assert egress.is_egress_tool(lit) is egress.is_egress_tool(raw) is False
        assert ti._is_held_off(lit) is ti._is_held_off(raw) is False

    def test_a_refusal_still_reads_its_own_sentence(self, build, monkeypatch):
        """The guard sits outside ``_read_tool``, so the sentences of the
        module stay. Mutation: put the guard inside ``_read_tool`` → red."""
        monkeypatch.setattr(cst, "_current_user_email", lambda: "")
        out = asyncio.run(_tool(build(ORG), "resolve_name")(name="Sharat"))
        assert out == cst.NAMES_NO_MEMBER
