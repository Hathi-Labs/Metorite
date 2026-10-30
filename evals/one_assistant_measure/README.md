# The measure: the real tool packs against delegation (WS-67, OA-5, OA-7h and OA-8e)

This folder compares two shapes of the one assistant on the same email
chats. The owning spec is `project-docs/specs/one_assistant_context.md`,
§4 and §5, slices OA-5 (D116) and OA-7h. The report of the first live run is
`docs/one_assistant_measure_2026-10-25.md`.

**Status.** OA-8e phase 1 (the CRM and Projects chats) was built on
2026-10-28. See the section "OA-8e" below. OA-5 was built on 2026-10-25. OA-7h phase 1 was built on
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

## OA-8e: the CRM and Projects chats

**Status.** Phase 1 of OA-8e was built on 2026-10-28: the harness, the
chats, the checks and the scripted run. No live run exists yet. The live run
is phase 2. It waits for the owner to open the Router tunnel. OA-10 waits for
the report of that run (`one_assistant_context.md` §4.3.3 and §5, OA-8e).

The owner turns on `TOOL_PACKS` for each pack only after a measure shows that
the pack is as good as delegation. Email has its measure above. This section
adds the same measure for `crm-records` (OA-8a) and for the four Projects
packs (OA-8b). It extends this folder and builds no second harness (§7
item 2).

### The arms

| Arm | State | What it holds |
|---|---|---|
| `delegation` | Runs | Today's code. The orchestrator hands each CRM or Projects task to the real specialist. |
| `pack` | Runs | The spec calls it `packs`. `run.pack_flag(owners)` turns on EVERY pack of the email, CRM and Projects owners for the eval org. |
| `packs+spawn` | Listed, skipped | OA-9 on too. It waits for OA-9b, because `spawn_subagents` does not exist. |

The key `pack` stays, so the stored results of the email measure still read.
`run --arms delegation,pack,packs+spawn` prints `SKIP` for the third arm.
`run.spawn_built()` looks for `async def spawn_subagents` in the code.
`test_spawn_subagents_is_not_built` fails when it finds one. Then the arm
and the fan-out plans must be built.

### The chats

`scenarios_work.py` holds the chats. `scenarios.py` holds the `Scenario`
fields `set`, `surface`, `mode` and `skip`. Each turn has fewer than 12
words (`SHORT_MESSAGE_WORDS`), so no turn-kind request goes out. The pack
loads by the surface (layer a), by a word of the pack (layer a), or by the
model (layer b). A test holds the words of each chat against the word lists.

| Set | Chats | Turns | What it covers |
|---|---|---|---|
| `crm` | 9 | 12 | The pipeline, a deal's history, a find and a move, a logged call, a new lead, a converted lead, Mirror mode, a word of the pack with no surface, and a miss with no word and no surface. |
| `projects` | 14 | 19 | My work, a project view, an overdue list, a find, a capacity question (`projects-plan`), a stuck-work report, a status change and a tag batch (`projects-structure`), an archive and three new tasks (`projects-bulk`), the Calendar and Tasks pages, a private task, and two misses. |
| `mixed` | 2 | 4 | "Make a task from it" after a mail read. "Log this call on Orbit", then "Make a task to send the new quote". |
| `fanout` | 3 | 3 | Five companies, five tasks, ten invoices. Listed and skipped until OA-9b. |

Each chat has a `surface`: `crm`, `projects`, `tasks`, `calendar` or
`email`. Four chats have none. A chat has at most one write, and the card is
its question. The `mode` of one CRM chat is `mirror`.

### The fake gateway

`workgateway.py` answers the `/crm` and `/projects` routes from fixtures: 4
deals, a lead, a contact, two organizations, 8 tasks in two projects, 4
statuses. The email chats keep `mailbox.py`. The CRM and Projects tools
reach the gateway through `httpx`, so the fake sits at the transport. A
request to any other URL goes to the guard that the harness installed
first. A route with no fixture answers 404 and shows in
`unknown_routes`. The scripted run fails on it. No real CRM, no real
mail and no member data are in the run.

### The checks

The checks that read a turn by name:

| Check | Passes when |
|---|---|
| `ran_tool` | A tool of the list ran, in the orchestrator or in a sub-run. A call that answers "Requested function not found" did not run. |
| `work_write` | A write of the method and path reached the gateway, and its body holds the text. |
| `no_unasked_write` | At most N write tools ran. With N = 0, no write reached the gateway. |
| `right_pack` | Pack arm. A tool of the pack ran in the orchestrator's own run, or the route went to the owner. None on the delegation arm. |
| `answer_all`, `answer_some`, `answer_none` | The answer holds, or lacks, the facts. |

The checks that run on every turn, and give None when they do not apply:

| Check | Passes when |
|---|---|
| `no_crm_delegation`, `no_projects_delegation` | Pack arm. No hand-off to the owner. A hand-off passes only when the pack that the turn needed was neither loaded nor offered by the loader in that turn. A hand-off with an unread log fails. |
| `mirror_respected` | In Mirror mode, no record write ran and none reached the gateway. A note that the run logs stays legal. |
| `one_card_per_write` | A write tool ran, and the turn shows at least one card and no more cards than write calls. |
| `tools_per_call` | No request of the orchestrator carries more than 110 tools. |
| `held_card`, `one_card`, `no_email_delegation` | The email checks, as above. |
| `spawned`, `not_spawned` | `spawn_subagents` ran, or did not. The fan-out chats use `spawned`. |

The row counts hand-offs by owner (`specialist_handoffs`) and, for email, by
kind (`handoffs`). The timing and cache columns are the email
ones, and the report adds them for each set.

OA-8i: the row counts each try of a hand-off that the product refused on its
own (`handoffs_refused`). The result of the call must be a refusal text of
`acb_skills.tool_packs` in full. Such a try ran no work. So it fails no
`no_*_delegation` check, and stop result 11 does not count it. The report
shows it on its own row.

A hand-off that ran still fails the check. The email check
`no_email_delegation` reads the tasks. So a hand-off of mail work that the
product refused still fails it.

### What the report adds

`run --report` prints one table per set and arm: pass, first text and total
(median and 90th percentile), calls, input tokens, cache share and cost. It
adds the loader table: the turns that needed a pack, the layer (b) turns,
the packs that loaded and were not used, the time of layer (a) in
milliseconds, the tools in a request, and the cache share of the turns with
a model load. Last, it prints the ten stop results of §4.3.3. Results 3, 4,
9 and 10 show `n/a`, because they need the `general` set, a run with no
packs and the spawn arm.

### Findings of the first scripted run

- On a Projects, Tasks or Calendar page the core Projects pack (23 tools)
  stands in the request. The plan pack (35) and the structure pack (34) do
  not fit beside it under the cap of 110. The loader does not offer them,
  and the Projects specialist stays. A plan or a status turn on those pages
  goes to the specialist, and the plan of `pm_capacity` and `pm_status`
  says so (`Plan.handoff`). A word of the pack ("task tags", "stuck
  tasks") loads the pack in place of the core pack. `pm_tags` and
  `pm_stuck` show it. `pm_capacity_open` has no surface, so the pack fits.
  The base of this harness is 58 tools. At that base the email pack (31
  tools) does not fit beside the core Projects pack either. The live run
  reads the real count in the line `executor.pack_request_tools`.
- After a read of mail, the CRM pack is held and drops its four writes
  (OA-8a). The delegated CRM run is held the same way. So no chat here logs
  a mail on a deal. This is a known limit, not a result of the measure.
- The first draft of `ran_tool` passed a tool that was not in the request.
  The run answered "Requested function not found", and the call still
  showed in the events. The check now ignores such a call.

### Run the new chats with no model

```bash
uv run python -m evals.one_assistant_measure.run --scripted
uv run python -m evals.one_assistant_measure.run --scripted --scenarios crm_pipeline,pm_status
```

With no `--scenarios`, the run takes the email chats, the CRM chats, the
Projects chats and the mixed chats, and it lists the fan-out chats as
skipped. The scripted plan loads the pack with `load_skill_tools` when
neither the surface nor a word of the turn does.

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

### Test one tier on another model (`--tier-map`)

`--tier-map` is for the eval only. It changes no product code and no flag.
Use it to ask whether a cheaper tier can serve the chat.

```bash
uv run python -m evals.one_assistant_measure.run --live --arms pack   --tier-map tier-balanced=tier-fast --out chunk_flash_a.json
```

1. A map is a list of `<tier>=<tier>` pairs. The target must be a tier.
   The Router refuses a model id ("name a tier, not a model").
2. The wire tap rewrites the `model` field of each request for the named
   tiers. It changes no other field. A tier that the map does not name goes
   out as before, and that includes System 1 (`tier-fast`).
3. The arm shows the map in its name, for example `pack[balanced=flash]`.
   The word `flash` stands for `tier-fast`. A row keeps `arm` and adds
   `arm_label` and `tier_map`. The summary and the tables use `arm_label`.
4. Each call lists `requested` (the tier the agent asked for), `model` (the
   tier that went out) and `served` (the model the Router names). The
   results header lists `tier_map` and `served_models`. Read `served_models`
   first. If the operator rebinds `tier-fast`, only that field shows what ran.
5. A call is priced by its `served` model, else by the target tier. The
   cost is the vendor `usage.cost` when the call has one. Otherwise the
   `flash` card in `rate_card.json` prices it, and the peak factor applies.
6. `--merge` refuses a chunk whose rows are not named by its own map. It also
   refuses one arm name that two maps share. A mapped and an unmapped chunk
   merge as two separate arms. The merged file lists `tier_maps`.

### Spend rules (after the drain of 2026-10-30)

A live run goes through the production Router, on the one DeepSeek account.
On 2026-10-30 the account ran out of funds, and production chat failed for
about 50 minutes. The harness total had read USD 4.62, and the Router had
billed USD 12.70 for the eval member. Three rules now bind each run.

1. **Count every call, and count the unknown at the worst case.**
   - The wire tap now covers the System-1 client. Before, each turn that asked
     the turn kind sent one request that no tap saw.
   - A call with no usage frame counts from the size of its request
     (`cost_source` `estimate`), and not as USD 0.
   - A send that reached the Router and that no tap saw counts at a worst case
     of 20k input tokens and 4k output tokens (`unseen_usd` on the row).
   - A call that starts in a peak band counts double (`peak` on the call).
   - A refused call (status 4xx or 5xx) counts nothing.

   The counted total is a safe upper bound, not an exact bill. Compare it with
   the `usage_event` rows of the eval member after each run.
2. **The budget stops the run at once.** `--budget-usd` counts the earlier
   chunks too (`--spent-before` and `--spent-before-from`). The runner checks it
   before each chat and before each turn after the first. A chat that crosses
   the cap ends there, and the file says `stopped`.
3. **Do not run in a peak band.** DeepSeek charges double in two bands.
   They run from 01:00 to 04:00 and from 06:00 to 10:00 UTC, Monday to Friday
   (<https://api-docs.deepseek.com/quick_start/pricing/>). The bands are
   `spend.PEAK_BANDS_UTC`. `--live` refuses to start in a band and prints the
   next off-peak start. Pass `--allow-peak` to override. A run that reaches a
   band between two chats stops, unless you passed `--allow-peak`.

The report prints the counted spend of each agent: the orchestrator, each
specialist, the worker, `system-one` and `unseen`. Read it to see where the
money went.

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

## Fix round 2 (after the first live run)

The first live run (`docs/one_assistant_measure_oa8e_2026-10-28.md`) found six
defects of the harness. All six are repaired, and none changes a product file.

1. `workgateway.py` answers the routes that the live models called: the
   `crm_*` read family (short reads, fields, pipelines, owners, deal contacts,
   the four reports, aggregate and stage dwell), the archive route, create
   project, calendar, my calendar, my inbox, my reminders, my areas, intake,
   assignees, people names, needs attention and agent runs. A route with no
   fixture still answers 404 and shows in `unknown_routes`.
2. A refused call is no write (`checks.refused`). That covers a tool that is
   not found, dropped or held, and a call that answers `Refused`.
3. `crm_report` and `status_report` carry `read_only=False` on purpose, because
   `make_report=true` writes files. They send only GET, so the harness table
   `READ_DESPITE_ANNOTATION` reads them as reads. The product is not at fault.
4. `scenarios_work.amount()` takes `450,000`, `4,50,000`, `450000` and `4.5 lakh`.
5. `run.no_local_database` closes the graph opener in every run, so
   `retrieve_entity_context` and `retrieve_sales_context` read no local database.
6. `--spent-before-from a.json,b.json` sums what each chunk spent by itself.
   Do not pass the `spent_usd` of a chunk, because it holds the chunks before it.

The fan-out chats and the `packs+spawn` arm are wired in phase 3 (below).

## Phase 3: the fan-out chats and the `packs+spawn` arm

`packs+spawn` is the `pack` arm plus `SPAWN_SUBAGENTS_ORGS=<the eval org>`,
set in the eval process only. A fan-out chat (`set = fanout`) runs on `pack`
and `packs+spawn`. Delegation holds no spawn tool, so it does not run them.

| Chat | Turn | Must |
|---|---|---|
| `fan_companies` | Research five companies, with their about pages | not spawn (five light reads), and name each company |
| `fan_invoices` | Total ten invoices, each on its supplier's public site | not spawn, and total 233,400 |
| `fan_contracts` | Payment terms and late-delivery penalty of five long contracts (about 40,000 characters each, built in `contracts.py`, facts past character 20,000 and 30,000, so a plain fetch misses them). Turn 2: the strictest penalty, and the cost of a 10-day delay on a 5 lakh order | turn 1: spawn, every child item parses, and name each vendor's two facts. Turn 2: Kaveri and Rs 15,000. It compares the input tokens and the time of turn 2 on each arm. This is the research chat of stop result 9 |
| `fan_tasks` | Five tasks from a list | not spawn (a write), one card |
| `fan_one_read` | One page read | not spawn |
| `fan_three_searches` | Three web searches | not spawn |

A child reads only fixtures (`fanout.py`): the web tools `web_search` and
`fetch_page` are replaced for each chat, so a live run reaches only the
Router. `no_local_database` still closes the entity graph. The worker's
scripted model (`fanout.WorkerModel`) decides from its request, because five
children share it. A research turn plays one `spawn_subagents` call on the
spawn arm, and `Plan.solo` (page by page) on the `pack` arm.

OA-9f: a spawn pays off for long material or a chain of steps. It does not pay
off for light reads (`one_assistant_context.md` section 6.3). Run 3 spent 31 to
34 s on a spawn of five page reads. The same reads in one parallel step took 11
to 21 s.

New checks: `spawned` and `not_spawned` (None off the spawn arm),
`items_contract` (no child answer that the parser could not read, no job error
and no job without an item), and the wall time of each fan-out (`measure.fanout`
in the log tap, `fanout_ms_mean` in the summary). Stop results 9 and 10 read
them. The `right_pack` check also accepts the read tools of the `crm` family in
the base for `crm-records` (`checks.BASE_COVER`). A hand-off never passes on that.

## The live run of OA-8e (phase 2, not run)

The live run needs the tunnel and the grant `deploy`, as above. Run each
chunk in less than 10 minutes. The runner alternates the order of the arms,
as before. `--repeat 2` gives two runs of each chat on each arm.

```bash
R="uv run python -m evals.one_assistant_measure.run --live --repeat 2 --timeout 200 --budget-usd 8"
$R --scenarios crm_pipeline,crm_deal_story,crm_move_deal,crm_log_call --out chunk_crm_a.json
$R --scenarios crm_new_lead,crm_convert,crm_mirror,crm_open_deals,crm_miss --spent-before <USD of chunk a> --out chunk_crm_b.json
$R --scenarios pm_my_day,pm_project_view,pm_find_task,pm_capacity,pm_capacity_open --out chunk_pm_a.json
$R --scenarios pm_stuck,pm_status,pm_tags,pm_archive,pm_new_tasks --out chunk_pm_b.json
$R --scenarios pm_calendar,pm_private_task,pm_no_surface,pm_miss,mix_task_from_mail,mix_call_then_task --out chunk_pm_c.json
uv run python -m evals.one_assistant_measure.run --merge chunk_crm_a.json,chunk_crm_b.json,chunk_pm_a.json,chunk_pm_b.json,chunk_pm_c.json --out evals/one_assistant_measure/results_oa8e_<date>.json
```

The runner never sets `TOOL_PACKS`, `TOOL_PACKS_ORGS` or
`SPAWN_SUBAGENTS_ORGS` on a box. It sets the flag in the eval process only.
The report in `docs/` for OA-8e also needs a run of the `general` and `miss`
sets and a run with no packs. Those are not in this phase.

## The fence

`tests/unit/test_one_assistant_measure_oa8e.py` is the fence of OA-8e (R7). It
runs every CRM, Projects and mixed plan on both arms, proves that
`pack_flag` turns on every pack of each owner, and gives each new check one
case that passes, one that fails and one named mutation. It also pins the
skip of the fan-out chats.

`tests/unit/test_one_assistant_measure.py` is the fence of the email measure (R7). It runs every
plan on both arms. It makes each checker fail with one mutation. It also
proves these facts:

- The pack arm sets the real flag, and the real run loads the real pack.
- A hand-off for setup work passes `no_email_delegation`, and a hand-off of
  mail work fails it.
- A send from turn 2 goes through the held card, with one card.
- The measure installs the mark store.
- The live mode refuses a send that is not the Router.
- The card reproduces the Router's figures.
