// ── Which rows the roster shows, and what the Remove dialog says ──────────
//
// Owner request, 2026-10-15: an admin must see the people they removed, so
// they can delete them permanently. The page reads the roster ONCE with
// `include_removed=true` and splits it here. `members` stays the same list it
// was before (no removed rows), so the Seats tab, the invite check and every
// count read exactly what they read before. `removed` feeds only the new
// "Removed" pill.
//
// Pure, because this repo has no component harness. A rule written inline in
// `OrganizationAdmin.tsx` could be fenced only by a grep for its copy.

import type { Member } from "@/app/settings/members/types";

/** The roster read. One request, and the removed rows come with it. */
export const ROSTER_URL = "/api/admin/members?include_removed=true";

export type RosterFilter = "all" | "active" | "invited" | "suspended" | "removed";

/** Split one roster read into the live roster and the removed people. */
export function splitRoster(rows: Member[]): {
  members: Member[];
  removed: Member[];
} {
  return {
    members: rows.filter((m) => m.status !== "removed"),
    removed: rows.filter((m) => m.status === "removed"),
  };
}

/**
 * The rows for one filter pill.
 *
 * ⚠️ **"All" leaves the removed rows out.** It meant "everybody in the
 * organization" before removed rows were loaded, and a removed person is not
 * in it. They show under "Removed" only.
 */
export function rowsFor(
  filter: string,
  members: Member[],
  removed: Member[],
): Member[] {
  if (filter === "removed") return removed;
  if (filter === "all") return members;
  return members.filter((m) => m.status === filter);
}

/** The line under an empty filter. A removed list that is empty is good news,
 *  so it must not read like a roster that failed to load. */
export function emptyLine(filter: string): string {
  return filter === "removed"
    ? "Nobody has been removed."
    : "No members here yet.";
}

/**
 * The Delete dialog's last line: what the admin can do instead.
 *
 * ⚠️ A removed person has no Remove to fall back to. The row offers none, and
 * their access is gone already. So for them the line says what this step adds
 * to the removal, and it must not send the admin to a button that is absent.
 */
export function purgeAlternative(member: Member): {
  lead: string;
  action: string | null;
  tail: string;
} {
  if (member.status === "removed") {
    return {
      lead:
        "This person is already removed and has no access. This step " +
        "deletes their record and what it still holds.",
      action: null,
      tail: "",
    };
  }
  return {
    lead: "If you only want to end their access, use ",
    action: "Remove",
    tail: " — that one is recoverable.",
  };
}

/** The words of the Remove dialog for one person. */
export type RemoveCopy = {
  title: string;
  /** What happens. The dialog draws the address first, then this text. */
  effect: string;
  /** How to come back from it, or what to use instead. */
  after: string;
  confirm: string;
  /** Show the D63 seal preview? Only a person who signed in has a workspace. */
  showSeal: boolean;
};

/**
 * An invited person never signed in. So they have no access to lose and no
 * personal workspace to seal, and Suspend is not offered on their row. The
 * dialog for them says what the act really is: the invitation ends.
 */
export function removeCopy(member: Member): RemoveCopy {
  const name = member.display_name || member.email;
  if (member.status === "invited") {
    return {
      title: `Revoke the invite for ${name}?`,
      // ⚠️ No word about a seat. An invited person holds no Core seat yet,
      // so "their seat goes back to the pool" would be false (review round 1).
      effect: "cannot accept the invitation after this, and cannot sign in.",
      after:
        "To bring them in later, invite them again. Use Delete permanently " +
        "if you also want their record gone.",
      confirm: "Revoke invite",
      showSeal: false,
    };
  }
  return {
    title: `Remove ${name}?`,
    effect:
      "loses access immediately and every role assignment they hold is " +
      "dropped. Their record is kept, because the rest of the system refers " +
      "to people by address.",
    after:
      "This is not a toggle — bringing them back means inviting them again " +
      "and activating the invitation. If you only want to pause their " +
      "access, use Suspend instead.",
    confirm: "Remove",
    showSeal: true,
  };
}
