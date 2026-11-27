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
# It refuses when the file is missing, is not a regular file, is a symlink, has
# more than one hard link, is not mode 0600, is not owned by this user, or
# holds no token of the right shape. It opens the file once, and checks and
# reads that open descriptor. A refusal prints one short reason on stderr,
# never the token or a part of it.
#
# ⚠️ A refusal still prints an Authorization header, the sentinel
# {"Authorization":"Bearer box-token-helper-refused"}, and exits 0. Claude
# Code ignores the stdout of a helper that exits nonzero. With no
# Authorization header it then starts OAuth discovery and Dynamic Client
# Registration (proved with Claude Code 2.1.332, review of 2026-11-26). With
# the sentinel, the server answers 401, and Claude Code does not start OAuth
# when a helper supplies Authorization. An EXIT trap prints the sentinel on
# every other way out too, for example an unexpected error.
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
# The bearer of a refusal. It is never a token: it does not match TOKEN_RE.
REFUSED_BEARER='box-token-helper-refused'

if [ -z "${BASH_VERSION:-}" ]; then
  printf 'metorite-box-headers: run this with bash.\n' >&2
  printf '{"Authorization":"Bearer %s"}\n' "$REFUSED_BEARER"
  exit 0
fi

# When box_connect.sh sources this file, stop here: define the two constants.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

{ set +x; } 2>/dev/null
set -u
umask 077
export LC_ALL=C

printed=0
# shellcheck disable=SC2317  # the EXIT trap calls it.
on_exit() {
  if [ "$printed" != 1 ]; then
    printf '{"Authorization":"Bearer %s"}\n' "$REFUSED_BEARER"
    exit 0
  fi
}
trap on_exit EXIT

refuse() {
  unset -v line tok
  printf 'metorite-box-headers: %s\n' "$1" >&2
  exit 0  # the EXIT trap prints the sentinel header
}

box_env="${1:-}"
if [ -z "$box_env" ]; then
  [ -n "${HOME:-}" ] || refuse "HOME is not set."
  box_env="$HOME/.config/metorite/box.env"
fi

# Cheap checks by name first. A FIFO would block the open, so the type check
# comes before it.
[ ! -L "$box_env" ] || refuse "the token file is a symlink. Run box_connect.sh again."
[ -e "$box_env" ] || refuse "the token file $box_env is missing. Run box_connect.sh."
[ -f "$box_env" ] || refuse "the token file is not a regular file. Run box_connect.sh again."

# Open the file once. Every later check and the read use this descriptor, so
# a swap of the name after the checks cannot change what is read.
{ exec {fd}<"$box_env"; } 2>/dev/null || refuse "could not open the token file."

# GNU stat first, then BSD stat, on the open descriptor: the mode in octal,
# the owner uid, the link count, then the type.
meta="$(stat -L -c '%a %u %h %F' "/dev/fd/$fd" 2>/dev/null \
  || stat -L -f '%Lp %u %l %HT' "/dev/fd/$fd" 2>/dev/null)" \
  || refuse "could not read the mode of the token file."
read -r mode owner nlink ftype <<<"$meta"
case "$ftype" in
  "regular file"|"Regular File") ;;
  *) refuse "the token file is not a regular file. Run box_connect.sh again." ;;
esac
[ "$mode" = "600" ] || refuse "the token file must have mode 0600, not $mode. Run: chmod 600 $box_env"
[ "$owner" = "$(id -u)" ] || refuse "the token file is not owned by this user."
# A hard link is a second name that another folder can expose.
[ "$nlink" = "1" ] || refuse "the token file has $nlink hard links, not 1. Run box_connect.sh again."

# Read the file with builtins only, from the open descriptor. Never source it:
# sourcing runs its text.
tok=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    METORITE_BOX_TOKEN=*) tok="${line#METORITE_BOX_TOKEN=}"; break ;;
  esac
done <&"$fd"
exec {fd}<&-
unset -v line
tok="${tok%$'\r'}"

[ -n "$tok" ] || refuse "the token file holds no METORITE_BOX_TOKEN line."
[[ "$tok" =~ $TOKEN_RE ]] || { unset -v tok; refuse "the token in the file does not have the shape of a box token."; }

# printf is a builtin, so the token makes no argv. The shape has no quote and
# no backslash, so the JSON needs no escape.
printf '{"Authorization":"Bearer %s"}\n' "$tok"
printed=1
unset -v tok
exit 0
