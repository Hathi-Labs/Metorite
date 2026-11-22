# Codex pack

kit_version: 2

This pack connects OpenAI Codex to Metorite. It holds no second copy of the rules.

The core is the file `kit/agents-md.md`. For Codex, the core is the `AGENTS.md` file. Codex reads `AGENTS.md` and no other name.

## Install

1. Copy `kit/agents-md.md` to the project root. Name the copy `AGENTS.md`.
2. For all projects, copy it to `~/.codex/AGENTS.md` instead.
3. Keep the file under 32 KiB. This is the default of `project_doc_max_bytes`. The core is far smaller.
4. Copy the folder `kit/skill` into the project as a plain folder. The core points to it.
5. Add the block of `config.toml.example` to `~/.codex/config.toml`. Or add it to `.codex/config.toml` of a trusted project.
6. Run `codex mcp login metorite`. Finish the sign-in in the browser.
7. Send the first message `Call my_work`.

## Notes

Codex custom prompts in `~/.codex/prompts` are deprecated and local only. This pack has no prompt file.

A headless box uses a box token and not OAuth. The steps are in the box connect guide of the Metorite docs. Never write a token in a file of this pack.
