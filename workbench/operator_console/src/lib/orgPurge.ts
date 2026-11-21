// Who may purge an organization — the pure rule (H-313, task 523).
//
// 🔴 **The BFF must decide this before the tenant act.** The gateway door
// trusts its operator token, and this console holds that token for every
// caller. The Console checks the caller's rank on `POST /orgs/purge`, but the
// BFF calls that route AFTER it destroyed the tenant plane. Without this check
// a viewer could destroy the tenant plane of a `deleted` organization and then
// get a 403. The Console's matrix row is ADMIN with an open elevation window
// (`operator_roles.py`, `("POST", "/orgs/purge")`).
//
// ⚠️ The break-glass caller is admitted. That token bypasses the matrix on the
// Console too, so a refusal here would be a second policy for one token.
//
// Pure, with the rank table of `memberRemove.ts`: this console holds one.

import { operatorRank, type ElevationRead } from "./memberRemove";
import type { Whoami } from "./whoami";

/** Null means the caller may purge. A string is the reason for the 403. */
export function mayPurgeOrg(
  who: Whoami,
  elevation: ElevationRead | null,
): string | null {
  if (who.method === "breakglass") return null;
  if (operatorRank(who.role) < operatorRank("admin")) {
    return "purging an organization needs the admin role";
  }
  if (elevation?.required === true && !elevation.elevated) {
    return "purging an organization needs an open elevation window";
  }
  return null;
}
