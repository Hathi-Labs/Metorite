"""Task 3974: the sign-in task needs a TallyPrime that only administrators can
change.

The sign-in task (TLY-5) runs TALLY_EXE at the sign-in of each person,
administrators too. ``windows/acl.py`` refuses it when a user can change the
program, its folder, or a folder above it.

The rule is pure, so these tests feed it owners and DACL entries on Linux.
The real reader (pywin32) runs on Windows only: ``test_the_real_reader_on_windows``
runs in the windows-latest job (PR 2837) and skips here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge import __main__ as cli  # noqa: E402
from tally_bridge.settings import FileSettingsStore, Settings  # noqa: E402
from tally_bridge.windows import acl, system  # noqa: E402

USERS = "S-1-5-32-545"
AUTHENTICATED = "S-1-5-11"
BOB = "S-1-5-21-1-2-3-1001"
CREATOR_OWNER = "S-1-3-0"
MODIFY = 0x001301BF  # the "Modify" right of Explorer
READ_EXECUTE = 0x001200A9
FULL = 0x001F01FF
ADD_FOLDER = acl.FILE_APPEND_DATA  # "Create folders" on C:\

PROGRAM_FILES = r"C:\Program Files\TallyPrime\tally.exe"
ROOT_INSTALL = r"C:\TallyPrime\tally.exe"


def _locked(path: str) -> acl.Security:
    """The rights of an install under Program Files: admins and SYSTEM full,
    TrustedInstaller the owner, Users read and run."""
    return acl.Security(acl.TRUSTED_INSTALLER, acl.entries([
        (0, 0, FULL, acl.SYSTEM), (0, 0, FULL, acl.ADMINISTRATORS),
        (0, 0, READ_EXECUTE, USERS),
    ]))


def _nothing_beside(folder: str) -> list[str]:
    return []


def _reader(overrides: dict[str, acl.Security]):
    def read(path: str) -> acl.Security:
        return overrides.get(path, _locked(path))  # keys as acl.chain spells them
    return read


def test_the_chain_ends_at_the_drive_root():
    assert acl.chain(PROGRAM_FILES) == [
        PROGRAM_FILES, r"C:\Program Files\TallyPrime", r"C:\Program Files", "C:\\"]


def _drive_root() -> acl.Security:
    """C:\\ of a fresh Windows: Authenticated Users may create folders there
    (this folder only), and get Modify on new subfolders (inherit only)."""
    return acl.Security(acl.TRUSTED_INSTALLER, acl.entries([
        (0, 0, FULL, acl.SYSTEM), (0, 0, FULL, acl.ADMINISTRATORS),
        (0, 0, READ_EXECUTE, USERS),
        (0, 0, ADD_FOLDER, AUTHENTICATED),
        (0, acl.INHERIT_ONLY | 0x03, MODIFY, AUTHENTICATED),
    ]))


# --- The allowed path ------------------------------------------------------------


def test_an_install_under_program_files_is_allowed():
    read = _reader({"C:\\": _drive_root()})
    assert acl.unsafe_component(PROGRAM_FILES, read, _nothing_beside) is None
    acl.check(PROGRAM_FILES, read, _nothing_beside)  # does not raise


def test_create_folders_on_the_drive_root_does_not_count():
    """A user can add a new folder at C:\\, but cannot replace one that exists."""
    assert acl.unsafe_component(PROGRAM_FILES, _reader({"C:\\": _drive_root()}), _nothing_beside) is None


def test_an_inherit_only_entry_does_not_count_for_the_object_itself():
    folder = acl.Security(acl.ADMINISTRATORS, acl.entries([
        (0, 0, FULL, acl.ADMINISTRATORS), (0, acl.INHERIT_ONLY | 0x03, FULL, CREATOR_OWNER),
    ]))
    read = _reader({r"C:\Program Files\TallyPrime": folder})
    assert acl.unsafe_component(PROGRAM_FILES, read, _nothing_beside) is None


# --- The refused path -------------------------------------------------------------


def test_a_root_install_with_inherited_modify_is_refused():
    """The finding: C:\\TallyPrime gets Modify for Authenticated Users."""
    tally = acl.Security(acl.ADMINISTRATORS, acl.entries([
        (0, 0x10, FULL, acl.SYSTEM), (0, 0x10, FULL, acl.ADMINISTRATORS),
        (0, 0x10, MODIFY, AUTHENTICATED),  # 0x10: inherited, applies here
    ]))
    read = _reader({"C:\\": _drive_root(), r"C:\TallyPrime": tally})
    part, reason, *_ = acl.unsafe_component(ROOT_INSTALL, read, _nothing_beside)
    assert part == r"C:\TallyPrime" and AUTHENTICATED in reason
    with pytest.raises(acl.UnsafeTallyFolder) as raised:
        acl.check(ROOT_INSTALL, read, _nothing_beside)
    text = str(raised.value)
    assert r"C:\TallyPrime" in text and "icacls" in text and "Program Files" in text


@pytest.mark.parametrize("mask", [acl.FILE_WRITE_DATA, acl.DELETE, acl.WRITE_DAC,
                                  acl.WRITE_OWNER, acl.GENERIC_WRITE, acl.GENERIC_ALL])
def test_a_user_who_can_change_the_program_file_is_refused(mask):
    exe = acl.Security(acl.ADMINISTRATORS, acl.entries([(0, 0, mask, USERS)]))
    part, *_ = acl.unsafe_component(PROGRAM_FILES, _reader({PROGRAM_FILES: exe}), _nothing_beside)
    assert part == PROGRAM_FILES


def test_a_user_who_can_add_a_file_beside_the_program_is_refused():
    """A DLL beside tally.exe would load into it."""
    folder = acl.Security(acl.ADMINISTRATORS, acl.entries([(0, 0, acl.FILE_WRITE_DATA, BOB)]))
    part, *_ = acl.unsafe_component(PROGRAM_FILES, _reader({r"C:\Program Files\TallyPrime": folder}), _nothing_beside)
    assert part == r"C:\Program Files\TallyPrime"


def test_a_user_who_can_rename_a_folder_above_is_refused():
    above = acl.Security(acl.ADMINISTRATORS, acl.entries([(0, 0, acl.FILE_DELETE_CHILD, BOB)]))
    part, *_ = acl.unsafe_component(PROGRAM_FILES, _reader({r"C:\Program Files": above}), _nothing_beside)
    assert part == r"C:\Program Files"


def test_a_folder_owned_by_a_user_is_refused():
    """An owner can always rewrite the DACL."""
    owned = acl.Security(BOB, acl.entries([(0, 0, FULL, acl.ADMINISTRATORS)]))
    part, reason, *_ = acl.unsafe_component(ROOT_INSTALL, _reader({r"C:\TallyPrime": owned}), _nothing_beside)
    assert part == r"C:\TallyPrime" and BOB in reason


def test_a_null_dacl_or_a_read_error_is_refused():
    def broken(path: str) -> acl.Security:
        raise OSError("access is denied")

    part, reason, *_ = acl.unsafe_component(PROGRAM_FILES, broken, _nothing_beside)
    assert part == PROGRAM_FILES and "cannot be read" in reason


# --- The sign-in task and its two callers ----------------------------------------


def _refuse(path: str) -> None:
    raise acl.UnsafeTallyFolder(r"C:\TallyPrime", f"{AUTHENTICATED} can change the program there")


def test_the_sign_in_task_is_refused_and_an_old_task_is_removed():
    ran: list[list[str]] = []
    with pytest.raises(acl.UnsafeTallyFolder):
        system.apply_startup(True, ROOT_INSTALL, lambda a: ran.append(list(a)) or 0, _refuse)
    assert ran == [system.startup_off()]  # no /Create; the old task goes


def test_the_sign_in_task_is_made_when_the_folder_is_locked():
    ran: list[list[str]] = []
    assert system.apply_startup(True, PROGRAM_FILES, lambda a: ran.append(list(a)) or 0,
                                lambda p: None) == 0
    assert any(a[:2] == ["schtasks", "/Create"] for a in ran)


def test_apply_windows_says_which_folder_and_how_to_lock_it(tmp_path, monkeypatch, capsys):
    from tally_bridge.windows import dpapi

    class FakeProtector:
        def protect(self, data: bytes) -> bytes:
            return data

        def unprotect(self, blob: bytes) -> bytes:
            return blob

    monkeypatch.setattr(dpapi, "DpapiProtector", FakeProtector)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    FileSettingsStore(tmp_path / "TallyBridge" / "settings.json").save(
        Settings(start_tally_at_signin=True, tally_exe=ROOT_INSTALL))
    args = argparse.Namespace(windows=True, settings_file=None, dev_secret_file=None,
                              dev_copy_key_file=None)
    assert cli._apply_windows(args, runner=lambda a: 0, check_folder=_refuse) == 1
    err = capsys.readouterr().err
    assert r"C:\TallyPrime" in err and "icacls" in err


def test_the_setup_page_callback_gives_the_same_note():
    import asyncio

    apply = cli._machine_applier(True, type("A", (), {"start": lambda s: True,
                                                      "stop": lambda s: None})(),
                                 lambda a: 1 if "/Query" in a else 0, _refuse)
    note = asyncio.run(apply(Settings(start_tally_at_signin=True, tally_exe=ROOT_INSTALL)))
    assert note and r"C:\TallyPrime" in note and "icacls" in note


def test_off_this_check_fails_closed_on_linux():
    """The real reader runs on Windows only. Elsewhere it refuses."""
    if sys.platform == "win32":
        pytest.skip("this is the Linux side")
    with pytest.raises(acl.UnsafeTallyFolder):
        acl.check(PROGRAM_FILES)


@pytest.mark.skipif(sys.platform != "win32", reason="the real DACL check runs on Windows only")
def test_the_real_reader_on_windows(tmp_path):
    """windows-latest (PR 2837): System32 is locked; a folder in the profile is not."""
    import os

    acl.check(os.path.join(os.environ["SYSTEMROOT"], "System32", "notepad.exe"))
    mine = tmp_path / "tally.exe"
    mine.write_bytes(b"MZ")
    with pytest.raises(acl.UnsafeTallyFolder):
        acl.check(str(mine))



@pytest.mark.parametrize("error", [ImportError("no pywin32"), RuntimeError("pywintypes.error")])
def test_any_error_of_the_reader_is_refused(error):
    """Verifier: pywin32 raises pywintypes.error, which is not an OSError."""

    def broken(path: str) -> acl.Security:
        raise error

    part, reason, *_ = acl.unsafe_component(PROGRAM_FILES, broken, _nothing_beside)
    assert part == PROGRAM_FILES and "cannot be read" in reason


@pytest.mark.parametrize("ace_type", [5, 9, 11])
def test_an_allowed_callback_or_object_entry_counts_as_a_grant(ace_type):
    exe = acl.Security(acl.ADMINISTRATORS, acl.entries([(ace_type, 0, acl.FILE_WRITE_DATA, BOB)]))
    part, *_ = acl.unsafe_component(PROGRAM_FILES, _reader({PROGRAM_FILES: exe}), _nothing_beside)
    assert part == PROGRAM_FILES


def test_a_deny_entry_alone_grants_nothing():
    exe = acl.Security(acl.ADMINISTRATORS, acl.entries([(1, 0, FULL, BOB)]))
    assert acl.unsafe_component(PROGRAM_FILES, _reader({PROGRAM_FILES: exe}), _nothing_beside) is None



# --- Review round 1 (Opus) ------------------------------------------------------------


def _link() -> acl.Security:
    return acl.Security(acl.ADMINISTRATORS, acl.entries([(0, 0, FULL, acl.ADMINISTRATORS)]),
                        reparse=True)


@pytest.mark.parametrize("where", [ROOT_INSTALL, r"C:\TallyPrime"])
def test_a_link_in_the_chain_is_refused(where):
    r"""Blocker: C:\TallyPrime as a junction to a locked D:\TallyPrime. The read
    follows the link, so the link object itself must be refused: a user can
    delete it and make their own folder there."""
    part, reason, *_ = acl.unsafe_component(ROOT_INSTALL, _reader({where: _link()}),
                                            _nothing_beside)
    assert part == where and "link" in reason


def test_a_file_beside_the_program_that_users_can_change_is_refused():
    """tally.ini names what TallyPrime loads at start; a DLL there loads into it."""
    folder = r"C:\Program Files\TallyPrime"
    ini = folder + r"\tally.ini"
    dll = folder + r"\a.dll"
    loose = acl.Security(acl.ADMINISTRATORS, acl.entries([(0, 0, MODIFY, USERS)]))
    part, *_, near = acl.unsafe_component(
        PROGRAM_FILES, _reader({ini: loose}), lambda f: [PROGRAM_FILES, dll, ini])
    assert part == ini and near is True
    assert acl.unsafe_component(PROGRAM_FILES, _reader({}),
                                lambda f: [PROGRAM_FILES, dll, ini]) is None


def test_a_folder_that_cannot_be_listed_is_refused():
    def broken(folder: str) -> list[str]:
        raise OSError("access is denied")

    part, reason, *_ = acl.unsafe_component(PROGRAM_FILES, _reader({}), broken)
    assert part == r"C:\Program Files\TallyPrime" and "listed" in reason


def test_the_lock_message_sets_the_owner_resets_the_rights_and_warns_about_data():
    text = acl.message(ROOT_INSTALL, "S-1-5-11 can change the program there",
                       r"C:\TallyPrime", True)
    for word in ("/setowner *S-1-5-32-544 /T /C", "/reset /T /C", "/inheritance:r /grant:r",
                 "company data", r'"C:\TallyPrime"'):
        assert word in text
    assert "Or install" not in text.split("/grant:r", 1)[1].split(" . ", 1)[0]


def test_a_folder_above_gets_the_move_advice_and_no_drive_wide_lock():
    text = acl.message(r"C:\Tools", "Bob can rename or replace a folder above it",
                       r"C:\Tools\TallyPrime", False)
    assert "icacls" not in text and "Program Files" in text


def test_the_page_shows_why_the_task_is_off():
    """Review: a refusal at install deletes the task; the page must say so."""
    problem = cli._machine_check(True, _refuse)
    assert r"C:\TallyPrime" in problem(Settings(start_tally_at_signin=True,
                                                tally_exe=ROOT_INSTALL))
    assert cli._machine_check(True, lambda p: None)(
        Settings(start_tally_at_signin=True, tally_exe=PROGRAM_FILES)) is None
    assert cli._machine_check(False) is None


def test_each_start_of_the_service_checks_again_and_removes_the_task():
    """Review: a support person loosened the folder after setup."""
    ran: list[list[str]] = []
    on = Settings(start_tally_at_signin=True, tally_exe=ROOT_INSTALL)
    note = cli._signin_guard_at_start(on, lambda a: ran.append(list(a)) or 0, _refuse)
    assert note and ran == [system.startup_off()]
    ran.clear()
    assert cli._signin_guard_at_start(on, lambda a: ran.append(list(a)) or 0,
                                      lambda p: None) is None
    assert ran == []
    assert cli._signin_guard_at_start(Settings(), lambda a: ran.append(list(a)) or 0,
                                      _refuse) is None
    assert ran == []
