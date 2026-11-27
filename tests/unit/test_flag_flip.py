"""Fence of scripts/flag_flip.py, the guarded flag flip (task 4086).

Hermetic. Each test runs the tool on a fake app dir, with fake ``curl``,
``sudo``, ``systemctl`` and ``journalctl`` first on PATH. No test touches a
real env file, a real unit or the network.
"""

from __future__ import annotations

import importlib.util
import os
import re
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
        '  *"restart acb-agent-edge"*) [ -f "$FAKE_DIR/environ.after" ] && mv "$FAKE_DIR/environ.after" "$FAKE_DIR/environ"; exit 0 ;;\n'
        '  *is-active*) echo active ;;\n'
        '  *MainPID*) echo 4242 ;;\n'
        "esac\nexit 0\n"
    ),
    # The cursor is the line count of the fake journal at the call.
    "journalctl": (
        "#!/bin/sh\n"
        'j="$FAKE_DIR/journal"; touch "$j"\n'
        'for a in "$@"; do case "$a" in\n'
        '  --show-cursor) echo "-- cursor: $(wc -l < "$j" | tr -d " ")"; exit 0 ;;\n'
        '  --after-cursor=*) n="${a#--after-cursor=}"; tail -n +"$((n + 1))" "$j"; exit 0 ;;\n'
        "esac; done\ncat \"$j\"\nexit 0\n"
    ),
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
    monkeypatch.setattr(ff, "LOCK_WAIT_S", 2.0)
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
    audit = (box.app.parent / "acb-flag-flips.log").read_text().split("\t")
    assert audit[1:5] == ["4086", "TOOL_PACKS_ORGS", ORG1, f"{ORG1},{ORG2}"]
    out = capsys.readouterr().out
    assert "OK TOOL_PACKS_ORGS is" in out and "proved at" in out and "cat " in out


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
    (box.fake / "environ.after").write_text(f"A=1\0AGENT_ACCESS_ORGS={ORG1}\0")
    assert flip("AGENT_ACCESS_ORGS", "--set", ORG1, "--task", "1", "--owner-approved", "not-a-uuid") == 2
    assert flip("AGENT_ACCESS_ORGS", "--set", ORG1, "--task", "1", "--owner-approved",
                "64887a7a-c190-44d1-a360-61e813b8f118", "--no-smoke") == 0
    # Closing an owner-only flag is the safe way, and needs no answer.
    box.reload_line("flags.reloaded applied=['AGENT_ACCESS_ORGS']")
    (box.fake / "environ.after").write_text("A=1\0AGENT_ACCESS_ORGS=\0")
    assert flip("AGENT_ACCESS_ORGS", "--off", "--task", "1", "--no-smoke") == 0


@pytest.mark.parametrize("value", ["not-an-org", f"{ORG1},*", f"{ORG1},{ORG1}", f"{ORG1};rm -rf /", f"{ORG1}\nX=1"])
def test_a_bad_value_is_refused(box, value):
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--set", value, "--task", "1") == 2
    assert box.root.read_bytes() == before


def test_a_modes_value_takes_name_mode_pairs_and_add_replaces_the_mode(box):
    # A feature name (DECIDE_FEATURE_MODES) and an org id (AGENT_CLAIM_SCOPE_MODES).
    box.root.write_text(box.root.read_text() + "DECIDE_FEATURE_MODES=email.cold_check=shadow\n"
                        + f"AGENT_CLAIM_SCOPE_MODES={ORG1}=warn\n")
    box.reload_line("flags.reloaded applied=['DECIDE_FEATURE_MODES']")
    assert flip("DECIDE_FEATURE_MODES", "--add", "email.cold_check=on", "--task", "1",
                "--owner-approved", ORG2, "--no-smoke") == 0
    assert "DECIDE_FEATURE_MODES=email.cold_check=on\n" in box.root.read_text()
    box.reload_line("flags.reloaded applied=['AGENT_CLAIM_SCOPE_MODES']")
    assert flip("AGENT_CLAIM_SCOPE_MODES", "--add", f"{ORG1}=refuse", "--task", "1", "--no-smoke") == 0
    assert f"AGENT_CLAIM_SCOPE_MODES={ORG1}=refuse\n" in box.root.read_text()
    assert flip("AGENT_CLAIM_SCOPE_MODES", "--add", ORG2, "--task", "1") == 2  # no mode


def test_a_gateway_with_no_reload_handler_is_refused_before_any_write(box):
    (box.fake / "version.json").write_text('{"flag_reload": false}')
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
    assert box.root.read_bytes() == before and "HUP" not in box.calls()


def test_no_reload_line_is_exit_3_with_the_rollback(box, capsys):
    box.reload_line("flags.reloaded applied=['CRM_ORGS']")  # names another key
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3
    err = capsys.readouterr()
    assert "FLIP NOT PROVED" in err.err and "cat " in err.out
    assert "\tnot proved\t" in (box.app.parent / "acb-flag-flips.log").read_text()


def test_a_refused_reload_is_exit_3(box):
    box.reload_line("flags.reload_refused error=invalid restart=[]")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3


def test_an_edge_held_key_changes_both_files_and_restarts_the_edge(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    for f in (box.root, box.app / EDGE):
        f.write_text(f.read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    (box.fake / "environ.after").write_text(f"X=1\0MCP_IMAGE_UPLOAD_ORGS={ORG1}\0")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n" in (box.app / EDGE).read_text()
    assert "restart acb-agent-edge" in box.calls()


def test_the_edge_env_must_hold_the_new_value_after_the_restart(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    for f in (box.root, box.app / EDGE):
        f.write_text(f.read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    (box.fake / "environ.after").write_text("X=1\0MCP_IMAGE_UPLOAD_ORGS=\0")  # the old value
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 3


def test_a_value_that_differs_between_files_is_refused(box):
    box.root.write_text(box.root.read_text() + f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG2, "--task", "1") == 2


def test_dry_run_writes_nothing_and_no_change_still_syncs_the_gateway(box):
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--dry-run") == 0
    assert box.root.read_bytes() == before and "HUP" not in box.calls()
    # No file changes. The gateway reload still runs, and a line that applies
    # nothing proves that the gateway already runs the file value.
    box.reload_line("flags.reloaded applied=[] restart=[]")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert box.root.read_bytes() == before and "HUP" in box.calls()
    assert not list(box.app.glob(".env.bak.*"))


def test_star_rules(box):
    box.root.write_text(box.root.read_text().replace(f"TOOL_PACKS_ORGS={ORG1}", "TOOL_PACKS_ORGS=*"))
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2  # already every org
    assert flip("TOOL_PACKS_ORGS", "--remove", ORG2, "--task", "1") == 2  # vague under *


def test_a_second_flip_waits_for_the_lock(box):
    import fcntl

    lock = box.app.parent / "acb-deploy.lock"
    lock.touch()
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ff.LOCK_WAIT_S = 0.0
    try:
        # The deploy lock: a release that holds it stops the flip.
        before = box.root.read_bytes()
        assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
        assert box.root.read_bytes() == before
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


def test_the_flip_keeps_the_inode_of_each_env_file(box):
    """The gateway binds the inode of the root .env (50-hardening.conf)."""
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    edge = box.app / EDGE
    inodes = (box.root.stat().st_ino, edge.stat().st_ino)
    (box.fake / "environ.after").write_text(f"X=1\0MCP_IMAGE_UPLOAD_ORGS={ORG1}\0")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert (box.root.stat().st_ino, edge.stat().st_ino) == inodes
    assert not list(box.app.rglob(".*.flagflip.*"))  # no temp copy is left


def test_an_edge_flag_goes_into_the_edge_file_even_when_it_is_absent(box):
    box.reload_line("flags.reloaded applied=['PLAYBOOK_ORGS']")
    (box.fake / "environ.after").write_text(f"X=1\0PLAYBOOK_ORGS={ORG1}\0")
    assert flip("PLAYBOOK_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert f"PLAYBOOK_ORGS={ORG1}\n" in (box.app / EDGE).read_text()
    assert f"PLAYBOOK_ORGS={ORG1}\n" in box.root.read_text()
    assert "restart acb-agent-edge" in box.calls()


def test_a_key_in_a_console_env_file_is_refused(box):
    cc = box.app / "apps/services/customer_console"
    cc.mkdir(parents=True)
    (cc / ".env").write_text(f"TOOL_PACKS_ORGS={ORG1}\n")
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
    assert box.root.read_bytes() == before


def test_edge_flags_holds_each_registry_flag_that_the_edge_reads():
    from acb_common import flag_registry as fr

    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "apps/services/agent_edge/agent_edge").rglob("*.py"))
    read = {n.upper() for n in fr.FlagRegistry.model_fields if re.search(rf"\b{n}\b", src)}
    assert read, "the scan found no flag: the edge path moved"
    assert read <= ff.EDGE_FLAGS, f"the edge reads {sorted(read - ff.EDGE_FLAGS)}: add them to EDGE_FLAGS"
    assert {n.lower() for n in ff.EDGE_FLAGS} <= set(fr.FlagRegistry.model_fields)


OWNER_MARK = re.compile(
    r"owner-only|owner only|only the owner|billed|credit|is the owner's|flip is the owner|does not cover"
    r"|real person|vendor money|spends? model|\bpays\b|H-166",
    re.IGNORECASE,
)


def test_each_flag_whose_comment_marks_it_owner_only_is_listed():
    """A comment that says the flip is the owner's puts its flag in OWNER_ONLY_FLAGS."""
    from acb_common import flag_registry as fr

    text = (REPO / "packages/acb_common/acb_common/flag_registry.py").read_text(encoding="utf-8")
    block: list[str] = []
    marked = set()
    for line in text.splitlines():
        if line.startswith("    #"):
            block.append(line)
            continue
        m = re.match(r"    ([a-z0-9_]+_(?:orgs|modes)): ", line)
        if m:
            if OWNER_MARK.search(" ".join(block)):
                marked.add(m.group(1))
            block = []
    assert len(marked) > 20, "the scan found too few: the comment form changed"
    missing = marked - fr.OWNER_ONLY_FLAGS
    assert not missing, f"add these to OWNER_ONLY_FLAGS: {sorted(missing)}"


def test_an_older_reloaded_line_never_proves_this_flip(box):
    # A line from an earlier flip sits in the journal before the cursor.
    (box.fake / "journal").write_text("flags.reloaded applied=['TOOL_PACKS_ORGS'] restart=[]\n")
    box.reload_line("flags.reloaded applied=[] restart=[]")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3


def test_a_refused_line_fails_even_after_a_reloaded_line(box):
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS'] restart=[]\nflags.reload_refused error=x")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3


def test_the_key_must_be_in_applied_not_in_restart(box):
    box.reload_line("flags.reloaded applied=[] restart=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 3


def test_set_fixes_a_value_that_differs_between_files(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    box.root.write_text(box.root.read_text() + f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    (box.fake / "environ.after").write_text(f"X=1\0MCP_IMAGE_UPLOAD_ORGS={ORG2}\0")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--set", ORG2, "--task", "1", "--no-smoke") == 0
    assert f"MCP_IMAGE_UPLOAD_ORGS={ORG2}\n" in (box.app / EDGE).read_text()


def test_a_key_only_in_the_edge_file_is_not_drift(box):
    box.reload_line("flags.reloaded applied=['PLAYBOOK_ORGS']")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    (box.fake / "environ.after").write_text(f"X=1\0PLAYBOOK_ORGS={ORG1},{ORG2}\0")
    assert flip("PLAYBOOK_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 0
    assert f"PLAYBOOK_ORGS={ORG1},{ORG2}\n" in box.root.read_text()


@pytest.mark.parametrize("line", [f"export TOOL_PACKS_ORGS={ORG1}", f"TOOL_PACKS_ORGS = {ORG1}",
                                  f'TOOL_PACKS_ORGS="{ORG1}"'])
def test_each_dotenv_form_is_read_and_replaced(box, line):
    box.root.write_text(box.root.read_text().replace(f"TOOL_PACKS_ORGS={ORG1}", line))
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 0
    text = box.root.read_text()
    assert f"TOOL_PACKS_ORGS={ORG1},{ORG2}\n" in text and text.count("TOOL_PACKS_ORGS") == 1


@pytest.mark.parametrize("arg", [f'"{ORG2}"', f"'{ORG2}"])
def test_add_stores_the_clean_item(box, arg):
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", arg, "--task", "1", "--no-smoke") == 0
    assert f"TOOL_PACKS_ORGS={ORG1},{ORG2}\n" in box.root.read_text()


def test_add_takes_one_item(box):
    assert flip("TOOL_PACKS_ORGS", "--add", f"{ORG1},{ORG2}", "--task", "1") == 2


def test_a_write_that_fails_midway_puts_each_file_back(box, monkeypatch):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    for f in (box.root, box.app / EDGE):
        f.write_text(f.read_text() + "MCP_IMAGE_UPLOAD_ORGS=\n")
    before = {f: f.read_bytes() for f in (box.root, box.app / EDGE)}
    real = ff.write_in_place
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(ff, "write_in_place", flaky)
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 3
    monkeypatch.setattr(ff, "write_in_place", real)
    assert {f: f.read_bytes() for f in before} == before
    assert "HUP" not in box.calls()
    assert "\twrite failed\t" in (box.app.parent / "acb-flag-flips.log").read_text()


def test_an_empty_means_all_flag_is_never_emptied(box):
    box.root.write_text(box.root.read_text() + f"ASSISTANT_PROFILE_ORGS={ORG1}\n")
    assert flip("ASSISTANT_PROFILE_ORGS", "--off", "--task", "1") == 2
    assert flip("ASSISTANT_PROFILE_ORGS", "--remove", ORG1, "--task", "1") == 2


def test_empty_means_all_matches_its_reader():
    """ASSISTANT_PROFILE_ORGS: empty is every org. A change of the reader must update the set."""
    src = (REPO / "packages/acb_common/acb_common/assistant_profile.py").read_text(encoding="utf-8")
    assert "return not orgs or org in orgs" in src
    assert set(ff.EMPTY_MEANS_ALL) == {"ASSISTANT_PROFILE_ORGS"}


def test_the_incident_case_an_edge_file_that_lacks_the_key_is_repaired(box):
    """2026-11-27: the root holds the value, the edge file does not."""
    box.root.write_text(box.root.read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    box.reload_line("flags.reloaded applied=[] restart=[]")  # the root did not change
    (box.fake / "environ").write_text("X=1\0")
    (box.fake / "environ.after").write_text(f"X=1\0PLAYBOOK_ORGS={ORG1}\0")
    assert flip("PLAYBOOK_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert f"PLAYBOOK_ORGS={ORG1}\n" in (box.app / EDGE).read_text()
    assert "restart acb-agent-edge" in box.calls()


def test_a_hand_edit_with_no_restart_is_made_live(box):
    box.root.write_text(box.root.read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    box.reload_line("flags.reloaded applied=[] restart=[]")
    (box.fake / "environ").write_text("X=1\0PLAYBOOK_ORGS=\0")  # the edge runs the old value
    (box.fake / "environ.after").write_text(f"X=1\0PLAYBOOK_ORGS={ORG1}\0")
    assert flip("PLAYBOOK_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert "restart acb-agent-edge" in box.calls()


def test_an_edge_that_runs_the_value_is_not_restarted(box):
    box.root.write_text(box.root.read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + f"PLAYBOOK_ORGS={ORG1}\n")
    box.reload_line("flags.reloaded applied=[] restart=[]")
    (box.fake / "environ").write_text(f"X=1\0PLAYBOOK_ORGS={ORG1}\0")
    assert flip("PLAYBOOK_ORGS", "--add", ORG1, "--task", "1", "--no-smoke") == 0
    assert "restart acb-agent-edge" not in box.calls()


def test_set_empty_makes_drifted_files_one(box):
    box.reload_line("flags.reloaded applied=['MCP_IMAGE_UPLOAD_ORGS']")
    box.root.write_text(box.root.read_text() + f"MCP_IMAGE_UPLOAD_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + f"MCP_IMAGE_UPLOAD_ORGS={ORG2}\n")
    (box.fake / "environ.after").write_text("X=1\0MCP_IMAGE_UPLOAD_ORGS=\0")
    assert flip("MCP_IMAGE_UPLOAD_ORGS", "--set", "", "--task", "1", "--no-smoke") == 0
    assert "MCP_IMAGE_UPLOAD_ORGS=\n" in box.root.read_text()
    assert "MCP_IMAGE_UPLOAD_ORGS=\n" in (box.app / EDGE).read_text()


def test_an_empty_means_all_flag_is_not_emptied_under_drift(box):
    box.root.write_text(box.root.read_text() + f"ASSISTANT_PROFILE_ORGS={ORG1}\n")
    (box.app / EDGE).write_text((box.app / EDGE).read_text() + f"ASSISTANT_PROFILE_ORGS={ORG2}\n")
    assert flip("ASSISTANT_PROFILE_ORGS", "--set", "", "--task", "1") == 2


def test_the_smoke_runs_after_the_deploy_lock_is_free(box, monkeypatch):
    import fcntl

    seen = []

    def smoke(app):
        fd = os.open(box.app.parent / "acb-deploy.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)  # smoke_chat.sh takes a shared hold
            seen.append("free")
        except BlockingIOError:
            seen.append("busy")
        finally:
            os.close(fd)
        return True

    monkeypatch.setattr(ff, "run_smoke", smoke)
    (box.app / "deploy").mkdir()
    (box.app / "deploy/smoke_chat.sh").write_text("exit 0\n")
    box.root.write_text(box.root.read_text() + "SMOKE_CHAT_TURN=1\n")
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 0
    assert seen == ["free"]


def test_a_key_line_inside_a_multi_line_value_is_kept(tmp_path):
    p = tmp_path / ".env"
    p.write_text('PEM="l1\nTOOL_PACKS_ORGS=x\nl3"\nTOOL_PACKS_ORGS=a\nB=2\n')
    assert ff.rewrite(p, "TOOL_PACKS_ORGS", "b").decode() == 'PEM="l1\nTOOL_PACKS_ORGS=x\nl3"\nTOOL_PACKS_ORGS=b\nB=2\n'


def test_a_failed_backup_is_refused_and_leaves_nothing(box, monkeypatch):
    def broken(path, stamp, task):
        raise OSError("disk full")

    monkeypatch.setattr(ff, "backup", broken)
    before = box.root.read_bytes()
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1") == 2
    assert box.root.read_bytes() == before and "HUP" not in box.calls()


def test_the_temp_copy_is_outside_the_checkout(box, monkeypatch):
    """The full copy holds secrets. A failed read-back keeps it, so it must not sit in the checkout."""
    dirs = []
    real = ff.tempfile.mkstemp

    def spy(*args, **kw):
        dirs.append(Path(kw["dir"]))
        return real(*args, **kw)

    monkeypatch.setattr(ff.tempfile, "mkstemp", spy)
    box.reload_line("flags.reloaded applied=['TOOL_PACKS_ORGS']")
    assert flip("TOOL_PACKS_ORGS", "--add", ORG2, "--task", "1", "--no-smoke") == 0
    assert dirs and all(d == box.app.parent for d in dirs)


def test_an_owner_only_flag_can_be_removed_with_no_answer(box):
    box.root.write_text(box.root.read_text() + f"CRM_DIGEST_ORGS={ORG1},{ORG2}\n")
    box.reload_line("flags.reloaded applied=['CRM_DIGEST_ORGS']")
    assert flip("CRM_DIGEST_ORGS", "--remove", ORG1, "--task", "1", "--no-smoke") == 0
    assert flip("CRM_DIGEST_ORGS", "--add", ORG1, "--task", "1") == 2
