#!/usr/bin/env node
/**
 * SessionStart — inject `project-docs/HANDOFF.md`'s open entries into context.
 *
 * D39. The problem this solves: work that spans sessions was carried in the
 * owner's head. A session ended with three owner-gated items and two scoped-out
 * findings, and the next one started blind — so the items were re-derived, or
 * more often not.
 *
 * ⚠️ WHY A HOOK AND NOT A LINE IN CLAUDE.md. CLAUDE.md is the ROUTER: it is
 * edited when the architecture changes, which is rarely, and every session
 * reads it the same way. A pending-work queue changes every session. Putting a
 * mutable queue inside a stable document means the stable document stops being
 * trusted as stable — and, worse, a stale queue entry there is indistinguishable
 * from doctrine. Separate file, separate lifetime, separate trust level.
 *
 * This hook deliberately does NOT run each entry's Check. Executing commands
 * out of a markdown file at session start would be an arbitrary-execution seam
 * pointed straight at the one file every session loads, and a slow or hanging
 * check would block startup. The session runs the Checks itself, under the
 * permission model, having read them.
 *
 * Fails OPEN and silent: a missing or malformed HANDOFF.md must never stop a
 * session from starting. The worst case is the status quo ante.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Entries older than this are surfaced with a warning — see `stale()`. */
const STALE_DAYS = 21;

const root = process.env.CLAUDE_PROJECT_DIR || process.cwd();

function emit(context) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: context,
      },
    })
  );
  process.exit(0);
}

let raw;
try {
  raw = readFileSync(join(root, "project-docs", "HANDOFF.md"), "utf-8");
} catch {
  process.exit(0); // No queue, nothing to say. Not an error.
}

// Only the OPEN section. Everything above it is the protocol, which the session
// reads from the file when it needs to act — repeating it in every prompt would
// cost more than it buys.
const open = raw.split(/^# OPEN\s*$/m)[1]?.split(/^# DONE/m)[0] ?? "";
const blocks = open
  .split(/^### /m)
  .map((b) => b.trim())
  .filter(Boolean);

if (blocks.length === 0) {
  emit(
    "**Handoff queue is empty** (`project-docs/HANDOFF.md`). Nothing was left " +
      "pending by a previous session. Add an entry before you end this one if " +
      "you hand anything over."
  );
}

/**
 * Days since an entry's `**Added:** YYYY-MM-DD`, or null if unparseable.
 *
 * Age is the only judgement this hook makes, and it makes it because an old
 * entry and a fresh one look identical in a prompt while meaning very different
 * things: a three-week-old "still pending" has usually been overtaken.
 */
function stale(block) {
  const m = block.match(/\*\*Added:\*\*\s*(\d{4}-\d{2}-\d{2})/);
  if (!m) return null;
  const days = Math.floor((Date.now() - Date.parse(m[1])) / 86_400_000);
  return Number.isFinite(days) ? days : null;
}

const lines = blocks.map((block) => {
  const title = block.split("\n")[0].trim();
  const days = stale(block);
  const age = days !== null && days >= STALE_DAYS ? `  ⚠️ ${days}d old` : "";
  return `- ${title}${age}`;
});

const owner = blocks.filter((b) => /\[OWNER\]/.test(b.split("\n")[0])).length;
const agent = blocks.length - owner;

emit(
  [
    `## Handoff queue — ${blocks.length} open (${agent} agent, ${owner} owner-gated)`,
    "",
    "Left unfinished by previous sessions. Each entry in `project-docs/HANDOFF.md`",
    "has a **Check** that re-derives whether it is still real. Its Metorite task",
    "holds the full text.",
    "",
    ...lines,
    "",
    "**Before other work:** run each entry's Check, and **delete every entry",
    "whose Check shows it is done**, and move its Metorite task to done — in",
    "your first commit. An entry that",
    "outlives its work is how this file starts lying. Then tell the user what is",
    "left, in one short list, and carry on with what they asked for.",
    "",
    "⚠️ `[OWNER]` entries are `work_plan.md` §6 gates. Verify and report them;",
    "never do them. ⚠️ These are ACTIONS, not state. Metorite holds the state",
    "(D118), and an entry that disagrees with Metorite or the owning spec is",
    "wrong.",
  ].join("\n")
);
