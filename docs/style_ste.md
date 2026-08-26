# Simplified Technical English — the writing contract

**Status: ACTIVE.** Owner directive, 2026-08-26.
**Fence:** `node .claude/hooks/ste-lint.test.mjs` and `node .claude/hooks/ste-lint.mjs --staged`.
**Word list of record:** `.claude/hooks/ste-words.json`. Do not copy it into prose.

This repo writes in Simplified Technical English (STE). STE is a controlled form
of English. It comes from the standard **ASD-STE100**, Issue 9, released on
2025-01-15. The standard has two parts. Part 1 holds 53 writing rules in nine
sections. Part 2 holds a dictionary of about 900 approved words, and a list of
about 1200 words that are not approved.

STE exists for one reason. A reader who is tired, or who reads English as a
second language, must get the same meaning as the writer had. This repo has a
second reason. Most text here is read by an agent, and an agent guesses when the
words are vague.

---

## 1. What this binds

| Surface | Bound | Notes |
|---|---|---|
| Messages from an agent to a person | Yes | Chat replies, status reports, review write-ups |
| Markdown files this repo owns | Yes | The two tiers in §2 set how hard |
| Commit messages, PR titles, PR bodies | Yes | Strict tier |
| Code comments and docstrings | Advisory | No fence. Write them this way anyway |
| Product UI copy | **No** | Owner decision, 2026-08-26. See the note below |
| `skills/upstream/` | No | Synced from Anthropic. We do not own the words |

**STE stops at the repo.** It binds the documentation we write and the agents we
run. It does not bind the app. No string a customer reads is in scope, in the
Control Plane, in the Operator Console, or in any pane.

The fence already matches that line by construction. `ste-lint.mjs` reads `.md`
and `.mdx` files and nothing else, so a TSX file, a theme token and a product
string never reach it. Do not widen it to reach them.

The reason is the audience. STE serves a reader who must act on a procedure
without guessing. Product copy serves a customer who must want the thing, and it
answers to the design system.

---

## 2. Two tiers

One rule set, two strengths. The tier changes the word list and nothing else.
Every grammar, sentence and punctuation rule binds in both tiers.

| Tier | Where | The word list |
|---|---|---|
| **STRICT** | All of `.claude/`, all of `docs/`, `HANDOFF.md`, `README.md`, `CONTRIBUTING.md`, and any name that holds `RUNBOOK`, `HOWTO`, `QUICKSTART`, `GO_LIVE`, `SETUP` or `INSTALL` | Both lists are errors |
| **INFORMED** | Everything else. `project-docs/`, the `AGENTS.md` files outside `.claude/`, decision records | `notApproved` is an error. `strictOnly` drops to a warning |

STRICT covers text that tells a person or an agent what to **do**. INFORMED
covers text that records **why** a decision was taken.

The reason for two tiers is narrow. A decision record earns the right to a
hedging word, because the hedge is the record. A runbook step does not.

To override the tier for one file, put this on its own line near the top:

```markdown
<!-- ste-tier: strict -->
```

---

## 3. The rules

Paraphrased from ASD-STE100 Part 1. The **Fence** column names what makes a
breach fail (rule R7). "Advisory" means a person must catch it.

### 3.1 Words

| # | Rule | Fence |
|---|---|---|
| W1 | Use only an approved word, a Technical Name, or a Technical Verb. See §4 | `ste/not-approved` |
| W2 | One word, one meaning. Do not use `test` as both a noun and a verb | Advisory |
| W3 | Keep to one term for one thing. Do not call it a board in one line and a queue in the next | Advisory |
| W4 | Do not use a word to sell. Name the measurable property | `ste/not-approved` |
| W5 | Write the words out. Do not use a contraction | `ste/contraction` (warning) |

### 3.2 Noun phrases

| # | Rule | Fence |
|---|---|---|
| N1 | A string of nouns holds at most three words | `ste/noun-cluster` (warning) |
| N2 | Break a longer string with `of`, `for`, or a hyphen | `ste/noun-cluster` (warning) |
| N3 | Keep the article. Do not drop `the` or `a` to save a word | Advisory |

### 3.3 Verbs

| # | Rule | Fence |
|---|---|---|
| V1 | Use the infinitive, the imperative, the simple present, the simple past, or the simple future | Advisory |
| V2 | Use the past participle as an adjective only | Advisory |
| V3 | Use the active voice. Name the actor | `ste/passive` (warning) |
| V4 | An `-ing` form is allowed inside a Technical Name only | `ste/ing` (warning) |
| V5 | In a description, the passive voice is allowed when the actor is unknown | Advisory |

### 3.4 Sentences

| # | Rule | Fence |
|---|---|---|
| S1 | A procedural sentence holds at most **20** words | `ste/sentence-length` |
| S2 | A descriptive sentence holds at most **25** words | `ste/sentence-length` |
| S3 | A paragraph holds at most **6** sentences and one topic | `ste/paragraph-length` |
| S4 | Give one instruction per sentence | Advisory |
| S5 | Use a vertical list for a set of conditions or parts | Advisory |

### 3.5 Procedures

| # | Rule | Fence |
|---|---|---|
| P1 | Start a step with the imperative. Write `Run the tests`, not `The tests should be run` | Advisory |
| P2 | Put the condition first. Write `If the check fails, cut a branch` | Advisory |
| P3 | Number the steps when the order matters | Advisory |

### 3.6 Descriptions

| # | Rule | Fence |
|---|---|---|
| D1 | Say what a thing is, then what it does, then what depends on it | Advisory |
| D2 | Keep a paragraph to one topic | `ste/paragraph-length` |

### 3.7 Warnings and cautions

| # | Rule | Fence |
|---|---|---|
| A1 | Put the warning **before** the step it guards | Advisory |
| A2 | Start with a command or a condition, not with a reason | Advisory |
| A3 | State the damage and how to prevent it | Advisory |

### 3.8 Punctuation

| # | Rule | Fence |
|---|---|---|
| U1 | Do not use a semicolon. Write two sentences | `ste/semicolon` |
| U2 | Do not use a slash between two words. Write `and` or `or`, and say which | `ste/not-approved` |
| U3 | Write `for example` and `that is` out. Do not use the Latin short forms | `ste/not-approved` |
| U4 | Do not close a list with `etc.` Name the items | `ste/not-approved` |

### 3.9 Practice

| # | Rule | Fence |
|---|---|---|
| R1 | Answer first, then give the reason. See §5 | Advisory |
| R2 | Give a number when you have one. `4 deploys`, not `several deploys` | Advisory |
| R3 | Do not hedge a fact you checked. State it | Advisory |
| R4 | Say what you did not do, and why | Advisory |

---

## 4. Technical Names and Technical Verbs

ASD-STE100 lets a writer use a word outside the dictionary when the word names a
part, a state, or a technical action. This is what makes STE usable for software
at all. `tenant`, `migration`, `schema`, `endpoint` and `grant` are all legal STE
in this repo.

The list is `technicalAllowed` in `.claude/hooks/ste-words.json`. Add a word
there when the linter reports a word the domain needs. Add the word. Do not
weaken a rule.

Two limits hold:

- A Technical Name must be a thing in this system, not a word you like better.
- Do not invent a second name for a thing that already has one. That breaks W3.

---

## 5. How this composes with BLUF

They do not fight. They act on different things.

- **BLUF** sets the **order**. The answer comes first. Reasons come after.
- **STE** sets the **language**. Short sentences, plain words, active voice.

A reply obeys both: the first sentence carries the conclusion, and every
sentence in it is short and plain.

---

## 6. The fence

| Command | What it reads | Exit |
|---|---|---|
| `node .claude/hooks/ste-lint.mjs <file>` | The whole file | 1 on any error |
| `node .claude/hooks/ste-lint.mjs --staged` | Added lines of staged markdown | 1 on any error |
| `node .claude/hooks/ste-lint.mjs --baseline` | Every markdown file we own | Always 0 |
| `node .claude/hooks/ste-lint.test.mjs` | The linter itself | 1 on any failure |

Add `--warnings` to see the heuristic findings. Add `--json` for machine output.

Two hooks run it without being asked:

1. **PostToolUse**, in `.claude/settings.json`. It reads the text an agent just
   wrote to a markdown file. An error comes back to the agent as exit 2.
2. **pre-commit**, in `.pre-commit-config.yaml`. It reads the added lines of
   staged markdown. An error stops the commit.

The linter **fails open**. A crash prints one line and allows the work. This is
the opposite of `plan-guard.mjs`, and the reason is in the header of
`.claude/hooks/ste-lint.mjs`. `plan-guard` guards credentials and production. This
guards prose.

---

## 7. What is grandfathered

`.pre-commit-config.yaml` states the house rule already: grandfather and ratchet.
New work and edited work must pass. Legacy text is paid down when someone next
touches it.

Measured on 2026-08-26, before any rewrite:

| Files we own | Errors | Warnings |
|---|---|---|
| 196 | 19094 | 8612 |

Read the current number with `--baseline`. Do not open a pull request that
rewrites a file only to lower it. A doc gets rewritten when the work touches it.

The count is why the fence reads **added lines only**. One line changed inside
`work_plan.md` must not fail on that file's whole backlog. A gate that blocks
ordinary work gets removed, and it takes the real gates with it.

---

## 8. Open questions

| # | Question | State |
|---|---|---|
| Q1 | Does STE bind product UI copy? | **Closed 2026-08-26. No.** See §1 |
| Q2 | Should `pr-check.yml` run `--staged` on a pull request, and block? | Open. Owner. See H-55 |

---

## 9. Source and copyright

ASD, Brussels, owns ASD-STE100. This file paraphrases the rule set. It does not
reproduce the text of the standard, and it does not reproduce the dictionary.
`.claude/hooks/ste-words.json` holds a cut-down list of the words this repo hits,
with a plain replacement for each.

Get the official copy, at no cost, from <https://www.asd-ste100.org/>.
