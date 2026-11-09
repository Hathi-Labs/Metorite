"""WS43-F13, the Custom Apps half — the app routes after a sandbox (WS-43h).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.5 rules A and B, and the
fix round of task 2280 (review P0 and P2). With ``app_builder:<org>`` on, a
sandbox container writes an app dir. So a command can plant a link there:
to ``/proc/self/environ``, to a host secret, or into a sibling app's dir.
And with no ``.git`` at the start, it can plant a ``.git/config`` filter
that host git would run.

What it proves:

* every reader and writer of ``gateway/routes/apps/`` refuses a link: the
  sync and the fork read, the rehydrate write, the file routes, the
  manifest, the publish read and the host T2 build;
* the broker gives an app dir a covered ``.git`` before a container starts,
  and refuses a ``.git`` that is a link or a file;
* a link that a command made is gone after the command;
* host git refuses a repo whose config names a filter, and a planted filter
  never runs on the host.

Mutations this suite catches (R7), each run red once by hand:

* ``_eligible_text_files`` reads with ``Path.read_bytes``: the read tests;
* ``rehydrate_workspace`` writes with ``Path.write_text``: the rehydrate test;
* ``_git`` skips ``_git_repo_trusted``: the filter test;
* the broker skips ``ensure_app_git``: the ``.git`` cover test.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from tests.unit._maf_gateway_fakes import FakeGateway, gateway  # noqa: F401
from tests.unit._sandbox_broker_fakes import bound_run, mounts_of
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    Sandbox,
    new_thread,
    sandbox,
    short_tmp,
)

SECRET = "WS43H-HOST-SECRET-0000"


def _link(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("this box cannot make a symlink")


@pytest.fixture
def apps(tmp_path: Path) -> dict[str, Path]:
    """Two sibling apps under one root, and a host secret outside it.

    ``my-app`` holds three planted links: a file link to the host secret
    (``/proc/self/environ`` where it exists), a file link into the sibling
    app, and a dir link to the sibling app's ``src``.
    """
    root = tmp_path / "custom_apps"
    mine, victim = root / "my-app", root / "victim-app"
    for ws in (mine, victim):
        (ws / "src").mkdir(parents=True)
        (ws / "app.json").write_text('{"name": "x", "entry": "index.html"}\n', encoding="utf-8")
        (ws / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    (victim / "src" / "private.ts").write_text(f"// {SECRET} victim\n", encoding="utf-8")
    secret = tmp_path / "host.env"
    secret.write_text(f"KEY={SECRET}\n", encoding="utf-8")
    environ = Path("/proc/self/environ")
    _link(mine / "notes.txt", environ if environ.exists() else secret)
    _link(mine / "secret.txt", secret)
    _link(mine / "theirs.ts", victim / "src" / "private.ts")
    _link(mine / "shared", victim / "src", is_dir=True)
    return {"root": root, "mine": mine, "victim": victim, "secret": secret}


def _row(ws: Path) -> SimpleNamespace:
    return SimpleNamespace(id="app-1", slug=ws.name, workspace_path=str(ws), manifest={})


# ── the readers: sync, fork, manifest, publish ──────────────────────────────


def test_the_sync_and_fork_read_never_follows_a_link(apps: dict[str, Path]) -> None:
    from gateway.routes.apps.durability import _read_workspace_files, list_workspace_text_files

    files = _read_workspace_files(apps["mine"])
    assert set(files) == {"app.json", "index.html"}, sorted(files)
    assert SECRET not in json.dumps(files)
    listed = {p.name for p in list_workspace_text_files(apps["mine"])}
    assert listed == {"app.json", "index.html"}


def test_the_app_tree_never_lists_a_link(apps: dict[str, Path]) -> None:
    """The one walk of the app routes lists no link, and enters no linked dir."""
    from gateway.routes.apps._common import iter_app_files

    listed = {"/".join(p) for p in iter_app_files(apps["mine"], lambda _p, _d: True)}
    assert listed == {"app.json", "index.html"}, listed


def test_a_fork_copies_no_linked_file(apps: dict[str, Path]) -> None:
    from gateway.routes.apps.durability import _read_workspace_files
    from gateway.routes.apps.lifecycle import _fork_workspace

    new = apps["root"] / "forked"
    _fork_workspace(new, _read_workspace_files(apps["mine"]), {"name": "forked"})
    copied = {p.relative_to(new).as_posix() for p in new.rglob("*")
              if p.is_file() and ".git" not in p.parts}
    assert copied == {"app.json", "index.html"}, copied
    assert all(SECRET not in p.read_text(encoding="utf-8", errors="replace")
               for p in new.rglob("*") if p.is_file() and ".git" not in p.parts)


def test_a_linked_manifest_reads_as_none(apps: dict[str, Path]) -> None:
    from gateway.routes.apps._common import read_workspace_manifest

    ws = apps["mine"]
    (ws / "app.json").unlink()
    _link(ws / "app.json", apps["victim"] / "app.json")
    assert read_workspace_manifest(ws) is None


def test_a_linked_publish_entry_is_refused(apps: dict[str, Path]) -> None:
    from gateway.routes.apps.publish import _read_entry_bundle

    ws = apps["mine"]
    (ws / "index.html").unlink()
    _link(ws / "index.html", apps["secret"])
    with pytest.raises(HTTPException) as exc:
        _read_entry_bundle(_row(ws))
    assert exc.value.status_code == 422


# ── the writer: rehydrate ────────────────────────────────────────────────────


def test_a_rehydrate_never_writes_through_a_link(apps: dict[str, Path]) -> None:
    from gateway.routes.apps.durability import rehydrate_workspace

    before = (apps["victim"] / "src" / "private.ts").read_text(encoding="utf-8")
    rehydrate_workspace(_row(apps["mine"]), [
        ("shared/private.ts", "OVERWRITTEN"),
        ("shared/new.ts", "PLANTED"),
        ("theirs.ts", "OVERWRITTEN"),
        ("src/ok.ts", "fine"),
    ])
    assert (apps["victim"] / "src" / "private.ts").read_text(encoding="utf-8") == before
    assert not (apps["victim"] / "src" / "new.ts").exists()
    assert (apps["mine"] / "src" / "ok.ts").read_text(encoding="utf-8") == "fine"


# ── the file routes ─────────────────────────────────────────────────────────


@pytest.fixture
def routes(apps: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Any:
    from gateway.routes.apps import files

    async def edit(_slug: str, _user: Any) -> tuple[Any, Path]:
        return _row(apps["mine"]), apps["mine"]

    async def mirror(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(files, "_edit_workspace", edit)
    monkeypatch.setattr(files, "mirror_app_file", mirror)
    return files


@pytest.mark.parametrize("path", ["notes.txt", "secret.txt", "theirs.ts", "shared/private.ts"])
def test_the_content_route_refuses_a_link(routes: Any, path: str) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_app_file("my-app", path=path, user=None))
    assert exc.value.status_code in (400, 404), exc.value.detail


def test_the_write_route_never_writes_through_a_link(routes: Any, apps: dict[str, Path]) -> None:
    before = (apps["victim"] / "src" / "private.ts").read_text(encoding="utf-8")
    for path in ("shared/private.ts", "theirs.ts"):
        body = routes.AppFileWrite(path=path, content="OVERWRITTEN")
        with pytest.raises(HTTPException):
            asyncio.run(routes.write_app_file("my-app", body=body, user=None))
    assert (apps["victim"] / "src" / "private.ts").read_text(encoding="utf-8") == before


def test_the_list_route_shows_no_link(routes: Any) -> None:
    listed = {e.path for e in asyncio.run(routes.list_app_files("my-app", user=None))}
    assert listed == {"app.json", "index.html"}, listed


def test_the_host_build_refuses_a_dir_with_a_link(routes: Any, apps: dict[str, Path]) -> None:
    refusal = routes.host_build_refusal(apps["mine"])
    assert refusal and "link" in refusal
    assert routes.host_build_refusal(apps["victim"]) is None


# ── the broker: the .git cover and the link sweep ───────────────────────────


@pytest.fixture
def app_scope(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    sandbox.set_scope(monkeypatch, f"app_builder:{ORG_A}")
    ws = sandbox.env["apps"] / "fresh-app"
    ws.mkdir()
    (ws / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")
    return ws


def _command(ws: Path, command: str) -> str:
    from acb_skills.sandbox_tools import app_builder_run_command

    with bound_run(ORG_A, agent="app-builder", thread=new_thread(), workspace=str(ws),
                   instance=""):
        return asyncio.run(app_builder_run_command(command))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_an_app_dir_with_no_git_gets_a_covered_git_first(
    app_scope: Path, sandbox: Sandbox,  # noqa: F811
) -> None:
    assert not (app_scope / ".git").exists()
    out = _command(app_scope, "true")
    assert "exit 0" in out, out
    assert (app_scope / ".git").is_dir(), "no .git, so the cover covers nothing"
    mounts = mounts_of(sandbox.docker.runs()[0])
    assert any(m.endswith("target=/workspace/.git,readonly") for m in mounts), mounts


@pytest.mark.parametrize("kind", ["file", "link"])
def test_a_git_that_is_not_a_real_dir_is_refused(
    app_scope: Path, sandbox: Sandbox, tmp_path: Path, kind: str,  # noqa: F811
) -> None:
    if kind == "file":
        (app_scope / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    else:
        elsewhere = tmp_path / "elsewhere-git"
        elsewhere.mkdir()
        _link(app_scope / ".git", elsewhere, is_dir=True)
    out = _command(app_scope, "true")
    assert "Nothing ran on the host" in out and sandbox.docker.runs() == [], out


def test_a_link_that_a_command_made_is_gone_after_it(
    app_scope: Path, sandbox: Sandbox, tmp_path: Path,  # noqa: F811
) -> None:
    secret = tmp_path / "host.env"
    secret.write_text(SECRET, encoding="utf-8")
    (app_scope / ".git").mkdir()

    async def plant(_argv: list[str]) -> None:
        _link(app_scope / "notes.txt", secret)
        (app_scope / "deep").mkdir(exist_ok=True)
        _link(app_scope / "deep" / "up", app_scope.parent, is_dir=True)

    sandbox.docker.on_stream = plant
    out = _command(app_scope, "ln -s /proc/self/environ notes.txt")
    assert "exit 0" in out, out
    assert not os.path.lexists(app_scope / "notes.txt")
    assert not os.path.lexists(app_scope / "deep" / "up")
    assert (app_scope / "deep").is_dir() and secret.read_text(encoding="utf-8") == SECRET


# ── host git: a planted filter never runs (§7.5 rule A) ─────────────────────


def _plant_filter(ws: Path) -> Path:
    marker = ws.parent / f"{ws.name}-FILTER-RAN"
    script = ws.parent / f"{ws.name}-filter.sh"
    script.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\ncat\n", encoding="utf-8")
    script.chmod(0o755)
    with (ws / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f'[filter "evil"]\n\tclean = sh {script.as_posix()}\n')
    (ws / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    (ws / "index.html").write_text("<h1>changed</h1>\n", encoding="utf-8")
    return marker


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_planted_filter_never_runs_at_a_checkpoint(tmp_path: Path) -> None:
    from gateway.routes.apps.durability import git_checkpoint

    ws = tmp_path / "apps" / "planted"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")
    assert git_checkpoint(ws, "first") is not None
    marker = _plant_filter(ws)
    assert git_checkpoint(ws, "second") is None, "host git took a repo that names a filter"
    assert not marker.exists(), "host git ran a filter that the app dir planted"

    # The control: plain git in the same plant runs the filter, so the plant works.
    control = tmp_path / "apps" / "control"
    shutil.copytree(ws, control)
    marker = _plant_filter(control)
    subprocess.run(["git", "add", "-A"], cwd=control, capture_output=True, check=False)
    if sys.platform != "win32" or marker.exists():
        assert marker.exists(), "the plant does not trigger, so this test proves nothing"


def test_the_checkpoint_git_refuses_a_linked_git(tmp_path: Path) -> None:
    from gateway.routes.apps.durability import _git_repo_trusted

    real = tmp_path / "real"
    (real / ".git").mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    _link(ws / ".git", real / ".git", is_dir=True)
    assert _git_repo_trusted(ws) is False


# ── review round 2: nested repos, git files, the sweep on a raise ───────────


def _filter_script(where: Path, name: str) -> tuple[Path, Path]:
    marker = where / f"{name}-FILTER-RAN"
    script = where / f"{name}-filter.sh"
    script.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\ncat\n", encoding="utf-8")
    script.chmod(0o755)
    return marker, script


def _git_plain(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", *args],
                   cwd=cwd, capture_output=True, check=False)


def _plant_nested_repo(ws: Path) -> Path:
    """What the reviewer's container did: a repo in ``sub/`` with a filter."""
    marker, script = _filter_script(ws.parent, ws.name)
    sub = ws / "sub"
    sub.mkdir()
    _git_plain(sub, "init", "-q")
    with (sub / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f'[filter "evil"]\n\tclean = sh {script.as_posix()}\n')
    (sub / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    (sub / "x.txt").write_text("x\n", encoding="utf-8")
    _git_plain(sub, "add", "-A")
    _git_plain(sub, "commit", "-qm", "planted")
    (sub / "x.txt").write_text("changed\n", encoding="utf-8")
    if marker.exists():
        marker.unlink()  # the plant's own commit ran it, in the "container"
    return marker


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_nested_repo_never_runs_its_filter_on_the_host(tmp_path: Path) -> None:
    from gateway.routes.apps.durability import git_checkpoint, nested_git_entries

    ws = tmp_path / "apps" / "nested"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")
    assert git_checkpoint(ws, "first") is not None
    marker = _plant_nested_repo(ws)
    assert nested_git_entries(ws) == ["sub/.git"]
    assert git_checkpoint(ws, "second") is None, "host git took a work tree with a nested repo"
    assert not marker.exists(), "host git ran the filter of a nested repo"

    # The control: plain git on the same plant runs the nested filter.
    control = tmp_path / "apps" / "nested-control"
    control.mkdir()
    (control / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")
    _git_plain(control, "init", "-q")
    _git_plain(control, "add", "-A")
    _git_plain(control, "commit", "-qm", "first")
    marker = _plant_nested_repo(control)
    _git_plain(control, "add", "-A")
    _git_plain(control, "status", "--porcelain")
    if sys.platform != "win32" or marker.exists():
        assert marker.exists(), "the plant does not trigger, so this test proves nothing"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("planted", ["commondir", "objects/info/alternates", "info/attributes"])
def test_a_git_file_that_init_never_writes_refuses_the_repo(tmp_path: Path, planted: str) -> None:
    """Review round 2, P3: ``commondir`` can point git at a config with a
    filter, ``alternates`` at foreign objects, and ``info/attributes`` sets
    attributes. Host git refuses a repo that holds one."""
    from gateway.routes.apps.durability import git_checkpoint

    ws = tmp_path / "apps" / "gitfile"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")
    assert git_checkpoint(ws, "first") is not None
    marker, script = _filter_script(tmp_path, "commondir")
    evil = ws / "evilcommon"
    evil.mkdir()
    (evil / "config").write_text(
        f'[core]\n\trepositoryformatversion = 0\n[filter "evil"]\n\tclean = sh {script.as_posix()}\n',
        encoding="utf-8")
    target = ws / ".git" / planted
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("../evilcommon\n" if planted == "commondir" else "* filter=evil\n",
                      encoding="utf-8")
    (ws / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    (ws / "index.html").write_text("<h1>changed</h1>\n", encoding="utf-8")
    assert git_checkpoint(ws, "second") is None
    assert not marker.exists()


def test_a_nested_repo_that_a_command_made_is_gone_after_it(
    app_scope: Path, sandbox: Sandbox,  # noqa: F811
) -> None:
    (app_scope / ".git").mkdir()
    (app_scope / ".git" / "keep").write_text("host\n", encoding="utf-8")

    async def plant(_argv: list[str]) -> None:
        (app_scope / "sub" / ".git").mkdir(parents=True)
        (app_scope / "sub" / ".git" / "config").write_text('[filter "evil"]\n', encoding="utf-8")
        (app_scope / "deep" / "er").mkdir(parents=True)
        (app_scope / "deep" / "er" / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    sandbox.docker.on_stream = plant
    out = _command(app_scope, "git init sub")
    assert "exit 0" in out, out
    assert not os.path.lexists(app_scope / "sub" / ".git")
    assert not os.path.lexists(app_scope / "deep" / "er" / ".git")
    assert (app_scope / ".git" / "keep").read_text(encoding="utf-8") == "host\n"


@pytest.mark.parametrize("ending", ["sandbox_error", "cancel"])
def test_the_sweep_runs_when_the_command_raises(
    app_scope: Path, sandbox: Sandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    ending: str,
) -> None:
    """Review round 2, P3: a command that raises, or a stop, may have written
    a link before it ended. The sweep runs in a ``finally``."""
    from orchestrator import sandbox_broker as sb

    secret = tmp_path / "host.env"
    secret.write_text(SECRET, encoding="utf-8")
    (app_scope / ".git").mkdir()

    async def exec_then_raise(_handle: Any, _command: str, _timeout: float) -> Any:
        _link(app_scope / "notes.txt", secret)
        if ending == "cancel":
            raise asyncio.CancelledError()
        raise sb.SandboxUnavailable("The sandbox container died.")

    monkeypatch.setattr(sandbox.broker, "exec", exec_then_raise)
    if ending == "cancel":
        with pytest.raises(asyncio.CancelledError):
            _command(app_scope, "ln -s x notes.txt")
    else:
        out = _command(app_scope, "ln -s x notes.txt")
        assert "failed" in out and "Nothing ran on the host" in out, out
    assert not os.path.lexists(app_scope / "notes.txt"), "the sweep did not run"
    assert all(h.leases == 0 for h in sandbox.broker._live.values())


def test_a_dir_that_a_container_mounted_never_builds_on_the_host(
    app_scope: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Review round 2, option b: the host build refuses a sandbox dir for good,
    also after a flip off, and the text says how the app builds again."""
    from gateway.routes.apps.files import host_build_refusal

    (app_scope / ".git").mkdir()
    assert host_build_refusal(app_scope) is None
    assert "exit 0" in _command(app_scope, "true")
    sandbox.set_scope(monkeypatch, "")
    refusal = host_build_refusal(app_scope)
    assert refusal and "builds only in its sandbox" in refusal and "MAF_CODING_SCOPE" in refusal
