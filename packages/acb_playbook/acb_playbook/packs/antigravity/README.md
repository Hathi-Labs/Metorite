# Antigravity pack

kit_version: 2

This pack connects Google Antigravity to Metorite. It holds no second copy of the rules.

The core is the file `kit/agents-md.md`. Antigravity reads `AGENTS.md` or `GEMINI.md` in the workspace. Neither file takes frontmatter.

## Install

1. Copy `kit/agents-md.md` to the workspace root. Name the copy `AGENTS.md`.
2. Optional: copy `packs/gemini/GEMINI.md` to the workspace root. It is the instruction file of the Gemini pack, and this pack reuses it.
3. Copy the folder `kit/skill` into the project as a plain folder. The core points to it.
4. Copy `mcp_config.json.example` to `.agents/mcp_config.json` in the workspace. For all workspaces, use `~/.gemini/config/mcp_config.json`.
5. Restart Antigravity and finish the sign-in. Then send the first message `Call my_work`.

## Notes

The key for a remote server is `serverUrl`. Antigravity does not take `httpUrl`. This pack ships no rule file. A file in `.agents/rules` needs `trigger` frontmatter, or Antigravity drops it without a message. Never write a token in a file of this pack.
