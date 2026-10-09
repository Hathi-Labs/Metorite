"use client";

/**
 * The reader of `/email?email=<id>`: it opens the email the link names.
 *
 * Two triggers, because one is not enough:
 *
 * - **The query.** `useSearchParams` runs this again when the link changes,
 *   so a fresh visit, and a first link pushed while the page is open, open
 *   their email (owner report, 2026-10-09).
 * - **Each click on a chat link** (`IN_APP_LINK_EVENT`, `lib/inAppLink.ts`).
 *   A second click on the SAME link pushes the URL the page already shows,
 *   and the query does not change, so the query alone opened nothing on the
 *   second click (review round 1, P2-b).
 *
 * An open always sends `cc-email-open`, so the page leaves a full-screen
 * scene (the chat) and shows the mail. It skips only the fetch when the mail
 * is already selected: the mail scene still comes forward. The chat card's
 * "Open in inbox" opens the mail first and then pushes the link
 * (`EmailToolCards.tsx`), so the query trigger skips an id it handled.
 *
 * `page.tsx` mounts it inside `<Suspense>`, the boundary `useSearchParams`
 * needs, so the rest of the page still renders on the server.
 * Fences: `lib/emailLink.test.ts` and `e2e/email-deep-link.spec.ts`.
 */

import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

import { IN_APP_LINK_EVENT, announcedHref } from "@/lib/inAppLink";

import { emailIdFromSearch } from "../lib/emailLink";
import { useEmailStore } from "../lib/emailStore";

export function EmailDeepLink({ ready }: { ready: boolean }) {
  const params = useSearchParams();
  const id = emailIdFromSearch(params);
  const handled = useRef<string | null>(null);
  const readyRef = useRef(ready);
  useEffect(() => {
    readyRef.current = ready;
  }, [ready]);

  const open = useCallback((mail: string) => {
    handled.current = mail;
    if (useEmailStore.getState().selectedEmailId !== mail) {
      void useEmailStore.getState().openEmailById(mail);
    }
    window.dispatchEvent(new CustomEvent("cc-email-open", { detail: mail }));
  }, []);

  // The query: a fresh visit, or a new link pushed while the page is open.
  useEffect(() => {
    if (!ready || !id || handled.current === id) return;
    open(id);
  }, [ready, id, open]);

  // Each click on a chat link, the same link again included.
  useEffect(() => {
    const onLink = (e: Event) => {
      const href = announcedHref(e);
      if (!href || !readyRef.current) return;
      let url: URL;
      try {
        url = new URL(href, window.location.origin);
      } catch {
        return;
      }
      if (url.pathname !== "/email") return;
      const mail = emailIdFromSearch(url.searchParams);
      if (mail) open(mail);
    };
    window.addEventListener(IN_APP_LINK_EVENT, onLink);
    return () => window.removeEventListener(IN_APP_LINK_EVENT, onLink);
  }, [open]);

  return null;
}
