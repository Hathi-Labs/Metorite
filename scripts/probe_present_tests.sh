#!/usr/bin/env bash
# CIV-31e: the default probe of scripts/bisect_main.sh and of
# scripts/home_server/auto_revert.py. It runs the given pytest node ids in the
# current directory, a checkout of the probed commit. It leaves out each id
# that is not in that tree.
#
# Usage: bash /abs/path/scripts/probe_present_tests.sh <test id>...
#
# Why. A failed test of a red batch can be new in the range. The good commit
# does not hold it, and pytest exits 4 there ("not found"). Before this helper,
# the probe called such a commit "cannot judge", and the bisect stopped.
#
# ⚠️ Run it by its ABSOLUTE path in the running checkout. The probed tree can be
# older than this file, and a relative path then exits 127 (cannot judge).
#
# The absent rule:
#   - The file of an id is the text before its first "::". When that path is
#     not in the tree, the id is absent.
#   - The files that are in the tree are collected once:
#       $PROBE_PYTEST_CMD --collect-only -q -p no:cacheprovider -- <files>
#     When the collection exits 5 (no test), or exits 0 and prints at least
#     one node id of those files, each id whose exact node id is not in that
#     list is absent. A parametrize id can hold spaces, so the match is exact.
#   - When the collection exits with another code (2: a collection error,
#     4: a usage or conftest error), or prints no node id that it can read,
#     no id is left out. pytest runs them all, and its exit code stands.
#     So while the file of an id is in the tree, a collection error is never
#     a pass.
#   - An id with no "::" names a whole path. It is absent only when the path
#     is not there.
#   - Two limits. An id whose file is not in the tree passes with no pytest
#     run, also when the conftest does not load. An id that the -m filter of the
#     repo deselects is absent, because the batch does not run it either.
#
# Exit codes:
#   0  every id is absent (a test that is not in the tree cannot fail), or
#      pytest passed the ids that are present
#   1  pytest failed a present id
#   2  no test id is given
#   *  any other code of pytest: the caller cannot judge the commit
#
# Environment:
#   PROBE_PYTEST_CMD  the pytest command, as shell text. The helper adds the
#                     arguments after it. Default: uv run python -m pytest
#
# Stdout: the output of pytest. Stderr: the absent ids and the notes.
# Fence: tests/unit/test_probe_present_tests.py.
set -uo pipefail

PYTEST_CMD="${PROBE_PYTEST_CMD:-uv run python -m pytest}"

note() { echo "probe_present_tests: $*" >&2; }

# run_pytest <args>: the pytest command with the given arguments.
run_pytest() { eval "$PYTEST_CMD"' "$@"'; }

if [ "$#" -eq 0 ]; then
  note "no test id is given."
  exit 2
fi

absent=()
present=()
files=()
for id in "$@"; do
  file="${id%%::*}"
  if [ -z "$file" ] || [ ! -e "$file" ]; then
    absent+=("$id")
    continue
  fi
  present+=("$id")
  if [[ "$id" == *::* ]]; then
    known=0
    for f in "${files[@]+"${files[@]}"}"; do
      [ "$f" = "$file" ] && known=1
    done
    [ "$known" -eq 0 ] && files+=("$file")
  fi
done

if [ "${#files[@]}" -gt 0 ]; then
  rc=0
  out="$(run_pytest --collect-only -q -p no:cacheprovider -- "${files[@]}")" || rc=$?
  out="${out//$'\r'/}"
  # The node id lines: the text before "::" is one of the collected files.
  nodes=""
  while IFS= read -r line; do
    for f in "${files[@]}"; do
      if [[ "$line" == "$f::"* ]]; then
        nodes+="$line"$'\n'
        break
      fi
    done
  done <<<"$out"
  if [ "$rc" -eq 5 ] || { [ "$rc" -eq 0 ] && [ -n "$nodes" ]; }; then
    keep=()
    for id in "${present[@]}"; do
      if [[ "$id" == *::* ]] && ! grep -Fxq -e "$id" <<<"$nodes"; then
        absent+=("$id")
      else
        keep+=("$id")
      fi
    done
    present=("${keep[@]+"${keep[@]}"}")
  else
    note "the collection exited ${rc}, or printed no node id that it can read. No id is left out."
    printf '%s\n' "$out" | tail -n 20 >&2
  fi
fi

if [ "${#absent[@]}" -gt 0 ]; then
  note "these ids are absent from this tree, so they do not run:"
  printf '  %s\n' "${absent[@]}" >&2
fi
if [ "${#present[@]}" -eq 0 ]; then
  note "ALL_ABSENT: every test id is absent from this tree. A test that is not in the tree cannot fail, so the probe passes."
  exit 0
fi

set -- -x -q -p no:cacheprovider -- "${present[@]}"
eval "exec $PYTEST_CMD"' "$@"'
