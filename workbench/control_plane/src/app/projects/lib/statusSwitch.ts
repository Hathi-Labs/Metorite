/**
 * Projects · reading a status-set switch before it happens.
 *
 * `StatusSetControl` renders the mapping card. This module answers the one
 * question the card's table cannot: **which lanes are about to become the same
 * lane.**
 *
 * ⚠️ **A merge is one-way, and every row of the table is individually true.**
 * Two rows both pointing at "Waiting" are two correct sentences, and nothing in
 * a per-row reading says the two lanes stop being two. It matters because the
 * switch back cannot undo it — the lanes return from the dormant set, the tasks
 * stay merged, because the only thing that could separate them was the lane
 * they used to be in.
 *
 * Measured against the running gateway, 2026-09-16: a set carrying "Next up"
 * and "Parked" (both `todo`) switched to a parent holding one `todo` lane. Both
 * landed there. Switching back sent both to "Parked".
 */

/** The shape this reads — the fields of `StatusMove` it actually needs. */
export interface MergeCandidate {
  status_id: string;
  name: string;
}

/** `[target lane name, the source lanes landing in it]`, two or more sources. */
export type Merge = [string, string[]];

/**
 * Which target lanes receive more than one source lane.
 *
 * ⚠️ Reads `choices` — what the human has PICKED — and never the automatic
 * suggestion. Resolving a merge by hand must make the warning go away, and
 * creating one by hand must make it appear. A warning computed from the
 * suggestions would do neither, and would be stale the moment anybody touched
 * a row.
 *
 * A row with no choice yet is skipped rather than grouped under "", which
 * would report every unanswered row as merging with every other one.
 */
export function findMerges(
  questions: readonly MergeCandidate[],
  choices: Readonly<Record<string, string>>
): Merge[] {
  const byTarget = new Map<string, string[]>();
  for (const q of questions) {
    const target = choices[q.status_id];
    if (!target) continue;
    byTarget.set(target, [...(byTarget.get(target) ?? []), q.name]);
  }
  return [...byTarget.entries()].filter(([, from]) => from.length > 1);
}

/** The sentence the card shows. Empty when nothing merges. */
export function mergeWarning(merges: readonly Merge[]): string {
  if (merges.length === 0) return "";
  const said = merges
    .map(([into, from]) => `${from.join(" and ")} both become ${into}.`)
    .join(" ");
  return `${said} Switching back will not separate them again.`;
}

/**
 * The exceptions to "everything under it inherits these lanes".
 *
 * ⚠️ **Written as a sentence, not a count.** "3 projects use their own
 * statuses" tells a reader that they have a problem and not where it is —
 * they would still open three dialogs to find out, which is the exact cost
 * this exists to remove. Names are the useful half, so names come first.
 *
 * Past three it truncates, because a space with nine breakaways has a policy
 * question rather than a list to read, and a paragraph inside a one-line note
 * would push the lanes off the screen.
 */
export function overrideNote(names: readonly string[]): string {
  if (names.length === 0) return "";
  const shown = names.slice(0, 3);
  const rest = names.length - shown.length;
  let subject: string;
  if (rest > 0) {
    subject = `${shown.join(", ")} and ${rest} other${rest === 1 ? "" : "s"}`;
  } else if (shown.length === 1) {
    subject = shown[0];
  } else {
    subject = `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
  }
  // ⚠️ The verb AND the pronoun both agree, and the first cut got the pronoun
  // wrong: "Mobile App keeps their own, so they will not change" reads as a
  // mistake in a note whose whole job is to be trusted. A project is an it.
  const one = names.length === 1;
  return one
    ? `${subject} keeps its own, so it will not change.`
    : `${subject} keep their own, so they will not change.`;
}
