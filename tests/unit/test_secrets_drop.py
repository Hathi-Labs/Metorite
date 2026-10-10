"""The secrets drop: scripts/secrets.sh and deploy/secrets/manifest.json.

The owner asked for it on 2026-10-08: put each key in a file in a folder on
the dev PC, and let one command check it, back up the old file on the box,
write the new one and verify it. docs/secrets_drop.md is the reference.

Every case runs the REAL script. `ssh`, `sudo`, `chown`, `stat`, `systemctl`,
`curl`, `journalctl`, `timeout` and (where it says so) `gpg` are exported bash
functions, the idiom of test_backup_deploy_wiring.py. The ssh stub runs the
remote half in a local "box" folder, so the remote script runs for real too.
Each stub, and each external tool that the script calls, writes its argv to a
calls log. The ssh stub also keeps everything that the box sent back.

🔴 The fence of the whole design is the canary test. A unique value goes into
every secret, through every command, every refusal and the rollback, and it
must never show in the output, in an argv, in a log, in what the box sends
back, or in a file on the box other than the target and its backups.

Every case runs with a temp HOME. None of them reads or writes the real drop
folder.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts/secrets.sh"
_MANIFEST = _ROOT / "deploy/secrets/manifest.json"

CANARY = "cAnArYq7Z3xW9vK2"
FPR40 = "0123456789ABCDEF0123456789ABCDEF01234567"

#: The host env must not reach the script under test.
_HOST_ENV_PREFIXES = (
    "METORITE_", "BACKUP_", "GNUPG", "STUB_", "BASH_ENV", "BASH_FUNC_", "SSH_",
)
_HOST_ENV_NAMES = frozenset({"ENV", "CDPATH", "GLOBIGNORE", "SHELLOPTS", "BASHOPTS", "W", "BOX", "CALLS"})


def _hermetic_env() -> dict[str, str]:
    return {
        k: v for k, v in os.environ.items()
        if k.upper() not in _HOST_ENV_NAMES and not k.upper().startswith(_HOST_ENV_PREFIXES)
    }


# The stubs. `BOX` stands in for the root of the box: the ssh stub maps
# /etc/acb and /opt/acb under it. chown records the owner, chmod records the
# mode, and `stat -c '%U:%G %a'` reads both back, because a test is not root.
#
# systemctl: STUB_INACTIVE fails every is-active, STUB_INACTIVE_ONCE fails the
# first only. NRestarts of STUB_NRESTARTS_UNIT starts to grow at read number
# STUB_NRESTARTS_FROM. curl answers STUB_HEALTH_CODE (200 when unset).
_STUBS = r"""
set -u
umask 077
export MSYS=winsymlinks:nativestrict
W="$(mktemp -d)"
export W
export HOME="$W/home"; mkdir -p "$HOME"
export BOX="$W/box"; mkdir -p "$BOX/opt/acb/app"
export CALLS="$W/calls.log"; : > "$CALLS"
: > "$W/remote.out"
D="$HOME/.metorite/secrets"
rec() { local n="$1"; shift; { printf '%s' "$n"; printf ' %q' "$@"; printf '\n'; } >> "$CALLS"; }
ssh() {
  rec ssh "$@"
  local cmd
  while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) shift; break ;; esac; done
  cmd="$*"
  # Map only an argument that STARTS with the path. A backup path that the
  # box sent back already holds $BOX, and must not be mapped twice.
  cmd="${cmd// \/etc\/acb/ $BOX/etc/acb}"
  cmd="${cmd// \/opt\/acb/ $BOX/opt/acb}"
  if [ -n "${STUB_SSH_FAILS:-}" ]; then echo "ssh: connect to host: Connection refused" >&2; return 255; fi
  if [ -n "${STUB_CORRUPT:-}" ]; then
    command sed 's/=./=Z/' | bash -c "$cmd" | command tee -a "$W/remote.out"
  else
    bash -c "$cmd" | command tee -a "$W/remote.out"
  fi
}
_mkey() { printf '%s' "$1" | command tr '/:' '__'; }
sudo() { rec sudo "$@"; if [ "${1:-}" = -n ]; then shift; fi; "$@"; }
chown() { rec chown "$@"; printf '%s' "$1" > "$W/meta.own.$(_mkey "$2")"; }
chmod() { rec chmod "$@"; command chmod "$@"; if [ "$#" = 2 ]; then printf '%s' "${1#0}" > "$W/meta.mode.$(_mkey "$2")"; fi; }
stat() {
  if [ "${1:-}" = -c ] && [ "${2:-}" = '%U:%G %a' ]; then
    local k; k="$(_mkey "$3")"
    printf '%s %s\n' "$(command cat "$W/meta.own.$k" 2>/dev/null || echo "${STUB_OWNER:-acb:acb}")" \
      "$(command cat "$W/meta.mode.$k" 2>/dev/null || echo "${STUB_MODE:-600}")"
    return 0
  fi
  if [ "${1:-}" = -c ] && [ "${2:-}" = '%a' ] && [ -n "${STUB_LOCAL_MODE:-}" ]; then echo "$STUB_LOCAL_MODE"; return 0; fi
  command stat "$@"
}
systemctl() {
  rec systemctl "$@"
  local u="${!#}" n=0
  case "$1" in
    is-active)
      if [ -n "${STUB_INACTIVE:-}" ]; then return 3; fi
      if [ -n "${STUB_INACTIVE_ONCE:-}" ] && [ ! -e "$W/inactive.once" ]; then : > "$W/inactive.once"; return 3; fi ;;
    show)
      if [ -f "$W/reads.$u" ]; then n="$(command cat "$W/reads.$u")"; fi
      n=$((n + 1)); printf '%s' "$n" > "$W/reads.$u"
      if [ "${STUB_NRESTARTS_UNIT:-}" = "$u" ] && [ "$n" -ge "${STUB_NRESTARTS_FROM:-1}" ]; then
        echo "$((n - ${STUB_NRESTARTS_FROM:-1} + 1))"
      else
        echo 0
      fi ;;
    start) if [ -n "${STUB_START_FAILS:-}" ]; then return 1; fi ;;
  esac
  return 0
}
curl() { rec curl "$@"; local c="${STUB_HEALTH_CODE:-200}"; printf '%s' "$c"; [ "$c" = 200 ] || return 22; }
journalctl() { rec journalctl "$@"; printf '%b' "${STUB_JOURNAL:-}"; }
timeout() { rec timeout "$@"; shift; "$@"; }
sleep() { :; }
uname() { if [ -n "${STUB_UNAME:-}" ]; then echo "$STUB_UNAME"; else command uname "$@"; fi; }
export -f rec ssh _mkey sudo chown chmod stat systemctl curl journalctl timeout sleep uname
# Each external tool records its argv, so the canary test sees every argv.
for c in cat cp mv mktemp sha256sum base64 awk sed grep cut tr head tail sort wc date dirname basename shred dd seq find rm; do
  eval "$c() { rec $c \"\$@\"; command $c \"\$@\"; }"
  export -f "$c"
done
# A fixed backup stamp, for the cases that plant a file at the backup name.
fixed_stamp() {
  date() { rec date "$@"; if [ "${1:-}" = -u ] && [ "${2:-}" = +%Y%m%dT%H%M%SZ ]; then echo 20260101T000000Z; else command date "$@"; fi; }
  export -f date
}
"""

# A stub gpg. A file that holds "PRIVATE" or "STUB-SECRET" shows as a secret
# key, and "STUB-BOTH" shows as a public AND a secret key.
_GPG_STUB = r"""
gpg() {
  rec gpg "$@"
  local a last="" op=""
  for a in "$@"; do
    case "$a" in
      --import|--list-keys|--export|--export-secret-keys|--quick-gen-key|--quick-add-key) op="$op $a" ;;
    esac
    last="$a"
  done
  case "$op" in
    *--import*)
      if command grep -q STUB-BOTH "$last"; then
        echo "pub:u:255:22:AAAA:::::::"; echo "sec:u:255:22:CCCC:::::::"
      elif command grep -q PRIVATE "$last" || command grep -q STUB-SECRET "$last"; then
        echo "sec:u:255:22:AAAA:::::::"; echo "ssb:u:255:18:BBBB:::::::"
      else
        echo "pub:u:255:22:AAAA:::::::"; echo "sub:u:255:18:BBBB:::::::"
      fi ;;
    *--list-keys*) echo "pub:u:255:22:AAAA:::::::"; echo "fpr:::::::::0123456789ABCDEF0123456789ABCDEF01234567:" ;;
    *--export-secret-keys*) printf -- '-----BEGIN PGP PRIVATE KEY BLOCK-----\nstub\n-----END PGP PRIVATE KEY BLOCK-----\n' ;;
    *--export*) printf -- '-----BEGIN PGP PUBLIC KEY BLOCK-----\nstub\n-----END PGP PUBLIC KEY BLOCK-----\n' ;;
  esac
  return 0
}
gpgconf() { :; }
export -f gpg gpgconf
"""

_PUBLIC_BLOCK = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\nstub\\n-----END PGP PUBLIC KEY BLOCK-----\\n"
_OK_JOURNAL = "start\\n    off-box copy ok (offbox:metorite-backups/nightly/20261008T023012Z, 5 files, 84M)\\ndone\\n"


def _fixture_repo(manifest: dict) -> str:
    """A copy of the script beside a fixture manifest, as bash text."""
    body = json.dumps(manifest, indent=2, ensure_ascii=True)
    return (
        'mkdir -p "$W/repo/scripts" "$W/repo/deploy/secrets"\n'
        'command cp scripts/secrets.sh "$W/repo/scripts/secrets.sh"\n'
        "command cat > \"$W/repo/deploy/secrets/manifest.json\" <<'JSON'\n"
        + body
        + "\nJSON\n"
        'SD() { bash "$W/repo/scripts/secrets.sh" "$@"; }\n'
    )


def _run(body: str, *, manifest: dict | None = None, gpg_stub: bool = True, timeout: int = 180) -> dict:
    """Run `body` after the stubs. SD runs the script. Returns the exit code,
    stdout, stderr, the calls log, push.log, what the box sent back, and every
    file under the box and the temp home as {relative path: bytes}."""
    repo = _fixture_repo(manifest) if manifest else 'SD() { bash scripts/secrets.sh "$@"; }\n'
    prog = (
        _STUBS
        + (_GPG_STUB if gpg_stub else "")
        + repo
        + body
        + "\nrc=$?\n"
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'command cat "$CALLS" >&2\n'
        + 'printf "\\n===LOG===\\n" >&2\n'
        + 'command cat "$D/push.log" >&2 2>/dev/null\n'
        + 'printf "\\n===REMOTE===\\n" >&2\n'
        + 'command cat "$W/remote.out" >&2\n'
        + 'printf "\\n===FILES===\\n" >&2\n'
        + '(cd "$W" && command find box home -type f | command sort | while IFS= read -r f; do '
        + 'printf "%s\\t%s\\n" "$f" "$(command base64 < "$f" | command tr -d "\\n")"; done) >&2\n'
        + 'command rm -rf "$W"\n'
        + "exit $rc\n"
    )
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=timeout, cwd=_ROOT,
        env=_hermetic_env(),
    )
    err = run.stderr.decode(errors="replace")
    err, _, rest = err.partition("\n===CALLS===\n")
    calls, _, rest = rest.partition("\n===LOG===\n")
    log, _, rest = rest.partition("\n===REMOTE===\n")
    remote_out, _, files = rest.partition("\n===FILES===\n")
    tree = {}
    for ln in files.splitlines():
        if "\t" in ln:
            path, b64 = ln.split("\t", 1)
            tree[path] = base64.b64decode(b64)
    return {
        "rc": run.returncode,
        "out": run.stdout.decode(errors="replace"),
        "err": err,
        "calls": calls,
        "log": log,
        "remote": remote_out,
        "files": tree,
    }


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def _entry(m: dict, name: str) -> dict:
    return next(e for e in m["entries"] if e["name"] == name)


def _merge_manifest(restart: list[str] | None = None, health: bool = True) -> dict:
    """The real manifest, with app-env given two keys to merge."""
    m = _manifest()
    e = _entry(m, "app-env")
    e["allowed_keys"] = ["FOO_TOKEN", "BAR_KEY"]
    e["required_keys"] = ["FOO_TOKEN"]
    e["validators"] = {"BAR_KEY": ["non_empty"]}
    if restart is not None:
        e["restart"] = restart
        e["health"] = {u: v for u, v in e.get("health", {}).items() if u in restart}
    if not health:
        e.pop("health", None)
    return m


def _offbox_env(**over: str | None) -> str:
    """A filled backup-offbox.env, as a bash command that writes it."""
    vals: dict[str, str | None] = {
        "BACKUP_S3_ENDPOINT": "https://wbjpwtxigkileyjsgahk.storage.supabase.co/storage/v1/s3",
        "BACKUP_S3_REGION": "ap-south-1",
        "BACKUP_S3_BUCKET": "metorite-backups",
        "BACKUP_S3_ACCESS_KEY_ID": "AKID" + CANARY,
        "BACKUP_S3_SECRET_ACCESS_KEY": "sec" + CANARY + "+/=",
        "BACKUP_GPG_RECIPIENT": FPR40,
        "BACKUP_GPG_PUBLIC_KEY_FILE": "/opt/acb/backup-public-key.asc",
    }
    vals.update(over)
    lines = "".join(f"{k}={v}\\n" for k, v in vals.items() if v is not None)
    return f"printf '{lines}' > \"$D/backup-offbox.env\"\n"


_MERGE_SETUP = (
    "SD init >/dev/null\nprintf 'FOO_TOKEN=abc\\n' > \"$D/app.env\"\n"
    "printf 'KEEP=1\\nFOO_TOKEN=old\\n' > \"$BOX/opt/acb/app/.env\"\n"
)


def _box(res: dict, path: str) -> bytes | None:
    return res["files"].get("box" + path)


def _cmd_lines(calls: str, name: str) -> list[str]:
    """The calls of one command. The sudo stub records the whole remote
    script as one argv, so a plain substring test would match its text."""
    return [ln for ln in calls.splitlines() if ln.startswith(name + " ")]


def _ssh_lines(calls: str) -> list[str]:
    return _cmd_lines(calls, "ssh")


def _verb_calls(calls: str, verb: str) -> list[str]:
    """The ssh calls of one remote verb. %q writes a space as a backslash
    and a space."""
    return [ln for ln in _ssh_lines(calls) if f"secrets-drop\\ {verb}\\ " in ln]


def _no_write(res: dict) -> None:
    assert not _verb_calls(res["calls"], "write"), "the script reached the write step"
    assert not [p for p in res["files"] if p.startswith("box/etc/acb/")]


def _bash_function(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} moved; update this test"
    return m.group(0)


# ── The manifest ──────────────────────────────────────────────────────────────


def _flatten(node: object, path: str = "") -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if isinstance(node, dict):
        out.append((path, "object", ""))
        for k, v in node.items():
            out += _flatten(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        out.append((path, "array", ""))
        for i, v in enumerate(node):
            out += _flatten(v, f"{path}.{i}" if path else str(i))
    elif isinstance(node, bool):
        out.append((path, "bool", "true" if node else "false"))
    elif node is None:
        out.append((path, "null", ""))
    elif isinstance(node, (int, float)):
        out.append((path, "number", json.dumps(node)))
    else:
        out.append((path, "string", str(node)))
    return out


def _json_awk() -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"read -r -d '' json_awk <<'AWK' \|\| true\n(.*?)\nAWK\n", text, re.S)
    assert m, "the awk JSON reader moved; update this test"
    return m.group(1)


def test_the_manifest_reader_agrees_with_json() -> None:
    """The script reads JSON with awk, so it needs no jq. This fence keeps the
    awk reader and Python's json module in step on the real manifest."""
    prog = (
        'TMPDIR_AWK="$(mktemp)"\n'
        "cat > \"$TMPDIR_AWK\" <<'AWK'\n" + _json_awk() + "\nAWK\n"
        'awk -f "$TMPDIR_AWK" deploy/secrets/manifest.json\n'
        'rc=$?; rm -f "$TMPDIR_AWK"; exit $rc\n'
    )
    run = subprocess.run(["bash"], input=prog.encode(), capture_output=True, cwd=_ROOT, timeout=60)
    assert run.returncode == 0, run.stderr.decode()
    got = [tuple(ln.split("\t", 2)) for ln in run.stdout.decode().splitlines()]
    assert got == _flatten(_manifest())


def test_the_manifest_ships_the_three_entries() -> None:
    m = _manifest()
    # The order is the order of push --all: the public key goes before the key
    # file, so the post-push check of backup-offbox finds both on the box.
    assert [e["name"] for e in m["entries"]] == ["backup-gpg-public", "backup-offbox", "app-env"]

    off = _entry(m, "backup-offbox")
    seven = {
        "BACKUP_S3_ENDPOINT", "BACKUP_S3_REGION", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY", "BACKUP_GPG_RECIPIENT", "BACKUP_GPG_PUBLIC_KEY_FILE",
    }
    assert (off["kind"], off["remote_path"]) == ("env-file", "/etc/acb/backup-offbox.env")
    assert (off["owner"], off["group"], off["mode"]) == ("root", "root", "0600")
    # The optional names live in the root file only (WS-49 BH-6a): the backup
    # reads them from nowhere else, so the drop must be able to write them.
    optional = {
        "BACKUP_S3_PREFIX", "BACKUP_S3_KEEP", "BACKUP_S3_TIMEOUT_SECS", "BACKUP_REMOTE",
        "BACKUP_PG_HOST", "BACKUP_PG_USER_SUFFIX", "BACKUP_CC_PG_HOST", "BACKUP_CC_PG_USER_SUFFIX",
    }
    assert set(off["allowed_keys"]) == seven | optional
    assert set(off["required_keys"]) == seven
    assert off["restart"] == []
    assert off["validators"]["BACKUP_GPG_RECIPIENT"] == ["gpg_fingerprint"]
    assert off["prefill"] == {
        "BACKUP_S3_ENDPOINT": "https://wbjpwtxigkileyjsgahk.storage.supabase.co/storage/v1/s3",
        "BACKUP_S3_BUCKET": "metorite-backups",
        "BACKUP_GPG_PUBLIC_KEY_FILE": "/opt/acb/backup-public-key.asc",
        "BACKUP_S3_REGION": "ap-south-1",
    }
    assert "check in dashboard" in off["key_notes"]["BACKUP_S3_REGION"].lower()
    # The match is the S3 copy's own line. backup_db.sh's rsync path prints
    # a bare "off-box copy ok", and that must not pass this check.
    assert off["post_push"]["start_unit"] == "acb-backup"
    assert off["post_push"]["journal_match"] == "off-box copy ok (offbox:"
    offbox_sh = (_ROOT / "scripts/backup_offbox.sh").read_text(encoding="utf-8")
    assert 'echo "    off-box copy ok ($night,' in offbox_sh
    assert "$night" in offbox_sh and "offbox_base" in offbox_sh

    pub = _entry(m, "backup-gpg-public")
    assert (pub["kind"], pub["remote_path"], pub["owner"], pub["mode"]) == (
        "file", "/opt/acb/backup-public-key.asc", "root", "0644",
    )
    assert pub["validators"] == {"file": ["gpg_public_key_only"]}
    assert pub["restart"] == []

    app = _entry(m, "app-env")
    assert (app["kind"], app["remote_path"], app["owner"]) == ("env-merge", "/opt/acb/app/.env", "acb")
    # Both units load the app env file (deploy/hostinger/*.service).
    assert app["restart"] == ["acb-gateway", "acb-whatsapp-bridge"]
    assert app["health"] == {"acb-gateway": "http://127.0.0.1:8080/health"}
    # The list is a reviewed allowlist, one key at a time. WS-47 WAC-0
    # (2026-10-10) added the WhatsApp bot's System User token, and nothing else.
    assert app["allowed_keys"] == ["WHATSAPP_ASSISTANT_ACCESS_TOKEN"], (
        "app-env holds only reviewed keys, so it cannot be used by accident"
    )
    assert app["validators"] == {"WHATSAPP_ASSISTANT_ACCESS_TOKEN": ["non_empty"]}
    # A BACKUP_ line in the app env is an ERROR of the backup (H-123, BH-6a),
    # and the gateway's Copilot CLI inherits that env (H-270).
    assert not [k for k in app["allowed_keys"] if k.startswith("BACKUP_")]
    for unit in app["restart"]:
        text = (_ROOT / f"deploy/hostinger/{unit}.service").read_text(encoding="utf-8")
        assert "EnvironmentFile=/opt/acb/app/.env" in text, unit


def test_a_bad_manifest_fails_loudly() -> None:
    m = _manifest()
    _entry(m, "backup-offbox")["validators"]["BACKUP_S3_REGION"] = ["no_such_check"]
    _entry(m, "app-env")["remote_path"] = "/opt/acb/../etc/shadow"
    _entry(m, "app-env")["health"] = {"acb-gateway": "http://evil.example/health"}
    _entry(m, "backup-gpg-public")["restart"] = "acb-gateway"
    res = _run("SD status\n", manifest=m)
    assert res["rc"] != 0
    assert "no_such_check is not a validator" in res["err"]
    assert "remote_path must be an absolute path" in res["err"]
    assert "the health URL of acb-gateway must be http://127.0.0.1 or localhost" in res["err"]
    assert "restart must be a list of unit names" in res["err"]
    assert "ssh " not in res["calls"]


def test_the_script_parses() -> None:
    run = subprocess.run(["bash", "-n", "scripts/secrets.sh"], cwd=_ROOT, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr.decode()


# ── init ──────────────────────────────────────────────────────────────────────


def test_init_creates_templates_and_never_overwrites() -> None:
    res = _run(
        "SD init || exit 1\n"
        "printf 'MINE\\n' > \"$D/app.env\"\n"
        "printf 'MY README\\n' > \"$D/README.txt\"\n"
        "SD init || exit 1\n"
        "command stat -c '%a' \"$D\" > \"$HOME/drop-mode\"\n"
    )
    assert res["rc"] == 0, res["err"]
    files = res["files"]
    tpl = files["home/.metorite/secrets/backup-offbox.env"].decode()
    lines = dict(ln.split("=", 1) for ln in tpl.splitlines() if ln and not ln.startswith("#"))
    off = _entry(_manifest(), "backup-offbox")
    assert list(lines) == off["allowed_keys"], "every allowed NAME, in order"
    for k, v in lines.items():
        assert v == off["prefill"].get(k, ""), k
    assert "check in dashboard" in tpl.lower()
    assert files["home/.metorite/secrets/app.env"] == b"MINE\n"
    assert files["home/.metorite/secrets/README.txt"] == b"MY README\n"
    assert "kept     app.env" in res["out"]
    # A file entry gets no empty template: an empty .asc helps nobody.
    assert "home/.metorite/secrets/backup-gpg-public.asc" not in files
    if os.name != "nt":
        assert files["home/drop-mode"].strip() == b"700"


def test_the_readme_says_what_each_file_is_for() -> None:
    res = _run("SD init\n")
    readme = res["files"]["home/.metorite/secrets/README.txt"].decode()
    for name in ("backup-offbox.env", "backup-gpg-public.asc", "app.env",
                 "backup-gpg-PRIVATE.asc", "backup-gpg-passphrase.txt"):
        assert name in readme, name
    assert "/etc/acb/backup-offbox.env" in readme


# ── The refusals ──────────────────────────────────────────────────────────────


def test_an_unknown_key_is_refused() -> None:
    body = (
        "SD init >/dev/null\n"
        + _offbox_env()
        + "printf 'BACKUP_S3_ACESS_KEY_ID=typo\\n' >> \"$D/backup-offbox.env\"\n"
        + "SD push backup-offbox --yes\n"
    )
    res = _run(body)
    assert res["rc"] != 0
    assert "BACKUP_S3_ACESS_KEY_ID is not an allowed key of backup-offbox" in res["err"]
    _no_write(res)


def test_a_missing_required_key_is_refused() -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env(BACKUP_S3_SECRET_ACCESS_KEY="") + "SD push backup-offbox --yes\n")
    assert res["rc"] != 0
    assert "BACKUP_S3_SECRET_ACCESS_KEY is required, and it is missing or empty" in res["err"]
    _no_write(res)


@pytest.mark.parametrize(
    ("key", "value", "words"),
    [
        ("BACKUP_GPG_RECIPIENT", "ABCDEF", "BACKUP_GPG_RECIPIENT fails the check gpg_fingerprint"),
        ("BACKUP_GPG_RECIPIENT", "0" * 41, "BACKUP_GPG_RECIPIENT fails the check gpg_fingerprint"),
        ("BACKUP_S3_ENDPOINT", "http://plain.example/s3", "BACKUP_S3_ENDPOINT fails the check https_url"),
        ("BACKUP_S3_SECRET_ACCESS_KEY", "has space", "BACKUP_S3_SECRET_ACCESS_KEY: it holds a space"),
        ("BACKUP_S3_SECRET_ACCESS_KEY", "a$(id)", "BACKUP_S3_SECRET_ACCESS_KEY: it holds a character that the shell reads"),
    ],
)
def test_a_failed_validator_is_refused(key: str, value: str, words: str) -> None:
    # Drop the good line first, so the key is set one time.
    body = (
        "SD init >/dev/null\n"
        + _offbox_env(**{key: None})
        + f"printf '%s\\n' '{key}={value}' >> \"$D/backup-offbox.env\"\n"
        + "SD push backup-offbox --yes\n"
    )
    res = _run(body)
    assert res["rc"] != 0
    assert words in res["err"]
    _no_write(res)


@pytest.mark.parametrize("fpr", [FPR40, FPR40.lower(), "A" * 64])
def test_a_v4_or_v5_fingerprint_passes(fpr: str) -> None:
    """40 hex digits (v4) or 64 (v5), the same as backup_offbox.sh accepts."""
    res = _run("SD init >/dev/null\n" + _offbox_env(BACKUP_GPG_RECIPIENT=fpr) + "SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    assert "([0-9A-F]{40}|[0-9A-F]{64})" in (_ROOT / "scripts/backup_offbox.sh").read_text(encoding="utf-8")


def test_a_key_set_two_times_is_refused() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "printf 'BACKUP_S3_REGION=eu-west-1\\n' >> \"$D/backup-offbox.env\"\n"
        + "SD push backup-offbox --yes\n"
    )
    assert res["rc"] != 0
    assert "BACKUP_S3_REGION is set more than one time" in res["err"]
    _no_write(res)


def test_the_public_key_validator_rejects_a_private_key() -> None:
    private = "-----BEGIN PGP PRIVATE KEY BLOCK-----\\nx\\n-----END PGP PRIVATE KEY BLOCK-----\\n"
    # A file that SAYS public, and that gpg shows as a secret key.
    disguised = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\nSTUB-SECRET\\n-----END PGP PUBLIC KEY BLOCK-----\\n"
    # A file that gpg shows as a public key AND a secret key.
    both = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\nSTUB-BOTH\\n-----END PGP PUBLIC KEY BLOCK-----\\n"
    for content in (private, disguised, both, "not a key\\n"):
        res = _run(
            "SD init >/dev/null\n"
            f"printf -- '{content}' > \"$D/backup-gpg-public.asc\"\n"
            "SD push backup-gpg-public --yes\n"
        )
        assert res["rc"] != 0, content
        assert "fails the check gpg_public_key_only" in res["err"], content
        assert _box(res, "/opt/acb/backup-public-key.asc") is None
    # The text check stands alone: a public block AND a private block, with a
    # gpg that reports only the public key, is still refused.
    res = _run(
        "SD init >/dev/null\n"
        f"printf -- '{_PUBLIC_BLOCK}{private}' > \"$D/backup-gpg-public.asc\"\n"
        "gpg() { rec gpg \"$@\"; echo 'pub:u:255:22:AAAA:::::::'; }; export -f gpg\n"
        "SD push backup-gpg-public --yes\n"
    )
    assert res["rc"] != 0
    assert "fails the check gpg_public_key_only" in res["err"]
    assert _box(res, "/opt/acb/backup-public-key.asc") is None
    res = _run(
        "SD init >/dev/null\n"
        f"printf -- '{_PUBLIC_BLOCK}' > \"$D/backup-gpg-public.asc\"\n"
        "SD push backup-gpg-public --yes\n"
    )
    assert res["rc"] == 0, res["err"]
    assert _box(res, "/opt/acb/backup-public-key.asc") == _PUBLIC_BLOCK.replace("\\n", "\n").encode()
    assert "chown root:root" in res["calls"] and "chmod 0644" in res["calls"]


def test_a_group_readable_file_is_refused_on_posix() -> None:
    body = "SD init >/dev/null\n" + _offbox_env()
    res = _run(body + "STUB_UNAME=Linux STUB_LOCAL_MODE=644 SD push backup-offbox --yes\n")
    assert res["rc"] != 0
    assert "the group or others can read it" in res["err"]
    _no_write(res)
    res = _run(body + "STUB_UNAME=Linux STUB_LOCAL_MODE=600 SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    res = _run(body + "STUB_UNAME=MINGW64_NT-10.0 STUB_LOCAL_MODE=644 SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    assert "WARNING: Windows has no file mode to check" in res["err"]


def test_push_without_yes_and_without_a_terminal_changes_nothing() -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env() + "SD push backup-offbox < /dev/null\n")
    assert res["rc"] != 0
    assert "add --yes" in res["err"]
    assert "+ BACKUP_S3_SECRET_ACCESS_KEY" in res["out"], "it shows the diff before it asks"
    _no_write(res)


# ── Symbolic links ────────────────────────────────────────────────────────────

_SKIP_NO_LINK = 'command ln -s "$1" "$2" 2>/dev/null && [ -L "$2" ] || { echo SKIP-NO-SYMLINK; exit 0; }\n'


def _link(target: str, link: str) -> str:
    return f"mklink() {{ {_SKIP_NO_LINK} }}\nmklink \"{target}\" \"{link}\"\n"


def _skip_if_no_link(res: dict) -> None:
    if "SKIP-NO-SYMLINK" in res["out"]:
        pytest.skip("this bash cannot make a symbolic link")


def test_a_symlink_at_the_target_is_refused() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "mkdir -p \"$BOX/etc/acb\"; printf 'ELSEWHERE\\n' > \"$W/elsewhere\"\n"
        + _link("$W/elsewhere", "$BOX/etc/acb/backup-offbox.env")
        + "SD push backup-offbox --yes\n"
    )
    _skip_if_no_link(res)
    assert res["rc"] != 0
    assert "backup-offbox.env is a symbolic link. The script refuses it." in res["err"]
    assert not _verb_calls(res["calls"], "write")


def test_a_symlink_at_the_backup_name_is_refused() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env() + "fixed_stamp\n"
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_S3_REGION=old\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + "printf 'ELSEWHERE\\n' > \"$W/elsewhere\"\n"
        + _link("$W/elsewhere", "$BOX/etc/acb/backup-offbox.env.bak-20260101T000000Z")
        + "SD push backup-offbox --yes\n"
    )
    _skip_if_no_link(res)
    assert res["rc"] != 0
    assert "backup-offbox.env.bak-20260101T000000Z is a symbolic link" in res["err"]
    assert _box(res, "/etc/acb/backup-offbox.env") == b"BACKUP_S3_REGION=old\n"
    assert res["files"].get("elsewhere") is None


def test_an_existing_backup_name_is_refused_and_never_suffixed() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env() + "fixed_stamp\n"
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_S3_REGION=old\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + "printf 'EARLIER\\n' > \"$BOX/etc/acb/backup-offbox.env.bak-20260101T000000Z\"\n"
        + "SD push backup-offbox --yes\n"
    )
    assert res["rc"] != 0
    assert "Wait one second, then push again" in res["err"]
    assert _box(res, "/etc/acb/backup-offbox.env.bak-20260101T000000Z") == b"EARLIER\n"
    assert _box(res, "/etc/acb/backup-offbox.env") == b"BACKUP_S3_REGION=old\n"
    assert not [p for p in res["files"] if re.search(r"\.bak-\d{8}T\d{6}Z\.\d+$", p)]


# ── The write ─────────────────────────────────────────────────────────────────


def test_push_streams_through_stdin_not_argv() -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env() + "SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    on_box = _box(res, "/etc/acb/backup-offbox.env")
    assert on_box is not None and f"BACKUP_S3_SECRET_ACCESS_KEY=sec{CANARY}+/=\n".encode() in on_box
    writes = _verb_calls(res["calls"], "write")
    assert len(writes) == 1
    assert CANARY not in res["calls"], "a value reached an argv"
    # The remote script is on argv as base64, and it holds no value either.
    b64 = re.search(r"printf\\ %s\\ ([A-Za-z0-9+/=]+)", writes[0])
    assert b64, writes[0]
    assert CANARY not in base64.b64decode(b64.group(1)).decode()
    assert "chown root:root" in res["calls"] and "chmod 0600" in res["calls"]
    assert "wrote: /etc/acb/backup-offbox.env (root:root 600)" in res["out"]


def test_a_backup_is_made_before_the_write() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_S3_REGION=old\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + "SD push backup-offbox --yes\n"
    )
    assert res["rc"] == 0, res["err"]
    baks = {p: b for p, b in res["files"].items() if p.startswith("box/etc/acb/backup-offbox.env.bak-")}
    assert list(baks.values()) == [b"BACKUP_S3_REGION=old\n"]
    assert re.fullmatch(r"box/etc/acb/backup-offbox\.env\.bak-\d{8}T\d{6}Z", next(iter(baks)))
    calls = res["calls"].splitlines()
    bak_at = next(i for i, ln in enumerate(calls) if ln.startswith("mv -f -T ") and ".bak-" in ln)
    swap_at = next(
        i for i, ln in enumerate(calls)
        if ln.startswith("mv -f -T ") and ln.endswith("backup-offbox.env") and ".bak-" not in ln
    )
    assert bak_at < swap_at, "the backup must come before the swap"
    assert "backup: /" in res["out"] and ".bak-" in res["out"]
    assert not [p for p in res["files"] if ".secrets-drop." in p], "a temp file was left"


def test_only_the_newest_three_backups_stay() -> None:
    plant = "".join(
        f"printf 'old{n}\\n' > \"$BOX/etc/acb/backup-offbox.env.bak-2025010{n}T000000Z\"\n" for n in range(1, 6)
    )
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_S3_REGION=old\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + plant
        + "printf 'other\\n' > \"$BOX/etc/acb/backup-offbox.env.bak-20250101T000000Z.1234\"\n"
        + "printf 'other\\n' > \"$BOX/etc/acb/other.env.bak-20250101T000000Z\"\n"
        + "SD push backup-offbox --yes\n"
    )
    assert res["rc"] == 0, res["err"]
    baks = sorted(p for p in res["files"] if p.startswith("box/etc/acb/backup-offbox.env.bak-"))
    stamped = [p for p in baks if re.search(r"\.bak-\d{8}T\d{6}Z$", p)]
    assert len(stamped) == 3, baks
    assert stamped[:2] == [
        "box/etc/acb/backup-offbox.env.bak-20250104T000000Z",
        "box/etc/acb/backup-offbox.env.bak-20250105T000000Z",
    ]
    # A name that is not <path>.bak-<stamp> is not ours to delete.
    assert "box/etc/acb/backup-offbox.env.bak-20250101T000000Z.1234" in baks
    assert "box/etc/acb/other.env.bak-20250101T000000Z" in res["files"]
    assert "pruned: 3" in res["out"]


def test_a_hash_mismatch_after_the_write_fails() -> None:
    res = _run(_MERGE_SETUP + "STUB_CORRUPT=1 SD push app-env --yes\n", manifest=_merge_manifest())
    assert res["rc"] != 0
    assert "FOO_TOKEN: the hash on the box does not match" in res["err"]
    assert "the verify failed, so no unit was restarted" in res["err"]
    assert not _cmd_lines(res["calls"], "systemctl")
    assert "result=verify-failed" in res["log"]

    res = _run("SD init >/dev/null\n" + _offbox_env() + "STUB_CORRUPT=1 SD push backup-offbox --yes\n")
    assert res["rc"] != 0
    assert "the file hash on the box does not match" in res["err"]


def test_a_wrong_owner_after_the_write_fails() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "chown() { rec chown \"$@\"; printf 'acb:acb' > \"$W/meta.own.$(_mkey \"$2\")\"; }; export -f chown\n"
        + "SD push backup-offbox --yes\n"
    )
    assert res["rc"] != 0
    assert "the manifest says 'root:root 600'" in res["err"]


def test_env_merge_keeps_the_inode_of_the_app_env() -> None:
    """WS-49 BH-2 fix round 1, P2-1. The gateway sandbox bind-mounts the
    inode of /opt/acb/app/.env. A merge that renamed a new file into place
    would leave the running gateway on the old, unlinked copy. So the merge
    writes INTO the file (r_put, `dd oflag=nofollow`), and the inode stays."""
    res = _run(
        _MERGE_SETUP
        + 'command stat -c %i "$BOX/opt/acb/app/.env" > "$W/ino.before"\n'
        + "SD push app-env --yes\n"
        + 'echo "INODES $(command cat "$W/ino.before") $(command stat -c %i "$BOX/opt/acb/app/.env")"\n',
        manifest=_merge_manifest(),
    )
    assert res["rc"] == 0, res["err"]
    line = next(ln for ln in res["out"].splitlines() if ln.startswith("INODES "))
    _, before, after = line.split()
    assert before == after, "the merge replaced the inode of the app env"
    assert "R put in-place" in res["remote"], res["remote"]
    assert _box(res, "/opt/acb/app/.env") == b"KEEP=1\nFOO_TOKEN=abc\n"
    dd = _cmd_lines(res["calls"], "dd")
    assert dd and all("oflag=nofollow" in ln for ln in dd), dd
    assert not [p for p in res["files"] if ".secrets-drop." in p], "a temp file was left"


def test_env_merge_keeps_the_other_lines_byte_for_byte() -> None:
    before = (
        b"# the app env\r\n"
        b"OTHER=keep me as I am\r\n"
        b"export FOO_TOKEN=old\n"
        b"  INDENTED='quoted value'\n"
        b"FOO_TOKEN=a-later-duplicate\n"
        b"# FOO_TOKEN=a comment stays\n"
        b"\n"
        b"LAST=1"
    )
    after = (
        b"# the app env\r\n"
        b"OTHER=keep me as I am\r\n"
        b"export FOO_TOKEN=new1\n"
        b"  INDENTED='quoted value'\n"
        b"# FOO_TOKEN=a comment stays\n"
        b"\n"
        b"LAST=1\n"
        b"BAR_KEY=new2\n"
    )
    plant = base64.b64encode(before).decode()
    res = _run(
        "SD init >/dev/null\nprintf 'FOO_TOKEN=new1\\r\\nBAR_KEY=new2\\r\\n' > \"$D/app.env\"\n"
        f"printf '%s' '{plant}' | command base64 -d > \"$BOX/opt/acb/app/.env\"\n"
        "SD push app-env --yes\n",
        manifest=_merge_manifest(),
    )
    assert res["rc"] == 0, res["err"]
    assert _box(res, "/opt/acb/app/.env") == after
    baks = [b for p, b in res["files"].items() if p.startswith("box/opt/acb/app/.env.bak-")]
    assert baks == [before]
    assert "+ BAR_KEY" in res["out"] and "~ FOO_TOKEN" in res["out"]
    assert "chown acb:acb" in res["calls"]


def test_env_merge_needs_the_env_file_to_exist() -> None:
    res = _run(
        "SD init >/dev/null\nprintf 'FOO_TOKEN=abc\\n' > \"$D/app.env\"\nSD push app-env --yes\n",
        manifest=_merge_manifest(),
    )
    assert res["rc"] != 0
    assert "A merge needs the env file to exist" in res["err"]
    assert _box(res, "/opt/acb/app/.env") is None


def test_the_shipped_app_env_refuses_every_key() -> None:
    res = _run("SD init >/dev/null\nprintf 'OPENAI_API_KEY=x\\n' > \"$D/app.env\"\nSD push app-env --yes\n")
    assert res["rc"] != 0
    assert "OPENAI_API_KEY is not an allowed key of app-env" in res["err"]
    assert not _verb_calls(res["calls"], "write")


def test_the_probe_hashes_only_the_allowed_keys() -> None:
    """A probe of the app env file must not hash a value that the manifest
    does not manage. For a whole file, the other key NAMES come back, with
    no hash, because a push removes them."""
    res = _run(
        "SD init >/dev/null\nprintf 'FOO_TOKEN=abc\\n' > \"$D/app.env\"\n"
        "printf 'OPENAI_API_KEY=sk-other\\nFOO_TOKEN=old\\nDATABASE_URL=pg://x\\n' > \"$BOX/opt/acb/app/.env\"\n"
        "SD diff app-env\n",
        manifest=_merge_manifest(),
    )
    assert res["rc"] == 0, res["err"]
    keys = [ln.split()[2] for ln in res["remote"].splitlines() if ln.startswith("R key ")]
    assert keys == ["FOO_TOKEN"]
    assert "OPENAI_API_KEY" not in res["remote"] and "DATABASE_URL" not in res["remote"]

    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        # A name the manifest does not manage. (This was BACKUP_S3_KEEP until
        # WS-49 BH-6a made that one an allowed key of the root file.)
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_LEGACY_KNOB=3\\nBACKUP_S3_REGION=ap-south-1\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + "SD diff backup-offbox\n"
    )
    assert res["rc"] == 0, res["err"]
    assert "R other BACKUP_LEGACY_KNOB\n" in res["remote"]
    assert not [ln for ln in res["remote"].splitlines() if ln.startswith("R key BACKUP_LEGACY_KNOB")]
    assert "  - BACKUP_LEGACY_KNOB" in res["out"]
    assert "  + BACKUP_S3_SECRET_ACCESS_KEY" in res["out"]
    assert "= 1 key(s) the same" in res["out"]


def test_the_ssh_host_comes_from_one_place() -> None:
    res = _run("SD init >/dev/null\nSD status backup-offbox\n")
    assert all(ln.split()[8] == "metorite" for ln in _ssh_lines(res["calls"])), res["calls"]
    res = _run("SD init >/dev/null\nMETORITE_SSH_HOST=acb@stand-in SD status backup-offbox\n")
    assert _ssh_lines(res["calls"]) and all(
        ln.split()[8] == "acb@stand-in" for ln in _ssh_lines(res["calls"])
    ), res["calls"]
    res = _run("SD init >/dev/null\nMETORITE_SSH_HOST=-oProxyCommand=x SD status\n")
    assert res["rc"] != 0 and "METORITE_SSH_HOST is not a host name" in res["err"]
    assert not _ssh_lines(res["calls"])


# ── The restart, and the rollback ─────────────────────────────────────────────


def test_the_restart_runs_only_when_the_manifest_sets_one() -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env() + "SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    assert not _cmd_lines(res["calls"], "systemctl")
    assert "restart: none" in res["out"]

    res = _run(_MERGE_SETUP + "SD push app-env --yes\n", manifest=_merge_manifest())
    assert res["rc"] == 0, res["err"]
    sysd = _cmd_lines(res["calls"], "systemctl")
    assert [ln for ln in sysd if " restart " in ln] == [
        "systemctl restart acb-gateway", "systemctl restart acb-whatsapp-bridge",
    ]
    for unit in ("acb-gateway", "acb-whatsapp-bridge"):
        assert f"systemctl is-active --quiet {unit}" in sysd
        assert f"systemctl show -p NRestarts --value {unit}" in sysd
    curls = _cmd_lines(res["calls"], "curl")
    assert curls and all(ln.endswith("http://127.0.0.1:8080/health") for ln in curls)
    assert "restart: acb-gateway is healthy, and http://127.0.0.1:8080/health answers 200" in res["out"]
    assert "restart: acb-whatsapp-bridge is healthy" in res["out"]

    res = _run(_MERGE_SETUP + "SD push app-env --yes\n", manifest=_merge_manifest(restart=[]))
    assert res["rc"] == 0, res["err"]
    assert not _cmd_lines(res["calls"], "systemctl")


@pytest.mark.parametrize(
    ("env", "words", "healthy_again"),
    [
        ("STUB_INACTIVE_ONCE=1", "acb-gateway is not active", True),
        ("STUB_INACTIVE=1", "acb-gateway is not active", False),
        # NRestarts grows between the read after the restart and the read
        # after the settle: the unit crashed and came back by itself.
        ("STUB_NRESTARTS_UNIT=acb-gateway STUB_NRESTARTS_FROM=2", "acb-gateway restarted by itself", False),
        # It grows only after the health check: the second read catches it.
        ("STUB_NRESTARTS_UNIT=acb-gateway STUB_NRESTARTS_FROM=3", "acb-gateway restarted by itself", False),
        # A unit with no health URL: only the first NRestarts read can see it.
        ("STUB_NRESTARTS_UNIT=acb-whatsapp-bridge STUB_NRESTARTS_FROM=2",
         "acb-whatsapp-bridge restarted by itself", False),
        ("STUB_HEALTH_CODE=503", "acb-gateway did not answer 200 at http://127.0.0.1:8080/health", False),
    ],
)
def test_a_unit_that_does_not_stay_healthy_is_rolled_back(env: str, words: str, healthy_again: bool) -> None:
    res = _run(_MERGE_SETUP + f"{env} SD push app-env --yes\n", manifest=_merge_manifest())
    assert res["rc"] == 2, res["err"]
    assert "ERROR: ROLLED BACK." in res["err"]
    assert words in res["err"]
    assert _box(res, "/opt/acb/app/.env") == b"KEEP=1\nFOO_TOKEN=old\n", "the old file is back"
    assert len(_verb_calls(res["calls"], "rollback")) == 1
    restarts = [ln for ln in _cmd_lines(res["calls"], "systemctl") if ln.startswith("systemctl restart acb-gateway")]
    assert len(restarts) == 2, "it restarts again with the old file"
    assert "rollback: the old file is back, hash" in res["out"]
    assert ("the units are healthy with the old file" in res["err"]) == healthy_again
    assert ("NOT healthy with the old file either" in res["err"]) == (not healthy_again)
    assert "result=rolled-back" in res["log"]
    assert "result=ok" not in res["log"]


def test_a_rollback_of_a_new_file_removes_it() -> None:
    m = _manifest()
    _entry(m, "backup-gpg-public")["restart"] = ["acb-gateway"]
    res = _run(
        "SD init >/dev/null\n"
        f"printf -- '{_PUBLIC_BLOCK}' > \"$D/backup-gpg-public.asc\"\n"
        "STUB_INACTIVE_ONCE=1 SD push backup-gpg-public --yes\n",
        manifest=m,
    )
    assert res["rc"] == 2, res["err"]
    assert _box(res, "/opt/acb/backup-public-key.asc") is None
    assert "the new file is gone" in res["out"]


# ── push --all and the post-push check ────────────────────────────────────────


def test_push_all_pushes_in_order_and_skips_what_is_not_ready() -> None:
    res = _run(
        "SD init >/dev/null\nSD gen backup-gpg >/dev/null\n"
        # The template still has empty S3 keys: backup-offbox is skipped.
        "SD push --all --yes; echo \"first=$?\"\n"
        + _offbox_env()
        + "SD push --all --yes; echo \"second=$?\"\n"
    )
    assert res["rc"] == 0, res["err"]
    first, _, second = res["out"].partition("first=")
    assert "== push backup-gpg-public" in first
    assert "skip backup-offbox: these required keys are empty: BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY" in first
    assert "skip app-env: the file holds no value to push." in first
    assert "0\n" in second[:3]
    order = [ln for ln in second.splitlines() if ln.startswith("== push ")]
    assert order == ["== push backup-gpg-public", "== push backup-offbox"]
    assert "in-sync: nothing to push." in second
    assert "second=0" in res["out"]
    assert _box(res, "/etc/acb/backup-offbox.env") is not None
    assert _box(res, "/opt/acb/backup-public-key.asc") is not None
    # Without --verify the long check does not run, and the script says how to run it.
    assert not [ln for ln in _cmd_lines(res["calls"], "systemctl") if " start " in ln]
    assert "scripts/secrets.sh push backup-offbox --verify" in res["out"]


def test_push_all_stops_at_the_first_failure() -> None:
    res = _run(
        "SD init >/dev/null\nSD gen backup-gpg >/dev/null\n" + _offbox_env()
        + "STUB_CORRUPT=1 SD push --all --yes\n"
    )
    # The stub public key holds no "=", so the corrupt stream leaves it whole.
    # The key file is the first entry that the corruption breaks.
    assert res["rc"] != 0
    assert "== push backup-gpg-public" in res["out"]
    assert "push --all stopped at backup-offbox" in res["err"]
    assert "app-env" not in res["out"], "it went on after the failure"


def test_the_post_push_check_passes_and_redacts() -> None:
    journal = (
        "ERROR: rclone used key sec" + CANARY + "+/= on the way\\n"
        "WARN: retry with AKID" + CANARY + "\\n" + _OK_JOURNAL
    )
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + f"STUB_JOURNAL='{journal}' SD push backup-offbox --yes --verify\n"
    )
    assert res["rc"] == 0, res["err"]
    starts = [ln for ln in _cmd_lines(res["calls"], "timeout")]
    assert starts == ["timeout 1900 systemctl start acb-backup"]
    assert "post-push check: PASS" in res["out"]
    assert "[REDACTED:BACKUP_S3_SECRET_ACCESS_KEY]" in res["out"]
    assert "[REDACTED:BACKUP_S3_ACCESS_KEY_ID]" in res["out"]
    assert CANARY not in res["out"] + res["err"]
    assert CANARY not in res["remote"], "the box sent a value back"
    assert "result=post-push-pass" in res["log"]
    # In sync, --verify still runs the check.
    res = _run(
        "SD init >/dev/null\n" + _offbox_env() + "SD push backup-offbox --yes >/dev/null\n"
        + f"STUB_JOURNAL='{_OK_JOURNAL}' SD push backup-offbox --verify\n"
    )
    assert res["rc"] == 0, res["err"]
    assert "in-sync: nothing to push." in res["out"] and "post-push check: PASS" in res["out"]


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ("STUB_JOURNAL='start\\nERROR: the bucket refused\\n'", "no ok line"),
        # The rsync path of backup_db.sh prints a bare "off-box copy ok".
        ("STUB_JOURNAL='    off-box copy ok\\n'", "the rsync line is not the S3 line"),
        (f"STUB_START_FAILS=1 STUB_JOURNAL='{_OK_JOURNAL}'", "the unit failed"),
    ],
)
def test_the_post_push_check_fails(env: str, why: str) -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env() + f"{env} SD push backup-offbox --yes --verify\n")
    assert res["rc"] != 0, why
    assert "post-push check: FAIL" in res["err"], why
    assert "result=post-push-fail" in res["log"]


def test_the_local_redaction_layer_on_its_own() -> None:
    """The box redacts first. This is the second layer, tested alone."""
    prog = (
        "declare -A sd_val=([S3_SECRET]='sec" + CANARY + "' [SHORT]='ab')\n"
        + _bash_function("redact")
        + "redact 'a sec" + CANARY + " b ab c'\n"
    )
    run = subprocess.run(["bash"], input=prog.encode(), capture_output=True, timeout=30)
    assert run.stdout.decode() == "a [REDACTED:S3_SECRET] b ab c\n"


# ── status and diff ───────────────────────────────────────────────────────────


def _states(out: str) -> dict[str, str]:
    return {ln.split()[0]: ln.split()[1] for ln in out.splitlines() if ln.strip()}


def test_status_reports_each_state_and_never_prints_a_value() -> None:
    m = _merge_manifest()
    res = _run(
        "SD init >/dev/null\n"
        "printf 'FOO_TOKEN=box%s\\n' " + CANARY + " > \"$BOX/opt/acb/app/.env\"\n"
        "SD status; echo ---\n"
        + _offbox_env()
        + "SD status backup-offbox; echo ---\n"
        "SD push backup-offbox --yes >/dev/null 2>&1\n"
        "SD status backup-offbox; echo ---\n"
        "printf 'BACKUP_S3_SECRET_ACCESS_KEY=other" + CANARY + "\\n' > \"$HOME/x\"\n"
        "command sed -i '/SECRET_ACCESS_KEY/d' \"$D/backup-offbox.env\"; command cat \"$HOME/x\" >> \"$D/backup-offbox.env\"\n"
        "SD status backup-offbox; echo ---\n"
        "SD diff backup-offbox\n",
        manifest=m,
    )
    assert res["rc"] == 0, res["err"]
    parts = res["out"].split("---\n")
    assert _states(parts[0]) == {
        "backup-gpg-public": "missing-local",
        "backup-offbox": "local-only",  # the template holds the prefilled values
        "app-env": "remote-only",
    }
    assert _states(parts[1]) == {"backup-offbox": "local-only"}
    assert _states(parts[2]) == {"backup-offbox": "in-sync"}
    assert _states(parts[3]) == {"backup-offbox": "differs"}
    assert "~ BACKUP_S3_SECRET_ACCESS_KEY" in parts[4]
    assert "= 6 key(s) the same" in parts[4]
    assert CANARY not in res["out"] + res["err"]
    for ln in parts[2].splitlines():
        assert re.fullmatch(r"\S+\s+\S+\s+local [0-9a-f-]{1,8}\s+remote [0-9a-f-]{1,8}\s*", ln), ln


def test_status_says_unreachable_when_ssh_fails() -> None:
    res = _run("SD init >/dev/null\nSTUB_SSH_FAILS=1 SD status\n")
    assert res["rc"] != 0
    assert "unreachable" in res["out"]


# ── gen and forget ────────────────────────────────────────────────────────────


def test_gen_with_a_stub_gpg_fills_only_the_recipient() -> None:
    res = _run(
        "SD init >/dev/null\n"
        "command cp \"$D/backup-offbox.env\" \"$HOME/before\"\n"
        "SD gen backup-gpg || exit 1\n"
        "SD gen backup-gpg; echo \"second=$?\"\n"
    )
    assert res["rc"] == 0, res["err"]
    f = res["files"]
    before = f["home/before"].decode().splitlines()
    after = f["home/.metorite/secrets/backup-offbox.env"].decode().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [("BACKUP_GPG_RECIPIENT=", f"BACKUP_GPG_RECIPIENT={FPR40}")]
    assert "second=1" in res["out"]
    assert "SHREDS the old private key and passphrase" in res["err"]
    assert "Copy backup-gpg-PRIVATE.asc and backup-gpg-passphrase.txt into your password manager" in res["out"]
    assert len(f["home/.metorite/secrets/backup-gpg-passphrase.txt"].strip()) >= 32
    assert not [p for p in f if ".gnupg-gen." in p], "the temp GNUPGHOME was left behind"


def test_gen_force_shreds_the_old_private_key_and_keeps_only_the_old_public_key() -> None:
    res = _run(
        "SD init >/dev/null\nSD gen backup-gpg >/dev/null || exit 1\n"
        "printf 'OLD-PRIVATE\\n' > \"$D/backup-gpg-PRIVATE.asc\"\n"
        "printf 'OLD-PASS\\n' > \"$D/backup-gpg-passphrase.txt\"\n"
        "SD gen backup-gpg --force || exit 1\n"
    )
    assert res["rc"] == 0, res["err"]
    f = res["files"]
    olds = sorted(p for p in f if ".old-" in p)
    assert len(olds) == 1 and olds[0].startswith("home/.metorite/secrets/backup-gpg-public.asc.old-"), olds
    assert b"OLD-PRIVATE" not in b"".join(f.values())
    assert b"OLD-PASS" not in b"".join(f.values())
    assert "shredded the old backup-gpg-PRIVATE.asc" in res["out"]
    assert [ln for ln in _cmd_lines(res["calls"], "shred") if "backup-gpg-PRIVATE.asc" in ln]


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg is not installed")
def test_gen_with_real_gpg_makes_a_usable_key_pair() -> None:
    res = _run(
        "SD init >/dev/null\n"
        "command cp \"$D/backup-offbox.env\" \"$HOME/before\"\n"
        "SD gen backup-gpg || exit 1\n"
        # The public file must pass the push check, with real gpg.
        "SD push backup-gpg-public --yes || exit 1\n"
        "SD gen backup-gpg --force || exit 1\n",
        gpg_stub=False,
    )
    assert res["rc"] == 0, res["err"]
    f = res["files"]
    pub = f["home/.metorite/secrets/backup-gpg-public.asc"].decode()
    priv = f["home/.metorite/secrets/backup-gpg-PRIVATE.asc"].decode()
    assert pub.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert priv.startswith("-----BEGIN PGP PRIVATE KEY BLOCK-----")
    fprs = re.findall(r"fingerprint: ([0-9A-F]{40})", res["out"])
    assert len(fprs) == 2 and fprs[0] != fprs[1]
    env = f["home/.metorite/secrets/backup-offbox.env"].decode()
    before = f["home/before"].decode().splitlines()
    assert [(a, b) for a, b in zip(before, env.splitlines(), strict=True) if a != b] == [
        ("BACKUP_GPG_RECIPIENT=", f"BACKUP_GPG_RECIPIENT={fprs[1]}")
    ]
    # --force kept only the old PUBLIC key.
    olds = [p for p in f if ".old-" in p]
    assert len(olds) == 1 and "backup-gpg-public.asc.old-" in olds[0], olds
    assert not [p for p in f if ".gnupg-" in p], "a temp GNUPGHOME was left behind"


def test_forget_deletes_the_private_pair_its_old_copies_and_gpg_leftovers() -> None:
    res = _run(
        "SD init >/dev/null\nSD gen backup-gpg >/dev/null || exit 1\n"
        # What the first version of `gen --force` left, and what a stopped gen leaves.
        "printf 'OLD\\n' > \"$D/backup-gpg-PRIVATE.asc.old-20261008T000000Z\"\n"
        "printf 'OLD\\n' > \"$D/backup-gpg-passphrase.txt.old-20261008T000000Z\"\n"
        "mkdir -p \"$D/.gnupg-gen.AbC123/private-keys-v1.d\"\n"
        "printf 'KEY\\n' > \"$D/.gnupg-gen.AbC123/private-keys-v1.d/x.key\"\n"
        "printf 'PUB\\n' > \"$D/backup-gpg-public.asc.old-20261008T000000Z\"\n"
        "SD forget backup-gpg-private < /dev/null; echo \"noyes=$?\"\n"
        "SD forget backup-gpg-private --yes || exit 1\n"
        "SD forget no-such-thing --yes; echo \"unknown=$?\"\n"
    )
    assert res["rc"] == 0, res["err"]
    assert "noyes=1" in res["out"] and "unknown=1" in res["out"]
    f = res["files"]
    left = sorted(p.removeprefix("home/.metorite/secrets/") for p in f if p.startswith("home/.metorite/secrets/"))
    assert not [p for p in left if "PRIVATE" in p or "passphrase" in p or ".gnupg-gen." in p], left
    assert "backup-gpg-public.asc" in left
    assert "backup-gpg-public.asc.old-20261008T000000Z" in left, "the public key is not a secret"
    assert "forgot: backup-gpg-PRIVATE.asc" in res["out"]
    assert "forgot: backup-gpg-PRIVATE.asc.old-20261008T000000Z" in res["out"]
    assert "forgot: .gnupg-gen.AbC123/ (a leftover gpg folder)" in res["out"]
    shredded = [ln for ln in _cmd_lines(res["calls"], "shred") if "x.key" in ln]
    assert shredded, "the key file in the leftover folder was not shredded"


# ── The canary ────────────────────────────────────────────────────────────────


def test_the_canary_never_appears_in_output_argv_or_logs() -> None:
    """Every command, every refusal and the rollback, with a canary in every
    secret value."""
    journal = "ERROR: uploading with sec" + CANARY + "+/=\\n" + _OK_JOURNAL
    body = (
        "SD init\n"
        "SD gen backup-gpg\n"
        + _offbox_env()
        + "printf 'FOO_TOKEN=foo" + CANARY + "\\nBAR_KEY=bar" + CANARY + "\\n' > \"$D/app.env\"\n"
        "printf 'KEEP=box" + CANARY + "\\nFOO_TOKEN=old" + CANARY + "\\n' > \"$BOX/opt/acb/app/.env\"\n"
        "SD status; SD diff backup-offbox; SD diff app-env\n"
        "SD push backup-offbox < /dev/null\n"
        f"STUB_JOURNAL='{journal}' SD push --all --yes --verify\n"
        "bash -x \"$W/repo/scripts/secrets.sh\" push app-env --yes\n"
        "( set -o xtrace; export SHELLOPTS; SD push backup-gpg-public --yes )\n"
        "SD status\n"
        "command sed -i 's/^BAR_KEY=.*/BAR_KEY=new" + CANARY + "/' \"$D/app.env\"\n"
        "STUB_CORRUPT=1 SD push app-env --yes\n"
        "SD push app-env --yes\n"
        # A rollback, with a canary in the values on both sides.
        "command sed -i 's/^BAR_KEY=.*/BAR_KEY=again" + CANARY + "/' \"$D/app.env\"\n"
        "STUB_HEALTH_CODE=503 SD push app-env --yes\n"
        # The refusals: an unknown key, a failed check, an unsafe value.
        "printf 'NOT_ALLOWED=x" + CANARY + "\\n' >> \"$D/app.env\"; SD push app-env --yes\n"
        "command sed -i '/NOT_ALLOWED/d' \"$D/app.env\"\n"
        "command sed -i 's/^BACKUP_GPG_RECIPIENT=.*/BACKUP_GPG_RECIPIENT=" + CANARY + "/' \"$D/backup-offbox.env\"\n"
        "SD push backup-offbox --yes; SD diff backup-offbox; SD push --all --yes\n"
        "command sed -i 's/^BAR_KEY=.*/BAR_KEY=" + CANARY + "$(id)/' \"$D/app.env\"\n"
        "SD push app-env --yes\n"
        "SD forget backup-gpg-private --yes\n"
        "SD forget app-env --yes\n"
        "true\n"
    )
    res = _run(body, manifest=_merge_manifest())
    assert res["rc"] == 0
    # The run did real work, so the absence below means something.
    assert "OK" in res["out"]
    assert "post-push check: PASS" in res["out"]
    assert "[REDACTED:BACKUP_S3_SECRET_ACCESS_KEY]" in res["out"]
    assert "ERROR: ROLLED BACK." in res["err"]
    assert CANARY.encode() in (_box(res, "/opt/acb/app/.env") or b"")
    assert CANARY.encode() in (_box(res, "/etc/acb/backup-offbox.env") or b"")
    assert "fails the check gpg_fingerprint" in res["err"]
    assert "NOT_ALLOWED is not an allowed key" in res["err"]
    assert "BAR_KEY: it holds a character that the shell reads as code" in res["err"]
    assert "result=verify-failed" in res["log"]
    assert "result=rolled-back" in res["log"]
    assert "+ " in res["err"], "bash -x should have traced something"

    assert CANARY not in res["out"], "a value reached stdout"
    assert CANARY not in res["err"], "a value reached stderr"
    assert CANARY not in res["calls"], "a value reached an argv"
    assert CANARY not in res["log"], "a value reached push.log"
    assert CANARY not in res["remote"], "the box sent a value back"
    for path, body_bytes in res["files"].items():
        if CANARY.encode() not in body_bytes:
            continue
        allowed = (
            path.startswith("home/.metorite/secrets/")
            and not path.endswith(("push.log", "README.txt"))
        ) or re.fullmatch(r"box/(etc/acb/backup-offbox\.env|opt/acb/app/\.env)(\.bak-\d{8}T\d{6}Z)?", path)
        assert allowed, f"a value reached {path}"


# ── The value rule matches env_guard ─────────────────────────────────────────


def test_the_env_value_rule_matches_env_guard() -> None:
    """secrets.sh repeats env_guard.value_problem in bash. Both must agree, or
    the box gets a value that the gateway's own writer would refuse."""
    from acb_common.env_guard import value_problem

    values = [
        "plain", "AKIA0123456789", "abc+/=", "https://x.example/a/b?c=d", "a,b.c-d_e@f:g%h",
        "has space", "a$b", "a`b", "a\\b", "a'b", 'a"b', "a;b", "a&b", "a|b", "a<b", "a>b",
        "a(b", "a)b", "~home", "#hash", "x:~y", "tab\there", "café", "x" * 4096, "x" * 4097,
        "a!b", "a*b", "a?b", "a[b]", "{a}", "a~b", "a#b", "=start",
    ]
    lines = "\n".join(base64.b64encode(v.encode()).decode() for v in values)
    prog = (
        "export LC_ALL=C\n"
        + _bash_function("env_value_problem")
        + "while IFS= read -r b; do v=\"$(printf '%s' \"$b\" | base64 -d)\"; "
        + "p=\"$(env_value_problem \"$v\")\"; if [ -n \"$p\" ]; then echo bad; else echo ok; fi; done <<'EOF'\n"
        + lines
        + "\nEOF\n"
    )
    run = subprocess.run(["bash"], input=prog.encode(), capture_output=True, timeout=60, env=_hermetic_env())
    got = run.stdout.decode().split()
    want = ["ok" if value_problem(v) is None else "bad" for v in values]
    assert got == want, list(zip(values, got, want, strict=False))
