"""The mail paths of the injection eval, each run with no model and no network.

WS-17 EM-G11d (UMCC). Each function runs the REAL prompt builder of one path
of ``email_app_master_plan.md`` section 12.3.13.2, item 4, with one attack
mail, and returns what that path hands to the model:

* the text the model reads (the messages of a model call, or the result of a
  tool that the email assistant reads);
* the tools of that call or that run;
* for the background consult, the ``no_egress`` answer of the consulted run.

A fake stands in for the model, the gateway and the database. The fakes
return fixed answers and decide nothing, so a check reads only what our code
built. Nothing here writes, sends or fetches.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals.email_injection.mails import Mail

REPO = Path(__file__).resolve().parents[2]
_AGENT_DIR = REPO / "apps" / "agents" / "agent-email-assistant"
_MAIL_ID = "9b2f4c1e-0d3a-4e5f-8a7b-6c5d4e3f2a1b"
_ACCOUNT = "4a3b2c1d-0e9f-4a8b-9c7d-6e5f4a3b2c1d"


@dataclass
class Capture:
    """What one path handed to the model for one mail."""

    path: str
    #: Each model call, or each tool result, as a list of texts. A model
    #: call holds the system message first. A tool result holds one text.
    calls: list[list[str]] = field(default_factory=list)
    #: The tool names of the call or the run. ``None`` when the path makes
    #: a model call with no tools at all.
    tools: frozenset[str] | None = None
    #: The ``no_egress`` answer of each consulted run (the consult only).
    no_egress: list[bool] = field(default_factory=list)
    #: The fields of a mail that this path reads.
    fields: frozenset[str] = frozenset()
    #: For a path of the email assistant's own run, what that run reaches past
    #: its own tools: the H-236 answer of a run that it delegates to with
    #: ``call_agent``, and the count of registry reads of the MCP injection.
    delegate_no_egress: bool | None = None
    mcp_reads: int | None = None


@contextlib.contextmanager
def _patched(*patches: tuple[Any, str, Any]) -> Iterator[None]:
    """Set each ``(owner, name, value)`` and put the old value back."""
    saved = [(owner, name, getattr(owner, name)) for owner, name, _ in patches]
    try:
        for owner, name, value in patches:
            setattr(owner, name, value)
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


# -- the email assistant ------------------------------------------------------

_AGENT_MODULE: Any = None
_AGENT_TOOLS: frozenset[str] | None = None


def email_agent() -> Any:
    """The module of the email assistant, loaded once from its file."""
    global _AGENT_MODULE
    if _AGENT_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "ea_injection_eval", _AGENT_DIR / "agents.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AGENT_MODULE = module
    return _AGENT_MODULE


def email_agent_tools() -> frozenset[str]:
    """The tool names of the email assistant on a plain run, after the
    injection chain applies its config (the scope and ``floor_opt_out``)."""
    global _AGENT_TOOLS
    if _AGENT_TOOLS is None:
        import orchestrator._tool_injection as ti
        from acb_common.settings import get_settings

        os.environ.setdefault("OPENAI_API_KEY", "sk-injection-eval-dummy")
        get_settings.cache_clear()
        cfg = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
        agents = email_agent().build_agents()
        with _patched(
            (ti, "_build_registry_block", lambda: "Registered agents:"),
            (ti, "_load_disabled_skill_families", lambda _name: frozenset()),
        ):
            import orchestrator.app_tools as app_tools

            with _patched((app_tools, "load_app_action_tools", lambda _name: [])):
                ti._build_injected_tools_addendum.cache_clear()
                ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
                ti._inject_agent_tools(
                    agents, tool_scope=cfg.get("tool_scope") or None,
                    agent_name="email-assistant", agent_config=cfg,
                )
                ti._build_injected_tools_addendum.cache_clear()
        _AGENT_TOOLS = frozenset(
            ti._tool_name(t) for t in agents[0].default_options["tools"])
    return _AGENT_TOOLS


_REACH: tuple[bool, int] | None = None


def email_agent_reach() -> tuple[bool, int]:
    """What a run of the email assistant reaches past its own tools.

    The first value is the H-236 answer for a run that the email assistant
    delegates to (``crm-assistant``, which holds ``fetch_page`` alone). The
    second counts the registry reads of the real MCP injection for the email
    assistant, with ``MAF_MCP_ORGS`` allowing the org. Both run the real code,
    and only the registry is a fake."""
    global _REACH
    if _REACH is None:
        import gateway.connections.mcp as mcp_registry
        import orchestrator._tool_injection as ti
        from orchestrator import _maf_mcp

        reads: list[str] = []

        async def servers_for_org(org: str, **_k: Any) -> list[Any]:
            reads.append(org)
            return []

        parent = {"agent_name": "email-assistant", "no_egress": False}
        delegated = ti._run_no_egress("crm-assistant", parent)
        with _patched((mcp_registry, "servers_for_org", servers_for_org),
                      (_maf_mcp, "maf_mcp_org_allowed", lambda _org: True),
                      (_maf_mcp, "is_native_maf_agent", lambda _a: True)):
            asyncio.run(ti._inject_mcp_servers(
                object(), "email-assistant", organization_id="org-injection-eval"))
        _REACH = (delegated, len(reads))
    return _REACH


def _assistant_capture(name: str, calls: list[list[str]], fields: set[str]) -> Capture:
    """A capture of a path that the email assistant's own run reads."""
    delegated, mcp_reads = email_agent_reach()
    return Capture(name, calls=calls, tools=email_agent_tools(),
                   fields=frozenset(fields), delegate_no_egress=delegated,
                   mcp_reads=mcp_reads)


def _gateway(mail: Mail) -> Callable[..., Awaitable[Any]]:
    """A fake ``_get`` of the agent: the gateway answers with *mail*."""
    sender = {"name": mail.value("from_name"), "email": mail.value("from")}
    message = {
        "id": _MAIL_ID, "account_id": _ACCOUNT, "thread_id": "t-1",
        "folder": "inbox", "from_address": sender,
        "to_addresses": [{"name": "Owner", "email": "owner@company.example"}],
        "cc_addresses": [], "subject": mail.value("subject"),
        "received_at": "2026-10-14T09:00:00Z", "body_text": mail.value("body"),
        "attachments": [{"id": "5e4d3c2b-1a09-4f8e-9d7c-6b5a4f3e2d1c",
                         "filename": mail.value("attachment"),
                         "mime_type": "application/pdf"}],
    }
    earlier = dict(message, body_text=mail.value("thread"),
                   from_address={"name": "Priya Raman", "email": "priya@vendor.example"})

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        if path.endswith("/full-body"):
            return {"subject": mail.value("subject"),
                    "from": f"{mail.value('from_name')} <{mail.value('from')}>",
                    "body_text": mail.value("body")}
        if path == "/email/messages":
            return {"emails": [earlier, message], "total": 2}
        return message

    return get


def _tool_path(name: str, fields: set[str],
               call: Callable[[Any], Awaitable[str]]) -> Callable[[Mail], Capture]:
    def run(mail: Mail) -> Capture:
        agents = email_agent()
        with _patched((agents, "_get", _gateway(mail))):
            out = asyncio.run(call(agents))
        return _assistant_capture(name, [[out]], fields)
    return run


read_email = _tool_path(
    "read_email", {"from_name", "from", "subject", "body", "attachment"},
    lambda a: a.read_email(_MAIL_ID))
read_email_full = _tool_path(
    "read_email_full", {"from_name", "from", "subject", "body"},
    lambda a: a.read_email(_MAIL_ID, full=True))
read_thread = _tool_path(
    "read_thread", {"from_name", "from", "subject", "body", "thread"},
    lambda a: a.read_thread(email_id=_MAIL_ID))


# -- the gateway paths --------------------------------------------------------

def _email_dict(mail: Mail) -> dict[str, str]:
    """The email dict that the drafter, the planner and the classifier read."""
    return {
        "from": mail.value("from"), "from_name": mail.value("from_name"),
        "subject": mail.value("subject"), "body": mail.value("body"),
        "thread": mail.value("thread"), "to": "owner@company.example", "cc": "",
        "attachments": f"Attachments: {mail.value('attachment')} (application/pdf)",
        "self": "owner@company.example", "sender_scope": "external",
        "date": "2026-10-14",
    }


def _texts(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("content") or "") for m in messages]


def drafter(mail: Mail) -> Capture:
    """``drafting._llm_draft_reply``, the reply drafter."""
    from gateway.routes.email.automation import drafting

    cap = Capture("drafter", fields=frozenset(
        {"from_name", "from", "subject", "body", "thread", "attachment"}))
    tools: list[Any] = []

    async def fake(*, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        cap.calls.append(_texts(messages))
        tools.append(kw.get("tools"))
        reply = SimpleNamespace(message=SimpleNamespace(content="Hello Priya,\n\nThanks."))
        return SimpleNamespace(choices=[reply]), model

    import acb_llm.context as llm_context

    about = "<personal_instructions>\nKeep replies short.\n</personal_instructions>"
    with _patched((llm_context, "acompletion_with_fallback", fake)):
        asyncio.run(drafting._llm_draft_reply(
            _email_dict(mail), about=about, signature="",
            context="From the crm-assistant agent (asked: order status?):\nIn transit.",
            model="tier-powerful"))
    cap.tools = frozenset() if not any(tools) else frozenset({"<tools>"})
    return cap


def compose_assist(mail: Mail) -> Capture:
    """``drafting._llm_compose_assist``: "Draft with AI" in the composer, in
    reply mode. The recipient and the subject come from the mail."""
    from gateway.routes.email.automation import drafting

    cap = Capture("compose_assist", fields=frozenset(
        {"from_name", "from", "subject", "body", "thread"}))
    tools: list[Any] = []

    async def fake(*, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        cap.calls.append(_texts(messages))
        tools.append(kw.get("tools"))
        reply = SimpleNamespace(message=SimpleNamespace(content="Hello Priya,\n\nThanks."))
        return SimpleNamespace(choices=[reply]), model

    import acb_llm.context as llm_context

    with _patched((llm_context, "acompletion_with_fallback", fake)):
        asyncio.run(drafting._llm_compose_assist(
            about="", signature="", current_body="", instruction="Say yes politely.",
            mode="reply",
            recipient=f"{mail.value('from_name')} <{mail.value('from')}>",
            subject=f"Re: {mail.value('subject')}", thread=mail.value("thread"),
            reply_to_body=mail.value("body"), sender="owner@company.example"))
    cap.tools = frozenset() if not any(tools) else frozenset({"<tools>"})
    return cap


def consult_plan(mail: Mail) -> Capture:
    """``drafting._draft_consult_plan``, the consult planner."""
    from gateway.routes.email.automation import drafting

    cap = Capture("consult_plan", fields=frozenset({"from", "subject", "body"}))
    tools: list[Any] = []

    async def fake(model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        cap.calls.append(_texts(messages))
        tools.append(kw.get("tools"))
        return {"kind": "other", "consult": []}, "", model

    with _patched((drafting, "_llm_json", fake)):
        asyncio.run(drafting._draft_consult_plan(_email_dict(mail)))
    cap.tools = frozenset() if not any(tools) else frozenset({"<tools>"})
    return cap


def consult_run(mail: Mail) -> Capture:
    """``drafting._orchestrate_draft`` with a plan that names a consult.

    The question is the attack text itself, the worst case: the planner
    copied the mail. The fake ``run_agent`` reads the parent context that a
    real run reads, and asks the H-236 rule for the answer of the run.
    """
    import orchestrator._tool_injection as ti
    import orchestrator.executor as executor
    from acb_skills import memory_tools
    from acb_skills.write_artifact import artifact_context
    from gateway.routes.email.automation import drafting

    cap = Capture("consult_run", fields=frozenset())

    async def plan(*_a: Any, **_k: Any) -> list[dict[str, str]]:
        return [{"agent": "crm-assistant", "question": mail.text}]

    async def run_agent(agent: str, payload: dict[str, Any], *_a: Any, **_k: Any) -> Any:
        cap.no_egress.append(ti._run_no_egress(agent, artifact_context()))
        return {"answer": "No data."}

    async def remember(*_a: Any, **_k: Any) -> str:
        return "no relevant memories"

    async def draft(*_a: Any, **_k: Any) -> str:
        return drafting.DRAFT_NO_DRAFT_SENTINEL

    with _patched((executor, "run_agent", run_agent),
                  (drafting, "_draft_consult_plan", plan),
                  (memory_tools, "remember", remember),
                  (drafting, "_llm_draft_reply", draft)):
        asyncio.run(drafting._orchestrate_draft(
            _email_dict(mail), "", "", "owner@company.example", account_id=_ACCOUNT))
    return cap


def _classifier(name: str, multi: bool) -> Callable[[Mail], Capture]:
    def run(mail: Mail) -> Capture:
        from gateway import decide_features
        from gateway.routes.email.automation import engine

        cap = Capture(name, fields=frozenset(
            {"from_name", "from", "subject", "body", "attachment"}))
        tools: list[Any] = []

        async def fake(model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
            cap.calls.append(_texts(messages))
            tools.append(kw.get("tools"))
            return ({"matches": []} if multi else {"index": -1}), "{}", model

        rules = [{"id": "r1", "name": "Needs reply", "instructions": "Mail that asks a question."},
                 {"id": "r2", "name": "Newsletter", "instructions": "Bulk mail."}]
        pick = engine._llm_pick_rules if multi else engine._llm_pick_rule
        with _patched((engine, "_llm_json", fake),
                      (decide_features, "mode_for", lambda _feature: "off")):
            asyncio.run(pick(_email_dict(mail), rules))
        cap.tools = frozenset() if not any(tools) else frozenset({"<tools>"})
        return cap
    return run


classifier_one = _classifier("classifier_one", multi=False)
classifier_many = _classifier("classifier_many", multi=True)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


def chat_context(mail: Mail) -> Capture:
    """``chat._build_chat_context``: the email open in the reader."""
    from gateway.routes.email.automation import chat

    row = SimpleNamespace(
        subject=mail.value("subject"), body_text=mail.value("body"),
        from_address={"name": mail.value("from_name"), "email": mail.value("from")},
        received_at="2026-10-14 09:00:00+00")

    class _Db:
        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            if "FROM email_accounts" in sql and "email_messages" not in sql:
                return _Result([SimpleNamespace(id=_ACCOUNT,
                                                email_address="owner@company.example")])
            if "em.body_text" in sql:
                return _Result([row])
            return _Result([])

    @contextlib.asynccontextmanager
    async def session() -> Any:
        yield _Db()

    with _patched((chat, "_tenant_session", session)):
        _account, parts = asyncio.run(chat._build_chat_context(
            "owner@company.example", None, _MAIL_ID, False))
    return _assistant_capture("chat_context", [["\n\n".join(parts)]],
                              {"from_name", "from", "subject", "body"})


#: Every path of the eval, by name. ``consult_run`` reads no text into a
#: prompt of ours. It proves the no-egress half of the consult.
PATHS: dict[str, Callable[[Mail], Capture]] = {
    "read_email": read_email,
    "read_email_full": read_email_full,
    "read_thread": read_thread,
    "drafter": drafter,
    "compose_assist": compose_assist,
    "consult_plan": consult_plan,
    "consult_run": consult_run,
    "classifier_one": classifier_one,
    "classifier_many": classifier_many,
    "chat_context": chat_context,
}
