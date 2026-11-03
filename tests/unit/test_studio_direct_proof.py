"""The direct proof of a Studio write, and its fences (WS-STU STU-B2c-1).

Spec: ``project-docs/specs/ai_studio.md`` §20.4 and the §14 B2c-1 row. Code:
``packages/acb_auth/acb_auth/direct_proof.py``. The door that reads it is
``gateway/routes/studio/invoke.py``. The door tests are in
``test_studio_action_door.py`` and ``test_studio_action_door_r8.py``.

This file proves, with no database:

- a proof signs and verifies, and every wrong key, member, path, method, time
  and shape is False;
- a key that is short, equal to a platform secret or unset makes no proof;
- the compare is constant time, and the window ends at 60 s;
- the Python and the TypeScript signers sign one string (a shared vector);
- the fences (R7): only the proof module and the settings read the key, no
  other code names the header, the door writes only for a direct call, the
  audit is in the session of the write, and the door reads the proof before
  the body.

**The mutations (R7).** Each one was applied alone on 2026-11-03, and the first
red tests are named. ``test_studio_action_door.py`` is the hermetic door suite,
and ``test_studio_action_door_r8.py`` is the R8 suite.

* M1. Accept a missing proof (``verify`` answers True for None).
  ``test_a_missing_or_malformed_proof_is_false[None]`` and
  ``test_a_missing_or_wrong_proof_is_403_and_writes_nothing[none-add]``.
* M2. ``==`` in place of ``compare_digest``. ``test_the_compare_is_constant_time``.
* M3. Drop the email from the HMAC. ``test_a_proof_for_another_member_is_false``
  and ``test_the_shared_vector_signs_to_its_header``.
* M4. Drop the path from the HMAC. ``test_a_proof_for_another_path_is_false``.
* M5. Skip the time window. ``test_an_old_proof_is_false``,
  ``test_a_future_proof_is_false`` and ``test_a_replay_outside_the_window_is_false``.
* M6. Skip the ``user.agent`` check.
  ``test_a_valid_proof_with_an_agent_scope_is_403`` (door suite).
* M7. Add create to ``OPEN_EXECUTORS``. ``test_the_direct_set_is_create_update_delete_only``.
* M8. Write the audit with ``record_app_audit_bound``.
  ``test_the_audit_is_in_the_write_session`` and 28 door tests.
* M9. Write the audit after the session closes. The same test, and
  ``test_a_direct_write_runs_and_audits_in_the_same_session`` (door suite).
* M10. Catch ``ObjectStoreError`` before ``StoreBusy`` (door suite).
  ``test_store_busy_is_503_before_object_store_error``.
* M11. Drop the admission cap. ``test_the_admission_caps_are_three_and_six``
  and ``test_the_fourth_writer_of_an_org_is_503_and_holds_no_session`` (door suite).
* M12. Raise ``HTTPException`` inside the session block (loses the audit row).
  23 door tests, among them ``test_a_body_with_other_keys_is_422_and_writes_nothing``.
* M13. Strip a computed key in place of refusing it. Only the R8 test
  ``test_r8_a_computed_field_is_refused_and_nothing_is_written`` goes red,
  because the fake store of the door suite refuses nothing.
* M14. Echo a value in a refusal. 12 door tests, among them
  ``test_a_body_with_other_keys_is_422_and_writes_nothing``.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from acb_auth import direct_proof
from acb_common import get_settings
from gateway.routes.studio import invoke

ROOT = Path(__file__).resolve().parents[2]
VECTOR = json.loads(
    (ROOT / "tests/fixtures/studio_direct_proof_vector.json").read_text(encoding="utf-8"))
KEY = VECTOR["key"]
EMAIL = "ana@example.test"
PATH = "/studio/apps/6f1f7a3e-2b0f-4f43-a3a5-0d3f8f6f9c11/actions/add"
NOW = 1_790_000_000.0


def _signed(**over: object) -> str:
    args = {"key": KEY, "email": EMAIL, "method": "POST", "path": PATH, "now": NOW}
    args.update(over)
    return direct_proof.sign(**args)  # type: ignore[arg-type]


def _check(value: str | None, **over: object) -> bool:
    args = {"key": KEY, "email": EMAIL, "method": "POST", "path": PATH, "now": NOW}
    args.update(over)
    return direct_proof.verify(
        value, args["key"], args["email"], args["method"], args["path"],  # type: ignore[arg-type]
        now=args["now"])  # type: ignore[arg-type]


# ── The round trip ──────────────────────────────────────────────────────────


def test_a_proof_signs_and_verifies() -> None:
    assert _check(_signed())


def test_the_email_and_the_method_are_normalised() -> None:
    assert _check(_signed(email="  ANA@Example.TEST "))
    assert _check(_signed(method="post"))


def test_a_proof_for_another_member_is_false() -> None:
    assert not _check(_signed(), email="ben@example.test")


def test_a_proof_for_another_path_is_false() -> None:
    assert not _check(_signed(), path=PATH.replace("/add", "/remove"))


def test_a_proof_for_another_method_is_false() -> None:
    assert not _check(_signed(), method="GET")
    assert not _check(_signed(method="PATCH"))


def test_a_proof_under_another_key_is_false() -> None:
    assert not _check(_signed(), key=KEY + "x")


def test_an_old_proof_is_false() -> None:
    assert _check(_signed(), now=NOW + direct_proof.WINDOW_S)
    assert not _check(_signed(), now=NOW + direct_proof.WINDOW_S + 1)


def test_a_future_proof_is_false() -> None:
    assert _check(_signed(), now=NOW - direct_proof.SKEW_S)
    assert not _check(_signed(), now=NOW - direct_proof.SKEW_S - 1)


def test_a_replay_outside_the_window_is_false() -> None:
    header = _signed()
    assert _check(header, now=NOW + 30)
    assert not _check(header, now=NOW + 3600)


@pytest.mark.parametrize("value", [
    None, "", "v1", "v1.", "v1..", "v2.1790000000." + "a" * 64,
    "v1.1790000000." + "a" * 63, "v1.1790000000." + "A" * 64,
    "v1.-5." + "a" * 64, "v1.1790000000." + "a" * 64 + "x", " v1.1790000000." + "a" * 64,
    "v1.1790000000." + "g" * 64, "v1.17900000000000." + "a" * 64,
])
def test_a_missing_or_malformed_proof_is_false(value: str | None) -> None:
    assert not _check(value)


def test_no_email_and_no_key_are_false() -> None:
    header = _signed()
    assert not _check(header, email=None)
    assert not _check(header, email="")
    assert not _check(header, key=None)
    assert not _check(header, key="")


def test_the_compare_is_constant_time(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(direct_proof.hmac, "compare_digest", spy)
    assert _check(_signed())
    assert not _check(_signed(), email="ben@example.test")
    assert len(calls) == 2
    assert "==" not in inspect.getsource(direct_proof.verify)


def test_the_header_name_is_x_studio_direct() -> None:
    assert direct_proof.HEADER == "X-Studio-Direct"
    assert direct_proof.WINDOW_S == 60


# ── The key ────────────────────────────────────────────────────────────────


def _set_key(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_direct_key", value)


def test_a_usable_key_is_read_from_the_settings(monkeypatch) -> None:
    _set_key(monkeypatch, KEY)
    assert direct_proof.usable_key() == KEY
    assert direct_proof.is_direct_proof(_signed(now=None), EMAIL, "POST", PATH)


def test_an_unset_key_makes_no_proof(monkeypatch) -> None:
    _set_key(monkeypatch, "")
    assert direct_proof.usable_key() is None
    assert not direct_proof.is_direct_proof(_signed(now=None), EMAIL, "POST", PATH)


def test_a_short_key_counts_as_unset(monkeypatch) -> None:
    short = "k" * (direct_proof.MIN_KEY_BYTES - 1)
    _set_key(monkeypatch, short)
    assert direct_proof.usable_key() is None
    _set_key(monkeypatch, "k" * direct_proof.MIN_KEY_BYTES)
    assert direct_proof.usable_key() is not None


def test_a_key_equal_to_a_platform_secret_counts_as_unset(monkeypatch) -> None:
    settings = get_settings()
    token = "t" * 40
    monkeypatch.setattr(settings, "gateway_internal_token", token)
    _set_key(monkeypatch, token)
    assert direct_proof.usable_key() is None
    monkeypatch.setattr(settings, "gateway_internal_token", "other" * 10)
    monkeypatch.setattr(settings, "litellm_master_key", token)
    assert direct_proof.usable_key() is None


def test_a_refusal_never_echoes_a_value() -> None:
    """The door's 422 text comes from the store and the rules, which promise to
    echo no value. The door adds only fixed sentences."""
    for text in (invoke.BAD_WRITE_INPUT, invoke.STORE_REFUSED, invoke.BUSY_MESSAGE):
        assert "{" not in text and "%" not in text
        assert text.endswith(".") and len(text.split()) <= 25


# ── The vector shared with TypeScript ──────────────────────────────────────


def test_the_shared_vector_signs_to_its_header() -> None:
    signed = direct_proof.sign(
        VECTOR["key"], VECTOR["email"], VECTOR["method"], VECTOR["path"], now=VECTOR["now"])
    assert signed == VECTOR["header"]
    mac = hmac.new(
        VECTOR["key"].encode(),
        f"v1\n{VECTOR['now']}\n{VECTOR['email'].lower()}\nPOST\n{VECTOR['path']}".encode(),
        hashlib.sha256).hexdigest()
    assert VECTOR["header"] == f"v1.{VECTOR['now']}.{mac}"
    assert direct_proof.verify(
        VECTOR["header"], VECTOR["key"], VECTOR["email"], "POST", VECTOR["path"],
        now=VECTOR["now"] + 1)


# ── The fences ─────────────────────────────────────────────────────────────

_CODE_DIRS = ("apps", "packages", "skills", "agents.py")
_SKIP = {"node_modules", ".venv", "__pycache__", ".next", "dist", "build"}


def _sources(*suffixes: str):
    for top in _CODE_DIRS:
        base = ROOT / top
        files = [base] if base.is_file() else base.rglob("*") if base.exists() else []
        for path in files:
            if path.suffix in suffixes and path.is_file() and not (_SKIP & set(path.parts)):
                yield path


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_direct_set_is_create_update_delete_only() -> None:
    assert {("object", "create"), ("object", "update"),
            ("object", "delete")} == invoke.DIRECT_EXECUTORS
    assert {("object", "get"), ("object", "list")} == invoke.OPEN_EXECUTORS
    assert not invoke.DIRECT_EXECUTORS & invoke.OPEN_EXECUTORS


def test_only_the_proof_module_and_settings_read_the_direct_key() -> None:
    allowed = {"packages/acb_auth/acb_auth/direct_proof.py",
               "packages/acb_common/acb_common/settings.py"}
    pattern = re.compile(r"studio_direct_key|STUDIO_DIRECT_KEY", re.I)
    readers = {_rel(p) for p in _sources(".py") if pattern.search(p.read_text(encoding="utf-8"))}
    assert readers <= allowed, sorted(readers - allowed)
    # No skill, no orchestrator and no skill app may name it, whatever is allowed.
    for rel in readers:
        assert not rel.startswith(("packages/acb_skills", "apps/skills")), rel
        assert "orchestrator" not in rel, rel


def test_no_delegated_caller_sends_the_direct_header() -> None:
    allowed = {"packages/acb_auth/acb_auth/direct_proof.py"}
    pattern = re.compile(r"x-studio-direct", re.I)
    found = {_rel(p) for p in _sources(".py", ".ts", ".tsx")
             if pattern.search(p.read_text(encoding="utf-8"))}
    assert found <= allowed, sorted(found - allowed)


def test_the_door_reads_the_proof_before_the_body() -> None:
    src = inspect.getsource(invoke.invoke_action)
    assert src.count("_is_direct(") == 1
    assert src.index("_is_direct(") < src.index("_run_executor(")
    assert "_read_body(" not in src
    run = inspect.getsource(invoke._run_executor)
    assert "_is_direct(" not in run
    whole = Path(invoke.__file__).read_text(encoding="utf-8")
    assert whole.count("_direct_proof.is_direct_proof(") == 1


def test_the_direct_path_is_the_only_door_write() -> None:
    whole = Path(invoke.__file__).read_text(encoding="utf-8")
    assert whole.count("apply_writes(") == 1
    assert "apply_writes(" in inspect.getsource(invoke._do_object_write)
    assert whole.count("await _do_object_write(") == 1
    assert whole.count("await _run_object_write(") == 1
    run = inspect.getsource(invoke._run_executor)
    guard = run.index("if not (direct and admitted):")
    assert guard < run.index("await _run_object_write(")
    # The only caller of the write is the branch that DIRECT_EXECUTORS names.
    assert run.index("in DIRECT_EXECUTORS:") < guard
    for name in ("create_record(", "update_record(", "delete_record("):
        assert name not in whole


def test_the_audit_is_in_the_write_session() -> None:
    src = inspect.getsource(invoke._run_object_write)
    assert "record_app_audit_in(db," in src
    assert "record_app_audit_bound" not in src
    assert "_tenant_session" not in src
    assert "record_app_audit_bound" not in inspect.getsource(invoke._do_object_write)
    # The bound writer runs once, and only for a call that wrote no audit row.
    final = inspect.getsource(invoke.invoke_action)
    assert final.count("record_app_audit_bound(") == 1
    assert "if not out.audited:" in final


def test_the_door_holds_no_sql_of_the_four_tables() -> None:
    whole = Path(invoke.__file__).read_text(encoding="utf-8")
    for table in ("studio_records", "studio_objects", "studio_record_hot",
                  "studio_object_fields"):
        assert table not in whole


def test_the_admission_caps_are_three_and_six() -> None:
    assert (invoke.WRITERS_PER_ORG, invoke.WRITERS_TOTAL) == (3, 6)


# ── The deploy line ────────────────────────────────────────────────────────

_SYNC_START = 'echo "==> Reconciling the workbench\'s STUDIO_DIRECT_KEY'
_SYNC_END = 'echo "==> Reconciling the workbench\'s DEPLOY_ENV'


def _sync_block() -> str:
    text = (ROOT / "scripts/vps_apply.sh").read_text(encoding="utf-8")
    start, end = text.index(_SYNC_START), text.index(_SYNC_END)
    return text[start:end]


def test_the_deploy_line_copies_the_key_and_never_makes_one() -> None:
    block = _sync_block()
    assert "STUDIO_DIRECT_KEY" in block
    for forbidden in ("openssl", "token_urlsafe", "/dev/urandom", "uuidgen"):
        assert forbidden not in block
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^STUDIO_DIRECT_KEY=\s*$", example, re.M)


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash on this box")
@pytest.mark.parametrize("env,local,expect", [
    ("STUDIO_DIRECT_KEY=abc\n", "OTHER=1\n", ["OTHER=1", "STUDIO_DIRECT_KEY=abc"]),
    ("STUDIO_DIRECT_KEY=abc\n", "STUDIO_DIRECT_KEY=old\nOTHER=1\n",
     ["OTHER=1", "STUDIO_DIRECT_KEY=abc"]),
    ("STUDIO_DIRECT_KEY=abc\n", "STUDIO_DIRECT_KEY=abc\nOTHER=1\n",
     ["STUDIO_DIRECT_KEY=abc", "OTHER=1"]),
    ("OTHER=2\n", "STUDIO_DIRECT_KEY=stale\nOTHER=1\n", ["OTHER=1"]),
    ("OTHER=2\n", "OTHER=1\n", ["OTHER=1"]),
    ("STUDIO_DIRECT_KEY=\n", "STUDIO_DIRECT_KEY=stale\n", []),
    # Fix round 1: systemd and dotenv take the LAST line of a key, so the copy
    # takes the last line too. With the first line, a copy of the first value
    # read as "already in sync".
    ("STUDIO_DIRECT_KEY=first\nSTUDIO_DIRECT_KEY=abc\n", "STUDIO_DIRECT_KEY=first\nOTHER=1\n",
     ["OTHER=1", "STUDIO_DIRECT_KEY=abc"]),
    ("STUDIO_DIRECT_KEY=first\nOTHER=2\nSTUDIO_DIRECT_KEY=abc\n", "OTHER=1\n",
     ["OTHER=1", "STUDIO_DIRECT_KEY=abc"]),
])
def test_the_deploy_line_syncs_the_key_from_env_to_env_local(tmp_path, env, local, expect) -> None:
    (tmp_path / "env").write_text(env, encoding="utf-8")
    (tmp_path / "local").write_text(local, encoding="utf-8")
    script = tmp_path / "block.sh"
    script.write_text(_sync_block(), encoding="utf-8", newline="\n")
    run = subprocess.run(
        [shutil.which("bash") or "bash", script.name],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        env={**os.environ, "ENV_FILE": "env", "WB_ENV": "local"})
    assert run.returncode == 0, run.stderr
    lines = (tmp_path / "local").read_text(encoding="utf-8").split()
    assert sorted(lines) == sorted(expect)
    assert "abc" not in run.stdout  # the key never reaches the log
