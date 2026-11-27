/**
 * The switch line of the one assistant (`one_assistant_context.md` §9.4, WT-4).
 *
 * When a turn loads a tool pack that the last turn did not hold, in a chat
 * that is not on hold, the executor sends ONE CUSTOM event
 * `chat_pack_switched` with `{ label }`, the pack label ("Projects"). The
 * chat draws one line, "Switched to Projects". The server sends the event
 * only while `TOOL_PACKS` serves the run, so this file never reads a flag.
 * It offers no chip and no choice: the pack already loaded. The chat
 * translator saves every custom event on the turn, so a reload draws the
 * same line.
 *
 * The server twin is `PACK_SWITCH_EVENT` in
 * `packages/acb_skills/acb_skills/tool_packs.py`.
 *
 * Pure. Fence (R7): `packSwitch.test.ts`.
 */

export const PACK_SWITCH_EVENT = "chat_pack_switched";

/** The longest label a line shows. */
const LABEL_MAX = 80;

/** The words of the line for *label*. */
export function packSwitchText(label: string): string {
  return `Switched to ${label}`;
}

/** The labels of the switch lines of one turn, in order. */
export function packSwitchesOf(
  customEvents: ReadonlyArray<{ name: string; value: unknown }> | undefined,
): string[] {
  const out: string[] = [];
  for (const e of customEvents ?? []) {
    if (e.name !== PACK_SWITCH_EVENT) continue;
    const value = (e.value ?? {}) as { label?: unknown };
    const label =
      typeof value.label === "string"
        ? value.label.replace(/\s+/g, " ").trim().slice(0, LABEL_MAX)
        : "";
    if (label) out.push(label);
  }
  return out;
}
