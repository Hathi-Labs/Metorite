"""Fence of scripts/flag_flip.py, the guarded flag flip (task 4086).

Hermetic. Each test runs the tool on a fake app dir, with fake ``curl``,
``sudo``, ``systemctl`` and ``journalctl`` first on PATH. No test touches a
real env file, a real unit or the network.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "flag_flip.py"
ORG1 = "11111111-1111-4111-8111-111111111111"
ORG2 = "22222222-2222-4222-8222-222222222222"
EDGE = "apps/services/agent_edge/.env"


def _load():
    spec = importlib.util.spec_from_file_location("flag_flip", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ff = _load()

FAKE = {
    # curl prints the /version body that the test chose.
    "curl": '#!/bin/sh\ncat "$FAKE_DIR/version.json"\n',
    # sudo runs the rest, and records it.
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$FAKE_DIR/calls"\nexec "$@"\n',
    "systemctl": (
        "#!/bin/sh\n"
        'echo "systemctl $*" >> "$FAKE_DIR/calls"\n'
        'case "$*" in\n'
        '  *"kill -s HUP"*) [ -f "$FAKE_DIR/hup_fails" ] && exit 1; '
        'cat "$FAKE_DIR/reload_line" >> "$FAKE_DIR/journal" 2>/dev/null; exit 0 ;;\n'
        '  *is-active*) echo active ;;\n'
        '  *MainPID*) echo 4242 ;;\n'
        "esac\nexit 0\n"
    ),
    "journalctl": '#!/bin/sh\ncat "$FAKE_DIR/journal" 2>/dev/null\nexit 0\n',
    # cat /proc/<pid>/environ reads the fake process env.
    "cat": (
        '#!/bin/sh\ncase "$1" in\n  /proc/*/environ) cat "$FAKE_DIR/environ" 2>/dev/null ;;\n'
        '  *) exec /bin/cat "$@" ;;\nesac\n'
    ),
}


@pytest.fixture
def box(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "apps/services/agent_edge").mkdir(parents=True)
    root = app / ".env"
    root.write_text(f"# head\nDATABASE_URL=x\nTOOL_PACKS_ORGS={ORG1}\nAGENT_ACCESS_ORGS=*\n", encoding="utf-8")
    os.chmod(root, 0o600)
    (app / EDGE).write_text("AGENT_EDGE_SIGNING_KEY=k\nAGENT_ACCESS_ORGS=*\n", encoding="utf-8")
    fake = tmp_path / "fake"
    bin_ = fake / "bin"
    bin_.mkdir(parents=True)
    for name, body in FAKE.items():
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    (fake / "version.json").write_text('{"flag_reload": true}')
    monkeypatch.setenv("FAKE_DIR", str(fake))
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FLAG_FLIP_RELOAD_WAIT_S", "2")
    monkeypatch.setattr(ff, "APP_DIR", app)
    monkeypatch.setattr(ff, "RELOAD_WAIT_S", 2.0)
    monkeypatch.setattr(ff.time, "sleep", lambda _s: None)

    class Box:
        pass

    b = Box()
    b.app, b.root, b.fake = app, root, fake

    def reload_line(text):
        (fake / "reload_line").write_text(text + "\n")

    b.reload_line = reload_line
    b.calls = lambda: (fake / "calls").read_text() if (fake / "calls").exists() else ""
    return b


def flip(*args):
    return ff.main(list(args))


def test_add_an_org_rewrites_one_line_and_keeps_every_other_byte(box, capsys):
    box.reload_line("\x1b[1mflags.reloaded\x1b[0m applied=['TOOL_PACKS_ORGS'] restart=[]")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "4086", "--no-smoke") == 0
    text = box.root.read_text(encoding="utf-8")
    assert text == f"# head\nDATABASE_URL=x\nTOOL_PACKS_ORGS={ORG1},{ORG2}\nAGENT_ACCESS_ORGS=*\n"
    assert oct(box.root.stat().st_mode & 0o777) == "0o600"
    assert "kill -s HUP --kill-whom=main acb-gateway" in box.calls()
    assert "restart acb-agent-edge" not in box.calls()  # the edge file does not hold the key
    baks = list(box.app.glob(".env.bak.*.flagflip-4086"))
    assert len(baks) == 1 and f"TOOL_PACKS_ORGS={ORG1}\n" in baks[0].read_text()
    audit = (box.app / ".flag_flips.log").read_text().split("\t")
    assert audit[1:5] == ["4086", "TOOL_PACKS_ORGS", ORG1, f"{ORG1},{ORG2}"]
    out = capsys.readouterr().out
    assert "OK TOOL_PACKS_ORGS flipped and proved" in out and "cp -p" in out


def test_a_name_outside_the_registry_is_refused_before_any_write(box):
    before = box.root.read_bytes()
    assert flip("AGENT_BOX_TOKENS_ENABLED", "--set", "true", "--task", "1") == 2
    assert flip("DATABASE_URL", "--off", "--task", "1") == 2
    # A per-org shape that is not a registry field: only the registry guard refuses it.
    assert flip("NOT_A_REGISTRY_ORGS", "--set", ORG1, "--task", "1") == 2
    assert box.root.read_bytes() == before and box.calls() == ""


def test_an_owner_only_flag_needs_the_owner_answer(box):
    before = box.root.read_bytes()
    assert flip("AGENT_ACCESS_ORGS", "--set", ORG1, "--task", "1") == 2
    assert box.root.read_bytes() == before
    box.reload_line("flags.reloaded applied=['AGENT_ACCESS_ORGS']")
    (box.fake / "environ").write_text(f"A=1\0AGENT_ACCESS_ORGS={ORG1}\0")
    assert flip("AGENT_ACCESS_ORGS", "--set", ORG1, "--task", "1", "--owner-approved",
                "64887a7a-c190-44d1-a360-61e813b8f118", "--no-smoke") == 0


@pytest.mark.parametrize("value", ["not-an-org", f"{ORG1},*", f"{ORG1},{ORG1}", f"{ORG1};rm -rf /", f"{ORG1}\nX=1"])
def test_a_bad_value_is_refused(box, value):
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--set", value, "--task", "1") == 2
    assert box.root.read_bytes() == before


def test_a_modes_value_takes_org_mode_pairs_and_add_replaces_the_mode(box):
    box.root.write_text(box.root.read_text() + f"DECIDE_FEATURE_MODES={ORG1}=warn\n")
    box.reload_line("flags.reloaded applied=['DECIDE_FEATURE_MODES']")
    assert flip("DECIDE_FEATURE_MODES", "--add", f"{ORG1}=refuse", "--task", "1", "--no-smoke") == 0
    assert f"DECIDE_FEATURE_MODES={ORG1}=refuse\n" in box.root.read_text()
    assert flip("DECIDE_FEATURE_MODES", "--add", ORG2, "--task", "1") == 2  # no mode


def test_a_gateway_with_no_reload_handler_is_refused_before_any_write(box):
    (box.fake / "version.json").write_text('{"flag_reload": false}')
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
    assert box.root.read_bytes() == before and "HUP" not in box.calls()


def test_no_reload_line_is_exit_3_with_the_rollback(box, capsys):
    box.reload_line("flags.reloaded applied=['CRM_ORGS']")  # names another key
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3
    err = capsys.readouterr()
    assert "FLIP NOT PROVED" in err.err and "cp -p" in err.out
    assert "\tnot proved\t" in (box.app / ".flag_flips.log").read_text()


def test_a_refused_reload_is_exit_3(box):
    box.reload_line("flags.reload_refused error=invalid restart=[]")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3


def test_an_edge_held_key_changes_both_files_and_restarts_the_edge(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    for f in (box.root, box.app / EDGE):
        f.write_text(f.read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    (box.fake / "environ").write_text(f"X=1\0MCP_IMAGE_UPLOAD_ORGS={ORG1}\0")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n" in (box.app / EDGE).read_text()
    assert "restart acb-agent-edge" in box.calls()


def test_the_edge_env_must_hold_the_new_value_after_the_restart(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    for f in (box.root, box.app / EDGE):
        f.write_text(f.read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    (box.fake / "environ").write_text("X=1\0MCP_IMAGE_UPLOAD_ORGS=\0")  # the old value
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 3


def test_a_value_that_differs_between_files_is_refused(box):
    box.root.write_text(box.root.read_text() + f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG2, "--task", "1") == 2


def test_dry_run_and_no_change_write_nothing(box, capsys):
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--dry-run") == 0
    assert flip("TOOL_PACKS_ORGS", "--add", ORG1, "--task", "1") == 0
    assert box.root.read_bytes() == before and "HUP" not in box.calls()
    assert not list(box.app.glob(".env.bak.*"))


def test_star_rules(box):
    box.root.write_text(box.root.read_text().replace(f"TOOL_PACKS_ORGS={ORG1}", "TOOL_PACKS_ORGS=*"))
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2  # already every org
    assert flip("TOOL_PACKS_ORGS", "--remove", ORG2, "--task", "1") == 2  # vague under *


def test_a_second_flip_waits_for_the_lock(box):
    import fcntl

    fd = os.open(box.app / ".flag_flip.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
    finally:
        os.close(fd)


def test_task_is_required_and_numeric(box):
    with pytest.raises(SystemExit):
        flip("TOOL_PACKS_ORGS", "--add", ORG2)
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "x;y") == 2


def test_duplicate_key_lines_become_one(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nK=old\nB=2\nK=older\n")
    assert ff.rewrite(p, "K", "new").decode() == "A=1\nK=new\nB=2\n"
    p.write_text("A=1")
    assert ff.rewrite(p, "K", "v").decode() == "A=1\nK=v\n"


def test_the_smoke_runs_only_when_it_is_on(box, monkeypatch):
    ran = []
    monkeypatch.setattr(ff, "run_smoke", lambda app: ran.append(app) or False)
    (box.app / "deploy").mkdir()
    (box.app / "deploy/smoke_chat.sh").write_text("exit 0\n")
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 0 and ran == []
    box.root.write_text(box.root.read_text() + "SMOKE_CHAT_TURN=1\n")
    assert flip("TOOL_PACKS_ORGS", "--remove", ORG2, "--task", "1") == 4 and len(ran) == 1


def test_each_owner_only_flag_is_a_registry_field():
    from acb_common import flag_registry as fr

    assert fr.OWNER_ONLY_FLAGS.issubset(fr.FlagRegistry.model_fields)


def test_the_wrapper_refuses_with_no_python(tmp_path):
    env = {**os.environ, "FLAG_FLIP_PYTHON": str(tmp_path / "none")}
    res = subprocess.run(["bash", str(REPO / "scripts/flag_flip.sh"), "X", "--off", "--task", "1"],
                         env=env, capture_output=True, text=True, timeout=30, check=False)
    assert res.returncode == 2 and "REFUSED" in res.stderr


def test_the_wrapper_runs_the_tool(tmp_path):
    env = {**os.environ, "FLAG_FLIP_PYTHON": sys.executable, "PYTHONPATH": str(REPO / "packages/acb_common")}
    res = subprocess.run(["bash", str(REPO / "scripts/flag_flip.sh"), "--help"],
                         env=env, capture_output=True, text=True, timeout=30, check=False)
    assert res.returncode == 0 and "--owner-approved" in res.stdout
