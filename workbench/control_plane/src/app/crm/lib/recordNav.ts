// ── `j` and `k` on the record page (WS-53 CRM-X4 item 5) ──────────────────
//
// Pure, so the runner (no DOM) can test every rule. `RecordSheet.tsx` listens
// for the keys and draws "3 of 50" and the two arrows. `page.tsx` hands it the
// order of the list that the member came from, or null.
//
// Spec: project-docs/specs/crm_platform.md section 19.8 item 5.
//
// ⚠️ A record opened from a link, from Cmd-K or from a related list has no
// list behind it. The page passes null, and then the keys do nothing.

import { isTypingTarget } from "@/lib/keyboard";

/**
 * The id one step from `id` in `ids`. Null at either end, and for an id that
 * the list does not hold, so the key does nothing there.
 */
export function neighbour(ids: readonly string[], id: string, step: 1 | -1): string | null {
  const at = ids.indexOf(id);
  if (at < 0) return null;
  return ids[at + step] ?? null;
}

/** "3 of 50", or null for an id that the list does not hold. */
export function navLabel(ids: readonly string[], id: string): string | null {
  const at = ids.indexOf(id);
  return at < 0 ? null : `${at + 1} of ${ids.length}`;
}

/** The parts of a `KeyboardEvent` that the step reads. */
export type NavKeyEvent = {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  /** The element that has focus. A DOM event passes its `EventTarget`. */
  target: unknown;
};

/**
 * The step a key asks for: `j` is the next record and `k` the one before.
 *
 * ⚠️ No modifier may be down. Cmd-K and Ctrl+K belong to the shell's command
 * bar (AGENTS.md rule 10), and `src/lib/shell/seams.test.ts` fails on an app
 * handler that reads `k` beside a modifier that is not negated. A key typed
 * into a field is text, never a step.
 */
export function navStep(e: NavKeyEvent): 1 | -1 | null {
  const typing = isTypingTarget(e.target as Parameters<typeof isTypingTarget>[0]);
  if (!e.metaKey && !e.ctrlKey && !e.altKey && !typing) {
    if (e.key === "j") return 1;
    if (e.key === "k") return -1;
  }
  return null;
}
