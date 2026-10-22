/**
 * Projects · the chat's `show_view` (WS-46 P11, G22).
 *
 * Owner, 2026-10-22: the chat sets filters, views and grouping, as the filter
 * bar does. The skill tool `show_view` (`skill_projects/nav.py`) reads the
 * project, resolves a person and a saved view by name, and sends this page
 * the result as `projects.show_view`. This module turns those arguments into
 * page state. It writes nothing, and it reads the stored shape through the one
 * reader, `fromConfig`, so a filter the chat sets and a saved view agree.
 */
import { type ViewMode, VIEW_MODES } from "./commands";
import {
  type BoardLanes,
  type Filters,
  type GroupBy,
  fromConfig,
  GROUP_OPTIONS,
} from "./grouping";

export interface ShowViewPlan {
  projectId: string;
  mode: ViewMode | null;
  /** A saved view, applied first, as the view chip applies it. */
  view: {
    id: string;
    filters: Filters;
    groupBy: GroupBy;
    lanes: BoardLanes;
    shownFields: string[];
  } | null;
  /** Replaces the filters on screen. `null` keeps them. */
  filters: Filters | null;
  groupBy: GroupBy | null;
  /** The board's second axis. `"none"` makes it flat. `null` keeps it. */
  subGroupBy: GroupBy | null;
}

const UUID = /^[0-9a-f-]{36}$/i;
const MODES: readonly string[] = VIEW_MODES.map((entry) => entry.id);

function axis(value: unknown): GroupBy | null | "bad" {
  if (value === undefined || value === null || value === "") return null;
  return GROUP_OPTIONS.includes(value as GroupBy) ? (value as GroupBy) : "bad";
}

/** The plan, or why the page refuses the arguments. */
export function planShowView(args: Record<string, unknown>): ShowViewPlan | string {
  const projectId = String(args.project_id ?? "");
  if (!UUID.test(projectId)) return "not a project id";
  const rawMode = args.mode;
  let mode: ViewMode | null = null;
  if (rawMode !== undefined && rawMode !== null && rawMode !== "") {
    if (!MODES.includes(String(rawMode))) return "not a mode";
    mode = String(rawMode) as ViewMode;
  }
  let view: ShowViewPlan["view"] = null;
  if (args.view_id !== undefined) {
    const id = String(args.view_id ?? "");
    if (!UUID.test(id)) return "not a view id";
    const read = fromConfig(args.view_config);
    view = {
      id,
      filters: read.filters,
      groupBy: read.groupBy,
      lanes: read.lanes,
      shownFields: read.shownFields,
    };
  }
  let filters: Filters | null = null;
  if (args.filters !== undefined) {
    if (typeof args.filters !== "object" || args.filters === null || Array.isArray(args.filters)) {
      return "filters is an object";
    }
    filters = fromConfig({ filters: args.filters }).filters;
  }
  const groupBy = axis(args.group_by);
  const subGroupBy = axis(args.sub_group_by);
  if (groupBy === "bad" || subGroupBy === "bad") return "not a grouping";
  return { projectId, mode, view, filters, groupBy, subGroupBy };
}

/**
 * The lanes after a plan. The rule `FilterBar`'s group handler keeps: a sub
 * axis equal to the main axis means nothing, so it goes flat.
 */
export function lanesAfter(
  current: BoardLanes,
  groupBy: GroupBy,
  subGroupBy: GroupBy | null,
): BoardLanes {
  const next =
    subGroupBy === null ? current : { ...current, subGroupBy, collapsedLanes: [] };
  return next.subGroupBy === groupBy
    ? { ...next, subGroupBy: "none", collapsedLanes: [] }
    : next;
}
