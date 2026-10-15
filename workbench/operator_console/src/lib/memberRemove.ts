// Remove or delete one member of a customer's organization — the pure rules.
// Owner request, 2026-10-15: "give ability for operator (backend) and admins
// to remove/delete a person who's invited/logged in."
//
// ⚠️ Pure, and safe for a client file to import. It holds no credential. The
// BFF route `api/operator/members/remove` and the roster in `Actions.tsx` both
// read it, so the two cannot disagree on a label or a rule.

import type { Whoami } from "./whoami";

/** `remove` takes access away and keeps the person's work. `purge` also
 *  deletes the person's own data. The gateway door decides what each keeps. */
export type MemberRemoveMode = "remove" | "purge";

export type RemoveRequest = {
  slug: string;
  email: string;
  mode: MemberRemoveMode;
};

/** Read and check the BFF body. The confirmation must equal the address
 *  verbatim, as the purge confirmation must equal the slug. The gateway door
 *  checks it again, so this is a protocol and not a UI nicety. */
export function readRemoveRequest(
  body: unknown,
): { ok: true; req: RemoveRequest } | { ok: false; error: string } {
  const b = (typeof body === "object" && body !== null ? body : {}) as Record<
    string,
    unknown
  >;
  const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const slug = str(b.org_slug);
  const email = str(b.email);
  const confirm = str(b.confirm);
  if (!slug) return { ok: false, error: "org_slug is required" };
  if (!email) return { ok: false, error: "email is required" };
  if (b.mode !== "remove" && b.mode !== "purge") {
    return { ok: false, error: 'mode must be "remove" or "purge"' };
  }
  if (confirm !== email) {
    return {
      ok: false,
      error: "confirm must equal the member's email address, verbatim",
    };
  }
  return { ok: true, req: { slug, email, mode: b.mode } };
}

// The Console's own ranks (`operator_roles._RANK`). An unknown role ranks
// below viewer, never above, as it does there.
const RANK: Record<string, number> = { viewer: 0, editor: 1, admin: 2 };
const rank = (role: string | null): number =>
  role !== null && Object.hasOwn(RANK, role) ? RANK[role] : -1;

/** The rank each mode needs. `remove` matches the seat release (EDITOR).
 *  `purge` deletes a person's data, so it matches the org purge (ADMIN). */
export function requiredRole(mode: MemberRemoveMode): "editor" | "admin" {
  return mode === "purge" ? "admin" : "editor";
}

export type ElevationRead = { elevated?: boolean; required?: boolean };

/**
 * May this caller reach the tenant door with this mode? Null means yes, and a
 * string is the reason for the 403.
 *
 * 🔴 **The BFF must decide this, because nobody else can in time.** The
 * gateway door trusts its operator token, and this console holds that token
 * for every caller. The Console checks the caller's rank on its own route,
 * but the BFF calls that route AFTER the tenant plane acted. Without this
 * check, a viewer could take a person's access away and only then get a 403.
 *
 * ⚠️ The break-glass caller is admitted. That token bypasses the §5 matrix on
 * the Console too, so a refusal here would be a second policy for one token.
 *
 * ⚠️ A purge also needs an open elevation window when the Console says one is
 * required (D72's flag). That is the `/orgs/purge` row of the matrix.
 */
export function mayRemove(
  who: Whoami,
  mode: MemberRemoveMode,
  elevation: ElevationRead | null,
): string | null {
  if (who.method === "breakglass") return null;
  const need = requiredRole(mode);
  if (rank(who.role) < rank(need)) {
    return mode === "purge"
      ? "deleting a member permanently needs the admin role"
      : "removing a member needs the editor role or higher";
  }
  if (mode === "purge" && elevation?.required === true && !elevation.elevated) {
    return "deleting a member permanently needs an open elevation window";
  }
  return null;
}

/** The label of the Remove button for one roster row, or null when the row
 *  takes no Remove. An invited person never signed in, so the act is to
 *  revoke the invite. A removed person has nothing left to remove, and
 *  "Delete permanently" stays for them. */
export function removeLabel(status: string): string | null {
  if (status === "removed") return null;
  if (status === "invited") return "Revoke invite";
  return "Remove";
}

/** Who refused a remove or delete, as the result line names it. The BFF
 *  route marks a gateway refusal with `refused_by: "tenant"`. Every other
 *  refusal (a Console answer, the rank check, a bad body) keeps the words the
 *  other panels use. A body that is not JSON keeps them too. */
export function refusedBy(text: string): "The tenant plane" | "The Console" {
  try {
    const body = JSON.parse(text) as { refused_by?: unknown } | null;
    if (body !== null && body.refused_by === "tenant") return "The tenant plane";
  } catch {
    // Not JSON, so not our marker.
  }
  return "The Console";
}
