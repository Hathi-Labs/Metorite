"""A member's sandbox sees only that member's skills (WS-43v, §16.3).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3 (the skill rule and
"What the projects container sees").

What breaks this fence: a ``projects`` container mounts the tenant dir at
``/workspace`` read-only, so with no cover a ``run_command`` of member Y can
``cat`` member X's ``SKILL.md`` and scripts. The broker covers
``/workspace/agent-data/skills`` with an empty dir, and mounts on it only the
run member's own skill folders. A container started for another skill set,
or for another member, is not reused.

Two halves: the fake-Docker half (the mount list), and the ``sandbox_docker``
half on the coding image, which ``.github/workflows/sandbox-docker.yml`` runs
and which fails on a skip.

Mutations this suite catches (R7), each run red once by hand on 2026-10-05:

* ``projects_mounts`` drops the cover of ``agent-data/skills``: the mount
  test and the Docker test;
* ``_own_skill_mounts`` mounts every skill folder, and not only the member's
  own: the mount test and the Docker test;
* ``_same_mounts`` ignores the skill set: the reuse test;
* ``mount_list`` starts a ``projects`` container with no skill cover: the
  cover test;
* ``skill_cover`` lets an ``OSError`` through: the file-system error test;
* ``_build_skill_cover`` raises when another start renamed its cover first:
  the two-starts test.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import bound_run, mounts_of
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    PA,
    SKILL_SECRET,
    new_thread,
    sandbox,
    short_tmp,
)

_X, _Y = "x-skills@example.com", "y-skills@example.com"
_SKILL = "---\nname: {name}\ndescription: Draw a chart.\n---\nRun scripts/plot.py.\n"


def _make_skill(ws: Path, name: str, member: str, *, body: str = "print('{name} ran')\n") -> None:
    """A skill folder that *member* made, as the host writers make it."""
    from acb_skills.agent_paths import claim_skill

    claim_skill(ws, f"agent-data/skills/{name}/SKILL.md", member)
    folder = ws / "agent-data" / "skills" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(_SKILL.format(name=name), encoding="utf-8")
    (folder / "scripts" / "plot.py").write_text(body.format(name=name), encoding="utf-8")


def _skill_mounts(argv: list[str]) -> list[str]:
    return [m for m in mounts_of(argv) if f"target={sb.SKILLS_TARGET}" in m]


# ═════════════════════════ the fake-Docker half ═════════════════════════════


async def test_a_projects_start_covers_the_skills_and_mounts_only_the_members_own(
    sandbox,  # noqa: F811
) -> None:
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread, member=_X) as ws:
        _make_skill(ws, "xs", _X)
        _make_skill(ws, "ys", _Y)
        # A folder with an empty marker, and one with no marker: no one's.
        (ws / "agent-data/skills/empty").mkdir(parents=True)
        (ws / "agent-data/skills/empty/.metorite-author").write_text("")
        (ws / "agent-data/skills/bare").mkdir(parents=True)
        await sandbox.broker.acquire()
        b = sb.read_run_binding()
    got = _skill_mounts(sandbox.docker.runs()[0])
    cover = got[0].split("source=", 1)[1].split(",", 1)[0]
    assert got == [
        f"type=bind,source={cover},target={sb.SKILLS_TARGET},readonly",
        f"type=bind,source={b.workspace / 'agent-data/skills/xs'},"
        f"target={sb.SKILLS_TARGET}/xs,readonly",
    ]
    # The cover holds one empty dir for each own skill, and nothing else.
    assert sorted(os.listdir(cover)) == ["xs"]
    assert os.listdir(Path(cover) / "xs") == []
    assert not Path(cover).is_relative_to(b.workspace)


async def test_a_member_with_no_skill_gets_the_empty_cover(sandbox) -> None:  # noqa: F811
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread, member=_Y) as ws:
        _make_skill(ws, "xs", _X)
        await sandbox.broker.acquire()
    got = _skill_mounts(sandbox.docker.runs()[0])
    assert len(got) == 1 and got[0].endswith(f"target={sb.SKILLS_TARGET},readonly")
    cover = got[0].split("source=", 1)[1].split(",", 1)[0]
    assert os.listdir(cover) == []


async def test_a_new_skill_or_another_member_starts_a_fresh_container(sandbox) -> None:  # noqa: F811
    """The skill set is part of what a container is reused for."""
    broker = sandbox.broker
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread, member=_X) as ws:
        first = await broker.acquire()
        await broker.release(first)
        again = await broker.acquire()
        await broker.release(again)
        assert again is first and len(sandbox.docker.runs()) == 1
        _make_skill(ws, "xs", _X)
        fresh = await broker.acquire()
        await broker.release(fresh)
    assert fresh is not first and len(sandbox.docker.runs()) == 2
    assert any(m.endswith(f"target={sb.SKILLS_TARGET}/xs,readonly")
               for m in _skill_mounts(sandbox.docker.runs()[1]))
    # Member Y in the same thread does not get X's container, or X's skill.
    with bound_run(ORG_A, agent=PA, thread=thread, member=_Y):
        other = await broker.acquire()
        await broker.release(other)
    assert other is not fresh and len(sandbox.docker.runs()) == 3
    assert not any("/xs" in m for m in _skill_mounts(sandbox.docker.runs()[2]))


async def test_a_linked_skill_folder_is_never_mounted(sandbox, short_tmp) -> None:  # noqa: F811
    thread = new_thread()
    elsewhere = short_tmp / "host-secret"
    elsewhere.mkdir()
    with bound_run(ORG_A, agent=PA, thread=thread, member=_X) as ws:
        _make_skill(ws, "xs", _X)
        await sandbox.broker.acquire()
        b = sb.read_run_binding()
    # The folder becomes a link after the set was read: the start leaves it out.
    from dataclasses import replace

    linked = replace(b, member=_X, skills=("xs",))
    folder = ws / "agent-data" / "skills" / "xs"
    import shutil

    shutil.rmtree(folder)
    os.symlink(elsewhere, folder, target_is_directory=True)
    assert sb._own_skill_mounts(linked) == []


def test_a_projects_container_never_starts_with_no_skill_cover(sandbox) -> None:  # noqa: F811
    with bound_run(ORG_A, agent=PA, thread=new_thread(), member=_X):
        b = sb.read_run_binding()
        sb.prepare_projects_dirs(b)
    with pytest.raises(sb.SandboxRefused, match="cover of the skills"):
        sb.mount_list(b, sandbox.broker.git_cover())


def _cover_binding(broker: sb.SandboxBroker) -> sb.RunBinding:
    with bound_run(ORG_A, agent=PA, thread=new_thread(), member=_X):
        b = sb.read_run_binding()
    from dataclasses import replace

    return replace(b, skills=("xs", "ys"))


def test_two_starts_that_build_one_cover_at_once_both_get_it(
    sandbox, monkeypatch,  # noqa: F811
) -> None:
    """The other start renames its cover into place first. This start's
    rename then fails, and it takes the cover that is there."""
    broker = sandbox.broker
    b = _cover_binding(broker)
    real_rename = os.rename
    raced = {"done": False}

    def rename_after_the_other(src: Any, dst: Any) -> None:
        if not raced["done"]:
            raced["done"] = True
            monkeypatch.setattr(os, "rename", real_rename)
            broker._build_skill_cover(b)  # the other start, all the way
            monkeypatch.setattr(os, "rename", rename_after_the_other)
            if Path(dst).exists():
                raise FileExistsError(17, "File exists", str(dst))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename_after_the_other)
    cover = broker.skill_cover(b)
    assert raced["done"]
    assert sorted(os.listdir(cover)) == ["xs", "ys"]
    assert not [p for p in cover.parent.iterdir() if p.name.startswith(".tmp-")]


def test_a_file_system_error_in_the_cover_is_sandbox_unavailable(
    sandbox, monkeypatch,  # noqa: F811
) -> None:
    """A FileNotFoundError must not escape as a crash of the tool."""
    broker = sandbox.broker
    b = _cover_binding(broker)

    def vanished(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "rename", vanished)
    with pytest.raises(sb.SandboxUnavailable):
        broker.skill_cover(b)


# ═════════════════════════ the sandbox_docker half ══════════════════════════

from tests.unit.test_run_data_hygiene import (  # noqa: E402,F401 — fixtures by name
    _REAL_PROCESS_IDS,
    DOCKER_ORG,
    bind_mounts_work,
    coding_sandbox_image,
    docker_image,
    real_projects,
)


@pytest.mark.sandbox_docker
async def test_docker_a_member_never_reads_another_members_skill(
    real_projects, monkeypatch,  # noqa: F811
) -> None:
    """X and Y each made a skill in the one tenant dir of the org. Each one's
    container reads and runs its own skill, and cannot list, read or run the
    other's. A skill that X makes later shows in X's next command."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "gateway_session_secret", SKILL_SECRET, raising=False)
    broker = real_projects["broker"]
    tx, ty = new_thread(), new_thread()
    with bound_run(DOCKER_ORG, agent=PA, thread=tx, member=_X) as ws:
        _make_skill(ws, "xs", _X, body="print('X-SECRET-SKILL')\n")
        _make_skill(ws, "ys", _Y, body="print('Y-SECRET-SKILL')\n")
        hx = await broker.acquire()
    with bound_run(DOCKER_ORG, agent=PA, thread=ty, member=_Y):
        hy = await broker.acquire()

    listed = await broker.exec(hy, "ls -A /workspace/agent-data/skills", 30)
    assert listed.exit_code == 0 and listed.output.split() == ["ys"], listed.output
    for command in (
        "cat /workspace/agent-data/skills/xs/SKILL.md",
        "python3 /workspace/agent-data/skills/xs/scripts/plot.py",
        "cat /workspace/agent-data/skills/xs/.metorite-author",
        "grep -r X-SECRET-SKILL /workspace",
    ):
        got = await broker.exec(hy, command, 30)
        assert "X-SECRET-SKILL" not in got.output and "Draw a chart" not in got.output, (
            command, got.output,
        )
        assert got.exit_code != 0, (command, got.output)
    own = await broker.exec(hy, "python3 /workspace/agent-data/skills/ys/scripts/plot.py", 30)
    assert own.exit_code == 0 and "Y-SECRET-SKILL" in own.output, own.output

    mine = await broker.exec(hx, "cat /workspace/agent-data/skills/xs/SKILL.md", 30)
    assert mine.exit_code == 0 and "Draw a chart" in mine.output, mine.output
    ran = await broker.exec(hx, "python3 /workspace/agent-data/skills/xs/scripts/plot.py", 30)
    assert ran.exit_code == 0 and "X-SECRET-SKILL" in ran.output, ran.output
    assert (await broker.exec(hx, "cat /workspace/agent-data/skills/ys/SKILL.md", 30)).exit_code != 0
    # The own skill is read-only in the container, and so is the cover.
    for path in ("/workspace/agent-data/skills/xs/x", "/workspace/agent-data/skills/new"):
        assert (await broker.exec(hx, f"mkdir -p {path}", 30)).exit_code != 0, path

    # X makes another skill. X's next command sees it, in a fresh container.
    await broker.release(hx)
    with bound_run(DOCKER_ORG, agent=PA, thread=tx, member=_X) as ws:
        _make_skill(ws, "xs2", _X)
        hx2 = await broker.acquire()
    after = await broker.exec(hx2, "ls -A /workspace/agent-data/skills", 30)
    assert after.output.split() == ["xs", "xs2"], after.output
