#!/usr/bin/env node
/**
 * Tests for ste-lint.mjs. Run: node .claude/hooks/ste-lint.test.mjs
 *
 * This file is the fence R7 asks for. ste-lint fails OPEN by design, so a
 * broken linter reports nothing and looks exactly like clean prose. Only this
 * file tells the two apart — run it whenever ste-words.json changes.
 *
 * Each case is [name, text, tier, expectations]. An expectation names a rule and
 * how many findings of it the text must produce. Rules not named are not
 * asserted, so a case can stay readable without pinning every heuristic.
 */
import { lintText } from './ste-lint.mjs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const LINT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'ste-lint.mjs')

const CASES = [
  // --- the word list -------------------------------------------------------
  ['not-approved verb', 'Utilize the migration runner.', 'informed', { 'ste/not-approved': 1 }],
  ['approved rewrite', 'Use the migration runner.', 'informed', {}],
  ['generated -ing form', 'We are utilizing the runner.', 'informed', { 'ste/not-approved': 1 }],
  ['generated -ed form', 'The team utilized the runner.', 'informed', { 'ste/not-approved': 1 }],
  ['explicit noun form', 'Disk utilization is high.', 'informed', { 'ste/not-approved': 1 }],
  ['phrase beats word', 'Run the check prior to the merge.', 'informed', { 'ste/not-approved': 1 }],
  ['abbreviation phrase', 'Some words, e.g. this one, are not approved.', 'informed', { 'ste/not-approved': 1 }],
  ['two hits on one line', 'Utilize the tool in order to help.', 'informed', { 'ste/not-approved': 2 }],

  // --- the two tiers -------------------------------------------------------
  ['strictOnly is an error in STRICT', 'However, the gate is open.', 'strict', { 'ste/not-approved': 1 }],
  ['strictOnly is a warning in INFORMED', 'However, the gate is open.', 'informed', {}],
  ['notApproved binds both tiers', 'Utilize it.', 'strict', { 'ste/not-approved': 1 }],

  // --- technical names and verbs are permitted -----------------------------
  ['technical verb passes', 'Verify the branch, then implement the slice.', 'strict', {}],
  ['technical phrase shields the word', 'Read the primary key.', 'strict', {}],
  ['same word unshielded still fails', 'Read the primary reason.', 'strict', { 'ste/not-approved': 1 }],

  // --- what must never be read as prose ------------------------------------
  ['fenced code is exempt', '```sh\nutilize --all\n```', 'strict', {}],
  ['tilde fence is exempt', '~~~\nutilize --all\n~~~', 'strict', {}],
  ['inline code is exempt', 'The `utilize` flag is gone.', 'strict', {}],
  ['front matter is exempt', '---\nname: utilize\n---\n\nUse it.', 'strict', {}],
  ['html comment is exempt', '<!-- utilize this later -->\nUse it.', 'strict', {}],
  ['a link target is exempt', 'Read [the guide](https://x.test/utilize-me).', 'strict', {}],
  ['link text is NOT exempt', 'Read [utilize me](https://x.test/ok).', 'strict', { 'ste/not-approved': 1 }],
  ['a heading is not exempt', '# Utilize the runner', 'strict', { 'ste/not-approved': 1 }],
  ['a table cell is not exempt', '| step | Utilize it |\n|---|---|', 'strict', { 'ste/not-approved': 1 }],

  // --- punctuation ---------------------------------------------------------
  ['semicolon', 'The gate is open; the branch is clean.', 'informed', { 'ste/semicolon': 1 }],
  ['semicolon inside code is exempt', 'Run `a=1; b=2` now.', 'informed', {}],

  // --- sentence and paragraph length ---------------------------------------
  [
    'descriptive sentence over 25 words',
    'The board row names the owning spec and the owning spec names the acceptance and the acceptance names the command that a person must run to be sure.',
    'informed',
    { 'ste/sentence-length': 1 },
  ],
  [
    'descriptive sentence at the limit',
    'The board row names the owning spec and that spec names the acceptance test which a person runs to be sure of it.',
    'informed',
    {},
  ],
  [
    'procedural sentence over 20 words',
    'Run the migration ledger check against the production database and then read the deployed SHA from the health endpoint before you continue.',
    'informed',
    { 'ste/sentence-length': 1 },
  ],
  [
    'paragraph over six sentences',
    'One is here. Two is here. Three is here. Four is here. Five is here. Six is here. Seven is here.',
    'informed',
    { 'ste/paragraph-length': 1 },
  ],
  [
    'six sentences is allowed',
    'One is here. Two is here. Three is here. Four is here. Five is here. Six is here.',
    'informed',
    {},
  ],
  [
    'a list is not a paragraph',
    '- One is here.\n- Two is here.\n- Three is here.\n- Four is here.\n- Five is here.\n- Six is here.\n- Seven is here.',
    'informed',
    {},
  ],
  ['a file name does not end a sentence', 'Read work_plan.md and then stop.', 'informed', {}],
  [
    // A bullet is a note, not a step. Holding `- **Why:** …` rationale to a
    // step's 20 words applies the rule to text it was not written for.
    'a bulleted note gets the descriptive limit',
    '- **Why:** the copy a customer reads lives in TSX and in the theme layer, and not in markdown at all.',
    'informed',
    {},
  ],
  [
    // This repo opens a sentence with ⚠️ everywhere. A splitter that wants a
    // capital letter next joins the two halves and reports a run-on.
    'a warning glyph still ends the sentence before it',
    'The rule holds in two places today. ⚠️ A person who does not install the commit hook is never bound by it at all.',
    'informed',
    {},
  ],
  [
    'a numbered step gets the procedural limit',
    '1. Run the migration ledger check against the database and then read the deployed SHA from the health endpoint before you open the pull request.',
    'informed',
    { 'ste/sentence-length': 1 },
  ],
  [
    // Found by this rule on its own skill file: a `**` between the full stop and
    // the next word hid the break, so two short steps read as one long one.
    'bold ends a sentence',
    '- **Do not weaken a rule to pass the linter.** Add the domain word to the allow list and then say so in the diff.',
    'informed',
    {},
  ],

  // --- heuristics (warnings) -----------------------------------------------
  ['passive voice', 'The migration was applied by the deploy.', 'informed', { 'ste/passive': 1 }],
  ['-ing verb form', 'The gateway is checking the token.', 'informed', { 'ste/ing': 1 }],
  ['contraction', 'It is a gate, so do not go around it.', 'informed', {}],
  ['noun cluster over three', 'Read the tenant scoped grant vocabulary table.', 'informed', { 'ste/noun-cluster': 1 }],
  ['three-word noun string is fine', 'Read the grant vocabulary table.', 'informed', {}],

  // --- a clean STE paragraph must be silent --------------------------------
  [
    'clean strict prose',
    '# Cut a branch\n\n1. Cut a branch off main.\n2. Make the change.\n3. Run the tests.\n4. Open a PR.\n\nThe loop stops here. A person does the merge.',
    'strict',
    {},
  ],
]

let failed = 0
for (const [name, text, tier, expect] of CASES) {
  const findings = lintText(text, { tier })
  const counts = {}
  for (const f of findings) counts[f.rule] = (counts[f.rule] || 0) + 1

  // With no expectations the text must produce no ERRORS at all. Warnings are
  // allowed there — pinning every heuristic would make each case unreadable.
  const keys = Object.keys(expect)
  let ok
  if (keys.length === 0) {
    ok = findings.every((f) => f.severity !== 'error')
  } else {
    ok = keys.every((k) => (counts[k] || 0) === expect[k])
    // An expectation of an error rule also means nothing ELSE errors.
    const extra = findings.filter((f) => f.severity === 'error' && !keys.includes(f.rule))
    if (extra.length) ok = false
  }

  if (!ok) failed++
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name.padEnd(38)} ${JSON.stringify(counts)}`)
}

// --- the PostToolUse contract -------------------------------------------------
// A bad markdown Write must come back as exit 2, because that is the only exit
// code Claude Code shows to the agent. Exit 1 would be logged and ignored.
const hook = (payload) =>
  spawnSync(process.execPath, [LINT, '--hook'], { input: JSON.stringify(payload), encoding: 'utf8' })

const HOOK_CASES = [
  ['write bad markdown blocks', { tool_name: 'Write', tool_input: { file_path: 'docs/x.md', content: 'Utilize it.' } }, 2],
  ['write clean markdown passes', { tool_name: 'Write', tool_input: { file_path: 'docs/x.md', content: 'Use it.' } }, 0],
  ['edit judges only the new text', { tool_name: 'Edit', tool_input: { file_path: 'docs/x.md', new_string: 'Use it.' } }, 0],
  ['edit with a bad word blocks', { tool_name: 'Edit', tool_input: { file_path: 'docs/x.md', new_string: 'Utilize it.' } }, 2],
  ['a python file is not our business', { tool_name: 'Write', tool_input: { file_path: 'a.py', content: 'utilize = 1' } }, 0],
  ['informed tier lets "however" through', { tool_name: 'Write', tool_input: { file_path: 'project-docs/x.md', content: 'However, it is open.' } }, 0],
  ['strict tier does not', { tool_name: 'Write', tool_input: { file_path: 'docs/x.md', content: 'However, it is open.' } }, 2],
  ['a tier marker overrides the path', { tool_name: 'Write', tool_input: { file_path: 'docs/x.md', content: '<!-- ste-tier: informed -->\nHowever, it is open.' } }, 0],
  ['a broken payload fails open', { tool_name: 'Write' }, 0],
  // The whole .claude tree is STRICT, not only agents/ and commands/.
  ['.claude/AGENTS.md is strict', { tool_name: 'Write', tool_input: { file_path: '.claude/AGENTS.md', content: 'However, it is open.' } }, 2],
  ['a vendored upstream skill is not ours', { tool_name: 'Write', tool_input: { file_path: 'skills/upstream/anthropics/x/SKILL.md', content: 'Utilize it.' } }, 0],
  // An ADOPTED upstream skill sits beside the skills we wrote, because Claude
  // Code reads `.claude/skills/<name>/SKILL.md` and no level deeper. So the
  // path says "ours" and only NOT_OURS says otherwise. Without these two
  // cases, an edit to that list silently makes 41 vendored files STRICT, and
  // the next commit that touches one fails on somebody else's prose.
  ['an adopted upstream skill is not ours', { tool_name: 'Write', tool_input: { file_path: '.claude/skills/frontend-design/SKILL.md', content: 'Utilize it.' } }, 0],
  ['a skill we wrote is still ours', { tool_name: 'Write', tool_input: { file_path: '.claude/skills/ste/SKILL.md', content: 'Utilize it.' } }, 2],
]

for (const [name, payload, expected] of HOOK_CASES) {
  const res = hook(payload)
  const ok = res.status === expected
  if (!ok) failed++
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name.padEnd(38)} exit=${res.status} expected=${expected}`)
  if (!ok && res.stderr) console.log(`        ${res.stderr.split('\n')[0]}`)
}

const total = CASES.length + HOOK_CASES.length
console.log(failed === 0 ? `\nall ${total} cases passed` : `\n${failed} of ${total} FAILED`)
process.exit(failed === 0 ? 0 : 1)
