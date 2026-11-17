/**
 * The floor view of a Studio app (STU-D23a, `ai_studio.md` §14 D23a). Pure: no React, no I/O.
 *
 * A member opens an app on a tablet as `/studio/package-preview?pkg=<id>&floor=1`. The app frame
 * then keeps the cells of the page and the record page. It drops the rail, the owner verbs, the
 * end-date bar, the setup card and the list bar. A page picker above the page takes the place of
 * the rail (STU-D23a-2, `floorPages`). A record always opens as the full page. The root
 * of the frame takes `data-floor`, so each control inside it takes its touch size at every width
 * (the `pointer-coarse` variant of `globals.css`). The app bar names the member who is signed in.
 *
 * **The key.** `floor` is a key of the viewer, not page state. `usePageState` does not own it, so
 * each write of the page state keeps it (`mergePageQuery`), and `decodePageState` ignores it. It
 * never starts with `f.`, which is the prefix of a filter. The name is `floorView` and never
 * `floorMode`: `floorMode` is the floor control of a chat room (`lib/rooms.ts`).
 *
 * **What it is not.** It signs nobody in, and it keeps the session of the member. The member
 * session on a paired tablet is D23b. Density stays the member's own choice.
 */

/** The query key of the floor view. Only the value `1` turns it on. */
export const FLOOR_KEY = "floor";

/** True when the query asks for the floor view. A hostile query gives false, never an error. */
export function isFloor(query: string | URLSearchParams | null | undefined): boolean {
  try {
    const p = typeof query === "string" ? new URLSearchParams(query) : query;
    return p?.get(FLOOR_KEY) === "1";
  } catch {
    return false;
  }
}

/** The parts of the app frame. `true` draws the part. */
export interface FrameParts {
  /** The floor view is on. */
  floor: boolean;
  /** The cells of the page and the record page stay in each view. */
  cells: true;
  record: true;
  /** The left rail of pages. */
  rail: boolean;
  /** The owner verbs (Open in Studio IDE, Delete app), or the read-only line of a viewer. */
  ownerBar: boolean;
  /** The end-date bar of the package (`PackageLifetimeBar`). */
  lifetime: boolean;
  /** The setup card above the app (`AppConnectCard`). */
  setupCard: boolean;
  /** The list bar: search, filters, sort, layout and group. */
  filterBar: boolean;
  /** A record always opens as the full page. */
  fullRecord: boolean;
  /** The full page has a way back to the side panel. */
  toPanel: boolean;
  /** The page picker above the page, which stands in for the rail (STU-D23a-2). */
  pagePicker: boolean;
}

/** The parts of the frame for the floor view, or for the usual view. */
export function floorView(floor: boolean): FrameParts {
  const usual = !floor;
  return {
    floor,
    cells: true,
    record: true,
    rail: usual,
    ownerBar: usual,
    lifetime: usual,
    setupCard: usual,
    filterBar: usual,
    fullRecord: floor,
    toPanel: usual,
    pagePicker: floor,
  };
}

/** One choice of the page picker. `group` is the title of its section of the rail. */
export interface FloorPageOption {
  value: string;
  label: string;
  group?: string;
}

/**
 * The choices of the page picker of the floor view (STU-D23a-2). The floor view has no rail, so a
 * member changes the page here. The choices are the rows of the rail, in the same order: the rows
 * that `railFor` and `visibleRail` give. A titled section of the rail gives the group heading.
 * Null in the usual view, and when one page or none is left to pick, so no picker draws.
 */
export function floorPages(
  parts: FrameParts,
  sections: readonly { title: string; rows: readonly { page: string; label: string }[] }[],
): FloorPageOption[] | null {
  if (!parts.pagePicker) return null;
  const options = sections.flatMap((s) => s.rows.map((r) => (s.title.trim()
    ? { value: r.page, label: r.label, group: s.title }
    : { value: r.page, label: r.label })));
  return options.length > 1 ? options : null;
}

/** Whether the open record shows as the full page. */
export function recordFull(parts: FrameParts, full: boolean): boolean {
  return parts.fullRecord || full;
}

/**
 * The scope line of the app bar in the floor view: "Signed in as <name>". The name comes from the
 * session, and the email stands in when the session has no name. The usual view has no such line.
 */
export function floorSubtitle(parts: FrameParts, user: { name?: string | null; email?: string | null } | null | undefined): string | undefined {
  if (!parts.floor) return undefined;
  const name = typeof user?.name === "string" ? user.name.trim() : "";
  const email = typeof user?.email === "string" ? user.email.trim() : "";
  const who = name || email;
  return who ? `Signed in as ${who}` : undefined;
}
