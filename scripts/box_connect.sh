#!/usr/bin/env bash
# Connect a headless box to Metorite with a box token (EA-19e, D-EA-5).
#
# A member makes a box token in Integrations, AI agents. The token is `mbt_`
# and 43 URL-safe characters. It has no refresh token, so a stale session on
# the box cannot sign out the others (the incident of 2026-11-06,
# external_agent_access.md §10.9). This script puts the token on the box:
#
#   1. It reads the token from a hidden prompt, or from stdin when stdin is
#      not a terminal. Never from argv, because argv shows in `ps`.
#   2. It checks the shape `^mbt_[A-Za-z0-9_-]{43}$` before it writes a file.
#   3. It writes ~/.config/metorite/box.env (mode 0600, umask 077, temp file
#      and mv -T) with the one line METORITE_BOX_TOKEN=<token>.
#   4. It writes the user-scope MCP entry `metorite` with the header
#      `Bearer ${METORITE_BOX_TOKEN}`. Claude Code expands the variable when it
#      connects, so the token never goes into .claude.json.
#   5. From the project folder, it runs `claude mcp get metorite` and then
#      `claude mcp list` (timeout 60 s each). The effective entry must be the
#      user entry with the box token header.
#
#   box_connect.sh [--url URL] [--project DIR] [--home DIR] [--config-dir DIR] [--replace] [--no-verify]
#
#   --url URL         the MCP address (default https://mcp.metorite.com/mcp)
#   --project DIR     the checkout where the lanes start claude. The default is
#                     the current folder when its .mcp.json names `metorite`.
#   --home DIR        use DIR as HOME (tests use a temp HOME)
#   --config-dir DIR  use DIR as CLAUDE_CONFIG_DIR (tests use a temp dir)
#   --replace         replace a user-scope or local-scope `metorite` entry.
#                     Without it the script refuses, and it writes nothing.
#   --no-verify       do not run `claude mcp get` and `claude mcp list`
#
# Exit codes: 0 connected (or written with --no-verify), 2 usage, 3 the token
# has the wrong shape or is empty, 4 `claude` is not installed, 5 a `metorite`
# entry exists and --replace is not set, 6 a write failed, 7 the effective
# `metorite` entry is not the user entry with the box token header, or it is
# not connected.
#
# ⚠️ A project entry wins over the user entry. In a checkout of this repo,
# `.mcp.json` names `metorite` with no header. When the settings of the user
# enable it (enabledMcpjsonServers), Claude Code uses it, sends no bearer and
# starts OAuth. The verify step finds this and stops with exit 7.
#
# ⚠️ It never prints the token or a part of it, on success and on every error
# path. It turns xtrace off first, so `bash -x box_connect.sh` shows no token.
# The token goes to builtins only (`read`, `printf`, `[[ =~ ]]`). The only
# children that see it are `claude mcp get` and `claude mcp list`, through
# their environment. Their output goes through a mask for `mbt_` tokens
# before it reaches the terminal.
#
# Runbook: docs/box_connect.md.
# Fence: tests/unit/test_box_token_secret_rules.py and
# tests/unit/test_box_token_claude_client.py.
{ set +x; } 2>/dev/null
set -Eeuo pipefail
umask 077
export LC_ALL=C

NAME="metorite"
URL="https://mcp.metorite.com/mcp"
PROJECT=""
REPLACE=0
VERIFY=1
TOKEN_RE='^mbt_[A-Za-z0-9_-]{43}$'
# Printable ASCII only: no space, no control character, no DEL.
PRINTABLE_RE='^[!-~]+$'
box_token=""
tmp_env=""

# shellcheck disable=SC2317  # the EXIT trap calls it.
cleanup() {
  unset -v box_token METORITE_BOX_TOKEN
  if [ -n "$tmp_env" ] && [ -e "$tmp_env" ]; then
    rm -f -- "$tmp_env"
  fi
}
trap cleanup EXIT
# The ERR trap names the line only. $BASH_COMMAND is not printed.
trap 'printf "box-connect: ERROR: a command failed at line %s.\n" "$LINENO" >&2' ERR

say() { printf 'box-connect: %s\n' "$*"; }
die() { printf 'box-connect: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
mask() { sed -E 's/mbt_[A-Za-z0-9_-]{43}/mbt_[redacted]/g'; }

usage() {
  sed -n '21,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --url) [ $# -ge 2 ] || die "--url needs a value." 2; URL="$2"; shift 2 ;;
    --project) [ $# -ge 2 ] || die "--project needs a value." 2; PROJECT="$2"; shift 2 ;;
    --home) [ $# -ge 2 ] || die "--home needs a value." 2; export HOME="$2"; shift 2 ;;
    --config-dir) [ $# -ge 2 ] || die "--config-dir needs a value." 2; export CLAUDE_CONFIG_DIR="$2"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument. The token is never an argument: the script reads it from a hidden prompt or from stdin. Run with --help." 2 ;;
  esac
done

: "${HOME:?HOME is not set}"
if ! [[ "$URL" =~ $PRINTABLE_RE ]] || [[ "$URL" == *[\"\\]* ]]; then
  die "--url must be printable ASCII, with no space, no quote and no backslash." 2
fi
if ! [[ "$URL" =~ ^https://.+$ || "$URL" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?/.*$ ]]; then
  die "--url must be https://, or http:// to 127.0.0.1 or localhost." 2
fi

command -v claude >/dev/null 2>&1 || die "claude is not on the PATH. Install Claude Code first." 4
command -v python3 >/dev/null 2>&1 || die "python3 is not on the PATH. The script reads the Claude config with it." 4

# The entry is proved to be valid JSON before anything is written or removed.
# shellcheck disable=SC2016  # the ${...} must reach Claude Code unexpanded.
entry_json='{"type":"http","url":"'"$URL"'","headers":{"Authorization":"Bearer ${METORITE_BOX_TOKEN}"}}'
if ! python3 -I -c 'import json,sys; json.loads(sys.argv[1])' "$entry_json" >/dev/null 2>&1; then
  die "the MCP entry for this --url is not valid JSON. Nothing was written." 2
fi

# ── The project folder: where the lanes start claude. ───────────────────────
if [ -n "$PROJECT" ]; then
  [ -d "$PROJECT" ] || die "--project $PROJECT is not a folder." 2
  PROJECT="$(cd -- "$PROJECT" && pwd)"
elif [ -f .mcp.json ] && python3 -I - .mcp.json "$NAME" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        servers = json.load(fh).get("mcpServers") or {}
except (OSError, ValueError, AttributeError):
    sys.exit(1)
sys.exit(0 if isinstance(servers, dict) and sys.argv[2] in servers else 1)
PY
then
  PROJECT="$(pwd)"
fi
verify_dir="${PROJECT:-$HOME}"
verify_dir_physical="$(cd -- "$verify_dir" && pwd -P)" || die "could not open $verify_dir." 2
if [ -n "$PROJECT" ]; then
  say "project folder: $PROJECT"
fi

# ── 1. Read the token. Never from argv. ─────────────────────────────────────
if [ -t 0 ]; then
  printf 'Paste the box token (it does not show): ' >&2
  IFS= read -rs box_token || true
  printf '\n' >&2
else
  IFS= read -r box_token || true
fi
box_token="${box_token%$'\r'}"

# ── 2. Check the shape before anything is written. ──────────────────────────
if [ -z "$box_token" ]; then
  die "no token was given. Paste the token from Integrations, AI agents." 3
fi
if ! [[ "$box_token" =~ $TOKEN_RE ]]; then
  die "the token does not have the shape of a box token (mbt_ and 43 URL-safe characters). Copy it again from Integrations, AI agents. Nothing was written." 3
fi

# ── 3. Look for existing entries, before anything is written. ───────────────
claude_json="${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json"
# Prints "unreadable", or one line for each entry found:
# "<scope> <type> <url> <header kind>", where the scope is user or local.
# It never prints a header value: an old entry can hold a literal token. It
# masks a token in the address and drops each character that is not printable.
existing="$(python3 -I - "$claude_json" "$NAME" "$verify_dir" "$verify_dir_physical" <<'PY'
import json, re, sys
path, name, dirs = sys.argv[1], sys.argv[2], sys.argv[3:]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except FileNotFoundError:
    sys.exit(0)
except (OSError, ValueError):
    print("unreadable"); sys.exit(0)
if not isinstance(data, dict):
    print("unreadable"); sys.exit(0)

def describe(scope, entry):
    headers = entry.get("headers") if isinstance(entry.get("headers"), dict) else {}
    auth = str(headers.get("Authorization") or "")
    if not auth:
        kind = "no-authorization-header(oauth)"
    elif auth == "Bearer ${METORITE_BOX_TOKEN}":
        kind = "box-token-env-header"
    else:
        kind = "another-authorization-header(value-not-shown)"
    url = str(entry.get("url") or entry.get("command") or "?")
    url = re.sub(r"mbt_[A-Za-z0-9_-]{43}", "mbt_[redacted]", url)
    url = "".join(c for c in url if "!" <= c <= "~")[:200] or "?"
    kind_type = "".join(c for c in str(entry.get("type", "?")) if "!" <= c <= "~")[:20] or "?"
    print(f"{scope} {kind_type} {url} {kind}")

entry = (data.get("mcpServers") or {}).get(name)
if isinstance(entry, dict):
    describe("user", entry)
projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
for d in dict.fromkeys(dirs):
    proj = projects.get(d)
    if isinstance(proj, dict):
        local = (proj.get("mcpServers") or {}).get(name)
        if isinstance(local, dict):
            describe("local", local)
            break
PY
)"
if [ "$existing" = "unreadable" ]; then
  die "$claude_json is not valid JSON. Repair it, then run this script again." 6
fi
old_user=""
old_local=""
while IFS= read -r row; do
  case "$row" in
    "user "*) old_user="${row#user }" ;;
    "local "*) old_local="${row#local }" ;;
  esac
done <<<"$existing"
if [ -n "$old_user" ]; then
  say "a user-scope '$NAME' entry exists: $old_user"
fi
if [ -n "$old_local" ]; then
  say "a local-scope '$NAME' entry exists for $verify_dir: $old_local"
  say "a local-scope entry wins over the user entry in that folder."
fi
if [ -n "$old_user$old_local" ]; then
  if [ "$REPLACE" -ne 1 ]; then
    die "the script did not replace it, and it wrote nothing. Run again with --replace to replace it." 5
  fi
  say "--replace is set. The script replaces it."
fi

# ── 4. Write box.env: 0600, temp file in the same folder, then mv -T. ───────
env_dir="$HOME/.config/metorite"
env_file="$env_dir/box.env"
mkdir -p -- "$env_dir" || die "could not make $env_dir." 6
chmod 700 -- "$env_dir" || die "could not set the mode of $env_dir." 6
tmp_env="$(mktemp "$env_dir/.box.env.XXXXXX")" || die "could not make a temp file in $env_dir." 6
chmod 600 -- "$tmp_env" || die "could not set the mode of the temp file." 6
# printf is a builtin, so the token makes no argv.
if ! printf 'METORITE_BOX_TOKEN=%s\n' "$box_token" >"$tmp_env"; then
  die "could not write the temp file." 6
fi
# -T: when box.env is a folder, mv fails. It never moves the file into it.
mv -fT -- "$tmp_env" "$env_file" 2>/dev/null \
  || die "could not move the temp file to $env_file. Is it a folder?" 6
tmp_env=""
say "wrote $env_file (mode 0600)."

# ── 5. Write the MCP entry, with the ${...} form only. ──────────────────────
restore_hint() {
  if [ -n "$old_user" ]; then
    say "the old user-scope entry was: $old_user"
  fi
  if [ -n "$old_local" ]; then
    say "the old local-scope entry for $verify_dir was: $old_local"
  fi
}
if [ -n "$old_local" ]; then
  (cd -- "$verify_dir" && claude mcp remove -s local "$NAME" >/dev/null 2>&1) \
    || die "claude mcp remove -s local $NAME failed in $verify_dir. The old entry is still there." 6
fi
if [ -n "$old_user" ]; then
  claude mcp remove -s user "$NAME" >/dev/null 2>&1 \
    || { restore_hint; die "claude mcp remove -s user $NAME failed. The old user entry is still there." 6; }
fi
if ! claude mcp add-json -s user "$NAME" "$entry_json" >/dev/null 2>&1; then
  restore_hint
  die "claude mcp add-json -s user $NAME failed. box.env is written. Run the script again with --replace." 6
fi
say "wrote the user-scope '$NAME' entry: $URL with the header 'Bearer \${METORITE_BOX_TOKEN}'."

say "each lane shell must load the token before it starts claude:"
# The path is a variable, so test_root_units_root_owned.py does not read this
# printed hint as a script that sources an env file.
# shellcheck disable=SC2088  # the hint shows a ~ path on purpose.
hint_path='~/.config/metorite/box.env'
say "    set -a; . ${hint_path}; set +a"

# ── 6. Verify, from the folder where the lanes start claude. ────────────────
if [ "$VERIFY" -ne 1 ]; then
  say "--no-verify is set. Run 'claude mcp get $NAME' in $verify_dir, in a shell that loaded box.env."
  exit 0
fi

# 6a. Which entry wins in that folder? `claude mcp get` names its scope.
say "running 'claude mcp get $NAME' in $verify_dir (up to 60 s)..."
get_rc=0
get_out="$(cd -- "$verify_dir" && METORITE_BOX_TOKEN="$box_token" timeout 60 claude mcp get "$NAME" 2>&1 | mask)" || get_rc=$?
scope_line="$(printf '%s\n' "$get_out" | grep -m 1 -E '^[[:space:]]*Scope:' || true)"
scope_line="${scope_line#"${scope_line%%[![:space:]]*}"}"
case "$scope_line" in
  *"User config"*) ;;
  *"Project config"*)
    say "$scope_line"
    die "in $verify_dir the project entry '$NAME' of .mcp.json wins over the user entry. It has no header, so Claude Code sends no box token and starts OAuth. The token does nothing here. An owner must close the project route. Move '$NAME' from enabledMcpjsonServers to disabledMcpjsonServers in the settings of the user (${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json, or the settings file that enables it). Then run this script again." 7
    ;;
  *"Local config"*)
    say "$scope_line"
    die "in $verify_dir a local-scope entry '$NAME' wins over the user entry. The token does nothing here. Remove it with: cd $verify_dir && claude mcp remove -s local $NAME. Or run this script again with --replace." 7
    ;;
  *)
    die "'claude mcp get $NAME' (exit $get_rc) did not name the scope of the entry in $verify_dir. The script cannot prove that the user entry wins." 7
    ;;
esac
# shellcheck disable=SC2016  # the literal ${...} is what the entry must show.
if ! printf '%s\n' "$get_out" | grep -qF 'Authorization: Bearer ${METORITE_BOX_TOKEN}'; then
  die "the user-scope '$NAME' entry in $verify_dir does not show the header 'Bearer \${METORITE_BOX_TOKEN}'. Run this script again with --replace." 7
fi
say "in $verify_dir the effective '$NAME' entry is the user entry with the box token header."

# 6b. Does it connect?
say "running 'claude mcp list' in $verify_dir (up to 60 s)..."
list_rc=0
list_out="$(cd -- "$verify_dir" && METORITE_BOX_TOKEN="$box_token" timeout 60 claude mcp list 2>&1 | mask)" || list_rc=$?
printf '%s\n' "$list_out"
line="$(printf '%s\n' "$list_out" | grep -E "^${NAME}:" | head -n 1 || true)"
if [ "$list_rc" -eq 0 ] && [[ "$line" == *Connected* ]] && [[ "$line" != *Failed* ]]; then
  say "'$NAME' is connected."
  exit 0
fi
if [[ "$line" == *"OAuth fallback is disabled"* || "$line" == *"rejected the configured Authorization header"* ]]; then
  die "the server rejected the token. It is revoked, expired or wrong. Make a new token in Integrations, AI agents." 7
fi
die "'$NAME' is not connected (claude mcp list exit $list_rc). See the output above." 7
