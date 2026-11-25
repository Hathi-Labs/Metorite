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

Only allow entries count, and an inherit-only entry does not apply to the
object itself. A deny entry is not trusted to undo an allow entry, so the
check can refuse a folder that is safe in fact. It fails closed.
"""

from __future__ import annotations

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


class UnsafeTallyFolder(Exception):
    """A person who is not an administrator can change the TallyPrime program."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(path, reason)
        self.path = path
        self.reason = reason

    def __str__(self) -> str:
        return message(self.path, self.reason)


def message(path: str, reason: str) -> str:
    """The text for the customer: which folder, why, and how to lock it."""
    folder = path if not path.lower().endswith(".exe") else str(PureWindowsPath(path).parent)
    return (
        f"The sign-in task is off: {reason} ({path}). An administrator can lock "
        f'the folder with: icacls "{folder}" /inheritance:r /grant:r '
        f"*{SYSTEM}:(OI)(CI)F *{ADMINISTRATORS}:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX "
        "Or install TallyPrime under C:\\Program Files. Then run apply-windows again."
    )


def chain(tally_exe: str) -> list[str]:
    """The program, its folder, and each folder above it, up to the drive root."""
    exe = PureWindowsPath(tally_exe)
    return [str(exe)] + [str(p) for p in exe.parents]


def _reason(security: Security, needed: int, near: bool) -> str | None:
    if security.owner not in TRUSTED:
        return f"its owner is {security.owner}, not an administrator"
    for entry in security.entries:
        if entry.ace_type != ACCESS_ALLOWED or entry.flags & INHERIT_ONLY:
            continue
        if entry.sid in TRUSTED:
            continue
        if entry.mask & needed:
            what = "change the program there" if near else "rename or replace a folder above it"
            return f"{entry.sid} can {what}"
    return None


def unsafe_component(tally_exe: str, read: Callable[[str], Security]) -> tuple[str, str] | None:
    """The first part of the path that a user can change, with the reason, or
    None when only administrators, SYSTEM and TrustedInstaller can."""
    parts = chain(tally_exe)
    for index, part in enumerate(parts):
        near = index <= 1  # the program, and its folder
        try:
            security = read(part)
        except OSError as exc:  # the check fails closed
            return part, f"its rights cannot be read ({exc.__class__.__name__})"
        reason = _reason(security, NEAR if near else FAR, near)
        if reason is not None:
            return part, reason
    return None


def check(tally_exe: str, read: Callable[[str], Security] | None = None) -> None:
    """Raise UnsafeTallyFolder when a user can change the program."""
    found = unsafe_component(tally_exe, read or WindowsAclReader())
    if found is not None:
        raise UnsafeTallyFolder(*found)


class WindowsAclReader:  # pragma: no cover - Windows
    """The owner and the DACL of one path, through pywin32."""

    def __call__(self, path: str) -> Security:
        if sys.platform != "win32":
            raise OSError("the folder rights can be read on Windows only")
        import win32security

        sd = win32security.GetNamedSecurityInfo(
            path,
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
        owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:  # a NULL DACL gives everyone full control
            return Security(owner, (Entry(ACCESS_ALLOWED, 0, GENERIC_ALL, "S-1-1-0"),))
        entries: list[Entry] = []
        for i in range(dacl.GetAceCount()):
            ace = dacl.GetAce(i)
            (ace_type, flags), mask = ace[0], ace[1]
            sid = ace[-1]
            entries.append(Entry(ace_type, flags, mask, win32security.ConvertSidToStringSid(sid)))
        return Security(owner, tuple(entries))


def entries(rows: Iterable[Sequence[object]]) -> tuple[Entry, ...]:
    """Build entries from (type, flags, mask, sid) rows. For tests."""
    return tuple(Entry(int(t), int(f), int(m), str(s)) for t, f, m, s in rows)
