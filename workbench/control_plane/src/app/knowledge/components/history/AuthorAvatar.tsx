"use client";

/**
 * The initials of a version's author, on a hue of the categorical ramp
 * (WS-61 KB-2g). The hue comes from the email, so one author has one hue in
 * each row (`src/lib/categorical.ts`). It is a mark, not a control.
 */

import { categoricalAccent } from "@/lib/categorical";
import { initials } from "@/lib/taskCard";

import { authorName } from "../../lib/history";

export default function AuthorAvatar({ email }: { email: string }) {
  const accent = categoricalAccent(email || "?");
  return (
    <span
      aria-hidden="true"
      className={`inline-flex h-5 w-5 shrink-0 select-none items-center justify-center rounded-full border text-[10px] font-semibold ${accent.chip}`}
    >
      {initials(authorName(email)) || "?"}
    </span>
  );
}
