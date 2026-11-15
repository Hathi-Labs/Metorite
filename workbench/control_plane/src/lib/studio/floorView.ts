/**
 * The floor view of a Studio app (STU-D23a, `ai_studio.md` §14 D23a). Pure: no React, no I/O.
 *
 * A member opens an app on a tablet as `/studio/package-preview?pkg=<id>&floor=1`. The app frame
 * then keeps the cells of the page and the record page. It drops the rail, the owner verbs, the
 * end-date bar, the setup card and the list bar. A record always opens as the full page. The root
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
  };
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
