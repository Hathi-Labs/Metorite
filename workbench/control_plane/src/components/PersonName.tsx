"use client";

/**
 * A person's name, with their full name and address on hover.
 *
 * Owner, 2026-10-10: *"we should be using their names only, not their email
 * IDs. On hovering on their name, we show more information about them with
 * their full name and email ID."*
 *
 * The label is `personDisplayName` (`lib/personName.ts`): the real name, else
 * the left half of the address. The tip shows the full name and the address,
 * or says there is no name on file. It is the shared tip (`useOverflowTip`
 * with `always`), so it waits, hides and reads exactly as every other tip in
 * the product does.
 */

import Icon from "@/components/Icon";
import { useOverflowTip } from "@/components/ui/OverflowTip";
import { personDisplayName, realName } from "@/lib/personName";

export interface PersonNameProps {
  /** The member's address, or an `agent:<name>` assignee. */
  email: string;
  /** What the directory holds, if anything. An address here is ignored. */
  name?: string | null;
  /** Layout and type for the label. `truncate` is already on it. */
  className?: string;
}

export function PersonName({ email, name, className = "" }: PersonNameProps) {
  const agent = email.startsWith("agent:");
  const real = agent ? null : realName(name);
  const label = agent ? email.slice("agent:".length) || email : personDisplayName(name, email);
  const content = agent ? (
    <span className="flex items-center gap-1">
      <Icon name="Bot" className="h-3 w-3 shrink-0" />
      An agent, {label}
    </span>
  ) : (
    <span className="flex flex-col">
      <span className="font-medium">{real ?? "No name on file"}</span>
      <span className="opacity-80">{email}</span>
    </span>
  );
  const { attachLabel, onPointerEnter, onPointerLeave, onFocus, onBlur, tip } = useOverflowTip(
    label,
    { always: true, content },
  );
  return (
    <>
      <span
        ref={attachLabel}
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
        onFocus={onFocus}
        onBlur={onBlur}
        // Reachable by keyboard, so the tip is not a pointer-only answer. The
        // tip is not described-by (as in `OverflowTip`), so the label says
        // everything the tip says, for a screen reader.
        tabIndex={0}
        aria-label={agent ? `An agent, ${label}` : `${real ?? label}, ${email}`}
        className={`truncate rounded-sm outline-none focus-visible:ring-1 focus-visible:ring-primary/50 ${className}`}
      >
        {label}
      </span>
      {tip}
    </>
  );
}

export default PersonName;
