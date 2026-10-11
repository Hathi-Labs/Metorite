/**
 * How a person's name reads, given what the directory holds. ONE rule, for
 * every surface that prints a member.
 *
 * Owner, 2026-10-10, on the Capacity panel: *"how come some people show up as
 * email IDs … ideally, we should be using their names only."*
 *
 * ## Why a directory "name" can be an email
 *
 * Measured on production that day: 9 of 38 `people` rows held the member's
 * address as their name. The founder bootstrap writes the address into
 * `app_user.display_name` (a `NOT NULL` column), and the directory trigger
 * (migration 206) copies a non-empty display name into `people.name`. So the
 * name is "present", and every `row.name || row.email` fallback printed the
 * address while looking like it had found a name. H-298 carries the source
 * fix. This file makes the display right whatever the row says.
 *
 * ## The rule
 *
 * - A name is REAL when it is non-empty and is not an email address.
 * - With no real name, a person reads as the left half of their address —
 *   the fallback the board's cards already use (`grouping.personLabel`).
 * - The full address is never the LABEL. `PersonName` puts it in the hover.
 *
 * Fence: `personName.test.ts`, and its sweep of the surfaces that print
 * people, so no `name || email` fallback grows back.
 */

/** Does this look like an email address? A name never holds an "@". */
export function isEmailLike(value: string | null | undefined): boolean {
  return typeof value === "string" && /\S@\S/.test(value);
}

/** The name, when it is one. Null for blank, or for an address posing as a name. */
export function realName(name: string | null | undefined): string | null {
  const clean = (name ?? "").trim();
  return clean && !isEmailLike(clean) ? clean : null;
}

/** The left half of an address: `priya` for `priya@acme.in`. */
export function emailLocalPart(email: string): string {
  return email.split("@")[0] || email;
}

/** How a person reads: their real name, else the left half of their address. */
export function personDisplayName(name: string | null | undefined, email: string): string {
  return realName(name) ?? emailLocalPart(email);
}
