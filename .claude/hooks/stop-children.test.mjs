import assert from 'node:assert/strict'
import { parsePs, oldChildren, report } from './stop-children.mjs'

const ps = `
  10     1  9000 claude
  11    10  7200 bash -c until [ -f /tmp/x ]; do sleep 5; done
  12    11  7190 sleep 5
  13    10    60 pytest tests/unit
  14     1  9999 unrelated-old-daemon
  15    10  1800 timeout 600 pytest
`
const rows = parsePs(ps)
assert.equal(rows.length, 6)
const old = oldChildren(rows, 10, 1800, 999)
assert.deepEqual(old.map((r) => r.pid), [11, 12, 15], 'descendants of the root, 30 min or older, oldest first')
assert.ok(!old.some((r) => r.pid === 14), 'a process outside the tree is never listed')
assert.ok(!old.some((r) => r.pid === 13), 'a young child is not listed')
assert.deepEqual(oldChildren(rows, 10, 1800, 11).map((r) => r.pid), [12, 15], 'the hook never lists itself')
assert.match(report(old), /3 child process/)
assert.match(report(old), /name the ones that stay/)
console.log('stop-children: ok')
