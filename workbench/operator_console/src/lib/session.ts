// SERVER-ONLY page-side gate. Route handlers use `gate`; server components
// use this to decide between rendering and redirecting to /login.
//
// ⚠️ **This decides what to RENDER, never what a caller may DO.** Every write
// goes through a BFF route, which gates again, and the Console gates a third
// time against the §5 matrix. A page that rendered a button somebody may not
// press is a cosmetic bug. A page that let them press it would be a hole, and
// that is why the answer is never taken from here alone.

import { cookies } from "next/headers";
import { isStaff, STAFF_COOKIE, StaffUnconfigured } from "./staff";
import { SESSION_COOKIE, looksLikeSession, usesSessions } from "./identity";

export type StaffSession = {
  //: Render the page, or redirect to /login.
  ok: boolean;
  //: False means the box has no gate configured at all. The caller shows a
  //: 503-shaped message rather than a sign-in form nobody can complete.
  configured: boolean;
  //: The caller's own token, on the session path only. Server components pass
  //: it to `console.ts` so a page read is attributed to the person too.
  authToken?: string;
};

export async function staffSession(): Promise<StaffSession> {
  const jar = await cookies();

  if (usesSessions()) {
    const token = jar.get(SESSION_COOKIE)?.value ?? null;
    // ⚠️ `configured: true` even with no cookie. The gate IS configured — the
    // person simply has not signed in — and saying otherwise would send the
    // reader to check environment variables instead of the sign-in page.
    return looksLikeSession(token)
      ? { ok: true, configured: true, authToken: (token as string).trim() }
      : { ok: false, configured: true };
  }

  try {
    return {
      ok: isStaff(jar.get(STAFF_COOKIE)?.value ?? null),
      configured: true,
    };
  } catch (e) {
    if (e instanceof StaffUnconfigured) return { ok: false, configured: false };
    throw e;
  }
}
