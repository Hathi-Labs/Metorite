// Rename a customer's organization — the pure rules (CP-8 "Rename an
// organization", owner request, 2026-10-27).
//
// ⚠️ Pure, and safe for a client file to import. It holds no credential. The
// BFF route `api/operator/orgs/rename` and the Rename panel in `Actions.tsx`
// both read it, so the two cannot disagree on a rule.
//
// 🔴 **The gateway's `clean_display_name` is the ONE name check.** This module
// decides nothing about what a good name is. It reads the body's shape, the
// caller's rank and the answer of each plane. A second copy of the limit here
// would drift from the gateway's, so an empty, long or unprintable name
// reaches the tenant door and comes back as its 422.

import { operatorRank } from "./memberRemove";
import type { Whoami } from "./whoami";

export type RenameRequest = { slug: string; name: string };

/** Read the BFF body. The slug must be a non-empty string and the name must
 *  be a string. The name is passed on as typed: the gateway cleans it. */
export function readRenameRequest(
  body: unknown,
): { ok: true; req: RenameRequest } | { ok: false; error: string } {
  const b = (typeof body === "object" && body !== null ? body : {}) as Record<
    string,
    unknown
  >;
  const slug = typeof b.org_slug === "string" ? b.org_slug.trim() : "";
  if (!slug) return { ok: false, error: "org_slug is required" };
  if (typeof b.name !== "string") {
    return { ok: false, error: "name is required, as a string" };
  }
  return { ok: true, req: { slug, name: b.name } };
}

/**
 * May this caller rename? Null means yes, and a string is the reason for the
 * 403.
 *
 * 🔴 **The BFF must decide this before the tenant act.** The gateway door
 * trusts its operator token, and this console holds that token for every
 * caller. The Console checks the rank on its own route, but the BFF calls
 * that route AFTER the tenant plane changed. The Console's matrix row is
 * EDITOR, and `test_operator_role_matrix_source.py` pins it.
 *
 * ⚠️ The break-glass caller is admitted. That token bypasses the matrix on
 * the Console too, so a refusal here would be a second policy for one token.
 */
export function mayRename(who: Whoami): string | null {
  if (who.method === "breakglass") return null;
  if (operatorRank(who.role) < operatorRank("editor")) {
    return "renaming an organization needs the editor role or higher";
  }
  return null;
}

/** The name the Console should store: the one the tenant plane stored, so
 *  both planes hold one value. The typed name only when the tenant answer
 *  has no readable name. */
export function storedName(tenantBody: unknown, typed: string): string {
  const name = (tenantBody as { display_name?: unknown } | null)?.display_name;
  return typeof name === "string" && name ? name : typed;
}

/** Statuses that prove the Console gave no ANSWER. The line
 *  `console_resolve.py` draws: a 5xx, 401, 408 and 429. */
function noAnswer(status: number): boolean {
  return (
    status >= 500 || status < 400 || status === 401 || status === 408 || status === 429
  );
}

/**
 * The answer when the tenant rename succeeded and the Console rename did not.
 *
 * - No answer (`status` null, a 5xx, 401, 408 or 429) is a fault on the way.
 *   Both writes set the same value, so the same action again finishes the
 *   job. 502, with the retry.
 * - Any other 4xx is the Console's VERDICT, and the same request gets the
 *   same verdict. The Console's own status is relayed with no retry advice.
 */
export function registryRenameFailure(
  status: number | null,
  detail: unknown,
): { status: number; retry: boolean; error: string } {
  const done = "the tenant plane is renamed";
  if (status === null || noAnswer(status)) {
    const what = status === null ? "did not answer" : `answered ${status}`;
    return {
      status: 502,
      retry: true,
      error:
        `${done}, but the Console ${what}. Run the rename again to finish ` +
        `the Console half. A retry is safe.`,
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
      `${done}, but the Console refused with ${status}: ${said}. Do not ` +
      `retry. The two planes now show different names, and the Console ` +
      `record needs attention.`,
  };
}

/** Why the Rename button is off, or null when it is on. The panel shows it
 *  as the button's title. Only "nothing typed" and "no change" are judged
 *  here. The gateway judges the name itself. */
export function renameBlocked(typed: string, current: string): string | null {
  const next = typed.split(/\s+/).filter(Boolean).join(" ");
  if (!next) return "Type the new name first.";
  if (next === current) return "This is the current name.";
  return null;
}
