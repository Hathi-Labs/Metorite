"use client";

import { useEffect } from "react";
import { useToast } from "@/components/ui/Toast";
import { NOTICE_SEEN_MS, clearSignOutNotice, peekSignOutNotice } from "@/lib/accountSwitch";

/**
 * The one notice of a sign-out that did more than the member asked
 * (review of 2026-10-12). "Sign out" in the top-right menu leaves ONE
 * account. When the browser cannot do that safely, it signs out of every
 * account instead (`signOutThis` fails closed), and the page reloads at
 * `/signin`. The flag waits in `sessionStorage`, and this shows it through
 * the one Toast channel.
 *
 * ⚠️ The flag ends when the toast closes, or after `NOTICE_SEEN_MS` on
 * screen, never on the first read. A first read that cleared it lost the
 * toast to a remount (Fast Refresh in dev, measured 2026-10-12). The same
 * key shows one toast however often this mounts.
 *
 * It draws nothing. `app/signin/SignInForm.tsx` mounts it.
 */
export default function SignOutNotice() {
  const toast = useToast();
  useEffect(() => {
    const notice = peekSignOutNotice();
    if (!notice) return;
    toast.show({ key: "signout-all", variant: "error", title: notice, onClose: clearSignOutNotice });
    const seen = window.setTimeout(clearSignOutNotice, NOTICE_SEEN_MS);
    return () => window.clearTimeout(seen);
  }, [toast]);
  return null;
}
