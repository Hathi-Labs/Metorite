"""The store under MAF's file tools for a sandboxed run (WS-43d).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.4 (the file tools), §7.5
rule B (the safe opener) and §16.3 (the thread's output folder and the run
data, D86).

``FileAccessProvider`` gives the model eight tools, ``file_access_read`` to
``file_access_grep``. This store is what they read and write. It sees the
SAME files as the run's container sees under ``/workspace``, so a script
that ``run_command`` runs and a file that a file tool writes always agree:

=====================  ===========================================  =========
Tool path              Host path                                    Kept
=====================  ===========================================  =========
``agent-data/…``       ``<working dir>/agent-data/…``               yes
``inputs/…``           ``<working dir>/inputs/…``                   yes
``outputs/…``          ``<working dir>/outputs/<thread slug>/…``    yes
``.run/…``             ``<state root>/.run-data/<org>/<thread>/…``  NO
=====================  ===========================================  =========

* **``outputs/`` is the thread's own folder.** No tool path reaches the
  shared ``outputs/`` or the folder of another thread: ``outputs/x`` always
  maps below ``outputs/<thread slug>/``.
* **``.run/`` is the run data.** It lies outside every kept folder, so the
  blob store never holds it, and the broker deletes it when the run ends.
* **Only four heads exist**: ``agent-data/``, ``inputs/``, ``outputs/`` and
  ``.run/``. A file at the root (a planted ``pandas.py``), any other folder,
  and any part that starts with a dot (``.git``, ``.cc-instance``, the skill
  author marker) are refused (review P1, fix round 1).
* **A skill folder belongs to the member who made it**
  (``agent_paths.claim_skill``). The store refuses to read, list, write or
  delete a skill folder that another member made, so no member's skill text
  or code reaches another member's run.
* **Every open goes through** :mod:`acb_skills.safe_open`, so a link at any
  depth fails the call, also one that appears during it.
* **Every call holds the dir lock** (``guard.hold()``, the broker's
  ``host_dir()``), so no exec of any container on the dir runs during it.
* **Each write and each delete in a kept folder goes to the blob store**:
  ``write_artifact.mirror_to_blob_store`` and
  ``write_artifact.mirror_delete_from_blob_store`` (``blob_store.delete_file``),
  under the run's own store key. A write under ``outputs/`` also shows as an
  artifact card that links to ``outputs/<thread slug>/<name>``.
* **A write past the quota is refused, and a delete still works** (§7.1 rule
  10), so the model can free space.
* **Each write and each delete calls ``decide()`` with the real host path**
  (the B6 write veto reads ``path``). In ``enforce`` mode a refusal
  fails the call. No boundary above depends on the permission mode.

This module does not import the orchestrator. The caller passes a guard
(``acb_skills.sandbox_tools``) that holds the broker's lock.

Fences: ``tests/unit/test_projects_sandbox_tools.py`` (WS43-F21),
``tests/unit/test_maf_code_session.py`` (WS43-F7, the store half, R8) and
``tests/unit/test_sandbox_safe_open.py`` (WS43-F14).
"""
from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acb_common import get_logger
from agent_framework import AgentFileStore, FileStoreEntry, FileSystemAgentFileStore

from acb_skills import safe_open

_log = get_logger("acb_skills.tenant_file_store")

__all__ = [
    "KEPT_HEADS",
    "OUTPUTS",
    "RUN_DATA",
    "HostFileGuard",
    "TenantFileStore",
    "first_time_shown",
]

#: The tool path of the thread's own output folder.
OUTPUTS = "outputs"
#: The tool path of the run data (``/workspace/.run`` in the container).
RUN_DATA = ".run"
#: The kept folders a tool path may start with, besides ``outputs`` and ``.run``.
KEPT_HEADS = ("agent-data", "inputs")


#: The last content shown as a card, per (working dir, path), so an output
#: whose content did not change is never mirrored or shown again (the
#: verifier, fix round 1). The sweep after a command and a file-tool write
#: both ask it. Bounded, and per process, as the broker is.
_SHOWN: dict[tuple[str, str], str] = {}
_SHOWN_MAX = 10_000


def first_time_shown(workspace: Path, rel: str, data: bytes) -> bool:
    """True, and remembered, when *data* is new content for *rel*."""
    import hashlib

    key = (str(workspace), rel)
    digest = hashlib.sha256(data).hexdigest()
    if _SHOWN.get(key) == digest:
        return False
    if key not in _SHOWN and len(_SHOWN) >= _SHOWN_MAX:
        _SHOWN.pop(next(iter(_SHOWN)))
    _SHOWN[key] = digest
    return True


class HostFileGuard(Protocol):
    """What the store needs from the broker. ``acb_skills.sandbox_tools`` makes one."""

    def hold(self) -> Any:
        """An async context manager that holds the dir lock of the working dir."""

    async def prepare(self) -> None:
        """Make the thread's output folder and run-data dir, under the lock."""

    def writes_refused(self) -> bool:
        """True while the working dir is over its quota."""


@dataclass(frozen=True)
class _Place:
    root: Path
    rel: str
    #: The working-dir path a kept file has in the blob store, or ``None``.
    store_rel: str | None
    outputs: bool = False
    run_data: bool = False


class TenantFileStore(FileSystemAgentFileStore):
    """``FileSystemAgentFileStore`` with the safe opener and the sandbox's map.

    It keeps MAF's root (the working dir) and replaces every open with
    :mod:`acb_skills.safe_open`. See the module docstring for the map.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        outputs_rel: str,
        run_data: Path,
        guard: HostFileGuard,
        member: str = "",
    ) -> None:
        super().__init__(workspace)
        self._workspace = Path(workspace)
        self._outputs_rel = outputs_rel.strip("/")
        self._run_data = Path(run_data)
        self._guard = guard
        #: The run's own member, from the run binding: the author of the
        #: skills this run may touch.
        self._member = str(member or "").strip().lower()
        #: This thread's own output folder name: the one ``outputs/`` maps to.
        self._own_slug = self._outputs_rel.rsplit("/", 1)[-1]

    # ── the map ─────────────────────────────────────────────────────────────

    def _place(self, path: str) -> _Place:
        parts = safe_open.split_rel(path)
        if not parts:
            return _Place(self._workspace, "", None)
        head, rest = parts[0], parts[1:]
        if any(part.startswith(".") for part in rest):
            raise safe_open.UnsafePath("A name that starts with a dot is not a workspace file.")
        if head == OUTPUTS:
            rel = "/".join([self._outputs_rel, *rest])
            return _Place(self._workspace, rel, rel, outputs=True)
        if head == RUN_DATA:
            return _Place(self._run_data, "/".join(rest), None, run_data=True)
        if head not in KEPT_HEADS:
            raise safe_open.UnsafePath(
                f"{head!r} is not a workspace folder. Use agent-data/, inputs/, "
                "outputs/ or .run/."
            )
        rel = "/".join(parts)
        return _Place(self._workspace, rel, rel)

    def _foreign_skill(self, place: _Place) -> bool:
        """True when *place* lies in a skill folder that another member made."""
        from acb_skills.agent_paths import skill_author, skill_top_rel

        if place.store_rel is None or place.root != self._workspace:
            return False
        # A path inside a skill folder, or the skill folder itself.
        top = skill_top_rel(place.store_rel) or skill_top_rel(f"{place.store_rel}/x")
        if top is None:
            return False
        author = skill_author(self._workspace, top)
        return author is not None and author != self._member

    def _refuse_foreign_skill(self, place: _Place) -> None:
        if self._foreign_skill(place):
            raise ValueError("That skill belongs to another member.")

    async def _claim(self, place: _Place, *, create: bool = True) -> None:
        """Check the write rules, and record this member on a new skill folder.

        A delete checks the rules with *create* off, so it never makes an author.
        """
        from acb_skills.agent_paths import claim_skill, refused_write

        if place.store_rel is None:
            return
        reason = await asyncio.to_thread(
            refused_write, self._workspace, place.store_rel,
            member=self._member, own_slug=self._own_slug,
        )
        if reason:
            raise ValueError(f"Refused: {reason}.")
        if not create:
            return
        claimed = await asyncio.to_thread(
            claim_skill, self._workspace, place.store_rel, self._member,
        )
        if claimed is not None:
            from acb_skills.write_artifact import mirror_to_blob_store

            await mirror_to_blob_store(
                claimed[0], claimed[1], mime_type="text/plain", action="create",
            )

    async def _prepare(self, place: _Place) -> None:
        if place.outputs or place.run_data:
            await self._guard.prepare()

    def _decide(self, tool_name: str, place: _Place, *, data: bytes | None) -> None:
        """``decide()`` with the REAL host target path, before a write or a delete.

        The B6 write veto reads ``path``, so it gets the host path the call
        will open, never the model's tool path. The containment root of the
        call is the root of that path, set as ``permission_check_root`` for
        this call only (the idiom of ``permission_policy._workspace_root``):
        the working dir for a kept folder, and this run's own run-data dir for
        ``.run/``, which lies outside the working dir by design (§16.3).

        In ``enforce`` mode a refusal raises, and the file tool answers with
        it. In ``audit`` mode it is logged only, and the map and the safe
        opener remain the boundary.
        """
        import os

        from acb_skills.permission_policy import decide
        from acb_skills.write_artifact import artifact_context_scope, derive_artifact_context

        target = str(place.root / place.rel)
        request: dict[str, Any] = {"tool_name": tool_name, "path": target}
        if data is not None:
            request["new_file_contents"] = ""
        with artifact_context_scope():
            derive_artifact_context(permission_check_root=str(place.root))
            try:
                approved, code, _detail = decide(request)
            except Exception:  # a policy bug must not open or brick the tool
                approved, code = True, "decide_error"
        mode = os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower()
        enforced = (not approved) and mode == "enforce"
        _log.info(
            "permission.decision", mode=mode, tool=tool_name, approved=not enforced,
            would_deny=not approved, reason=code, path=target[:300], surface="sandbox_file",
        )
        if enforced:
            raise ValueError(f"blocked by permission policy: {code}")

    # ── the store API ───────────────────────────────────────────────────────

    async def write(self, path: str, content: str, *, overwrite: bool = True) -> None:
        place = self._place(path)
        if not place.rel:
            raise safe_open.UnsafePath("A write needs a file name.")
        if self._guard.writes_refused():
            raise ValueError(
                "The working dir is over its quota. Delete files to free space, "
                "then write again."
            )
        data = content.encode("utf-8")
        self._decide("file_access_write", place, data=data)
        async with self._guard.hold():
            await self._prepare(place)
            await self._claim(place)
            existed = await asyncio.to_thread(safe_open.is_file, place.root, place.rel)
            await asyncio.to_thread(
                safe_open.write_bytes, place.root, place.rel, data, exclusive=not overwrite,
            )
        await self._after_write(place, data, existed=existed)

    async def _after_write(self, place: _Place, data: bytes, *, existed: bool) -> None:
        if place.store_rel is None:
            return
        if place.outputs and not first_time_shown(self._workspace, place.store_rel, data):
            return
        from acb_skills.write_artifact import announce_artifact, mirror_to_blob_store

        mime = mimetypes.guess_type(place.store_rel)[0] or "application/octet-stream"
        await mirror_to_blob_store(
            place.store_rel, data, mime_type=mime,
            action="modify" if existed else "create",
        )
        if place.outputs:
            announce_artifact(place.store_rel, data)

    async def read(self, path: str) -> str | None:
        place = self._place(path)
        if not place.rel:
            return None
        async with self._guard.hold():
            await asyncio.to_thread(self._refuse_foreign_skill, place)
            raw = await asyncio.to_thread(safe_open.read_bytes, place.root, place.rel)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"File '{path}' is not UTF-8 text and cannot be read.") from exc

    async def delete(self, path: str) -> bool:
        place = self._place(path)
        if not place.rel:
            raise safe_open.UnsafePath("A delete needs a file name.")
        self._decide("file_access_delete", place, data=None)
        async with self._guard.hold():
            await self._claim(place, create=False)
            deleted = await asyncio.to_thread(safe_open.unlink, place.root, place.rel)
        if deleted and place.store_rel is not None:
            from acb_skills.write_artifact import mirror_delete_from_blob_store

            await mirror_delete_from_blob_store(place.store_rel)
        return deleted

    async def list_children(self, directory: str = "") -> list[FileStoreEntry]:
        place = self._place(directory)
        async with self._guard.hold():
            await asyncio.to_thread(self._refuse_foreign_skill, place)
            listed = await asyncio.to_thread(safe_open.list_dir, place.root, place.rel)
            entries = [(n, k) for n, k in (listed or []) if not n.startswith(".")]
            if place.root == self._workspace and not place.rel:
                # The root shows the four tool heads, and nothing else.
                heads = (*KEPT_HEADS, OUTPUTS)
                entries = [(n, k) for n, k in entries if k == "dir" and n in heads]
                entries.insert(0, (RUN_DATA, "dir"))
            elif place.store_rel == "agent-data/skills":
                entries = [
                    (n, k) for n, k in entries
                    if not self._foreign_skill(self._place(f"agent-data/skills/{n}/SKILL.md"))
                ]
        return [
            FileStoreEntry(
                name, FileStoreEntry.DIRECTORY if kind == "dir" else FileStoreEntry.FILE,
            )
            for name, kind in entries
        ]

    async def file_exists(self, path: str) -> bool:
        place = self._place(path)
        if not place.rel:
            return False
        async with self._guard.hold():
            if await asyncio.to_thread(self._foreign_skill, place):
                return False
            return await asyncio.to_thread(safe_open.is_file, place.root, place.rel)

    async def create_directory(self, path: str) -> None:
        place = self._place(path)
        async with self._guard.hold():
            await self._prepare(place)
            if place.rel:
                await asyncio.to_thread(self._refuse_foreign_skill, place)
                await asyncio.to_thread(safe_open.ensure_dir, place.root, place.rel)

    async def search(
        self,
        directory: str,
        regex_pattern: str,
        glob_pattern: str | None = None,
        *,
        recursive: bool = False,
    ) -> list[Any]:
        """MAF's base search, over :meth:`list_children` and :meth:`read`.

        ``FileSystemAgentFileStore.search`` walks the disk with its own reads,
        so this store uses the base class search, which reads through the
        safe opener here.
        """
        return await AgentFileStore.search(
            self, directory, regex_pattern, glob_pattern, recursive=recursive,
        )
