#!/usr/bin/env bash
# Switch the Metorite organization of a machine (MCP-G17h, task 4034, EA-19h).
#
# Owner directive 2026-11-26: a machine stays signed in, unless an owner
# switches its organization. This script is the ONE deliberate, logged act that
# ends a sign-in. The user-scope MCP entry `metorite` runs the header helper
# `~/.local/bin/metorite-box-headers <abs path of box.env>` on each connect
# (EA-19g). So a switch swaps the token in box.env, then the sessions restart.
# No `claude mcp` command runs.
#
#   metorite_switch_org.sh add <label> [--replace]
#   metorite_switch_org.sh list
#   metorite_switch_org.sh to <label> [--stop-lanes] [--dry-run]
#
#   add <label>     read a box token from a hidden prompt, or from stdin when
#                   stdin is not a terminal (never from argv). Check the shape
#                   with TOKEN_RE of metorite_box_headers.sh, and write
#                   ~/.config/metorite/orgs/<label>.env (mode 0600).
#   --replace       add: replace an org file that exists.
#   list            show the labels, and mark the active one. Never a token.
#   to <label>      make <label> the organization of this machine.
#   --stop-lanes    to: stop each live Claude session first (`claude stop`).
#   --dry-run       to: show what the switch does, and change nothing.
#
# A label matches ^[a-z0-9][a-z0-9-]{0,31}$. The active label is in
# ~/.config/metorite/active-org.
#
# The steps of `to`. Every check runs before a session stops or a file changes.
#   1. Take the lock ~/.config/metorite/.switch-org.lock (flock). A second
#      `add` or `to` exits at once.
#   2. Refuse inside a Claude session (CLAUDECODE or a CLAUDE_CODE_* variable),
#      because a stop of the lanes stops the caller too.
#   3. Refuse an unknown label. Run the installed header helper on the org
#      file, and refuse on the sentinel bearer. Refuse when the user-scope
#      entry in .claude.json does not name the helper and box.env (run
#      box_connect.sh first). Refuse an address that is not
#      https://mcp.metorite.com, or http(s) to 127.0.0.1 or localhost (the rule
#      of mcpUrl() in .claude/hooks/session-handoff.mjs).
#   4. Refuse a box.env or box.env.prev that is a symlink or not a regular file,
#      and a box.env that is not mode 0600 of this user with one link. Refuse a
#      box.env whose token no org file holds: run `add <label>` for it first.
#   5. Count the live sessions with `claude agents --json` (an entry with a
#      pid). Refuse when one is live, unless --stop-lanes is set. Then stop each
#      one, and wait until none is live.
#   6. Copy box.env to box.env.prev (0600). Write the new token into box.env
#      (temp file 0600, then mv -T). From here until the proof passes, a
#      SIGINT, SIGTERM or SIGHUP puts box.env.prev back.
#   7. Prove the new identity: POST initialize, then tools/call whoami, to the
#      address of the entry, with the header that the helper prints. It follows
#      no redirect. Show the email and the organization id. On a failure, put
#      box.env.prev back (or remove box.env when there was none).
#   8. Write active-org. Read the live sessions again, and name each session
#      that started during the switch. Append one NOTE line to
#      ~/lanes/merge-queue.log when that file exists, and one line to
#      ~/.local/state/metorite/org-switch.log.
#
# Exit codes: 0 done (or --dry-run done), 2 usage, a bad label or a bad
# address in the entry, 3 the token is empty or has the wrong shape, 4 a tool
# or the helper source is missing, 5 the org file exists and --replace is not
# set, 6 a write failed, 7 the user-scope entry does not name the header helper
# or the helper is not installed, 8 the label is unknown or the helper refuses
# its org file, 9 a live session and no --stop-lanes, a run inside a Claude
# session, or the sessions could not be read or stopped, 10 the identity proof
# failed or a signal came (box.env is put back), 11 box.env or box.env.prev is
# not a safe file, or box.env holds a token that no org file holds, 12 another
# switch holds the lock.
#
# ⚠️ It never prints, logs or passes a token in argv. It turns xtrace off
# first. The token goes to builtins only (`read`, `printf`, `[[ =~ ]]`). The
# identity proof runs the helper in a child, and the header stays in memory.
#
# Runbook: docs/box_connect.md §7. Fence: tests/unit/test_metorite_switch_org.py.
{ set +x; } 2>/dev/null
set -Eeuo pipefail
umask 077
export LC_ALL=C
unset -v METORITE_BOX_TOKEN

LABEL_RE='^[a-z0-9][a-z0-9-]{0,31}$'
PRINTABLE_RE='^[!-~]+$'
tok=""
tmp_file=""
who_out=""

# shellcheck disable=SC2317  # the EXIT trap calls it.
cleanup() {
  unset -v tok line
  local f
  for f in "$tmp_file" "$who_out"; do
    if [ -n "$f" ] && [ -e "$f" ]; then rm -f -- "$f"; fi
  done
}
trap cleanup EXIT
# The ERR trap names the line only. $BASH_COMMAND is not printed.
trap 'printf "switch-org: ERROR: a command failed at line %s.\n" "$LINENO" >&2' ERR

say() { printf 'switch-org: %s\n' "$*"; }
die() { printf 'switch-org: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

usage() {
  sed -n '/^#   metorite_switch_org.sh add/,/^#   --dry-run/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
label=""
REPLACE=0
STOP_LANES=0
DRY_RUN=0
case "$cmd" in
  -h|--help|help) usage; exit 0 ;;
  add|to)
    [ $# -ge 1 ] || die "$cmd needs a label. Run with --help." 2
    label="$1"; shift ;;
  list) ;;
  *) die "unknown command. Run with --help." 2 ;;
esac
while [ $# -gt 0 ]; do
  case "$cmd:$1" in
    add:--replace) REPLACE=1; shift ;;
    to:--stop-lanes) STOP_LANES=1; shift ;;
    to:--dry-run) DRY_RUN=1; shift ;;
    *) die "unknown argument. The token is never an argument: 'add' reads it from a hidden prompt or from stdin. Run with --help." 2 ;;
  esac
done
if [ "$cmd" != "list" ] && ! [[ "$label" =~ $LABEL_RE ]]; then
  die "a label is 1 to 32 characters of a-z, 0-9 and '-', and starts with a letter or a digit." 2
fi

# The token shape has one definition, in the header helper (TOKEN_RE).
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
helper_src="$script_dir/metorite_box_headers.sh"
[ -f "$helper_src" ] || die "$helper_src is missing. Run the script from a full checkout." 4
TOKEN_RE=""
REFUSED_BEARER=""
# shellcheck source=/dev/null  # it only sets TOKEN_RE and REFUSED_BEARER, then returns.
. "$helper_src"
if [ -z "$TOKEN_RE" ] || [ -z "$REFUSED_BEARER" ]; then die "$helper_src does not define TOKEN_RE and REFUSED_BEARER." 4; fi

: "${HOME:?HOME is not set}"
if ! [[ "$HOME" =~ ^/[A-Za-z0-9_./-]*$ ]] || [[ "$HOME" == *..* ]]; then
  die "HOME must be an absolute path of letters, digits, '_', '.', '-' and '/' only." 2
fi
env_dir="$HOME/.config/metorite"
env_file="$env_dir/box.env"
prev_file="$env_dir/box.env.prev"
orgs_dir="$env_dir/orgs"
active_file="$env_dir/active-org"
helper_path="$HOME/.local/bin/metorite-box-headers"
helper_cmd="$helper_path $env_file"
state_log="$HOME/.local/state/metorite/org-switch.log"
note_log="$HOME/lanes/merge-queue.log"
claude_json="${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json"

# Write text to a path: temp file 0600 in the same folder, then mv -T. -T:
# when the path is a folder, mv fails and never moves into it. The text is a
# function argument, so it makes no argv of a child.
write_atomic() {
  local dest="$1" dir
  dir="$(dirname -- "$dest")"
  tmp_file="$(mktemp "$dir/.switch-org.XXXXXX")" || die "could not make a temp file in $dir." 6
  chmod 600 -- "$tmp_file" || die "could not set the mode of the temp file." 6
  printf '%s' "$2" >"$tmp_file" || die "could not write the temp file." 6
  mv -fT -- "$tmp_file" "$dest" 2>/dev/null || die "could not move the temp file to $dest. Is it a folder?" 6
  tmp_file=""
}

# Copy a file the same way: temp file 0600, then mv -T.
copy_atomic() {
  local src="$1" dest="$2" dir
  dir="$(dirname -- "$dest")"
  tmp_file="$(mktemp "$dir/.switch-org.XXXXXX")" || die "could not make a temp file in $dir." 6
  chmod 600 -- "$tmp_file" || die "could not set the mode of the temp file." 6
  cat -- "$src" >"$tmp_file" || die "could not copy $src." 6
  mv -fT -- "$tmp_file" "$dest" 2>/dev/null || die "could not move the temp file to $dest. Is it a folder?" 6
  tmp_file=""
}

active_label() {
  local a=""
  if [ -f "$active_file" ] && [ ! -L "$active_file" ]; then
    IFS= read -r a <"$active_file" || true
  fi
  if [[ "$a" =~ $LABEL_RE ]]; then printf '%s' "$a"; fi
}

# One `add` or `to` at a time. The lock holds until the script exits.
take_lock() {
  command -v flock >/dev/null 2>&1 || die "flock is not on the PATH." 4
  mkdir -p -- "$env_dir" || die "could not make $env_dir." 6
  chmod 700 -- "$env_dir" || die "could not set the mode of $env_dir." 6
  local lock="$env_dir/.switch-org.lock"
  if [ -L "$lock" ] || { [ -e "$lock" ] && [ ! -f "$lock" ]; }; then
    die "$lock is not a regular file. Remove it, then run again." 12
  fi
  exec {lock_fd}>>"$lock" || die "could not open $lock." 12
  flock -n "$lock_fd" || die "another org switch runs on this machine now (lock $lock). Wait for it, then run again." 12
}

# ── add ─────────────────────────────────────────────────────────────────────
if [ "$cmd" = "add" ]; then
  take_lock
  org_file="$orgs_dir/$label.env"
  if [ -e "$org_file" ] || [ -L "$org_file" ]; then
    [ "$REPLACE" -eq 1 ] || die "the org file $org_file exists. Nothing was written. Run again with --replace to replace it." 5
  fi
  if [ -t 0 ]; then
    printf 'Paste the box token for %s (it does not show): ' "$label" >&2
    IFS= read -rs tok || true
    printf '\n' >&2
  else
    IFS= read -r tok || true
  fi
  tok="${tok%$'\r'}"
  [ -n "$tok" ] || die "no token was given. Paste the token from Integrations, AI agents." 3
  if ! [[ "$tok" =~ $TOKEN_RE ]]; then
    die "the token does not have the shape of a box token (mbt_ and 43 URL-safe characters). Nothing was written." 3
  fi
  mkdir -p -- "$orgs_dir" || die "could not make $orgs_dir." 6
  chmod 700 -- "$env_dir" "$orgs_dir" || die "could not set the mode of $orgs_dir." 6
  # printf is a builtin, so the token makes no argv.
  write_atomic "$org_file" "METORITE_BOX_TOKEN=$tok"$'\n'
  unset -v tok
  say "wrote $org_file (mode 0600)."
  if [ "$(active_label)" = "$label" ]; then
    say "$label is the active org. Run 'to $label' to put the new token into box.env."
  fi
  exit 0
fi

# ── list ────────────────────────────────────────────────────────────────────
if [ "$cmd" = "list" ]; then
  active="$(active_label)"
  found=0
  if [ -d "$orgs_dir" ]; then
    for f in "$orgs_dir"/*.env; do
      [ -e "$f" ] || continue
      name="$(basename -- "$f" .env)"
      [[ "$name" =~ $LABEL_RE ]] || continue
      found=1
      if [ "$name" = "$active" ]; then printf '* %s (active)\n' "$name"; else printf '  %s\n' "$name"; fi
    done
  fi
  [ "$found" -eq 1 ] || say "no org file in $orgs_dir. Add one with: add <label>"
  exit 0
fi

# ── to ──────────────────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || die "python3 is not on the PATH." 4
command -v claude >/dev/null 2>&1 || die "claude is not on the PATH. The script reads the live sessions with it." 4

# 1. The lock.
take_lock

# 2. Never inside a Claude session: a stop of the lanes stops the caller too.
in_claude=0
[ -n "${CLAUDECODE:-}" ] && in_claude=1
for var in $(compgen -e); do
  case "$var" in CLAUDE_CODE_*) in_claude=1 ;; esac
done
if [ "$in_claude" -eq 1 ] && [ "$DRY_RUN" -ne 1 ]; then
  die "this shell runs inside a Claude session (CLAUDECODE or a CLAUDE_CODE_* variable is set). Run the switch from a plain shell." 9
fi

# 3a. The label must have an org file.
org_file="$orgs_dir/$label.env"
[ -e "$org_file" ] || [ -L "$org_file" ] || die "no org file for '$label' in $orgs_dir. Add it with: add $label" 8

# 3b. The user-scope entry must name the installed helper and box.env, the way
# box_connect.sh writes it. Its address must pass the rule of mcpUrl() in
# .claude/hooks/session-handoff.mjs: https://mcp.metorite.com, or http(s) to
# 127.0.0.1 or localhost, and no user or password. A token goes to no other
# host. Prints the address. Never a header.
entry_rc=0
url="$(python3 -I - "$claude_json" "$helper_cmd" <<'PY'
import json, sys
from urllib.parse import urlsplit
path, helper_cmd = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as fh:
        entry = (json.load(fh).get("mcpServers") or {}).get("metorite")
except (OSError, ValueError, AttributeError):
    sys.exit(3)
if not isinstance(entry, dict) or entry.get("headersHelper") != helper_cmd:
    sys.exit(3)
if entry.get("headers"):
    sys.exit(4)
url = entry.get("url")
if not isinstance(url, str) or not url.isascii() or not url.isprintable() or " " in url:
    sys.exit(5)
try:
    u = urlsplit(url)
    host = u.hostname
    u.port  # a bad port raises
except ValueError:
    sys.exit(5)
if u.username is not None or u.password is not None or "@" in u.netloc:
    sys.exit(5)
local = host in ("127.0.0.1", "localhost")
metorite = u.scheme == "https" and host == "mcp.metorite.com"
if not (metorite or (local and u.scheme in ("http", "https"))):
    sys.exit(5)
print(url)
PY
)" || entry_rc=$?
case "$entry_rc" in
  0) ;;
  4) die "the user-scope 'metorite' entry holds a static header next to the header helper. Run scripts/box_connect.sh --replace first." 7 ;;
  5) die "the address of the 'metorite' entry is not https://mcp.metorite.com (or http(s) to 127.0.0.1 or localhost), or it holds a user name. No token goes to it. Run scripts/box_connect.sh --replace." 2 ;;
  *) die "the user-scope 'metorite' entry in $claude_json does not name the header helper $helper_cmd. Run scripts/box_connect.sh first (docs/box_connect.md)." 7 ;;
esac
[ -x "$helper_path" ] && [ ! -L "$helper_path" ] \
  || die "the header helper $helper_path is not installed. Run scripts/box_connect.sh --replace." 7

# 3c. The helper must accept the org file. A refusal exits 0 and prints the
# sentinel bearer, so the script checks the output. The output holds the
# token, so it stays in a variable and is never printed.
helper_out=""
helper_out="$("$helper_path" "$org_file")" || true
if [ -z "$helper_out" ] || [[ "$helper_out" == *"$REFUSED_BEARER"* ]]; then
  unset -v helper_out
  die "the header helper refused the org file $org_file. See the line above." 8
fi
unset -v helper_out

# 4. box.env and box.env.prev must be safe files, and the token in box.env
# must be in an org file, so no switch loses it. The check reads the files in
# this child and prints no token.
pre_rc=0
python3 -I - "$env_file" "$prev_file" "$orgs_dir" "$TOKEN_RE" <<'PY' || pre_rc=$?
import os, re, stat, sys
env_file, prev_file, orgs_dir, token_re = sys.argv[1:5]
shape = re.compile(token_re)

def token_of(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("METORITE_BOX_TOKEN="):
                    tok = line[len("METORITE_BOX_TOKEN="):].rstrip("\r\n")
                    return tok if shape.match(tok) else None
    except OSError:
        return None
    return None

for path, code in ((prev_file, 4), (env_file, 3)):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        continue
    if not stat.S_ISREG(st.st_mode):
        sys.exit(code)
if os.path.lexists(env_file):
    st = os.lstat(env_file)
    if stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.getuid() or st.st_nlink != 1:
        sys.exit(3)
    current = token_of(env_file)
    if current is not None:
        held = False
        if os.path.isdir(orgs_dir):
            for name in os.listdir(orgs_dir):
                p = os.path.join(orgs_dir, name)
                if name.endswith(".env") and not os.path.islink(p) and token_of(p) == current:
                    held = True
                    break
        if not held:
            sys.exit(5)
PY
case "$pre_rc" in
  0) ;;
  3) die "$env_file is a symlink, is not a regular file, or is not mode 0600 of this user with one link. Repair it, or run scripts/box_connect.sh --replace. Nothing changed." 11 ;;
  4) die "$prev_file is a symlink or not a regular file. Remove it, then run again. Nothing changed." 11 ;;
  5) die "$env_file holds a token that no org file holds. A switch would lose it. Run 'add <label>' with that token first. Nothing changed." 11 ;;
  *) die "could not check $env_file. Nothing changed." 11 ;;
esac

old_label="$(active_label)"
[ -n "$old_label" ] || old_label="none"

# Prints one line for each live session (an entry with a pid):
# "<id>\t<name>\t<session id>". Exit 1 when the list cannot be read.
live_sessions() {
  local raw
  raw="$(timeout 60 claude agents --json 2>/dev/null </dev/null)" || return 1
  python3 -I - "$raw" <<'PY'
import json, re, sys
try:
    agents = json.loads(sys.argv[1])
except ValueError:
    sys.exit(1)
if not isinstance(agents, list):
    sys.exit(1)
def clean(v):
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(v or ""))[:64]
for a in agents:
    if isinstance(a, dict) and a.get("pid"):
        print("\t".join([clean(a.get("id")), clean(a.get("name")) or "-", clean(a.get("sessionId"))]))
PY
}

# 5. The live sessions.
live=""
live="$(live_sessions)" || die "could not read the live sessions with 'claude agents --json'. Nothing changed." 9
n_live=0
[ -z "$live" ] || n_live="$(printf '%s\n' "$live" | wc -l | tr -d ' ')"

if [ "$DRY_RUN" -eq 1 ]; then
  say "dry run. Nothing changes."
  say "org file: $org_file (the helper accepts it)."
  say "the entry: $url with the header helper $helper_path."
  say "the active org now: $old_label."
  say "live sessions: $n_live."
  if [ -n "$live" ]; then
    while IFS=$'\t' read -r sid name _; do say "  $name ($sid)"; done <<<"$live"
  fi
  if [ "$in_claude" -eq 1 ]; then
    say "this shell runs inside a Claude session. The real switch refuses here."
  fi
  if [ "$n_live" -gt 0 ] && [ "$STOP_LANES" -ne 1 ]; then
    say "the switch refuses while a session is live. Add --stop-lanes to stop them."
  fi
  say "then: copy box.env to box.env.prev, write the token of '$label' into box.env, call whoami at $url, and write active-org."
  exit 0
fi

stopped_names=()
restart_hints() {
  local name
  if [ "${#stopped_names[@]}" -gt 0 ]; then
    say "start the stopped lanes again, fresh and not as a resume (docs/home_box.md §3.3):"
    for name in "${stopped_names[@]}"; do
      say "  bash scripts/home_server/box.sh start $name <model> <start note>"
    done
  fi
}

if [ "$n_live" -gt 0 ]; then
  if [ "$STOP_LANES" -ne 1 ]; then
    while IFS=$'\t' read -r sid name _; do say "live: $name ($sid)"; done <<<"$live"
    die "$n_live Claude session(s) are live, and each holds the old token until it connects again. Stop them, or run again with --stop-lanes. Nothing changed." 9
  fi
  while IFS=$'\t' read -r sid name _; do
    [ -n "$sid" ] || { restart_hints; die "a live session ($name) has no short id to stop. The token did not change." 9; }
    timeout 60 claude stop "$sid" >/dev/null 2>&1 </dev/null \
      || { restart_hints; die "'claude stop $sid' ($name) failed. Stop it by hand, then run again. The token did not change." 9; }
    stopped_names+=("$name")
    say "stopped $name ($sid)."
  done <<<"$live"
  # The stop is not instant. Wait for the list to show no live session.
  wait_s="${METORITE_SWITCH_STOP_WAIT_S:-30}"
  [[ "$wait_s" =~ ^[0-9]{1,3}$ ]] && [ "$wait_s" -ge 1 ] || wait_s=30
  for _ in $(seq 1 "$wait_s"); do
    live="$(live_sessions)" || { restart_hints; die "could not read the live sessions after the stop. The token did not change." 9; }
    [ -n "$live" ] || break
    sleep 1
  done
  if [ -n "$live" ]; then
    restart_hints
    die "a session is still live after the stop. The token did not change. Run again." 9
  fi
fi

# Calls initialize, then tools/call whoami, with the header that the helper
# prints for <box.env>. Prints "<email>\t<organization id>". The header stays
# in this child. A failure prints one short reason on stderr, never a header.
WHOAMI_PY="$(cat <<'PY'
import json, re, subprocess, sys, urllib.error, urllib.request
url, helper, box_env, refused = sys.argv[1:5]

def fail(msg):
    print(f"switch-org: the identity proof failed: {msg}", file=sys.stderr)
    sys.exit(1)

try:
    run = subprocess.run([helper, box_env], capture_output=True, text=True, timeout=30, check=False)
    auth = json.loads(run.stdout).get("Authorization", "")
except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
    fail("the header helper gave no header.")
if not isinstance(auth, str) or not auth or refused in auth:
    fail("the header helper refused box.env.")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # never send the header to another address
        return None

opener = urllib.request.build_opener(NoRedirect)
version = "2025-06-18"
session = None

def call(n, method, params):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": auth}
    if method != "initialize":
        headers["MCP-Protocol-Version"] = version
        if session:
            headers["Mcp-Session-Id"] = session
    body = json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=20) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            text = resp.read(1 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        fail(f"{method} answered HTTP {exc.code}.")
    except (urllib.error.URLError, OSError) as exc:
        fail(f"{method} did not reach the server ({type(exc).__name__}).")
    if text.lstrip().startswith(("event:", "data:")):
        datas = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        text = datas[-1] if datas else ""
    try:
        msg = json.loads(text)
    except ValueError:
        fail(f"{method} did not answer JSON.")
    if not isinstance(msg, dict) or "result" not in msg:
        fail(f"{method} answered an error.")
    return msg["result"], sid

result, sid = call(1, "initialize", {
    "protocolVersion": version, "capabilities": {},
    "clientInfo": {"name": "metorite-switch-org", "version": "1"}})
if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
    version = result["protocolVersion"]
session = sid
result, _ = call(2, "tools/call", {"name": "whoami", "arguments": {}})
if not isinstance(result, dict) or result.get("isError"):
    fail("whoami answered a tool error.")
who = result.get("structuredContent")
if not isinstance(who, dict):
    try:
        who = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        who = {}
clean = lambda v: re.sub(r"[^A-Za-z0-9_.@+:-]", "", str(v or ""))[:120]
email, org = clean(who.get("email")), clean(who.get("organization_id"))
if not email or not org:
    fail("whoami gave no email or no organization id.")
print(f"{email}\t{org}")
PY
)"

# The old identity, for the record. A failure is not fatal: the old token can
# be revoked already.
old_org="?"
if [ -e "$env_file" ]; then
  old_who="$(python3 -I -c "$WHOAMI_PY" "$url" "$helper_path" "$env_file" "$REFUSED_BEARER" 2>/dev/null </dev/null)" \
    && old_org="${old_who#*$'\t'}" || true
fi

# 6. box.env.prev, then box.env. Read the org file with builtins. Never source it.
had_prev=0
if [ -e "$env_file" ]; then
  copy_atomic "$env_file" "$prev_file"
  had_prev=1
fi
tok=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    METORITE_BOX_TOKEN=*) tok="${line#METORITE_BOX_TOKEN=}"; break ;;
  esac
done <"$org_file"
unset -v line
tok="${tok%$'\r'}"
[[ "$tok" =~ $TOKEN_RE ]] || { unset -v tok; restart_hints; die "the org file $org_file changed and holds no box token now. The token did not change." 8; }

# Put the old box.env back: the copy in box.env.prev, or no file.
restore_box_env() {
  if [ "$had_prev" -eq 1 ]; then
    copy_atomic "$prev_file" "$env_file"
  else
    rm -f -- "$env_file"
  fi
}
proof_pid=""
# shellcheck disable=SC2317  # the signal trap calls it.
on_signal() {
  trap - INT TERM HUP
  if [ -n "$proof_pid" ]; then kill "$proof_pid" 2>/dev/null || true; fi
  restore_box_env
  restart_hints
  die "a signal stopped the switch before the proof. box.env is put back. The active org is still $old_label." 10
}
# From the write of box.env until the proof passes, a signal puts it back.
trap on_signal INT TERM HUP
write_atomic "$env_file" "METORITE_BOX_TOKEN=$tok"$'\n'
unset -v tok
say "wrote the token of '$label' into $env_file (mode 0600). The old file is $prev_file."

# 7. Prove the new identity. The proof runs in the background, so a signal
# stops the wait at once. On a failure, put the old box.env back.
who_out="$(mktemp "$env_dir/.switch-org-who.XXXXXX")" || { restore_box_env; die "could not make a temp file in $env_dir. box.env is put back." 10; }
python3 -I -c "$WHOAMI_PY" "$url" "$helper_path" "$env_file" "$REFUSED_BEARER" >"$who_out" </dev/null {lock_fd}>&- &
proof_pid=$!
proof_rc=0
wait "$proof_pid" || proof_rc=$?
proof_pid=""
new_who=""
[ "$proof_rc" -ne 0 ] || IFS= read -r new_who <"$who_out" || true
if [ "$proof_rc" -ne 0 ] || [ -z "$new_who" ]; then
  trap - INT TERM HUP
  restore_box_env
  restart_hints
  die "the server did not accept the token of '$label'. box.env is put back as it was. The active org is still $old_label." 10
fi
trap - INT TERM HUP
email="${new_who%%$'\t'*}"
new_org="${new_who#*$'\t'}"
say "the new identity: $email, organization $new_org."

# 8. active-org, the sessions that started during the switch, the NOTE line
# and the state log. The switch is complete, so a failed log only warns.
write_atomic "$active_file" "$label"$'\n'
late=""
if after="$(live_sessions)"; then
  if [ -n "$after" ]; then
    late="$(printf '%s\n' "$after" | cut -f2 | paste -sd ' ' -)"
  fi
else
  say "WARNING: could not read the live sessions after the switch. Check them with 'claude agents --json'."
fi
late_text=""
if [ -n "$late" ]; then
  late_text=" Started during the switch: $late. They hold the old org until a restart."
  say "started during the switch: $late. They hold the old org until a restart."
fi
host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || printf 'unknown')"
host="${host//[^A-Za-z0-9_.-]/}"
stamp="$(date -u +%H:%M)"
n_stopped="${#stopped_names[@]}"
names="${stopped_names[*]:-}"
if [ -f "$note_log" ]; then
  printf 'NOTE\torg-switch\t%s UTC: %s: Metorite org %s/%s -> %s/%s as %s. Lanes stopped: %s.%s\n' \
    "$stamp" "$host" "$old_label" "$old_org" "$label" "$new_org" "$email" "$n_stopped" "$late_text" \
    >>"$note_log" 2>/dev/null || say "WARNING: could not append the NOTE line to $note_log."
fi
if mkdir -p -- "$(dirname -- "$state_log")" 2>/dev/null \
  && printf '%s\t%s\t%s/%s\t%s/%s\t%s\tstopped=%s\t%s\tlate=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$host" "$old_label" "$old_org" "$label" "$new_org" "$email" \
    "$n_stopped" "${names:--}" "${late:--}" >>"$state_log" 2>/dev/null; then
  say "active org: $label. Logged to $state_log."
else
  say "WARNING: active org: $label, but the line to $state_log failed. The switch is complete."
fi
restart_hints
exit 0
