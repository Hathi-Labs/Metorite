"""The CRM assistant agent (WS-26d) — registration, routes, verb + path fences.

Spec: ``project-docs/specs/crm_app.md`` §6 (Agent, Phase D) + §9 WS-26d.
The write tools' own behaviour — confirm-before-write, fail-closed, payload
shapes — lives in ``test_crm_agent_write.py``; what is asserted HERE is the
whole-surface properties, which is why every one of them enumerates ``_TOOLS``
rather than a hand-typed list of tool names.

Three properties carry this slice, and each has failed in this repo before:

1. **An agent that is not in BOTH registries silently never loads.**
   ``build_agents()`` alone is not registration (``_AGENT_REGISTRY`` is what
   ``local_path`` comes from, and what the orchestrator imports for its
   delegation tools); a registry entry alone is not an agent. The
   agent-orchestrator wrapper shipped with the second half missing and was
   unreachable for weeks — ``test_orchestrator_registration.py`` exists because
   of it. Both halves are asserted here.
2. **Every gateway call names the person it is for, or does not happen.**
   ``acb_auth/deps.py`` §1b reads a bearer with no ``X-User-Email`` as the
   platform acting as itself and grants SERVICE_ACCESS, so a run that cannot
   resolve its user must refuse rather than call. Asserted at the transport,
   with a fake httpx client, so the real ``_headers()`` runs — **for all eight
   tools**, because a write issued as the platform itself is the worse half of
   that bug.
3. **The verb allowlist is a property of the transport, not a promise in a
   docstring.** WS-26d-write widened it from ``{"GET"}`` to
   ``{"GET", "POST", "PATCH"}`` — and the check survived the widening, which is
   the part worth testing: ``DELETE`` and ``PUT`` still raise inside the one
   round-trip helper every tool goes through, so a tool added later cannot
   remove or replace a record. Asserted behaviourally (a DELETE raises),
   structurally (no ``_delete``/``_put`` helper exists for one to reach for),
   and by observation (every call the eight tools actually make is in the set).

Plus the WhatsApp half of the ticket: ``"crm"`` is a KNOWN entity-ref system so
a hand-set ref parses — and **nothing more**. The ``crm`` context block stays
``None``, which is what makes this parse-only rather than a link.

The agent module is loaded by file path (as the Dynamic Agent Loader does), not
as an installed package. No gateway and no MAF runtime are started.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.unit._crm_agent_fakes import (
    AGENT_DIR as _AGENT_DIR,
)
from tests.unit._crm_agent_fakes import (
    REPO_ROOT,
    FakeClient,
    FakeResponse,
    agent_source,
    approve,
    fake_gateway,
    load_agent_module,
)

_M = load_agent_module()

# WS-53 CRM-C7: the shared read tools live in ``acb_skills.crm_tools`` and
# speak through its own transport, so a fake gateway patches both modules.
import acb_skills.crm_tools as _T  # noqa: E402

_ID = str(uuid4())
_STATUS_ID = str(uuid4())


def _fake_gateway(monkeypatch, responder, *, user: str | None = "sales@fracktal.in"):
    """Both transports share ONE call list, in the order the calls happen."""
    calls = fake_gateway(_M, monkeypatch, responder, user=user)
    monkeypatch.setattr(
        _T, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: FakeClient(calls, responder)),
    )
    if user is not None:
        monkeypatch.setattr(_T, "_current_user_email", lambda: user)
    return calls


# ── 1. Registration — both halves, or the agent never loads ──────────────────

routes_agent = pytest.importorskip(
    "gateway.routes.agent", reason="gateway not installed in this environment",
)


def _registry_entry(name: str) -> dict | None:
    return next(
        (e for e in routes_agent._AGENT_REGISTRY if e.get("name") == name), None,
    )


def test_the_agent_is_in_the_run_allowlist() -> None:
    """``_KNOWN_AGENTS`` is what ``/agent/run*`` validates a name against."""
    assert "crm-assistant" in routes_agent._KNOWN_AGENTS


def test_the_agent_is_in_the_registry_the_orchestrator_imports() -> None:
    """``orchestrator/agents.py`` imports ``_AGENT_REGISTRY`` directly, so this
    entry — not ``agent_registry.json`` — is what makes it delegatable."""
    entry = _registry_entry("crm-assistant")
    assert entry is not None, (
        "crm-assistant missing from _AGENT_REGISTRY — build_agents() alone is "
        "not registration; the agent silently never loads"
    )
    assert entry["description"], "no description ⇒ the orchestrator skips it"


def test_registry_entry_points_at_the_real_agent_directory() -> None:
    """``local_path`` is the only mapping from ``crm-assistant`` to ``agent-crm``."""
    entry = _registry_entry("crm-assistant")
    assert entry is not None
    assert entry["local_path"] == "apps/agents/agent-crm"
    agent_dir = REPO_ROOT / entry["local_path"]
    assert agent_dir.is_dir()
    assert (agent_dir / "agents.py").is_file()
    assert (agent_dir / "config.json").is_file()
    assert (agent_dir / "instructions.md").is_file()


def test_the_agent_is_declared_maf_not_copilot() -> None:
    """github-copilot routes it through the Copilot SDK and 402s — the trap
    already annotated on the email- and whatsapp-assistant entries."""
    entry = _registry_entry("crm-assistant")
    assert entry is not None
    assert entry["agent_runtime"] == "maf"
    config = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert config["runtime"] == "maf"


def test_the_catalog_registry_agrees_with_the_gateway_one() -> None:
    """``agent_registry.json`` is catalog-only — no runtime code reads it — so
    the risk is not that it breaks, it is that it drifts and misinforms."""
    catalog = json.loads(
        (REPO_ROOT / "agent_registry.json").read_text(encoding="utf-8"))
    entry = next((e for e in catalog if e.get("name") == "crm-assistant"), None)
    assert entry is not None
    assert entry["local_path"] == "apps/agents/agent-crm"
    assert entry["agent_runtime"] == "maf"


def test_config_scope_matches_the_registered_tools() -> None:
    config = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert set(config["own_tool_scope"]) == {fn.__name__ for fn in _M._TOOLS}


def test_build_agents_constructs_one_native_maf_agent() -> None:
    """End to end: the object the named-agent path would actually load."""
    agent_framework = pytest.importorskip("agent_framework")
    built = _M.build_agents()
    assert len(built) == 1
    agent = built[0]
    assert isinstance(agent, agent_framework.Agent)
    assert agent.name == "crm-assistant"
    # A Copilot-SDK agent carries _default_options; a native MAF one does not.
    assert not hasattr(agent, "_default_options")
    # Left unset so the executor injects the risk-aware handler (B6).
    assert getattr(agent, "_permission_handler", None) is None
    tools = (agent.default_options or {}).get("tools")
    assert tools, "built with no tools"
    assert len(tools) == len(_M._TOOLS) == 8 + len(_T.CRM_TOOLS)


def test_the_advertised_description_does_not_still_say_read_only() -> None:
    """R4 — the description is what the orchestrator routes on, so a stale
    "read-only" there does not just misinform a reader: it teaches the router
    to send write requests somewhere else. Four artifacts claimed it; this pins
    the two that live in the agent's own package."""
    built = _M.build_agents()
    config = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    for text in (built[0].description or "", config["description"]):
        assert "read-only" not in text.lower(), f"stale read-only claim: {text}"
        assert "create" in text.lower()


def test_all_tools_are_async_and_documented() -> None:
    assert _M._TOOLS
    for fn in _M._TOOLS:
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} must be async"
        assert (fn.__doc__ or "").strip(), f"{fn.__name__} needs a docstring"


def test_register_agent_tools_maps_names() -> None:
    reg = _M._register_agent_tools()
    assert set(reg) == {
        "search_crm", "get_pipeline", "get_record", "get_timeline",
        "create_lead", "update_deal_status", "log_activity", "convert_lead",
    } | set(_T.CRM_TOOL_NAMES)
    assert all(reg[n] is getattr(_T, n) for n in _T.CRM_TOOL_NAMES)
    assert reg["search_crm"] is _M.search_crm


# ── 2. Identity — every call names its person, or does not happen ────────────

_EMPTY_LIST = {"rows": [], "total": 0}

#: The deal stage vocabulary ``update_deal_status`` resolves a NAME against.
_STATUSES = [
    {"id": _STATUS_ID, "name": "Negotiation", "type": "open", "probability": 60},
]


def _responder(call: dict) -> Any:
    """Minimal well-shaped payload per route, so formatting runs for real."""
    path = call["path"]
    # Ordered before the generic ``/crm/<entity>/<id>`` arm below, which these
    # two would otherwise match on segment count alone.
    if path == "/crm/statuses/deal":
        return _STATUSES
    if path == "/crm/lost-reasons":
        return []
    if path in ("/crm/pipelines", "/crm/modules") or path.startswith("/crm/fields/"):
        return []
    if path.endswith("/convert"):
        return {"lead": {"id": _ID}, "deal": {"id": _ID, "name": "Acme AMC"}}
    if path.endswith("/activities"):
        return {"id": _ID, "type": "note", "subject": "Logged"}
    if path.endswith("/timeline"):
        return {"entries": []}
    if path == "/crm/pipeline":
        return {"lanes": []}
    if path.count("/") == 3:  # /crm/<entity>/<id>
        return {"id": _ID, "name": "Acme", "lead_name": "Acme", "first_name": "A"}
    if call["method"] == "POST":  # POST /crm/leads
        return {"id": _ID, "lead_name": "Acme", "owner_email": "sales@fracktal.in"}
    return _EMPTY_LIST


#: Every tool, with arguments that reach the wire — enumerated ONCE, because a
#: whole-surface property asserted over a hand-typed subset is the failure mode
#: this file already had: the read half's two "every tool" tests listed the
#: four read tools by hand, so they would have stayed green while covering none
#: of the write half.
_INVOCATIONS: dict[str, Any] = {
    "search_crm": lambda: _M.search_crm("acme"),
    "get_pipeline": lambda: _M.get_pipeline(),
    "get_record": lambda: _M.get_record("deals", _ID),
    "get_timeline": lambda: _M.get_timeline("deals", _ID),
    "create_lead": lambda: _M.create_lead("Acme Industries", email="a@acme.test"),
    "update_deal_status": lambda: _M.update_deal_status(_ID, "Negotiation"),
    "log_activity": lambda: _M.log_activity("deals", _ID, "note", "Called them"),
    "convert_lead": lambda: _M.convert_lead(_ID, deal_name="Acme AMC"),
    # CRM-C7: the shared read tools, one call each.
    "crm_list_records": lambda: _T.crm_list_records("deals", q="acme"),
    "crm_get_record": lambda: _T.crm_get_record("deals", _ID),
    "crm_list_fields": lambda: _T.crm_list_fields("deals"),
    "crm_list_pipelines": lambda: _T.crm_list_pipelines(),
    "crm_deal_contacts": lambda: _T.crm_deal_contacts(_ID),
    "crm_list_owners": lambda: _T.crm_list_owners(),
    "crm_report": lambda: _T.crm_report("pipeline"),
    "crm_aggregate": lambda: _T.crm_aggregate("deals", "status"),
    "crm_stage_dwell": lambda: _T.crm_stage_dwell(),
    "crm_list_modules": lambda: _T.crm_list_modules(),
    "crm_list_module_records": lambda: _T.crm_list_module_records("Products"),
    "crm_get_module_record": lambda: _T.crm_get_module_record("Products", _ID),
}


def test_the_invocation_table_covers_every_registered_tool() -> None:
    """The fence on the fence. Add a tool and forget this table and the two
    whole-surface properties below quietly stop covering it — which is exactly
    what happened to the read half's hand-typed lists."""
    assert set(_INVOCATIONS) == {fn.__name__ for fn in _M._TOOLS}


async def _call_every_tool() -> None:
    for invoke in _INVOCATIONS.values():
        await invoke()


async def test_every_tool_sends_the_acting_user(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder, user="sales@fracktal.in")
    await _call_every_tool()
    assert calls, "no gateway call was made at all"
    for call in calls:
        assert call["headers"].get("X-User-Email") == "sales@fracktal.in", (
            f"{call['path']} called the gateway with no acting user"
        )
        assert call["headers"].get("Authorization", "").startswith("Bearer ")


@pytest.mark.parametrize("tool", sorted(_INVOCATIONS), ids=sorted(_INVOCATIONS))
async def test_a_run_with_nobody_to_act_as_refuses_rather_than_calling(
    tool: str, monkeypatch,
) -> None:
    """Fails CLOSED: the bearer alone would be read as SERVICE_ACCESS.

    Covers the WRITE tools too, with the confirmation APPROVED — so this is the
    case where a human said yes and there is still nobody the write can be
    attributed to. Approving is what makes it a real test: with the card
    denied, three of the four would return "cancelled" without ever reaching
    ``_headers()`` and the fence would be untested.
    """
    approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder, user="")
    with pytest.raises(RuntimeError) as exc:
        await _INVOCATIONS[tool]()
    # Not "ACB_AGENT_USER_EMAIL" any more: S1-4 deleted that fallback, because
    # one process-global slot that no run ever cleared handed a run with no
    # identity the LAST run's user — and under one-org-per-user that email IS
    # the tenant. `_current_user_email` now resolves to "" and `_headers`
    # refuses, so the message is about the missing actor, not the missing var.
    assert "nobody to act as" in str(exc.value)
    assert calls == [], "the gateway was called despite having nobody to act as"


# ── 3. The verb allowlist — behaviourally, structurally, by observation ──────

def test_the_method_allowlist_is_the_three_verbs_the_tools_use() -> None:
    """WS-26d-write widened this by exactly two entries.

    ``DELETE``/``PUT`` are absent on purpose: no tool here removes or replaces
    a record, so the check that used to enforce "read-only" now enforces "never
    destroys", and deleting it along with the read-only doctrine would have
    thrown that away.
    """
    assert set(_M._ALLOWED_METHODS) == {"GET", "POST", "PATCH"}


@pytest.mark.parametrize("verb", ["PUT", "DELETE", "HEAD", "OPTIONS"])
async def test_a_destroying_verb_is_refused_before_the_request_is_built(
    verb: str, monkeypatch,
) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    with pytest.raises(RuntimeError, match="does not issue"):
        await _M._request(verb, "/crm/leads", json={"lead_name": "x"})
    assert calls == [], f"{verb} reached the gateway"


async def test_no_tool_issues_a_verb_outside_the_allowlist(monkeypatch) -> None:
    """Observation, not assertion-by-inspection: run every tool, watch the wire.

    Both halves of the claim: nothing outside the allowlist is ever spoken, and
    the four READ tools still speak nothing but GET — the widening must not
    have quietly let a read tool start writing.
    """
    approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder)
    await _call_every_tool()
    assert {c["method"] for c in calls} <= set(_M._ALLOWED_METHODS)
    assert {c["method"] for c in calls} == {"GET", "POST", "PATCH"}

    reads = _fake_gateway(monkeypatch, _responder)
    for name in ("search_crm", "get_pipeline", "get_record", "get_timeline"):
        await _INVOCATIONS[name]()
    assert {c["method"] for c in reads} == {"GET"}


def test_the_module_defines_only_the_write_helpers_its_tools_use() -> None:
    """Structural: ``_post`` and ``_patch`` now exist, and ``_delete``/``_put``
    still do not. A helper nobody needs is a helper somebody finds — and the
    absence is what makes "this agent cannot destroy a CRM record" a property
    of the module rather than a claim about its current tool list."""
    for helper in ("_post", "_patch"):
        assert hasattr(_M, helper), f"{helper} is missing — the write half needs it"
    for helper in ("_delete", "_put"):
        assert not hasattr(_M, helper), (
            f"{helper} exists — no tool here removes or replaces a record, and "
            "a helper for it is how the first one gets written"
        )
    source = agent_source()
    for verb in ('"DELETE"', '"PUT"'):
        assert f"_request({verb}" not in source


def test_every_tool_declares_its_risk_and_the_write_half_declares_it_destructive(
) -> None:
    """Root AGENTS.md standing rule 1 — a new agent tool declares its risk.

    Generalised rather than duplicated (WS-26d-write done-when 2): the read
    tools are ``read_only``, the four write tools are ``destructive``, and the
    two sets partition ``_TOOLS`` — so a ninth tool that declares neither, or
    both, fails here rather than shipping unclassified.

    ``destructive`` is what makes ``permission_policy.decide()`` return
    ``tool_destructive_defer`` instead of approving the call outright — it
    defers to the tool's OWN ``request_confirmation`` rather than double-gating
    it. Annotation is not enforcement; it is what tells the policy layer that
    enforcement lives in the tool.
    """
    annotations = pytest.importorskip("acb_skills.tool_annotations")
    writers = {"create_lead", "update_deal_status", "log_activity", "convert_lead"}
    seen: set[str] = set()
    for fn in _M._TOOLS:
        hints = annotations.TOOL_ANNOTATIONS.get(fn.__name__)
        assert hints is not None, f"{fn.__name__} declares no risk annotation"
        if fn.__name__ in CRM_FILE_WRITERS:
            _assert_a_file_writer(hints, fn.__name__)
        elif fn.__name__ in writers:
            assert hints["destructive"] is True, f"{fn.__name__} is not destructive"
            assert hints["read_only"] is False
            seen.add(fn.__name__)
        else:
            assert hints["read_only"] is True, f"{fn.__name__} is not read_only"
            assert hints["destructive"] is False
    assert seen == writers, f"a write tool is unannotated: {writers - seen}"


#: The CRM reads that can also write files: ``crm_report`` makes a kept report
#: with make_report=true (``projects_ai_chat.md`` §14.10.9). It still sends
#: only GET to the CRM, so it destroys nothing and reaches nothing outside.
#: ``read_only=False`` and ``idempotent=False`` stop an edited turn from
#: running it again unseen (``chat_supersede.is_side_effect_tool``).
CRM_FILE_WRITERS = frozenset({"crm_report"})


def _assert_a_file_writer(hints: dict, name: str) -> None:
    assert hints["read_only"] is False and hints["idempotent"] is False, name
    assert hints["destructive"] is False and hints["open_world"] is False, name


# ── 4. The routes each tool actually hits ────────────────────────────────────

async def test_search_one_entity_hits_that_entitys_list_route(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    await _M.search_crm("acme", entity="deals")
    assert [c["path"] for c in calls] == ["/crm/deals"]
    assert calls[0]["params"]["q"] == "acme"
    assert calls[0]["params"]["page_size"] == 10


async def test_search_without_an_entity_covers_all_four(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    await _M.search_crm("acme")
    assert [c["path"] for c in calls] == [
        "/crm/leads", "/crm/deals", "/crm/contacts", "/crm/organizations",
    ]


async def test_search_page_size_is_capped_below_the_kernels_own_cap(
    monkeypatch,
) -> None:
    """The list kernel caps at 100 itself; asking for 10 000 is a 422 there.
    Clamping here keeps the tool honest instead of relaying an error."""
    calls = _fake_gateway(monkeypatch, _responder)
    await _M.search_crm("acme", entity="leads", limit=10_000)
    assert calls[0]["params"]["page_size"] == 50


async def test_search_renders_rows_with_the_ids_a_follow_up_needs(
    monkeypatch,
) -> None:
    rows = {"rows": [{"id": _ID, "name": "Acme AMC", "amount": 250000,
                      "currency": "INR", "organization_name": "Acme Ltd",
                      "owner_email": "sales@fracktal.in"}], "total": 1}
    _fake_gateway(monkeypatch, lambda _c: rows)
    out = await _M.search_crm("acme", entity="deals")
    assert "Acme AMC" in out
    assert f"id={_ID}" in out
    assert "INR 250,000" in out


async def test_get_pipeline_hits_the_board_route_and_totals_the_lanes(
    monkeypatch,
) -> None:
    lanes = {"lanes": [
        {"status": {"id": "s1", "name": "Qualification", "type": "open"},
         "rows": [{"id": _ID, "name": "Acme AMC", "amount": 100000}],
         "count": 3, "amount": 300000.0},
        {"status": {"id": "s2", "name": "Won", "type": "won"},
         "rows": [], "count": 1, "amount": 50000.0},
    ]}
    calls = _fake_gateway(monkeypatch, lambda _c: lanes)
    out = await _M.get_pipeline(owner="sales@fracktal.in")
    assert [c["path"] for c in calls] == ["/crm/pipeline"]
    assert calls[0]["params"]["owner"] == "sales@fracktal.in"
    assert "Qualification [open] — 3 deals · INR 300,000" in out
    # Empty lanes still render: a board that hides them is a list.
    assert "Won [won] — 1 deal · INR 50,000" in out
    assert "Total: 4 deals · INR 350,000 across 2 stages." in out


async def test_get_record_hits_the_single_record_route(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, lambda _c: {
        "id": _ID, "name": "Acme AMC", "amount": 250000, "next_step": None,
    })
    out = await _M.get_record("deals", _ID)
    assert [c["path"] for c in calls] == [f"/crm/deals/{_ID}"]
    assert "Acme AMC" in out
    assert "amount: 250000" in out
    # An empty field is omitted rather than printed as None.
    assert "next_step" not in out


async def test_get_record_flags_a_converted_lead_with_its_deal(monkeypatch) -> None:
    """§3.3/B6 — "converted" keys on ``converted_deal_id``, and the live
    conversation has moved to that deal."""
    deal_id = str(uuid4())
    _fake_gateway(monkeypatch, lambda _c: {
        "id": _ID, "lead_name": "Acme", "converted_deal_id": deal_id,
    })
    out = await _M.get_record("lead", _ID)
    assert "converted" in out.lower()
    assert deal_id in out


async def test_get_timeline_hits_the_timeline_route(monkeypatch) -> None:
    entries = {"entries": [
        {"kind": "status_change", "at": "2026-08-01T10:00:00Z", "origin": "lead",
         "status_change": {"from_status": "New", "to_status": "Qualified",
                           "changed_by": "sales@fracktal.in",
                           "dwell_seconds": 172800}},
        {"kind": "activity", "at": "2026-07-30T09:00:00Z", "origin": "own",
         "activity": {"type": "call", "subject": "Intro call",
                      "body": "Discussed AMC", "created_by": "sales@fracktal.in"}},
    ]}
    calls = _fake_gateway(monkeypatch, lambda _c: entries)
    out = await _M.get_timeline("deals", _ID, limit=5)
    assert [c["path"] for c in calls] == [f"/crm/deals/{_ID}/timeline"]
    # A page of one total order: the route takes `offset` and says
    # `has_more` (test_read_tool_paging.py, review of #1014).
    assert calls[0]["params"]["limit"] == 5
    assert calls[0]["params"]["offset"] == 0
    assert "New → Qualified" in out
    assert "2.0d in the previous stage" in out
    # An inherited entry is labelled, never passed off as the deal's own (§5.3).
    assert "[from its lead]" in out
    assert "Intro call" in out


async def test_get_timeline_renders_an_email_thread_in_full(monkeypatch) -> None:
    """WS-26d-email — the agent is the THIRD consumer of the timeline shape,
    and it had the same binary dispatch the UI did: "not a status change" meant
    "an activity", so an email entry came out as ``email_thread: (no subject)``
    with the sender, subject, snippet and status all dropped.

    That is not a cosmetic loss. ``_timeline`` merges every source and THEN
    truncates to this tool's limit, so on a mail-heavy deal the twenty rows the
    model gets back were twenty blank ones — and it would answer "what's the
    story with this deal?" with nothing, from a record full of history.
    """
    entries = {"entries": [
        {"kind": "email_thread", "at": "2026-08-04T14:00:00Z", "origin": "own",
         "email_thread": {
             "thread_id": "thread-1", "account_id": "acct-1",
             "message_id": "msg-1", "subject": "Re: quote for 40 units",
             "snippet": "Can you hold that price to month end",
             "folder": "INBOX", "from_name": "Ravi Menon",
             "from_email": "ravi@acme.example",
             "received_at": "2026-08-04T14:00:00Z", "status": "NEEDS_REPLY"}},
        {"kind": "status_change", "at": "2026-08-01T10:00:00Z", "origin": "lead",
         "status_change": {"from_status": "New", "to_status": "Qualified",
                           "changed_by": "sales@fracktal.in",
                           "dwell_seconds": 172800}},
        {"kind": "activity", "at": "2026-07-30T09:00:00Z", "origin": "own",
         "activity": {"type": "call", "subject": "Intro call",
                      "created_by": "sales@fracktal.in"}},
    ]}
    _fake_gateway(monkeypatch, lambda _c: entries)
    out = await _M.get_timeline("deals", _ID)

    assert "Re: quote for 40 units" in out
    assert "Ravi Menon" in out and "ravi@acme.example" in out
    # D-CRM-12 — sender, subject, status, date; NEVER the snippet. The join is
    # caller-scoped, but an agent answer lands in a chat transcript and a ROOM
    # has other participants who can read it: sender+subject says a
    # conversation exists, a snippet publishes its CONTENT. The `/crm` UI keeps
    # the snippet on purpose — a browser has an audience of one.
    assert "Can you hold that price to month end" not in out
    # The status vocabulary is spoken, not echoed as a wire token.
    assert "[Needs reply]" in out and "NEEDS_REPLY" not in out
    # The email branch must not have eaten the other two.
    assert "New → Qualified" in out
    assert "Intro call" in out
    # And it is never mistaken for an activity.
    assert "email_thread: (no subject)" not in out


async def test_get_timeline_survives_a_thin_email_thread(monkeypatch) -> None:
    """Every field but the ids is optional on the wire. A thread with no
    subject, no sender and no status still renders one honest line — the
    ``.get()`` tolerance the rest of this tool is written with."""
    _fake_gateway(monkeypatch, lambda _c: {"entries": [
        {"kind": "email_thread", "at": "2026-08-04T14:00:00Z", "origin": "lead",
         "email_thread": {"thread_id": "t", "account_id": "a",
                          "message_id": "m"}},
        # A kind this build has never heard of must not take the answer down.
        {"kind": "future_kind", "at": "2026-08-03T09:00:00Z", "origin": "own"},
    ]})
    out = await _M.get_timeline("deals", _ID)

    assert "(no subject)" in out
    assert "unknown sender" in out
    assert "[from its lead]" in out
    assert "[]" not in out  # an absent status prints nothing, not empty brackets


async def test_get_timeline_shows_an_unrecognised_thread_status_verbatim(
    monkeypatch,
) -> None:
    """Same rule as the UI's ``threadStatusLabel``: the email app owns this
    vocabulary and may grow it, and hiding a value we do not recognise is how
    an agent starts disagreeing with the screen the user is looking at."""
    _fake_gateway(monkeypatch, lambda _c: {"entries": [
        {"kind": "email_thread", "at": "2026-08-04T14:00:00Z", "origin": "own",
         "email_thread": {"thread_id": "t", "account_id": "a",
                          "message_id": "m", "subject": "Hi",
                          "status": "SNOOZED"}},
    ]})
    assert "[SNOOZED]" in await _M.get_timeline("deals", _ID)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("lead", "leads"), ("Deals", "deals"), ("opportunity", "deals"),
     ("account", "organizations"), ("company", "organizations"),
     ("person", "contacts")],
)
def test_entity_aliases_resolve_to_the_route_slug(given: str, expected: str) -> None:
    assert _M._entity_slug(given) == expected


# ── 4a. The record id is a UUID, or the request is never built ──────────────
#
# This fence was invisible to the first 48 cases because every one of them
# passed `str(uuid4())`. It is not a validation nicety: `record_id` is
# interpolated into the path, httpx removes `..` segments before sending, and
# the tool therefore became a general authenticated GET client against the whole
# gateway — carrying the internal bearer AND the acting user's header.
#
# The payloads below are the ones measured reaching real routes: they are kept
# verbatim so this file records what the hole actually was, not a sanitised
# rendering of it.

_TRAVERSALS = [
    ("../../admin/members", "/admin/members"),
    ("x/../../../email/messages", "/email/messages/timeline"),
    ("../../memory/agent:email-assistant", "/memory/agent:email-assistant"),
    ("../../admin/members/owner@fracktal.in/access", None),
    ("../../whatsapp/chats", None),
    ("..%2F..%2Fadmin%2Fmembers", None),
    ("../pipeline", None),
]


#: Every tool that takes a record id, called with ONE — the id under test.
#: The write tools are in here because a traversal in a write path is the same
#: hole pointed at a bigger gun: it would carry the internal bearer to an
#: arbitrary route with a body attached.
_ID_TAKING_TOOLS: dict[str, Any] = {
    "get_record": lambda rid: _M.get_record("deals", rid),
    "get_timeline": lambda rid: _M.get_timeline("deals", rid),
    "log_activity": lambda rid: _M.log_activity("deals", rid, "note", "Called"),
    "update_deal_status": lambda rid: _M.update_deal_status(rid, "Negotiation"),
    "convert_lead": lambda rid: _M.convert_lead(rid),
}


@pytest.mark.parametrize("payload", [p for p, _ in _TRAVERSALS])
@pytest.mark.parametrize("tool", sorted(_ID_TAKING_TOOLS), ids=sorted(_ID_TAKING_TOOLS))
async def test_a_traversal_id_is_refused_before_any_request_is_built(
    tool: str, payload: str, monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder)
    with pytest.raises(RuntimeError, match="Invalid CRM record id"):
        await _ID_TAKING_TOOLS[tool](payload)
    assert calls == [], (
        f"{tool}({payload!r}) reached the gateway at "
        f"{[c['path'] for c in calls]} — the agent can read outside the CRM"
    )
    assert asked == [], (
        f"{tool}({payload!r}) put a traversal in front of a human to approve — "
        "a malformed id is refused by the guard, never delegated to a card"
    )


@pytest.mark.parametrize(
    "bad", ["ACME-123", "", "   ", "12345", "deal-42", "null", "undefined"],
)
@pytest.mark.parametrize("tool", sorted(_ID_TAKING_TOOLS), ids=sorted(_ID_TAKING_TOOLS))
async def test_a_non_uuid_id_is_refused_here_not_relayed_as_a_driver_500(
    tool: str, bad: str, monkeypatch,
) -> None:
    """Second-order win: all four tables key on ``CAST(:id AS uuid)``, so a
    hallucinated id used to come back as a 500 from the driver."""
    approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder)
    with pytest.raises(RuntimeError, match="Invalid CRM record id"):
        await _ID_TAKING_TOOLS[tool](bad)
    assert calls == []


@pytest.mark.parametrize("tool", sorted(_ID_TAKING_TOOLS), ids=sorted(_ID_TAKING_TOOLS))
async def test_a_real_uuid_still_works(tool: str, monkeypatch) -> None:
    """The guard must not cost the legitimate call — the failure mode of a fence
    added under review is that it refuses everything and nobody notices."""
    approve(monkeypatch)
    calls = _fake_gateway(monkeypatch, _responder)
    await _ID_TAKING_TOOLS[tool](_ID)
    assert calls, f"{tool} made no call at all with a valid id"
    for call in calls:
        assert call["path"].startswith("/crm/"), (
            f"{tool} left the CRM: {call['path']}"
        )
    assert any(_ID in c["path"] for c in calls), (
        f"{tool} never addressed the record it was given"
    )


@pytest.mark.parametrize(
    "template",
    ["{0}", "{{{0}}}", "urn:uuid:{0}", "  {0}  "],
    ids=["plain", "braced", "urn", "padded"],
)
def test_alternate_uuid_spellings_are_normalised_to_the_canonical_form(
    template: str,
) -> None:
    """``uuid.UUID`` accepts braced/urn/unhyphenated forms, so the guard returns
    ``str(UUID(...))`` rather than the caller's string: whatever spelling the
    model produced, what reaches the path is 36 characters that cannot hold a
    separator."""
    assert _M._record_uuid(template.format(_ID)) == _ID


def test_unhyphenated_and_uppercase_ids_normalise_too() -> None:
    assert _M._record_uuid(_ID.replace("-", "")) == _ID
    assert _M._record_uuid(_ID.upper()) == _ID


#: The only names a ``/crm`` path may interpolate: ``slug`` comes from
#: ``_entity_slug`` and ``record`` from ``_record_uuid``, so binding to one of
#: these two IS the claim that a validator produced the value.
_VALIDATED_PATH_NAMES = frozenset({"slug", "record"})


def _is_crm_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/crm")
    )


def _add_leaves(node: ast.AST) -> list[ast.AST]:
    """Every operand of an ``a + b + c`` chain, flattened."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_leaves(node.left) + _add_leaves(node.right)
    return [node]


# One reader per string-building idiom. Split up rather than inlined into one
# walk because the fence's own test parametrises over the idioms: "the fence
# went blind to `.format`" should point at a function, not at a branch buried
# in a loop.

def _fstring_parts(node: ast.JoinedStr) -> list[ast.AST]:
    """``f"/crm/{slug}/{record}"`` — the idiom the read half already scanned."""
    if not any(_is_crm_literal(part) for part in node.values):
        return []
    return [p.value for p in node.values if isinstance(p, ast.FormattedValue)]


def _format_parts(node: ast.Call) -> list[ast.AST]:
    """``"/crm/{}/{}".format(entity, record_id)``."""
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "format"
        and _is_crm_literal(func.value)
    ):
        return []
    return [*node.args, *(kw.value for kw in node.keywords)]


def _binop_parts(node: ast.BinOp) -> list[ast.AST]:
    """``"/crm/%s" % record_id``, and ``"/crm/" + entity + "/" + record_id``."""
    if isinstance(node.op, ast.Mod) and _is_crm_literal(node.left):
        right = node.right
        return list(right.elts) if isinstance(right, ast.Tuple) else [right]
    if isinstance(node.op, ast.Add):
        leaves = _add_leaves(node)
        if any(_is_crm_literal(leaf) for leaf in leaves):
            return [leaf for leaf in leaves if not isinstance(leaf, ast.Constant)]
    return []


def _crm_interpolations(node: ast.AST) -> list[ast.AST]:
    """The dynamic operands of a ``/crm`` string build, whichever idiom built it."""
    if isinstance(node, ast.JoinedStr):
        return _fstring_parts(node)
    if isinstance(node, ast.Call):
        return _format_parts(node)
    if isinstance(node, ast.BinOp):
        return _binop_parts(node)
    return []


def _crm_path_offenders(
    source: str, validated: frozenset[str] = _VALIDATED_PATH_NAMES,
) -> list[str]:
    """Every ``/crm`` path this source builds out of something unvalidated.

    **Four idioms, not one.** The read half scanned ``ast.JoinedStr`` only, so
    the identical hole spelled ``"/crm/{}/{}".format(entity, record_id)`` —
    or with ``%``, or with ``+`` — walked straight past it. That is the P2 the
    re-review left open (WS-26d-write done-when 4), and it matters now rather
    than in the abstract: the write tools are exactly where somebody assembling
    a longer path reaches for ``.format``.

    The fence is deliberately blunt. It does not try to decide whether an
    expression is *safe*; it requires the value to arrive under one of two
    names, so "is this validated?" is answered by where it came from rather
    than by re-deriving the answer at every call site.
    """
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        for expr in _crm_interpolations(node):
            name = getattr(expr, "id", None)
            if name not in validated:
                offenders.append(
                    f"line {getattr(node, 'lineno', '?')}: interpolates "
                    f"{name or ast.dump(expr)!r}"
                )
    return offenders


def test_the_path_guard_is_structural_not_a_docstring() -> None:
    """The fence in the same spirit as the ``_ALLOWED_METHODS`` one.

    Every ``/crm`` path the module builds may interpolate ONLY names that can
    hold a validated value: ``slug`` (from ``_entity_slug``) and ``record``
    (from ``_record_uuid``). A tool that drops a raw parameter — ``record_id``,
    ``entity``, ``query`` — straight into the path fails here, whichever of the
    four string-building idioms it used to do it.
    """
    offenders = _crm_path_offenders(agent_source())
    assert offenders == [], (
        "a /crm path interpolates something no validator produced: "
        f"{offenders}. Route it through _entity_slug/_record_uuid and bind the "
        "result to `slug`/`record`."
    )


#: One synthetic module per string-building idiom, in the unvalidated spelling
#: and the validated one. This is what makes the fence itself testable: a fence
#: only ever run against code that already passes cannot tell you it has gone
#: blind, and going blind is precisely what the f-string-only version had done.
_UNVALIDATED_PATHS: dict[str, str] = {
    "fstring": 'x = f"/crm/{entity}/{record_id}/activities"\n',
    "format": 'x = "/crm/{}/{}/activities".format(entity, record_id)\n',
    "percent": 'x = "/crm/%s/%s/activities" % (entity, record_id)\n',
    "concat": 'x = "/crm/" + entity + "/" + record_id + "/activities"\n',
}
_VALIDATED_PATHS: dict[str, str] = {
    "fstring": 'x = f"/crm/{slug}/{record}/activities"\n',
    "format": 'x = "/crm/{}/{}/activities".format(slug, record)\n',
    "percent": 'x = "/crm/%s/%s/activities" % (slug, record)\n',
    "concat": 'x = "/crm/" + slug + "/" + record + "/activities"\n',
}


@pytest.mark.parametrize("idiom", sorted(_UNVALIDATED_PATHS))
def test_the_path_fence_sees_every_string_building_idiom(idiom: str) -> None:
    """Done-when 4: the fence catches ``.format``/``%``/``+``, not just f-strings.

    Both directions per idiom, because a fence that flags everything is as
    useless as one that flags nothing — and the "flags everything" failure is
    the one that gets a fence deleted rather than fixed.
    """
    assert _crm_path_offenders(_UNVALIDATED_PATHS[idiom]), (
        f"the fence is blind to the {idiom} idiom — a /crm path built that way "
        "from a raw parameter would ship unnoticed"
    )
    assert _crm_path_offenders(_VALIDATED_PATHS[idiom]) == [], (
        f"the fence refuses a correctly-validated {idiom} path"
    )


def test_the_path_fence_ignores_strings_that_are_not_crm_paths() -> None:
    """Scoping: an error message that interpolates a caller's word is prose,
    not a path, and a fence that fires on it teaches nobody anything."""
    assert _crm_path_offenders('x = f"Unknown record type {entity!r}"\n') == []
    assert _crm_path_offenders('x = "/email/{}".format(message_id)\n') == []


def test_an_unknown_entity_is_refused_not_defaulted() -> None:
    """The list kernel refuses an unknown sort key rather than falling back;
    answering about the wrong record type is the same class of silent wrong."""
    with pytest.raises(RuntimeError, match="Unknown CRM record type"):
        _M._entity_slug("invoices")


async def test_an_unknown_entity_never_reaches_the_gateway(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    with pytest.raises(RuntimeError):
        await _M.get_record("invoices", _ID)
    assert calls == []


async def test_a_gateway_refusal_is_relayed_not_swallowed(monkeypatch) -> None:
    """A 403 from the feature gate must surface as a refusal the agent can
    repeat, not as an empty result that reads like "there is no such deal"."""
    class _Denying(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any):
            self._calls.append({"method": method, "url": url})
            return FakeResponse({"detail": "Feature 'crm' not enabled"}, 403)

    calls = fake_gateway(
        _M, monkeypatch, None, user="nobody@fracktal.in", client_class=_Denying,
    )
    with pytest.raises(RuntimeError) as exc:
        await _M.get_pipeline()
    assert "403" in str(exc.value)
    assert "Feature 'crm' not enabled" in str(exc.value)
    assert len(calls) == 1


# ── 4b. The shared read tools (WS-53 CRM-C7, crm_platform.md §20.10) ────────

#: Each shared tool → the route it must read. Enumerated over CRM_TOOLS, so a
#: thirteenth tool with no row here fails the first test below.
_SHARED_ROUTES: dict[str, str] = {
    "crm_list_records": "/crm/deals",
    "crm_get_record": f"/crm/deals/{_ID}",
    "crm_list_fields": "/crm/fields/deals",
    "crm_list_pipelines": "/crm/pipelines",
    "crm_deal_contacts": f"/crm/deals/{_ID}/contacts",
    "crm_list_owners": "/crm/owners",
    "crm_report": "/crm/reports/pipeline",
    "crm_aggregate": "/crm/aggregate",
    "crm_stage_dwell": "/crm/aggregate/stage-dwell",
    "crm_list_modules": "/crm/modules",
    "crm_list_module_records": "/crm/modules/Products/records",
    "crm_get_module_record": f"/crm/modules/Products/records/{_ID}",
}


def test_the_route_table_covers_every_shared_tool() -> None:
    assert set(_SHARED_ROUTES) == set(_T.CRM_TOOL_NAMES)


@pytest.mark.parametrize("tool", sorted(_SHARED_ROUTES), ids=sorted(_SHARED_ROUTES))
async def test_each_shared_tool_reads_its_route_as_the_member(
    tool: str, monkeypatch,
) -> None:
    """Done-when 1: the route, the verb and the member headers, per tool."""
    calls = _fake_gateway(monkeypatch, _responder, user="sales@fracktal.in")
    answer = await _INVOCATIONS[tool]()
    assert isinstance(answer, str) and answer
    assert _SHARED_ROUTES[tool] in [c["path"] for c in calls]
    for call in calls:
        assert call["method"] == "GET"
        assert call["headers"].get("X-User-Email") == "sales@fracktal.in"
        assert call["headers"].get("Authorization", "").startswith("Bearer ")


def test_every_shared_tool_is_an_annotated_read() -> None:
    """Read only, and ``open_world=False`` stated, so H-236 keeps them."""
    annotations = pytest.importorskip("acb_skills.tool_annotations")
    for fn in _T.CRM_TOOLS:
        hints = annotations.TOOL_ANNOTATIONS[fn.__name__]
        if fn.__name__ in CRM_FILE_WRITERS:
            _assert_a_file_writer(hints, fn.__name__)
        else:
            assert hints["read_only"] is True and hints["destructive"] is False
        assert hints["open_world"] is False
        assert fn.__tool_risk__ is hints
        assert inspect.iscoroutinefunction(fn) and (fn.__doc__ or "").strip()


async def test_a_record_answer_shows_labels_not_raw_keys(monkeypatch) -> None:
    """Done-when 1: a custom key and a picklist value read as their labels."""
    record = {
        "id": _ID, "name": "Acme AMC", "expected_close_date": "2026-11-01",
        "custom": {"cf_region_x1": "s", "cf_tags_x2": ["a", "b"]},
    }
    fields = [
        {"key": "cf_region_x1", "label": "Sales region", "type": "picklist",
         "options": [{"label": "South", "value": "s"}]},
        {"key": "cf_tags_x2", "label": "Tags", "type": "multiselect",
         "options": [{"label": "Alpha", "value": "a"}, {"label": "Beta", "value": "b"}]},
    ]

    def _answer(call: dict) -> Any:
        return fields if call["path"].startswith("/crm/fields/") else record

    _fake_gateway(monkeypatch, _answer)
    out = await _T.crm_get_record("deal", _ID)
    assert "Sales region: South" in out
    assert "Tags: Alpha, Beta" in out
    assert "Expected close date: 2026-11-01" in out
    assert "cf_region_x1" not in out and "cf_tags_x2" not in out


async def test_a_long_list_holds_fifty_rows_and_says_more(monkeypatch) -> None:
    """D1: 50 rows at most, and the next page is named."""
    rows = [{"id": str(uuid4()), "name": f"Deal {i}"} for i in range(50)]
    calls = _fake_gateway(monkeypatch, lambda _c: {"rows": rows, "total": 120})
    out = await _T.crm_list_records("deals", page_size=500)
    assert calls[0]["params"]["page_size"] == 50
    assert out.count("\n- ") == 50
    # The shared hint line (acb_common.paging), 2026-10-14.
    assert out.rstrip().endswith("Showing 1-50 of 120. Call again with page=2 for the rest.")
    last = await _T.crm_list_records("deals", page=3, page_size=50)
    assert "Call again" not in last


async def test_a_status_name_becomes_the_status_id(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    await _T.crm_list_records("deals", status="negotiation", owner="me")
    listed = next(c for c in calls if c["path"] == "/crm/deals")
    assert listed["params"]["status_id"] == _STATUS_ID
    assert listed["params"]["owner"] == "me"


async def test_the_aggregate_sends_the_filters_of_the_list(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    await _T.crm_aggregate(
        "deals", "owner", measure="weighted", close_from="2026-10-01",
        custom={"cf_region": "South"},
    )
    params = calls[-1]["params"]
    assert calls[-1]["path"] == "/crm/aggregate"
    assert (params["entity"], params["group_by"], params["measure"]) == (
        "deals", "owner", "weighted",
    )
    assert params["close_from"] == "2026-10-01"
    assert params["custom.cf_region"] == "South"
    assert "_status" not in params


def _refusing(status: int, detail: Any) -> type[FakeClient]:
    class _Refusing(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any):
            self._calls.append({"method": method, "url": url})
            return FakeResponse({"detail": detail}, status)
    return _Refusing


_SHARED_TOOLS = sorted(_T.CRM_TOOL_NAMES)


@pytest.mark.parametrize("tool", _SHARED_TOOLS, ids=_SHARED_TOOLS)
@pytest.mark.parametrize(("status", "detail", "expected"), [
    (404, "Not Found", "The CRM is off for this organization"),
    (403, "Forbidden: an admin of your organization has not given you access "
          "to the CRM.", "The CRM is not open to you."),
    (403, "Feature 'crm' not enabled", "The CRM is not open to you."),
])
async def test_a_refusal_is_the_answer(
    status, detail, expected, tool: str, monkeypatch,
) -> None:
    """D3: the kill switch (404) and the access rule (403) read apart, for
    EVERY shared tool. A tool that loses ``_read_tool`` raises here."""
    calls: list[dict] = []
    monkeypatch.setattr(_T, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: _refusing(status, detail)(calls, None)))
    monkeypatch.setattr(_T, "_current_user_email", lambda: "sales@fracktal.in")
    answer = await _INVOCATIONS[tool]()
    assert expected in answer
    assert len(calls) == 1


@pytest.mark.parametrize("tool", _SHARED_TOOLS, ids=_SHARED_TOOLS)
@pytest.mark.parametrize("identity", ["room:4f1c-thread", "agent:crm", "default"])
async def test_a_shared_room_reads_no_crm(tool: str, identity: str, monkeypatch) -> None:
    """A run whose identity is not an email (a shared room's compartment)
    sends nothing to the gateway, and the tool names the shared room."""
    calls = _fake_gateway(monkeypatch, _responder, user=identity)
    answer = await _INVOCATIONS[tool]()
    assert answer == _T.CRM_SHARED_ROOM
    assert "shared room" in answer
    assert calls == []


async def test_the_mode_reader_refuses_a_shared_room(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, lambda _c: {"mode": "mirror"}, user="room:x")
    with pytest.raises(Exception, match="shared room"):
        await _T.crm_mode()
    assert calls == []


async def test_a_missing_record_is_not_read_as_the_crm_off(monkeypatch) -> None:
    """A 404 that names the record is an error, not the kill switch."""
    monkeypatch.setattr(_T, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: _refusing(404, "Deal not found")([], None)))
    monkeypatch.setattr(_T, "_current_user_email", lambda: "sales@fracktal.in")
    with pytest.raises(RuntimeError, match="Deal not found"):
        await _T.crm_get_record("deals", _ID)


async def test_mirror_dwell_relays_the_reason(monkeypatch) -> None:
    """D9: Mirror mode has no stage log, and the tool says so."""
    reason = "This CRM mirrors Zoho, and the sync records no stage changes."
    _fake_gateway(monkeypatch, lambda _c: {
        "stages": [], "overdue": [], "overdue_total": 0, "reason": reason,
    })
    assert await _T.crm_stage_dwell() == reason


#: The names a /crm path in ``crm_tools`` may interpolate, each bound to a
#: validator's result: the entity, an id, a module name, a report kind, and a
#: status vocabulary from a fixed map.
_SHARED_VALIDATED = frozenset({"slug", "record", "mod", "report", "vocab"})


def test_the_shared_tools_bound_every_path_segment() -> None:
    source = Path(_T.__file__).read_text(encoding="utf-8")
    assert _crm_path_offenders(source, _SHARED_VALIDATED) == []


@pytest.mark.parametrize("bad", ["../admin", "Products/../../x", "1abc", ""])
async def test_a_bad_module_name_never_reaches_the_gateway(bad, monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, _responder)
    with pytest.raises(RuntimeError, match="Invalid module name"):
        await _T.crm_list_module_records(bad)
    with pytest.raises(RuntimeError, match="Unknown report"):
        await _T.crm_report("../../admin")
    assert calls == []


async def test_the_shared_mode_reader_reads_the_member_route(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, lambda _c: {"mode": "mirror"})
    assert await _T.crm_mode() == "mirror"
    assert [c["path"] for c in calls] == ["/crm/mode"]


# ── 5. WhatsApp: "crm" is parse-only, and the test says so ───────────────────

wa_context = pytest.importorskip(
    "gateway.routes.whatsapp.transport.context",
    reason="gateway not installed in this environment",
)


def test_crm_is_a_known_entity_ref_system() -> None:
    assert "crm" in wa_context._KNOWN_SYSTEMS


@pytest.mark.parametrize("kind", ["lead", "deal", "contact", "organization"])
def test_a_crm_entity_ref_parses_into_a_deep_linkable_ref(kind: str) -> None:
    record_id = str(uuid4())
    ref = wa_context.parse_entity_ref(f"crm:{kind}:{record_id}")
    assert ref is not None
    assert (ref.system, ref.kind, ref.id) == ("crm", kind, record_id)


def test_an_unknown_system_still_degrades_to_unlinked() -> None:
    """Adding one system must not turn the allowlist into a pass-through."""
    assert wa_context.parse_entity_ref(f"salesforce:lead:{uuid4()}") is None
    assert wa_context.parse_entity_ref("crm:lead") is None
    assert wa_context.parse_entity_ref("crm::abc") is None


_GATEWAY_ROOT = REPO_ROOT / "apps" / "services" / "gateway" / "gateway"
_WHATSAPP_ROOT = _GATEWAY_ROOT / "routes" / "whatsapp"


def _string_literals(root: Path) -> list[tuple[str, str]]:
    """(file, string literal) for every .py under ``root``.

    Read through ``ast`` on purpose: a ``#`` comment is not a literal, so this
    cannot be satisfied — or tripped — by prose ABOUT the thing it is pinning.
    That is the trap the first version of this test fell into, matching the
    comment that documents the parse-only rule.
    """
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        out.extend(
            (rel, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    return out


def test_nothing_writes_a_crm_entity_ref_yet() -> None:
    """PARSE-ONLY, pinned. The linker that would write
    ``entity_ref = 'crm:<kind>:<uuid>'`` is a later slice; until it lands, this
    constant changes exactly one behaviour — a hand-set ref parses. If this test
    starts failing because a writer appeared, that writer owes the ``crm``
    context block too, and this pin should be REPLACED, not deleted.

    Scoped to the WhatsApp package: ``"crm:"`` is not a reserved string
    repo-wide (``routes/crm/broker_handlers.py`` uses ``"crm:zoho-sync"`` as the
    sync engine's actor label), and a fence that fires on an unrelated colon
    teaches nobody anything.
    """
    offenders = sorted({
        rel for rel, literal in _string_literals(_WHATSAPP_ROOT)
        if "crm:" in literal
    })
    assert offenders == [], f"something now emits a CRM entity_ref: {offenders}"


def test_nothing_writes_wa_contacts_entity_ref_at_all() -> None:
    """The wider truth this slice does not change: ``entity_ref`` is read-only
    everywhere in the gateway today — no route, importer or enricher sets it for
    ANY system. So adding ``"crm"`` to the allowlist cannot start producing
    links; it can only stop discarding one somebody typed in by hand."""
    offenders = sorted({
        rel
        for rel, literal in _string_literals(_GATEWAY_ROOT)
        if "entity_ref" in (low := literal.lower())
        and ("update " in low or "insert into" in low)
    })
    assert offenders == [], f"an entity_ref writer appeared in {offenders}"


def test_the_crm_context_block_is_still_unfilled() -> None:
    """The other half of parse-only: the model's ``crm`` block defaults to None,
    so a parsed ref is a deep link and nothing more."""
    model = wa_context.ChatContextModel(chat_id="c1")
    assert model.crm is None
    assert wa_context.ChatContextModel.model_fields["crm"].default is None
