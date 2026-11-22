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
#   - CIV-31f: the helper first removes each trailing CR from each id. An id
#     from a CRLF file is then the same id.
#   - The file of an id is the text before its first "::". When that path is
#     not in the tree, the id is absent.
#   - The files that are in the tree are collected once:
#       $PROBE_PYTEST_CMD --collect-only -q -p no:cacheprovider -- <files>
#     When the collection exits 0 or 5, the helper reads the node id lines of
#     each file. CIV-31f: the rule applies to each file. A file that gives at
#     least one node id line is read: each id of that file whose exact node id
#     is not in that list is absent. A parametrize id can hold spaces, so the
#     match is exact (grep -Fx). A file that gives no node id line keeps its
#     ids. A module-level skip (pytest.importorskip, or pytest.skip with
#     allow_module_level=True) collects no test, and so does a file whose
#     tests the -m filter of the repo deselects. Those ids run, and pytest
#     exits 4 or 5: the caller cannot judge the commit.
#   - When the collection exits with another code (2: a collection error,
#     4: a usage or conftest error), no id is left out. pytest runs them all,
#     and its exit code stands. So while the file of an id is in the tree, a
#     collection error is never a pass.
#   - An id with no "::" names a whole path. It is absent only when the path
#     is not there.
#   - One limit. An id whose file is not in the tree passes with no pytest
#     run, also when the conftest does not load.
#
# Exit codes:
#   0  every id is absent (a test that is not in the tree cannot fail), or
#      pytest passed the ids that are present
#   1  pytest failed a present id
#   2  no test id is given
#   *  any other code of pytest: the caller cannot judge the commit
#
# Exit 0 does not always mean that a test ran. CIV-31f: auto_revert.py reads
# the two notes below on stderr, and it calls such a probe "absent", not
# "pass". So the parent of a culprit that added the test is no proof of the
# revert, and a test that main moved or deleted is no sign of a fix.
#
# Environment:
#   PROBE_PYTEST_CMD  the pytest command, as shell text. The helper adds the
#                     arguments after it. Default: uv run python -m pytest
#
# Stdout: the output of pytest. Stderr: the absent ids and the notes.
# Fence: tests/unit/test_probe_present_tests.py.
set -uo pipefail

PYTEST_CMD="${PROBE_PYTEST_CMD:-uv run python -m pytest}"

# CIV-31f: the two absent notes are a contract with
# scripts/home_server/auto_revert.py (PROBE_ABSENT_NOTE and PROBE_ALL_ABSENT
# there). note() puts "probe_present_tests: " before each one. After
# ABSENT_NOTE, each absent id is on its own line, after two spaces. Change
# them only with the reader. Fence: test_civ31f_the_absent_notes_are_one_contract
# in tests/unit/test_auto_revert.py.
ABSENT_NOTE="these ids are absent from this tree, so they do not run:"
ALL_ABSENT_NOTE="ALL_ABSENT: every test id is absent from this tree. A test that is not in the tree cannot fail, so the probe passes."

note() { echo "probe_present_tests: $*" >&2; }

# run_pytest <args>: the pytest command with the given arguments.
run_pytest() { eval "$PYTEST_CMD"' "$@"'; }

if [ "$#" -eq 0 ]; then
  note "no test id is given."
  exit 2
fi

# CIV-31f (F4): an id with a trailing CR names no file in the tree.
ids=()
for id in "$@"; do
  while [[ "$id" == *$'\r' ]]; do id="${id%$'\r'}"; done
  ids+=("$id")
done
set -- "${ids[@]}"

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
  # listed[<file>] marks a file that gave at least one node id line.
  nodes=""
  declare -A listed=()
  while IFS= read -r line; do
    for f in "${files[@]}"; do
      if [[ "$line" == "$f::"* ]]; then
        nodes+="$line"$'\n'
        listed["$f"]=1
        break
      fi
    done
  done <<<"$out"
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 5 ]; then
    keep=()
    for id in "${present[@]}"; do
      file="${id%%::*}"
      if [[ "$id" == *::* ]] && [ -n "${listed[$file]:-}" ] && ! grep -Fxq -e "$id" <<<"$nodes"; then
        absent+=("$id")
      else
        keep+=("$id")
      fi
    done
    present=("${keep[@]+"${keep[@]}"}")
    bare=0
    for f in "${files[@]}"; do
      if [ -z "${listed[$f]:-}" ]; then
        note "the collection (exit ${rc}) gives no node id of ${f}, so its ids are not left out. pytest runs them, and its exit code stands."
        bare=1
      fi
    done
    [ "$bare" -eq 0 ] || printf '%s\n' "$out" | tail -n 20 >&2
  else
    note "the collection exited ${rc}. No id is left out."
    printf '%s\n' "$out" | tail -n 20 >&2
  fi
fi

if [ "${#absent[@]}" -gt 0 ]; then
  note "$ABSENT_NOTE"
  printf '  %s\n' "${absent[@]}" >&2
fi
if [ "${#present[@]}" -eq 0 ]; then
  note "$ALL_ABSENT_NOTE"
  exit 0
fi

set -- -x -q -p no:cacheprovider -- "${present[@]}"
eval "exec $PYTEST_CMD"' "$@"'
