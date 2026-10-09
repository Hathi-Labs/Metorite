/**
 * NavBadge — the count on a nav entry: the desktop sidebar, the phone drawer
 * and the phone's bottom bar draw this one component.
 *
 * Two tones, two meanings:
 *   - `success`: assistants running on this app (WS-51 S1). It pulses, and
 *     only when the member allows motion (`motion-safe:`).
 *   - `warning`: something waits for the member. Today that is the agent
 *     updates on /agents. `chat_run_continuity.md` S3 reserves it for "needs
 *     your answer", so a running count never takes it.
 *
 * The count is not colour alone: `label` is its spoken name ("2 assistants
 * running"), on `role="img"` so a screen reader reads it inside the link.
 *
 * Fence (R7): `navBadge.test.ts`.
 */

import { badgeText } from "@/lib/runActivity";

export type NavBadgeTone = "success" | "warning";

const TONE: Record<NavBadgeTone, string> = {
  success: "bg-success text-success-foreground motion-safe:animate-pulse",
  warning: "bg-warning text-warning-foreground",
};

/** `corner` sits on an icon. `inline` ends a row of text. */
export type NavBadgePlacement = "corner" | "inline";

const PLACE: Record<NavBadgePlacement, string> = {
  corner: "absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1",
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
  /** Position only. Never a colour. */
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
