#!/usr/bin/env bash
# The headersHelper of the box token entry (MCP-G17, task 1819, EA-19g).
#
# Claude Code runs this command each time it connects the user-scope MCP entry
# `metorite`, and again after a 401 or 403. It reads the box token from the
# file that box_connect.sh writes, and prints one JSON object:
#
#   {"Authorization":"Bearer <token>"}
#
# So a lane does not need METORITE_BOX_TOKEN in its environment. A lane that a
# long-lived `claude daemon` starts never loaded box.env, and a `${VAR}`
# header expands from the environment of that daemon.
#
#   metorite-box-headers [BOX_ENV]
#
# BOX_ENV is the path of the token file. The default is
# $HOME/.config/metorite/box.env. box_connect.sh writes the absolute path into
# the user-scope entry, so no project file can move it. There is no
# environment variable that changes the path: a project settings file can set
# an environment variable, and then point the lanes at a token of another
# member.
#
# It refuses (exit 1, nothing on stdout, one short line on stderr) when the
# file is missing, is not a regular file, is a symlink, is not mode 0600, is
# not owned by this user, or holds no token of the right shape. A refusal
# never shows the token or a part of it.
#
# box_connect.sh installs a copy outside the checkout, at
# ~/.local/bin/metorite-box-headers (mode 0700), so a worktree removal does not
# break the lanes. box_connect.sh also sources this file to read TOKEN_RE, so
# the token shape has one definition.
#
# Fence: tests/unit/test_box_token_secret_rules.py and
# tests/unit/test_box_token_claude_client.py.

# The one definition of the box token shape. box_connect.sh reads it from here.
TOKEN_RE='^mbt_[A-Za-z0-9_-]{43}$'

if [ -z "${BASH_VERSION:-}" ]; then
  printf 'metorite-box-headers: run this with bash.\n' >&2
  exit 1
fi

# When box_connect.sh sources this file, stop here: define TOKEN_RE only.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

{ set +x; } 2>/dev/null
set -u
umask 077
export LC_ALL=C

refuse() {
  unset -v line tok
  printf 'metorite-box-headers: %s\n' "$1" >&2
  exit 1
}

box_env="${1:-}"
if [ -z "$box_env" ]; then
  [ -n "${HOME:-}" ] || refuse "HOME is not set."
  box_env="$HOME/.config/metorite/box.env"
fi

[ ! -L "$box_env" ] || refuse "the token file is a symlink. Run box_connect.sh again."
[ -e "$box_env" ] || refuse "the token file $box_env is missing. Run box_connect.sh."
[ -f "$box_env" ] || refuse "the token file is not a regular file. Run box_connect.sh again."

# GNU stat first, then BSD stat. Mode in octal, then the owner uid.
meta="$(stat -c '%a %u' -- "$box_env" 2>/dev/null || stat -f '%Lp %u' -- "$box_env" 2>/dev/null)" \
  || refuse "could not read the mode of the token file."
mode="${meta%% *}"
owner="${meta##* }"
[ "$mode" = "600" ] || refuse "the token file must have mode 0600, not $mode. Run: chmod 600 $box_env"
[ "$owner" = "$(id -u)" ] || refuse "the token file is not owned by this user."

# Read the file with builtins only. Never source it: sourcing runs its text.
tok=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    METORITE_BOX_TOKEN=*) tok="${line#METORITE_BOX_TOKEN=}"; break ;;
  esac
done <"$box_env"
unset -v line
tok="${tok%$'\r'}"

[ -n "$tok" ] || refuse "the token file holds no METORITE_BOX_TOKEN line."
[[ "$tok" =~ $TOKEN_RE ]] || { unset -v tok; refuse "the token in the file does not have the shape of a box token."; }

# printf is a builtin, so the token makes no argv. The shape has no quote and
# no backslash, so the JSON needs no escape.
printf '{"Authorization":"Bearer %s"}\n' "$tok"
unset -v tok
exit 0
