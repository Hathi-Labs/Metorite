#!/usr/bin/env node
/**
 * ste-lint — the fence for this repo's Simplified Technical English rules.
 *
 * Contract: `docs/style_ste.md`. Word list of record: `ste-words.json` beside
 * this file. Tests: `node .claude/hooks/ste-lint.test.mjs`.
 *
 * ⚠️ THIS FAILS OPEN, and that is the opposite of `plan-guard.mjs` on purpose.
 * plan-guard defends credentials and production, so an unevaluated call there is
 * not a safe call. This defends prose. A style checker that crashes and stops a
 * commit teaches one lesson — disable the style checker — and takes the real
 * gate down with it the next time someone edits the hook block. The test file is
 * what keeps this honest, not a hard exit.
 *
 * ⚠️ DIFF-SCOPED BY DEFAULT, which is the only reason it is survivable.
 * `.pre-commit-config.yaml` already states the house philosophy: grandfather and
 * ratchet, new and edited work must pass, legacy is paid down when it is next
 * touched. Measured 2026-08-26: 196 markdown files we own carry 19094 errors.
 * If editing one line of `work_plan.md` (583 KB) failed on that file's whole
 * backlog, the hook would be removed within a week. So `--staged` reads ADDED
 * LINES ONLY. Whole-file checking is opt-in (`<file>` or `--baseline`).
 *
 * TWO TIERS (owner decision, 2026-08-26):
 *   STRICT   — anything that tells a human or an agent what to DO. Full word
 *              list. `.claude/agents|commands|skills`, `docs/`, runbooks,
 *              HANDOFF.md, README, CONTRIBUTING.
 *   INFORMED — rationale prose (decision records, why-comments). Every grammar,
 *              sentence and punctuation rule still binds; the hedging/connective
 *              words in `strictOnly` drop to warnings so nuance survives.
 * Override per file with an HTML comment: `<!-- ste-tier: strict|informed -->`.
 *
 * Modes:
 *   ste-lint.mjs <file...>            whole file(s). exit 1 on any error.
 *   ste-lint.mjs --staged             added lines of staged *.md. exit 1 on error.
 *   ste-lint.mjs --baseline           whole repo, summary only, always exit 0.
 *   ste-lint.mjs --hook               PostToolUse. Payload JSON on stdin.
 *   ste-lint.mjs --text --tier strict text on stdin (used by the tests and /ste).
 * Add --warnings to show heuristic findings, --json for machine output.
 */

import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = process.env.CLAUDE_PROJECT_DIR || path.resolve(HERE, '..', '..')

// ---------------------------------------------------------------- limits ----
// ASD-STE100 Part 1. Procedural sentences are held shorter than descriptive
// ones because a step is read while the reader's hands are busy.
const MAX_PROCEDURAL_WORDS = 20
const MAX_DESCRIPTIVE_WORDS = 25
const MAX_SENTENCES_PER_PARAGRAPH = 6

// ------------------------------------------------------------ word lists ----
const WORDS = JSON.parse(readFileSync(path.join(HERE, 'ste-words.json'), 'utf-8'))

const ALLOWED_WORDS = new Set(
  WORDS.technicalAllowed.filter((w) => !w.includes(' ')).map((w) => w.toLowerCase()),
)
const ALLOWED_PHRASES = WORDS.technicalAllowed
  .filter((w) => w.includes(' '))
  .map((w) => w.toLowerCase())

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')

/**
 * Every surface form of one dictionary entry.
 *
 * `verb: true` generates -s/-ed/-ing rather than making the data file list them,
 * because an entry a human cannot read at a glance is an entry nobody maintains.
 * Irregular forms (doubled consonants, noun forms) stay explicit in `forms`.
 */
function formsOf(base, entry) {
  const out = new Set([base])
  if (entry.verb && !base.includes(' ')) {
    const stem = base.endsWith('e') ? base.slice(0, -1) : base
    out.add(base + 's').add(stem + 'ed').add(stem + 'ing')
  }
  for (const f of entry.forms || []) out.add(f)
  return [...out]
}

function compile(map) {
  const rules = []
  for (const [base, entry] of Object.entries(map)) {
    for (const form of formsOf(base, entry)) {
      // Lookarounds, not \b: entries like `e.g.` and `and/or` end in a character
      // \b treats as a boundary in the wrong place.
      rules.push({
        base,
        form,
        instead: entry.instead,
        re: new RegExp(`(?<![\\w-])${esc(form)}(?![\\w])`, 'gi'),
      })
    }
  }
  // Longest first, so `in order to` is reported instead of nothing and
  // `take into account` beats a shorter overlapping entry.
  return rules.sort((a, b) => b.form.length - a.form.length)
}

const NOT_APPROVED = compile(WORDS.notApproved)
const STRICT_ONLY = compile(WORDS.strictOnly)

// ------------------------------------------------------------------ tier ----
const STRICT_PATH = [
  // The whole harness tree, not only agents/ and commands/. Every file under
  // .claude/ tells an agent what to do, and `.claude/AGENTS.md` says so itself.
  /(^|[\\/])\.claude[\\/]/i,
  /(^|[\\/])docs[\\/]/i,
  /(^|[\\/])(HANDOFF|CONTRIBUTING|README)\.md$/i,
  /(RUNBOOK|HOWTO|QUICKSTART|GO_LIVE|SETUP|INSTALL)/i,
]

/**
 * Files this repo does not own.
 *
 * `skills/upstream/` is rewritten every week by skills-upstream-sync.yml from
 * anthropics/skills and VoltAgent/awesome-agent-skills. A rewrite there is undone
 * by the next sync, and failing a sync pull request on somebody else's prose
 * stops the sync. A vendored file is not a style problem, it is a copy.
 *
 * `.github/skills/` is NOT on this list. Nothing syncs it, so it is ours.
 */
const NOT_OURS = [
  /^skills\/upstream\//,
  // Vendored verbatim from github.com/dickwu/apple-design-skill @ d0bac1e.
  // A refresh re-copies the upstream files, so a rewrite here is undone too.
  /^\.claude\/skills\/apple-design\//,
  // Adopted verbatim from skills/upstream/anthropics/ at SHA 53048666.
  //
  // ⚠️ THE PATH CANNOT CARRY THE PROVENANCE, so this list carries it. Claude
  // Code reads `.claude/skills/<name>/SKILL.md` and finds nothing one level
  // deeper, so an adopted skill must sit beside the skills we wrote. A reader
  // cannot tell the two apart from the path. Add a name here when you adopt
  // one. Delete the name when you drop it.
  /^\.claude\/skills\/(webapp-testing|frontend-design|skill-creator|theme-factory|brand-guidelines)\//,
  /(^|\/)node_modules\//,
  /(^|\/)(CHANGELOG|LICENSE|NOTICE)(\.md)?$/i,
]

const relOf = (file) => path.relative(ROOT, path.resolve(ROOT, file)).replace(/\\/g, '/')
const ours = (file) => !NOT_OURS.some((re) => re.test(relOf(file)))

function tierFor(file, text = '') {
  const marker = text.match(/<!--\s*ste-tier:\s*(strict|informed)\s*-->/i)
  if (marker) return marker[1].toLowerCase()
  const rel = path.relative(ROOT, path.resolve(ROOT, file)).replace(/\\/g, '/')
  return STRICT_PATH.some((re) => re.test(rel)) ? 'strict' : 'informed'
}

// -------------------------------------------------------------- stripping ----
const blankSame = (s) => s.replace(/[^\n]/g, ' ')

/**
 * Blank out everything that is not prose, WITHOUT changing the line count.
 * Line numbers are the whole value of a lint report; a strip that shifts them
 * produces findings nobody can act on.
 */
function stripNonProse(text) {
  let t = text.replace(/<!--[\s\S]*?-->/g, blankSame)

  const out = []
  let fence = null
  let inFrontMatter = false
  const lines = t.split(/\r?\n/)

  lines.forEach((line, i) => {
    if (i === 0 && /^---\s*$/.test(line)) {
      inFrontMatter = true
      return out.push({ n: 1, kind: 'meta', clean: '' })
    }
    if (inFrontMatter) {
      if (/^---\s*$/.test(line)) inFrontMatter = false
      return out.push({ n: i + 1, kind: 'meta', clean: '' })
    }

    const fenceOpen = line.match(/^\s*(```+|~~~+)/)
    if (fence) {
      if (fenceOpen && fenceOpen[1][0] === fence[0]) fence = null
      return out.push({ n: i + 1, kind: 'code', clean: '' })
    }
    if (fenceOpen) {
      fence = fenceOpen[1]
      return out.push({ n: i + 1, kind: 'code', clean: '' })
    }
    // Four-space indents are code only when they are not a nested list item.
    if (/^ {4,}\S/.test(line) && !/^\s*([-*+]|\d+[.)])\s/.test(line)) {
      return out.push({ n: i + 1, kind: 'code', clean: '' })
    }

    const clean = line
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/`[^`]*`/g, ' CODE ')
      .replace(/https?:\/\/\S+/g, ' URL ')
      .replace(/<\/?[A-Za-z][^>]*>/g, ' ')

    let kind = 'prose'
    if (/^\s*$/.test(line)) kind = 'blank'
    else if (/^\s{0,3}#{1,6}\s/.test(line)) kind = 'heading'
    else if (/^\s*\|/.test(line)) kind = 'table'
    else if (/^\s*([-*+]|\d+[.)])\s/.test(line)) kind = 'list'
    else if (/^\s{0,3}(={3,}|-{3,}|\*{3,}|_{3,})\s*$/.test(line)) kind = 'blank'

    out.push({ n: i + 1, kind, clean })
  })

  return out
}

// -------------------------------------------------------------- sentences ----
const ABBREV = new Set([
  'e.g', 'i.e', 'etc', 'vs', 'cf', 'no', 'fig', 'ref', 'approx', 'incl', 'min',
  'max', 'mr', 'ms', 'dr', 'inc', 'ltd', 'co', 'st', 'al', 'jan', 'feb', 'mar',
  'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
])

/** Split into sentences, keeping each one's offset so it can be given a line. */
function sentences(text) {
  const out = []
  let start = 0
  for (let i = 0; i < text.length; i++) {
    if (!'.!?'.includes(text[i])) continue
    const after = text.slice(i + 1)
    // A break needs whitespace-or-end after it, then a capital, digit or quote.
    // `*` and `_` ride along because markdown puts them where a quote would go:
    // `**Do not weaken a rule.** Add the word.` is two sentences, and a splitter
    // that cannot see past the closing `**` reads it as one 23-word run-on.
    if (!/^["')\]*_]*(\s|$)/.test(after)) continue
    // The next sentence starts with anything that is NOT a lowercase letter.
    // A capital or a digit is the usual case. This repo also opens sentences
    // with `⚠️`, `§` and a backtick, and an [A-Z0-9] test read every one of
    // those as no break at all — which turned two sentences into one 43-word
    // violation that was not there.
    if (!/^["')\]*_]*(\s+["'(\[*_]*[^\sa-z]|\s*$)/u.test(after)) continue
    const before = text.slice(start, i)
    const lastWord = (before.match(/([\w.]+)$/) || [, ''])[1].toLowerCase()
    if (ABBREV.has(lastWord.replace(/\.$/, ''))) continue
    if (/(^|[^\w])[A-Za-z]$/.test(before)) continue // single-letter initial
    out.push({ text: text.slice(start, i + 1).trim(), offset: start })
    start = i + 1
  }
  const tail = text.slice(start).trim()
  if (tail) out.push({ text: tail, offset: start })
  return out
}

const IMPERATIVE = new Set(
  ('do run use set make check read write open close start stop add remove delete ' +
   'install cut keep put give send get find go see build deploy apply call click ' +
   'select enter type press connect copy move create update flip land ship stay ' +
   'tell ask name list show refuse cite prefer never always fix edit merge push ' +
   'pull commit revert restore rotate flag report record note extend follow')
    .split(' '),
)

function isProcedural(sentence) {
  const first = (sentence.match(/[A-Za-z]+/) || [''])[0].toLowerCase()
  return IMPERATIVE.has(first) || /^\s*(do not|don't)\b/i.test(sentence)
}

const countWords = (s) =>
  s
    .replace(/[*_~>#|]/g, ' ')
    .split(/\s+/)
    .filter((w) => /[A-Za-z0-9]/.test(w)).length

// --------------------------------------------------------------- findings ----
const STOPWORDS = new Set(
  ('the a an this that these those its their each every any and or but of in on ' +
   'to for with by at from as is are was were be been being not no if then so ' +
   'between into over under through within without across against about during ' +
   'outside inside beside below above near next around toward per off onto ' +
   'upon such only just also because where which who whom whose what how why ' +
   'than when while after before it we you they he she i our your all more most ' +
   'can cannot may must should will would could does do did has have had ' +
   'either neither both other another same own new old next last first')
    .split(' '),
)

const DETERMINER = /(?:^|[\s(])(the|a|an|this|that|these|those|its|their|each|every|any)\s+$/i

function wordFindings(line, tier) {
  const found = []
  const lower = line.clean.toLowerCase()
  const covered = [] // spans already reported, so a long phrase wins over a word

  const allowedSpans = []
  for (const phrase of ALLOWED_PHRASES) {
    let i = lower.indexOf(phrase)
    while (i !== -1) {
      allowedSpans.push([i, i + phrase.length])
      i = lower.indexOf(phrase, i + 1)
    }
  }

  const scan = (rules, severity) => {
    for (const rule of rules) {
      rule.re.lastIndex = 0
      let m
      while ((m = rule.re.exec(line.clean)) !== null) {
        const s = m.index
        const e = s + m[0].length
        if (ALLOWED_WORDS.has(m[0].toLowerCase())) continue
        if (allowedSpans.some(([a, b]) => s >= a && e <= b)) continue
        if (covered.some(([a, b]) => s < b && e > a)) continue
        covered.push([s, e])
        found.push({
          line: line.n,
          rule: 'ste/not-approved',
          severity,
          message: `"${m[0]}" is not approved. Instead: ${rule.instead}`,
        })
      }
    }
  }

  scan(NOT_APPROVED, 'error')
  scan(STRICT_ONLY, tier === 'strict' ? 'error' : 'warning')

  // Rule 8: no semicolons. A semicolon joins two thoughts a reader must hold at
  // once, which is the thing STE exists to stop.
  if (line.kind !== 'code' && line.clean.includes(';')) {
    found.push({
      line: line.n,
      rule: 'ste/semicolon',
      severity: 'error',
      message: 'semicolon — split it into two sentences',
    })
  }

  // ---- heuristics: warnings only, in both tiers ----
  const passive = /\b(is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w{3,}(?:ed|en))\b/i.exec(line.clean)
  if (passive) {
    found.push({
      line: line.n,
      rule: 'ste/passive',
      severity: 'warning',
      message: `"${passive[0]}" looks passive — name who or what does it`,
    })
  }

  const ing = /\b(is|are|was|were|be|been|being|by|when|while|after|before)\s+(\w{4,}ing)\b/i.exec(line.clean)
  if (ing) {
    found.push({
      line: line.n,
      rule: 'ste/ing',
      severity: 'warning',
      message: `"${ing[0]}" — an -ing form is allowed only inside a technical name`,
    })
  }

  const contraction = /\b\w+n't\b|\b\w+'(re|ve|ll|d|m)\b/i.exec(line.clean)
  if (contraction) {
    found.push({
      line: line.n,
      rule: 'ste/contraction',
      severity: 'warning',
      message: `"${contraction[0]}" — write the words out`,
    })
  }

  // Noun cluster: STE caps a noun string at 3. Reported only when a determiner
  // introduces 4+ plain words in a row, because looser tests fire on lists.
  // Deliberately NOT case-insensitive. A noun string in prose is lowercase, and
  // the `i` flag made the ` CODE ` placeholder left by an inline code span read
  // as one of the nouns — so `` `AGENTS.md` files outside `.claude/` `` reported
  // a cluster that has two words in it.
  const cluster = /(?:^|[\s(])(?:[Tt]he|[Aa]n?|[Tt]his|[Tt]hat|[Tt]hese|[Tt]hose|[Ii]ts|[Tt]heir|[Ee]ach|[Ee]very|[Aa]ny)\s+((?:[a-z][a-z-]+\s+){3,}[a-z][a-z-]+)/.exec(line.clean)
  if (cluster) {
    const run = cluster[1].trim().split(/\s+/)
    const nouny = run.every((w) => !STOPWORDS.has(w.toLowerCase()) && !/ly$/i.test(w))
    if (nouny) {
      found.push({
        line: line.n,
        rule: 'ste/noun-cluster',
        severity: 'warning',
        message: `"${run.join(' ')}" — a noun string of more than 3 words; add "of" or a hyphen`,
      })
    }
  }

  return found
}

/**
 * Sentence-length and paragraph-length rules.
 *
 * Skipped in `--staged` mode: an added line is a fragment, and counting the
 * sentences of half a paragraph reports a violation that does not exist.
 */
const LIST_MARKER = /^\s*([-*+]|\d+[.)])\s+/

/**
 * One list ITEM is one unit, not the whole list.
 *
 * Joining a list and splitting it on full stops reads seven bullets as one
 * 28-word sentence, and then reports a length violation that is not there. The
 * marker also has to come off: the `.` in `1.` is a sentence break to any
 * splitter that has not been told otherwise.
 */
function itemsOf(block) {
  const units = []
  for (const line of block) {
    if (line.kind === 'list' || units.length === 0) units.push([line])
    else units[units.length - 1].push(line)
  }
  return units
}

function structureFindings(lines, tier) {
  const found = []
  let block = []

  const flush = () => {
    if (block.length === 0) return
    const listy = block.some((l) => l.kind === 'list')

    for (const unit of listy ? itemsOf(block) : [block]) {
      const joined = unit.map((l) => l.clean.replace(LIST_MARKER, '')).join('\n')
      const sents = sentences(joined)

      if (!listy && sents.length > MAX_SENTENCES_PER_PARAGRAPH) {
        found.push({
          line: unit[0].n,
          rule: 'ste/paragraph-length',
          severity: 'error',
          message: `${sents.length} sentences in one paragraph — the limit is ${MAX_SENTENCES_PER_PARAGRAPH}, and a paragraph holds one topic`,
        })
      }

      // A NUMBERED item is a step, so it takes the 20-word procedural limit. A
      // bulleted item is not: half the notes in this repo are `- **Why:** …`
      // rationale, and holding those to a step's length is the standard applied
      // to text it was not written for. A bullet is judged by its own first word.
      const ordered = /^\s*\d+[.)]\s/.test(unit[0].clean)

      for (const s of sents) {
        const words = countWords(s.text)
        const proc = ordered || isProcedural(s.text)
        const limit = proc ? MAX_PROCEDURAL_WORDS : MAX_DESCRIPTIVE_WORDS
        if (words <= limit) continue
        const upto = joined.slice(0, s.offset)
        const line = unit[Math.min(upto.split('\n').length - 1, unit.length - 1)].n
        found.push({
          line,
          rule: 'ste/sentence-length',
          severity: 'error',
          message: `${words} words in one ${proc ? 'procedural' : 'descriptive'} sentence — the limit is ${limit}`,
        })
      }
    }
    block = []
  }

  for (const l of lines) {
    if (l.kind === 'prose' || l.kind === 'list') block.push(l)
    else flush()
  }
  flush()
  return found
}

// ------------------------------------------------------------------- API ----
export function lintText(text, { tier = 'informed', structure = true } = {}) {
  const lines = stripNonProse(text)
  const found = []
  for (const l of lines) {
    if (l.kind === 'code' || l.kind === 'meta' || l.kind === 'blank') continue
    found.push(...wordFindings(l, tier))
  }
  if (structure) found.push(...structureFindings(lines, tier))
  return found.sort((a, b) => a.line - b.line)
}

export function lintFile(file) {
  const text = readFileSync(file, 'utf-8')
  return { tier: tierFor(file, text), findings: lintText(text, { tier: tierFor(file, text) }) }
}

// ------------------------------------------------------------------- CLI ----
function report(file, tier, findings, { showWarnings, json }) {
  const errors = findings.filter((f) => f.severity === 'error')
  const warns = findings.filter((f) => f.severity === 'warning')
  if (json) {
    process.stdout.write(JSON.stringify({ file, tier, findings }) + '\n')
    return errors.length
  }
  const shown = showWarnings ? findings : errors
  if (shown.length) {
    process.stdout.write(`\n${file}  [${tier}]\n`)
    for (const f of shown) {
      const tag = f.severity === 'error' ? 'error  ' : 'warn   '
      process.stdout.write(`  ${tag} ${String(f.line).padStart(5)}  ${f.rule.padEnd(20)} ${f.message}\n`)
    }
    if (!showWarnings && warns.length) {
      process.stdout.write(`  (${warns.length} warning${warns.length === 1 ? '' : 's'} hidden — rerun with --warnings)\n`)
    }
  }
  return errors.length
}

function stagedAddedLines() {
  const diff = execFileSync('git', ['diff', '--cached', '-U0', '--', '*.md'], {
    encoding: 'utf-8',
    cwd: ROOT,
    maxBuffer: 32 * 1024 * 1024,
  })
  const files = new Map()
  let file = null
  let lineNo = 0
  for (const line of diff.split(/\r?\n/)) {
    const head = line.match(/^\+\+\+ b\/(.+)$/)
    if (head) {
      file = head[1]
      if (!files.has(file)) files.set(file, [])
      continue
    }
    const hunk = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)/)
    if (hunk) {
      lineNo = Number(hunk[1])
      continue
    }
    if (file && line.startsWith('+') && !line.startsWith('+++')) {
      files.get(file).push({ n: lineNo++, text: line.slice(1) })
    }
  }
  return files
}

async function main() {
  const argv = process.argv.slice(2)
  const opt = {
    showWarnings: argv.includes('--warnings'),
    json: argv.includes('--json'),
  }
  const flags = new Set(argv.filter((a) => a.startsWith('--')))
  const files = argv.filter((a) => !a.startsWith('--'))

  // ---- PostToolUse: judge only what was just written ----
  if (flags.has('--hook')) {
    let raw = ''
    process.stdin.setEncoding('utf8')
    for await (const chunk of process.stdin) raw += chunk
    const payload = JSON.parse(raw)
    const input = payload.tool_input || {}
    const file = String(input.file_path || input.notebook_path || '')
    if (!/\.(md|mdx)$/i.test(file) || !ours(file)) process.exit(0)

    // A Write replaces the file, so the whole body is new. An Edit adds only its
    // new_string — checking the rest would bill this author for legacy debt.
    const isWrite = payload.tool_name === 'Write'
    const body = isWrite
      ? String(input.content || '')
      : [input.new_string, ...(input.edits || []).map((e) => e.new_string)]
          .filter(Boolean)
          .join('\n\n')
    if (!body.trim()) process.exit(0)

    const tier = tierFor(file, body)
    const findings = lintText(body, { tier, structure: isWrite })
    const errors = findings.filter((f) => f.severity === 'error')
    const warns = findings.filter((f) => f.severity === 'warning')

    if (errors.length) {
      process.stderr.write(
        `STE (${tier}) — ${errors.length} error${errors.length === 1 ? '' : 's'} in the text just written to ${file}:\n` +
          errors.slice(0, 20).map((f) => `  ${f.rule}: ${f.message}`).join('\n') +
          `\n\nRewrite that text to the contract in docs/style_ste.md, then continue.\n` +
          `Line numbers are relative to the text you wrote. Check the file with:\n` +
          `  node .claude/hooks/ste-lint.mjs ${file} --warnings\n`,
      )
      process.exit(2)
    }
    if (warns.length) {
      process.stdout.write(
        JSON.stringify({
          hookSpecificOutput: {
            hookEventName: 'PostToolUse',
            additionalContext:
              `STE (${tier}) advisory on ${file}: ` +
              warns.slice(0, 6).map((f) => `${f.rule} — ${f.message}`).join('; '),
          },
        }),
      )
    }
    process.exit(0)
  }

  // ---- pre-commit: added lines only ----
  if (flags.has('--staged')) {
    let errors = 0
    for (const [file, added] of stagedAddedLines()) {
      if (added.length === 0 || !ours(file)) continue
      const full = (() => {
        try {
          return readFileSync(path.join(ROOT, file), 'utf-8')
        } catch {
          return ''
        }
      })()
      const tier = tierFor(file, full)
      // Rebuild a sparse document so line numbers stay the file's own.
      const maxLine = added[added.length - 1].n
      const sparse = new Array(maxLine).fill('')
      for (const a of added) sparse[a.n - 1] = a.text
      const findings = lintText(sparse.join('\n'), { tier, structure: false })
      errors += report(file, tier, findings, opt)
    }
    if (errors) {
      process.stdout.write(
        `\n${errors} STE error${errors === 1 ? '' : 's'} in the lines you added.\n` +
          `Contract: docs/style_ste.md. Only ADDED lines are checked — legacy text is grandfathered.\n`,
      )
    }
    process.exit(errors ? 1 : 0)
  }

  // ---- baseline: the whole repo, never fails ----
  if (flags.has('--baseline')) {
    const list = execFileSync('git', ['ls-files', '*.md'], { encoding: 'utf-8', cwd: ROOT })
      .split(/\r?\n/)
      .filter(Boolean)
    const rows = []
    let totals = { error: 0, warning: 0 }
    for (const rel of list) {
      if (!ours(rel)) continue
      let res
      try {
        res = lintFile(path.join(ROOT, rel))
      } catch {
        continue
      }
      const e = res.findings.filter((f) => f.severity === 'error').length
      const w = res.findings.length - e
      totals.error += e
      totals.warning += w
      rows.push({ rel, tier: res.tier, e, w })
    }
    rows.sort((a, b) => b.e - a.e)
    if (opt.json) {
      process.stdout.write(JSON.stringify({ totals, files: rows }, null, 2) + '\n')
    } else {
      process.stdout.write(`STE baseline — ${rows.length} markdown files\n`)
      process.stdout.write(`  errors: ${totals.error}   warnings: ${totals.warning}\n\n`)
      process.stdout.write('  errors  warns  tier      file\n')
      for (const r of rows.slice(0, 30)) {
        process.stdout.write(
          `  ${String(r.e).padStart(6)}  ${String(r.w).padStart(5)}  ${r.tier.padEnd(8)}  ${r.rel}\n`,
        )
      }
      if (rows.length > 30) process.stdout.write(`  … ${rows.length - 30} more files\n`)
    }
    process.exit(0)
  }

  // ---- text on stdin ----
  if (flags.has('--text')) {
    const tierArg = argv[argv.indexOf('--tier') + 1]
    let raw = ''
    process.stdin.setEncoding('utf8')
    for await (const chunk of process.stdin) raw += chunk
    const findings = lintText(raw, { tier: tierArg === 'strict' ? 'strict' : 'informed' })
    const errors = report('<stdin>', tierArg || 'informed', findings, { ...opt, showWarnings: true })
    process.exit(errors ? 1 : 0)
  }

  // ---- whole files ----
  if (files.length === 0) {
    process.stdout.write(readFileSync(fileURLToPath(import.meta.url), 'utf-8').split('*/')[0])
    process.exit(0)
  }
  let errors = 0
  for (const f of files) {
    const res = lintFile(f)
    errors += report(f, res.tier, res.findings, opt)
  }
  process.exit(errors ? 1 : 0)
}

// Fail open. See the header: this guards prose, not production.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    process.stderr.write(`ste-lint could not run (${err.message}). Skipping.\n`)
    process.exit(0)
  })
}
