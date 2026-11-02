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

def attacks(_html=""):
    return {
        "secret_file": attempt(lambda: read(secret)),
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


def _probe(tmp_path: Path, mode: str, port: int, *extra: str) -> dict[str, str]:
    app = tmp_path / "opt" / "acb" / "app"
    app.mkdir(parents=True, exist_ok=True)
    secret = app / ".env"
    secret.write_text("DATABASE_URL=postgresql://u:web2bcanary@h/db\n", encoding="utf-8")
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

    def test_the_real_main_refuses_each_attack(self, tmp_path, listener) -> None:
        got = _probe(tmp_path, "confine", listener)
        assert got["secret_file"] == "refused:EACCES", got
        assert got["write_file"] == "refused:EACCES", got
        assert not (tmp_path / "opt/acb/app/written.txt").exists()
        assert got["stdlib_read"] == "allowed:read", got
        assert got["tcp_connect"] == "refused:EPERM", got  # seccomp: no new socket
        assert got["unix_socket"] == "refused:EPERM", got
        assert got["signal_parent"] == "refused:EPERM", got
        # The descriptor that the parent could pass on is shut before anything.
        assert got["tcp_inherited_socket"] == "refused:EBADF", got
        if os.geteuid() != 0:  # root ignores RLIMIT_NPROC
            assert got["fork"] in ("refused:EAGAIN", "refused:EWOULDBLOCK"), got

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

    async def test_a_normal_page_still_reads_in_the_real_confined_child(self) -> None:
        """No fake and no patch: the worker as the parent starts it. On Linux
        it is confined, so the preload must leave the read nothing to open."""
        page = await web_tools._child_read(_fixture("menu_page.html"))
        assert page is not None and page.title == "Julia Pro: a large 3D printer", page
        assert page.date == "2026-10-05"
        assert len(page.text) > 200

    async def test_the_confined_child_reads_a_page_with_a_changed_cwd(
            self, tmp_path, monkeypatch) -> None:
        """The cwd is the app dir on the box. The read dirs must not need it."""
        monkeypatch.chdir(tmp_path)
        page = await web_tools._child_read(_fixture("menu_page.html"))
        assert page is not None and page.title == "Julia Pro: a large 3D printer"
