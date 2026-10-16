# Connect an outside agent to Metorite

**Status: LIVE since 2026-10-14, for each organization.** This page says only what is built. The design and the build slices are in `project-docs/specs/external_agent_access.md` (WS-55).

## What you get

An outside AI agent reads your projects and tasks in Metorite, and it makes two kinds of change. The agent acts as you. It sees only what you see. You choose what it may do on the consent page. You can revoke it at any time.

## The address

`https://mcp.metorite.com/mcp`

The **AI agents** view of the Integrations app shows this address, with a copy button and the steps for each client.

## Connect Claude Code

1. In a terminal, run this command:

   ```
   claude mcp add --transport http metorite https://mcp.metorite.com/mcp
   ```

2. In Claude Code, run `/mcp`. Choose **metorite**, then **Authenticate**.
3. Your browser opens the Metorite consent page. Sign in if Metorite asks.
4. Read the page. Its headline names the verified host of the agent. The name that the agent gives itself shows second, as "It calls itself".
5. Make your choices on the page (see below), then select **Approve**.

## Work on a task from Claude Code

When the connection can change tasks, Claude Code shows the command `/mcp__metorite__work_on_task`.

- Run `/mcp__metorite__work_on_task 42` with the task number or the task id. The agent reads the task, claims it, does the work, and links the branch and the pull request.
- When the pull request opens, the agent moves the task to an "in progress" status whose name says review. It moves the task to done only after the merge.
- **Ask an agent** in Metorite gives work to the agent. To continue that work, run the command with no number.

The description of the task is the brief of the agent. Metorite limits what the connection can reach. It cannot limit the tools of the agent on your computer, such as the shell and `git push`. Your Claude Code permission settings control those tools.

## The choices on the consent page

- **The scopes.** Clear a box to give less. You cannot add a scope that the agent did not ask for.
- **Project pins.** Pin the agent to one or more projects. With no pin, the agent reaches each project that you see.
- **Personal projects.** Your personal projects stay out. The box that includes them is off. Select it only if you want the agent to read them.

## What the agent can do

Seven tools read:

| Tool | What it reads |
|---|---|
| `whoami` | The member and the organization of the connection, its scopes and its pins |
| `list_projects` | The projects you see |
| `get_project` | One project |
| `list_tasks` | The tasks you see, at most 100 in one call |
| `get_task` | One task, with its references |
| `get_task_timeline` | The history of one task |
| `search_tasks` | A search of the tasks you see, at most 50 hits |

Six tools make or change a task:

| Tool | What it does |
|---|---|
| `create_task` | It makes a task in a project or a sub-project that you can see, and it can make a subtask. In Projects, the task shows the source "AI agent". With the same `idempotency_key`, a second call gives back the first task and makes no second task |
| `update_task` | It changes the title, the description, the tags, the due date or the custom fields of a task |
| `claim_task` | It makes you an assignee of a task, and the other assignees stay. A task in a backlog or to do status moves to the first in progress status. The agent calls it when it starts the work |
| `set_task_status` | It moves a task to another status. It can move a task to a done status when the agent finishes the work. It never moves a task to a cancelled status. A person does that step |
| `add_task_reference` | It links a pull request, a commit or a branch to a task |
| `add_comment` | It writes a comment on a task, such as a short progress note while it works. You are the author of the comment. It can reply to a top-level comment. A comment holds at most 20,000 characters. With the same `idempotency_key`, a second call gives back the first comment and adds no second comment |

Each change shows in the task history, with the agent as `mcp:<host>`. A status change of the agent can start a workflow of your organization that runs on a status change, as your own change does. A move to done is a status change too. A connection makes at most 120 changes in 10 minutes.

**A comment notifies people.** Each comment that the agent writes notifies the watchers and the assignees of the task, as your own comment does. An `@` followed by the address of a member who can see the task notifies that member too. These notifications go to the bell and the inbox in Metorite. Metorite does not send them by email.

**A comment can start a workflow.** Each comment, also one that the agent writes, sends the event `pm.task.comment_added`. If your organization has a published workflow that starts on that event, the workflow runs. That workflow can call an integration or a web address, as it does for your own comment.

The agent can also do these two things:

- **Start work with a command.** In Claude Code, run `/mcp__metorite__work_on_task` with a task number, or with no number. "Work on a task from Claude Code" above tells how.
- **Ask to archive a task, and you approve it in Metorite.** The tool `request_task_archive` puts the request on the task, and the bell tells you. Only you can approve or decline it. An approve does not archive the subtasks.

## Ask an agent in Metorite

**"Ask an agent" is live in each organization since 2026-10-15.** You can give work to your agent from the task itself:

1. Open the task in Projects, then select **Ask an agent**.
2. Choose one of your connected agents. Only an agent that may make changes shows in the list.
3. Write what the agent must do, then send it. The task shows "Queued for" and the name of the agent.

Your agent finds the work when it next starts a session, because it looks for queued runs first. It does not start by itself.

While the agent works, it can ask you a question on the task. The task then shows "Waiting for" and your name, and the bell tells you. Answer on the task. The agent reads your answer, also in a later session. You can stop the agent from the same place.

Six tools work with these runs:

| Tool | What it does |
|---|---|
| `start_agent_run` | It starts a run on a task, so the task shows that the agent works on it |
| `post_agent_activity` | It posts a progress line, a result or a question in the run |
| `ask_on_task` | It asks you a question on a task, with choices or free text |
| `get_agent_run` | It reads a run and your answers |
| `list_agent_runs` | It lists your runs, such as the runs that wait for the agent |
| `finish_agent_run` | It ends a run |

## What the agent cannot do

- It cannot cancel a task, and it cannot make a task in a done or cancelled status. A move to done does not close the subtasks.
- It cannot assign a task to another person, move a task to another project, or delete a task. It can claim a task for you only.
- It cannot see what you cannot see. If an admin removes your access to a project, the agent loses it on the next call.
- It cannot act after you revoke it.

## Revoke an agent

1. Open **Integrations**, then the **AI agents** view.
2. Find the agent in **My connected agents**.
3. Select **Revoke**, then confirm.

The next call of the agent gets a sign-in error, and its next token refresh fails. An admin with `integrations:manage` sees each agent connection of the organization in the same view, and can revoke any of them.

Metorite also ends a connection that nobody uses for 90 days.

## The clients

| Client | State |
|---|---|
| Claude Code | Tested, version 2.1.296 |
| claude.ai | Not tested yet |
| Claude Desktop | Not tested yet |
| VS Code | Not tested yet. It may need Dynamic Client Registration, which is not built |
| Cursor | Not supported yet |

For claude.ai and Claude Desktop, the setup is **Settings**, then **Connectors**, then **Add custom connector**. Paste the address there.

## What you must know

- Data that the agent reads goes to the company that runs the agent. Metorite does not control that company.
- A task title or comment can hold text that other people wrote. A hostile text can try to give the agent orders. Use an agent that asks you before it makes a change.
- An agent token is not a password. Do not paste it anywhere.

## Related

- `project-docs/specs/external_agent_access.md`: the design, the decisions and the build slices.
- `project-docs/specs/mcp_plugin_integration.md`: the other direction, where Metorite reads outside MCP servers.
