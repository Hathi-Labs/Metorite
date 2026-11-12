# How every lane works

These rules go at the end of the start note of every box lane
(`docs/home_box.md` §3.2). The owner approved the plan on 2026-11-04 and
added rules 10 to 15 on 2026-11-06. Task 2519 saved them here on 2026-11-09.
The text of record before that was a session scratchpad (`lane-common-v4`).

You are a lane on the home box `metorite-home`. You run as user `dev` in
`~/Metorite`. Read CLAUDE.md first, and follow it exactly.

1. **Call `my_work` first, before each new task.** A run that the owner
   answered (`run_answered`) or queued (`run_queued`) comes first. Read it with
   `get_agent_run`, then go on with that task. Check out its branch in a
   worktree. The task's references name the branch. Then find the next task
   in YOUR lane that nobody holds, with `search_tasks` or `list_tasks` with
   `unheld` true. Claim it.
   - Never take a task outside your lane.
   - Never take a task that a live session holds.
   - Refuse OWNER-GATE work (`work_plan.md` §6).
2. **Read the whole task.** After `claim_task`, call `get_task_context`. Read
   the description and EVERY comment. When the task has more than 20
   comments, read the older ones with `get_task_timeline` (kind comments).
   The owner's feedback is often in an old comment or in an answer on an
   earlier run.
3. **Build one narrowed slice** with `/next-ticket`, in its own worktree
   (`bash scripts/worktree.sh new <slug>`).
   - Do NOT use the EnterWorktree tool. Its move outside `.claude/worktrees/`
     stops the session on a permission prompt that nobody can answer.
   - Run each command as `cd <worktree path> && ...` in Bash. Give subagents
     the absolute path.
   - Sonnet does the work. Use Opus only for a HIGH-tier slice or an
     adversarial review.
4. **Never wait on the owner, and never wait in silence.** When a slice needs
   a decision, a D145 preview or a flag flip, you MUST call `ask_on_task`. A
   thought or a comment that says "waits on the owner" does NOT reach the
   owner. Then take your next task.
   - A preview question names the pull request, what changed, where to click,
     and the visual-review result in words. The owner cannot open a localhost
     address on this box, so do not send one.
   - When you post the question, stop your local stack (`scripts/dev_up.sh`
     and its containers). Check that `docker ps` shows no container of your
     worktree. Start it again only when the owner asks for a live look.
5. **Put the small slices of one feature into one pull request** where you
   can. Never skip the verifier or a required gate.
6. **Local CI:** `bash scripts/ci_local.sh`. When another run holds the lane,
   do not wait more than 20 minutes. Push the branch, and use GitHub's
   pr-check on the SAME SHA as the full-suite evidence.
7. **A test that is red on `main` is not yours.** Before you blame your
   change, read the newest `main` run with `gh run list --branch main`. Do not
   edit a spec that another task repairs.
8. **Post progress on your task at least once a minute**
   (`post_agent_activity`), as CLAUDE.md requires. Metorite is the source of
   truth for progress.
9. **Keep going.** After each slice that you merge or hand to lane-merge, take
   the next free task in your lane. Stop only when your lane has no free
   AGENT-SAFE task, or at an account usage limit. Before you stop, release or
   finish your task, with a note on what is done and what is next.
10. **Clean up after yourself.** Other lanes share this box.
    - Give every wait an end. A wait loop (`until ...; do sleep`) needs a
      deadline. Run each test command under `timeout`.
    - Stop a background job with TaskStop when you no longer need it. Stop
      each tunnel, tmux session, preview server and container that you started.
    - After a merge, remove your worktree with
      `bash scripts/worktree.sh remove <slug>`.
    - Before the final message of a turn, list your background jobs. Stop each
      one that the next step does not need. Name the ones that stay, and say
      why.
11. **Share the box.** The lanes and the local CI lane share 24 GB of memory.
    Memory, not CPU, is the limit.
    - Before you start `dev_up`, `ci_local`, a browser run, or a pytest run of
      more than one file, read `available` with `free -m`. Below 5000 MB, do
      not start it. Push the branch and use GitHub's pr-check on the same SHA,
      or wait up to 20 minutes and read it again.
    - Run at most one `dev_up` stack in your lane. Stop it when you post the
      preview question.
    - Hold `flock ~/lanes/e2e.lock` around a browser run. Two at once use all
      the memory.
    - Do not watch GitHub CI for more than 10 minutes. Rule 14 says where a CI
      wait goes.
    - Never restart WSL, Docker or the box. Never stop another lane's process.
12. **How a turn ends.** Before your final message, each task that you hold
    ends with exactly one of these:
    - Done, or the review status while its pull request waits on a person.
      Then call `finish_agent_run`.
    - A question with `ask_on_task`. NEVER put a question for the owner only
      in your terminal. Nobody reads a background session's terminal.
    - `release_task`, with a comment of what is done, what is left and the
      next step.
13. **Each slice follows CLAUDE.md §4.** Preview the change to the owner with
    `ask_on_task`. Then run the gate: `ci_local` or GitHub pr-check on the
    same SHA, then `premerge.sh`. Then merge. Production takes `main` at the
    next release slot: 05:30, 13:30 or 21:30 UTC. Never dispatch `deploy.yml`.
14. **A CI wait goes to lane-merge.** GitHub can queue a pull request for
    hours, so one lane, lane-merge, waits for the checks of every pull request
    and merges it.
    - Hand a pull request over when the verifier passed, the review of its
      risk tier passed, and the owner approved its preview (if it needed one).
      Only GitHub checks or `premerge.sh` may remain.
    - To hand it over, run
      `bash scripts/home_server/lane_handover.sh <PR> <task> <worktree> <lane> <tier> "note"`.
      The tier is `LOW`, `STANDARD` or `HIGH`. The PR body needs a line that
      starts with `VERIFY: PASS`. The script appends the line to
      `~/lanes/merge-bot-queue.tsv`, and the merge bot reads it. Never append
      to a queue or a log by hand.
    - Comment on the task: "Handed to lane-merge: <pull request URL>. Verify:
      <result>. Review: <result>. Only GitHub checks remain." Then call
      `release_task`. Lane-merge claims the task, merges it, and moves it to
      Done.
    - Keep the worktree. Lane-merge runs `premerge.sh` in it and removes it
      after the merge.
    - Then take your next task. Do not wait for the merge.
    - When a required check of your pull request goes red, lane-merge
      comments the failing test on the task and releases it. Find it with
      `my_work` or `search_tasks`, fix it, push, and hand it over again. Read
      `~/lanes/merge-bot.log` for `BACK` lines after each hand-over.
15. **Save GitHub runs.** A red run costs a full set of jobs.
    - Before each push, run the tests that your diff can affect on this box:
      `timeout 2400 uv run python scripts/affected_tests.py --run`. For
      frontend files, also run `npx tsc --noEmit` and the `vitest related`
      command that it prints. Rule 11 still applies.
    - Push only on a pass. Push once for each round of fixes, not once for
      each commit.
    - Do not push to "see what CI says". Run the test here.
