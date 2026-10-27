# OA-5: the email tool pack against delegation, measured (2026-10-25)

**Bottom line.** On 18 email turns, run 3 times on each shape, the tool
pack was as accurate as delegation and about twice as fast. It also made
half the model calls and cost half as much. Each shape passed 51 of 54
turns. The median turn took 22.6 s with delegation and 10.0 s with the
pack.

**Fix round 1.** An independent check found two faults in this report. The
first edition said that only delegation stated a file name as content. In
fact each shape did it once. It also counted a failure that a limit of the
harness caused as a defect of delegation.

A fixed check then scored the stored answers again, with no new live run.
The pack's pass count fell from 52 to 51. Section 4 and section 5 give the
details.

This report is input for the owner's decision at OA-6. It does not make
that decision.

Spec: `project-docs/specs/one_assistant_context.md`, §4 and §5, slice OA-5
(D116). Board: WS-67. Code: `evals/one_assistant_measure/`. Raw data: git history,
commit `63550a052` (#1348).

## 1. The two shapes

| Shape | What the one assistant does with email |
|---|---|
| Delegation (today) | The orchestrator gives each email task to the email assistant. That agent runs as a sub-agent with its own prompt and tools, and it answers in text. |
| Tool pack (target, §3) | The orchestrator holds the email assistant's own tools and a short rules block. No email agent runs. |

The tool pack in this measure is an eval fixture (`pack.py`). It changes
the agents that the real factory built. No production code changed. The
fixture removes the `email_assistant` tool, and it adds 58 email tools. It
replaces 8 email bullets of the persona with a rules block of about 2,200
characters. The email assistant's own `instructions.md` has about 28,000.

## 2. Method

**The harness.** Both arms run through the one harness of the routing eval,
`evals/routing/harness.py` (H-412). The real orchestrator run, the real
email factory, the real delegation and the real cards ran. The email
gateway was the only fake. It held 13 synthetic mails and the mailboxes of
the five-turn email test.

**The turns.** The set has 13 chats and 18 member turns:

- the five turns of `tests/unit/test_one_assistant_email.py`
- the Navy-order shape twice: a first ask, and a replay that starts from
  the wrong guess "CB6 and CB7" and the member's correction
- eight more email asks, on one mail: read it, find it, use its reference
  file, and read its scanned file
- and on many mails: summarise the unread mail, say what needs a reply,
  count the invoices, and summarise a thread
- a reply after a correction in an earlier turn, and a reply with the
  member's exact text

**The checks.** Each turn has machine checks: 31 named checks of 19 kinds. Examples are
the right tool, the facts of the answer, the reply card, the member's
correction in the reply, and "no file name as content". The pack arm has
one more check on each turn: it handed no task to an email agent.

**The live run.** Each chat ran on each arm 3 times through the production
Router, through an SSH tunnel, on 2026-10-25. The orchestrator asked
`tier-balanced`. A delegated email assistant ran on `tier-powerful`, its
own default. The Router served `deepseek-v4-pro` on both tiers. The arms
took turns, so a time of day did not favour one arm.

**The numbers.** A tap on each agent's model client recorded each request:
its time, its tokens and its usage. The harness timed each turn to its
first answer text and to its end. A streamed call gives the vendor's own
cost (`usage.cost`). A buffered call gives no cost, so a card prices it.
The card came from four probe calls, and it reproduces each one within 1
percent.

**The settings.** `EMAIL_HELD_CARD_SEND` was on, as in production. Tier
routing, native sessions and narrowing were off, which is their default.
The eval did not read the production flags. The browser history of the chat
went in `messages`, as the web chat sends it.

## 3. The numbers

| Measure | Delegation | Tool pack |
|---|---|---|
| Turns that pass every check | 51 of 54 (94%) | 51 of 54 (94%) |
| Checks that pass | 153 of 156 | 204 of 207 |
| Turns that ended in an error | 0 | 0 |
| First text, median | 21.6 s | 9.1 s |
| First text, mean | 25.1 s | 10.2 s |
| First text, 90th percentile | 39.0 s | 19.3 s |
| Turn total, median | 22.6 s | 10.0 s |
| Turn total, mean | 26.0 s | 11.3 s |
| Turn total, 90th percentile | 39.9 s | 20.2 s |
| Time in model calls, mean | 25.6 s | 10.9 s |
| Time outside the model, mean | 0.40 s | 0.37 s |
| Model calls per turn | 5.37 | 2.85 |
| Of them, the orchestrator | 1.91 | 2.85 |
| Of them, the email assistant | 3.24 | 0 |
| Input tokens per turn | 156,013 | 117,791 |
| Of them, from the cache | 152,649 | 116,907 |
| Output tokens per turn | 2,186 | 808 |
| Cost per turn, measured | USD 0.0099 | USD 0.0048 |
| Cost per turn, with no cache (estimate) | USD 0.107 | USD 0.079 |

The pass counts are after the re-score of fix round 1. That re-score
changed one result: the pack's `scanned_file`, repeat 2, now fails. Before
it, the pack passed 52 of 54 turns and 205 of 207 checks.

The other 0.22 calls per turn of delegation came from two turns:
`five_turns` turn 2, repeats 1 and 3. In each, the email assistant called
the CRM assistant, and in repeat 3 the Projects assistant too. That makes
12 calls. Section 4 gives these turns.

**The size of each call.**

| Agent, arm | Calls | Tools offered | Input tokens per call | Output tokens per call | Time per call |
|---|---|---|---|---|---|
| Orchestrator, delegation | 103 | 53 | 20,490 | 394 | 4.7 s |
| Email assistant, delegation | 175 | 66 to 69 | 34,712 | 430 | 4.9 s |
| CRM and Projects assistants, delegation | 12 | 38 and 123 | 13,282 to 33,325 | 152 to 201 | 3.2 s |
| Orchestrator, tool pack | 154 | 110 | 41,303 | 283 | 3.8 s |

**Each chat.** The tool pack was faster and cheaper in each of the 13 chats.

| Chat | Delegation: time, cost, calls | Tool pack: time, cost, calls | Turns passed (delegation, pack) |
|---|---|---|---|
| `five_turns` | 27.6 s, USD 0.0123, 5.2 | 11.8 s, USD 0.0046, 2.4 | 14/15, 15/15 |
| `navy_fresh` | 20.3 s, USD 0.0075, 5.7 | 8.9 s, USD 0.0041, 3.0 | 3/3, 3/3 |
| `navy_replay` | 26.5 s, USD 0.0087, 5.0 | 15.4 s, USD 0.0060, 3.0 | 3/3, 3/3 |
| `read_invoice` | 20.2 s, USD 0.0075, 5.7 | 9.5 s, USD 0.0046, 3.3 | 3/3, 3/3 |
| `find_priya` | 16.0 s, USD 0.0064, 5.3 | 6.9 s, USD 0.0038, 3.0 | 3/3, 3/3 |
| `summarise_unread` | 31.5 s, USD 0.0117, 5.0 | 8.6 s, USD 0.0035, 2.0 | 3/3, 3/3 |
| `needs_reply` | 19.9 s, USD 0.0078, 5.0 | 5.9 s, USD 0.0028, 2.0 | 3/3, 3/3 |
| `count_invoices` | 32.2 s, USD 0.0116, 5.3 | 13.6 s, USD 0.0051, 2.3 | 3/3, 3/3 |
| `thread_summary` | 24.5 s, USD 0.0092, 5.7 | 11.6 s, USD 0.0060, 4.0 | 3/3, 3/3 |
| `reply_correction` | 34.9 s, USD 0.0108, 5.3 | 18.6 s, USD 0.0067, 3.3 | 5/6, 4/6 |
| `reference_file` | 18.7 s, USD 0.0071, 5.3 | 7.2 s, USD 0.0037, 3.0 | 3/3, 3/3 |
| `scanned_file` | 23.5 s, USD 0.0086, 6.0 | 10.9 s, USD 0.0054, 4.0 | 2/3, 2/3 |
| `exact_reply` | 27.2 s, USD 0.0093, 6.0 | 8.6 s, USD 0.0041, 3.0 | 3/3, 3/3 |

Each time and cost is the mean of one turn. The time of a turn is almost
all model time, so the gap comes from the count of calls. A delegated turn
makes about two orchestrator calls and three or more email calls, one after
the other. The member sees no answer text until the sub-run ends and the
orchestrator writes its answer.

**The scripted run.** Every plan of the 18 turns passes every check on both
arms (36 turns, no model, no network). This is the "email trajectory
suite" of §5. The unit test `tests/unit/test_one_assistant_measure.py`
runs it, and it passes 39 of 39.

The scripted run plays a known-good plan, so it passes 100 percent on each
arm by construction. It proves that each path works end to end. It does not
show which shape is more accurate. Only the live run shows that.

## 4. The failures

**The Navy shape passed on both arms, 6 of 6 each.** No answer named CB6 or
CB7 as a part, and each answer gave P-12 and P-31 from the body. This is
the live half of OA-1 and OA-2. In the delegation arm, no task held "CB6",
3 of 3 (OA-1). The replay answers, repeat 1:

- Delegation: the answer gave "P-12 hydraulic seal kit, quantity 40" and
  "P-31 bearing housing, quantity 12". It ended: "The attachments are
  sample drawings for reference only."
- Tool pack: "The order asks for two parts, both named in the body of the
  email (...) The two attached PDFs (CB6_sample, CB7_sample) are sample
  drawings, for reference only, so they are not the parts ordered."

A person read each of the 6 live sentences that name CB6 or CB7. Each one
calls the files samples or references, and none names them as parts. So 6
of 6 holds by hand too. The first check let any "not" in a sentence clear
the name. Fix round 1 counts a "not" only when it denies the name. The
re-score did not change a Navy result.

**A draft that the member did not ask for. Both arms: delegation 1 of 3,
the pack 2 of 3.** In `reply_correction` turn 1, the member only corrects
a date: "That date is wrong: they arrive on 14 November." The assistant
then made a reply card and saved a draft. The card asked first, and the
eval's member approved each card.

The tool pack, repeat 2, said: "Draft is ready for Anil, correcting the
date to 14 November. It's saved in your Work Drafts, nothing sent yet."
Both shapes kept the corrected date in the reply of turn 2, 3 of 3 each.
So the member's correction came through. The failure is an act that the
member did not ask for, and both shapes made it.

**A file name stated as content. Both arms, 1 of 3 each.** In
`scanned_file` the file is a scan with no text. Only its name,
`Gearbox_GB-40_rev3.pdf`, gives the model and the revision. This is cause
C2 of the spec. In repeat 2, each shape stated the name as a fact:

- Delegation: "The drawing Ravi sent is a gearbox, model GB-40, revision 3."
- Tool pack: "It's a revision-3 drawing of a gearbox, model GB-40, and he
  wants your comments by Monday (tomorrow)."

The first edition of this report said that the pack made this mistake 0
times. That was false. The first check found no content verb in either
sentence, so it missed both claims. It failed the delegation answer on a
harmless pointer instead. That sentence only told the member to open the
file to see the drawing.

Fix round 1 fixed the check in both directions. It takes the file name and
an indirect question ("what the drawing shows") out of each sentence first.
It then fails a fact that only the name gives, with or without a verb. In
delegation, repeat 3, the answer also gives the model and the revision. But
it says: "The subject and filename tell us it's a gearbox drawing". The
check counts that as a guess said as one, and so did the person who read
it.

**A placeholder in a draft. Delegation, 1 of 3, and the harness caused
it.**

In `five_turns` turn 2 of repeats 1 and 3, the email assistant called the
CRM and the Projects assistants. It wanted the figure of the quote. Only
the email gateway has a fake. So the harness refused their 20 reads of the
gateway, 6 in repeat 1 and 14 in repeat 3 (`sent` in the results file).
These agents got errors where production gives them answers.

In repeat 3 the email assistant then wrote a draft that held "₹[amount]".
In turn 3 ("send it"), the orchestrator did not send: "The draft still
holds the placeholder, so I can't send it yet." That refusal was correct.
The placeholder follows from the refused reads, so this report does not
count it as a clean defect of delegation. With this turn set aside,
delegation passed 51 of 53 turns.

The two turns still show one cost of a chain of delegations. The hop from
the email assistant to two more agents took 12 more model calls, and turn
2 of repeat 3 took 106 seconds.

## 5. The limits of this measure

1. **The held-chat guard did not move.** The tool pack ran the email tools
   in the orchestrator's own run, which is never held. So a send used the
   plain card and not the held card. OA-7 must move the guard with the
   tools (§4). This measure does not include the cost of that guard.
2. **Only email turns.** The pack arm offered 110 tools on each call. Each
   call held about 41,000 input tokens, even for a turn with no email. A real pack
   loads only for a turn that needs it (§3). This measure did not run a
   turn for another app with the pack loaded.
3. **The cache was warm.** About 98 percent of the input came from the
   cache, because the eval repeats the same prompts. With no cache, the
   pack still costs about 26 percent less. With a warm cache, it costs
   about 52 percent less.
4. **One model.** The Router served `deepseek-v4-pro` on both tiers. The
   pack ran the email work on the orchestrator's tier. If the two tiers
   point at different models later, the pack needs a tier rule.
5. **A fake gateway, and a fake reply writer.** The server's own reply
   writer did not run. When a reply card gets `instruction`, the fake
   writes a fixed text around the member's words. That text can never hold
   a placeholder. A draft that the model writes and passes in `draft`
   can hold one. This favours the arm that uses `instruction` more. The
   results file keeps no tool arguments, so this measure cannot count the
   mode of each arm. The real writer costs a model call in either mode.
6. **The timing lines.** The measure did not read `agent.run_timing`,
   because the gateway writes it and the harness has no gateway. The
   executor writes `executor.run_setup_ms`, and the harness log shows it
   (for example `setup_ms=31`). The measure did not read that line either.
   The harness timed each turn and each call instead. It did not read the
   Console's `usage_event`. The cost came from the vendor's figure on each
   stream, and from the card for each buffered call.
7. **Four checks read prose.** `no_name_claim`, `no_reference_subject`,
   `admits_unreadable` and `no_content_claim` are heuristics. Section 4
   quotes each answer that failed one of them. Fix round 1 fixed two of
   them, and a re-score of the stored answers changed one result.
8. **A small sample.** Three runs of 18 turns on each arm is small. The
   pass rates are equal. The gap in time and cost is not noise: it showed in
   each of the 13 chats.
9. **I replaced one run.** The first run of `reply_correction` found a
   fault in the fake. Its held card answered each send with the people of
   the five-turn chat. Commit `3bac947df` fixed the fake, and that chat ran
   again on both arms. The results file keeps the 4 old turns under
   `replaced`. Of them, only the delegation turn 1 failed (`no_write`), in
   the same way as section 4 says. The pack turn 1 passed.
10. **The tunnel dropped once.** The first sweep lost its tunnel, and one
    turn hung for 57 minutes. That sweep is not in the data. The runner now
    checks the Router before each chat, and it drops a chat that loses it.
11. **The harness refused the other agents' reads.** In two delegation
    turns, the harness refused 20 reads of the CRM and the Projects
    assistants (section 4). Their errors led to one failure. The pack arm
    called no other agent, so this did not touch it.
12. **The guard covers HTTP only.** The live guard refuses each httpx send,
    async and sync, that does not go to the Router. It does not cover a
    database, Redis or graph driver. The few reads of those went to the
    local development services on this machine. Mem0 and Graphiti were
    off, which is their default. The runner now pins them off too, because
    their calls to the Router would pass the tap and the card unseen. The
    Router address was the local tunnel, 127.0.0.1. The sync guard, the pin
    and the check of the Router address came in fix round 1, after the
    live run.
13. **Two commits ran the data.** Repeat 1, except `reply_correction`, ran
    on commit `382641bd6`. Repeats 2 and 3 and the rerun of
    `reply_correction` ran on `3bac947df`. The two commits differ only in
    the fake's saved drafts and the `replied` check. The results file now
    records the SHA of each chunk and of each row.
14. **The scripted suite passes by construction.** Section 3 says why. Its
    100 percent on both arms is no evidence of accuracy.

## 6. Spend

The whole OA-5 work spent USD 0.91 of the USD 5 cap. Of that, the 108 turns
of the data cost USD 0.79: delegation USD 0.53, and the tool pack USD 0.26.
The rest went to the probes of the rate card, a pilot, the first sweep that
lost its tunnel, and the replaced chat.

## 7. Input for the owner's decision (OA-6)

The owner decides at OA-6. These facts are the input. They are not the
decision.

- **For a yes.** On email conversation the tool pack is as accurate, and it
  is about 2.3 times faster to the first text. It makes half the model
  calls, and it costs half as much with a warm cache. It also removes the
  hop from the email assistant to other agents (section 4).
- **What the shape does not fix.** Each shape stated a file name as content
  once in 3 runs. The fix for that is OCR (OA-3) and the marks of OA-2,
  not the shape.
- **What a yes costs.** OA-7 must move the held-chat guard with the tools.
  It must load the pack per turn, or each other turn pays about 20,000 more
  input tokens. It needs a tier rule for email work.
- **What this measure does not show.** It did not test the routing of turns
  for other apps with 110 tools in hand. Measure that before OA-10 retires
  delegation.
- **Either way.** Both shapes wrote a draft that the member did not ask
  for, after a correction. A rule in the persona or the pack can fix that,
  and OA-4 (chat constraints) is the place for it.

The harness is ready for the chat-speed work of H-412, item 2 ("fewer model
rounds per email run"). Use it, and do not build a second one.
