# The home box: agent sessions and CI on `metorite-home`

The home box is the CI and agent machine of the project (task 1534). This
page tells a session how to reach it, how to give it a session, and how to
run CI there. It points to the code and does not copy it. The owner of each
topic is named in its section.

Task 2519 wrote this page on 2026-11-09. Before then, most of it lived only in
a session scratchpad and on the owner's PC.

This page is the worked case for one machine. `docs/dev_capacity.md` holds
the method for any machine: how to size the work, and how to add or retire a
machine.

---

## 1. What the box is

| Part | Value |
|---|---|
| Host | The owner's Windows 11 PC. The owner also uses it as a desktop. |
| Linux | The WSL distro `metorite`, Ubuntu 24.04. |
| Size | 16 CPUs and 24 GB of WSL memory, with 16 GB of swap. **Memory is the limit, not CPU.** |
| Disk | `C:\wsl\metorite\ext4.vhdx`. It grows and never shrinks. A full `C:` drive stops the box (§7). |
| Network | Tailscale. The box is on the tailnet as `metorite-home`. |
| Claude | Claude Code, signed in as user `dev` through the account Hub (§5). |

### Users and how to reach them

| User | What it does | From the owner's PC |
|---|---|---|
| `dev` | Agent sessions, the lane keeper, the Hub and Remote Control | `ssh metorite-home` |
| `acb` | Admin, with passwordless sudo | `ssh metorite-home-admin` |
| `ci` | The `ci_local` lane. Docker, no sudo. | `bash scripts/ci_local.sh` reaches it |
| `ghrunner` | The GitHub Actions runners, with rootless Docker (§6.3) | Through `acb` only |
| Windows admin | Repairs only: restart WSL, free disk space | `ssh metorite-home-win` (10 to 17 s per call) |

The three aliases live in `~/.ssh/config` on the owner's PC. `metorite-home`
and `metorite-home-admin` go to the tailnet name `metorite-home`, port 2222,
as `dev` and `acb`. `metorite-home-win` goes to port 22 of the Windows host.
Both sshd servers take keys only.

To set the box up from nothing, use `scripts/home_server/`:
`remote_access.ps1`, then `wsl_setup.ps1`, then `setup.sh`. The user units
are in `scripts/home_server/systemd/README.md`.

---

## 2. Check the box

Run these from the owner's PC. Each one only reads.

```bash
ssh metorite-home 'free -m | sed -n 2p; df -h ~ | tail -1; claude --version'
ssh metorite-home 'cd ~/Metorite && claude mcp list'     # metorite must say "Connected"
ssh metorite-home 'tail -5 ~/lanes/keeper.log; cat ~/lanes/keeper-lanes.txt'
ssh metorite-home 'systemctl --user list-timers --no-legend'
bash scripts/home_server/box.sh sessions                 # each session, its task, its last line
```

Read the results this way:

- Do not start a test run, `dev_up` or a new lane when `available` memory
  is below 5000 MB.
- When `claude mcp list` shows `metorite` as "Needs authentication", a lane
  cannot claim a task or write a comment. Repair the sign-in first (§5.2).
- `claude agents --json --all` lists every session, also the old ones. A
  session with no process is a dead record.

⚠️ An `ssh` call whose output goes straight to the Bash tool can hang past
its timeout. Write the output to a file, then read the file.

---

## 3. Give the box a session (a lane)

A **lane** is one `claude --bg` session on the box, named `lane-<x>`, with
Remote Control on. It works one stream of Metorite tasks, and it follows the
lane rules in `scripts/home_server/lanes/lane-rules.md`.

### 3.1 Before you start a lane

1. Check the box (§2). The Metorite MCP must be connected, and 5000 MB of
   memory must be free.
2. Read the live account and its limits: `bash scripts/home_server/box.sh accounts`.
   On 2026-11-07, twelve lanes used about 4% of one Max 5x account's weekly
   limit in one hour.
3. Choose the tasks. Name the task numbers in the start note. A lane that
   gets "take the next free task" often reports that it has nothing to do.

### 3.2 Write the start note

The start note is the first prompt of the session. Put these parts in one
plain text file on the PC:

1. The lane's name, its stream and the task numbers, in priority order.
2. A time window, not a hard deadline. A lane refuses a HIGH slice that
   cannot finish before a hard deadline, so it stops early.
3. The full text of `scripts/home_server/lanes/lane-rules.md`.
4. For the merge lane only: `scripts/home_server/lanes/lane-merge.md` before
   the lane rules.

### 3.3 Start the lane

```bash
bash scripts/home_server/box.sh start lane-x sonnet path/to/start-note.txt
```

The helper copies the note to `~/lanes/lane-x.txt` on the box and runs this
command there:

```bash
cd ~/Metorite && git fetch -q origin && \
  claude --bg --remote-control lane-x -n lane-x --model sonnet \
    --permission-mode auto "$(cat ~/lanes/lane-x.txt)"
```

Always pass `--model` and `--permission-mode auto`. A background session that
stops on a permission prompt waits forever, because nobody reads its terminal.
Use `opus` only for a lane that does HIGH-tier work (CLAUDE.md §4).

### 3.4 Watch a lane

- `bash scripts/home_server/box.sh sessions` lists each session, its task and
  its last line.
- `bash scripts/home_server/box.sh tail lane-x 10` prints its last 10 messages.
- The Metorite board is the source of truth: each lane posts on its task.
- The Remote Control link opens the lane in the Claude app. The app shows
  `metorite-home` only when it is signed in as the box's live account.
- ⚠️ `SendMessage` cannot reach a box lane. Only sessions on the PC are listed.
- ⚠️ `claude agents` can say `working` for a lane whose turn ended. Read the
  last message with `box.sh tail` before you decide.

### 3.5 Give a lane new orders, or wake it

A lane cannot read a message while it runs. Stop it, then resume it with a
wake note:

```bash
ssh metorite-home 'claude stop <first 8 characters of the session id>'
ssh metorite-home 'cd ~/Metorite && claude --bg --resume <full session id> \
  --remote-control lane-x -n lane-x --model sonnet --permission-mode auto "<wake note>"'
```

- **A resume forks the session.** The new session gets a new id. Take the
  next id from `claude agents --json --all`.
- **Stop the old session first.** Two copies of one lane do the same work
  twice. Two copies of lane-merge can both pass the migration-number check.
- **Pass `-n` and `--remote-control` again.** Without them the fork gets an
  automatic name, and the keeper wakes the old session.
- **Never stop a lane during `premerge.sh`.** The stop leaves its worktree in
  the middle of a merge.
- After an outage or a sign-in repair, start a **fresh** session (§3.3), not
  a resume. A resumed lane kept a dead MCP connection for 12 hours on
  2026-11-08.

### 3.6 Stop a lane

```bash
bash scripts/home_server/box.sh stop lane-x
ssh metorite-home 'pgrep -af wt-<slug>'   # a test run of the lane can outlive it
```

`claude stop` does not stop the session's child processes. Stop a test run
that is left by its process id, never by its test name.

### 3.7 The lane keeper

`metorite-lane-keeper.timer` runs `lane_keeper.py` every 10 minutes as `dev`.
The code and its header are the contract: `scripts/home_server/lane_keeper.py`.
Its fence is `tests/unit/test_lane_keeper.py`.

| File on the box | What it does |
|---|---|
| `~/lanes/keeper-lanes.txt` | The only lanes that may wake, one name per line. Empty file: nothing wakes. |
| `~/lanes/start/<lane>.txt` | The start note of a listed lane that has no session. With no file, the keeper starts nothing. |
| `~/lanes/keeper.log` | Each wake and each skip, with its reason. |
| `~/lanes/keeper-state.json` | The time of the last wake of each lane. |

- To pause all waking, empty `keeper-lanes.txt`. Do not stop the timer.
- The keeper never wakes `lane-chat` or `lane-bill`, because those lanes
  wait on the owner.
- ⚠️ **The timer runs an installed copy, not the checkout:**
  `~/.local/lib/metorite/lane_keeper.py`. After a merge that changes the
  keeper, copy it again:

  ```bash
  ssh metorite-home 'cd ~/Metorite && git fetch -q origin && \
    git show origin/main:scripts/home_server/lane_keeper.py > ~/.local/lib/metorite/lane_keeper.py'
  ```

- ⚠️ **The keeper passes no `--model` and no `--permission-mode`** when it
  starts or resumes a lane (task 2466, gap 7). Until that is fixed, start
  each lane yourself (§3.3). Use the keeper only to wake lanes that already
  run with those flags.

### 3.8 The merge lane and its queue

One lane, `lane-merge`, waits for the GitHub checks of every pull request and
merges it. The other lanes keep building. Its role is in
`scripts/home_server/lanes/lane-merge.md`.

| File on the box | What it holds |
|---|---|
| `~/lanes/merge-queue.tsv` | One line for each hand-over: pull request, task, worktree, lane, note. Tabs between the fields. |
| `~/lanes/merge-queue.log` | One line for each result: `DONE <pull request> <sha>` or `BACK <pull request> <reason>`. |

A lane hands over a pull request only after three steps. The verifier passed,
the review of its risk tier passed, and the owner approved its preview when
it needed one.

### 3.9 End of a delegation

1. Each lane ends its task with one disposition (lane rule 12). That is Done,
   a question with `ask_on_task`, or `release_task` with a hand-off comment.
2. Stop the lane (§3.6).
3. Remove its merged worktrees on the box with
   `bash scripts/worktree.sh remove <slug>`. Before you remove one, push any
   commit that is on no remote.

---

## 4. Remote Control and the web UI

| Unit (user `dev`) | What it runs |
|---|---|
| `metorite-rc.service` | `claude remote-control --name metorite-home --spawn worktree`, through the wrapper `~/.local/bin/metorite-rc`. It lets the owner open a box session from the Claude app. |
| `cloudcli.service` | The CloudCLI web UI on `127.0.0.1:3456`. Tailscale Serve on the Windows host publishes it on the tailnet. |
| `claude-account-switcher.service` | The account Hub (§5). |

A session that the owner opens through Remote Control runs as `dev` in
`~/Metorite`. It does not end by itself. Archive it in the Claude app when it
is done.

---

## 5. Claude accounts and the Metorite sign-in

### 5.1 Accounts

The Hub holds several Claude accounts and makes one of them live for every
session on the box. Its code is in the separate `claude-hub` repository.

```bash
export BOX_HUB=https://<the box's tailnet name>     # the Hub address, not in this repo
export BOX_SWITCH_SKIP=<the account that this PC's sessions use>
bash scripts/home_server/box.sh accounts           # each account, its session and weekly use
bash scripts/home_server/box.sh switch auto        # switch only when the live one is near a limit
```

- `box.sh` signs in to the Hub with the secret in
  `~/.metorite/secrets/switcher-login.txt` on the PC. Never print that file.
- Put the PC's own account in `BOX_SWITCH_SKIP`. Two machines on one account
  share its limits.
- ⚠️ **A switch ends the turn of every busy lane.** Wake each lane again after
  each switch (§3.5).
- Remote Control is registered under the live account.
- `~/.local/bin/hub-add-accounts` on the box signs a new account in.

### 5.2 The Metorite MCP sign-in on the box

When `claude mcp list` shows `metorite` as "Needs authentication":

1. The owner runs this in PowerShell on the PC:
   `ssh -t metorite-home "cd ~/Metorite && claude mcp login metorite --no-browser"`.
2. The owner opens the link and approves it.
3. The owner sends the address `http://localhost:<port>/callback?...` that the
   browser shows.
4. Deliver it on the box at once, while the listener lives:
   `ssh metorite-home 'curl -s "<that address>"'`.
5. Check with `claude mcp list`. Then start fresh lanes (§3.3), not resumes.

The login needs a terminal. A login with no terminal opens no listener. A
callback address that waits too long expires. The per-box token of task 1819
(MCP-G17) replaces this procedure when it ships. `docs/box_connect.md` holds
the box-token steps.

---

## 6. CI and CD on the box

### 6.1 `ci_local`: the full pr-check, on the box

```bash
bash scripts/ci_local.sh          # the commit at HEAD. Commit first.
bash scripts/ci_local.sh --home   # only the home box
bash scripts/ci_local.sh --full   # every test, as pr-check does on a merge
```

The home box is the default lane, and this PC's `metorite-ci` distro is the
second lane. The script's header and `engineering_practice.md` §7a own the
details. On 2026-11-06 a full suite peaked at about 10.7 GB of memory and
took about 41 minutes (task 1907). So run one full suite at a time on the box.

GitHub `pr-check` is the gate (D218), so `ci_local` is optional. Before each
push, run the tests that the diff can affect:
`timeout 2400 uv run python scripts/affected_tests.py --run`.

### 6.2 Merge and release

- `bash scripts/premerge.sh` runs the collision fences right before
  `gh pr merge` (CLAUDE.md §3 rule 7). Merge with `--squash`. Never pass
  `--admin` or `--auto`.
- `metorite-nightly-release.timer` starts each release. It runs
  `nightly_release.sh` at 05:30, 13:30 and 21:30 UTC, and the script
  dispatches `deploy.yml` on `main` (D178, D194).
- ⚠️ The timer runs the installed copy
  `~/.local/lib/metorite/nightly_release.sh`. Copy it again after a merge
  that changes it, as for the keeper (§3.7).
- Verify each release by evidence: the backup line, the migration ledger, the
  deployed SHA and the smoke job (CLAUDE.md §3 rule 8).

### 6.3 GitHub Actions runners on the box

The runners run as user `ghrunner` with the label `metorite-box` only (task
2304, CIV-23). Eight are registered. `metorite-box-1` to `metorite-box-4` are
online, and `metorite-box-5` to `metorite-box-8` are offline. Read them with
`gh api repos/Hathi-Labs/Metorite/actions/runners --jq '.runners[] | "\(.name) \(.status)"'`.

The `runs-on` line of each pr-check job says where it runs. Eight jobs can go
to the box: `changes`, `lint`, `unit-tests`, `migrations`, `frontend`,
`frontend-operator`, `caddy` and `secret-scan`. The unit shards (`test`),
`backup-restore` and `e2e` always run on GitHub-hosted runners. A job goes
to the box only when both of these are true:

- The repository variable `USE_BOX_RUNNERS` is `1`.
- The run is not a pull request from a fork. A push run counts as ours.

**They are off now.** `USE_BOX_RUNNERS` has been `0` since 2026-11-09 02:22
UTC, because the box ran the jobs 2 to 3 times slower than GitHub-hosted
runners. Turn them on only when GitHub's own queue backs up
(`gh run list --status queued`):

```bash
gh variable set USE_BOX_RUNNERS --body 1   # on
gh variable set USE_BOX_RUNNERS --body 0   # off: the kill switch
```

The variable changes only where jobs go. The online runners keep running in
both states.

### 6.4 Postgres for tests

Put the data directory of a test database on tmpfs:
`--tmpfs /var/lib/postgresql/data`. A benchmark on 2026-11-08 (task 2304)
measured about 2.5 ms for each commit on the WSL disk. R8 suites on the box
then ran 10 to 50 times slower than on GitHub.

---

## 7. Traps

| Trap | What to do |
|---|---|
| A closed local port **hangs** on the box (WSL mirrored loopback). It does not refuse. | A test that expects a refused connection hangs. `metorite-close-6379.service` closes port 6379 until task 1745 lands. |
| `C:` on the Windows host fills up, and WSL fails with `E_FAIL`. | Only the owner deletes host files. Look for orphan `swap.vhdx` files under `%LOCALAPPDATA%\Temp`. The lasting fix is to move the distro to `D:`. |
| Two browser suites at once use all the memory. | Hold `flock ~/lanes/e2e.lock` around a browser run. |
| `ssh box 'pkill -f "<match>"'` kills its own shell. | Build the match from two parts. |
| An agent cannot install a service on the box or delete host files. The classifier blocks both. | Give the owner the exact command. |
| A lane restarts WSL, Docker or the box. | Never. Other lanes and the CI lane share it. |
| A lane waits on the owner in its terminal. | Nobody reads a background terminal. Ask with `ask_on_task`. |

---

## 8. Files and units on the box

| Path | What it is |
|---|---|
| `~/Metorite` | The checkout that lanes and the keeper use. Lanes work in their own worktrees beside it (`~/wt-<slug>`). |
| `~/lanes/` | Keeper files, start notes, the merge queue and `e2e.lock`. |
| `~/.local/lib/metorite/` | The installed copies of `lane_keeper.py` and `nightly_release.sh`. |
| `~/.local/bin/` | `metorite-rc`, `hub-add-accounts`, `metorite-dev-toolchain`, `metorite-dev-verify`. |
| `~/.claude/settings.json` | Allows the project MCP servers `metorite`, `supabase` and `codegraph`. |
| User units of `dev` | `metorite-rc`, `cloudcli`, `claude-account-switcher`, `metorite-lane-keeper.timer`, `metorite-nightly-release.timer`, `metorite-close-6379`. |
