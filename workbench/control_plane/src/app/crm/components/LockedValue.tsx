"use client";

/**
 * The value of a field that Zoho owns (CRM-U15, spec section 20.19 part A).
 *
 * Mirror mode says once, in the strip under the app bar, that the records are
 * a read-only copy. So a row no longer carries a lock icon. A click on the
 * value shows one short line on that field: `LOCK_TIP`. The value stays
 * readable, and no edit control opens.
 */

import { useId, useState, type ReactNode } from "react";

import { LOCK_TIP } from "../lib/connect";

export default function LockedValue({ children }: { children: ReactNode }) {
  const [asked, setAsked] = useState(false);
  const tipId = useId();
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5 text-xs text-foreground">
      {/* Not a button: it starts no action. A click or Enter only shows the line. */}
      <span
        tabIndex={0}
        title={LOCK_TIP}
        aria-describedby={tipId}
        onClick={() => setAsked(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            // Space must not scroll the page.
            e.preventDefault();
            setAsked(true);
          }
        }}
        className="w-full cursor-default break-words rounded-sm text-left"
      >
        {children}
      </span>
      {/* The tip is in the page from the start for a screen reader. It shows
          on screen after a click or a key. */}
      <span
        id={tipId}
        role={asked ? "status" : undefined}
        className={asked ? "text-[11px] text-muted-foreground" : "sr-only"}
      >
        {LOCK_TIP}
      </span>
    </span>
  );
}
