"use client";

/**
 * The value of a field that Zoho owns (CRM-U15, spec section 20.19 part A).
 *
 * Mirror mode says once, in the strip under the app bar, that the records are
 * a read-only copy. So a row no longer carries a lock icon. A click on the
 * value shows one short line on that field: `LOCK_TIP`. The value stays
 * readable, and no edit control opens.
 */

import { useState, type ReactNode } from "react";

import { LOCK_TIP } from "../lib/connect";

export default function LockedValue({ children }: { children: ReactNode }) {
  const [asked, setAsked] = useState(false);
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5 text-xs text-foreground">
      {/* Not a button: it starts no action. A click or Enter only shows the line. */}
      <span
        tabIndex={0}
        title={LOCK_TIP}
        onClick={() => setAsked(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setAsked(true);
        }}
        className="w-full cursor-default break-words rounded-sm text-left"
      >
        {children}
      </span>
      {asked && (
        <span role="status" className="text-[11px] text-muted-foreground">
          {LOCK_TIP}
        </span>
      )}
    </span>
  );
}
