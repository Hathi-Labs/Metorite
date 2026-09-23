/**
 * Projects · how much of a project is finished, as a ring.
 *
 * Owner directive, 2026-09-23: *"replace the green-circle dot with a completion
 * wheel in the projects app. Change colors only when a project or subproject's
 * status is green (i.e. it's a live project/subproject). The progress wheel
 * should be adaptive, and all other icons remain unchanged."*
 *
 * ## What "only when it is green" means here
 *
 * A LIVE project trades its dot for a wheel. Every other run state — paused,
 * stopped, whatever `PROJECT_STATES` grows next — keeps the glyph and the hue
 * it has today, untouched. That is the owner's "all other icons remain
 * unchanged", and it keeps D-PM-27 intact: hue AND glyph, never hue alone. A
 * paused project still reads as paused at a glance, because its glyph did not
 * become a ring like everything else.
 *
 * ## Why the rules are here and not in the component
 *
 * `vitest.config.ts` is `environment: "node"` and never collects a `.tsx`
 * (D-PM-21), so a rule written inside the icon would carry no fence. The
 * component draws what these return and decides nothing.
 */

/** What the tree row carries after the server's subtree roll-up. */
export interface NodeProgress {
  /** Tasks in this node's whole subtree. `0` means there is no work here. */
  tasks?: number | null;
  /** How many of those were DELIVERED — the `done` category only. */
  done?: number | null;
  /** How many were abandoned. Subtracted from the denominator, never added. */
  cancelled?: number | null;
}

/**
 * Should this row draw a wheel at all?
 *
 * **Every live project, without exception.** The owner's instruction was to
 * replace the green dot for a live project, full stop.
 *
 * ⚠️ **An earlier version of this carried a rule the owner did not ask for**:
 * a live project with no tasks kept its plain dot, on the argument that an
 * empty ring claims "none of this is done" about work that does not exist.
 * The owner looked at a real sidebar afterwards and said *"the icons still
 * look the same. I don't see any completion ring next to the projects."* A
 * conditional the reader cannot see is indistinguishable from a feature that
 * did not ship — and 0 of 0 drawing as an empty ring is a fair reading of
 * "nothing done here yet" anyway. Owner call, 2026-09-23.
 */
export function showsWheel(state: string, _progress: NodeProgress): boolean {
  return isLive(state);
}

/**
 * Only the live state gets the new treatment.
 *
 * Kept as its own predicate rather than inlined, because "which state is the
 * green one" is the single fact this whole feature turns on, and it is the one
 * a later state rename would silently break.
 */
export function isLive(state: string): boolean {
  return state === "active";
}

/**
 * How much of the CLOSABLE work is delivered, 0 to 1.
 *
 * ⚠️ **Cancelled work leaves the denominator, and never joins the numerator.**
 * `core.py` puts it plainly: a team that cancelled forty tasks did not finish
 * forty tasks, and *"a metric that adds them makes cancellation the cheapest
 * way to improve itself"*.
 *
 * ⚠️ **This is also the rule `NodeDashboard` already prints**, in
 * `CompletionFigure` and in `ProgressCard`, and the tree sits BESIDE that
 * dashboard on one screen. A first version of this ring counted every closing
 * category as done, so a project with four done, four cancelled and two open
 * drew a ring at 80% next to a figure reading 67% — and a project whose every
 * open task was cancelled drew a FULL ring beside a dashboard reading 0%. Two
 * answers to one question, a thumb apart. Both dashboard sites now call this,
 * so there is one rule rather than three copies of it.
 *
 * Clamped at both ends. If a roll-up ever disagrees with itself the ring must
 * not sweep past a full circle and start again — a wheel that reads 110% as
 * 10% is worse than one that reads it as full.
 */
export function completion(progress: NodeProgress): number {
  const closable = (progress.tasks ?? 0) - (progress.cancelled ?? 0);
  const done = progress.done ?? 0;
  if (closable <= 0) return 0;
  return Math.max(0, Math.min(1, done / closable));
}

/**
 * The denominator — the work that can still be delivered.
 *
 * Exported because every caller that prints the percent also wants to print
 * "N of M", and M is the surprising half. Never negative.
 */
export function closableCount(progress: NodeProgress): number {
  return Math.max(0, (progress.tasks ?? 0) - (progress.cancelled ?? 0));
}

/** Whole percent, for the label a reader hovers to see. */
export function completionPercent(progress: NodeProgress): number {
  return Math.round(completion(progress) * 100);
}

/**
 * The `stroke-dasharray` pair for a ring of radius `r` at this completion.
 *
 * SVG draws a circle's stroke from 3 o'clock, so the component rotates the
 * whole ring -90°; this only has to produce "how much of the circumference is
 * painted, and how much is not".
 */
export function ringDash(progress: NodeProgress, radius: number): string {
  const circumference = 2 * Math.PI * radius;
  const painted = circumference * completion(progress);
  return `${painted} ${circumference - painted}`;
}

/**
 * What the row says when a reader hovers or a screen reader reaches it.
 *
 * Names the counts as well as the percent. "60%" alone is the number people
 * misremember — 3 of 5 and 600 of 1000 are the same percent and not the same
 * situation.
 *
 * ⚠️ Says so when cancelled work was excluded. `CompletionFigure`'s tooltip
 * reaches the same conclusion: the denominator is the surprising half, and
 * cancelling the last open task jumping the ring to 100% is correct and
 * baffling unless the label says why.
 */
export function wheelLabel(
  level: string, name: string, progress: NodeProgress,
): string {
  const done = progress.done ?? 0;
  const closable = closableCount(progress);
  const cancelled = progress.cancelled ?? 0;
  const note = cancelled > 0 ? ` ${cancelled} cancelled, not counted.` : "";
  return (
    `${level}, active — ${name}. ` +
    `${completionPercent(progress)}% done, ${done} of ${closable} tasks.${note}`
  );
}
