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
import {
  SESSION_COOKIE,
  looksLikeSession,
  passphraseFallbackEnabled,
  usesSessions,
} from "./identity";

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
    if (looksLikeSession(token)) {
      return { ok: true, configured: true, authToken: (token as string).trim() };
    }
    // ⛔ **The back door — CP-12k.** OFF by default, so done-when 29 still
    // holds. When the owner turns it on, a valid passphrase cookie renders
    // the page and carries NO `authToken`, exactly as the interim path does.
    // That difference matters: a passphrase names nobody, so the Console must
    // fall back to the shared operator token rather than attribute the read
    // to a person who did not prove they are one.
    if (passphraseFallbackEnabled()) {
      try {
        if (isStaff(jar.get(STAFF_COOKIE)?.value ?? null)) {
          return { ok: true, configured: true };
        }
      } catch (e) {
        // An unconfigured passphrase is not an error HERE — the identity door
        // is the real one, and this is only a backup that is not set up.
        if (!(e instanceof StaffUnconfigured)) throw e;
      }
    }
    return { ok: false, configured: true };
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
