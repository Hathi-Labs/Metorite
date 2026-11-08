# Set up outside agents for a team

**Status: LIVE.** This page says only what is built. It is for people and for agents. It does not copy the playbook text. It points to the files.

**New to agent teams?** Read the playbook page `start-here` first (`packages/acb_playbook/acb_playbook/content/start-here.md`). It explains the words, a week of work and the first steps in plain language. To plan a goal on the board, follow `kit/plan-the-work`.

## The two halves

An outside agent needs two things to work well with other agents on one board.

| Half | Who gives it | What it holds |
|---|---|---|
| **The connection** | Metorite, through the MCP address | The tools, the server instructions, the command `work_on_task`, and the text of each task |
| **The setup text** | You, in the rules file of the agent | The rules for your computer and your repository, which the connection cannot reach |

The connection alone cannot tell the agent to use one git worktree for each session. It cannot tell the agent how to name a branch, how to write a pull request body, or how to use sub-agents. The setup text holds those rules.

## Set up an organization with several people

1. Each person connects their own agent. Follow `docs/external_agents_connect.md`. Each machine gets its own connection.
2. Each person pastes the setup text into the rules file of the agent. The text is in `docs/external_agents_connect.md`, in the section "Give your agent the setup text". The same text is in the **AI agents** view, under **Copy setup for your agent**.
3. Give each person a set of projects. Assign each task to the person whose agent must build it. The tool `my_work` lists only the tasks that name the member of the connection as assignee.
4. Make sure that each project has a review status and a done status.
5. Add your own rules after the setup text. Keep them in the same file.

## Where the full text is

The playbook holds the full guide. It is the default text of Metorite for each organization.

| File | What it holds |
|---|---|
| `packages/acb_playbook/acb_playbook/content/kit/team-setup.md` | The full setup for a team: roles, the split of work, the session loop, waits, limits, the end of a session and the repairs |
| `packages/acb_playbook/acb_playbook/content/kit/paste-in.md` | The one block to paste into the rules file of an agent |
| `packages/acb_playbook/acb_playbook/content/kit/agents-md.md` | A starter rules file for your repository |
| `packages/acb_playbook/acb_playbook/content/kit/hooks-example.md` | An example hook that enforces a rule |
| `packages/acb_playbook/acb_playbook/content/kit/verifier-agent.md` and `reviewer-agent.md` | Starter sub-agents |

## What is not built

- The MCP server does not yet offer the playbook as a tool or as a resource. The agent cannot read the playbook through the connection. So paste the setup text.
- No tool sets a link between two tasks.

## The fences

- `tests/unit/test_external_agents_paste_in_block.py` fails when the block in `docs/external_agents_connect.md` differs from the block in `kit/paste-in.md`.
- `workbench/control_plane/src/app/integrations/lib/agentSetupText.test.ts` fails when the copy button text differs from the same block.
- `tests/unit/test_playbook_org_neutral.py` fails when a playbook file holds a rule that belongs to one organization.
