"use client";

/**
 * Projects · the assignee chips — who owns a task, as a row of badges.
 *
 * ONE component for the two surfaces that draw it (WS-39 S6e, §4.8): the
 * task panel's Assignees cell and the move dialog's promote step. S6c copied
 * the panel's block into the dialog, and a copied block is the failure this
 * file exists to end — the next change to how an unknown address is flagged
 * would have landed in one of the two.
 *
 * The picker beside the chips stays the caller's: the panel saves on every
 * pick, the dialog collects and sends with the move, and that is an
 * interaction difference, not a look.
 */

import Badge from "@/components/ui/Badge";
import Icon from "@/components/Icon";

import { classify } from "../lib/assignees";
import { labelWith } from "../lib/grouping";

export function AssigneeChips({
  assignees,
  disabled = false,
  personLabels,
  onRemove,
  emptyClass = "text-xs text-muted-foreground",
}: {
  assignees: readonly string[];
  disabled?: boolean;
  /** Directory names for addresses. Absent, the local part of the email. */
  personLabels?: ReadonlyMap<string, string>;
  onRemove: (who: string) => void;
  /** The "Nobody yet" line's class — the dialog inherits its row's size. */
  emptyClass?: string;
}) {
  const label = labelWith(personLabels);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {assignees.map((who) => {
        const kind = classify(who);
        return (
          <Badge
            key={who}
            // An address that is neither an email nor `agent:<name>` is a
            // typo somebody has to see, so it takes the warning tone rather
            // than a quieter outline.
            tone={kind === "unknown" ? "warning" : "neutral"}
            // Agents and people are one vocabulary (D-PM-4), so the
            // difference is an icon, never a separate field.
            icon={kind === "agent" ? "Bot" : undefined}
            title={kind === "unknown" ? "Not an email or agent:<name>" : who}
          >
            {label(who)}
            <button
              type="button"
              disabled={disabled}
              aria-label={`Unassign ${who}`}
              onClick={() => onRemove(who)}
              className="opacity-70 hover:opacity-100"
            >
              <Icon name="X" className="h-3 w-3" />
            </button>
          </Badge>
        );
      })}
      {assignees.length === 0 ? <span className={emptyClass}>Nobody yet</span> : null}
    </div>
  );
}

export default AssigneeChips;
