"use client";

/**
 * The reader of `/email?email=<id>`: it opens the email the link names.
 *
 * It reads the query through `useSearchParams`, so it runs again when the
 * link changes while the page is open. That is the case of a link in the
 * email chat, which navigates inside `/email` (owner report, 2026-10-09).
 * The reader it replaces ran once per mailbox and read
 * `window.location`, so a second link in the same visit opened nothing.
 *
 * It waits for a mailbox (`ready`), because `openEmailById` moves to the
 * mailbox of the mail from the one in view. It skips an id that is already
 * open: the chat card's "Open in inbox" opens the mail first and then pushes
 * the link (`EmailToolCards.tsx`). It sends `cc-email-open`, so the page
 * leaves a full-screen scene (the chat) and shows the mail.
 *
 * `page.tsx` mounts it inside `<Suspense>`, the boundary `useSearchParams`
 * needs, so the rest of the page still renders on the server.
 * Fence: `lib/emailLink.test.ts`.
 */

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

import { emailIdFromSearch } from "../lib/emailLink";
import { useEmailStore } from "../lib/emailStore";

export function EmailDeepLink({ ready }: { ready: boolean }) {
  const params = useSearchParams();
  const id = emailIdFromSearch(params);
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!ready || !id || handled.current === id) return;
    handled.current = id;
    if (useEmailStore.getState().selectedEmailId === id) return;
    void useEmailStore.getState().openEmailById(id);
    window.dispatchEvent(new CustomEvent("cc-email-open", { detail: id }));
  }, [ready, id]);

  return null;
}
