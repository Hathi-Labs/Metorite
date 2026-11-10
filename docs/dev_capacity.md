# Development capacity: sessions, sub-agents, runners and CI on any machine

This page tells you how to size the delivery system to the machines that you
have today. It also tells you how to add a machine, for example a large cloud
VPS, and how to retire one. The system must not depend on one machine.

`docs/home_box.md` is the worked case for the home box `metorite-home`. Read
it for the exact commands on that box. This page holds the method for any
machine.

Task 2623 wrote this page on 2026-11-10. Each measured number names its date
and its source. A number marked **estimate** has no measurement yet. Measure
it before you trust it.

---

## 1. The parts of the delivery system

| Part | What it is | Where it runs | Owner of the details |
|---|---|---|---|
| **Orchestrator** | One interactive Claude session. It plans, starts lanes, verifies and merges. | Where the owner works, usually the owner's PC | CLAUDE.md §4, `.claude/commands/next-ticket.md` |
| **Interactive session** | A Claude session that a person drives, in its own worktree | Any machine with the repo | CLAUDE.md §4 (one worktree per session) |
| **Lane** | A background session (`claude --bg`) with Remote Control, on one stream of tasks | A Linux machine with Claude Code signed in | `docs/home_box.md` §3, `scripts/home_server/lanes/lane-rules.md` |
| **Sub-agent** | An agent that a session starts with the Agent tool, inside that session | The machine of its parent session | `.claude/agents/*.md`, CLAUDE.md §4 (models) |
| **Workflow** | A script that starts many sub-agents in a fixed order | The machine of its parent session | The Workflow tool. Opt-in only (§4.3). |
| **Local CI run** | The pr-check jobs in local containers (`scripts/ci_local.sh`) | A Linux machine with user `ci` and Docker | `engineering_practice.md` §7a, `scripts/ci_pc/slots.sh` |
| **GitHub-hosted CI** | pr-check on GitHub's runners | GitHub | `project-docs/specs/ci_velocity.md` |
| **Self-hosted runner** | A GitHub Actions runner on our machine, label `metorite-box` | A Linux machine with user `ghrunner` | `docs/home_box.md` §6.3 |
| **Release timer** | Dispatches `deploy.yml` at 05:30, 13:30 and 21:30 UTC | Exactly ONE always-on machine | `docs/home_box.md` §6.2 |
| **Coordination** | The Metorite board through the MCP, and the merge queue file | Every session needs the MCP signed in | CLAUDE.md §4, `docs/home_box.md` §3.8 |

Never run a lane, a CI run or a runner on the production VPS. It serves
customers, and a test run there can take its memory.

---

## 2. What limits throughput, in order

More machines raise only the last two limits. Check the first three before
you buy a machine.

1. **Integration.** Reviews, owner previews and merges limit how much work
   lands. CLAUDE.md §4 and `engineering_practice.md` §5 cap the branches in
   flight at three or four for each person who integrates. More lanes past
   that cap make a queue of unmerged work, not more delivery.
2. **Claude account limits.** On 2026-11-07, twelve lanes used about 4% of
   one Max 5x account's weekly limit in one hour. The account Hub on the box
   switches between accounts (`docs/home_box.md` §5.1).
3. **GitHub concurrency.** The organization is on GitHub Team, which runs
   60 jobs at once. Pull request 2039 used 9 jobs on 2026-11-09. A run that
   selects more test shards uses up to about 17.
4. **Machine memory.** Memory, not CPU, limited the home box (2026-11-08).
   §3 lists the cost of each part.
5. **Disk.** Each worktree takes disk, and so do Docker images.

---

## 3. What each part costs

| Unit | Memory | Disk | Source |
|---|---|---|---|
| Idle background session | about 0.35 GB | — | Box audit, 2026-11-07 |
| Lane that builds and runs unit tests | 2 GB (**estimate**) | 3.4 GB per worktree | Disk: `du` of a box worktree, 2026-11-10 |
| Local dev stack (`scripts/dev_up.sh`) | 3 GB (**estimate**) | — | Measure with `docker stats` |
| Browser suite run | up to 8.5 GB for one `next-server` | — | Box, 2026-11-07 |
| Local CI run, selected tests (1 shard) | reserves 8 GB | one worktree | `slots.sh`: `CI_FIXED_GB=5` plus `CI_SHARD_GB=3` per shard |
| Local CI run, full suite (5 shards) | peaked at 17.7 GB, reserves 20 GB | one worktree | `slots.sh` header, measured 2026-11-04 |
| Self-hosted runner | capped at 5 GB each | its work folder | Runner units, task 2304 |
| Kept free for the system | 6 GB | — | `slots.sh`: `CI_OTHER_GB=6` |

`slots.sh` already sizes local CI to the memory of each machine. It reads
`MemTotal`, and a run starts only when its reservation fits. So a larger
machine takes more CI runs with no code change.

The home box shows what too much looks like. On 2026-11-08 it ran 9 lanes and
8 runners on 24 GB. It had 475 MB free and 8.2 GB of swap in use, and its
unit shards ran 36 minutes instead of 14 on GitHub.

---

## 4. Size the work to what you have today

Do these steps at the start of each work period, and again after a machine
or an account changes.

### 4.1 Read the four inputs

1. **Ready work.** Call `get_plan` for each active epic. Count the streams
   that have a ready task on or near the critical path. A lane with no ready
   task burns tokens for nothing.
2. **Account headroom.** Run `bash scripts/home_server/box.sh accounts`.
3. **Machine memory.** Run `free -m` on each machine. Use the `available`
   column.
4. **GitHub queue.** Run `gh run list --status queued --limit 50`. A long
   queue means that more pushes only wait.

### 4.2 Choose the numbers

1. **Lanes in total:** the smallest of these three numbers:
   - the ready streams (4.1 step 1)
   - the in-flight cap of §2 step 1
   - what the account headroom carries until its weekly reset
2. **Lanes on one machine:** `(available GB - CI reservation - 6) / 2`. Use
   the 2 GB **estimate** of §3. Round down.
3. **Local CI runs on one machine:** let `slots.sh` decide. Keep the lanes
   from taking the memory that CI needs.
4. **Self-hosted runners:** keep them off while GitHub's own queue is short
   (`docs/home_box.md` §6.3). Turn them on only when the queue backs up.
5. Put the lane names in `~/lanes/keeper-lanes.txt` on each machine, and
   start each lane by hand (`docs/home_box.md` §3.3).

### 4.3 Sub-agents and workflows

- A session starts sub-agents with the Agent tool. Several sub-agents start
  at once when one message holds several Agent calls.
- **Model per role** (CLAUDE.md §4): Sonnet does the work. Opus does the
  adversarial review, HIGH-tier slices and hard problems. Haiku does lookups.
  Each file in `.claude/agents/` names its model, and
  `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in `.claude/settings.json` is the
  default. The fence is `tests/unit/test_agent_model_routing.py`.
- **A sub-agent shares its parent's machine.** Four sub-agents that each run
  tests need four times the test memory. Count them in §4.2.
- Give each sub-agent its own scratch folder and its own worktree when it
  writes files. Two agents in one folder overwrite each other.
- **A workflow is opt-in.** Start one only when the owner asks for it, or
  says "ultracode". The size guideline is set in `/config`
  ("Dynamic workflow size"). Its default is under 10 agents.

---

## 5. Add a machine (for example a large cloud VPS)

The machine needs Ubuntu 24.04 LTS, the release that production runs.

### 5.1 Set it up

1. Join the machine to the tailnet, and give it a short name, for example
   `metorite-vps1`. Open no public port except SSH with keys.
2. Run `scripts/home_server/setup.sh` as the admin user. The header of the
   script says how. It makes the users `acb`, `dev` and `ci`, and installs
   Docker, git, gh, Node 20, uv and Claude Code. It also clones the repo.
3. As `dev`, run `bash scripts/home_server/dev_toolchain.sh`. It installs the
   Python env, the npm packages, Playwright, gitleaks and codegraph.
4. As `dev`, run `bash scripts/home_server/dev_verify.sh`. All steps must
   pass, and the R8 test must not skip.
5. Sign in to Claude, gh and the Metorite MCP (`docs/home_box.md` §5).
   Check with `claude mcp list`. Allow the project MCP servers in
   `~/.claude/settings.json`, as on the box (`docs/home_box.md` §8).
6. Add an SSH alias for the machine to `~/.ssh/config` on the owner's PC.

### 5.2 Give it work

- **Lanes:** `BOX_HOST=metorite-vps1 bash scripts/home_server/box.sh start lane-x sonnet note.txt`.
  Every `box.sh` command takes `BOX_HOST`.
- **Lane keeper:** install `metorite-lane-keeper.service` and `.timer` from
  `scripts/home_server/` as user units. Copy `lane_keeper.py` to
  `~/.local/lib/metorite/`. Each machine has its own `~/lanes/keeper-lanes.txt`.
- **Local CI:** `CI_HOME=ci@metorite-vps1 CI_HOME_PORT=22 bash scripts/ci_local.sh --home`.
  To make it the default lane, export both variables in the shell profile of
  the PC.
- **Self-hosted runners:** see §5.3.
- **Release timer:** do not install it. Exactly one machine starts each
  release. Two timers dispatch the release twice.

### 5.3 Self-hosted runners on a new machine

The design of task 2304 (CIV-23) binds every runner:

- A user `ghrunner` with no sudo, not in the `docker` group, with rootless
  Docker.
- The runner registers with `--labels metorite-box --no-default-labels`, so
  only jobs that name `metorite-box` reach it. Each runner gets a name with
  the machine in it, for example `metorite-vps1-1`.
- Each runner runs as a systemd user unit with `MemoryMax`. Never set
  `MemoryHigh` below a job's need: it slows the job to a crawl.
- Get the registration token with
  `gh api -X POST repos/Hathi-Labs/Metorite/actions/runners/registration-token`.
  Never print the token in chat or a log.
- `vars.USE_BOX_RUNNERS` routes jobs to every runner with the label, on every
  machine. A fork's pull request never reaches them.

The `runs-on` lines of `.github/workflows/pr-check.yml` decide which jobs can
go to the label. Today the unit shards (`test`) always run on GitHub. A large
machine can take them too, but that change needs the owner's decision and a
change to the fence `tests/unit/test_box_runner_routing.py`.

### 5.4 A worked example: a 128 GB VPS

This is arithmetic from §3, not a measurement.

| Use | Memory |
|---|---|
| Kept free for the system (`CI_OTHER_GB`) | 6 GB |
| Two full local CI runs at once (`slots.sh`) | 40 GB |
| Four self-hosted runners at 5 GB | 20 GB |
| Left for lanes, at 2 GB each (**estimate**) | 62 GB, about 30 lanes |

Memory would allow about 30 lanes. §2 caps the number far lower. The
integration cap allows three or four branches in flight for each person who
integrates. Twelve lanes used 4% of one account's weekly limit in one hour.
So use a large machine first for **CI**: make it the default `ci_local` lane,
and give it the runners. Then give it the lanes, and keep their number at the
§4.2 result.

---

## 6. Retire or swap a machine

1. Empty `~/lanes/keeper-lanes.txt` on it, so that nothing wakes.
2. Ask each lane to end its tasks with a disposition (lane rule 12). Then
   stop each lane (`docs/home_box.md` §3.6).
3. Push every branch that has a commit on no remote. Then remove the
   worktrees with `bash scripts/worktree.sh remove <slug>`.
4. Deregister its runners. List them with
   `gh api repos/Hathi-Labs/Metorite/actions/runners`, then delete each one
   with `gh api -X DELETE repos/Hathi-Labs/Metorite/actions/runners/<id>`.
5. When it holds the release timer, install the timer on the next machine
   first. Then disable it here.
6. When it is the default `ci_local` lane, change `CI_HOME` on the PC.
7. Remove it from the tailnet, and remove its SSH alias.

---

## 7. The knobs

| Knob | Where | What it changes |
|---|---|---|
| `~/lanes/keeper-lanes.txt` | Each machine | Which lanes the keeper may wake. |
| `~/lanes/start/<lane>.txt` | Each machine | The start note for a listed lane with no session. |
| `BOX_HOST`, `BOX_HUB`, `BOX_SWITCH_SKIP` | Env of the PC | The machine and the account Hub that `box.sh` drives. |
| `CI_HOME`, `CI_HOME_PORT`, `CI_LANE` | Env of the PC | Where `ci_local.sh` runs. |
| `CI_SHARD_GB`, `CI_FIXED_GB`, `CI_OTHER_GB` | Env of the CI machine | The memory that each local CI run reserves. |
| `vars.USE_BOX_RUNNERS` | GitHub repository variable | `1` routes the allowed jobs to self-hosted runners. `0` is the kill switch. |
| `runs-on` in `pr-check.yml` | The repo | Which jobs may go to self-hosted runners. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `.claude/settings.json` | The default sub-agent model. |
| `model:` in `.claude/agents/*.md` | The repo | The model of each named sub-agent. |
| Dynamic workflow size | `/config` | How many agents one workflow may start. |
| `metorite-nightly-release.timer` | Exactly one machine | Which machine starts the releases. |

---

## 8. What comes next

Today a person starts and sizes the lanes with this page. Story 2507 asks
for the product to do it: plan the program, read the critical path, and start
agents from the capacity that is free. The runner of task 1924 (SW-78) holds
the rule for that: start `min(free slots, ready slices)` sessions for each
lane. When it ships, this page changes from a procedure to a reference.
