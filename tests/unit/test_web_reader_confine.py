"""WEB-2b: the confinement of the web reader child (``web_reader_worker.py``).

Spec: ``project-docs/specs/web_research.md`` section 3.2, slice WEB-2b. The
child parses a hostile page as the gateway's user. A bug in a parser could
read ``/opt/acb/app/.env`` or open a socket. These are the fences, each one a
test class. The Linux tests prove a rule on a real kernel. A test with the
mark ``linux_only`` skips on Windows. The box and CI run Linux.

1. **The shared module** (:class:`TestTheSharedModule`). The OCR child and the
   web child load ONE ``child_confine.py`` by path. Neither writes a second
   copy of Landlock or of the seccomp filter.
2. **The order of main** (:class:`TestTheOrder`). Close the descriptors, limit,
   confine, preload, close the limits, and only then read the page.
3. **The confinement** (:class:`TestTheConfinement`, Linux). A child that runs
   the real ``main`` cannot read a ``.env``, write a file, open a socket, use a
   socket that it inherited, signal its parent or fork. A control child with
   no confinement can do each. A kernel with no Landlock fails closed, and so
   does a child that has a second thread.
4. **The descriptors** (:class:`TestTheDescriptors`, Linux). A parent on
   uvloop leaks an inheritable descriptor to a plain child. The real worker
   shuts it before anything else.
5. **The group** (:class:`TestTheGroup`, POSIX). The parent kills the process
   group at the end of each read, also when the leader exited 0.
6. **The fallback and the page** (:class:`TestTheFallback`). A fault
   ``unavailable`` gives the strip. A normal page still reads.

Run::

    uv run pytest tests/unit/test_web_reader_confine.py -q -p no:cacheprovider
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from acb_skills import web_tools

from tests.unit.test_web_readers import (
    ORG,
    URL,
    _fake_child,
    _fixture,
    _on,
    _record_log,
    _serve,
)

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "packages" / "acb_skills" / "acb_skills"
WORKER = PKG / "web_reader_worker.py"
OCR_WORKER = PKG / "attachment_ocr_worker.py"
SHARED = PKG / "child_confine.py"

linux_only = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Landlock, seccomp and /proc exist on Linux only; the box and CI run Linux",
)
posix_only = pytest.mark.skipif(
    os.name != "posix", reason="process groups exist on POSIX only; the box and CI run Linux",
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CC = _load(SHARED, "web2b_child_confine_under_test")
LANDLOCK_ABI = _CC.landlock_abi()
#: A Linux host with no Landlock below the web floor: the real child answers
#: ``unavailable`` there, so a test that needs a page to read skips. The CI
#: guard at the end of this file fails when CI would skip them.
needs_a_confinable_host = pytest.mark.skipif(
    sys.platform == "linux" and LANDLOCK_ABI < 1,
    reason="this Linux kernel has no Landlock, so the real child fails closed here",
)


# ── 1. The shared module ────────────────────────────────────────────────────


class TestTheSharedModule:
    def test_both_workers_load_the_one_file(self) -> None:
        web, ocr = _load(WORKER, "web2b_w"), _load(OCR_WORKER, "web2b_o")
        assert Path(web._cc.__file__).resolve() == SHARED.resolve()
        assert Path(ocr._cc.__file__).resolve() == SHARED.resolve()
        assert ocr._Refused is ocr._cc.Refused
        assert ocr.seccomp_program is ocr._cc.seccomp_program

    def test_no_worker_holds_a_second_copy_of_the_filter(self) -> None:
        for worker in (WORKER, OCR_WORKER):
            names = {n.name for n in ast.walk(ast.parse(worker.read_text(encoding="utf-8")))
                     if isinstance(n, ast.FunctionDef)}
            assert not names & {"seccomp_program", "_install_seccomp", "landlock_abi",
                                "_read_dirs", "thread_count"}, (worker.name, names)

    def test_the_shared_module_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(SHARED.read_text(encoding="utf-8"))
        modules = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom)
                   else a.name.split(".")[0]
                   for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)
                   for a in (n.names if isinstance(n, ast.Import) else [None])}
        assert {m for m in modules if m not in sys.stdlib_module_names} == set(), modules

    def test_a_worker_writes_no_bytecode_for_the_shared_module(self, tmp_path) -> None:
        """``-I`` leaves bytecode on, and a ``__pycache__`` in the package dir
        would be a file that the child wrote. The load switches it off."""
        for worker in (WORKER, OCR_WORKER):
            copy = tmp_path / worker.stem
            copy.mkdir()
            for src in (worker, SHARED):
                (copy / src.name).write_bytes(src.read_bytes())
            done = subprocess.run(
                [sys.executable, "-I", "-c",
                 "import importlib.util, sys\n"
                 "s = importlib.util.spec_from_file_location('w', sys.argv[1])\n"
                 "m = importlib.util.module_from_spec(s)\n"
                 "s.loader.exec_module(m)\n", str(copy / worker.name)],
                capture_output=True, encoding="utf-8", timeout=60, check=False,
            )
            assert done.returncode == 0, done.stderr[-1500:]
            assert not list(copy.glob("__pycache__/child_confine*")), worker.name


# ── 2. The order of main ────────────────────────────────────────────────────


class TestTheOrder:
    def test_main_closes_confines_and_preloads_before_it_reads_the_page(self) -> None:
        tree = ast.parse(WORKER.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        steps = ["_cc.close_inherited_fds", "_limit_self", "_cc.confine", "_preload",
                 "_cc.close_limits", "sys.stdin.buffer.read", "_answer"]
        positioned = sorted(
            (n.end_lineno, n.end_col_offset, ast.unparse(n.func))
            for n in ast.walk(main) if isinstance(n, ast.Call))
        order = [c for _l, _c, c in positioned if c in set(steps)]
        assert order == steps, order

    def test_the_floor_is_the_file_system(self) -> None:
        assert _load(WORKER, "web2b_w2").LANDLOCK_ABI == 1

    def test_file_mode_reads_the_file_only_after_the_confinement(self) -> None:
        """WEB-3: file mode is the SAME ``main``. The bytes of the file come
        from the one stdin read, and ``_answer_file`` runs after it."""
        tree = ast.parse(WORKER.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        wanted = {"_cc.confine", "_preload", "_cc.close_limits", "sys.stdin.buffer.read",
                  "_answer_file"}
        positioned = sorted((n.end_lineno, n.end_col_offset, ast.unparse(n.func))
                            for n in ast.walk(main) if isinstance(n, ast.Call))
        assert [c for _l, _c, c in positioned if c in wanted] == [
            "_cc.confine", "_preload", "_cc.close_limits", "sys.stdin.buffer.read",
            "_answer_file"]

    def test_file_mode_has_no_second_confinement(self) -> None:
        names = {n.name for n in ast.walk(ast.parse(WORKER.read_text(encoding="utf-8")))
                 if isinstance(n, ast.FunctionDef)}
        assert not names & {"seccomp_program", "_install_seccomp", "landlock_abi", "confine"}


# ── 3. The confinement (Linux) ──────────────────────────────────────────────

#: The probe child. It loads the real worker, puts the attacks in place of the
#: page reader, and runs the real ``main``. Mode ``none`` is the control: it
#: runs the attacks with no confinement at all.
_PROBE = r'''
import errno, importlib.util, json, os, socket, sys
spec = importlib.util.spec_from_file_location("w", sys.argv[1])
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
mode, secret, out_path, port = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
# A TCP socket made BEFORE main: a descriptor that the parent could pass on.
pre = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
pre.settimeout(3)

def attempt(fn):
    try:
        got = fn()
        return "allowed" + (":" + got if isinstance(got, str) else "")
    except OSError as exc:
        return "refused:" + errno.errorcode.get(exc.errno, str(exc.errno))

def read(path):
    with open(path, "rb") as fh:
        return "canary" if b"web2bcanary" in fh.read() else "read"

def write():
    with open(out_path, "w") as fh:
        fh.write("x")

def connect():
    socket.create_connection(("127.0.0.1", port), timeout=3).close()

def connect_inherited():
    pre.connect(("127.0.0.1", port))

def fork():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)

def signal_parent():
    os.kill(os.getppid(), 0)

def unix():
    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()

def truncate_env():
    os.truncate(secret, 0)

def chmod_env():
    os.chmod(secret, 0)

def seccomp_mode():
    # PR_GET_SECCOMP. /proc is outside the read dirs, so the status file is not
    # readable here. The prctl answer is the same fact: 2 is a filter.
    import ctypes
    return "mode" + str(ctypes.CDLL(None, use_errno=True).prctl(21, 0, 0, 0, 0))

def attacks(_html=""):
    return {
        "secret_file": attempt(lambda: read(secret)),
        "truncate_env": attempt(truncate_env),
        "chmod_env": attempt(chmod_env),
        "seccomp": seccomp_mode(),
        "write_file": attempt(write),
        "tcp_connect": attempt(connect),
        "tcp_inherited_socket": attempt(connect_inherited),
        "unix_socket": attempt(unix),
        "signal_parent": attempt(signal_parent),
        "fork": attempt(fork),
        "stdlib_read": attempt(lambda: read(os.path.join(os.path.dirname(os.__file__), "json", "__init__.py"))),
    }

if mode == "none":
    print(json.dumps(attacks()))
    sys.exit(0)
if mode == "file":
    # WEB-3: the same attacks in file mode, in place of the file reader.
    w._answer_file = lambda _data, _ext: attacks()
    sys.exit(w.main(".pdf"))
if mode == "thread":
    import threading, time
    threading.Thread(target=time.sleep, args=(30,), daemon=True).start()
if mode == "abi":
    w.LANDLOCK_ABI = int(sys.argv[6])
def _page_read_must_not_run(_html):
    return {"fault": "the page was read"}
w._answer = _page_read_must_not_run if mode in ("thread", "abi") else attacks
sys.exit(w.main())
'''


_SECRET_TEXT = "DATABASE_URL=postgresql://u:web2bcanary@h/db\n"


def _secret_state(tmp_path: Path) -> tuple[int, int]:
    """``(size, mode)`` of the ``.env`` stand-in of :func:`_probe`."""
    st = (tmp_path / "opt" / "acb" / "app" / ".env").stat()
    return st.st_size, st.st_mode & 0o7777


def _probe(tmp_path: Path, mode: str, port: int, *extra: str) -> dict[str, str]:
    app = tmp_path / "opt" / "acb" / "app"
    app.mkdir(parents=True, exist_ok=True)
    secret = app / ".env"
    secret.unlink(missing_ok=True)
    secret.write_text(_SECRET_TEXT, encoding="utf-8")
    secret.chmod(0o644)
    done = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, str(WORKER), mode, str(secret),
         str(app / "written.txt"), str(port), *extra],
        cwd=app, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False,
    )
    assert done.stdout, done.stderr.decode(errors="replace")[-2000:]
    return json.loads(done.stdout.splitlines()[-1])


@pytest.fixture
def listener():
    """A TCP port on the loopback that a probe may try to connect to."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    yield server.getsockname()[1]
    server.close()


@linux_only
@pytest.mark.skipif(LANDLOCK_ABI < 1, reason="this Linux kernel has no Landlock at all")
class TestTheConfinement:
    def test_each_attack_works_unconfined(self, tmp_path, listener) -> None:
        """The control: with no confinement each attack succeeds, so the
        refusals below are real."""
        got = _probe(tmp_path, "none", listener)
        assert got["secret_file"] == "allowed:canary", got
        assert got["write_file"] == "allowed", got
        assert got["tcp_connect"] == "allowed", got
        assert got["tcp_inherited_socket"] == "allowed", got
        assert got["unix_socket"] == "allowed", got
        assert got["signal_parent"] == "allowed", got
        assert got["fork"] == "allowed", got
        assert got["truncate_env"] == "allowed" and got["chmod_env"] == "allowed", got
        assert _secret_state(tmp_path) == (0, 0), "the control did not harm the stand-in"
        assert got["seccomp"] == "mode0", got

    def test_the_real_main_refuses_each_attack(self, tmp_path, listener) -> None:
        got = _probe(tmp_path, "confine", listener)
        assert got["secret_file"] == "refused:EACCES", got
        assert got["write_file"] == "refused:EACCES", got
        assert not (tmp_path / "opt/acb/app/written.txt").exists()
        assert got["stdlib_read"] == "allowed:read", got
        assert got["tcp_connect"] == "refused:EPERM", got  # seccomp: no new socket
        assert got["unix_socket"] == "refused:EPERM", got
        assert got["signal_parent"] == "refused:EPERM", got
        # The filter is in force: PR_GET_SECCOMP answers 2 (a filter).
        assert got["seccomp"] == "mode2", got
        # The descriptor that the parent could pass on is shut before anything.
        assert got["tcp_inherited_socket"] == "refused:EBADF", got
        if os.geteuid() != 0:  # root ignores RLIMIT_NPROC
            assert got["fork"] in ("refused:EAGAIN", "refused:EWOULDBLOCK"), got

    def test_file_mode_refuses_each_attack_too(self, tmp_path, listener) -> None:
        """WEB-3: a file parser is a bigger target than a page parser, and it
        runs through the same ``main``. It meets the same refusals."""
        got = _probe(tmp_path, "file", listener)
        assert got["secret_file"] == "refused:EACCES", got
        assert got["write_file"] == "refused:EACCES", got
        assert got["tcp_connect"] == "refused:EPERM", got
        assert got["unix_socket"] == "refused:EPERM", got
        assert got["signal_parent"] == "refused:EPERM", got
        assert got["seccomp"] == "mode2", got
        assert got["tcp_inherited_socket"] == "refused:EBADF", got
        assert got["truncate_env"] == "refused:EPERM" and got["chmod_env"] == "refused:EPERM", got
        assert _secret_state(tmp_path) == (len(_SECRET_TEXT), 0o644)

    def test_the_child_cannot_shrink_or_chmod_the_env_file(self, tmp_path, listener) -> None:
        """Repair 1, P1 and P2. Below Landlock ABI 3 a truncate by path is
        outside Landlock, and no ABI covers a mode change. The filter refuses
        both, so the file keeps its size and its mode."""
        got = _probe(tmp_path, "confine", listener)
        assert got["truncate_env"] == "refused:EPERM", got
        assert got["chmod_env"] == "refused:EPERM", got
        assert _secret_state(tmp_path) == (len(_SECRET_TEXT), 0o644)

    @pytest.mark.skipif(LANDLOCK_ABI < 4, reason=(
        f"Landlock ABI {LANDLOCK_ABI} has no network rules; ABI 4 (Linux 6.7) adds TCP. "
        "CI (ubuntu-24.04) and the box have it"))
    def test_with_abi_four_the_kernel_refuses_tcp_too(self, tmp_path, listener) -> None:
        """The seccomp filter and the closed descriptors already stop a TCP
        connect. From ABI 4 on, Landlock refuses it as well: ``confine`` asks
        for the net rights that the kernel offers."""
        code = ("import sys, socket, importlib.util\n"
                "s = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
                "c = importlib.util.module_from_spec(s); s.loader.exec_module(c)\n"
                "pre = socket.socket(); pre.settimeout(3)\n"
                "c.confine(1)\n"
                "try:\n    pre.connect(('127.0.0.1', int(sys.argv[2]))); print('allowed')\n"
                "except OSError as e:\n    print(e.errno)\n")
        done = subprocess.run([sys.executable, "-I", "-c", code, str(SHARED), str(listener)],
                              capture_output=True, encoding="utf-8", timeout=60, check=False)
        assert done.stdout.strip() == "13", done  # EACCES from Landlock, not EPERM

    def test_a_kernel_below_the_floor_fails_closed(self, tmp_path, listener) -> None:
        got = _probe(tmp_path, "abi", listener, str(LANDLOCK_ABI + 1))
        assert got == {"fault": "unavailable"}, got

    def test_a_second_thread_fails_closed(self, tmp_path, listener) -> None:
        got = _probe(tmp_path, "thread", listener)
        assert got == {"fault": "unavailable"}, got


# ── 4. The descriptors (Linux) ──────────────────────────────────────────────

#: Runs in a parent on uvloop. It holds an inheritable pipe, starts a child
#: through ``web_tools._child_read``, and prints what the child saw.
_HARNESS = r'''
import asyncio, json, os, sys
import uvloop
from acb_skills import web_tools
mode, worker = sys.argv[1], sys.argv[2]
r, w = os.pipe()
os.set_inheritable(r, True)
os.set_inheritable(w, True)
CONTROL = ("import json, os\n"
           "fds = [int(n) for n in os.listdir('/proc/self/fd')\n"
           "       if os.path.exists('/proc/self/fd/' + n)]\n"
           "print(json.dumps({'text': json.dumps(sorted(fds))}))\n")
REAL = ("import importlib.util, json, os, sys\n"
        "s = importlib.util.spec_from_file_location('w', sys.argv[1])\n"
        "w = importlib.util.module_from_spec(s); s.loader.exec_module(w)\n"
        "seen = []\n"
        "real = w._limit_self\n"
        "def limit():\n"
        "    seen.extend(int(n) for n in os.listdir('/proc/self/fd')\n"
        "                if os.path.exists('/proc/self/fd/' + n))\n"
        "    real()\n"
        "w._limit_self = limit\n"
        "w._answer = lambda _h: {'text': json.dumps(sorted(seen))}\n"
        "sys.exit(w.main())\n")
argv = ([sys.executable, "-I", "-c", CONTROL] if mode == "control"
        else [sys.executable, "-I", "-c", REAL, worker])
web_tools._reader_argv = lambda: argv

async def run():
    page = await web_tools._child_read("<p>x</p>")
    print(json.dumps({"loop": type(asyncio.get_running_loop()).__module__,
                      "mine": [r, w], "seen": json.loads(page.text) if page else None}))

uvloop.run(run())
'''


def _harness(mode: str) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, "-c", _HARNESS, mode, str(WORKER)],
        capture_output=True, encoding="utf-8", timeout=120, check=False, cwd=REPO,
    )
    assert done.stdout, done.stderr[-2000:]
    return json.loads(done.stdout.splitlines()[-1])


@linux_only
class TestTheDescriptors:
    def test_a_parent_on_uvloop_leaks_an_inheritable_descriptor(self) -> None:
        """The control: ``close_fds`` is not in force under uvloop, so a plain
        child sees the pipe of the parent. The premise of the fix."""
        pytest.importorskip("uvloop")
        got = _harness("control")
        assert got["loop"].startswith("uvloop"), got
        assert set(got["mine"]) <= set(got["seen"]), got

    def test_the_real_worker_shuts_every_descriptor_from_three_up(self) -> None:
        pytest.importorskip("uvloop")
        got = _harness("real")
        assert got["loop"].startswith("uvloop"), got
        assert got["seen"] is not None and set(got["seen"]) <= {0, 1, 2}, got
        assert not set(got["mine"]) & set(got["seen"]), got

    def test_the_helper_leaves_descriptors_zero_to_two(self) -> None:
        code = ("import importlib.util, os, sys\n"
                "s = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
                "c = importlib.util.module_from_spec(s); s.loader.exec_module(c)\n"
                "a = os.open(sys.argv[1], os.O_RDONLY); os.set_inheritable(a, True)\n"
                "c.close_inherited_fds()\n"
                "print([os.path.exists('/proc/self/fd/%d' % n) for n in (0, 1, 2, a)])\n")
        done = subprocess.run([sys.executable, "-I", "-c", code, str(SHARED)],
                              capture_output=True, encoding="utf-8", timeout=60, check=False,
                              stdin=subprocess.PIPE)
        assert done.stdout.strip() == "[True, True, True, False]", done


# ── 5. The group (POSIX) ────────────────────────────────────────────────────

#: A leader that forks a grandchild into its own group, writes the pid, prints
#: a good answer and exits 0. ``HOLD`` decides if the grandchild keeps stdout.
_LEADER = (
    "import pathlib, subprocess, sys\n"
    "out = None if {hold} else subprocess.DEVNULL\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],\n"
    "                     stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL)\n"
    "pathlib.Path({mark!r}).write_text(str(p.pid))\n"
    "print('{{\"text\": \"leader answer\"}}', flush=True)\n"
)


def _alive(pid: int) -> bool:
    """True when *pid* runs. A zombie is dead: nobody has reaped it yet."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _gone(pid: int, bound: float = 8.0) -> bool:
    end = time.monotonic() + bound
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


@linux_only
@posix_only
class TestTheGroup:
    async def test_a_grandchild_dies_when_the_leader_exits_zero(
            self, monkeypatch, tmp_path) -> None:
        mark = tmp_path / "grandchild.pid"
        _fake_child(monkeypatch, tmp_path, _LEADER.format(hold=False, mark=str(mark)))
        page = await web_tools._child_read("<p>x</p>")
        pid = int(mark.read_text(encoding="utf-8"))
        try:
            assert page is not None and page.text == "leader answer"
            assert _gone(pid), "a forked grandchild outlived the read"
        finally:
            if _alive(pid):
                os.kill(pid, 9)

    async def test_a_grandchild_that_holds_stdout_dies_at_the_timeout(
            self, monkeypatch, tmp_path) -> None:
        """The leader is gone, and the pipe stays open until the timeout. The
        kill that follows must still reach the group."""
        mark = tmp_path / "grandchild.pid"
        _fake_child(monkeypatch, tmp_path, _LEADER.format(hold=True, mark=str(mark)))
        monkeypatch.setattr(web_tools, "READER_TIMEOUT_S", 0.7)
        log = _record_log(monkeypatch)
        start = time.perf_counter()
        page = await web_tools._child_read("<p>x</p>")
        pid = int(mark.read_text(encoding="utf-8"))
        try:
            assert page is None and log.reasons == ["timeout"], log.reasons
            assert time.perf_counter() - start < 6
            assert _gone(pid), "a forked grandchild outlived the read"
        finally:
            if _alive(pid):
                os.kill(pid, 9)

    async def test_the_slot_returns_with_no_process_left(self, monkeypatch, tmp_path) -> None:
        mark = tmp_path / "grandchild.pid"
        _fake_child(monkeypatch, tmp_path, _LEADER.format(hold=False, mark=str(mark)))
        monkeypatch.setattr(web_tools, "_run_org", lambda: ORG)
        answer = web_tools._Answer(200, content_type="text/html", body=b"<p>x</p>")
        page = await web_tools._read_html(answer)
        pid = int(mark.read_text(encoding="utf-8"))
        try:
            assert page is not None and not web_tools._SLOTS_IN_USE
            assert _gone(pid), "the slot came back with a process left"
        finally:
            if _alive(pid):
                os.kill(pid, 9)


# ── 6. The fallback and the page ────────────────────────────────────────────


class TestTheFallback:
    def test_the_parent_knows_the_unavailable_code(self) -> None:
        assert "unavailable" in web_tools._CHILD_FAULTS

    async def test_a_child_that_cannot_confine_gives_the_strip_and_one_log_line(
            self, monkeypatch, tmp_path) -> None:
        _fake_child(monkeypatch, tmp_path, 'print(\'{"fault": "unavailable"}\')\n')
        log = _record_log(monkeypatch)
        _on(monkeypatch)
        _serve(monkeypatch, _fixture("menu_page.html"))
        out = await web_tools.fetch_page(URL)
        assert "Home menu item" in out and "Title:" not in out
        assert log.reasons == ["unavailable"]

    @linux_only
    @pytest.mark.skipif(LANDLOCK_ABI < 1, reason="this Linux kernel has no Landlock at all")
    async def test_the_real_worker_with_no_landlock_gives_the_strip(
            self, monkeypatch) -> None:
        """The real worker asks for an ABI that the kernel lacks. It parses no
        page and answers ``unavailable``, so the parent reads with the strip."""
        code = ("import importlib.util, sys\n"
                "s = importlib.util.spec_from_file_location('w', sys.argv[1])\n"
                "w = importlib.util.module_from_spec(s); s.loader.exec_module(w)\n"
                f"w.LANDLOCK_ABI = {LANDLOCK_ABI + 1}\n"
                "sys.exit(w.main())\n")
        argv = [sys.executable, "-I", "-c", code, str(WORKER)]
        monkeypatch.setattr(web_tools, "_reader_argv", lambda: argv)
        log = _record_log(monkeypatch)
        _on(monkeypatch)
        _serve(monkeypatch, _fixture("menu_page.html"))
        out = await web_tools.fetch_page(URL)
        assert "Home menu item" in out and "Title:" not in out
        assert log.reasons == ["unavailable"]

    @needs_a_confinable_host
    async def test_a_normal_page_still_reads_in_the_real_confined_child(self) -> None:
        """No fake and no patch: the worker as the parent starts it. On Linux
        it is confined, so the preload must leave the read nothing to open."""
        page = await web_tools._child_read(_fixture("menu_page.html"))
        assert page is not None and page.title == "Julia Pro: a large 3D printer", page
        assert page.date == "2026-10-05"
        assert len(page.text) > 200

    @needs_a_confinable_host
    async def test_the_confined_child_reads_a_page_with_a_changed_cwd(
            self, tmp_path, monkeypatch) -> None:
        """The cwd is the app dir on the box. The read dirs must not need it."""
        monkeypatch.chdir(tmp_path)
        page = await web_tools._child_read(_fixture("menu_page.html"))
        assert page is not None and page.title == "Julia Pro: a large 3D printer"


# ── 6a. The file child (WEB-3, Linux) ───────────────────────────────────────

_FILES = Path(__file__).resolve().parent / "fixtures" / "web" / "files"
_FILE_CASES = [
    ("julia.pdf", ".pdf", "Build volume is 400 by 400"),
    ("julia.docx", ".docx", "Build volume is 400 by 400"),
    ("julia.pptx", ".pptx", "Build volume is 400 by 400"),
    ("julia.xlsx", ".xlsx", "| Build volume | 400 by 400 by 450 millimetres |"),
    ("julia.csv", ".csv", "| nozzle-001 | 1 |"),
    ("julia.epub", ".epub", "Build volume is 400 by 400"),
]


@linux_only
class TestTheFileChild:
    """The real file child, confined: MarkItDown, magika, pdfminer, pdfplumber,
    pandas and onnxruntime must import and run with read access to the Python
    and library dirs only, and with no new thread after the limits."""

    @needs_a_confinable_host
    @pytest.mark.parametrize(("name", "ext", "phrase"), _FILE_CASES)
    async def test_each_type_reads_in_the_real_confined_child(self, name, ext, phrase) -> None:
        answer = web_tools._Answer(200, body=(_FILES / name).read_bytes())
        page = await web_tools._read_file(answer, ext)
        assert page is not None and phrase in page.text, page
        assert page.reader == "markitdown"

    @needs_a_confinable_host
    async def test_a_pdf_that_markitdown_fails_on_reads_by_pdfplumber_when_confined(
            self, monkeypatch) -> None:
        """The child with MarkItDown replaced by a failure: the confined
        pdfplumber reads the PDF."""
        code = ("import importlib.util, sys\n"
                "s = importlib.util.spec_from_file_location('w', sys.argv[1])\n"
                "w = importlib.util.module_from_spec(s); s.loader.exec_module(w)\n"
                "real, pre, done = w._markitdown, w._preload, []\n"
                "def preload(mode):\n"
                "    pre(mode)\n"
                "    done.append(1)\n"
                "def fails(data, ext):\n"
                "    if done and ext == '.pdf':\n"
                "        raise ValueError('no')\n"
                "    return real(data, ext)\n"
                "w._preload, w._markitdown = preload, fails\n"
                "sys.exit(w.main(sys.argv[3]))\n")
        argv = [sys.executable, "-I", "-c", code, str(WORKER), "x"]
        monkeypatch.setattr(web_tools, "_reader_argv", lambda: argv)
        answer = web_tools._Answer(200, body=(_FILES / "julia.pdf").read_bytes())
        page = await web_tools._read_file(answer, ".pdf")
        assert page is not None and page.reader == "pdfplumber", page
        assert "Julia Pro data sheet" in page.text

    @needs_a_confinable_host
    async def test_a_hostile_file_leaves_the_parent_free(self) -> None:
        """A workbook whose part inflates to 16 MB: the child fails or ends at its caps, and the
        read gives no text. It never raises in the parent."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/workbook.xml", b"<x/>" * 4_000_000)
        answer = web_tools._Answer(200, body=buf.getvalue())
        start = time.monotonic()
        page = await web_tools._read_file(answer, ".xlsx")
        assert page is None
        assert time.monotonic() - start < web_tools.FILE_TIMEOUT_S + 10


# ── 7. Repair round 1 ───────────────────────────────────────────────────────

_FD_FALLBACK = '''
import importlib.util, os, resource, sys
spec = importlib.util.spec_from_file_location("c", sys.argv[1])
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
mode = sys.argv[2]
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
def no_listing(_path):
    raise OSError("no /proc")
os.listdir = no_listing
if mode == "high":
    if hard != resource.RLIM_INFINITY and hard < 6000:
        print("skip"); sys.exit(0)
    resource.setrlimit(resource.RLIMIT_NOFILE, (6000, hard))
    fd = os.open(sys.argv[1], os.O_RDONLY)
    high = os.dup2(fd, 5000)
    c.close_inherited_fds()
    try:
        os.fstat(high); print("open")
    except OSError:
        print("closed")
else:
    real = resource.getrlimit
    resource.getrlimit = lambda _n: (1024, resource.RLIM_INFINITY)
    try:
        c.close_inherited_fds(); print("no refusal")
    except c.Refused as exc:
        print("refused:" + exc.reason)
'''


def _fd_fallback(mode: str) -> str:
    done = subprocess.run([sys.executable, "-I", "-c", _FD_FALLBACK, str(SHARED), mode],
                          capture_output=True, encoding="utf-8", timeout=60, check=False)
    assert done.stdout, done.stderr[-1500:]
    return done.stdout.strip()


@posix_only
class TestTheFdFallback:
    def test_with_no_proc_listing_an_fd_above_4095_is_closed(self) -> None:
        got = _fd_fallback("high")
        if got == "skip":
            pytest.skip("the hard RLIMIT_NOFILE of this host is below 6000")
        assert got == "closed"

    def test_with_no_hard_limit_the_helper_refuses(self) -> None:
        assert _fd_fallback("infinity") == "refused:unavailable"


_OCR_FDS = '''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("o", sys.argv[1])
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
keep = os.open(sys.argv[1], os.O_RDONLY)
os.set_inheritable(keep, True)
def fake_spec(_raw):
    seen = [n for n in range(3, 64) if os.path.exists("/proc/self/fd/%d" % n)]
    print(json.dumps({"open": seen, "kept": keep}), file=sys.stderr)
    raise o._Refused("failed")
o._spec = fake_spec
sys.exit(o.main(["worker", "{}"]))
'''


class TestTheOcrChildClosesFds:
    def test_main_closes_the_fds_before_anything_else(self) -> None:
        tree = ast.parse(OCR_WORKER.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = sorted((n.end_lineno, n.end_col_offset, ast.unparse(n.func))
                       for n in ast.walk(main) if isinstance(n, ast.Call))
        names = [c for _l, _c, c in calls if c != "time.monotonic"]
        assert names[0] == "_cc.close_inherited_fds", names

    def test_the_parent_passes_no_fd_on_purpose(self) -> None:
        source = (PKG / "attachment_ocr.py").read_text(encoding="utf-8")
        assert "pass_fds" not in source

    @linux_only
    def test_the_real_ocr_main_leaves_no_inherited_fd(self) -> None:
        done = subprocess.run([sys.executable, "-I", "-c", _OCR_FDS, str(OCR_WORKER)],
                              capture_output=True, encoding="utf-8", timeout=60, check=False)
        got = json.loads(done.stderr.strip().splitlines()[-1])
        assert got["kept"] >= 3 and got["open"] == [], got


class TestTheCiGuard:
    def test_ci_runs_on_a_kernel_with_landlock_abi_four(self) -> None:
        """R7. Every confinement test skips when Landlock is missing. On a CI
        runner that would read green. So this test FAILS there instead."""
        if not (os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
                and os.uname().machine == "x86_64"):
            pytest.skip("only a GitHub Actions run on Linux x86-64 needs this guard")
        assert LANDLOCK_ABI >= 4, (
            f"the CI runner offers Landlock ABI {LANDLOCK_ABI}: the confinement tests "
            "would skip or prove less than the box (ABI 4) has")
