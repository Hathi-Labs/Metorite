// Remove or delete one member of a customer's organization — the pure rules.
// Owner request, 2026-10-15: "give ability for operator (backend) and admins
// to remove/delete a person who's invited/logged in."
//
// ⚠️ Pure, and safe for a client file to import. It holds no credential. The
// BFF route `api/operator/members/remove` and the roster in `Actions.tsx` both
// read it, so the two cannot disagree on a label or a rule.

import type { MemberRow } from "./format";
import type { Whoami } from "./whoami";

/** `remove` takes access away and keeps the person's work. `purge` also
 *  deletes the person's own data. The gateway door decides what each keeps. */
export type MemberRemoveMode = "remove" | "purge";

export type RemoveRequest = {
  slug: string;
  email: string;
  mode: MemberRemoveMode;
  /** Who becomes the billing owner when the target is the only one. */
  newOwner?: string;
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
  const newOwner = str(b.new_owner_email);
  return {
    ok: true,
    req: { slug, email, mode: b.mode, ...(newOwner ? { newOwner } : {}) },
  };
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

/** The question a remove or revoke confirm asks. It names the person and the
 *  organization, because at 390px the email column has scrolled out of view
 *  when the confirm opens (round 4). A purge names the person in its own
 *  warning, so it needs no question here. */
export function removePrompt(
  status: string,
  email: string,
  slug: string,
): string {
  if (status === "invited") {
    return (
      `Revoke the invitation for ${email} to ${slug}? They cannot accept ` +
      `it after this.`
    );
  }
  return (
    `Remove ${email} from ${slug}? They lose access at once. Their work ` +
    `stays.`
  );
}

/** The mark the BFF puts on a refusal that is its own: a bad body, the rank
 *  check, the owner hand-over, a door that is not configured. */
export const OWN_REFUSAL = "operator-console";

/** Who refused a remove or delete, as the result line names it. The BFF
 *  route marks a gateway refusal with `refused_by: "tenant"`, and its own
 *  refusals with `refused_by: OWN_REFUSAL`. Every other answer (a Console
 *  answer, a body that is not JSON) keeps the words the other panels use. */
export function refusedBy(
  text: string,
): "The tenant plane" | "The operator console" | "The Console" {
  try {
    const body = JSON.parse(text) as { refused_by?: unknown } | null;
    if (body !== null && body.refused_by === "tenant") return "The tenant plane";
    if (body !== null && body.refused_by === OWN_REFUSAL) {
      return "The operator console";
    }
  } catch {
    // Not JSON, so not our marker.
  }
  return "The Console";
}

const same = (a: string, b: string) =>
  a.trim().toLowerCase() === b.trim().toLowerCase();

/** The active owners on the registry roster. The registry email is CITEXT,
 *  so every comparison here ignores case. */
function activeOwners(roster: MemberRow[]): MemberRow[] {
  return roster.filter((m) => m.status === "active" && m.role === "owner");
}

/** Is this person the ONLY active owner? Then removing them leaves the org
 *  with no billing owner, unless somebody takes over. */
export function isSoleOwner(roster: MemberRow[], email: string): boolean {
  const owners = activeOwners(roster);
  return owners.length === 1 && same(owners[0].email, email);
}

/** Who may take over billing from `email`: every other ACTIVE member. */
export function ownerCandidates(roster: MemberRow[], email: string): string[] {
  return roster
    .filter((m) => m.status === "active" && !same(m.email, email))
    .map((m) => m.email);
}

/**
 * The owner hand-over rule (owner decision, 2026-10-15: "the acting admin
 * inherits"). The BFF runs it on the registry roster BEFORE the tenant act.
 *
 * ⚠️ The Console refuses the same cases, but only AFTER the tenant plane
 * took access away. Checking here first is what keeps a refusal from leaving
 * a half-done removal.
 *
 * - The target is the only active owner: `newOwner` is required.
 * - A `newOwner` that is given must be an active member and not the target,
 *   whether or not it is required.
 */
export function ownerHandover(
  roster: MemberRow[],
  email: string,
  newOwner: string | undefined,
): { ok: true } | { ok: false; error: string } {
  if (newOwner === undefined) {
    if (isSoleOwner(roster, email)) {
      return {
        ok: false,
        error:
          `${email} is the only active owner. Choose a new billing owner ` +
          `(new_owner_email) from the active members first.`,
      };
    }
    return { ok: true };
  }
  if (same(newOwner, email)) {
    return { ok: false, error: "the new owner must not be the person removed" };
  }
  if (!ownerCandidates(roster, email).some((c) => same(c, newOwner))) {
    return {
      ok: false,
      error: `${newOwner} is not an active member of this organization`,
    };
  }
  return { ok: true };
}

/** Statuses that prove the Console gave no ANSWER. The same line as
 *  `console_resolve.py` draws: a 5xx, 401 (our own credential is wrong, never
 *  a fact about the member), 408 and 429 (no answer produced). */
function noAnswer(status: number | null): boolean {
  return (
    status === null ||
    status >= 500 ||
    status < 400 ||
    status === 401 ||
    status === 408 ||
    status === 429
  );
}

/**
 * The answer when the tenant half succeeded and the registry half did not.
 *
 * ⚠️ **Two kinds of failure, and only one is worth a retry.**
 *
 * - No answer (`status` null, a 5xx, 401, 408 or 429) is a fault on the way.
 *   The Console call is idempotent, so the same action again finishes the
 *   job. 502.
 * - Any other 4xx (400, 403, 404, 409) is the Console's VERDICT. The same request gets the
 *   same verdict, so "run it again" would only teach the operator to click
 *   until it stops. The Console's own status is relayed, because it is the
 *   truth of what happened. The text says the tenant half is done and that
 *   the registry row needs a person to look at it.
 *
 * Both are non-2xx, so the roster never reads either as success.
 */
export function registryFailure(
  email: string,
  status: number | null,
  detail: unknown,
  slug = "",
): { status: number; retry: boolean; error: string } {
  const done = `the tenant half succeeded (${email} has no access now)`;
  if (status === null || noAnswer(status)) {
    const what = status === null ? "did not answer" : `answered ${status}`;
    return {
      status: 502,
      retry: true,
      error:
        `${done}, but the Console ${what}. Run this action again to finish ` +
        `the registry half. A retry is safe.`,
    };
  }
  if (status === 404) {
    // The member door answers {found: false} for an absent member, so a 404
    // means the registry has no such ORGANIZATION.
    return {
      status,
      retry: false,
      error:
        `${done}, but the registry has no organization` +
        `${slug ? ` "${slug}"` : ""}. Do not retry. Check which registry ` +
        `this console reads, and where the organization is recorded.`,
    };
  }
  const said =
    typeof (detail as { detail?: unknown } | null)?.detail === "string"
      ? (detail as { detail: string }).detail
      : typeof detail === "string" && detail
        ? detail
        : "no detail";
  return {
    status,
    retry: false,
    error:
      `${done}, but the registry refused with ${status}: ${said}. Do not ` +
      `retry. The registry row for ${email} needs attention, because it ` +
      `still shows this person.`,
  };
}
