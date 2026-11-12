# Gemini CLI pack

kit_version: 2

This pack connects Gemini CLI to Metorite. It holds no second copy of the rules.

The core is the file `kit/agents-md.md`. Gemini CLI reads `GEMINI.md` from `~/.gemini` and from the workspace.

## Install

1. Copy `kit/agents-md.md` to the project root. Name the copy `AGENTS.md`.
2. Copy `GEMINI.md` of this pack to the project root. It points to `AGENTS.md`.
3. Copy the folder `kit/skill` into the project as a plain folder. The core points to it.
4. Add the blocks of `settings.json.example` to `.gemini/settings.json`. Or add them to `~/.gemini/settings.json`.
5. The block `context.fileName` makes Gemini CLI read `AGENTS.md` too.
6. The block `mcpServers` adds the remote server over Streamable HTTP with `httpUrl`.
7. Run `/mcp auth metorite` and finish the sign-in. Then send the first message `Call my_work`.

## Notes

The key `url` is for the older SSE transport. Use `httpUrl` for Metorite. Never write a token in a file of this pack.
