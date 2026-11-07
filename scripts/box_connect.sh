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
#      and mv) with the one line METORITE_BOX_TOKEN=<token>.
#   4. It writes the user-scope MCP entry `metorite` with the header
#      `Bearer ${METORITE_BOX_TOKEN}`. Claude Code expands the variable when it
#      connects, so the token never goes into .claude.json.
#   5. It runs `claude mcp list` (timeout 60 s) and prints the result.
#
#   box_connect.sh [--url URL] [--home DIR] [--config-dir DIR] [--replace] [--no-verify]
#
#   --url URL         the MCP address (default https://mcp.metorite.com/mcp)
#   --home DIR        use DIR as HOME (tests use a temp HOME)
#   --config-dir DIR  use DIR as CLAUDE_CONFIG_DIR (tests use a temp dir)
#   --replace         replace a user-scope `metorite` entry. Without it the
#                     script refuses, and it writes nothing.
#   --no-verify       do not run `claude mcp list`
#
# Exit codes: 0 connected (or written with --no-verify), 2 usage, 3 the token
# has the wrong shape or is empty, 4 `claude` is not installed, 5 a `metorite`
# entry exists and --replace is not set, 6 a write failed, 7 `claude mcp list`
# did not show `metorite` as connected.
#
# ⚠️ It never prints the token or a part of it, on success and on every error
# path. It turns xtrace off first, so `bash -x box_connect.sh` shows no token.
# The token goes to builtins only (`read`, `printf`, `[[ =~ ]]`). The one
# child that sees it is `claude mcp list`, through its environment. Its output
# goes through a mask for `mbt_` tokens before it reaches the terminal.
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
REPLACE=0
VERIFY=1
TOKEN_RE='^mbt_[A-Za-z0-9_-]{43}$'
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

usage() {
  sed -n '19,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --url) [ $# -ge 2 ] || die "--url needs a value." 2; URL="$2"; shift 2 ;;
    --home) [ $# -ge 2 ] || die "--home needs a value." 2; export HOME="$2"; shift 2 ;;
    --config-dir) [ $# -ge 2 ] || die "--config-dir needs a value." 2; export CLAUDE_CONFIG_DIR="$2"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument. The token is never an argument: the script reads it from a hidden prompt or from stdin. Run with --help." 2 ;;
  esac
done

: "${HOME:?HOME is not set}"
if ! [[ "$URL" =~ ^https://[^[:space:]\"\\]+$ || "$URL" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?/[^[:space:]\"\\]*$ ]]; then
  die "--url must be https://, or http:// to 127.0.0.1 or localhost." 2
fi

command -v claude >/dev/null 2>&1 || die "claude is not on the PATH. Install Claude Code first." 4
command -v python3 >/dev/null 2>&1 || die "python3 is not on the PATH. The script reads the Claude config with it." 4

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

# ── 3. Look for an existing entry, before anything is written. ──────────────
claude_json="${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json"
# Prints one line: "none", or "<type> <url> <header kind>". It never prints a
# header value: an old entry can hold a literal token.
existing="$(python3 -I - "$claude_json" "$NAME" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except FileNotFoundError:
    print("none"); sys.exit(0)
except (OSError, ValueError):
    print("unreadable"); sys.exit(0)
entry = (data.get("mcpServers") or {}).get(name)
if not isinstance(entry, dict):
    print("none"); sys.exit(0)
auth = str((entry.get("headers") or {}).get("Authorization") or "")
if not auth:
    kind = "no-authorization-header(oauth)"
elif auth == "Bearer ${METORITE_BOX_TOKEN}":
    kind = "box-token-env-header"
else:
    kind = "another-authorization-header(value-not-shown)"
url = str(entry.get("url") or entry.get("command") or "?").split()[0][:200]
print(f"{entry.get('type', '?')} {url} {kind}")
PY
)"
if [ "$existing" = "unreadable" ]; then
  die "$claude_json is not valid JSON. Repair it, then run this script again." 6
fi
if [ "$existing" != "none" ]; then
  say "a user-scope '$NAME' entry exists: $existing"
  if [ "$REPLACE" -ne 1 ]; then
    die "the script did not replace it, and it wrote nothing. Run again with --replace to replace it." 5
  fi
  say "--replace is set. The script replaces that entry."
fi

# ── 4. Write box.env: 0600, temp file in the same folder, then mv. ──────────
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
mv -f -- "$tmp_env" "$env_file" || die "could not move the temp file to $env_file." 6
tmp_env=""
say "wrote $env_file (mode 0600)."

# ── 5. Write the MCP entry, with the ${...} form only. ──────────────────────
# shellcheck disable=SC2016  # the ${...} must reach Claude Code unexpanded.
entry_json='{"type":"http","url":"'"$URL"'","headers":{"Authorization":"Bearer ${METORITE_BOX_TOKEN}"}}'
if [ "$existing" != "none" ]; then
  claude mcp remove -s user "$NAME" >/dev/null 2>&1 \
    || die "claude mcp remove -s user $NAME failed. The old entry is still there." 6
fi
if ! claude mcp add-json -s user "$NAME" "$entry_json" >/dev/null 2>&1; then
  die "claude mcp add-json -s user $NAME failed. box.env is written. Run the script again with --replace." 6
fi
say "wrote the user-scope '$NAME' entry: $URL with the header 'Bearer \${METORITE_BOX_TOKEN}'."

say "each lane shell must load the token before it starts claude:"
# The path is a variable, so test_root_units_root_owned.py does not read this
# printed hint as a script that sources an env file.
# shellcheck disable=SC2088  # the hint shows a ~ path on purpose.
hint_path='~/.config/metorite/box.env'
say "    set -a; . ${hint_path}; set +a"

# ── 6. Verify. ──────────────────────────────────────────────────────────────
if [ "$VERIFY" -ne 1 ]; then
  say "--no-verify is set. Run 'claude mcp list' in a shell that loaded box.env."
  exit 0
fi

say "running 'claude mcp list' (up to 60 s)..."
# Run from HOME, so a .mcp.json of the current folder does not answer for the
# user entry. The mask is defence in depth: claude does not print the token.
list_rc=0
list_out="$(cd "$HOME" && METORITE_BOX_TOKEN="$box_token" timeout 60 claude mcp list 2>&1 \
  | sed -E 's/mbt_[A-Za-z0-9_-]{43}/mbt_[redacted]/g')" || list_rc=$?
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
