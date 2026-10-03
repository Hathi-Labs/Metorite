"""The safe opener — no host file access that a symlink can redirect.

WS-43d. Spec ``project-docs/specs/maf_coding_engine.md`` §7.5 rule B, fence
WS43-F14 (``tests/unit/test_sandbox_safe_open.py``).

A sandbox container can write anything in its mounted dir: a symlink, a
directory that becomes a symlink while the host reads it, a hard-to-see
``..`` chain. So every host reader and writer of a mounted dir opens its
paths through this module, and through nothing else.

How it opens a path:

* The root is opened with ``O_DIRECTORY | O_NOFOLLOW``. The caller computed
  the root on the server, from the run binding. It is never request input.
* On Linux 5.6 or later, one ``openat2`` call opens the path beneath the
  root with ``RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS``.
  The kernel then refuses a symlink at ANY depth, also one that appears
  during the call.
* Elsewhere on POSIX, it walks the path one part at a time: each part opens
  with ``O_NOFOLLOW`` relative to the directory descriptor of the part before
  it. A part that is a link fails with ``ELOOP`` or ``ENOTDIR``, and a part
  that a racing process swaps after the open no longer matters, because the
  walk holds the descriptor and never reopens a name.
* A path part of ``..``, ``.``, an empty part, an absolute path and a NUL are
  refused before any system call.

⚠️ Windows has no ``dir_fd``. There the module checks each part with
``lstat`` and then opens it, which closes the static case and not a race.
Production is Linux, and the sandbox runs only there. The dev box gets the
same answers for every test that does not race.

Every function here is synchronous. An async caller runs it with
``asyncio.to_thread``, inside ``broker.host_files()`` or ``broker.host_dir()``,
so no exec of a container runs on the dir during the call (§7.5).
"""
from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat as _stat
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "UnsafePath",
    "ensure_dir",
    "is_file",
    "list_dir",
    "open_read",
    "read_bytes",
    "remove_tree",
    "split_rel",
    "stat_file",
    "unlink",
    "walk_files",
    "write_bytes",
]


class UnsafePath(ValueError):
    """A path that the safe opener refuses: a link, a ``..``, or a bad part."""


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

#: True where the walk can hold directory descriptors. Linux and macOS.
_FD_WALK = os.name == "posix" and os.open in os.supports_dir_fd

#: ``openat2`` (Linux 5.6+). ``None`` until the first call probes it, then the
#: answer for the life of the process. Tests set it to ``False`` to drive the
#: per-part walk on a kernel that has ``openat2``.
_OPENAT2: bool | None = None
_SYS_OPENAT2 = 437  # the same number on every Linux architecture
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08


def split_rel(rel: str | os.PathLike[str]) -> list[str]:
    """The parts of a relative path, or :class:`UnsafePath`.

    ``""`` gives ``[]``, the root itself. A backslash counts as a separator,
    so a Windows-shaped path cannot hide a ``..``.
    """
    text = os.fspath(rel).replace("\\", "/")
    if "\x00" in text:
        raise UnsafePath("A path holds a NUL byte.")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise UnsafePath("A path must be relative to its root.")
    parts = [p for p in text.split("/") if p != ""]
    for part in parts:
        if part in (".", ".."):
            raise UnsafePath("A path may not hold '.' or '..'.")
    return parts


# ── The low-level open ───────────────────────────────────────────────────────


def _open_root(root: Path) -> int:
    if not root.is_absolute():
        raise UnsafePath("The root must be an absolute path.")
    try:
        return os.open(root, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePath("The root is not a real directory.") from exc
        raise


_LIBC: object | None = None
_OPEN_HOW: type | None = None


def _openat2_handles() -> tuple[object, type] | None:
    """The libc handle and the ``struct open_how`` type, made once."""
    global _LIBC, _OPEN_HOW
    if _LIBC is not None and _OPEN_HOW is not None:
        return _LIBC, _OPEN_HOW
    import ctypes

    class _OpenHowStruct(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint64),
            ("mode", ctypes.c_uint64),
            ("resolve", ctypes.c_uint64),
        ]

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall  # noqa: B018 — probe that the symbol exists
    except (OSError, AttributeError):
        return None
    _LIBC, _OPEN_HOW = libc, _OpenHowStruct
    return _LIBC, _OPEN_HOW


def _openat2(dir_fd: int, rel: str, flags: int, mode: int) -> int:
    """One ``openat2`` call, or ``-1`` when the kernel has none."""
    global _OPENAT2
    if _OPENAT2 is False:
        return -1
    import ctypes

    handles = _openat2_handles()
    if handles is None:
        _OPENAT2 = False
        return -1
    libc, open_how = handles
    # openat2 refuses a mode with EINVAL unless the call can create a file.
    how = open_how(
        flags | _O_CLOEXEC, mode if flags & os.O_CREAT else 0,
        _RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_MAGICLINKS,
    )
    fd = libc.syscall(  # type: ignore[attr-defined]
        ctypes.c_long(_SYS_OPENAT2), ctypes.c_int(dir_fd), os.fsencode(rel),
        ctypes.byref(how), ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if fd >= 0:
        _OPENAT2 = True
        return int(fd)
    err = ctypes.get_errno()
    if err in (errno.ENOSYS, errno.EPERM) and _OPENAT2 is None:
        # No openat2 on this kernel, or a seccomp profile refuses it.
        _OPENAT2 = False
        return -1
    if err == errno.EXDEV:
        raise UnsafePath("The path leaves its root.")
    if err == errno.ELOOP:
        raise UnsafePath("The path has a symbolic link in it.")
    raise OSError(err, os.strerror(err), rel)


def _walk_open(dir_fd: int, parts: list[str], flags: int, mode: int) -> int:
    """Open ``parts`` beneath ``dir_fd`` one part at a time, never following."""
    fd = os.dup(dir_fd)
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(
                    part, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
                    dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePath("The path has a symbolic link in it.") from exc
                raise
            os.close(fd)
            fd = nxt
        try:
            return os.open(parts[-1], flags | _O_NOFOLLOW | _O_CLOEXEC, mode, dir_fd=fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP or (
                exc.errno == errno.ENOTDIR and flags & _O_DIRECTORY
            ):
                raise UnsafePath("The path has a symbolic link in it.") from exc
            raise
    finally:
        os.close(fd)


def _windows_open(root: Path, parts: list[str], flags: int, mode: int) -> int:
    """Best effort on a platform with no ``dir_fd``: ``lstat`` each part, then open."""
    cur = root
    if cur.is_symlink() or cur.is_junction():
        raise UnsafePath("The root is not a real directory.")
    for part in parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            break
        if _stat.S_ISLNK(st.st_mode) or cur.is_junction():
            raise UnsafePath("The path has a symbolic link in it.")
    return os.open(cur, flags | _O_BINARY, mode)


def _open_beneath(root: Path, rel: str | os.PathLike[str], flags: int, mode: int = 0o600) -> int:
    """A descriptor for ``root/rel``, refusing a symlink at any depth."""
    parts = split_rel(rel)
    if not _FD_WALK:
        if not parts:
            raise UnsafePath("The root itself cannot be opened as a file.")
        return _windows_open(root, parts, flags, mode)
    root_fd = _open_root(root)
    try:
        if not parts:
            return os.dup(root_fd)
        fd = _openat2(root_fd, "/".join(parts), flags, mode)
        if fd >= 0:
            return fd
        return _walk_open(root_fd, parts, flags, mode)
    finally:
        os.close(root_fd)


@contextlib.contextmanager
def _dir_fd(root: Path, parts: list[str], *, create: bool = False) -> Iterator[int]:
    """A descriptor of the directory ``root/parts``, made on demand when *create*."""
    fd = _open_root(root)
    try:
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=fd)
            try:
                nxt = os.open(
                    part, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePath("The path has a symbolic link in it.") from exc
                raise
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def _windows_dir(root: Path, parts: list[str], *, create: bool) -> Path:
    cur = root
    for part in [None, *parts]:
        if part is not None:
            cur = cur / part
        if create and part is not None:
            with contextlib.suppress(FileExistsError):
                cur.mkdir()
        if cur.is_symlink() or cur.is_junction():
            raise UnsafePath("The path has a symbolic link in it.")
        if not cur.is_dir():
            raise FileNotFoundError(str(cur))
    return cur


# ── The public API ───────────────────────────────────────────────────────────


def open_read(root: Path, rel: str) -> BinaryIO | None:
    """A binary reader for a regular file beneath *root*, or ``None`` when absent.

    Raises :class:`UnsafePath` for a link at any depth, and for anything that
    is not a regular file.
    """
    try:
        fd = _open_beneath(root, rel, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            return None
        raise
    try:
        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("The path is not a regular file.")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_bytes(root: Path, rel: str, *, limit: int | None = None) -> bytes | None:
    """The bytes of a regular file beneath *root*, or ``None`` when absent.

    With *limit*, a file larger than *limit* bytes raises :class:`UnsafePath`
    rather than filling memory.
    """
    fh = open_read(root, rel)
    if fh is None:
        return None
    with fh:
        if limit is not None and os.fstat(fh.fileno()).st_size > limit:
            raise UnsafePath(f"The file is larger than {limit} bytes.")
        return fh.read()


def stat_file(root: Path, rel: str) -> os.stat_result | None:
    """The ``fstat`` of a regular file beneath *root*, or ``None``."""
    fh = open_read(root, rel)
    if fh is None:
        return None
    with fh:
        return os.fstat(fh.fileno())


def is_file(root: Path, rel: str) -> bool:
    """True when ``root/rel`` is a regular file reached with no link."""
    try:
        return stat_file(root, rel) is not None
    except (UnsafePath, OSError):
        return False


def write_bytes(
    root: Path, rel: str, data: bytes, *, exclusive: bool = False, make_parents: bool = True,
) -> None:
    """Write *data* to a regular file beneath *root*.

    Parent dirs are made on demand, one part at a time, and never through a
    link. *exclusive* refuses an existing file with :class:`FileExistsError`.
    """
    parts = split_rel(rel)
    if not parts:
        raise UnsafePath("A write needs a file name.")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if not _FD_WALK:
        parent = _windows_dir(root, parts[:-1], create=make_parents)
        target = parent / parts[-1]
        if target.is_symlink() or target.is_junction():
            raise UnsafePath("The path has a symbolic link in it.")
        fd = os.open(target, flags | _O_BINARY, 0o644)
    else:
        with _dir_fd(root, parts[:-1], create=make_parents) as parent_fd:
            try:
                fd = os.open(
                    parts[-1], flags | _O_NOFOLLOW | _O_CLOEXEC, 0o644, dir_fd=parent_fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise UnsafePath("The path has a symbolic link in it.") from exc
                raise
    try:
        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("The path is not a regular file.")
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def unlink(root: Path, rel: str) -> bool:
    """Remove a regular file beneath *root*. ``False`` when it was absent.

    A link at the last part is removed as a link and never followed.
    """
    parts = split_rel(rel)
    if not parts:
        raise UnsafePath("An unlink needs a file name.")
    if not _FD_WALK:
        try:
            parent = _windows_dir(root, parts[:-1], create=False)
        except FileNotFoundError:
            return False
        target = parent / parts[-1]
        if target.is_dir() and not target.is_symlink():
            raise IsADirectoryError(str(target))
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True
    try:
        with _dir_fd(root, parts[:-1]) as parent_fd:
            try:
                st = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if _stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(rel)
            os.unlink(parts[-1], dir_fd=parent_fd)
            return True
    except FileNotFoundError:
        return False


def ensure_dir(root: Path, rel: str) -> None:
    """Make ``root/rel`` and each missing parent, refusing any link on the way."""
    parts = split_rel(rel)
    if not _FD_WALK:
        _windows_dir(root, parts, create=True)
        return
    with _dir_fd(root, parts, create=True):
        pass


def list_dir(root: Path, rel: str = "") -> list[tuple[str, str]] | None:
    """``[(name, "dir" | "file")]`` of ``root/rel``, or ``None`` when absent.

    A link, a device and a socket are left out. Dirs come before files.
    """
    parts = split_rel(rel)
    try:
        if not _FD_WALK:
            here = _windows_dir(root, parts, create=False)
            entries = [(e.name, e) for e in os.scandir(here)]
            out_dirs, out_files = [], []
            for name, entry in entries:
                if entry.is_symlink() or Path(entry.path).is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    out_dirs.append((name, "dir"))
                elif entry.is_file(follow_symlinks=False):
                    out_files.append((name, "file"))
            return sorted(out_dirs) + sorted(out_files)
        with _dir_fd(root, parts) as fd:
            out_dirs, out_files = [], []
            for name in os.listdir(fd):
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                if _stat.S_ISDIR(st.st_mode):
                    out_dirs.append((name, "dir"))
                elif _stat.S_ISREG(st.st_mode):
                    out_files.append((name, "file"))
            return sorted(out_dirs) + sorted(out_files)
    except (FileNotFoundError, NotADirectoryError):
        return None


def walk_files(
    root: Path, rel: str = "", *, max_files: int | None = None,
) -> list[tuple[str, int, float]]:
    """``[(path, size, mtime)]`` of every regular file below ``root/rel``.

    *path* is relative to *root*, with ``/``. No link is followed, and a link
    is never listed. *max_files* bounds the list.
    """
    parts = split_rel(rel)
    found: list[tuple[str, int, float]] = []
    if not _FD_WALK:
        try:
            base = _windows_dir(root, parts, create=False)
        except FileNotFoundError:
            return found
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if not ((here / d).is_symlink() or (here / d).is_junction())
            )
            for name in sorted(filenames):
                p = here / name
                st = os.lstat(p)
                if not _stat.S_ISREG(st.st_mode):
                    continue
                found.append((p.relative_to(root).as_posix(), st.st_size, st.st_mtime))
                if max_files is not None and len(found) >= max_files:
                    return found
        return found
    try:
        with _dir_fd(root, parts) as start_fd:
            prefix = "/".join(parts)
            for dirpath, dirnames, filenames, dir_fd in os.fwalk(
                ".", dir_fd=start_fd, follow_symlinks=False,
            ):
                dirnames.sort()
                rel_dir = dirpath[2:] if dirpath.startswith("./") else ""
                for name in sorted(filenames):
                    try:
                        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError:
                        continue
                    if not _stat.S_ISREG(st.st_mode):
                        continue
                    path = "/".join(p for p in (prefix, rel_dir, name) if p)
                    found.append((path, st.st_size, st.st_mtime))
                    if max_files is not None and len(found) >= max_files:
                        return found
    except (FileNotFoundError, NotADirectoryError):
        return found
    return found


def remove_tree(root: Path, rel: str) -> bool:
    """Remove ``root/rel`` and all below it, following no link. ``False`` when absent.

    The parent is opened with the safe walk, and ``shutil.rmtree`` then works
    from that descriptor (``dir_fd``), which is its symlink-safe form.
    """
    parts = split_rel(rel)
    if not parts:
        raise UnsafePath("The root itself is never removed.")
    if not _FD_WALK:
        try:
            parent = _windows_dir(root, parts[:-1], create=False)
        except FileNotFoundError:
            return False
        target = parent / parts[-1]
        if target.is_symlink() or target.is_junction():
            target.unlink()
            return True
        if not target.exists():
            return False
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True
    try:
        with _dir_fd(root, parts[:-1]) as parent_fd:
            try:
                st = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if _stat.S_ISDIR(st.st_mode):
                shutil.rmtree(parts[-1], dir_fd=parent_fd)
            else:
                os.unlink(parts[-1], dir_fd=parent_fd)
            return True
    except FileNotFoundError:
        return False
