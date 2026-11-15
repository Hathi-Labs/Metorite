/**
 * The step that the inspector of the workflow editor shows (row E7e).
 *
 * React Flow reports its selection through `onSelectionChange`. It sends a
 * report when it mounts, and each time the set of selected ids changes. It
 * also sends one each time the handler changes. Before E7e the editor passed
 * an inline arrow, a new function on each render. A tap on the palette
 * selected the new step, the page drew again, and the repeated report (no
 * step selected on the canvas) cleared it. The inspector stayed empty.
 *
 * The editor now passes a stable handler (`useCallback`), and a new step is
 * selected on the canvas too, so the canvas and the inspector agree.
 *
 * This file holds the rule and no React, so a test holds it
 * (`editorSelection.test.ts`).
 */

/**
 * The step to show after a report.
 *
 * - `current`: the step that the inspector shows now.
 * - `before`: the ids that the canvas had selected before this report.
 * - `after`: the ids that the canvas has selected now.
 *
 * One selected step shows that step. Two or more show none. An empty report
 * clears the step only when the canvas had a selection before, so the member
 * cleared it (a click on the pane or on a wire, a delete). An empty report
 * with nothing selected before keeps the step. That is the report of a canvas
 * that mounts, such as the Canvas tab of the phone layout.
 */
export function selectionAfterChange(
  current: string | null,
  before: readonly string[],
  after: readonly string[],
): string | null {
  if (after.length === 1) return after[0];
  if (after.length > 1) return null;
  return before.length > 0 ? null : current;
}
