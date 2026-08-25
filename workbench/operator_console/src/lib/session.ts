// SERVER-ONLY page-side staff gate. Route handlers use `gateStaff`; server
// components use this to decide between rendering and redirecting to /login.

import { cookies } from "next/headers";
import { isStaff, STAFF_COOKIE, StaffUnconfigured } from "./staff";

export async function staffSession(): Promise<{
  ok: boolean;
  configured: boolean;
}> {
  const jar = await cookies();
  try {
    return { ok: isStaff(jar.get(STAFF_COOKIE)?.value ?? null), configured: true };
  } catch (e) {
    if (e instanceof StaffUnconfigured) return { ok: false, configured: false };
    throw e;
  }
}
