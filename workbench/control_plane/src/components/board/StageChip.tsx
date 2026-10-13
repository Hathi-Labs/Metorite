/**
 * The stage chip — the one place a board lane shows its colour (WS-53 CRM-C2,
 * `crm_platform.md` §20.5).
 *
 * The owner did not like how the colour cap of a column met its round corner.
 * A `h-1 w-full` bar under `overflow-hidden rounded-lg` touches the curve, and
 * the clip leaves a sliver at each end. No radius fixes that, because a cap
 * must touch the edge.
 *
 * So the colour moved into this chip. The chip sits inside the column's own
 * padding and never touches the column edge, so no corner clips it. Its radius
 * is `rounded-md`, one step under the column's `rounded-lg`, so the two curves
 * nest. Colour stays in one small area of each lane, and the column frame
 * stays neutral.
 *
 * The fill and the ink are the hue's `chip` slot. For a coloured hue that is
 * its 10 % tint and its text tone, which `contrast.test.ts` measures in both
 * modes. For gray it is `bg-secondary text-muted-foreground`, because the gray
 * `soft` fill is too faint to read as a chip.
 *
 * Colour is never alone (`DESIGN_SYSTEM.md` §7). The name is always text, and a
 * lane that has an icon (a won, a lost or an on-hold CRM stage) shows the icon
 * in the place of the dot.
 *
 * `BoardColumn` draws it in each column header, and the Projects swimlane
 * header row draws it once above the lanes. Both read this file.
 */

import Icon from "@/components/Icon";
import type { StatusAccent } from "@/lib/statusAccent";

/** What a lane header says. The hue comes from `statusAccent()` only. */
export interface StageHeader {
  /** The lane's hue, as `statusAccent()` or `accentForHue()` returns it. */
  accent: StatusAccent;
  /** The lane name. Always text, so the colour is never the only signal. */
  title: string;
  /** The cards in the lane. Omit it to draw no count. */
  count?: number | null;
  /** A Lucide name, drawn in the place of the dot. */
  icon?: string | null;
  /** A tooltip for the count, for example how many cards are subtasks. */
  countTitle?: string;
}

export function StageChip({
  accent,
  title,
  count,
  icon,
  countTitle,
  className = "",
}: StageHeader & { className?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ${accent.chip} ${className}`}
    >
      {icon ? (
        <Icon name={icon} size={12} className="shrink-0" aria-hidden />
      ) : (
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
      )}
      <span className="truncate">{title}</span>
      {count === undefined || count === null ? null : (
        <span className="shrink-0 tabular-nums" title={countTitle}>
          {count}
        </span>
      )}
    </span>
  );
}
