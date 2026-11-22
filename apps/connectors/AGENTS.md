# Connectors

<!-- ste-tier: strict -->

## Purpose

A connector is a program that runs on the **customer's machine**, not on ours.
Metorite never deploys it. The customer installs it, and it reads a local
system for the chat, such as TallyPrime. Spec: `integrations_console.md` §3.8.
Build brief of the first one: `docs/tally_bridge_brief.md`.

## Rules

1. **A connector imports nothing from `apps/services/` or `packages/`.** It
   ships alone, with its own bundled Python. Fence:
   `tests/unit/test_tally_bridge_fences.py`.
2. **A connector is not a uv workspace member.** Its `pyproject.toml` names its
   own dependencies. The root workspace globs do not match `apps/connectors/*`.
3. **Three modules, kept apart:** the reader of the local system, the MCP
   tools, and the transport. The reader does not import the tools or the
   transport. The tools do not import the transport. A later connector, such
   as one for Busy, reuses the transport and writes a new reader and new tools.
   Fence: `tests/unit/test_tally_bridge_fences.py`.
4. **No name, domain, ledger or address of ours** in the program or its
   defaults (brief test 17). Fence: `tests/unit/test_tally_bridge_fences.py`.
5. **Put SYNTHETIC in the file name of a fixture that we wrote by hand.** A
   synthetic match never counts as a pass of brief tests 6, 7 or 8.

## Folders

- `tally_bridge/`: the Tally bridge (TLY-1, task 3656). Its README lists what
  is built, and the Tally answers to record on a real TallyPrime.
