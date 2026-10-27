# The email measure: the real tool pack against delegation (WS-67, OA-5 and OA-7h)

This folder compares two shapes of the one assistant on the same email
chats. The owning spec is `project-docs/specs/one_assistant_context.md`,
§4 and §5, slices OA-5 (D116) and OA-7h. The report of the first live run is
`docs/one_assistant_measure_2026-10-25.md`.

**Status.** OA-5 was built on 2026-10-25. OA-7h phase 1 was built on
2026-10-27: the `pack` arm runs the real pack, and the scripted run passes on
both arms. The live run of OA-7h waits for OA-7f. The results of the live run
of 2026-10-25 are in git history only. Commit `63550a052` (#1348) added
`results_2026-10-25.json`, and the cleanup of 2026-10-27 deleted it.

## The two arms

| Arm | What the orchestrator holds |
|---|---|
| `delegation` | Today's code. The orchestrator gives each email task to the real email assistant, which runs as a sub-agent. |
| `pack` | The REAL `email` pack. `run.pack_flag()` sets `TOOL_PACKS=email` and `TOOL_PACKS_ORGS=<the eval org>` in the eval process, and the real run loads the pack. |

OA-5 faked the pack with `pack.install`. OA-7h removed that fixture. The
real code now does the work: `tool_packs.choose` (layers a and b),
`_apply_tool_packs`, `load_skill_tools`, the held answer and the persona
swap. `pack.py` keeps only the names that the other files share, and the
constant `REFUSED`, which `tests/unit/test_tool_packs_choice.py` pins.

Both arms run with the settings of production, set in the eval process
only (`run.PRODUCTION`): `EMAIL_HELD_CARD_SEND`, `AI_TIER_ROUTING=orchestrator`,
`AI_REASONING_OFF_AGENTS=orchestrator`, `MAF_NATIVE_SESSIONS=true` and
`NARROWING_AGENTS=email-assistant`. The runner never writes them on a box,
and never flips `TOOL_PACKS`, `TOOL_PACKS_ORGS` or `SPAWN_SUBAGENTS_ORGS`
anywhere.

The measure installs the in-memory mark store
(`tests/unit/_egress_mark_store.py`). A chat that read mail is then marked,
and the next turn holds the pack, as in production. Without the store, the
mark read fails closed and the held-card check proves nothing. The run stops
when the store does not install.

With the `email` pack alone, the `email-setup` pack is not loaded. A rule, a
setting, the knowledge and the writing style go to the email assistant by a
hand-off. This is legal on the `pack` arm, and it costs one delegated run.
The report counts these turns as the setup gap.

## What it reuses

The measure uses ONE harness (H-412): `evals/routing/harness.py`. The
harness drives the real orchestrator run, the real email factory, the real
delegation and the real cards. The email gateway is the only fake.
`mailbox.py` extends the `Gateway` fake of
`tests/unit/test_one_assistant_email.py` with 13 synthetic mails.

The harness gives the measure three things:

- `model_calls`: each model request, with its time, its tokens and its cost.
- `after_build`: a hook that changes the built agents.
- A live mode with real specialists. Each host send that is not the Router
  fails.

## The scenarios

`scenarios.py` holds 13 chats and 18 member turns. All the data is
synthetic.

| Scenario | What it asks |
|---|---|
| `five_turns` | The five turns of `test_one_assistant_email.py`. Turn 4 names the rule, so that a live model knows what to make. |
| `navy_fresh`, `navy_replay` | The shape of the Navy-order chat (§1 of the spec). The order body names the parts, and two scanned files have names that look like parts. The replay starts from a wrong guess and the member's correction. |
| `read_invoice`, `find_priya` | Find and read one email. |
| `summarise_unread`, `needs_reply`, `count_invoices` | Questions over many emails. |
| `thread_summary` | Read a thread. |
| `reply_correction` | The member corrects a date in turn 1, and asks for the reply in turn 2. |
| `reference_file` | A readable file that is only a reference. |
| `scanned_file` | A scanned file: the name is all there is. |
| `exact_reply` | A reply with the member's exact text. |

Each turn has machine checks (`checks.py`) and a known-good plan. Four
checks read prose, and they are heuristics: `no_name_claim`,
`no_reference_subject`, `admits_unreadable` and `no_content_claim`.

Four checks apply to every turn, with no entry in the scenario:

| Check | Passes when |
|---|---|
| `no_email_delegation` | Pack arm only. No hand-off of mail work (read, search, reply, send) and no refusal of the pack. A hand-off for a rule, a setting, the knowledge or the writing style passes. |
| `held_card` | From turn 2, each send goes through `/email/held-card/act`. A plain send fails. Turn 1 and a turn with no send give None. |
| `one_card` | The turn shows at most one card for each write. A send that shows the gate card and then the held card fails. |
| `ran` | The turn ended with no error. |

The report adds these rows: the cache share (cache-read tokens over input
tokens), the turns that hand off for setup work with their cost, the turns
that hand off mail work, the turn-kind request time with and without pack
items, and the turns where a pack loaded before the first model call. The
last two read the log events of the pack code, which `run.LogTap` keeps in
each row as `pack_events`.

## Run it with no model

```bash
uv run python -m evals.one_assistant_measure.run --scripted
uv run python -m evals.one_assistant_measure.run --scripted --scenarios five_turns
```

Each plan plays on both arms, and every check must pass. A scripted run
proves the path and the checks. It does not prove the model's choice. On the
`pack` arm, the plan loads the pack with `load_skill_tools` when the words
of the turn do not, and it hands a setup call to the email assistant.

## Run it live

A live run spends credits on the Router. Open the tunnel to the Console
first, and close it after the run. The tunnel maps local port 18090 to the
Router on port 8090 of the production box (`staging_and_daily_release.md`
holds the same mapping for staging). It needs the grant `deploy` in
`.claude/OWNER_GRANTS.md`. The runner refuses a Router that is not on this
machine.

```bash
ssh -N -L 127.0.0.1:18090:127.0.0.1:8090 metorite
```

`~/.metorite/secrets/eval.env` holds `LITELLM_BASE_URL=http://127.0.0.1:18090`
and the key. Close the tunnel with Ctrl+C in that shell.

1. Put `LITELLM_BASE_URL` and `LITELLM_API_KEY` in the environment or in
   `~/.metorite/secrets/eval.env`. The runner never prints the key.
2. Run the chats in chunks. Each chunk must end in less than 10 minutes:

   ```bash
   uv run python -m evals.one_assistant_measure.run --live \
     --scenarios five_turns --repeat 1 --first-repeat 1 \
     --spent-before 0 --budget-usd 5 --timeout 200 --out chunk_r1_a.json
   ```

3. Merge the chunk files, and print the tables:

   ```bash
   uv run python -m evals.one_assistant_measure.run --merge a.json,b.json \
     --out evals/one_assistant_measure/results_<date>.json
   ```

**The System-1 seam (eval only).** Production runs the turn-kind request, because
`routing_is_on()` is true there. The eval has no Console, so `system_one._ask`
would answer `router_off`. In a live run, `run._system_one_seam` makes
`routing_is_on` read True for `system_one._ask` only. Each other caller reads
the real answer. The System-1 client already reads the Router base and key
that `_use_router` sets, and it asks `tier-fast`. The seam sets no
`ROUTER_SERVING_ENABLED` and changes no product code. It bypasses Console
billing for that call, as the rest of the eval does.

The runner checks the Router port before each chat. When the Router does
not answer, the runner stops. When a chat loses the Router, the runner
drops that chat and stops. The Router address must be on this machine,
which is the tunnel, or the run does not start. The merge keeps the SHA of
each chunk, and of each row.

The live guard refuses each httpx send, async and sync, that does not go
to the Router. It does not cover a database, Redis or graph driver. The
runner pins Mem0 and Graphiti off, because their calls to the Router would
pass the tap unseen.

To score the stored answers again after a change to a check, with no new
live run:

```bash
uv run python -m evals.one_assistant_measure.run --rescore \
  --out evals/one_assistant_measure/results_<date>.json
```

A re-score runs only the checks that read the stored answer and tasks. Each
other check keeps its stored value, because a results file keeps no tool
arguments, cards or gateway calls. Each change goes in `rescored`.

The cost of a streamed call is the vendor's `usage.cost`. A buffered call
has no such field, so `rate_card.json` prices it. The card reproduces each
probe of 2026-10-25 within 1 percent.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The scripted run passed, or the live run finished |
| 1 | A scripted check failed |
| 2 | The live run cannot start |

## The fence

`tests/unit/test_one_assistant_measure.py` is the fence (R7). It runs every
plan on both arms. It makes each checker fail with one mutation. It also
proves these facts:

- The pack arm sets the real flag, and the real run loads the real pack.
- A hand-off for setup work passes `no_email_delegation`, and a hand-off of
  mail work fails it.
- A send from turn 2 goes through the held card, with one card.
- The measure installs the mark store.
- The live mode refuses a send that is not the Router.
- The card reproduces the Router's figures.
