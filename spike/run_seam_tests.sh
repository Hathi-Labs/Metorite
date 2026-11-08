#!/bin/bash
# per-file run of the unit tests that reference agent_framework / code_session; one summary line each
export PYTEST_ADDOPTS="-o timeout_method=thread"
out=${1:-.seam.log}; : > "$out"
for f in $(cat ${LIST:-.maf_tests2.txt}); do
  r=$(timeout 300 uv run --no-sync pytest "$f" -q -p no:cacheprovider --timeout=120 -x --no-header 2>&1 | tail -1)
  echo "$f | $r" >> "$out"
done
echo DONE >> "$out"
