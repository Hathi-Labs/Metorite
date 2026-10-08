"""The secrets drop: scripts/secrets.sh and deploy/secrets/manifest.json.

The owner asked for it on 2026-10-08: put each key in a file in a folder on
the dev PC, and let one command check it, back up the old file on the box,
write the new one and verify it. docs/secrets_drop.md is the reference.

Every case runs the REAL script. `ssh`, `sudo`, `chown`, `stat`, `systemctl`
and (where it says so) `gpg` are exported bash functions, the idiom of
test_backup_deploy_wiring.py. The ssh stub runs the remote half in a local
"box" folder, so the remote script runs for real too. Each stub, and each
external tool that the script calls, writes its argv to a calls log. So a test
can see every argv that the script made, here and "on the box".

🔴 The fence of the whole design is the canary test. A unique value goes into
every secret, through every command and every refusal, and it must never show
in the output, in an argv, in a log, or in a file on the box other than the
target and its backup.
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
_STUBS = r"""
set -u
umask 077
W="$(mktemp -d)"
export W
export HOME="$W/home"; mkdir -p "$HOME"
export BOX="$W/box"; mkdir -p "$BOX/opt/acb/app"
export CALLS="$W/calls.log"; : > "$CALLS"
D="$HOME/.metorite/secrets"
rec() { local n="$1"; shift; { printf '%s' "$n"; printf ' %q' "$@"; printf '\n'; } >> "$CALLS"; }
ssh() {
  rec ssh "$@"
  local cmd
  while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) shift; break ;; esac; done
  cmd="$*"
  cmd="${cmd//\/etc\/acb/$BOX/etc/acb}"
  cmd="${cmd//\/opt\/acb/$BOX/opt/acb}"
  if [ -n "${STUB_SSH_FAILS:-}" ]; then echo "ssh: connect to host: Connection refused" >&2; return 255; fi
  if [ -n "${STUB_CORRUPT:-}" ]; then command sed 's/=./=Z/' | bash -c "$cmd"; else bash -c "$cmd"; fi
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
systemctl() { rec systemctl "$@"; if [ "$1" = is-active ] && [ -n "${STUB_INACTIVE:-}" ]; then return 3; fi; return 0; }
sleep() { :; }
uname() { if [ -n "${STUB_UNAME:-}" ]; then echo "$STUB_UNAME"; else command uname "$@"; fi; }
export -f rec ssh _mkey sudo chown chmod stat systemctl sleep uname
# Each external tool records its argv, so the canary test sees every argv.
for c in cat cp mv mktemp sha256sum base64 awk sed grep cut tr head sort wc date dirname basename shred dd seq; do
  eval "$c() { rec $c \"\$@\"; command $c \"\$@\"; }"
  export -f "$c"
done
"""

# A stub gpg. A file that holds "PRIVATE" shows as a secret key, so the
# validator sees what real gpg shows for a private key.
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
    stdout, stderr, the calls log, push.log and every file under the box and
    the home folder as {relative path: bytes}."""
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
    log, _, files = rest.partition("\n===FILES===\n")
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
        "files": tree,
    }


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def _merge_manifest(restart: str | None = "acb-gateway") -> dict:
    """The real manifest, with app-env given two keys to merge."""
    m = _manifest()
    for e in m["entries"]:
        if e["name"] == "app-env":
            e["allowed_keys"] = ["FOO_TOKEN", "BAR_KEY"]
            e["required_keys"] = ["FOO_TOKEN"]
            e["validators"] = {"BAR_KEY": ["non_empty"]}
            e["restart"] = restart
    return m


def _offbox_env(**over: str | None) -> str:
    """A filled backup-offbox.env, as a bash command that writes it."""
    vals: dict[str, str | None] = {
        "BACKUP_S3_ENDPOINT": "https://wbjpwtxigkileyjsgahk.storage.supabase.co/storage/v1/s3",
        "BACKUP_S3_REGION": "ap-south-1",
        "BACKUP_S3_BUCKET": "metorite-backups",
        "BACKUP_S3_ACCESS_KEY_ID": "AKID" + CANARY,
        "BACKUP_S3_SECRET_ACCESS_KEY": "sec" + CANARY + "+/=",
        "BACKUP_GPG_RECIPIENT": "0123456789ABCDEF0123456789ABCDEF01234567",
        "BACKUP_GPG_PUBLIC_KEY_FILE": "/opt/acb/backup-public-key.asc",
    }
    vals.update(over)
    lines = "".join(f"{k}={v}\\n" for k, v in vals.items() if v is not None)
    return f"printf '{lines}' > \"$D/backup-offbox.env\"\n"


def _box(res: dict, path: str) -> bytes | None:
    return res["files"].get("box" + path)


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
        "command cat > \"$TMPDIR_AWK\" <<'AWK'\n" + _json_awk() + "\nAWK\n"
        'awk -f "$TMPDIR_AWK" deploy/secrets/manifest.json\n'
    )
    prog = 'TMPDIR_AWK="$(mktemp)"\n' + prog + 'rc=$?; rm -f "$TMPDIR_AWK"; exit $rc\n'
    run = subprocess.run(["bash"], input=prog.encode(), capture_output=True, cwd=_ROOT, timeout=60)
    assert run.returncode == 0, run.stderr.decode()
    got = [tuple(ln.split("\t", 2)) for ln in run.stdout.decode().splitlines()]
    assert got == _flatten(_manifest())


def test_the_manifest_ships_the_three_entries() -> None:
    m = _manifest()
    by = {e["name"]: e for e in m["entries"]}
    assert set(by) == {"backup-offbox", "backup-gpg-public", "app-env"}

    off = by["backup-offbox"]
    seven = {
        "BACKUP_S3_ENDPOINT", "BACKUP_S3_REGION", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY", "BACKUP_GPG_RECIPIENT", "BACKUP_GPG_PUBLIC_KEY_FILE",
    }
    assert (off["kind"], off["remote_path"]) == ("env-file", "/etc/acb/backup-offbox.env")
    assert (off["owner"], off["group"], off["mode"]) == ("root", "root", "0600")
    assert set(off["allowed_keys"]) == seven == set(off["required_keys"])
    assert off["restart"] is None
    assert off["prefill"] == {
        "BACKUP_S3_ENDPOINT": "https://wbjpwtxigkileyjsgahk.storage.supabase.co/storage/v1/s3",
        "BACKUP_S3_BUCKET": "metorite-backups",
        "BACKUP_GPG_PUBLIC_KEY_FILE": "/opt/acb/backup-public-key.asc",
        "BACKUP_S3_REGION": "ap-south-1",
    }
    assert "check in dashboard" in off["key_notes"]["BACKUP_S3_REGION"].lower()

    pub = by["backup-gpg-public"]
    assert (pub["kind"], pub["remote_path"], pub["owner"], pub["mode"]) == (
        "file", "/opt/acb/backup-public-key.asc", "root", "0644",
    )
    assert pub["validators"] == {"file": ["gpg_public_key_only"]}

    app = by["app-env"]
    assert (app["kind"], app["remote_path"], app["owner"], app["restart"]) == (
        "env-merge", "/opt/acb/app/.env", "acb", "acb-gateway",
    )
    assert app["allowed_keys"] == [], "app-env starts EMPTY, so it cannot be used by accident"
    # backup_offbox.sh refuses a BACKUP_ line in the app env (H-123), and the
    # gateway's Copilot CLI inherits that env (H-270).
    assert not [k for k in app["allowed_keys"] if k.startswith("BACKUP_")]


def test_a_bad_manifest_fails_loudly() -> None:
    m = _manifest()
    m["entries"][0]["validators"]["BACKUP_S3_REGION"] = ["no_such_check"]
    m["entries"][2]["remote_path"] = "/opt/acb/../etc/shadow"
    res = _run("SD status\n", manifest=m)
    assert res["rc"] != 0
    assert "no_such_check is not a validator" in res["err"]
    assert "remote_path must be an absolute path" in res["err"]
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
    off = next(e for e in _manifest()["entries"] if e["name"] == "backup-offbox")
    assert list(lines) == off["allowed_keys"], "every allowed NAME, in order"
    for k, v in lines.items():
        assert v == off["prefill"].get(k, ""), k
    assert "check in dashboard" in tpl.lower()
    # The second init kept both files the owner had changed.
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
        ("BACKUP_GPG_RECIPIENT", "ABCDEF", "BACKUP_GPG_RECIPIENT fails the check fingerprint_40_hex"),
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
    cp_at = next(i for i, ln in enumerate(calls) if ln.startswith("cp -p ") and "backup-offbox.env" in ln)
    mv_at = next(i for i, ln in enumerate(calls) if ln.startswith("mv -f ") and "backup-offbox.env" in ln)
    assert cp_at < mv_at, "the backup must come before the swap"
    assert "backup: /" in res["out"] and ".bak-" in res["out"]
    # No temp file is left beside the target.
    assert not [p for p in res["files"] if ".secrets-drop." in p]


def test_a_hash_mismatch_after_the_write_fails() -> None:
    res = _run(
        "SD init >/dev/null\nprintf 'FOO_TOKEN=abc\\n' > \"$D/app.env\"\n"
        "printf 'FOO_TOKEN=old\\n' > \"$BOX/opt/acb/app/.env\"\n"
        "STUB_CORRUPT=1 SD push app-env --yes\n",
        manifest=_merge_manifest(),
    )
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


def test_the_restart_runs_only_when_the_manifest_sets_one() -> None:
    res = _run("SD init >/dev/null\n" + _offbox_env() + "SD push backup-offbox --yes\n")
    assert res["rc"] == 0, res["err"]
    assert not _cmd_lines(res["calls"], "systemctl")
    assert "restart: none" in res["out"]

    merge = (
        "SD init >/dev/null\nprintf 'FOO_TOKEN=abc\\n' > \"$D/app.env\"\n"
        "printf 'FOO_TOKEN=old\\n' > \"$BOX/opt/acb/app/.env\"\n"
    )
    res = _run(merge + "SD push app-env --yes\n", manifest=_merge_manifest())
    assert res["rc"] == 0, res["err"]
    sysd = _cmd_lines(res["calls"], "systemctl")
    assert sysd[0] == "systemctl restart acb-gateway"
    assert sysd[1] == "systemctl is-active --quiet acb-gateway"
    assert "restart: acb-gateway is active" in res["out"]

    res = _run(merge + "STUB_INACTIVE=1 SD push app-env --yes\n", manifest=_merge_manifest())
    assert res["rc"] != 0
    assert "acb-gateway is not active after the restart" in res["err"]
    assert "result=restart-failed" in res["log"]

    res = _run(merge + "SD push app-env --yes\n", manifest=_merge_manifest(restart=None))
    assert res["rc"] == 0, res["err"]
    assert not _cmd_lines(res["calls"], "systemctl")


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


def test_the_ssh_host_comes_from_one_place() -> None:
    res = _run("SD init >/dev/null\nSD status backup-offbox\n")
    assert all(ln.split()[4] == "metorite" for ln in _ssh_lines(res["calls"])), res["calls"]
    res = _run("SD init >/dev/null\nMETORITE_SSH_HOST=acb@stand-in SD status backup-offbox\n")
    assert _ssh_lines(res["calls"]) and all(
        ln.split()[4] == "acb@stand-in" for ln in _ssh_lines(res["calls"])
    ), res["calls"]
    res = _run("SD init >/dev/null\nMETORITE_SSH_HOST=-oProxyCommand=x SD status\n")
    assert res["rc"] != 0 and "METORITE_SSH_HOST is not a host name" in res["err"]
    assert not _ssh_lines(res["calls"])


# ── status and diff ───────────────────────────────────────────────────────────


def _states(out: str) -> dict[str, str]:
    return {ln.split()[0]: ln.split()[1] for ln in out.splitlines() if ln.strip()}


def test_status_reports_each_state_and_never_prints_a_value() -> None:
    m = _merge_manifest()
    res = _run(
        "SD init >/dev/null\n"
        # missing-local and remote-only: no local value, and the box has one.
        "printf 'FOO_TOKEN=box%s\\n' " + CANARY + " > \"$BOX/opt/acb/app/.env\"\n"
        "SD status; echo ---\n"
        # local-only, then in-sync, then differs.
        + _offbox_env()
        + "SD status backup-offbox; echo ---\n"
        "SD push backup-offbox --yes >/dev/null 2>&1\n"
        "SD status backup-offbox; echo ---\n"
        "printf 'BACKUP_S3_SECRET_ACCESS_KEY=other" + CANARY + "\\n' > \"$D/x\"\n"
        "command sed -i '/SECRET_ACCESS_KEY/d' \"$D/backup-offbox.env\"; command cat \"$D/x\" >> \"$D/backup-offbox.env\"\n"
        "SD status backup-offbox; echo ---\n"
        "SD diff backup-offbox\n",
        manifest=m,
    )
    assert res["rc"] == 0, res["err"]
    parts = res["out"].split("---\n")
    assert _states(parts[0]) == {
        "backup-offbox": "local-only",  # the template holds the prefilled values
        "backup-gpg-public": "missing-local",
        "app-env": "remote-only",
    }
    assert _states(parts[1]) == {"backup-offbox": "local-only"}
    assert _states(parts[2]) == {"backup-offbox": "in-sync"}
    assert _states(parts[3]) == {"backup-offbox": "differs"}
    assert "~ BACKUP_S3_SECRET_ACCESS_KEY" in parts[4]
    assert "= 6 key(s) the same" in parts[4]
    assert CANARY not in res["out"] + res["err"]
    for ln in parts[2].splitlines():
        # Names and 8-digit hash prefixes, nothing more.
        assert re.fullmatch(r"\S+\s+\S+\s+local [0-9a-f-]{1,8}\s+remote [0-9a-f-]{1,8}\s*", ln), ln


def test_status_says_unreachable_when_ssh_fails() -> None:
    res = _run("SD init >/dev/null\nSTUB_SSH_FAILS=1 SD status\n")
    assert res["rc"] != 0
    assert "unreachable" in res["out"]


def test_diff_names_removed_keys_of_a_whole_file() -> None:
    res = _run(
        "SD init >/dev/null\n" + _offbox_env()
        + "mkdir -p \"$BOX/etc/acb\"; printf 'BACKUP_S3_KEEP=3\\nBACKUP_S3_REGION=ap-south-1\\n' > \"$BOX/etc/acb/backup-offbox.env\"\n"
        + "SD diff backup-offbox\n"
    )
    assert res["rc"] == 0, res["err"]
    assert "  - BACKUP_S3_KEEP" in res["out"]
    assert "  + BACKUP_S3_SECRET_ACCESS_KEY" in res["out"]
    assert "= 1 key(s) the same" in res["out"]


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
    assert len(before) == len(after)
    assert changed == [("BACKUP_GPG_RECIPIENT=", "BACKUP_GPG_RECIPIENT=0123456789ABCDEF0123456789ABCDEF01234567")]
    assert "second=1" in res["out"]
    assert "already in the drop folder" in res["err"]
    assert "Copy backup-gpg-PRIVATE.asc and backup-gpg-passphrase.txt into your password manager" in res["out"]
    assert len(f["home/.metorite/secrets/backup-gpg-passphrase.txt"].strip()) >= 32
    assert not [p for p in f if ".gnupg-gen." in p], "the temp GNUPGHOME was left behind"


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
    assert f"BACKUP_GPG_RECIPIENT={fprs[1]}\n" in env
    before = f["home/before"].decode().splitlines()
    after = env.splitlines()
    assert [(a, b) for a, b in zip(before, after, strict=True) if a != b] == [
        ("BACKUP_GPG_RECIPIENT=", f"BACKUP_GPG_RECIPIENT={fprs[1]}")
    ]
    # --force kept the first pair.
    olds = [p for p in f if ".old-" in p]
    assert len(olds) == 3, olds
    assert not [p for p in f if ".gnupg-" in p], "a temp GNUPGHOME was left behind"


def test_forget_deletes_the_private_pair() -> None:
    res = _run(
        "SD init >/dev/null\nSD gen backup-gpg >/dev/null || exit 1\n"
        "SD forget backup-gpg-private < /dev/null; echo \"noyes=$?\"\n"
        "SD forget backup-gpg-private --yes || exit 1\n"
        "SD forget no-such-thing --yes; echo \"unknown=$?\"\n"
    )
    assert res["rc"] == 0, res["err"]
    assert "noyes=1" in res["out"] and "unknown=1" in res["out"]
    f = res["files"]
    assert "home/.metorite/secrets/backup-gpg-PRIVATE.asc" not in f
    assert "home/.metorite/secrets/backup-gpg-passphrase.txt" not in f
    assert "home/.metorite/secrets/backup-gpg-public.asc" in f
    assert "forgot: backup-gpg-PRIVATE.asc" in res["out"]


# ── The canary ────────────────────────────────────────────────────────────────


def test_the_canary_never_appears_in_output_argv_or_logs() -> None:
    """Every command, every refusal, with a canary in every secret value."""
    body = (
        "SD init\n"
        "SD gen backup-gpg\n"
        # A filled backup-offbox, and an app env with two managed keys.
        + _offbox_env()
        + "printf 'FOO_TOKEN=foo" + CANARY + "\\nBAR_KEY=bar" + CANARY + "\\n' > \"$D/app.env\"\n"
        "printf 'KEEP=box" + CANARY + "\\nFOO_TOKEN=old" + CANARY + "\\n' > \"$BOX/opt/acb/app/.env\"\n"
        "SD status; SD diff backup-offbox; SD diff app-env\n"
        "SD push backup-offbox < /dev/null\n"
        "SD push backup-offbox --yes\n"
        "bash -x \"$W/repo/scripts/secrets.sh\" push app-env --yes\n"
        "( set -o xtrace; export SHELLOPTS; SD push backup-gpg-public --yes )\n"
        "SD status\n"
        # Change one value and push again, so a backup holds the old one.
        "command sed -i 's/^BAR_KEY=.*/BAR_KEY=new" + CANARY + "/' \"$D/app.env\"\n"
        "STUB_CORRUPT=1 SD push app-env --yes\n"
        "SD push app-env --yes\n"
        # The refusals: an unknown key, a failed check, an unsafe value.
        "printf 'NOT_ALLOWED=x" + CANARY + "\\n' >> \"$D/app.env\"; SD push app-env --yes\n"
        "command sed -i '/NOT_ALLOWED/d' \"$D/app.env\"\n"
        "command sed -i 's/^BACKUP_GPG_RECIPIENT=.*/BACKUP_GPG_RECIPIENT=" + CANARY + "/' \"$D/backup-offbox.env\"\n"
        "SD push backup-offbox --yes; SD diff backup-offbox\n"
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
    assert CANARY.encode() in (_box(res, "/opt/acb/app/.env") or b"")
    assert CANARY.encode() in (_box(res, "/etc/acb/backup-offbox.env") or b"")
    assert "fails the check fingerprint_40_hex" in res["err"]
    assert "NOT_ALLOWED is not an allowed key" in res["err"]
    assert "BAR_KEY: it holds a character that the shell reads as code" in res["err"]
    assert "result=verify-failed" in res["log"]
    assert "+ /" in res["err"] or "+ " in res["err"], "bash -x should have traced something"

    assert CANARY not in res["out"], "a value reached stdout"
    assert CANARY not in res["err"], "a value reached stderr"
    assert CANARY not in res["calls"], "a value reached an argv"
    assert CANARY not in res["log"], "a value reached push.log"
    for path, body_bytes in res["files"].items():
        if CANARY.encode() not in body_bytes:
            continue
        allowed = (
            path.startswith("home/.metorite/secrets/")
            and not path.endswith(("push.log", "README.txt"))
        ) or re.fullmatch(r"box/(etc/acb/backup-offbox\.env|opt/acb/app/\.env)(\.bak-\d{8}T\d{6}Z(\.\d+)?)?", path)
        assert allowed, f"a value reached {path}"


# ── The value rule matches env_guard ─────────────────────────────────────────


def _bash_function(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} moved; update this test"
    return m.group(0)


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
