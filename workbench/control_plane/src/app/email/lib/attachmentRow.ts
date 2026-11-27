/**
 * The closed file list of a message: one row of chips (owner, 2026-11-06).
 *
 * The files draw above the body, as in Outlook. A list that wrapped to a
 * second row pushed the body down when it arrived after the body, by 66 px or
 * more (UI review of task 1830). So the closed list keeps to one row: the
 * chips that fit, then a "+N more" chip that opens the rest. Its height is the
 * height of one chip, and so is the height of the shape that holds its place.
 *
 * Every chip has one width, so the count is arithmetic and needs no measure of
 * each chip. `AttachmentList.tsx` draws the chips at these widths, and
 * `attachmentRow.test.ts` holds the two in step.
 */

/** The width of one file chip, in px. */
export const CHIP_PX = 220;
/** The gap between two chips, in px. */
export const GAP_PX = 8;
/** The width of the "+N more" chip, in px. */
export const MORE_PX = 72;

/**
 * How many file chips the closed list draws in a row `width` px wide. When
 * some do not fit, room stays for the "+N more" chip. Always at least one
 * chip. A row not measured yet (`width` 0) draws them all, and clips the rest.
 */
export function chipsInRow(width: number, total: number): number {
  if (total <= 0) return 0;
  if (width <= 0) return total;
  if (total * CHIP_PX + (total - 1) * GAP_PX <= width) return total;
  // n chips, n gaps and the more chip: n * (CHIP_PX + GAP_PX) + MORE_PX.
  const room = Math.floor((width - MORE_PX) / (CHIP_PX + GAP_PX));
  return Math.max(1, Math.min(total - 1, room));
}

/**
 * The list row stands in for a mail whose detail fetch threw. If the row
 * advertises files and holds none, its files did not load, so the slot says
 * so and offers Try again. This reads the thrown fetch, never an empty list.
 */
export function withFailedFiles<T extends { hasAttachments: boolean; attachments?: unknown[] }>(
  row: T,
): T & { attachmentsStatus?: "ok" | "failed" } {
  return row.hasAttachments && (!row.attachments || row.attachments.length === 0)
    ? { ...row, attachmentsStatus: "failed" }
    : row;
}
