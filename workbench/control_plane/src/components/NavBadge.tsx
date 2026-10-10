/**
 * NavBadge — the count on a nav entry: the desktop sidebar, the phone drawer
 * and the phone's bottom bar draw this one component.
 *
 * Three tones, three meanings:
 *   - `success`: assistants running on this app (WS-51 S1). It pulses, and
 *     only when the member allows motion (`motion-safe:`).
 *   - `warning`: something waits for the member. On a run badge that is an
 *     assistant that needs the member's answer (WS-51 S2): it wins over the
 *     green count on the same pane (`runBadge` in `lib/runActivity.ts`), and
 *     it does not pulse. /agents keeps its updates count in it too. A plain
 *     running count never takes it.
 *   - `info`: a reply that the member has not read (WS-51 S5). It sits between
 *     the two: amber wins over it, and it wins over green. It does not pulse,
 *     because nothing runs.
 *
 * The count is not colour alone: `label` is its spoken name ("2 assistants
 * running", "1 assistant needs your answer"), on `role="img"` so a screen
 * reader reads it inside the link.
 *
 * Fence (R7): `navBadge.test.ts`.
 */

import { badgeText } from "@/lib/runActivity";

export type NavBadgeTone = "success" | "warning" | "info";

const TONE: Record<NavBadgeTone, string> = {
  success: "bg-success text-success-foreground motion-safe:animate-pulse",
  warning: "bg-warning text-warning-foreground",
  info: "bg-info text-info-foreground",
};

/**
 * `corner` sits on a sidebar icon. `tab` sits on a bottom-bar icon, which is
 * wrapped in its own `relative` box: a tab is `flex-1`, so a badge placed on
 * the tab drifts to the edge of the screen. `inline` ends a row of text.
 */
export type NavBadgePlacement = "corner" | "tab" | "inline";

const PLACE: Record<NavBadgePlacement, string> = {
  corner: "absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1",
  tab: "absolute -top-1.5 -right-3 h-4 min-w-4 px-1",
  inline: "ml-auto h-4 min-w-4 px-1.5",
};

export default function NavBadge({
  count,
  tone,
  label,
  placement = "inline",
  className = "",
}: {
  count: number;
  tone: NavBadgeTone;
  /** The spoken name of the count, e.g. "2 assistants running". */
  label: string;
  placement?: NavBadgePlacement;
  /** Layout only, never a colour or a position that `placement` sets. */
  className?: string;
}) {
  if (!(count > 0)) return null;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-nav-badge={tone}
      className={`flex items-center justify-center rounded-full text-[10px] font-bold leading-none ${PLACE[placement]} ${TONE[tone]} ${className}`}
    >
      {badgeText(count)}
    </span>
  );
}
