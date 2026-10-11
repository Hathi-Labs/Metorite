"""package_artifacts — zip the files of THIS chat into one download card.

Owner request, 2026-10-11: "If I have chatted with a particular AI assistant
and it's generated multiple files for me, I should be able to ask it to zip
them into one package and allow me to download."

**Which files are "this chat's".** The rule of every other file reader, and
no new one:

* In a shared agent's tenant dir (store key ``o:<org>``), the chat owns the
  folder ``outputs/<thread slug>/`` (H-227, D12). With no paths, the tool
  takes every file in that folder.
* In any other working dir (a personal ``u:<member>`` dir, a team dir), the
  ``outputs/`` folder is the member's or the team's, not the chat's. With no
  paths, the tool takes the ``outputs/`` files that the blob history says
  this session wrote (``acb_memory.session_paths``, the rule that the chat
  delete purge reads).
* A named path goes through the rules of ``share_artifact``: containment
  (:func:`write_artifact.resolve_in_workspace`), the thread folder
  (:func:`write_artifact._thread_scoped`) and the check for another chat's
  folder or a loose file (:func:`write_artifact._not_this_chats`). It must
  also lie in ``outputs/`` or ``inputs/``. ``agent-data/`` is the agent's
  memory and is never packaged.

**What it never takes.** An absolute path, a ``..``, a NUL, a drive, a link
at any depth (every read goes through :mod:`acb_skills.safe_open`), and every
name that the gateway's file routes refuse (``agent_paths.is_blocked_rel``:
dotfiles such as ``.env``, keys, ``.git``, credential caches).

**The zip.** Entry names are relative, cleaned part by part, with no ``..``,
no leading ``/``, no drive and no backslash, so no entry can climb out of the
folder it is unpacked into (zip-slip). A second entry with the same name gets
``name (n).ext``. At most :data:`MAX_FILES` files and :data:`MAX_TOTAL_BYTES`
of input, and the zip itself at most the download cap of the file route. Over
a cap the tool writes nothing and says why: never a partial zip. The zip is
written with ``safe_open.publish_bytes`` (a temp file, then one link), under
the broker's dir lock, then shown through :func:`write_artifact.announce_artifact`,
the card seam of every other file tool, and mirrored to the blob store like
every ``outputs/`` file, so it survives a restart.

**Channels.** A WhatsApp run cannot receive a file today: ``bot_run`` sends
text, interactive parts and images only. So in a WhatsApp run the tool says
so plainly and builds nothing. It never claims a download it cannot give.

**Not a shell tool and not an egress tool.** It runs no code, and it reaches
nothing outside the run's own working dir (``open_world=False`` in
``tool_annotations``). The tenant, the member, the thread and the working dir
come from the run's bound artifact context, never from a tool argument (R5).

Fence: ``tests/unit/test_package_artifacts.py``.
"""
from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import os
import re
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

#: The most files one zip holds.
MAX_FILES = 200
#: The most bytes of input one zip reads. The same 50 MB as the download cap
#: of the file route (``gateway.routes.workspace._MAX_FILE_BYTES``).
MAX_TOTAL_BYTES = 50 * 1024 * 1024
#: The largest zip the tool writes. The file route refuses a bigger file
#: with 413, so a bigger zip would be a card that cannot download.
MAX_ZIP_BYTES = 50 * 1024 * 1024
#: The folders a package may read: the chat's documents and its uploads.
PACKAGE_HEADS = ("outputs", "inputs")
ZIP_MIME = "application/zip"
# The card reads the type that ``mimetypes`` guesses (``announce_artifact``).
# A Windows registry can map .zip to application/x-zip-compressed.
mimetypes.add_type(ZIP_MIME, ".zip")

#: Kinds that are compressed already. They go in stored, which costs no CPU.
_STORED_SUFFIXES = frozenset({
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".mov", ".m4a", ".pdf",
    ".docx", ".xlsx", ".pptx",
})

#: A character that may not stand in an entry name or a zip name.
_ENTRY_UNSAFE = re.compile(r'[\x00-\x1f\x7f\\/:*?"<>|]')
_NAME_UNSAFE = re.compile(r"[^\w .()-]+")

#: The answer in a WhatsApp run (rule 4 of the request: say so plainly).
WHATSAPP_ANSWER = (
    "I cannot send a zip file on WhatsApp. Open this chat in the Metorite app "
    "and ask there, and the download appears in the chat."
)


def _refusal(ctx: Mapping[str, Any]) -> str | None:
    """Why this run may not package, or ``None``. Read from the bound context only."""
    from acb_skills.write_artifact import DELIVER_TO

    if not ctx.get("workspace_root"):
        return "No workspace is configured for this run, so nothing was packaged."
    try:
        from acb_skills.whatsapp_ui import current_run
    except ImportError:  # pragma: no cover — acb_skills ships it
        current_run = None
    if current_run is not None and current_run() is not None:
        return WHATSAPP_ANSWER
    if isinstance(ctx.get(DELIVER_TO), Mapping):
        return (
            "An agent that another chat called cannot package files. Return the "
            "file paths, and the agent of that chat packages them."
        )
    if ctx.get("batch_thread") is True or not ctx.get("session_id"):
        return "This run has no chat, so there is nothing to package."
    return None


def _path_list(paths: Any) -> list[str] | str:
    """The named paths: a JSON list, one path per line, or a list. Or the error."""
    if paths is None:
        return []
    if isinstance(paths, (list, tuple)):
        items: list[Any] = list(paths)
    else:
        text = str(paths).strip()
        if not text:
            return []
        if text.startswith(("[", "{")):
            try:
                items = json.loads(text)
            except ValueError:
                return "paths must be a JSON list of strings, so nothing was packaged."
            if not isinstance(items, list):
                return "paths must be a JSON list of strings, so nothing was packaged."
        else:
            items = text.splitlines()
    if any(not isinstance(i, str) for i in items):
        return "paths must be a JSON list of strings, so nothing was packaged."
    out = list(dict.fromkeys(i.strip() for i in items if i.strip()))
    if len(out) > MAX_FILES:
        return f"Name at most {MAX_FILES} paths, so nothing was packaged."
    return out


def zip_name(name: Any) -> str:
    """The file name of the zip: the base name cleaned, then ``.zip``."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base.lower().endswith(".zip"):
        base = base[:-4]
    base = _NAME_UNSAFE.sub("-", base).strip(" .-")[:80].strip(" .-")
    return f"{base or 'files'}.zip"


def entry_name(parts: list[str]) -> str:
    """One zip entry name from path parts. It can never climb out (zip-slip).

    Each part loses every separator, drive colon and control character, and a
    part that is empty, ``.`` or ``..`` becomes ``_``. So the name is
    relative, and every part of it is a plain name.
    """
    clean: list[str] = []
    for part in parts:
        p = _ENTRY_UNSAFE.sub("_", str(part)).strip().rstrip(".").strip()
        clean.append(p if p not in ("", ".", "..") else "_")
    return "/".join(clean) or "_"


def _entry_parts(rel: str) -> list[str]:
    """The path inside the zip: below the chat's output folder, or ``inputs/...``."""
    from acb_skills.agent_paths import is_thread_slug

    parts = rel.split("/")
    head, rest = parts[0], parts[1:]
    if len(rest) > 1 and is_thread_slug(rest[0]):
        rest = rest[1:]
    return rest if head == "outputs" else [head, *rest]


def _unique(name: str, seen: set[str]) -> str:
    """*name*, or ``name (n).ext`` when the zip already holds it (any case)."""
    if name.casefold() not in seen:
        seen.add(name.casefold())
        return name
    p = PurePosixPath(name)
    for n in range(1, MAX_FILES + 2):
        candidate = str(p.with_name(f"{p.stem} ({n}){p.suffix}"))
        if candidate.casefold() not in seen:
            seen.add(candidate.casefold())
            return candidate
    raise ValueError(name)  # pragma: no cover — more names than files


def _scoped_head(rel: str) -> str:
    return rel.split("/", 1)[0]


def _named_rel(raw: str, root: Path, ctx: Mapping[str, Any]) -> str:
    """The thread-scoped path that one named *raw* path gives, or raises ``ValueError``.

    The same steps as ``share_artifact``, with three more refusals: an
    absolute path, a dot name, and a folder outside ``outputs/`` and
    ``inputs/``.
    """
    from acb_skills import safe_open
    from acb_skills.write_artifact import (
        _normalise_path,
        _not_this_chats,
        _thread_scoped,
        resolve_in_workspace,
    )

    text = raw.replace("\\", "/").strip()
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if "\x00" in text or text.startswith("/") or any(":" in p for p in parts):
        raise ValueError(f"'{raw}' was refused: name a path inside this chat, not an absolute one.")
    if any(p == ".." for p in parts):
        raise ValueError(f"'{raw}' was refused: a path may not hold '..'.")
    if any(p.startswith(".") for p in parts):
        raise ValueError(f"'{raw}' was refused: hidden files are never packaged.")
    clean = _normalise_path("/".join(parts) or "outputs")
    try:
        safe_open.split_rel(clean)
    except safe_open.UnsafePath as exc:
        raise ValueError(f"'{raw}' was refused: {exc}") from exc
    # Containment, as share_artifact checks it. A link at any depth is the
    # safe opener's to refuse, in every read below.
    if resolve_in_workspace(root, clean) is None:
        raise ValueError(f"'{raw}' is outside the workspace.")
    scoped = _thread_scoped(clean, ctx)
    if scoped is None:
        raise ValueError("This chat has no thread folder, so nothing was packaged.")
    if _scoped_head(scoped) not in PACKAGE_HEADS:
        raise ValueError(f"'{raw}' was refused: only files in outputs/ or inputs/ are packaged.")
    if _not_this_chats(ctx)(scoped):
        raise ValueError(f"'{raw}' belongs to another chat.")
    return scoped


def _kind_of(root: Path, rel: str) -> str | None:
    """``"file"``, ``"dir"`` or ``None`` (absent, or a link) for *rel*, with no link followed."""
    from acb_skills import safe_open

    parent, _, name = rel.rpartition("/")
    entries = safe_open.list_dir(root, parent)
    if entries is None:
        return None
    return dict(entries).get(name)


def _collect_named(
    root: Path, named: list[str], ctx: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """The files that the named paths give, and the files a folder walk left out.

    Raises ``ValueError`` for a refused path, so the call packages nothing.
    """
    from acb_skills import safe_open
    from acb_skills.agent_paths import is_blocked_rel
    from acb_skills.write_artifact import _not_this_chats

    not_this_chats = _not_this_chats(ctx)
    files: dict[str, None] = {}
    skipped: list[str] = []
    for raw in named:
        rel = _named_rel(raw, root, ctx)
        try:
            kind = _kind_of(root, rel)
        except safe_open.UnsafePath as exc:
            raise ValueError(f"'{raw}' was refused: it goes through a link.") from exc
        if kind == "file":
            if is_blocked_rel(rel):
                raise ValueError(f"'{raw}' was refused: that file is never packaged.")
            files[rel] = None
        elif kind == "dir":
            try:
                found = safe_open.walk_files(root, rel, max_files=MAX_FILES * 5)
            except safe_open.UnsafePath as exc:
                raise ValueError(f"'{raw}' was refused: it goes through a link.") from exc
            for path, _size, _mtime in found:
                if not_this_chats(path) or is_blocked_rel(path):
                    skipped.append(path)
                    continue
                files[path] = None
        else:
            raise ValueError(f"File not found: {raw}")
    return list(files), skipped


async def _collect_chat(root: Path, ctx: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Every file that this chat produced in ``outputs/``, and the names left out.

    Raises ``ValueError`` when the files cannot be known.
    """
    from acb_skills import safe_open
    from acb_skills.agent_paths import is_blocked_rel, is_tenant_instance, thread_outputs_rel
    from acb_skills.write_artifact import _current_agent_name, _not_this_chats

    session_id = str(ctx.get("session_id") or "")
    instance = str(ctx.get("instance") or "")
    if is_tenant_instance(instance):
        try:
            folder = thread_outputs_rel(session_id)
        except ValueError as exc:
            raise ValueError("This chat has no thread folder, so nothing was packaged.") from exc
        try:
            found = await asyncio.to_thread(
                safe_open.walk_files, root, folder, max_files=MAX_FILES * 5,
            )
        except safe_open.UnsafePath as exc:
            raise ValueError("This chat's folder goes through a link, so nothing was packaged.") from exc
        candidates = [path for path, _size, _mtime in found]
    else:
        try:
            from acb_memory import session_paths

            rows = await session_paths(session_id, instance=instance)
        except Exception as exc:  # the store is down: ask for names
            raise ValueError(
                "I could not list the files of this chat. Name the files or "
                "folders to package."
            ) from exc
        agent = _current_agent_name()
        candidates = sorted({
            path for a, path in rows
            if a == agent and _scoped_head(path) == "outputs"
        })
    not_this_chats = _not_this_chats(ctx)
    files: list[str] = []
    skipped: list[str] = []
    for path in candidates:
        if path.lower().endswith(".zip"):
            continue  # an earlier package never goes into the next one
        if not_this_chats(path) or is_blocked_rel(path):
            skipped.append(path)
            continue
        files.append(path)
    return files, skipped


def _build_zip(root: Path, files: list[str]) -> tuple[bytes, list[str], list[str]]:
    """The zip of *files*, its entry names, and the files that vanished. Raises ``ValueError``.

    Every read goes through the safe opener, so a link at any depth fails
    the call. Over a cap, the call fails with no zip.
    """
    from acb_skills import safe_open

    buf = io.BytesIO()
    names: list[str] = []
    gone: list[str] = []
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in files:
            try:
                fh = safe_open.open_read(root, rel)
            except safe_open.UnsafePath as exc:
                raise ValueError(f"'{rel}' was refused: it goes through a link.") from exc
            if fh is None:
                gone.append(rel)
                continue
            with fh:
                st = os.fstat(fh.fileno())
                if total + st.st_size > MAX_TOTAL_BYTES:
                    raise ValueError(_size_error())
                data = fh.read(MAX_TOTAL_BYTES - total + 1)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError(_size_error())
            arcname = _unique(entry_name(_entry_parts(rel)), seen)
            stamp = datetime.fromtimestamp(st.st_mtime, tz=UTC)
            info = zipfile.ZipInfo(arcname, date_time=_zip_time(stamp))
            info.external_attr = 0o644 << 16
            info.compress_type = (
                zipfile.ZIP_STORED
                if PurePosixPath(rel).suffix.lower() in _STORED_SUFFIXES
                else zipfile.ZIP_DEFLATED
            )
            zf.writestr(info, data)
            names.append(arcname)
    data = buf.getvalue()
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError(_size_error())
    return data, names, gone


def _zip_time(stamp: datetime) -> tuple[int, int, int, int, int, int]:
    """A zip timestamp. The format starts in 1980, so an older time is clamped."""
    if stamp.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)


def _size_error() -> str:
    mb = MAX_TOTAL_BYTES // (1024 * 1024)
    return (
        f"These files hold more than {mb} MB, so nothing was packaged. "
        "Name fewer files, or split them into two packages."
    )


def _publish(root: Path, rel: str, data: bytes) -> str:
    """Write *data* as a NEW file at *rel*, or at ``name (n).zip``. Returns the path taken."""
    from acb_skills import safe_open

    p = PurePosixPath(rel)
    for counter in range(1000):
        name = p.name if counter == 0 else f"{p.stem} ({counter}){p.suffix}"
        candidate = str(p.with_name(name))
        try:
            safe_open.publish_bytes(root, candidate, data)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(rel)


async def _make(
    root: Path, ctx: Mapping[str, Any], named: list[str], out_rel: str,
) -> tuple[str, bytes, list[str], list[str]]:
    """Collect, zip and write, under the broker's dir lock. Raises ``ValueError``.

    Returns the path taken, the zip, its entry names and the files left out.
    The lock means that no container on this dir runs an exec while the host
    reads and writes it, and its quota rule applies (§7.5, §7.1).
    """
    from acb_skills.write_artifact import _chat_dir_guard

    async with _chat_dir_guard(root) as over_quota:
        if over_quota:
            raise ValueError("The working dir is over its quota, so nothing was packaged.")
        if named:
            files, skipped = await asyncio.to_thread(_collect_named, root, named, ctx)
        else:
            files, skipped = await _collect_chat(root, ctx)
        if not files:
            what = "the paths you named" if named else "this chat"
            raise ValueError(f"There are no files to package in {what}.")
        if len(files) > MAX_FILES:
            raise ValueError(
                f"That is more than {MAX_FILES} files, so nothing was "
                "packaged. Name fewer files or folders."
            )
        data, entries, gone = await asyncio.to_thread(_build_zip, root, files)
        if not entries:
            raise ValueError("The files are gone from the workspace, so nothing was packaged.")
        rel = await asyncio.to_thread(_publish, root, out_rel, data)
    return rel, data, entries, skipped + gone


async def package_artifacts(paths: str = "", name: str = "") -> dict:
    """Zip this chat's files into one .zip, shown as one download card.

    Use it when the member asks to zip, package or bundle files. With no
    paths, it takes every file this chat produced. Never build a link.

    Args:
        paths: Optional JSON list of files or folders, e.g. ["outputs/a.md"].
        name:  Optional zip name.
    """
    from acb_skills import safe_open
    from acb_skills.agent_paths import refused_write
    from acb_skills.write_artifact import (
        _thread_scoped,
        announce_artifact,
        artifact_context,
        mirror_to_blob_store,
    )

    ctx = artifact_context()
    refusal = _refusal(ctx)
    if refusal:
        return {"error": refusal}
    named = _path_list(paths)
    if isinstance(named, str):
        return {"error": named}
    root = Path(str(ctx["workspace_root"])).resolve()
    out_rel = _thread_scoped(f"outputs/{zip_name(name)}", ctx)
    if out_rel is None:
        return {"error": "This chat has no thread folder, so nothing was packaged."}
    reason = refused_write(
        root, out_rel, member=ctx.get("member"), thread_id=ctx.get("session_id"),
    )
    if reason:
        return {"error": f"The zip was refused: {reason}."}
    try:
        made = await _make(root, ctx, named, out_rel)
    except safe_open.UnsafePath:  # a ValueError, so it comes first
        return {"error": "The zip was refused: its folder goes through a link."}
    except ValueError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": "No free name for the zip, so nothing was packaged."}
    except OSError as exc:
        return {"error": f"The zip could not be written: {exc.strerror or exc}."}
    rel, data, entries, left_out = made
    # The same write-through as every outputs/ file, so a restart keeps it.
    asyncio.ensure_future(mirror_to_blob_store(rel, data, mime_type=ZIP_MIME, action="create"))
    # The card seam of every file tool: the artifact_created event.
    download_url = announce_artifact(rel, data)
    result: dict[str, Any] = {
        "path": rel,
        "name": rel.rsplit("/", 1)[-1],
        "size": len(data),
        "mime_type": ZIP_MIME,
        "files": len(entries),
        "entries": entries[:20],
    }
    if download_url:
        result["download_url"] = download_url
    if left_out:
        result["left_out"] = left_out[:20]
        result["note"] = (
            f"{len(left_out)} file(s) were left out: hidden, secret, another "
            "chat's, or gone. Tell the member."
        )
    return result
