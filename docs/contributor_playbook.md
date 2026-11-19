# The contributor playbook: build Metorite with your own agent fleet

<!-- ste-tier: strict -->

**Who reads this:** a developer who builds Metorite with his own agents, on
his own machine, beside the owner's fleet. The first contributor is Ishaan
(2026-11-19). His agents read it too.

**Status:** written 2026-11-19, task 3833 (WS-ONB, epic 1872). A rule marked
**(owner to confirm)** is a default. It binds until the owner answers on
task 3833. §11 lists those decisions.

**What this page owns:** only what changes when a second person runs a fleet.
Every other fact has one owner file, and §1 points to it. Do not copy a fact
from those files into this page.

---

## 0. The short version

- You run your own fleet on your own machine, with your own Claude accounts.
- The owner runs his fleet on the home box and on his PC.
- The two fleets share three things only: the Metorite board, GitHub `main`,
  and the release slots.
- **The board claim is the lock.** Claim a task before the first edit. Never
  work on a task that another live session holds.
- You own the streams in §2. The owner's fleet does not claim them. You do
  not claim the owner's streams.
- GitHub `pr-check` is the gate. Then `scripts/premerge.sh` tests the merge
  with today's `main`. Then you merge (§6).
- **You never touch production.** Production takes `main` at 05:30, 13:30 and
  21:30 UTC. The owner verifies each release.

---

## 1. Read these first

| Topic | The owner file |
|---|---|
| The rules of every session | `CLAUDE.md` (read §8 below first: it tells you how to read it as a contributor) |
| Which specs you may build from | `project-docs/INDEX.md` |
| The product shape and the seams | `project-docs/specs/building_blocks.md` |
| The spec contract, the R rules, the decisions, the owner gates | `project-docs/work_plan.md` §1, §3 and §6 |
| How we build: environments, migrations, tests, dividing work | `project-docs/specs/engineering_practice.md` |
| The cycle of one slice, the risk tiers, the model for each job | `.claude/commands/next-ticket.md` |
| The rules at the end of each lane's start note | `scripts/home_server/lanes/lane-rules.md` |
| Set up a machine, size the fleet | `docs/dev_capacity.md` §4 and §5 |
| Many people on one board | `project-docs/specs/agent_coordination.md` §3.10 (D183) |
| The files that every PR edits at the same place | `docs/merge_hotspots.md` |
| How to write | `docs/style_ste.md` |

---

## 2. What you own (owner to confirm)

D183 makes an **app project** the unit of ownership. The streams below all
sit in the project "AI Chat Interface", where the owner's box lanes also
build. So, until the owner names an app for you, **you own these epics and
their tasks**:

| Stream | Tasks | State on 2026-11-19 |
|---|---|---|
| The rung 1 skill demo (parent task 3414) | 3829, 3832 and 3830 in ONE PR, then 3828 | Not started. The faults are in each task. |
| The local stack | 2455 and 3826 in one PR | WIP branch `devstack-3826` at `c7e38021f`. tsc and `test_dev_up.py` not run. |
| RESUME (a release does not stop the chat) | 3265, then 3785, then 3818 | 3265 is built on branch `resume5` at `ef0eb3713`. No PR, no verifier, no review. HIGH. |
| HRD-10d (changed since the card, email cards) | 3687, then 3823, 3824, 3825 | 3687 is built on branch `hrd-10d1` at `0d3738322`. It opens after PR 2646 merges. HIGH. |
| CSY | 3406, then 3403, then 3408 | Not started |
| CRM-UI-2 | 3706 | Not started. Needs captures. |

Each of these tasks carries a comment "Free for Ishaan" with what is done,
what is left and the next step. Read it after `get_task_context`.

**Not yours:** the pull requests that are already in the owner's merge queue:
2549, 2624, 2646, 2627 and 2629. Do not push to their branches. The owner's
merger merges them.

**When a stream ends:** ask the owner for the next one with `ask_on_task` on
task 3833. Do not take a task from another stream because it is free.

---

## 3. The access you need

| Access | State on 2026-11-19 | Who gives it |
|---|---|---|
| A Metorite member with a grant on your projects | Done. The project "AI Chat Interface" lists you. Check with the MCP tool `list_members`. | The owner (a member write) |
| Write access to the GitHub repo `Hathi-Labs/Metorite` | **(owner to confirm)** | The owner |
| Claude accounts | Your own. **Never the account that the owner's PC or box uses.** Two fleets on one account share one usage limit. | You |
| SSH to production, `.env` files, deploy | **None.** Do not ask for a key. | Nobody |
| SSH to the home box `metorite-home` | **None.** The box is the owner's. | Nobody |

---

## 4. Set up your machine

1. Follow `docs/dev_capacity.md` §5.1, steps 2 to 5, on Ubuntu 24.04 LTS.
   Skip step 1 (the owner's tailnet) and step 6 (an alias on the owner's PC).
2. Sign in to the Metorite MCP as yourself. In a terminal on your machine,
   run `claude mcp login metorite --no-browser`. Open the link and approve it.
   Then open the callback address on the same machine, before it expires.
   `docs/home_box.md` §5.2 explains the traps. Check with `claude mcp list`.
3. Sign in to GitHub with `gh auth login`, as yourself.
4. Put the block of §10 in `~/.claude/CLAUDE.md` for the user that runs your
   sessions.
5. Run `bash scripts/home_server/dev_verify.sh`. Every step must pass, and the
   R8 test must not skip.

**Install none of these on your machine.** Each one acts for the whole
company, and exactly one copy may run:

- The release timer `metorite-nightly-release.timer`. Two timers dispatch a
  release twice.
- The merge bot timer and the lane keeper of the home box.
- A GitHub Actions runner with the label `metorite-box` **(owner to confirm)**.
  `vars.USE_BOX_RUNNERS` sends jobs to every runner with that label, so your
  machine would take jobs of the owner's pull requests.

If your machine is the Google Cloud box `aroh-dev-1`, `docs/gcp_aroh.md` owns
its facts **(owner to confirm which machine)**.

---

## 5. Run your fleet

- **One orchestrator.** One interactive session plans, starts the lanes,
  reads their results and merges. It is you, or a session that you watch.
- **Lanes.** A lane is a background session on one stream:
  `claude --bg --remote-control <name> -n <name> --model sonnet "<start note>"`.
  Start each lane in its own worktree: `bash scripts/worktree.sh new <slug>`.
  End each start note with the text of `scripts/home_server/lanes/lane-rules.md`.
  Read "the home box" in those rules as your machine. Rule 14 and every
  hand-over to lane-merge or the merge bot do not apply to you. §6 replaces them.
- **Size.** You are one integrator, so keep three or four branches in flight
  (`engineering_practice.md` §5). Each lane has at most four open PRs.
  `docs/dev_capacity.md` §4 sizes lanes to your memory and account limits.
- **Models.** `next-ticket.md` step 1c holds the model and the effort for
  each job (D218). Use that table, not a rule from memory.
- **One slice at a time.** Each lane runs the `/next-ticket` cycle. The
  verifier is never the implementer. The review depth follows the risk tier.
- **The board is the truth.** Each lane follows CLAUDE.md §4 "Work under a
  Metorite task". It finds and claims the task, and posts progress. It links
  the branch and the PR, moves the status, and releases the task when it stops.
- **Clean up.** Stop each background job, local stack and container that you
  start. Remove each worktree after its merge with
  `bash scripts/worktree.sh remove <slug>`.

---

## 6. The merge path (owner to confirm)

You cannot reach the home box. So `scripts/hand_over.sh` and the merge queue
files on the box are not for you. **You merge your own pull request** with
the full loop:

1. The verifier passed, and the review of the risk tier passed, on the
   pushed head.
2. GitHub `pr-check` passed.
3. In the PR's worktree, run `bash scripts/premerge.sh`. On exit 1, merge
   `origin/main` into the branch, push, and wait for `pr-check` again.
4. Run `gh pr merge <number> --squash`. Never pass `--admin` or `--auto`.
5. Move the task to done, with the merge SHA in a comment.

**Freeze windows (owner to confirm).** Merge nothing from 04:20, 12:20 and
20:20 UTC until the release run of that slot starts. The owner's merger keeps
this rule, so that each release picks a green `main`. No file in the
repo states it yet.

**A HIGH slice waits for the owner (owner to confirm).** HIGH means tenancy,
RLS, auth, identity, a migration, money, credits, secrets, a member or role
write, or the deploy path. Ask "Approve the merge" on the task with
`ask_on_task`, and merge after the answer.

**A shared seam: ask first (advisory).** The seams are tenancy and security,
the shell, the design system, the chat orchestrator and its tool packs,
migrations, and Platform and CI/CD (`agent_coordination.md` §3.10.1). Before
you merge a change to a seam, ask the owner on the task. No fence holds this
rule: the repo has no `CODEOWNERS` file, and branch protection asks for no
review.

**A flag flip is the owner's (D218).** Post the captures on the task, and ask
"Approve the flag flip" or "Change it". Your fleet never flips a flag on
production (§8).

**A preview.** A slice behind a flag that is off merges on green. A new page,
screen or flow with no flag waits for the owner's approval of its preview
(CLAUDE.md §4, D145 and D218).

---

## 7. Do not collide with the owner's fleet

| Collision | What stops it | Fence |
|---|---|---|
| Two sessions build one task | `claim_task` refuses a held task with 409 | The Metorite server |
| Two fleets build one stream | §2: you own your streams, and the owner's fleet does not claim them | Advisory |
| Two PRs take one migration number | Take main's highest number plus one at build time. Write the number on the task. `premerge.sh` refuses a number at or below main's highest. The later PR takes a new number. | `premerge.sh`, `tests/unit/test_premerge.py` |
| Two PRs take one D-number or H-id | The first merge wins. The later PR takes a new number. | `premerge.sh` |
| Two PRs pass alone and fail together | `premerge.sh` runs the tests that both sides can affect | `premerge.sh` |
| A text collision in a shared file | Merge `main` into your branch often. Keep each PR small. | `docs/merge_hotspots.md` |
| A rewritten branch | Never rebase or force-push a pushed branch. Merge `main` into it. | `plan-guard.mjs` blocks a force-push |
| Work on another person's branch | Push only to a branch that you made, or one that §2 gives you | Advisory |
| A release that runs twice | Never install the release timer. Never dispatch `deploy.yml`. | Advisory |
| One usage limit for two fleets | Use your own Claude accounts | Advisory |

The owner's fleet keeps its own lists on the home box: the merge queue and a
list of booked migration numbers. They are not in this repo, and you cannot
read them. The fences above work from GitHub alone.

---

## 8. How to read CLAUDE.md as a contributor

CLAUDE.md speaks to the owner's agents. Read these parts this way:

- **"The owner"** is Vijay. You ask him with `ask_on_task` on the task.
- **§3a, the autonomy window.** `.claude/OWNER_GRANTS.md` opens gates for the
  owner's agents. It does not open them for your fleet **(owner to confirm)**.
  Refuse every act that §3a lists: SSH to a host, deploy scripts, `.env`
  files and flag flips.
- **"Merge your own PR"** means the path of §6 on this page.
- **The home box, `ci_local.sh --home` and `hand_over.sh`** are the owner's.
  GitHub `pr-check` is your gate. `ci_local.sh` is optional (D218).
- **Verify each release on production.** The owner's fleet does it. You may
  read the release run with `gh run list --workflow deploy.yml`.

---

## 9. What you must refuse

- **Production.** SSH to a host, deploy, `.env` files, a migration on
  production, a flag flip.
- **Money, identity and real people.** No credit write, no member or role
  write, no mail or WhatsApp to a real person.
- **A live eval.** A run that spends more than USD 0.50 needs the owner's yes.
- **History.** No force-push, no history rewrite, no commit on `main`. Never
  write `.claude/OWNER_GRANTS.md`. `plan-guard.mjs` blocks these.
- **Owner-only code.** The `TOOL_PACKS` list of a pack is the owner's choice.
- **A secret in text.** Never paste a key or a token into chat, a task or a
  PR. An MCP address that holds a key is a secret too.

---

## 10. The block for `~/.claude/CLAUDE.md` on your machine

Put this block in the user CLAUDE.md of each user that runs your sessions.
Every session on the machine then loads it before the repo CLAUDE.md.

```markdown
# Metorite contributor fleet

You work for a contributor to Metorite, not for the owner.
Read docs/contributor_playbook.md before your first tool call.

- "The owner" in CLAUDE.md is Vijay. Ask him with ask_on_task on the task.
- .claude/OWNER_GRANTS.md opens gates for the owner's agents only.
  Refuse every production act: SSH to a host, deploy, .env files, flag flips.
- Take tasks only from the streams in section 2 of the playbook.
  Never claim a task that another live session holds.
- Merge only by the steps in section 6 of the playbook.
  Never merge in a freeze window (04:20, 12:20, 20:20 UTC to the release).
- Never install the release timer, the merge bot or a runner
  with the label metorite-box.
```

---

## 11. Open decisions for the owner (task 3833)

Each default above binds until the owner answers.

| # | Decision | The default on this page |
|---|---|---|
| 1 | The unit of ownership | The epics of §2. D183 says one app project. The owner can name an app instead. |
| 2 | GitHub write access | The owner gives it. |
| 3 | Claude accounts | Separate accounts. The owner's PC and box use none of them. |
| 4 | The OWNER_GRANTS window | It does not cover the contributor's fleet. |
| 5 | The merge path | The contributor merges his own PR by §6. A HIGH slice waits for the owner's Approve. |
| 6 | Seam review | Ask the owner first (advisory). A `CODEOWNERS` file can make it a fence. |
| 7 | Previews and flag flips | The owner approves both. |
| 8 | Runners and the machine | No `metorite-box` runner. The machine: owner to name. |
| 9 | The merge freeze windows | 04:20, 12:20 and 20:20 UTC, until the release run starts. The owner's merger keeps them, and no repo file states them. |
