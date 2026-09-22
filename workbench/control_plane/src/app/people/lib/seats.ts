/**
 * People Center · §5.6 Seats and roles — the matrix, as a pure pivot.
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.6 ·
 * `org_access_control.md` · `groups_sessions_authority.md` §1.
 *
 * **No new endpoint, and that is the point.** The matrix is people down the
 * side and groups across the top, which is `GET /admin/groups` (groups WITH
 * their members inline, and `is_center`) crossed with `GET /admin/members`
 * (the roster and its roles) and `GET /people` (the directory, which knows
 * who has no login). All three already exist, all three sit on the same
 * `admin:members:read` floor, and a fourth endpoint that re-read the same
 * three tables would be the CLAUDE.md §5 defect.
 *
 * So the pivot lives here, as a function over data, and the page renders what
 * it returns. That also makes the interesting part testable without a
 * network: the arithmetic below is where a seat can go missing.
 *
 * ⚠️ **A seat needs a LOGIN, and the join is what says so.**
 * `org_group_member` references `app_user`, so a directory-only person cannot
 * hold one — there is no row to point at. The matrix therefore has two kinds
 * of row, and it says which:
 *
 * - a member (has `app_user`) whose seats are checkboxes, and
 * - a **directory-only** person, whose row carries no checkbox at all and
 *   whose only available act is an invite.
 *
 * Rendering an unchecked box for somebody who cannot hold a seat would be a
 * control that silently does nothing, which §4.3 already refuses for write
 * permissions ("no disabled-button theatre"). The same argument applies to a
 * row the schema cannot accept.
 *
 * ⚠️ **Every address is compared LOWERCASED.** `lower(email)` is the join in
 * migration 148, in `people_row_from_member`, and in `resolve_access`. A
 * comparison here that used the raw string would put `Priya@…` and
 * `priya@…` in different rows and quietly halve somebody's seats.
 */

/** One group, as `GET /admin/groups` returns it. */
export interface SeatGroup {
  slug: string;
  display_name: string;
  is_center?: boolean;
  members?: Array<{ email: string; role?: string }>;
}

/** One roster row, as `GET /admin/members` returns it. */
export interface SeatMember {
  email: string;
  display_name?: string;
  status?: string;
  roles?: string[];
}

/** One directory row, as `GET /people` returns it. */
export interface SeatPerson {
  id: string;
  name: string;
  email?: string | null;
  title?: string | null;
  department?: string | null;
  status?: string | null;
  has_login?: boolean;
}

export interface SeatCell {
  slug: string;
  /** True when this person is in that group. */
  member: boolean;
  /** `lead` manages the group's own membership. Group-scoped, not a role. */
  lead: boolean;
}

export interface SeatRow {
  /** The directory id, when the person has a directory row. */
  id: string;
  name: string;
  email: string;
  title: string;
  department: string;
  /** False for a directory-only person: no `app_user`, so no seat is possible. */
  hasLogin: boolean;
  /** Org roles from the roster — read-only here, edited in Settings. */
  roles: string[];
  /** One cell per column, in the same order as `columns`. */
  cells: SeatCell[];
  /** How many groups this person is in. */
  seatCount: number;
}

export interface SeatMatrix {
  columns: SeatGroup[];
  rows: SeatRow[];
  /** People with a login but no directory row — agents, alumni, an odd import. */
  membersWithoutADirectoryRow: number;
  /** People in the directory who cannot hold a seat until somebody invites them. */
  directoryOnly: number;
}

const lower = (value: string | null | undefined): string =>
  (value ?? "").trim().toLowerCase();

/**
 * Build the matrix.
 *
 * The row set is the UNION of the directory and the roster, keyed on the
 * lowered address, because neither one alone is the answer:
 *
 * - the directory alone misses a member who has a login and no directory row
 *   (which migration 206's trigger now prevents for new members, and does not
 *   retrofit for an address that predates it), and
 * - the roster alone misses every directory-only person, who is exactly who
 *   the invite half of this surface exists for.
 *
 * A person with no address at all is dropped, and the page reports the count.
 * There is nothing to join them on, so a row for them could never be right.
 */
export function buildSeatMatrix(
  groups: SeatGroup[],
  members: SeatMember[],
  people: SeatPerson[],
): SeatMatrix {
  const columns = [...groups].sort((a, b) => {
    // Centers first — they are the scoping primitive the grant model rests
    // on (D12), so they are the columns somebody came here to read.
    if (Boolean(a.is_center) !== Boolean(b.is_center)) return a.is_center ? -1 : 1;
    return (a.display_name || a.slug).localeCompare(b.display_name || b.slug);
  });

  const seatsByEmail = new Map<string, Map<string, string>>();
  for (const group of groups) {
    for (const m of group.members ?? []) {
      const key = lower(m.email);
      if (!key) continue;
      if (!seatsByEmail.has(key)) seatsByEmail.set(key, new Map());
      seatsByEmail.get(key)!.set(group.slug, m.role || "member");
    }
  }

  const roster = new Map<string, SeatMember>();
  for (const m of members) {
    const key = lower(m.email);
    if (key) roster.set(key, m);
  }

  const directory = new Map<string, SeatPerson>();
  for (const p of people) {
    const key = lower(p.email);
    if (key) directory.set(key, p);
  }

  const keys = new Set<string>([...directory.keys(), ...roster.keys()]);
  const rows: SeatRow[] = [];
  for (const key of keys) {
    const person = directory.get(key);
    const member = roster.get(key);
    const seats = seatsByEmail.get(key) ?? new Map<string, string>();
    const cells = columns.map((g) => ({
      slug: g.slug,
      member: seats.has(g.slug),
      lead: seats.get(g.slug) === "lead",
    }));
    rows.push({
      id: person?.id ?? "",
      name: person?.name || member?.display_name || key,
      email: person?.email || member?.email || key,
      title: person?.title || "",
      department: person?.department || "",
      // The ROSTER is the authority on whether a login exists, not the
      // directory's derived flag: `has_login` is computed per read and the
      // roster row IS the `app_user`. They agree today; if they ever
      // disagree, the one that can hold a seat is the one that matters.
      hasLogin: Boolean(member),
      roles: member?.roles ?? [],
      cells,
      seatCount: seats.size,
    });
  }

  rows.sort((a, b) => a.name.localeCompare(b.name));

  let directoryOnly = 0;
  let membersWithoutADirectoryRow = 0;
  for (const key of keys) {
    if (!roster.has(key)) directoryOnly += 1;
    else if (!directory.has(key)) membersWithoutADirectoryRow += 1;
  }

  return { columns, rows, membersWithoutADirectoryRow, directoryOnly };
}

/**
 * What a toggle would do, named rather than inferred at the call site.
 *
 * `add` and `remove` map onto the two endpoints that already exist —
 * `POST /admin/groups/{slug}/members` and
 * `DELETE /admin/groups/{slug}/members/{email}`. `blocked` is the third
 * answer, and it is the one the schema gives for a person with no login.
 */
export type SeatAction = "add" | "remove" | "blocked";

export function actionFor(row: SeatRow, slug: string): SeatAction {
  if (!row.hasLogin) return "blocked";
  const cell = row.cells.find((c) => c.slug === slug);
  return cell?.member ? "remove" : "add";
}
