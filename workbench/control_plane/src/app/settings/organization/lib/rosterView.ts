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
 * A sentence in parts. A part with `em` names a control on the screen, and
 * the dialog draws it with the `text-foreground` emphasis, so the admin sees
 * the button the sentence means. The words stay here, and the dialog only
 * draws them.
 */
export type CopyPart = { text: string; em?: true };

/** The parts as one plain string, for a test or a title attribute. */
export function plainText(parts: CopyPart[]): string {
  return parts.map((p) => p.text).join("");
}

/**
 * The Delete dialog's last line: what the admin can do instead.
 *
 * ⚠️ Each status points at a control its row really has. A removed person
 * has no Remove to fall back to, because their access is gone already, so
 * the line says what this step adds. An invited person's row says "Revoke
 * invite", not "Remove", so the line names that.
 */
export function purgeAlternative(member: Member): CopyPart[] {
  if (member.status === "removed") {
    return [
      {
        text:
          "This person is already removed and has no access. This step " +
          "deletes their record and what it still holds.",
      },
    ];
  }
  if (member.status === "invited") {
    return [
      { text: "If you only want to end the invitation, use " },
      { text: "Revoke invite", em: true },
      { text: " — that keeps their record." },
    ];
  }
  return [
    { text: "If you only want to end their access, use " },
    { text: "Remove", em: true },
    { text: " — that one is recoverable." },
  ];
}

/** The words of the Remove dialog for one person. */
export type RemoveCopy = {
  title: string;
  /** What happens. The dialog draws the address first, then this text. */
  effect: string;
  /** How to come back from it, or what to use instead. */
  after: CopyPart[];
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
      after: [
        { text: "To bring them in later, invite them again. Use " },
        { text: "Delete permanently", em: true },
        { text: " if you also want their record gone." },
      ],
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
    after: [
      {
        text:
          "This is not a toggle — bringing them back means inviting them " +
          "again and activating the invitation. If you only want to pause " +
          "their access, use ",
      },
      { text: "Suspend", em: true },
      { text: " instead." },
    ],
    confirm: "Remove",
    showSeal: true,
  };
}
