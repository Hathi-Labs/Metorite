# How Metorite writes — the voice of every agent and every draft

**Status.** ACTIVE. Owner directive, 2026-10-10. Board row **WS-52**.
Verified against code on 2026-10-10.

S1 is built on branch `agent-writing-voice` (PR #828). The chief-of-staff
role of the orchestrator (§11) is built on branch
`orchestrator-chief-of-staff`, 2026-10-13. S2 is built on branch
`agent-voice-drafting`, which starts from the S1 branch (§8.2). S3 and S4 are
spec only.

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

- One contract of twelve rules, and one overlay for each of six surfaces.
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
10. Stay inside the records. Never invent a name, number, date or quote.
11. Untangle stacked nouns: "the review of how we approve vendor payments", not "the vendor payment approval process review".
12. Match the language of the person who will read it. These rules cover your own words, never quoted text, code or data.
```

### 3.1 The overlays

A drafting call adds the core and one overlay. An agent reads all six,
because an agent writes on each surface. A sub-agent reads the core
alone, because it answers its parent agent and not a member.

```text
Chat: name the source (task #141, the 3 Oct email from Priya). Use formatting, or a list, only where the items stand apart and it helps scanning.
Title: up to 8 words unless the prompt sets a limit, sentence case, no final full stop, no "X: Y" subtitle.
Description (task, project, card body): what, why, and when it counts as done, in 1 to 3 short sentences.
Email: the member's own voice. The member's own instructions, writing style and voice profile come first. Where they differ from these rules, follow the member. Write in the recipient's language. One clear ask when possible. No "I hope this finds you well".
Message (WhatsApp or chat to a contact): short and conversational, with no greeting or sign-off unless the member uses them. The member's own instructions and style come first. Write in the recipient's language.
Summary (digests, meeting notes, reports): decisions and facts first, then open items. No adjectives that pass judgment.
```

The owner may edit the wording. Edit `voice.py` and §3 in the same PR, and the
fence keeps them equal. `test_agent_voice.py` checks both blocks byte for
byte.

## 4. Three changes from the owner's list

The owner's list was the input. Three rules changed on purpose.

1. **Hedging became "state real uncertainty once".** The list banned hedges.
   An honest statement of uncertainty is a safety property. A member who acts
   on a guess that read as a fact can pay money or lose a customer. So rule 8
   keeps one plain statement, where it applies, and bans every other hedge.
2. **The blanket ban on short side-by-side clauses went.** Short clauses side
   by side are part of how a colleague talks. A ban on them makes the text
   stiff. Rule 3 asks for varied shape instead.
3. **"Never invent a fact" is new.** Rule 10 bans an invented name, number,
   date or quote. A fluent voice that invents facts is worse than a stiff
   one.

### 4.1 Changes from review round 1 (2026-10-10)

Four changes protect text that goes to a person outside the org.

1. **"Name the source" moved from rule 10 to the chat overlay.** In an email
   to a customer, "task #141" leaks an internal record id.
2. **Rule 12 follows the reader's language.** The email drafter writes in
   the contact's language. "Match the member's language" fought that rule.
   The email and message overlays say that the recipient's language wins.
3. **Outbound text defers to the member.** The email drafter already ranks
   the member's instructions, writing style and voice profile. The email
   and message overlays say that those come first. The house voice is the
   floor, never an override.
4. **New overlay `message`.** A WhatsApp reply is not an email. It is short,
   with no greeting or sign-off unless the member uses them.

Two changes cut cost. The chat overlay lost "the answer in the first
sentence", because rule 1 says it. The list clause of rule 12 joined the
chat overlay's formatting line. The title overlay now yields to a limit
that the prompt sets.

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
| `OVERLAYS` | One rule set for each surface: `chat`, `title`, `description`, `email`, `message`, `summary` |
| `voice_prompt(surface)` | The heading, the core and one overlay. `AGENT`, the default, gives every overlay. An unknown surface raises `KeyError` |
| `core_prompt()` | The heading and the core, with no overlay. A sub-agent reads it |
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
injection on one agent adds nothing. A sub-agent (`is_sub_agent=True`) gets
`core_prompt()`, with no overlay.

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

Measured on 2026-10-10, after review round 1.

| Text | Run-context tokenizer (chars/4) | tiktoken `o200k_base` |
|---|---|---|
| `CORE` | 327 | 303 |
| The sub-agent block (`core_prompt()`) | 333 | 310 |
| One surface, from `voice_prompt("description")` to `voice_prompt("email")` | 359 to 397 | 339 to 365 |
| The agent block (`voice_prompt(AGENT)`) | 577 | 538 |

Each agent pays the agent block once in its prefix. A Projects turn makes
about 5.7 requests, and the cache covers each one after the first.
`projects_ai_chat.md` §25 records the rest of the prefix. The agent block
grew by 99 tokens in review round 1, because the email and message
overlays now defer to the member. A delegated call pays only the core.

The ratchet in `test_agent_voice.py` holds the core at 340, the sub-agent block
at 345 and the agent block at 600. To grow one, edit that test on purpose.

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

`evals/agent_voice/` holds thirteen fixed cases over the six surfaces
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
| **S3** | Extend the email voice profile that exists (`email_voice_profiles`): consent before the first build, and derived features only | See §8.1 | A test that no sample body reaches `traits` or `style_guide`, and a test that a build with no consent starts no job | 🔴 OWNER-GATE: the consent wording, and whether WhatsApp reads the profile |
| **S4** | A tracked score: the eval on a schedule, a model grader for the rules that the checker cannot see, and the score over time | A run records the score of each arm by date, and a drop below the last score fails the run | The S4 runner test, and the grader's own scripted cases | AGENT-SAFE to build. To run it on a schedule against the Router spends credits, so that is OWNER-GATE |

### 8.1 S3 — extend the email voice profile that exists

**S3 adds no table.** Review round 1 (2026-10-10) found that the voice profile
is already built. S3 extends it and builds nothing parallel to it.

**What exists today** (`gateway/routes/email/automation/voice_profile.py`,
migration 94):

| Part | As built |
|---|---|
| Store | `email_voice_profiles`, one row for each mail account (`account_id` is the primary key), under FORCE ROW LEVEL SECURITY. It holds `traits` (JSONB: tone, formality, typical length, greetings, sign-offs, common phrases, dos and don'ts), a narrative `style_guide`, the source folders and the date range |
| Build | `POST /email/voice-profile/build` starts a job when the member clicks. It reads at most 150 sent or drafted mails in the range, strips the quoted chains and keeps no sample |
| Use | `voice_profile_block` renders a `<voice_profile>` block. `_load_assistant_about` puts it between `<writing_style>` (which outranks it) and `<learned_writing_style>` |
| View and delete | `GET /email/voice-profile` shows it, `PUT` turns it on or off, and `DELETE` removes it with its unapproved knowledge suggestions |

**Keying.** The key stays the mail account. An account has one owner
(`email_accounts.user_id`), and `_assert_account_owner` checks it on each
route. So a profile is per member and per mailbox. A member with two
mailboxes can write two ways, and S3 keeps that. S3 adds no organization key,
because the RLS policy already binds the organization.

**What S3 changes.**

1. **The house voice defers to the profile.** S2 review round 1 did this: the
   email and message overlays say that the member's voice profile comes
   first. `test_drafting_voice.py` checks it on the drafter.
2. **Consent before the first build.** Today the build dialog starts a job
   with no consent text. S3 adds one notice that says what the job reads, what
   it keeps and how to delete it. The member must accept it once for each
   mailbox before the first build.
3. **Derived features only, by test.** `common_phrases`, `greetings` and
   `signoffs` can quote a mail. S3 caps each item at a short length and adds a
   test that no sample body reaches `traits` or `style_guide`.
4. **WhatsApp reads it, or not.** The WhatsApp drafter does not read the
   profile. Whether a mailbox profile may shape a WhatsApp reply is the
   second owner question.

**Owner decisions owed.** The wording of the consent notice, and whether the
WhatsApp drafter may read the email profile.

### 8.2 S2 as built — the direct drafting calls

**Status.** Built on 2026-10-10, on branch `agent-voice-drafting`. That
branch starts from the S1 branch.

**How a site gets the voice.** Each member-facing site appends
`voice_prompt(<surface>)` to its own system string, after its rules and its
DATA fence. It adds no second system message, so a test that reads
`messages[0]` or `messages[1]` still reads the same message.

A site whose output is JSON passes `json=True`, which adds `JSON_NOTE`. It says that the
rules bind only prose that a member reads. They never bind keys, enum
values, ids, patterns, names, addresses or quoted source text, so a rule's
`subject_pattern` keeps its exact value. A site that writes two kinds of
text names both surfaces, for example `voice_prompt("title", "description")`.

**A limit in the site's prompt wins.** The title overlay says "up to 8
words unless the prompt sets a limit". So the capture prompt keeps its
15 words, and the meeting title keeps its 12.

**A WhatsApp message takes the `message` overlay.** That covers the reply
and the nudge. Review round 1 added it, because the WhatsApp prompt asks
for a chat register and the email overlay asked for an email.

**Outbound text puts the member first.** The email and message overlays
say that the member's own instructions, writing style and voice profile
come first. In the email drafter the voice is the last block of the system
message, after the drafter's own ranking of those blocks. The member's
blocks arrive in the user message after it.

The sites that take the voice (26 functions). The sweep finds 66 functions that call a model, and the other 40 are machine sites:

| Area | Function | Surfaces |
|---|---|---|
| Email | `drafting._llm_draft_reply`, `drafting._llm_compose_assist` | email |
| Email | `rules._llm_generate_rules` | title |
| Email | `digest._digest_brief`, through the constant `_BRIEF_SYSTEM` | summary |
| Notes | `summaries._single_pass` and `_map_reduce`, through `templates.build_system_prompt` | summary, title |
| Notes | `share._draft`, `dispatch._draft_email` | email |
| Notes | `dispatch._dispatch_document` | title, summary |
| Notes | `qa.ask_meeting`, `copilot._craft` | chat |
| Notes | `copilot_agenda.draft_agenda` | chat, title |
| Tasks | `ai._llm_propose`, `capture_email._llm_capture`, `capture_email._llm_detect_commitment`, `planning._llm_plan` | title, description |
| Tasks | `ai._llm_suggest_title` | title |
| Tasks | `calendar._llm_rank_day`, `resume_parse.llm_extract_profile` | summary |
| WhatsApp | `drafting.draft_reply`, `commitments.draft_nudge`, through their message builders | message |
| WhatsApp | `groups.summarize_group`, through its builder | summary |
| Workflows | `copilot._call_copilot`, through `workflow_copilot` | chat |
| Orchestrator | `executor._llm_recovery`, `agents/pull_agent.answer`, `agents/sales_pull_agent.answer` | chat |

The two pull agents have no production caller today. They take the voice, so
the day one is called, it already writes in the house voice.

**The sites that take no voice, on purpose.** `MACHINE_SITES` in the fence
lists each one with its reason. There are four kinds:

1. **No member prose.** Transport wrappers, embeddings, classifiers, routing
   JSON, extraction, a health ping and an entity tie-break.
2. **The member's own voice is the point.** These are the voice profile
   (observe, synthesize, sample) and the writing-style guides. The template
   fill and the split of a mind-dump into captures are here too. The house voice must not bias
   what these learn, and it must not rewrite the member's words.
3. **Someone else owns the prompt.** A Custom App's `ai_complete`.
4. **The output is code or configuration.** The workflow module generator,
   and the API discovery for an admin.

Three email classifiers show a short reason in the UI (`engine` twice and
`senders`). They stay machine sites, because the reason is a label and they
run only when decide is off.

**The fence.** `test_drafting_voice.py` walks the AST of every production
module under `apps/` and `packages/`. It finds each function that calls a
model entry point. The billed Router entry, `completion_on_router`, is one
of them. Each such function must be in `VOICE_SITES` or in `MACHINE_SITES`.

For a voice site, the function (or the builder or the
constant that it names) must call `voice_prompt` with exactly its surfaces.
A machine site must not call it. An entry whose function no longer calls a
model fails, so the lists cannot go stale.

**Advisory.** The sweep cannot see three things. One is a one-shot MAF
`Agent` that code builds (`acb_skills.system_one`). One is a vendor SDK with
its own client (Graphiti, Mem0). One is an entry point with a new name.

**The cost.** One site adds the core and one or two overlays: 355 to 400
tokens in `o200k_base`, and about 15 more with `json=True`. A machine site
adds nothing.

## 9. Verification commands

```bash
uv run pytest tests/unit/test_agent_voice.py tests/unit/test_agent_voice_eval.py tests/unit/test_drafting_voice.py -q
uv run pytest tests/unit/test_orchestrator_voice.py tests/unit/test_orchestrator_cos_eval.py tests/unit/test_orchestrator_one_source.py tests/unit/test_run_org_name_r8.py -q
uv run pytest tests/unit/test_chat_upload_every_agent.py tests/unit/test_floor_opt_out.py tests/unit/test_generated_addendum.py tests/unit/test_skill_index.py tests/unit/test_tool_schema_diet.py -q
uv run python -m evals.agent_voice.run --scripted
uv run python -m evals.orchestrator_cos.run --scripted
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

## 11. The chief of staff: the orchestrator's role

**Status.** Built on 2026-10-13. Owner directive of the same day. The words
are the owner's, so they sit in a code block.

```text
"The orchestrator's messages are overly verbose, overwhelming decision-makers.
The agent should function as a chief of staff, distilling key information and
sharing useful insights rather than dumping raw data. It must proactively
identify important details and present them to support decision-making."
```

### 11.1 The contract

`acb_llm.voice.ROLES["chief_of_staff"]` holds the text of record. The block
below must match it byte for byte. `tests/unit/test_orchestrator_voice.py`
fails when the two differ.

```text
As chief of staff (the member decides, you prepare the decision):
- Open with the bottom line: the answer, the status, or the decision the member must take. Do not restate the question.
- Then give the 2 to 5 points that matter, the most important first. Leave out what does not change what the member knows or does next.
- Never paste the output of a read or another agent's reply. Say what it means. The detail stays in its step, in a link, or in one folded card. The one exception is text the member asked for (a draft, a message, a document): give it whole.
- Raise what the member did not ask about only when it is real and close: a deadline, a risk, a conflict, a number out of line, or a thing that waits on the member. One line each. Raise nothing to fill space.
- For a decision, give each option, your recommendation and its trade-off, one line each. Then say what happens next and who owns it.
- Fit the length to the question. A fact gets a sentence. A routine reply stays under about 120 words of text. When the member asks for the detail or the data, give all of it.
```

### 11.2 Where each rule lives, and why

| Rule | Where | Why there |
|---|---|---|
| Give the answer first, with no preamble | `CORE` rule 1 | It suits every agent, and the core already says it |
| The role (§11.1) | `ROLES["chief_of_staff"]`. `AGENT_ROLES` gives it to `orchestrator` only | The orchestrator answers for the whole company. A specialist must keep its detail: an email draft, a coding report, a Projects plan |
| Merge the replies of the specialists, and never relay one whole. The role also says it: a draft that the member asked for is the one exception to "never paste" | The orchestrator's own instructions, `_PULL_INSTRUCTIONS` in `orchestrator/agents.py` | It names the specialist tools, and the voice names no tool |
| Keep a deliverable whole (an email, a message, a document) | The same instructions | A draft is the answer, so a summary of it loses the work |

**A role is not a surface.** A surface says what kind of text a call writes.
A role says what one agent is for. So `voice_prompt(AGENT, role=...)` adds the
role after the overlays, and a role on a drafting surface raises
`ValueError`. A sub-agent never reads a role, because it answers its parent.

**The core did not change.** Every other agent reads the same block as
before. `test_orchestrator_voice.py` builds the seven other in-tree agents. It
checks that each one holds `voice_prompt(AGENT)` and no role.

**The cap.** A routine reply holds about 120 words of prose. Two sources give
that number. The executive style of the Agent SDK cookbook allows 30 seconds
of reading, which is about 120 words at 240 words a minute. The c-level
chief-of-staff skill allows 5 bullets in a section, at most. The live eval
(§11.5) is the check, and a later PR can move the number on its evidence.

**The cost.** The role is 278 tokens in the run-context tokenizer (256 in
`o200k_base`). The orchestrator's voice block grows from 577 to 847. The
orchestrator pays it once in its prefix, and the cache covers it after the
first request. No other agent pays it.

### 11.3 The references (owner, 2026-10-13)

| Reference | What we took | State |
|---|---|---|
| alirezarezvani `claude-skills`, chief-of-staff `SKILL.md` | Bottom line first, with no narration of the process. At most 5 bullets in a section, and the rest goes to a reference. Name a conflict between advisors, and do not smooth it. Each action has an owner and a date | Loaded |
| skillsdirectory, amey-thakur `agent-chief-of-staff` | Protect the attention of the human: only what needs a human reaches them, ranked, with a recommendation. Find each stall, and name its blocker. Each assignment says what, who and by when | Loaded |
| Medium, "I turned Claude Code into my chief of staff" | Nothing | HTTP 403, not read |
| Claude cookbook, the chief-of-staff agent | Delegate to a specialist, then make its result into a recommendation. The executive output style: 30 seconds to read, 3 to 5 bullets, numbers before narrative, decisions before background | Loaded, with its `executive.md` output style |

**One change from the references.** The c-level skill gives the options with
no recommendation. The owner asked for a recommendation, and the second
reference attaches one. So the role asks for the options, a recommendation
and the trade-off.

### 11.4 The delegation result

`call_agent` returns the final text of the sub-agent, and never its whole
transcript. `call_agents_parallel` joins the replies as `[agent-name]` blocks.
`_run_sub_agent_streaming` cuts each reply at 8000 characters
(`SUB_AGENT_MAX_RESULT_CHARS`).

That shape makes it easy for a model to paste the blocks one after another.
Before 2026-10-13, the instructions of the orchestrator also said "relay its
full response". The fix is in the instructions only, for two reasons. A
framing line in the tool result shows in the step trail of the member. A new
tool docstring changes the schema of each agent that holds the tool, and the
WS-23 ratchet of `test_tool_schema_diet.py` with it. The output of each other
agent stays the same.

**`apps/agents/agent-orchestrator/instructions.md` is gone (2026-10-14).**
The chat prompt never held it. §11.7 records where each of its rules went.

### 11.5 The eval

`evals/orchestrator_cos/` holds twelve cases, and §11.7 adds three more:

- A status across apps, and what needs my attention.
- Two decisions.
- A long relay, and a relay where two agents disagree.
- Two facts, and a quiet account.
- An explicit request for all the detail, and "tell me more" after a short
  answer.
- A draft that must stay whole.

Each reply gets eight deterministic checks: `bluf`, `length`, `no_dump`,
`no_relay`, `recommendation`, `mentions`, `keeps_deliverable` and `voice`.
The `voice` check is `voice_lint` with no findings. `--judge-model` adds a
model rubric of five scores from 1 to 5. The `README.md` of that folder
gives each check.

- `--scripted` runs in the unit job, through
  `tests/unit/test_orchestrator_cos_eval.py`. Each good answer passes, and
  each bad answer fails exactly the checks that it names.
- `--live --arm before --prompt-file <old prompt>` and `--live --arm after`
  write both arms to `results.json`. `--report` prints the table.

**The live baseline is not recorded yet.** The build machine has no Router.
The Router of `app.metorite.com` needs its key on the build machine, and a
permission check refused that copy on 2026-10-13. The owner runs the commands
in the `README.md` of the eval, and commits `results.json`.

### 11.6 Fences

| Fence | What fails it |
|---|---|
| `tests/unit/test_orchestrator_voice.py` | The orchestrator loses the role, or reads it twice. Another agent or a sub-agent gets it. A rule marker goes. The role passes 300 tokens, or the block of the orchestrator passes 900. "relay its full response" comes back. The spec copy drifts |
| `tests/unit/test_orchestrator_cos_eval.py` | A check stops firing. The case set loses a kind. The cap moves from 120 |
| `tests/unit/test_agent_voice.py` | The same as before, and still green: the core, the agent block, and one copy of the voice in each agent |

### 11.7 One source, and no tenant name (owner, 2026-10-14)

**Status.** Built on 2026-10-14 on branch `orchestrator-instructions-merge`.
The owner decided: merge the useful rules of the unread file into the live
prompt, then delete the file. A tenant name in a shared prompt is an M1
defect, so the same change removes it.

**The rules of `apps/agents/agent-orchestrator/instructions.md`, one by one.**

| Rule | Verdict | Where it went, and why |
|---|---|---|
| Rules 1 to 8, the tool sections, the link rule | Already live | `_PULL_INSTRUCTIONS` held them word for word |
| 9. Never name a tool to the member | Kept | `_PULL_INSTRUCTIONS` rule 9. The CRM, email, WhatsApp and Projects agents each hold it in their own text, so it is a rule of each agent, not of `CORE` |
| 10. No «» around a name | Kept | `_PULL_INSTRUCTIONS` rule 10. It is not universal: the Projects agent writes the marks on purpose, and the chat draws them as pills. The WhatsApp bot runs the orchestrator, and WhatsApp shows the marks raw |
| 11. A Markdown list, never "•" | Kept | `_PULL_INSTRUCTIONS` rule 7, in place of "Be concise", which the role says |
| Placement: a read shows under its step | Dropped | `PLACEMENT_RULE` says it, and the role says "Say what it means" |
| Placement: give a list once | Dropped | The one-card limit, the cap of the role and its "one folded card" cover it |
| Placement: one card at most, after the text | Dropped | `PLACEMENT_RULE` |
| Placement: fewer than six items stay text | Dropped | `PLACEMENT_RULE` |
| Placement: a question is not the answer card | Dropped | `PLACEMENT_RULE` |
| Placement: one decision at a time | Kept | `_PULL_INSTRUCTIONS` rule 11. No other text says it. `PLACEMENT_RULE` stays the same, so no other agent changes |

**The file is gone.** `mutation.py` read it as the purpose context of the
self-repair sandbox. An agent with no `instructions.md` now gives the module
docstring of its `agents.py` (`_agents_py_purpose`). The function parses the
file and never imports it.

**The tenant name.** The prompt said "for Fracktal Works" to every tenant.
Now it says "for this organisation". The per-turn context names the run's
organisation: `executor._run_org_display_name` reads
`organization.display_name` for the org of `_current_run_org`. That org
comes from the run boundary, never from the payload (R5).

`_context_preamble_parts` adds the line "Organisation: <name>.", and both
paths of a run read that one builder. The line sits after the cache break,
so the static prefix of each agent stays byte-stable. A run with no org, or
an org with no name, gets no line. A cache keeps each name for 10 minutes,
so a turn pays at most one read.

The name is a member's words, and nothing checks it at signup. So
`_clean_org_name` makes it one line of at most 120 characters. It folds
the text with NFKC, and it removes each control character, each format
character and the marks « and ». A name with a newline cannot forge a
context block, and a long name cannot cost its size on each turn.

The same sweep removed the name from three places. They are the app
builder's instructions, an example in the email agent's instructions, and
three examples in the docstrings of the memory tools. `github_org` still defaults to
`FracktalWorks` in the settings. That is the GitHub owner of the agent
repos, not prompt text, so it stays.

**Fences.**

| Fence | What fails it |
|---|---|
| `tests/unit/test_orchestrator_one_source.py` | The file comes back, or code reads it. A kept rule leaves the live prompt. The placement section comes back. "Fracktal" is in a built prompt or a tool text of any of the 8 agents. Two orgs do not get their own names. The name comes from the payload. A turn reads the database more than once. The sandbox loses the purpose context |
| `tests/unit/test_run_org_name_r8.py` | R8, on a promoted catalog as a role with NOBYPASSRLS: the read returns the wrong name, or a row policy hides it |
| `tests/unit/test_chat_placement_instructions.py` | The orchestrator moved to `DIRECTIVE_AGENTS`. It must read `PLACEMENT_RULE` once, and its own text must not repeat it |
| `evals/orchestrator_cos` C13 to C15 | Three scripted cases and three checks: `no_tool_name`, `no_guillemets` and `one_ask` |
