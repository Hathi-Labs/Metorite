/**
 * The count of the sidebar's Waiting row (WS-AP AP-2a, `waiting_on_me.md`
 * §5.1). Pure rules and the one fetch, so the fences read them.
 * `useNavNeeds.ts` reads it through the cache, and joins the ONE poll
 * (`joinNeedsPoll`) under its own key. No second timer exists.
 *
 * The rules:
 *
 * - **One count path** (§7.3). The row's number IS `GET /shell/waiting/count`.
 *   The client adds nothing to it: not the agent questions, not the needs
 *   feed. Until AP-1b lands, the count holds the AP-1a kinds only (§5.1,
 *   the lead's option (a)).
 * - **A 404 shows no count.** `WAITING_ORGS` does not hold the org. The row
 *   stays, because no second nav filter hides a row (`launch_surface.md`
 *   §8.4). A failed read shows no count either, never a 0 or a stale one.
 * - **A floor prints "15+".** `capped` names a source whose total is a
 *   floor, and `navNeeds.ts` draws it as it draws a needs floor.
 * - **Read only when the row is held.** The pane is `preview`, so the read
 *   runs only where the sidebar shows the row.
 *
 * Fence (R7): `waitingCount.test.ts`. The browser case joins
 * `e2e/sidebar-row-counts.spec.ts` with AP-2c: the e2e build does not set
 * `NEXT_PUBLIC_SHOW_PREVIEW_APPS`, so a `preview` row does not draw there.
 */
import { PROJECTS_CACHE } from "@/app/projects/lib/api";

/** The href of the Waiting pane in `nav.ts`. */
export const WAITING_HREF = "/waiting";

/**
 * The cache key of the count. Under `PROJECTS_CACHE`, so a Projects write
 * (an answered decision) drops it, as it drops the needs feed.
 */
export const WAITING_COUNT_KEY = `${PROJECTS_CACHE}shell/waiting/count`;

export interface WaitingCount {
  count: number;
  /** The count is a floor: "15+", "15 or more need you". */
  floor: boolean;
}

/** Read the count's answer. A field the server left out reads as 0. */
export function readWaitingCount(raw: unknown): WaitingCount {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const n = typeof body.count === "number" && Number.isFinite(body.count) ? Math.max(0, Math.floor(body.count)) : 0;
  const floor = Array.isArray(body.capped) && body.capped.length > 0;
  return { count: n, floor };
}

/**
 * Read the count through the BFF. `null` is the OFF state (404). Any other
 * refusal or failure throws, so the row shows no number.
 */
export async function fetchWaitingCount(): Promise<WaitingCount | null> {
  const res = await fetch("/api/shell/waiting/count", { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("The Waiting count could not load.");
  return readWaitingCount(await res.json());
}

/**
 * Put the Waiting count on its row. A count of 0, the OFF state and a failed
 * read put nothing there. Returns the inputs when nothing changes.
 */
export function withWaitingCount(
  counts: Record<string, number>,
  floors: ReadonlySet<string>,
  waiting: WaitingCount | null | undefined,
  failed: boolean,
): { counts: Record<string, number>; floors: ReadonlySet<string> } {
  if (failed || !waiting || waiting.count <= 0) return { counts, floors };
  const nextFloors = waiting.floor ? new Set([...floors, WAITING_HREF]) : floors;
  return { counts: { ...counts, [WAITING_HREF]: waiting.count }, floors: nextFloors };
}
