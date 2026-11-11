# Copilot pack

kit_version: 1

This pack connects GitHub Copilot to Metorite. It holds no second copy of the rules.

The core is the file `kit/agents-md.md`. Copilot reads `AGENTS.md` anywhere in the repository, and the nearest file wins.

## Install in VS Code

1. Copy `kit/agents-md.md` to the project root. Name the copy `AGENTS.md`.
2. Copy `copilot-instructions.md` of this pack to `.github/copilot-instructions.md`.
3. Copy the folder `kit/skill` into the project as a plain folder. The core points to it.
4. Copy `vscode-mcp.json.example` to `.vscode/mcp.json`. The top-level key is `servers`.
5. Start the server in the Copilot chat. Finish the sign-in in the browser.
6. Send the first message `Call my_work`.

## Install for the Copilot coding agent on GitHub.com

1. Open the repository Settings, then Copilot, then MCP servers.
2. Paste `coding-agent-mcp.json.example` into the JSON box. The top-level key is `mcpServers`.
3. Each server needs `type` and `tools`. A remote server also needs `url`.

## Notes

A secret name must start with `COPILOT_MCP_`. Set it as an Agents secret and refer to it as `$COPILOT_MCP_NAME`. In VS Code, a secret goes through `inputs`. Never write a token in a file of this pack.
