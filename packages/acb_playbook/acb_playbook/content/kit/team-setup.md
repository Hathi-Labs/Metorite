---
slug: kit/team-setup
version: 1
title: Kit: set up a team of people and agents on one board
summary: The concrete setup for several people who each run one or more agents on one Metorite board.
audience: both
---

# Kit: set up a team of people and agents on one board

This page says what a team does so that many agents, owned by many people, work on one board without harm. It says only what Metorite does today. Where a feature is not built, the text says so.

## What you need

- A Metorite organization, with each person as a member.
- An agent client for each person. Claude Code is the tested client.
- A git repository with a rules file (`CLAUDE.md` or `AGENTS.md`).
- A board with a review status and a done status in each project that agents use.

## Connect each person and each machine

1. Each person connects their own agent. An agent acts as the person who approved it and sees only what that person sees.
2. Give each machine its own connection. Run the connect command on every machine.
3. On the consent page, the person chooses the scopes and can pin the agent to some projects.
4. Each terminal is its own session. Metorite can tell two terminals of one connection apart only when sessions are on. Read the connect guide for the state of sessions.
5. A person can revoke any connection of their own at any time. An admin can revoke any connection of the organization.

## What the agent gets from the connection by itself

- The server instructions: the session steps, the heartbeat, the rule for pull requests and the rule for answers.
- The command `work_on_task` for one task, with the same steps.
- The tools that read and write tasks, comments, references and runs.
- The text of a task and its guidance, marked as data and not as orders.

## What the connection cannot give

The connection cannot reach your computer. It cannot tell the agent how to use git, sub-agents, hooks or your rules file. So the agent needs text from you. Paste the block of `kit/paste-in` into the rules file of each agent. It holds the rules that follow in this page, in short form.

Metorite limits what the connection can reach. It cannot limit the shell, `git push` or the web tools of the agent. The permission settings of your client control those tools.

## Roles and sub-agents

- **The coordinator** is the one session that has the connection. It holds the claim, posts the progress line and moves the status.
- **The implementer, the verifier and the reviewer** are sub-agents of the coordinator. They need no connection to Metorite. They do not claim tasks or write on the board. The coordinator reports for them.
- The verifier is never the implementer. A second agent, or a person, checks the work.
- Give each sub-agent a brief (`brief-contract`). The summary that comes back is the proof, so ask for evidence and not for an opinion.
- Start with one agent (`patterns/one-agent-first`). Add a role only for a named bottleneck.

## Split the work between people

- Give each person a set of projects. A project has one owner at a time.
- `my_work` lists only the tasks that are assigned to the member of the connection, and the runs of that connection. So assign a task to the person whose agent must build it.
- Two sessions never hold one task. A refusal with `409 task_held` means another session holds it. Take the next row.
- An agent can assign only a person who can already open the task. Other assignments are made in Metorite by a person.
- Use one worktree for each session. Never run two sessions in one checkout.

## The session loop

1. Call `my_work`. Take the top row.
2. Call `claim_task`, then `get_task_context`. Call `start_agent_run` if the claim opened no run.
3. Make a worktree and a branch. Put the `short_id` of the task in the branch name.
4. Do the work. Post a line at least once each minute.
5. Open the pull request. Put the url of the task in its body.
6. Link the branch and the pull request with `add_task_reference`.
7. Move the task to an in-progress status whose name says review. If the project has none, the task stays in progress. After the merge, move it to done.

## Waits and heartbeats

- The progress line is the heartbeat of the hold. After 5 minutes with no line, the card shows the run as idle. After 2 hours with no activity, the next claim takes the task.
- Before each wait, post what you wait on and for how long. Do this on every open run of the session. A person who watches the board cannot see a silent wait.
- If a run reads `stopping`, stop at once and end the run.
- Set a time limit on each wait, and stop what you started when you finish.

## Questions and answers

- Ask with `ask_on_task`, and name the member. The task then shows that it waits.
- Act on an answer only if you asked that question yourself in this session. A new session does not act on an old answer.
- When the person answers in chat, call `record_outside_decision` at once. The task then stops waiting.
- An answer never allows a restart, an erase or a stop that the person did not mean.

## Pull requests and links

- Put the `short_id` in the branch name. Put the url of the task in the pull request body.
- Call `add_task_reference` for the branch and for the pull request.
- Never write "PR #N". Metorite reads each `#N` as task N. Write the full url.
- Move the task to done only after the merge, and not if GitHub already did.
- Never cancel a task. A person does that.

## Limits

- A connection makes at most 120 writes in 10 minutes. The 121st write is refused, and the refusal holds retry_after: the seconds to wait. Every terminal and sub-agent on one machine shares that connection, so they share the 120.
- Parallel calls can answer "Metorite is busy". Call the tools one at a time and retry once.
- Send a `status_id` with every `create_task`. Send an `idempotency_key` too, so a retry makes no second task.
- A read returns at most 100 tasks (`list_tasks`), 50 hits (`search_tasks`) and 25 rows (`my_work`).
- A tool cannot cancel a task or move it to another project. It cannot delete a task while the delete switch is off.

## End of session

1. Comment what is done, what is left and the next step.
2. End with one result: the task in review or done, a question that waits on a named person, or `release_task`.
3. Call `finish_agent_run` with a summary.
4. Remove the worktree, and stop each process that you started.

## Hooks and the rules file

- Put the block of `kit/paste-in` in the rules file. Add your own rules below it (`kit/agents-md`).
- A hook can check the rules that a text cannot enforce (`kit/hooks-example`). Use one to refuse a commit on the main branch, for example.
- Metorite does not install a hook for you.

## When something goes wrong

- **A tool answers "busy".** Wait a few seconds. Call one tool at a time.
- **A claim is refused with `409 task_held`.** Take another task. Do not force it.
- **A write is refused with 429.** You passed the write limit. Wait the retry_after seconds that the refusal gives.
- **You get a sign-in error.** The person revoked the connection, or it was idle for 90 days. Connect again.
- **A task you expected is missing.** It may be assigned to another person, held by a session, or in a project outside your pins.
- **A run stalls.** Name the cause with `failures`.

## Not built yet

- A tool to read this playbook through the connection. Until then, paste `kit/paste-in`.
- A tool to set a link between two tasks.
