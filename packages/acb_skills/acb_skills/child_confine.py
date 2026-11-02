"""The confinement of a child process that parses a hostile file (WS-WEB WEB-2b).

ONE module for every such child: the OCR child (``attachment_ocr_worker``), the
web reader (``web_reader_worker``), and each later parser (WEB-3). Do not write
a second copy of Landlock or of the seccomp filter.

**How a child loads it.** A child is a script that runs as ``python -I
<path>``. It imports nothing from ``acb_skills``, because the package
``__init__`` loads the whole tool set. So the child loads THIS file by path,
with ``importlib``, and never with ``import acb_skills.child_confine``. This
file imports the standard library only. It writes no bytecode when it loads
(see ``load_confine`` in each worker).

**What it gives.** :func:`close_inherited_fds` shuts each inherited file
descriptor from 3 up. :func:`confine` applies Landlock (read of the Python and
library dirs only, no write, with ABI 4 no TCP bind or connect) and the seccomp
filter of :func:`seccomp_program` (no new socket, no signal to another
process). :func:`close_limits` sets ``RLIMIT_NPROC`` and ``RLIMIT_FSIZE`` to 0.
Each of the three fails closed on Linux and does nothing on another system.
``confine`` raises :class:`Refused` ``("unavailable")`` when a step fails, so
a child never parses unconfined on Linux.

Fence: ``tests/unit/test_attachment_ocr.py`` (``TestTheConfinement``) and
``tests/unit/test_web_reader_confine.py``.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from typing import Any

# ── Landlock (Linux), by syscall through ctypes ──────────────────────────────
# The numbers are the same on every architecture. See
# https://docs.kernel.org/userspace-api/landlock.html.
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_NET_BIND_TCP = 1 << 0
_NET_CONNECT_TCP = 1 << 1
_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
_SCOPE_SIGNAL = 1 << 1
#: The shared library dirs that a later ``dlopen`` can need.
_LIBRARY_DIRS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64")
#: Mapped paths that are not library files.
_NOT_LIBRARIES = ("/dev/", "/proc/", "/sys/", "/memfd:")


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


# ── seccomp (Linux x86-64), by prctl through ctypes (fix round 2) ────────────
# See https://docs.kernel.org/userspace-api/seccomp_filter.html.
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_EPERM = 1
_AUDIT_ARCH_X86_64 = 0xC000003E
#: An x32 call sets this bit in its number. The filter refuses each one.
_X32_SYSCALL_BIT = 0x40000000
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_RET_K = 0x06
#: The offsets in ``struct seccomp_data`` (x86-64 is little-endian): the call
#: number, the architecture, and the low and high words of ``args[0]`` and
#: the low word of ``args[1]``.
_DATA_NR = 0
_DATA_ARCH = 4
_DATA_ARG0_LOW = 16
_DATA_ARG0_HIGH = 20
_DATA_ARG1_LOW = 24
#: The x86-64 calls that the confined child never needs, each with its
#: number. Each one reaches another process, opens a socket, or changes the
#: scheduling of a process. The filter answers EPERM to each of them.
SECCOMP_DENIED: dict[str, int] = {
    "kill": 62,
    "tkill": 200,
    "tgkill": 234,
    "rt_sigqueueinfo": 129,
    "rt_tgsigqueueinfo": 297,
    "pidfd_open": 434,
    "pidfd_send_signal": 424,
    "ptrace": 101,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "socket": 41,
    "socketpair": 53,
    # Fix round 3. io_uring can make a socket with no socket() call
    # (IORING_OP_SOCKET), so no ring is set up at all.
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
    # Fix round 3. Each can act on the gateway's pid, which has our uid.
    "setpriority": 141,
    "sched_setparam": 142,
    "sched_setscheduler": 144,
    "sched_setaffinity": 203,
    "ioprio_set": 251,
    "sched_setattr": 314,
    # WEB-2b repair 1. Landlock covers TRUNCATE by path only from ABI 3, and it
    # never covers metadata. So a parser bug could shrink, chmod, chown or
    # relabel a file of the gateway user on an older kernel. These x86-64 calls
    # change a file in place. The child never needs one.
    "truncate": 76,
    "chmod": 90,
    "fchmod": 91,
    "chown": 92,
    "fchown": 93,
    "lchown": 94,
    "utime": 132,
    "setxattr": 188,
    "lsetxattr": 189,
    "fsetxattr": 190,
    "removexattr": 197,
    "lremovexattr": 198,
    "fremovexattr": 199,
    "utimes": 235,
    "fchownat": 260,
    "futimesat": 261,
    "fchmodat": 268,
    "utimensat": 280,
    "fchmodat2": 452,
}
_SYS_FCNTL = 72
_SYS_IOCTL = 16
_SYS_PRLIMIT64 = 302
#: The ``fcntl`` commands that make the kernel send a signal to another
#: process when the file is ready: F_SETOWN, F_SETSIG, F_SETOWN_EX.
FCNTL_DENIED: tuple[int, ...] = (8, 10, 15)
#: The ``ioctl`` requests that do the same: FIOSETOWN, SIOCSPGRP.
IOCTL_DENIED: tuple[int, ...] = (0x8901, 0x8902)


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]



def seccomp_program() -> list[tuple[int, int, int, int]]:
    """The filter, as ``(code, jt, jf, k)`` instructions.

    A call of another architecture kills the child. These answer EPERM: a
    call with the x32 bit, each call of :data:`SECCOMP_DENIED`, ``fcntl``
    with a command of :data:`FCNTL_DENIED`, ``ioctl`` with a request of
    :data:`IOCTL_DENIED`, and ``prlimit64`` for any pid but 0 (the child
    itself). Every other call passes. A jump counts from the next
    instruction. The kernel reads ``cmd`` and ``request`` as 32-bit values,
    so the low word of ``args[1]`` decides.
    """
    program: list[list[Any]] = []
    labels: dict[str, int] = {}

    def emit(code: int, k: int, jt: int | str = 0, jf: int | str = 0) -> None:
        program.append([code, jt, jf, k])

    emit(_BPF_LD_W_ABS, _DATA_ARCH)
    emit(_BPF_JEQ_K, _AUDIT_ARCH_X86_64, jt=1)
    emit(_BPF_RET_K, _SECCOMP_RET_KILL_PROCESS)
    emit(_BPF_LD_W_ABS, _DATA_NR)
    emit(_BPF_JGE_K, _X32_SYSCALL_BIT, jt="refuse")
    for number in sorted(SECCOMP_DENIED.values()):
        emit(_BPF_JEQ_K, number, jt="refuse")
    emit(_BPF_JEQ_K, _SYS_FCNTL, jt="fcntl")
    emit(_BPF_JEQ_K, _SYS_IOCTL, jt="ioctl")
    emit(_BPF_JEQ_K, _SYS_PRLIMIT64, jt="prlimit")
    emit(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    labels["fcntl"] = len(program)
    emit(_BPF_LD_W_ABS, _DATA_ARG1_LOW)
    for command in FCNTL_DENIED:
        emit(_BPF_JEQ_K, command, jt="refuse")
    emit(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    labels["ioctl"] = len(program)
    emit(_BPF_LD_W_ABS, _DATA_ARG1_LOW)
    for request in IOCTL_DENIED:
        emit(_BPF_JEQ_K, request, jt="refuse")
    emit(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    labels["prlimit"] = len(program)
    emit(_BPF_LD_W_ABS, _DATA_ARG0_LOW)
    emit(_BPF_JEQ_K, 0, jf="refuse")
    emit(_BPF_LD_W_ABS, _DATA_ARG0_HIGH)
    emit(_BPF_JEQ_K, 0, jf="refuse")
    emit(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    labels["refuse"] = len(program)
    emit(_BPF_RET_K, _SECCOMP_RET_ERRNO | _EPERM)
    out: list[tuple[int, int, int, int]] = []
    for index, (code, jt, jf, k) in enumerate(program):
        jt = labels[jt] - (index + 1) if isinstance(jt, str) else jt
        jf = labels[jf] - (index + 1) if isinstance(jf, str) else jf
        if not (0 <= jt <= 255 and 0 <= jf <= 255):
            raise ValueError("a seccomp jump is out of range")
        out.append((code, jt, jf, k))
    return out


def _install_seccomp(libc: Any) -> None:
    """Install :func:`seccomp_program` on this process, or refuse.

    It needs ``NO_NEW_PRIVS``, which ``confine`` has set. Another machine
    than x86-64, or a filter that does not install, refuses with
    ``unavailable``: the child never parses without it on Linux.
    """
    if os.uname().machine != "x86_64":
        raise Refused("unavailable")
    program = seccomp_program()
    filters = (_SockFilter * len(program))(*(_SockFilter(*ins) for ins in program))
    prog = _SockFprog(len(program), ctypes.cast(filters, ctypes.POINTER(_SockFilter)))
    if libc.prctl(
        ctypes.c_int(_PR_SET_SECCOMP), ctypes.c_ulong(_SECCOMP_MODE_FILTER),
        ctypes.byref(prog), ctypes.c_ulong(0), ctypes.c_ulong(0),
    ) != 0:
        raise Refused("unavailable")


class Refused(Exception):
    """A file that the child does not read. ``reason`` is a code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The Landlock ABI of this kernel, or 0 when it has none."""
    if sys.platform != "linux":
        return 0
    try:
        got = _libc().syscall(
            ctypes.c_long(_SYS_CREATE_RULESET), None, ctypes.c_size_t(0),
            ctypes.c_uint32(_CREATE_RULESET_VERSION),
        )
    except Exception:  # no libc symbol is no Landlock
        return 0
    return max(int(got), 0)


def _beneath(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _read_dirs() -> list[str]:
    """The dirs that the confined child may read, and no other.

    The Python dirs on ``sys.path`` that lie in the venv or in the Python
    install, the dir of each mapped shared library, and the system library
    dirs. A dir that holds the cwd (the app dir, with its ``.env``) or this
    file never joins, so no rule can reach the app dir. Each dir is a real
    path, and a dir inside another one is dropped.
    """
    prefixes = {os.path.realpath(p) for p in (
        sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
    ) if p}
    found: set[str] = set()
    for entry in sys.path:
        real = os.path.realpath(entry) if entry else ""
        if real and os.path.isdir(real) and any(_beneath(real, p) for p in prefixes):
            found.add(real)
    with open("/proc/self/maps", encoding="utf-8", errors="replace") as maps:
        for line in maps:
            parts = line.split(None, 5)
            path = parts[5].strip() if len(parts) == 6 else ""
            if not path.startswith("/") or path.startswith(_NOT_LIBRARIES):
                continue
            if os.path.isfile(path):
                found.add(os.path.realpath(os.path.dirname(path)))
    found.update(os.path.realpath(d) for d in _LIBRARY_DIRS if os.path.isdir(d))
    guards = (os.path.realpath(os.getcwd()), os.path.dirname(os.path.realpath(__file__)))
    allowed = sorted(d for d in found if not any(_beneath(g, d) for g in guards))
    return [d for d in allowed if not any(o != d and _beneath(d, o) for o in allowed)]


def _handled_fs(abi: int) -> int:
    """Each file system right that this ABI knows, so each one is denied
    unless a rule grants it."""
    bits = (1 << 13) - 1  # ABI 1: EXECUTE up to MAKE_SYM
    if abi >= 2:
        bits |= 1 << 13  # REFER
    if abi >= 3:
        bits |= 1 << 14  # TRUNCATE
    if abi >= 5:
        bits |= 1 << 15  # IOCTL_DEV
    return bits


def thread_count() -> int:
    """The threads of this process, from ``/proc/self/status``, or 0."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as status:
            for line in status:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def confine(min_abi: int) -> None:
    """Landlock and the seccomp filter, BEFORE the preload and the bytes.

    Landlock and seccomp bind the calling thread and the threads that it
    starts later. A thread that runs already stays free, and a parser bug
    shares its memory (fix round 3, LOW-1). So the child must have exactly
    one thread here, and it confines itself before any native library can
    start one. The onnxruntime helper thread of ``_preload`` then starts
    confined. It raises ``Refused("unavailable")`` when a step fails, so
    the child never parses unconfined on Linux. It does nothing on other
    systems.
    """
    if sys.platform != "linux":
        return
    abi = landlock_abi()
    if abi < min_abi:
        raise Refused("unavailable")
    if thread_count() != 1:
        raise Refused("unavailable")
    libc = _libc()
    attr = _RulesetAttr(
        handled_access_fs=_handled_fs(abi),
        handled_access_net=(_NET_BIND_TCP | _NET_CONNECT_TCP) if abi >= 4 else 0,
        scoped=(_SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL) if abi >= 6 else 0,
    )
    ruleset = libc.syscall(
        ctypes.c_long(_SYS_CREATE_RULESET), ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0),
    )
    if ruleset < 0:
        raise Refused("unavailable")
    try:
        for path in _read_dirs():
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathBeneath(allowed_access=_FS_READ_FILE | _FS_READ_DIR, parent_fd=fd)
                if libc.syscall(
                    ctypes.c_long(_SYS_ADD_RULE), ctypes.c_int(ruleset),
                    ctypes.c_int(_RULE_PATH_BENEATH), ctypes.byref(rule), ctypes.c_uint32(0),
                ) != 0:
                    raise Refused("unavailable")
            finally:
                os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise Refused("unavailable")
        if libc.syscall(
            ctypes.c_long(_SYS_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0),
        ) != 0:
            raise Refused("unavailable")
    finally:
        os.close(ruleset)
    # No signal to another process, no ptrace, no new socket (fix rounds 2, 3).
    _install_seccomp(libc)


def close_limits() -> None:
    """No fork, no new thread and no file growth, after the preload.

    ``RLIMIT_NPROC`` 0 must come after ``_preload``, because onnxruntime
    starts a thread there. ``setrlimit`` is ``prlimit64`` on the child
    itself, which the filter lets pass (pid 0). It does nothing off Linux.
    """
    if sys.platform != "linux":
        return
    import resource
    import signal

    # A write past the cap fails with EFBIG and does not kill the child.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def close_inherited_fds() -> None:
    """Close each file descriptor from 3 up. It runs first in a child.

    ``close_fds`` is not in force when the parent runs on uvloop, which is
    how uvicorn runs the gateway. So an inheritable descriptor of the parent
    (a socket, a pipe, a file) reaches the child, and a bug in a parser could
    use it. The child shuts every one before it reads a byte. Descriptors 0,
    1 and 2 are the pipes of the parent and stay. It does nothing on Windows.
    """
    if os.name != "posix":
        return
    try:
        # The listing holds its own descriptor open. That one fails on close.
        found = [int(name) for name in os.listdir("/proc/self/fd")]
    except (OSError, ValueError):
        # No listing: close every number up to the HARD limit. A number above
        # it cannot exist. With no hard limit the range is unbounded, so refuse.
        import resource

        hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if hard == resource.RLIM_INFINITY or hard < 0:
            raise Refused("unavailable") from None
        os.closerange(3, min(hard, 2**31 - 1))
        return
    for fd in found:
        if fd >= 3:
            with contextlib.suppress(OSError):
                os.close(fd)
