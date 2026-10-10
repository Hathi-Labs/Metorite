"use client";

/**
 * The status pill — one shape for /projects and /tasks (WS-27ad).
 *
 * A status was drawn three different ways: a coloured pill with a dot on
 * /tasks' cards, a bare grey word in /projects' list and table cells, and
 * nothing at all on /projects' board. Same fact, three appearances.
 *
 * This is only the *appearance*. WHICH accent a status earns is
 * `src/lib/statusAccent.ts`, fed by each app's own facts (/projects hands over
 * the stored colour and category, /tasks the stage name and position) — a
 * shared component that knew about either app's status model would be shared in
 * name only.
 *
 * Not interactive on purpose: /tasks' pill opens a stage menu and /projects'
 * cells are read-only or drive a `<select>`. Each app wraps this in whatever
 * control it needs, and the pill itself stays one thing.
 */

import type { StatusAccent } from "@/lib/statusAccent";

export function StatusChip({
  accent,
  label,
  trailing,
  ariaLabel,
  className = "",
}: {
  accent: StatusAccent;
  label: string;
  /** A chevron, a count — whatever the wrapping control needs to show. */
  trailing?: React.ReactNode;
  /**
   * The pill's whole name, when the word alone does not say it is a status
   * ("Status: Backlog"). The pill then reads as one labelled image. The name
   * also shows on hover, because a long name truncates at 84 px.
   */
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <span
      {...(ariaLabel ? { role: "img", "aria-label": ariaLabel, title: ariaLabel } : {})}
      className={[
        "inline-flex items-center gap-1 rounded-full border border-transparent px-1.5 py-0.5 text-[10px] font-medium",
        accent.soft,
        accent.text,
        className,
      ].join(" ")}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${accent.dot}`} />
      <span className="max-w-[84px] truncate">{label}</span>
      {trailing}
    </span>
  );
}
