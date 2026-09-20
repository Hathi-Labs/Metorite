/**
 * People Center · the page frame. ONE width for the whole app.
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.0 ·
 * `workbench/control_plane/DESIGN_SYSTEM.md` (one product, one look).
 *
 * **The defect this closes.** The app's seven surfaces declared FOUR
 * different content widths — `max-w-3xl` on Find skills, the working week,
 * data quality and the org chart, `max-w-4xl` on My profile and the summary,
 * `max-w-6xl` on Workload. Measured 2026-09-20 by rendering them: the left
 * edge of the heading moved on four of the six tab clicks. Nothing was
 * broken, and the app did not read as one app.
 *
 * `AGENTS.md` rule 1 says every app is a projection of one product, and
 * CLAUDE.md §4 records that the conformance suite checks eight regexes and
 * **nothing tests layout or cross-app continuity** — so the gate is to look
 * at a surface and at its neighbour. This is that look, turned into a
 * constant.
 *
 * ⚠️ **`max-w-5xl`, and the two ends both lost something.** Workload gives up
 * 6xl, which it used for a row of seven figures — they still fit, and a table
 * wider than every sibling is what made the app feel like three apps. The
 * 3xl pages gain width they do not need for a form; where a measure matters,
 * the CONTENT constrains itself (`max-w-prose` on a paragraph), which is the
 * right level for that decision and travels with the paragraph.
 *
 * 📌 **The directory is the one exception, and it is a different kind of
 * page**: a full-height two-pane layout with its own scroll region, not a
 * document that flows. It is `flex h-full min-h-0` and takes no frame. The
 * fence below knows about it by name.
 */

/** The frame for a flowing People page. Apply to the outermost element. */
export const PAGE_FRAME = "mx-auto flex w-full max-w-5xl flex-col gap-4 p-4";

/**
 * The same frame without `flex`, for a page that lays its own children out.
 *
 * Kept as a second constant rather than letting callers hand-append classes:
 * the moment a page writes its own `max-w-` the drift starts again, and a
 * grep fence can see a missing constant but not a widened one.
 */
export const PAGE_FRAME_BLOCK = "mx-auto w-full max-w-5xl p-4";
