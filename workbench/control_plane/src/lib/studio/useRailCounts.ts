"use client";

/**
 * The counts of the rail (STU-AF3b, `ai_studio.md` §5.5 rule 6). One hook, and it reads through
 * `door.read` only. A row reads one `list` with `limit: 1`, and the label comes from its `total`.
 *
 * At most `MAX_NAV_COUNTS` reads run, two at a time, so the 5 s cost guard of a page holds. Each
 * read is a cached resource keyed with `sourceKey`. A write through the door calls
 * `invalidate(doorPrefix(door))`, and that wakes each count key, so a count follows a write with no
 * code of its own. A count that is loading, failed, or refused gives no label, and the rail shows nothing.
 */
import { MAX_NAV_COUNTS } from "@/lib/studio/manifest";
import { type Row, type StudioDataDoor, rowsMeta, sourceKey } from "@/lib/studio/dataDoor";
import { countLabel } from "@/lib/studio/railCounts";
import type { StudioSource } from "@/lib/studio/dataDoor";
import { useCachedResource } from "@/lib/useCachedResource";
import { useNumberSystem } from "@/lib/numberSystem";

/** A row to count: the page id and the source, with `me` resolved. A null source is not read. */
export interface RailCountRow {
  page: string;
  source: StudioSource | null;
}

/** How many reads run at once. */
const AT_ONCE = 2;

/** The label of each counted page. A page with no label is absent from the map. */
export function useRailCounts(door: StudioDataDoor, rows: readonly RailCountRow[]): ReadonlyMap<string, string> {
  const system = useNumberSystem();
  const wanted = rows.slice(0, MAX_NAV_COUNTS);
  const states: { settled: boolean; label: string | null }[] = [];
  // A fixed number of hooks, so the order of the hooks never changes.
  for (let i = 0; i < MAX_NAV_COUNTS; i += 1) {
    const row = wanted[i];
    // Slot i waits until the slots before the last AT_ONCE are done.
    const turn = states.slice(0, Math.max(0, i - AT_ONCE + 1)).every((s) => s.settled);
    const source = row?.source ?? null;
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const res = useCachedResource<Row[]>(
      source && turn ? sourceKey(door, source) : null,
      () => door.read(source as StudioSource),
    );
    states.push({
      settled: !source || (!res.loading && (res.data !== undefined || res.error !== null)),
      label: res.data !== undefined ? countLabel(rowsMeta(res.data), system) : null,
    });
  }
  const out = new Map<string, string>();
  wanted.forEach((row, i) => {
    const label = states[i]?.label;
    if (label) out.set(row.page, label);
  });
  return out;
}
