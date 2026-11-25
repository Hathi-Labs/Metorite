"""Who can change the TallyPrime program (task 3974).

The sign-in task (``system.startup_xml``) runs TALLY_EXE at the sign-in of
each person, administrators too. So a person who is not an administrator must
not be able to change that program. TallyPrime is often installed at
``C:\\TallyPrime``. A folder that an administrator makes at a drive root gets
Modify for Authenticated Users, so any user can replace ``tally.exe`` there.

``unsafe_component`` reads the owner and the DACL of the program and of each
folder above it, and names the first one that a user can use for that. The
rule is pure (``Entry`` lists), so a test on Linux covers it. Only
``WindowsAclReader`` reads real security descriptors, through pywin32. It runs
on Windows only: the windows-latest job runs the real check
(``tests/unit/test_tally_bridge_folder_acl.py``, the Windows test).

The rule, for each part of the path:

- **The program file:** a write, an append, a delete, a change of its DACL or
  owner, or a generic write or all.
- **The folder of the program:** the same, and also adding a file or a
  folder there (a user could put a DLL beside ``tally.exe`` that it loads).
- **Each folder above it:** a delete of that folder, the delete of a child, a
  change of the DACL or owner, or a generic write or all. Adding a new entry
  there is harmless, because it cannot replace an entry that exists.
- **The owner** of any part must be SYSTEM, the Administrators group or
  TrustedInstaller, because an owner can always rewrite the DACL.

Every entry but a deny entry counts as a grant, the callback and object
forms too, and an inherit-only entry does not apply to the object itself. A
deny entry is not trusted to undo an allow entry, so the check can refuse a
folder that is safe in fact. Any error of the read refuses too. It fails
closed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PureWindowsPath

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
#: The only principals that may change the program or its folders.
TRUSTED = frozenset({SYSTEM, ADMINISTRATORS, TRUSTED_INSTALLER})

ACCESS_ALLOWED = 0
#: The deny types: ACCESS_DENIED, its object, callback and callback-object
#: forms. Every other entry type in a DACL counts as a grant (it fails closed).
DENY_TYPES = frozenset({1, 6, 10, 12})
INHERIT_ONLY = 0x08

FILE_WRITE_DATA = 0x0002  # a file: write; a folder: add a file
FILE_APPEND_DATA = 0x0004  # a file: append; a folder: add a folder
FILE_DELETE_CHILD = 0x0040
DELETE = 0x00010000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000

#: The program file, and the folder of the program.
NEAR = (
    FILE_WRITE_DATA
    | FILE_APPEND_DATA
    | FILE_DELETE_CHILD
    | DELETE
    | WRITE_DAC
    | WRITE_OWNER
    | GENERIC_ALL
    | GENERIC_WRITE
)
#: Each folder above the folder of the program.
FAR = FILE_DELETE_CHILD | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL | GENERIC_WRITE


@dataclass(frozen=True)
class Entry:
    """One access control entry: its type, its flags, its mask and its SID."""

    ace_type: int
    flags: int
    mask: int
    sid: str


@dataclass(frozen=True)
class Security:
    owner: str
    entries: tuple[Entry, ...]
    #: True for a junction or a symbolic link. The read follows a link, so a
    #: user who can delete the link can put their own folder in its place.
    reparse: bool = False


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
#: The one folder beside the program that Windows reads for its DLLs.
DOT_LOCAL = "tally.exe.local"


class UnsafeTallyFolder(Exception):
    """A person who is not an administrator can change the TallyPrime program."""

    def __init__(self, path: str, reason: str, folder: str = "", near: bool = True) -> None:
        super().__init__(path, reason)
        self.path = path
        self.reason = reason
        self.folder = folder or path
        self.near = near

    def __str__(self) -> str:
        return message(self.path, self.reason, self.folder, self.near)


def message(path: str, reason: str, folder: str, near: bool) -> str:
    """The text for the customer: which part, why, and the one fix.

    For the program folder, the commands lock it: the administrators own each
    item, the old rights go, and only SYSTEM and the administrators may write.
    For a folder above it (often a drive root), locking would spread over the
    whole drive, so the fix is to install TallyPrime under Program Files."""
    head = f"The sign-in task is off. {reason}: {path}."
    if not near:
        return (
            f"{head} A user can rename or replace a folder above TallyPrime. "
            "Install TallyPrime under C:\\Program Files, set TALLY_EXE to it, "
            "then run apply-windows again."
        )
    return (
        f"{head} An administrator can lock the folder with three commands. "
        f'(1) icacls "{folder}" /setowner *{ADMINISTRATORS} /T /C '
        f'(2) icacls "{folder}" /reset /T /C '
        f'(3) icacls "{folder}" /inheritance:r /grant:r *{SYSTEM}:(OI)(CI)F '
        f"*{ADMINISTRATORS}:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX . "
        "Users can then only read that folder: move the company data out of it "
        "first. Or install TallyPrime under C:\\Program Files. "
        "Then run apply-windows again."
    )


def chain(tally_exe: str) -> list[str]:
    """The program, its folder, and each folder above it, up to the drive root."""
    exe = PureWindowsPath(tally_exe)
    return [str(exe)] + [str(p) for p in exe.parents]


def _reason(security: Security, needed: int, near: bool) -> str | None:
    if security.reparse:
        return "It is a link (a junction or a symbolic link), and a user can change where it points"
    if security.owner not in TRUSTED:
        return f"Its owner is {security.owner}, not an administrator"
    for entry in security.entries:
        if entry.ace_type in DENY_TYPES or entry.flags & INHERIT_ONLY:
            continue
        if entry.sid in TRUSTED:
            continue
        if entry.mask & needed:
            what = "change the program there" if near else "rename or replace a folder above it"
            return f"{entry.sid} can {what}"
    return None


def _first(
    parts: Iterable[str], read: Callable[[str], Security], needed: int, near: bool
) -> tuple[str, str] | None:
    for part in parts:
        try:
            security = read(part)
        # The check fails closed on any error: pywin32 raises pywintypes.error
        # (not an OSError), and a missing pywin32 raises ImportError.
        except Exception as exc:
            return part, f"Its rights cannot be read ({exc.__class__.__name__})"
        reason = _reason(security, needed, near)
        if reason is not None:
            return part, reason
    return None


def unsafe_component(
    tally_exe: str,
    read: Callable[[str], Security],
    list_dir: Callable[[str], list[str]] | None = None,
) -> tuple[str, str, str, bool] | None:
    """The first part that a user can change: (the part, the reason, the
    program folder, near), or None when only administrators, SYSTEM and
    TrustedInstaller can.

    The parts: the program and its folder (near), the folders above (far),
    and each other file in the program folder and a ``tally.exe.local``
    folder (near): ``tally.ini`` names what TallyPrime loads at start, and a
    DLL there loads into it. A subfolder such as the company data is not
    read: a write there does not change what starts."""
    parts = chain(tally_exe)
    folder = parts[1]
    found = _first(parts[:2], read, NEAR, near=True)
    if found is not None:
        return (*found, folder, True)
    found = _first(parts[2:], read, FAR, near=False)
    if found is not None:
        return (*found, folder, False)
    try:
        beside = (list_dir or windows_beside)(folder)
    except Exception as exc:
        return folder, f"Its files cannot be listed ({exc.__class__.__name__})", folder, True
    others = [b for b in beside if b.lower() != parts[0].lower()]
    found = _first(others, read, NEAR, near=True)
    return (*found, folder, True) if found is not None else None


def check(
    tally_exe: str,
    read: Callable[[str], Security] | None = None,
    list_dir: Callable[[str], list[str]] | None = None,
) -> None:
    """Raise UnsafeTallyFolder when a user can change the program."""
    found = unsafe_component(tally_exe, read or WindowsAclReader(), list_dir)
    if found is not None:
        raise UnsafeTallyFolder(*found)


class WindowsAclReader:  # pragma: no cover - Windows
    """The owner and the DACL of one path, through pywin32."""

    def __call__(self, path: str) -> Security:
        if sys.platform != "win32":
            raise OSError("the folder rights can be read on Windows only")
        import win32security

        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        reparse = bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
        sd = win32security.GetNamedSecurityInfo(
            path,
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
        owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:  # a NULL DACL gives everyone full control
            return Security(owner, (Entry(ACCESS_ALLOWED, 0, GENERIC_ALL, "S-1-1-0"),), reparse)
        entries: list[Entry] = []
        for i in range(dacl.GetAceCount()):
            ace = dacl.GetAce(i)
            (ace_type, flags), mask = ace[0], ace[1]
            sid = ace[-1]
            entries.append(Entry(ace_type, flags, mask, win32security.ConvertSidToStringSid(sid)))
        return Security(owner, tuple(entries), reparse)


def windows_beside(folder: str) -> list[str]:  # pragma: no cover - Windows
    """The files in the program folder, and a ``tally.exe.local`` folder."""
    if sys.platform != "win32":
        raise OSError("the program folder can be listed on Windows only")
    out = []
    with os.scandir(folder) as found:
        for item in found:
            if (
                item.is_symlink()
                or item.is_file(follow_symlinks=False)
                or item.name.lower() == DOT_LOCAL
            ):
                out.append(item.path)
    return out


def entries(rows: Iterable[Sequence[object]]) -> tuple[Entry, ...]:
    """Build entries from (type, flags, mask, sid) rows. For tests."""
    return tuple(Entry(int(t), int(f), int(m), str(s)) for t, f, m, s in rows)
