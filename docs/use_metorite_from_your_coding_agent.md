# Use Metorite from your coding agent

**Status: BUILT 2026-11-11 (KIT-8, task 2558).** No live run on a real client has been done for any client below. The conformance matrix in `project-docs/specs/orchestration_playbook.md` (section 6a) says "not run" for each row. Treat each step as written from the vendor docs, and report a step that fails.

This page is for people and for agents. A plain-text index for agents is `docs/llms.txt`.

## What you get

- The MCP address `https://mcp.metorite.com/mcp`. It gives your agent the tools to read and change your tasks.
- The kit: a rules file, a skill and a connection example for your client.
- The protocol: the agent calls `my_work`, then `claim_task`, and works on one slice.
- The live text stays in the MCP: prompts, `read_playbook` and the guidance of your organization.
- The kit holds no token. Your agent acts as you and sees only what you see.

This page does not copy the kit or the MCP text. It points to them.

## Before you start

1. Have a Metorite account, with access to at least one project.
2. On your own computer, the agent signs in with OAuth in your browser. The steps for the consent page are in `docs/external_agents_connect.md`.
3. A headless box has no browser. It uses a box token, and `docs/box_connect.md` gives the steps.
4. Never write a token in a kit file or in a rules file.

## Claude Code

### Connect

1. In a terminal, run `claude mcp add --transport http metorite https://mcp.metorite.com/mcp`.
2. In Claude Code, run `/mcp`, choose **metorite**, then choose **Authenticate**.
3. Sign in on the Metorite consent page in your browser, then select **Approve**.
4. Put the rules file `CLAUDE.md` in your repository. Claude Code also reads `AGENTS.md` when no `CLAUDE.md` exists.
5. The full guide for this client is `docs/external_agents_connect.md`.

### Get the kit

Open Integrations, then **AI agents**, and choose the tab for Claude Code. Select **Download the agent kit**. The control appears only for organizations on the early list. The zip holds an install note, `METORITE_KIT.md`. If you already have a `CLAUDE.md` or `AGENTS.md`, merge the kit text into it.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## OpenAI Codex

### Connect

1. Add a table `[mcp_servers.metorite]` with `url = "https://mcp.metorite.com/mcp"` to `~/.codex/config.toml`.
2. For one project only, put the same table in `.codex/config.toml` of a trusted project.
3. Run `codex mcp login metorite` and finish the sign-in in the browser.
4. Put the rules in `AGENTS.md` in the project, or in `~/.codex` for all projects.
5. The example is `packages/acb_playbook/acb_playbook/packs/codex/config.toml.example`.

### Get the kit

Open Integrations, then **AI agents**, and choose the tab for Codex. Select **Download the agent kit**. The control appears only for organizations on the early list. Read `METORITE_KIT.md` in the zip. Merge the kit text into an existing `AGENTS.md`, and keep the file under 32 KiB.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## GitHub Copilot

This section covers the editor (VS Code) and the coding agent on GitHub.com.

### Connect

1. In VS Code, put the file `.vscode/mcp.json` in the workspace, with a top-level `servers` key.
2. Add an entry with `"type": "http"` and `"url": "https://mcp.metorite.com/mcp"`, then sign in when VS Code asks.
3. For the coding agent, open the repository settings, then Copilot, then MCP servers.
4. Paste one JSON with a top-level `mcpServers` key. Each server needs `type`, `tools` and `url`.
5. Put the rules in `.github/copilot-instructions.md`. Copilot also reads `AGENTS.md`.

The examples are `vscode-mcp.json.example` and `coding-agent-mcp.json.example` in `packages/acb_playbook/acb_playbook/packs/copilot`. A secret name for the coding agent must start with `COPILOT_MCP_`.

### Get the kit

Open Integrations, then **AI agents**, and choose the tab for Copilot. Select **Download the agent kit**. The control appears only for organizations on the early list. Read `METORITE_KIT.md` in the zip. Merge the kit text into an existing `.github/copilot-instructions.md`.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## Google Antigravity

### Connect

1. Copy `mcp_config.json.example` to `.agents/mcp_config.json` in the workspace.
2. For all workspaces, use `~/.gemini/config/mcp_config.json` instead.
3. Set the key `serverUrl` to `https://mcp.metorite.com/mcp`. Antigravity does not take `httpUrl`.
4. Restart Antigravity and finish the sign-in.
5. Put the rules in `AGENTS.md` or `GEMINI.md` in the workspace.

### Get the kit

Open Integrations, then **AI agents**, and choose the tab for Antigravity. Select **Download the agent kit**. The control appears only for organizations on the early list. Read `METORITE_KIT.md` in the zip. Merge the kit text into an existing `AGENTS.md` or `GEMINI.md`.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## Gemini CLI

### Connect

1. Open `~/.gemini/settings.json`, or `.gemini/settings.json` in the project.
2. Add an entry under `mcpServers` named `metorite`, with `httpUrl` set to `https://mcp.metorite.com/mcp`.
3. Add the setting `context.fileName`, so that Gemini CLI also reads `AGENTS.md`.
4. Start Gemini CLI and finish the sign-in in the browser.
5. Put the rules in `GEMINI.md` in `~/.gemini` or in the workspace.

### Get the kit

Open Integrations, then **AI agents**, and choose the tab for Gemini CLI. Select **Download the agent kit**. The control appears only for organizations on the early list. Read `METORITE_KIT.md` in the zip. Merge the kit text into an existing `GEMINI.md`.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## Any other MCP client

### Connect

1. Find the setting where your client adds a remote MCP server.
2. Choose the Streamable HTTP type, and give the address `https://mcp.metorite.com/mcp`.
3. Choose OAuth for the sign-in. A headless box uses a box token (see `docs/box_connect.md`).
4. Put the rules in the instruction file that your client reads. Many clients read `AGENTS.md`.

### Get the kit

Use any tab on Integrations, then **AI agents**. Select **Download the agent kit**. The control appears only for organizations on the early list. The rules file in the zip is neutral, so you can copy it to the name that your client reads. Read `METORITE_KIT.md` in the zip.

### Check it works

1. Send the first message `Call my_work`.
2. Read the rows that come back.
3. Ask the agent to claim a slice, with `claim_task`.

## The text for your rules file

Use the **Copy setup for your agent** button on the same Integrations view. Or copy the block in `packages/acb_playbook/acb_playbook/content/kit/paste-in.md`. Both give the same text, and a test fails when they differ. This page does not copy the block. Its first line is:

`# Metorite: how this agent works with tasks`

## Next steps

- For a team of people, read `docs/external_agents_team_setup.md`.
- For the prompts, run `set_up_agent_team`, `plan_slices`, `write_brief`, `review_my_fleet` or `run_retro`.
- For the full playbook text, call `read_playbook`.
