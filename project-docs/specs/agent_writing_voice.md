# How Metorite writes — the voice of every agent and every draft

**Status.** ACTIVE. Owner directive, 2026-10-10. Board row **WS-52**.
Verified against code on 2026-10-10.

S1 is built on branch `agent-writing-voice`. S2 follows in a second PR. S3
and S4 are spec only.

**Owner of the text.** `packages/acb_llm/acb_llm/voice.py` holds the text of
record. §3 copies it, and `tests/unit/test_agent_voice.py` fails when the two
differ.

---

## 1. The owner's ask

The owner asked for this on 2026-10-10. The words are the owner's, so they
sit in a code block, out of reach of the STE lint.

```text
"Create proper instructions for how the agents of Metorite actually produce
text … for both agent sessions, but also during drafting things like
descriptions, headings, email drafts … A baseline level of clarity … should
also be given to all of the agents … The agent harness for each of these
agents should at least have a good philosophy for creating and generating
text … for all agents that we currently have, as well as the agents in the
future."
```

The owner also said that the agent may later learn the voice of a member from
the emails that the member sent. S3 records that.

## 2. Scope and non-goals

**In scope.**

- One contract of twelve rules, and one overlay for each of five surfaces.
- Every agent reads the contract, on both runtimes, and so does each agent
  that a later PR adds.
- Every direct model call that drafts text for a member reads the contract
  and one overlay (S2).
- A deterministic checker for evals and tests, and an eval set.

**Non-goals.**

- The checker never changes the text of an answer, and no live path calls it.
- No rule here binds the repo's docs. STE binds those (§5).
- No rule here binds quoted text, code or data. Rule 12 says so.
- S1 changes no tool, no route and no schema, and it adds no flag.

## 3. The contract

This is the text that every agent reads. The fenced block below must match
`acb_llm.voice.CORE` byte for byte.

```text
1. Give the answer or the result first. No opening line about what you are going to do, and no closing line that repeats it.
2. Write the way a capable colleague talks: plain words, active voice, concrete verbs. Write "decide", not "make a decision".
3. Let sentence length follow the thought. Do not give the sentences in one paragraph the same shape.
4. Say what is so. Do not frame it against what it is not, and do not build up to a reveal.
5. Give as many items as the facts hold. Do not round to three, and do not pair ideas for rhythm.
6. Cut filler (genuinely, really, truly, actually, very, just, simply) and corporate verbs (leverage, utilize, underscore, reflect, streamline, empower, unlock).
7. No em dashes. Use a comma, a full stop or brackets.
8. If something is uncertain, say so once, plainly, where it applies. Do not hedge anything else.
9. No performed enthusiasm, no praise for the question, and no apology unless something went wrong.
10. Stay inside the records. Name the source (task #141, the 3 Oct email from Priya). Never invent a name, number, date or quote.
11. Untangle stacked nouns: "the review of how we approve vendor payments", not "the vendor payment approval process review".
12. Match the member's language. Use a list only when the items stand apart. These rules cover your own words, never quoted text, code or data.
```

### 3.1 The overlays

A drafting call adds the core and one overlay. An agent reads all five,
because an agent writes on each surface.

```text
chat:        the answer in the first sentence. Formatting only where it helps scanning.
title:       up to 8 words, sentence case, no final full stop, no "X: Y" subtitle.
description: (task, project, card body) what, why, and when it counts as done, in 1 to 3 short sentences.
email:       the member's own voice. One clear ask when possible. No "I hope this finds you well".
summary:     (digests, meeting notes, reports) decisions and facts first, then open items. No adjectives that pass judgment.
```

The owner may edit the wording. Edit `voice.py` and §3 in the same PR, and the
fence keeps them equal.

## 4. Three changes from the owner's list

The owner's list was the input. Three rules changed on purpose.

1. **Hedging became "state real uncertainty once".** The list banned hedges.
   An honest statement of uncertainty is a safety property. A member who acts
   on a guess that read as a fact can pay money or lose a customer. So rule 8
   keeps one plain statement, where it applies, and bans every other hedge.
2. **The blanket ban on short side-by-side clauses went.** Short clauses side
   by side are part of how a colleague talks. A ban on them makes the text
   stiff. Rule 3 asks for varied shape instead.
3. **"Never invent a fact" is new.** Rule 10 makes the agent name its source,
   and it bans an invented name, number, date or quote. A fluent voice that
   invents facts is worse than a stiff one.

## 5. Two audiences, two contracts

| Contract | Binds | Reader |
|---|---|---|
| STE, `docs/style_ste.md` | The repo's docs, commits, PR bodies, and agent replies to the owner | An engineer or an agent that must act without a guess |
| This contract | What the product writes for a member: chat replies, titles, descriptions, emails and summaries | A member of a customer org |

`docs/style_ste.md` §1 already puts product copy out of scope for STE. This
contract fills that gap for text that a model writes. Neither one replaces the
other. An agent of this repo that answers the owner still writes STE.

## 6. S1 as built — the core, every agent, the checker

### 6.1 One module

`packages/acb_llm/acb_llm/voice.py` holds:

| Name | What it is |
|---|---|
| `CORE` | The twelve rules of §3 |
| `OVERLAYS` | One rule set for each surface: `chat`, `title`, `description`, `email`, `summary` |
| `voice_prompt(surface)` | The heading, the core and one overlay. `AGENT`, the default, gives every overlay. An unknown surface raises `KeyError` |
| `voice_lint(text, surface=None)` | The checker of §6.5 |

The module is in `acb_llm` because the orchestrator and the gateway both
import that package, and S2 calls it from gateway routes. It is pure text and
regular expressions, with no import of its own.

### 6.2 The seam on each runtime

`orchestrator/_tool_injection.py` `_apply_voice` is the one place that puts
the voice into an agent's system prompt. `_inject_agent_tools` calls it first,
before any tool work. So a box that collects no platform tool still gives the
voice.

| Runtime | Where the voice goes |
|---|---|
| Copilot SDK (`GitHubCopilotAgent`, the `_tools` shape) | `_default_options["system_message"]["content"]`, right after the agent's own text. The Copilot addendum comes after it |
| Native MAF `Agent` | `default_options["instructions"]`, right after `instructions.md`. The registry block, the UI rule, the output rule and the attachment rule come after it |
| Older MAF shape (a string `instructions`) | That attribute, the same way |

The heading `## How Metorite writes` is the idempotency marker. A second
injection on one agent adds nothing.

**Why not inside the addendum.** Three tests pin the Copilot addendum to
`acb_skills.addendum.rendered_parts` byte for byte. The QM-2 index mode
(`SKILLS_INDEX_ONLY`) moves every addendum section into a body that loads on
demand. The voice must never load on demand. So it stays out of the addendum
and goes in beside it.

**Prompt caching.** The text is static, and it lands before the memory block
and before `CACHE_BREAK`. So it is part of the stable prefix, and the cache
covers it on every turn. Nothing adds it per turn.

### 6.3 Every agent, now and later

Every in-tree agent goes through `_inject_agent_tools`, and so does each agent
that the registry loads from a repo. So a new agent gets the voice with no
edit. The fence builds all eight in-tree agents with their own factory, and
three new agents of each shape.

**`floor_opt_out` cannot remove the voice.** The opt-out reads the names of
tools only (`FLOOR_OPT_OUT_ALLOWED`), and the voice is not a tool. That is the
decision of this spec: an agent may add a stricter rule in its own
instructions, and it never drops the core.

### 6.4 The cost

Measured on 2026-10-10.

| Text | Run-context tokenizer (chars/4) | tiktoken `o200k_base` |
|---|---|---|
| `CORE` | 347 | 325 |
| One surface (`voice_prompt("email")`) | 376 | 355 |
| The agent block (`voice_prompt(AGENT)`) | 478 | 457 |

Each agent pays the agent block once in its prefix. A Projects turn makes
about 5.7 requests, and the cache covers each one after the first.
`projects_ai_chat.md` §25 records the rest of the prefix. The core is a
little over the target of 300, because it keeps the owner's wording. The
ratchet in `test_agent_voice.py` holds the core at 360 and the agent block at
500. To grow either one, edit that test on purpose.

### 6.5 The checker

`voice_lint` is deterministic, and it calls no model. It skips fenced code,
inline code, quoted lines (`>`) and quoted spans of up to 300 characters.

| Rule id | What it finds |
|---|---|
| `em_dash` | An em dash (U+2014) |
| `filler` | `genuinely`, `really`, `truly`, `actually`, `very`, `just`, `simply` |
| `corporate_verb` | `leverage`, `utilize`, `underscore`, `reflect`, `streamline`, `empower`, `unlock`, with their other forms |
| `opener` | A first line that starts with `Great question`, `Good question`, `Certainly`, `Absolutely`, `Of course`, `I'd be happy to` or `Let me` |
| `closer` | A sentence that starts with "In summary", "In conclusion", "To sum up", "To summarize" or "Overall,", or the words "Hope this helps" |
| `not_x_but_y` | "not X, but Y" and "it's not X, it's Y". A "but" that a pronoun follows is a plain contrast, and it does not match |
| `hope_finds_you_well` | "I hope this email finds you well" and "Hope you are well" |
| `title_length`, `title_full_stop`, `title_subtitle` | With `surface="title"` only |
| `description_length` | With `surface="description"` only, more than 3 sentences |

**Advisory.** The checker cannot see rules 3, 4 (a reveal), 5, 8, 10 or 11.
Those need a model grader, which is S4.

## 7. The eval

`evals/agent_voice/` holds twelve fixed cases over the five surfaces
(`cases.json`).

- `python -m evals.agent_voice.run --scripted` scores hand-written answers,
  and checks that the checker finds exactly the expected rules. It calls no
  model. The unit job runs it through `tests/unit/test_agent_voice_eval.py`.
- `python -m evals.agent_voice.run --live` is a manual run. It asks each case
  twice, once with a plain system prompt and once with the voice. It scores
  both arms and writes `baseline.json`. It needs `LITELLM_BASE_URL`.

**The baseline is not recorded yet.** No model was reachable from the build
machine on 2026-10-10, and CI has none. The first person with a local stack
runs `--live` and commits `baseline.json`. S4 makes that a tracked score.

## 8. The slices

| Slice | What | Done when | Fence | Gate |
|---|---|---|---|---|
| **S1** | The core, the overlays, every agent on both runtimes, the checker, the eval set | Every in-tree agent and a new agent of each shape hold `CORE` once, after their own instructions. `floor_opt_out` cannot remove it. The core stays under its ceiling. The scripted eval passes | `tests/unit/test_agent_voice.py`, `tests/unit/test_agent_voice_eval.py` | AGENT-SAFE |
| **S2** | Every direct model call that drafts member text reads `voice_prompt(<surface>)` | Each registered drafting site holds the voice, with the right overlay. A new member-facing prompt builder with no voice fails the sweep. A machine-only prompt is on an explicit allowlist with a reason | `tests/unit/test_drafting_voice.py` (a registry plus an AST sweep) | AGENT-SAFE |
| **S3** | The email voice profile of each member, learned from the mail that the member sent | See §8.1 | A tenant-coverage test, an RLS test against a real database (R8), and a test that no raw email body reaches the table | 🔴 OWNER-GATE: the consent wording |
| **S4** | A tracked score: the eval on a schedule, a model grader for the rules that the checker cannot see, and the score over time | A run records the score of each arm by date, and a drop below the last score fails the run | The S4 runner test, and the grader's own scripted cases | AGENT-SAFE to build. To run it on a schedule against the Router spends credits, so that is OWNER-GATE |

### 8.1 S3 — the email voice profile

- **Opt-in per member.** The default is off. Nothing reads sent mail until the
  member turns it on.
- **Tenant-scoped.** One new table, keyed by organization and member, under
  FORCE ROW LEVEL SECURITY, through the generated RLS migration (R5).
- **Derived features only.** It stores the greeting, the sign-off, the typical
  length, the formality and the recurring phrases. It never stores a copy of
  an email, and no raw body enters the table.
- **The member sees and deletes it.** One settings pane shows each feature,
  and one action deletes the profile.
- **How it reaches a draft.** The email overlay gains a short "your voice"
  line from the profile, after the core. The core always comes first.
- **Owner decision owed.** The wording of the consent that the member reads
  before the profile reads the mail that the member sent.

## 9. Verification commands

```bash
uv run pytest tests/unit/test_agent_voice.py tests/unit/test_agent_voice_eval.py -q
uv run pytest tests/unit/test_chat_upload_every_agent.py tests/unit/test_floor_opt_out.py tests/unit/test_generated_addendum.py tests/unit/test_skill_index.py tests/unit/test_tool_schema_diet.py -q
uv run python -m evals.agent_voice.run --scripted
# Manual, with a model:
LITELLM_BASE_URL=http://127.0.0.1:8080 uv run python -m evals.agent_voice.run --live
```

## 10. What is left

- **The agents' own instructions still use em dashes.** The addendum and many
  `instructions.md` files hold them. A model copies the style of its prompt,
  so a later PR can rewrite those files to the contract. That is prose work
  only, and it needs no new fence.
- **The checker has false positives.** `reflect` and `just` have plain uses.
  The checker is for evals, so a count is the signal, never one hit.
- **The Skills catalog does not count the voice.** It measures the addendum,
  and the voice is not in the addendum. §6.4 records the cost instead.
