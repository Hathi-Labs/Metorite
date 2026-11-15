/**
 * The cue for a wide layout that scrolls sideways (STU-LY-4, capture finding of task 3317). Pure: it reads
 * three numbers. A board with more lanes than the page is wide hides the last lane past the edge, so the
 * layout says so and offers a step to the right.
 */

/** Is there more to see to the right of what shows now? A rounding gap of one pixel does not count. */
export function hasMoreRight(m: { scrollWidth: number; clientWidth: number; scrollLeft: number }): boolean {
  return m.scrollWidth - m.clientWidth - m.scrollLeft > 1;
}

/** The step of one press: four fifths of the view, so the lane at the edge stays in sight. */
export function stepRight(clientWidth: number): number {
  return Math.max(1, Math.round(clientWidth * 0.8));
}
