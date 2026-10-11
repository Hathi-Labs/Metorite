# Connect an outside agent to Metorite

**Status: DRAFT. This feature does not exist yet.** The place of the screens (the Integrations app) is a recommendation that the owner has not yet confirmed. This page describes how it will work after the build in `project-docs/specs/external_agent_access.md`. Do not give this page to a customer before the owner turns the feature on.

## What you get

An outside agent reads and updates your work in Metorite. The agent acts as you. It sees only what you see. You choose what it may do, and you can stop it at any time.

## Before you start

- Your organization admin must switch on **Connected agents**. The switch is off by default.
- You need a Metorite seat. The agent uses your seat and costs nothing extra.
- Your agent client must support remote MCP servers with sign-in. Claude and ChatGPT do. Claude Code does.

## Connect an agent

1. Open your agent client. Find the setting that adds an MCP server or a custom connector.
2. Enter this URL: `https://mcp.metorite.com/mcp`
3. Sign in to Metorite when the browser opens.
4. Read the consent screen. It names the agent and the address it connects from.
5. Select what the agent may do. See the table below.
6. Pin the agent to one or more projects if you want to limit it.
7. Select **Approve**.

The agent now shows in **Integrations**, filtered to inbound connections.

## What each choice lets the agent do

| Choice | The agent may |
|---|---|
| Read projects | Read the projects, tasks, comments and history that you can see |
| Change projects | Create tasks, change fields and status, comment, claim a task, and add a pull request or commit to a task |
| Read company context | Read your company name, domain, branding and shared memory |
| Stay connected | Keep working without a new sign-in until you stop it |

The agent never gets more than you have. If an admin removes your access to a project, the agent loses it on the next call.

Your **personal projects** stay private. The consent screen has a box to include them. The box is off.

The agent cannot delete anything. It cannot change who can see a project. It cannot read your email or your messages.

## Example: a coding agent works on project 7

1. Connect the agent with **Read projects** and **Change projects**. Pin it to project 7.
2. Ask the agent: "Take the next coding task in project 7."
3. The agent lists the coding tasks that wait. It claims one. You become the assignee and the status moves to "in progress".
4. The agent works in your code checkout. Metorite does not see your code.
5. The agent adds the branch and the pull request to the task. It writes a short comment.
6. The agent sets the status to "in review". A person merges and closes the task.

Each change shows in the task history. The history names the agent, for example `mcp:claude-code`.

## Example: design a page from company context

1. Connect Claude with **Read company context** only.
2. Ask Claude to design a landing page for your company.
3. Claude reads your company context and writes the page in its own window.

Claude cannot see your projects in this example. Add **Read projects** only if you want it to.

## Stop an agent

1. Open **Integrations** and filter to inbound connections.
2. Select the agent.
3. Select **Remove**.

The agent gets a sign-in error on its next call. An organization admin can remove any agent in the organization.

## What Metorite records

Metorite writes one audit row for each call. The row holds the member, the agent, the tool, the target and the result. An admin reads the rows in the admin view.

## What you must know

- Data that the agent reads goes to the company that runs the agent. Metorite does not control that company.
- A task title or comment can hold text written by other people. A hostile text can try to give the agent orders. Use an agent that asks you before it writes.
- An agent token is not a password. Do not paste it anywhere.

## Limits

- 100 rows in one list call.
- A call limit for each agent and each member. The numbers are set in the build.
- Metorite removes an agent that you do not use for 90 days.

## Related

- `project-docs/specs/external_agent_access.md` — the design, the decisions and the build slices.
- `project-docs/specs/mcp_plugin_integration.md` — the other direction, where Metorite reads outside MCP servers.
