---
name: ste
description: >
  Rewrite text into this repo's Simplified Technical English (ASD-STE100), or
  check text that already exists. Use this skill when you write or edit a
  markdown file, a commit message, a pull request body, an agent prompt, a
  runbook step, or a report to the owner. Use it when the ste-lint hook reports
  an error and you must repair the text. Also use it when someone asks to
  simplify, shorten, de-jargon or plain-English a document. Trigger on "STE",
  "Simplified Technical English", "ASD-STE100", "plain English", "simplify this
  doc", "make this readable".
---

# Simplified Technical English

**The contract is `docs/style_ste.md`. Read it once per session before you
rewrite a whole file.** This skill is the procedure. It does not restate the
rules, because a second copy of a rule goes stale and then lies.

**The word list of record is `.claude/hooks/ste-words.json`.**

---

## 1. Decide the tier first

| Tier | Text | Word list |
|---|---|---|
| STRICT | Anything that tells a person or an agent what to **do** | Both lists bind |
| INFORMED | Anything that records **why** a decision was taken | `strictOnly` drops to a warning |

Path defaults are in `docs/style_ste.md` §2. To check one file:

```sh
node .claude/hooks/ste-lint.mjs <file> --warnings
```

The report names the tier in brackets after the file name.

---

## 2. Rewrite in this order

Work top down. Each step feeds the next.

1. **Cut the sentence at the first full stop it earns.** A procedural sentence
   holds 20 words. A descriptive sentence holds 25.
2. **Delete every semicolon.** Write two sentences.
3. **Name the actor.** Change `the check is run` to `the verifier runs the check`.
4. **Replace the flagged words.** The linter gives the replacement.
5. **Break a noun string longer than three words.** Add `of`, `for`, or a hyphen.
6. **Split a paragraph longer than six sentences.** One paragraph holds one topic.
7. **Put the condition before the action.** Write `If the check fails, stop`.
8. **Re-read the first sentence.** It must carry the answer on its own.

---

## 3. What you must not do

- **Do not delete a fact to meet a word count.** Split the sentence.
- **Do not drop an article.** `the` and `a` stay.
- **Do not invent a second name for a thing that has one.** One term, one thing.
- **Do not rewrite a file you were not asked to touch.** The rule is a ratchet.
  See `docs/style_ste.md` §7.
- **Do not weaken a rule to pass the linter.** Add the domain word to
  `technicalAllowed` in `.claude/hooks/ste-words.json` and say so in the diff.
- **Do not rewrite `skills/upstream/`.** A sync overwrites it every week.

---

## 4. Domain words are legal

ASD-STE100 allows a Technical Name and a Technical Verb outside the dictionary.
`tenant`, `migration`, `schema`, `endpoint`, `grant` and `seam` are all correct
STE here. A plain-English rewrite that removes a domain word makes the text
worse, not better.

---

## 5. This binds your replies too

Every message you send to the owner obeys the STRICT tier.

BLUF and STE act on different things, so both hold at once:

- BLUF sets the **order**. The answer comes first.
- STE sets the **language**. Short sentences, plain words, an actor for each verb.

A status line reads `Deployed and verified.` It does not read `I have now gone
ahead and completed the deployment, which appears to have been successful.`

---

## 6. Check your work

```sh
node .claude/hooks/ste-lint.mjs <file> --warnings   # one file
node .claude/hooks/ste-lint.mjs --staged            # the lines you added
node .claude/hooks/ste-lint.test.mjs                # the linter itself
```

Errors must reach zero. Warnings are heuristic, so read each one and decide.
A warning you keep is a choice. Say why in the pull request body.
