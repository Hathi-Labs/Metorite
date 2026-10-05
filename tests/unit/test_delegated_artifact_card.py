"""A file that a delegated agent writes opens in the chat that asked for it.

The live bug (2026-10-05). In the Projects AI chat, projects-assistant called
email-assistant with ``call_agent``. email-assistant is a PERSONAL agent, so
its sub-run works in the ``u:<member>`` dir of the member. Its
``write_artifact`` wrote ``outputs/reports/welmont-school-project-brief.md``
into THAT dir. The card in the Projects chat linked to
``/agent/workspace/<projects chat session>/file?path=outputs/reports/…``,
and the session route serves that link from the CHAT's tenant dir and thread
folder. So Open, Download and PDF all answered 404.

The fix. The run boundary of a delegated run binds ``deliver_to``
(``write_artifact.delegation_target``): the working dir, the store key, the
agent and the session of the chat that asked. It comes from the parent's
bound context only, never from the model or a tool argument (R5).
``write_artifact`` and ``share_artifact`` of the delegated run put the
document in that chat's own thread folder (H-227), with the safe opener,
because a covered chat's sandbox container mounts that folder.

Three halves:

* the rules, with no database;
* the REAL executor: a covered projects-assistant chat calls email-assistant,
  and email-assistant calls ``write_artifact`` (the MAF path), and a
  Copilot-shaped sub-agent does the same (the Copilot path);
* the session route on the R8 database, as the NOBYPASSRLS app role.

Mutations this suite catches (R7), each run red by hand on this branch:

* ``write_artifact`` ignores ``deliver_to`` (the bug);
* ``share_artifact`` ignores ``deliver_to``;
* ``delegation_target`` passes on no inherited target (a grandchild, and
  every MAF sub-run, which runs under the Copilot-path derive);
* the executor's batch path binds no ``deliver_to``;
* the mirror keys the row by the sub-agent, not by the chat (the fault-in
  then restores nothing);
* the delivered write follows a link (plain ``Path.write_bytes``).

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_delegated_artifact_card.py -v -rs
"""
from __future__ import annotations

import asyncio
import importlib
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.unit._sandbox_broker_fakes import bound_run, configure_env
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    PA,
    new_thread,
    sandbox,
    short_tmp,
)

wa = importlib.import_module("acb_skills.write_artifact")

EMAIL = "email-assistant"
MEMBER = "member@example.com"
BRIEF = "# Welmont School project brief" + chr(10) + "Scope: the new lab." + chr(10)


# ═════════════════════════ the rules, no database ═══════════════════════════


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> dict[str, list]:  # noqa: F811
    """A scratch clone root, and the blob rows and the cards recorded."""
    configure_env(monkeypatch, short_tmp)
    seen: dict[str, list] = {"rows": [], "cards": []}

    async def mirror(rel: str, data: bytes, **kw: Any) -> None:
        target = kw.get("target")
        ctx = wa.artifact_context()
        seen["rows"].append((
            rel,
            (target or {}).get("agent_name") or ctx.get("agent_name"),
            (target or {}).get("instance") if target else ctx.get("instance"),
            (target or {}).get("session_id") if target else ctx.get("session_id"),
        ))

    async def notify(*, session_id: Any, workspace_root: str, artifact: dict, **_k: Any) -> None:
        seen["cards"].append((session_id, workspace_root, artifact["path"]))

    monkeypatch.setattr(wa, "mirror_to_blob_store", mirror)
    monkeypatch.setattr(wa, "_notify", notify)
    return seen


async def _settle() -> None:
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)


def _cards(monkeypatch) -> list[tuple[Any, str, str]]:
    seen: list[tuple[Any, str, str]] = []

    async def notify(*, session_id: Any, workspace_root: str, artifact: dict, **_k: Any) -> None:
        seen.append((session_id, workspace_root, artifact["path"]))

    monkeypatch.setattr(wa, "_notify", notify)
    return seen


@contextmanager
def _sub_run(*, session: str | None = None, agent: str = EMAIL) -> Iterator[Path]:
    """A delegated run of a PERSONAL agent, bound as ``_run_agent_inner``
    binds it: its own ``u:`` dir, a minted session (or *session*), and the
    target that ``delegation_target`` reads from the parent's frame."""
    from acb_skills.agent_paths import ensure_state_dir

    target = wa.delegation_target(wa.artifact_context())
    own = ensure_state_dir(agent, f"u:{MEMBER}")
    with wa.artifact_context_scope():
        wa.bind_artifact_context(
            session_id=session or f"{agent}:{uuid.uuid4()}", agent_name=agent,
            run_id=f"r-{uuid.uuid4().hex[:6]}", workspace_root=str(own),
            instance=f"u:{MEMBER}", member=MEMBER,
            **({"deliver_to": target} if target is not None else {}),
        )
        yield own


def test_the_target_is_the_chat_and_comes_from_the_parent_only(runs) -> None:
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        top = wa.delegation_target(wa.artifact_context())
        with _sub_run():
            # A grandchild delivers to the chat at the top, not to its parent.
            assert dict(wa.delegation_target(wa.artifact_context())) == dict(top)
    assert dict(top) == {
        "workspace_root": str(ws), "session_id": t, "agent_name": PA,
        "instance": f"o:{ORG_A}",
    }
    # A batch run (no chat) and a frame with no run are no target.
    assert wa.delegation_target({**top, "batch_thread": True}) is None
    assert wa.delegation_target({}) is None
    assert wa.delegation_target({"workspace_root": "/x", "agent_name": PA}) is None


async def test_a_delegated_document_lands_in_the_chats_thread_folder(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug = thread_slug(t)
    with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER) as ws, _sub_run() as own:
        out = await wa.write_artifact("reports/welmont-school-project-brief.md", BRIEF)
        again = await wa.write_artifact("reports/welmont-school-project-brief.md", "V2")
        await _settle()
    want = f"outputs/{slug}/reports/welmont-school-project-brief.md"
    assert out["path"] == want, out
    assert out["download_url"] == f"/api/agent/workspace/{t}/file?path={want}"
    assert (ws / want).read_text(encoding="utf-8") == BRIEF
    assert not (own / "outputs" / "reports").exists(), "the sub-agent's own dir again"
    # Never clobbered: the second write takes a new name, as in the chat itself.
    assert again["path"] == want.replace("brief.md", "brief (1).md"), again
    # The card and the blob row name the CHAT: its session, dir, agent and key.
    assert runs["cards"][0] == (t, str(ws), want)
    assert runs["rows"][0] == (want, PA, f"o:{ORG_A}", t)


async def test_the_chats_uploads_are_refused_and_agent_data_stays_home(runs) -> None:
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER) as ws, _sub_run() as own:
        upload = await wa.write_artifact("inputs/forged-upload.txt", "x")
        notes = await wa.write_artifact("agent-data/welmont.md", "a fact")
        escape = await wa.write_artifact("outputs/../../../x.md", "x")
        await _settle()
    assert "cannot write that chat's uploads" in upload.get("error", ""), upload
    assert "escapes" in escape.get("error", ""), escape
    # The sub-agent's own memory: its own dir, no link and no card.
    assert notes["path"] == "agent-data/welmont.md" and "download_url" not in notes
    assert (own / "agent-data" / "welmont.md").read_text(encoding="utf-8") == "a fact"
    assert runs["cards"] == []
    assert not [p for p in (ws / "inputs").rglob("*") if p.is_file()]


async def test_a_run_that_nobody_delegated_is_unchanged(runs) -> None:
    from acb_skills.agent_paths import ensure_state_dir

    key = f"u:{MEMBER}"
    own = ensure_state_dir(EMAIL, key)
    t = new_thread()
    with bound_run(ORG_A, agent=EMAIL, thread=t, instance=key, workspace=str(own)):
        assert wa.DELIVER_TO not in wa.artifact_context()
        out = await wa.write_artifact("reports/brief.md", BRIEF)
        await _settle()
    assert out["path"] == "outputs/reports/brief.md"
    assert (own / out["path"]).read_text(encoding="utf-8") == BRIEF
    assert runs["cards"] == [(t, str(own), out["path"])]
    assert runs["rows"] == [(out["path"], EMAIL, key, t)]


async def test_a_delegated_share_copies_the_file_into_the_chat(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug = thread_slug(t)
    with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER) as ws:
        # The chat already holds another file with the same name.
        theirs = ws / "outputs" / slug / "reports" / "summary.md"
        theirs.parent.mkdir(parents=True)
        theirs.write_text("THE CHAT'S OWN", encoding="utf-8")
        with _sub_run() as own:
            (own / "outputs" / "reports").mkdir(parents=True)
            (own / "outputs" / "reports" / "summary.md").write_text(BRIEF, encoding="utf-8")
            (own / "agent-data").mkdir(exist_ok=True)
            (own / "agent-data" / "rows.csv").write_text("a,b", encoding="utf-8")
            first = await wa.share_artifact("outputs/reports/summary.md")
            second = await wa.share_artifact("outputs/reports/summary.md")
            csv = await wa.share_artifact("agent-data/rows.csv")
            await _settle()
    copy = f"outputs/{slug}/reports/summary (1).md"
    assert [a["path"] for a in first["artifacts"]] == [copy], first
    assert first["download_url"] == f"/api/agent/workspace/{t}/file?path={copy}"
    assert theirs.read_text(encoding="utf-8") == "THE CHAT'S OWN"
    assert (ws / copy).read_text(encoding="utf-8") == BRIEF
    # The same bytes again: the same copy, never a third file.
    assert [a["path"] for a in second["artifacts"]] == [copy], second
    assert [a["path"] for a in csv["artifacts"]] == [f"outputs/{slug}/rows.csv"], csv
    assert [c[2] for c in runs["cards"]] == [copy, copy, f"outputs/{slug}/rows.csv"]
    assert [r[0] for r in runs["rows"]] == [copy, f"outputs/{slug}/rows.csv"]
    assert {(r[1], r[2], r[3]) for r in runs["rows"]} == {(PA, f"o:{ORG_A}", t)}


async def test_a_link_in_the_chats_folder_is_never_followed(runs) -> None:
    """A covered chat's container can plant a link in its own output folder.
    A delegated write through it fails, and writes nothing at its target."""
    import os

    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER) as ws:
        elsewhere = ws.parent / "elsewhere"
        elsewhere.mkdir()
        (ws / "outputs" / thread_slug(t)).mkdir(parents=True)
        try:
            os.symlink(elsewhere, ws / "outputs" / thread_slug(t) / "reports",
                       target_is_directory=True)
        except OSError as exc:  # pragma: no cover - CI is Linux
            pytest.skip(f"no symlink here: {exc}")
        with _sub_run():
            out = await wa.write_artifact("reports/brief.md", BRIEF)
            await _settle()
    assert "error" in out, out
    assert not list(elsewhere.iterdir()), "the write followed the link"
    assert runs["cards"] == [] and runs["rows"] == []


async def test_the_chats_sandbox_sees_the_document_at_workspace_outputs(
    sandbox, monkeypatch,  # noqa: F811
) -> None:
    """The covered chat's container mounts ``outputs/<thread slug>/`` at
    ``/workspace/outputs``. So the chat's next ``run_command`` finds the
    delegated document at ``/workspace/outputs/reports/…``."""
    from orchestrator import sandbox_broker as sb

    _cards(monkeypatch)
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER):
        with _sub_run():
            out = await wa.write_artifact("reports/brief.md", BRIEF)
            await _settle()
        binding = sb.read_run_binding()
        sb.prepare_projects_dirs(binding)
        cover = binding.workspace.parent / "cover"
        cover.mkdir(exist_ok=True)
        mounts = {m.target: m for m in sb.projects_mounts(binding, cover)}
    outputs = mounts[sb.OUTPUTS_TARGET]
    assert sb.OUTPUTS_TARGET == "/workspace/outputs" and not outputs.readonly
    assert (outputs.source / "reports" / "brief.md").read_text(encoding="utf-8") == BRIEF
    assert out["path"].endswith("/reports/brief.md")


# ═════════════════════════ through the REAL executor ════════════════════════

from tests.unit.test_delegation_no_egress import (  # noqa: E402
    _call,
    _harness,
    _Model,
    _register_every_annotation,
    _run_top,
    _say,
)


@pytest.fixture
def no_db(monkeypatch):
    """The registry block and the skill toggles read the database. Stub both,
    as the H-236 suite does, so the executor tests open no connection."""
    import gateway.routes.agent as routes_agent
    from orchestrator import _tool_injection as ti

    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    ti._build_registry_block.cache_clear()
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_registry_block.cache_clear()
    ti._build_injected_tools_addendum.cache_clear()


def test_a_projects_chat_calls_email_assistant_and_the_card_is_the_chats(
    sandbox, monkeypatch, no_db,  # noqa: F811
) -> None:
    """The live bug, end to end on the MAF path: the REAL executor, the real
    factories of both agents, only the model wire scripted."""
    from acb_skills.agent_paths import agent_state_dir, tenant_instance, thread_slug

    _register_every_annotation()
    parent = _Model([
        _call("call_agent", {"agent_name": EMAIL, "message": "Brief from the Welmont mails"}),
        _say("Here is the brief."),
    ])
    email = _Model([
        _call("write_artifact", {
            "path": "reports/welmont-school-project-brief.md", "content": BRIEF,
        }),
        _say("I wrote the brief."),
    ])
    _harness(monkeypatch, sandbox, {PA: parent, EMAIL: email})
    cards = _cards(monkeypatch)
    threads: list[str] = []
    real_stream = importlib.import_module("orchestrator.executor").run_agent_stream

    def spy(*a: Any, **k: Any) -> Any:
        threads.append(k["thread_id"])
        return real_stream(*a, **k)

    monkeypatch.setattr(importlib.import_module("orchestrator.executor"), "run_agent_stream", spy)
    _run_top(PA, ORG_A)
    (t,) = threads
    ws = agent_state_dir(PA, tenant_instance(ORG_A))
    want = f"outputs/{thread_slug(t)}/reports/welmont-school-project-brief.md"
    results = [m for b in email.bodies[1:] for m in b.get("messages", []) if m.get("role") == "tool"]
    assert (ws / want).read_text(encoding="utf-8") == BRIEF, results
    assert cards == [(t, str(ws), want)], cards
    own = agent_state_dir(EMAIL, f"u:{MEMBER}")
    assert not (own / "outputs" / "reports").exists(), "the sub-agent's own dir again"
    assert any(f"/api/agent/workspace/{t}/file?path={want}" in str(m.get("content"))
               for m in results), results


class _CopilotWriter:
    """Copilot-SDK shaped. Its turn calls ``write_artifact`` in the run."""

    def __init__(self) -> None:
        self.name = "copilot-writer"
        self._tools: list[Any] = []
        self.tools: list[Any] = []
        self._default_options: dict[str, Any] = {"model": "tier-balanced"}
        self._permission_handler: Any = None
        self.out: dict[str, Any] = {}

    def _prepare_tools(self, tools: Any) -> list[Any]:
        return list(tools or [])

    def run(self, *_a: Any, **_k: Any) -> Any:
        return self._turn()

    async def _turn(self) -> Any:
        self.out = await wa.write_artifact("reports/brief.md", BRIEF)
        yield types.SimpleNamespace(
            role="assistant", message_id="m1",
            contents=[types.SimpleNamespace(type="text", text="done")],
        )

    async def __aenter__(self) -> _CopilotWriter:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def test_a_copilot_sub_agent_delivers_to_the_chat_too(sandbox, monkeypatch, no_db) -> None:  # noqa: F811
    import gateway.routes.agent as routes_agent
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.agent_paths import thread_slug
    from orchestrator import executor

    agent = _CopilotWriter()
    cfg = {"name": "copilot-writer", "sharing": {"instancing": "personal"}}
    clone = sandbox.env["clone"] / "repos" / "copilot-writer"
    clone.mkdir(parents=True, exist_ok=True)

    class _Ctx:
        def __enter__(self) -> Any:
            return types.SimpleNamespace(agent_dir=clone, agent_name="copilot-writer",
                                         config=cfg, build_agents=lambda: [agent])

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *_a, **_k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [{
        "name": "copilot-writer", "agent_runtime": "github-copilot",
        "repo_name": None, "local_path": None,
    }])
    cards = _cards(monkeypatch)
    t = new_thread()
    token = bind_tenant(ORG_A)
    try:
        with bound_run(ORG_A, agent=PA, thread=t, member=MEMBER) as ws:
            asyncio.run(executor._run_sub_agent_streaming(
                "copilot-writer", "write the brief", "r-sub", event_queue=asyncio.Queue(),
            ))
            after = dict(wa.artifact_context())
    finally:
        release_tenant(token)
    want = f"outputs/{thread_slug(t)}/reports/brief.md"
    assert agent.out.get("path") == want, agent.out
    assert (ws / want).read_text(encoding="utf-8") == BRIEF
    assert cards == [(t, str(ws), want)], cards
    assert wa.DELIVER_TO not in after, "the parent's own context changed"


# ═════════════════════════ the session route, on the R8 database ════════════

from tests.unit.test_chat_write_under_rls import (  # noqa: E402,F401 — fixtures by name
    _ALICE,
    _BOB,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402,F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session  # noqa: E402
from tests.unit.test_h201_tenant_workdirs import (  # noqa: E402,F401 — fixtures by name
    _P,
    _S,
    _cfg,
    _client,
    _tenant_dir,
    disk,
)


def _delegated_write(disk_: Any, org: str, sid: str, member: str, rel: str, body: str,
                     *, delegated: bool = True) -> dict:
    """A chat run of the shared agent ``_S`` for *member*, which delegates to
    the PERSONAL agent ``_P``, and ``_P`` calls ``write_artifact``. Bound as
    the executor binds both, with the real blob write-through."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.agent_paths import ensure_state_dir
    from orchestrator.executor import _resolve_run_workspace

    ws, key = _resolve_run_workspace(disk_.shared, _cfg(None), organization_id=org)
    own = ensure_state_dir(_P, f"u:{member}")

    async def _go() -> dict:
        token = bind_tenant(org)
        wa.bind_artifact_context(
            session_id=sid, agent_name=_S, run_id=f"r-{uuid.uuid4().hex[:6]}",
            workspace_root=ws, instance=key, member=member,
            gateway_url="http://127.0.0.1:9", gateway_token="x",
        )
        target = wa.delegation_target(wa.artifact_context()) if delegated else None
        try:
            with wa.artifact_context_scope():
                wa.bind_artifact_context(
                    session_id=sid if not delegated else f"{_P}:{uuid.uuid4()}",
                    agent_name=_P, run_id=f"r-{uuid.uuid4().hex[:6]}",
                    workspace_root=str(own), instance=f"u:{member}", member=member,
                    gateway_url="http://127.0.0.1:9", gateway_token="x",
                    **({"deliver_to": target} if target is not None else {}),
                )
                out = await wa.write_artifact(rel, body)
                await _settle()
                return out
        finally:
            release_tenant(token)

    return asyncio.run(_go())


@_DB_GATE
def test_the_card_opens_for_the_member_and_for_nobody_else(graph_as_app, disk) -> None:  # noqa: F811
    a = graph_as_app.org_a
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    alice, bob = _client(_user(_ALICE, a)), _client(_user(_BOB, a))
    assert alice.get(f"/agent/workspace/{sa}").status_code == 200  # makes the tenant dir

    # Short: a Windows dev box stops at 260 characters, and the tenant dir and
    # the thread slug already take most of them. Production is Linux.
    name = f"b-{uuid.uuid4().hex[:4]}.md"
    res = _delegated_write(disk, a, sa, _ALICE, f"reports/{name}", BRIEF)
    link = res["download_url"][len("/api"):]
    assert link.startswith(f"/agent/workspace/{sa}/file?path=outputs/"), res

    # Open, Download and PDF: the card's own link, for the member who asked.
    opened = alice.get(link)
    assert opened.status_code == 200 and opened.text == BRIEF, opened.text
    pdf = alice.get(f"{link}&format=pdf")  # the PDF form of the card
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-"
    assert res["path"] in {f["path"] for f in alice.get(f"/agent/workspace/{sa}").json()["files"]}

    # Another member of the org: not in Alice's room, and not in his own chat.
    assert bob.get(link).status_code == 404
    assert bob.get(f"{link}&format=pdf").status_code == 404
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": res["path"]}).status_code == 404

    # The blob row is the chat's, so the fault-in restores it for Alice only.
    (_tenant_dir(a) / res["path"]).unlink()
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": res["path"]}).status_code == 404
    assert alice.get(link).text == BRIEF


@_DB_GATE
def test_without_the_target_the_card_is_the_bug(graph_as_app, disk) -> None:  # noqa: F811
    """The control: the same write with no ``deliver_to`` is the 2026-10-05
    bug. The file lands in the personal dir, and the chat's link is a 404.
    This proves the route test above tests the target, and not luck."""
    a = graph_as_app.org_a
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    alice = _client(_user(_ALICE, a))
    assert alice.get(f"/agent/workspace/{sa}").status_code == 200
    res = _delegated_write(disk, a, sa, _ALICE, "reports/brief.md", BRIEF, delegated=False)
    assert res["path"] == "outputs/reports/brief.md", res
    assert alice.get(res["download_url"][len("/api"):]).status_code == 404
