/**
 * The period of a dashboard and the link from a tile to its list (STU-LY-7b, `studio_layouts.md` L4).
 * Pure: no React, no I/O.
 *
 * **The period.** One control in the dashboard header cuts every stat, chart and the worklist to the last 7, 30
 * or 90 days, or to all time. It adds ONE filter to the source of each tile: `<date field> gte <day>`. The date
 * field is the first hot `date` or `datetime` field of the object that the tile reads (only a hot field filters:
 * `acb_common/studio_query.py`). A tile whose object has no such field is not cut, and says nothing. The day is
 * the UTC day, as the store reads a date. The period lives in the URL as `period`.
 *
 * **The tile link.** A stat or a chart tile may open the page that lists its object: the first other page that
 * holds a table or a board of the same object. The filter of the tile does not carry over (a later slice maps a
 * source filter to the chips of the list bar).
 */
import type { PageState } from "./pageState";

export const PERIODS = [
  { id: "all", label: "All time", days: 0 },
  { id: "7d", label: "Last 7 days", days: 7 },
  { id: "30d", label: "Last 30 days", days: 30 },
  { id: "90d", label: "Last 90 days", days: 90 },
] as const;
export type PeriodId = (typeof PERIODS)[number]["id"];
export const DEFAULT_PERIOD: PeriodId = "all";

export const isPeriod = (v: unknown): v is PeriodId => PERIODS.some((p) => p.id === v);

export interface PeriodField { key: string; type: string; hot?: boolean; many?: boolean; lines?: unknown }

/** The first hot date or datetime field of an object, or `null`. */
export function periodFieldOf(fields: readonly PeriodField[] | null | undefined): string | null {
  const f = (fields ?? []).find((x) => (x.type === "date" || x.type === "datetime") && x.hot === true && x.many !== true && typeof x.lines !== "string");
  return f ? f.key : null;
}

export interface PeriodFilter { field: string; op: "gte"; value: string }

/** The one filter of a period, or `null` for all time, an unknown period or an object with no date field. */
export function periodFilter(field: string | null, period: string | undefined, now: Date): PeriodFilter | null {
  if (!field || !isPeriod(period)) return null;
  const days = PERIODS.find((p) => p.id === period)?.days ?? 0;
  if (days <= 0 || Number.isNaN(now.getTime())) return null;
  return { field, op: "gte", value: new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10) };
}

/** The state with a period set. `all` takes the key away, so the URL stays short. */
export function withPeriod(state: PageState, period: string): PageState {
  const next = { ...state };
  if (isPeriod(period) && period !== DEFAULT_PERIOD) next.period = period;
  else delete next.period;
  return next;
}

type Source = { action: string; args?: Record<string, unknown> };
type NodeLike = { props?: { source?: Source } & Record<string, unknown> } & Record<string, unknown>;

/** The node with the filter added to its source. The input is not changed. A node with no source comes back as it is. */
export function periodNode<T extends NodeLike>(node: T, filter: PeriodFilter | null): T {
  const src = node?.props?.source;
  if (!filter || !src || typeof src.action !== "string") return node;
  const had = Array.isArray(src.args?.filter) ? (src.args!.filter as unknown[]) : [];
  return { ...node, props: { ...node.props, source: { ...src, args: { ...(src.args ?? {}), filter: [...had, filter] } } } };
}

export interface TilePage { id: string; viewIds: readonly string[] }
export interface TileView { id: string; block: string }

/**
 * The page that lists an object: the first page other than `from` that holds a table or a board that reads
 * `object`. `objectOf` says which object a view reads. `null` when no page lists it.
 */
export function tileTarget(
  pages: readonly TilePage[], views: readonly TileView[], objectOf: (viewId: string) => string | null,
  from: string, object: string | null,
): string | null {
  if (!object) return null;
  const lists = new Set(views.filter((v) => v.block === "table" || v.block === "board").map((v) => v.id));
  for (const p of pages) {
    if (p.id === from) continue;
    if (p.viewIds.some((id) => lists.has(id) && objectOf(id) === object)) return p.id;
  }
  return null;
}
