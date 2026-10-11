"""package_artifacts — one zip of this chat's files, as one download card.

Owner request, 2026-10-11: "If I have chatted with a particular AI assistant
and it's generated multiple files for me, I should be able to ask it to zip
them into one package and allow me to download."

The tool is ``acb_skills.package_artifacts.package_artifacts``. Its module
docstring holds the rules. This file is their fence (R7):

* **Scope.** No paths: the chat's own ``outputs/<thread slug>/`` in a shared
  agent's tenant dir, or the ``outputs/`` files that this session wrote in
  any other dir. Another thread's file, a loose file, ``..``, an absolute
  path, a drive, a link and a secret name are refused.
* **Zip safety.** No entry name can climb out (zip-slip), names are made
  unique, and the file count and the size are capped. Over a cap, nothing is
  written and no card shows.
* **Delivery.** The zip is a NEW file, written atomically, and shown through
  ``write_artifact.announce_artifact`` (the ``artifact_created`` card) and
  the blob mirror, the seams of every other file tool.
* **Channels and runs.** WhatsApp, a delegated run and a batch run get a
  plain refusal.
* **Injection.** In the core floor, for MAF and Copilot agents, in a
  ``no_egress`` run too, and not a shell or an egress tool.
* **The route (R8).** The gateway serves the zip as ``application/zip`` to
  its own session, and answers 404 to another session and another org.

Mutations this file catches (the PR carries the table):

* ``_named_rel`` drops ``_not_this_chats``: another chat's file goes in;
* ``_build_zip`` reads with plain ``open`` (no safe opener): a link goes in;
* a cap check is removed: an over-cap zip is written;
* ``package_artifacts`` drops ``announce_artifact``: no card.

Run::

    uv run pytest tests/unit/test_package_artifacts.py -v -rs
    # the route half needs the R8 ladder database:
    eval "$(bash scripts/dev_db.sh --export)"
"""
from __future__ import annotations

import asyncio
import importlib
import io
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tests.unit._sandbox_broker_fakes import bound_run, configure_env
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    PA,
    new_thread,
    short_tmp,
)

wa = importlib.import_module("acb_skills.write_artifact")
pa = importlib.import_module("acb_skills.package_artifacts")


# ═════════════════════════ the pure rules ═══════════════════════════════════


@pytest.mark.parametrize("parts", [
    ["..", "..", "etc", "passwd"],
    ["/abs", "x"],
    ["C:", "x.txt"],
    ["a\\..\\b"],
    [".."],
    ["a/../../b"],
    ["...", "x"],
    ["\x00evil", "x"],
    [""],
])
def test_no_entry_name_can_climb_out(parts: list[str]) -> None:
    name = pa.entry_name(parts)
    assert not name.startswith("/"), name
    assert "\\" not in name and ":" not in name and "\x00" not in name, name
    assert all(p not in ("", ".", "..") for p in name.split("/")), name


def test_an_entry_keeps_its_plain_path() -> None:
    assert pa.entry_name(["reports", "q3.md"]) == "reports/q3.md"


def test_the_zip_name_is_one_clean_base_name() -> None:
    assert pa.zip_name("") == "files.zip"
    assert pa.zip_name("../../evil") == "evil.zip"
    assert pa.zip_name("Q3 report.ZIP") == "Q3 report.zip"
    assert pa.zip_name("a/b\\c:d") == "c-d.zip"
    assert pa.zip_name(".env") == "env.zip"


def test_a_second_name_is_made_unique() -> None:
    seen: set[str] = set()
    assert [pa._unique(n, seen) for n in ("a.txt", "A.txt", "a.txt", "b/a.txt")] == [
        "a.txt", "A (1).txt", "a (2).txt", "b/a.txt",
    ]


def test_the_paths_are_a_json_list_or_one_per_line() -> None:
    assert pa._path_list("") == []
    assert pa._path_list('["outputs/a.md", "b.csv", "b.csv"]') == ["outputs/a.md", "b.csv"]
    assert pa._path_list("outputs/a.md\nb.csv\n") == ["outputs/a.md", "b.csv"]
    assert pa._path_list(["x"]) == ["x"]
    assert "JSON list" in pa._path_list('{"a": 1}')
    assert "JSON list" in pa._path_list("[not json")
    assert "JSON list" in pa._path_list("[1, 2]")
    assert "at most" in pa._path_list([f"f{i}" for i in range(pa.MAX_FILES + 1)])


def test_the_secret_rule_is_the_gateways_one_rule() -> None:
    """The tool leaves out what the file routes refuse: ONE rule."""
    from acb_skills import agent_paths
    from gateway.routes import workspace

    assert workspace._is_blocked_path is agent_paths.is_blocked_rel
    assert workspace._is_hidden_or_secret_file is agent_paths.is_hidden_or_secret_name
    for rel in (".env", "outputs/.env.local", "outputs/id_rsa", "outputs/x.pem",
                "outputs/.git/config", "outputs/node_modules/a.js",
                "outputs/zoho_token_cache.json", "outputs/my-credentials.txt"):
        assert agent_paths.is_blocked_rel(rel), rel
    assert not agent_paths.is_blocked_rel("outputs/report.md")


def test_the_caps_fit_the_download_route() -> None:
    from gateway.routes import workspace

    assert pa.MAX_FILES == 200
    assert pa.MAX_ZIP_BYTES <= workspace._MAX_FILE_BYTES
    assert pa.MAX_TOTAL_BYTES <= workspace._MAX_FILE_BYTES


@pytest.mark.parametrize("module", ["gateway.routes.workspace", "acb_skills.package_artifacts"])
def test_each_side_names_a_zip_application_zip_by_itself(module: str) -> None:
    """The route's download type and the card's type, each in a fresh process.

    A Windows registry maps .zip to application/x-zip-compressed, so each
    module fixes the type on import, and neither leans on the other.
    """
    import subprocess
    import sys

    code = f"import mimetypes, {module}; print(mimetypes.guess_type('files.zip')[0])"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "application/zip", out.stderr[-500:]


def test_publish_never_replaces_a_file_and_leaves_no_temp(tmp_path: Path) -> None:
    from acb_skills import safe_open

    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "a.zip").write_bytes(b"OLD")
    with pytest.raises(FileExistsError):
        safe_open.publish_bytes(tmp_path, "outputs/a.zip", b"NEW")
    assert (tmp_path / "outputs" / "a.zip").read_bytes() == b"OLD"
    safe_open.publish_bytes(tmp_path, "outputs/b.zip", b"NEW")
    assert (tmp_path / "outputs" / "b.zip").read_bytes() == b"NEW"
    assert sorted(p.name for p in (tmp_path / "outputs").iterdir()) == ["a.zip", "b.zip"]


# ═════════════════════════ the tool ═════════════════════════════════════════


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> dict[str, list]:  # noqa: F811
    """A scratch clone root. The blob mirror and the card are recorded."""
    configure_env(monkeypatch, short_tmp)
    seen: dict[str, list] = {"mirrored": [], "cards": []}

    async def mirror(rel: str, data: bytes, **kw: Any) -> None:
        seen["mirrored"].append((rel, kw.get("mime_type")))

    async def notify(*, artifact: dict, session_id: str, **_kw: Any) -> None:
        seen["cards"].append((artifact["path"], artifact["mime_type"], session_id))

    monkeypatch.setattr(wa, "mirror_to_blob_store", mirror)
    monkeypatch.setattr(wa, "_notify", notify)
    return seen


async def _pack(paths: Any = "", name: str = "") -> dict:
    out = await pa.package_artifacts(paths, name)
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)
    return out


def _put(ws: Path, rel: str, body: bytes | str = b"x") -> None:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)


def _entries(ws: Path, rel: str) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO((ws / rel).read_bytes())) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def _zips(ws: Path) -> list[str]:
    return sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*.zip"))


async def test_no_paths_zips_every_file_of_this_chat_and_only_those(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug, other = thread_slug(t), thread_slug(new_thread())
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        for rel, body in (
            (f"outputs/{slug}/report.md", "# Plan"),
            (f"outputs/{slug}/data/sheet.csv", "a,b"),
            (f"outputs/{slug}/.env", "SECRET=1"),
            (f"outputs/{slug}/old.zip", "PK"),
            (f"outputs/{other}/theirs.md", "ANOTHER CHAT"),
            ("outputs/loose.md", "A COLLEAGUE'S OLD DOC"),
            (f"inputs/{slug}/brief.txt", "AN UPLOAD"),
            ("agent-data/NOTES.md", "MEMORY"),
        ):
            _put(ws, rel, body)
        out = await _pack()
    assert "error" not in out, out
    assert out["path"] == f"outputs/{slug}/files.zip"
    assert out["mime_type"] == "application/zip" and out["files"] == 2
    assert out["download_url"] == f"/api/agent/workspace/{t}/file?path={out['path']}"
    assert _entries(ws, out["path"]) == {"report.md": b"# Plan", "data/sheet.csv": b"a,b"}
    assert out["left_out"] == [f"outputs/{slug}/.env"], "a left-out file is reported"
    # The card and the blob row: the seams of every other file tool.
    assert runs["cards"] == [(out["path"], "application/zip", t)]
    assert runs["mirrored"] == [(out["path"], "application/zip")]


async def test_a_second_package_is_a_new_file(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug = thread_slug(t)
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{slug}/a.md", "A")
        first = await _pack(name="bundle")
        second = await _pack(name="bundle")
    assert first["path"] == f"outputs/{slug}/bundle.zip"
    assert second["path"] == f"outputs/{slug}/bundle (1).zip"
    assert list(_entries(ws, second["path"])) == ["a.md"], "an earlier zip went in"
    assert not [p for p in ws.rglob("*.tmp")], "a temp file stayed"


@pytest.mark.parametrize(("path", "says"), [
    ("../outside.md", "'..'"),
    ("outputs/../../outside.md", "'..'"),
    ("/etc/passwd", "absolute"),
    ("C:/Windows/win.ini", "absolute"),
    (".env", "hidden"),
    ("outputs/.env", "hidden"),
    ("agent-data/NOTES.md", "only files in outputs/ or inputs/"),
    ("config.json", "File not found"),
    ("outputs/loose.md", "File not found"),
])
async def test_a_named_path_outside_this_chat_is_refused(runs, path: str, says: str) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{thread_slug(t)}/mine.md", "MINE")
        _put(ws, "outputs/loose.md", "LOOSE")
        _put(ws, "outputs/.env", "SECRET")
        _put(ws, "agent-data/NOTES.md", "MEMORY")
        _put(ws.parent, "outside.md", "OUTSIDE")
        out = await _pack([path])
    assert says in out.get("error", ""), out
    assert _zips(ws) == [] and runs["cards"] == []


async def test_another_threads_file_is_refused(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    other = thread_slug(new_thread())
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{thread_slug(t)}/mine.md", "MINE")
        _put(ws, f"outputs/{other}/theirs.md", "ANOTHER CHAT")
        _put(ws, f"inputs/{other}/brief.txt", "ANOTHER UPLOAD")
        theirs = await _pack([f"outputs/{other}/theirs.md"])
        upload = await _pack([f"inputs/{other}/brief.txt"])
        folder = await _pack([f"outputs/{other}"])
    for out in (theirs, upload, folder):
        assert "another chat" in out.get("error", ""), out
    assert _zips(ws) == [] and runs["cards"] == []


def _link(target: Path, link: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this box cannot make a symlink: {exc}")


async def test_a_link_is_refused(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug, other = thread_slug(t), thread_slug(new_thread())
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{slug}/mine.md", "MINE")
        _put(ws, f"outputs/{other}/theirs.md", "ANOTHER CHAT")
        _link(ws / "outputs" / other / "theirs.md", ws / "outputs" / slug / "planted.md")
        _link(ws / "outputs" / other, ws / "outputs" / slug / "planted-dir", is_dir=True)
        file_link = await _pack([f"outputs/{slug}/planted.md"])
        dir_link = await _pack([f"outputs/{slug}/planted-dir"])
        everything = await _pack()
    assert "error" in file_link and "error" in dir_link, (file_link, dir_link)
    # A folder walk never follows or lists a link.
    assert "error" not in everything, everything
    assert _entries(ws, everything["path"]) == {"mine.md": b"MINE"}


def test_each_layer_refuses_a_link_alone(tmp_path: Path) -> None:
    """Two layers refuse a link: the kind check never reports one, and the
    zip reads through the safe opener. Each holds without the other."""
    (tmp_path / "outputs").mkdir()
    (tmp_path / "secret.txt").write_text("OUTSIDE", encoding="utf-8")
    (tmp_path / "outputs" / "real.md").write_text("REAL", encoding="utf-8")
    _link(tmp_path / "secret.txt", tmp_path / "outputs" / "planted.md")
    _link(tmp_path, tmp_path / "outputs" / "planted-dir", is_dir=True)
    assert pa._kind_of(tmp_path, "outputs/real.md") == "file"
    assert pa._kind_of(tmp_path, "outputs/planted.md") is None
    assert pa._kind_of(tmp_path, "outputs/planted-dir") is None
    with pytest.raises(ValueError, match="link"):
        pa._build_zip(tmp_path, ["outputs/planted.md"])
    with pytest.raises(ValueError, match="link"):
        pa._build_zip(tmp_path, ["outputs/planted-dir/secret.txt"])


async def test_named_files_and_folders_go_in_with_unique_names(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    slug = thread_slug(t)
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{slug}/inputs/a.txt", "FROM OUTPUTS")
        _put(ws, f"inputs/{slug}/a.txt", "AN UPLOAD")
        _put(ws, f"outputs/{slug}/charts/one.png", b"\x89PNG one")
        out = await _pack('["outputs/inputs/a.txt", "inputs/a.txt", "outputs/charts"]', "pack")
    assert "error" not in out, out
    assert _entries(ws, out["path"]) == {
        "inputs/a.txt": b"FROM OUTPUTS",
        "inputs/a (1).txt": b"AN UPLOAD",
        "charts/one.png": b"\x89PNG one",
    }


async def test_over_the_file_cap_nothing_is_written(runs, monkeypatch) -> None:
    from acb_skills.agent_paths import thread_slug

    monkeypatch.setattr(pa, "MAX_FILES", 3)
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        for i in range(4):
            _put(ws, f"outputs/{thread_slug(t)}/f{i}.md", f"{i}")
        out = await _pack()
    assert "more than 3 files" in out.get("error", ""), out
    assert _zips(ws) == [] and runs["cards"] == [] and runs["mirrored"] == []


async def test_over_the_size_cap_nothing_is_written(runs, monkeypatch) -> None:
    from acb_skills.agent_paths import thread_slug

    monkeypatch.setattr(pa, "MAX_TOTAL_BYTES", 10)
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{thread_slug(t)}/a.md", "12345678")
        _put(ws, f"outputs/{thread_slug(t)}/b.md", "12345678")
        out = await _pack()
    assert "MB, so nothing was packaged" in out.get("error", ""), out
    assert _zips(ws) == [] and runs["cards"] == [] and runs["mirrored"] == []


async def test_over_the_zip_cap_nothing_is_written(runs, monkeypatch) -> None:
    from acb_skills.agent_paths import thread_slug

    monkeypatch.setattr(pa, "MAX_ZIP_BYTES", 50)
    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{thread_slug(t)}/a.png", os.urandom(200))
        out = await _pack()
    assert "nothing was packaged" in out.get("error", ""), out
    assert _zips(ws) == [] and runs["cards"] == []


async def test_a_chat_with_no_files_gets_a_plain_error(runs) -> None:
    with bound_run(ORG_A, agent=PA, thread=new_thread()) as ws:
        out = await _pack()
    assert out == {"error": "There are no files to package in this chat."}
    assert _zips(ws) == []


async def test_a_personal_dir_takes_the_files_this_session_wrote(runs, monkeypatch) -> None:
    import acb_memory
    from acb_skills.agent_paths import ensure_state_dir

    key = "u:alice@example.com"
    ws = ensure_state_dir(PA, key)
    t = new_thread()
    seen: dict[str, Any] = {}

    async def session_paths(session_id: str, *, instance: str, organization_id=None):
        seen["asked"] = (session_id, instance)
        return [(PA, "outputs/mine.md"), (PA, "inputs/upload.txt"),
                ("other-agent", "outputs/theirs.md"), (PA, "outputs/gone.md")]

    monkeypatch.setattr(acb_memory, "session_paths", session_paths)
    for rel in ("outputs/mine.md", "outputs/theirs.md", "outputs/earlier-chat.md",
                "inputs/upload.txt"):
        _put(ws, rel, rel)
    with bound_run(ORG_A, agent=PA, thread=t, instance=key, workspace=str(ws)):
        out = await _pack()
    assert seen["asked"] == (t, key), "the session and the key come from the run"
    assert "error" not in out, out
    assert out["path"] == "outputs/files.zip"
    assert _entries(ws, out["path"]) == {"mine.md": b"outputs/mine.md"}
    assert out["left_out"] == ["outputs/gone.md"]


async def test_a_personal_dir_with_no_store_asks_for_names(runs, monkeypatch) -> None:
    import acb_memory
    from acb_skills.agent_paths import ensure_state_dir

    async def down(*_a: Any, **_k: Any) -> list:
        raise RuntimeError("database down")

    monkeypatch.setattr(acb_memory, "session_paths", down)
    key = "u:alice@example.com"
    ws = ensure_state_dir(PA, key)
    _put(ws, "outputs/mine.md", "MINE")
    with bound_run(ORG_A, agent=PA, thread=new_thread(), instance=key, workspace=str(ws)):
        out = await _pack()
        named = await _pack(["outputs/mine.md"])
    assert "Name the files or folders" in out.get("error", ""), out
    assert "error" not in named and _entries(ws, named["path"]) == {"mine.md": b"MINE"}


async def test_whatsapp_gets_a_plain_refusal(runs) -> None:
    from acb_skills.agent_paths import thread_slug
    from acb_skills.whatsapp_ui import whatsapp_run

    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws, whatsapp_run(PA):
        _put(ws, f"outputs/{thread_slug(t)}/a.md", "A")
        out = await _pack()
    assert out == {"error": pa.WHATSAPP_ANSWER}
    assert "cannot send a zip file on WhatsApp" in pa.WHATSAPP_ANSWER
    assert _zips(ws) == [] and runs["cards"] == []


async def test_a_delegated_a_batch_and_an_unbound_run_package_nothing(runs) -> None:
    from acb_skills.agent_paths import thread_slug

    t = new_thread()
    with bound_run(ORG_A, agent=PA, thread=t) as ws:
        _put(ws, f"outputs/{thread_slug(t)}/a.md", "A")
        wa.derive_artifact_context(deliver_to={
            "workspace_root": str(ws), "session_id": t, "agent_name": PA, "instance": "",
        })
        delegated = await _pack()
    with bound_run(ORG_A, agent=PA, thread=t):
        wa.derive_artifact_context(batch_thread=True)
        batch = await _pack()
    unbound = await _pack()
    assert "another chat called" in delegated["error"]
    assert "no chat" in batch["error"]
    assert "No workspace" in unbound["error"]
    assert _zips(ws) == [] and runs["cards"] == []


# ═════════════════════════ the injection ════════════════════════════════════

import orchestrator._tool_injection as ti  # noqa: E402

from tests.unit.test_chat_upload_every_agent import (  # noqa: E402,F401 — fixture by name
    AGENTS,
    _built,
    _no_db,
    _prompt,
    _tool_names,
)


def test_it_is_a_floor_tool_with_no_shell_and_no_egress() -> None:
    from acb_skills.manifest import SHELL_TOOLS
    from acb_skills.skill_families import SKILL_FAMILIES
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    from acb_skills.whatsapp_ui import WITHHELD_TOOLS

    assert "package_artifacts" in ti._CORE_STANDARD_TOOL_NAMES
    assert "package_artifacts" in SKILL_FAMILIES["core"]["tools"]
    assert "package_artifacts" not in SHELL_TOOLS
    assert TOOL_ANNOTATIONS["package_artifacts"]["open_world"] is False
    # It stays in a WhatsApp run, and there it says plainly what it cannot do.
    assert "package_artifacts" not in WITHHELD_TOOLS
    names = {ti._tool_name(t) for t in ti._collect_injectable_platform_tools()}
    assert "package_artifacts" in names


def test_a_no_egress_run_keeps_it() -> None:
    agent = type("A", (), {})()
    agent.name = "fake"
    agent.default_options = {"tools": [], "instructions": ""}
    ti._inject_agent_tools(
        [agent], tool_scope=["web_search"], agent_name="fake",
        agent_config={}, no_egress=True,
    )
    assert "package_artifacts" in _tool_names(agent)


@pytest.mark.parametrize(("agent_dir", "agent_name", "runtime"), AGENTS)
def test_every_agent_holds_it_and_reads_when_to_use_it(
    agent_dir: str, agent_name: str, runtime: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _built(agent_dir, agent_name, monkeypatch)
    assert "package_artifacts" in _tool_names(agent)
    if runtime == "copilot":
        # The Copilot addendum carries the line (``addendum.py``).
        from agent_framework_github_copilot import GitHubCopilotAgent

        assert isinstance(agent, GitHubCopilotAgent), type(agent)
        prompt = _prompt(agent)
        assert "package_artifacts(paths?" in prompt, agent_name
        assert "zip, package or bundle" in prompt, agent_name
    else:
        # A native agent reads no addendum: the tool's own description is
        # what tells it when to call the tool.
        pool = agent._tools if isinstance(getattr(agent, "_tools", None), list) else (
            agent.default_options["tools"]
        )
        tool = next(t for t in pool if ti._tool_name(t) == "package_artifacts")
        text = str(getattr(tool, "description", "") or getattr(tool, "__doc__", ""))
        assert "zip, package or bundle" in text, agent_name


# ═════════════════════════ the route (R8) ═══════════════════════════════════

from tests.unit.test_chat_write_under_rls import (  # noqa: E402,F401 — fixtures by name
    _ALICE,
    _BOB,
    _CAROL,
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
    _S,
    _cfg,
    _client,
    disk,
)


def _run_package(disk: Any, org: str, sid: str) -> dict:  # noqa: F811
    """One run of the shared agent: write two documents, then package them."""
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator.executor import _resolve_run_workspace

    ws, key = _resolve_run_workspace(disk.shared, _cfg(None), organization_id=org)

    async def _go() -> dict:
        token = bind_tenant(org)
        wa.bind_artifact_context(
            session_id=sid, agent_name=_S, run_id=f"r-{uuid.uuid4().hex[:6]}",
            workspace_root=ws, instance=key,
            gateway_url="http://127.0.0.1:9", gateway_token="x",
        )
        try:
            await wa.write_artifact("plan.md", "THE PLAN")
            await wa.write_artifact("data.csv", "a,b")
            out = await pa.package_artifacts("", "handover")
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*rest, return_exceptions=True)
            return out
        finally:
            release_tenant(token)

    return asyncio.run(_go())


@_DB_GATE
def test_the_route_serves_the_zip_to_its_chat_only(graph_as_app, disk) -> None:  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sbob = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    sc = _seed_session(graph_as_app, b, _CAROL, None, agent=_S)
    alice, bob, carol = (_client(_user(e, o)) for e, o in ((_ALICE, a), (_BOB, a), (_CAROL, b)))

    out = _run_package(disk, a, sa)
    assert "error" not in out, out
    url = out["download_url"][len("/api"):]
    got = alice.get(url)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "application/zip"
    assert 'filename="handover.zip"' in got.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(got.content)) as zf:
        assert sorted(zf.namelist()) == ["data.csv", "plan.md"]
        assert zf.read("plan.md") == b"THE PLAN"

    # Bob: same org, his own session of the same agent. Carol: another org.
    assert bob.get(url).status_code == 404
    assert bob.get(f"/agent/workspace/{sbob}/file", params={"path": out["path"]}).status_code == 404
    assert carol.get(url).status_code == 404
    assert carol.get(f"/agent/workspace/{sc}/file", params={"path": out["path"]}).status_code == 404
