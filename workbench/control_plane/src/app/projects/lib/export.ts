/**
 * Projects · the CSV export's client half (WS-27ae, P-26).
 *
 * The endpoint is `GET /api/projects/export/tasks.csv` and it exports **the
 * filter that is on screen**, with **the columns the view is showing**. So the
 * only thing this module does is hand the server the state the board is already
 * holding — through the SAME `toQuery`/`sortQuery` the list fetch uses, never a
 * second serialisation. Two of those would drift and then the file and the
 * table would disagree about what "my open bugs in Ops" means, which is the
 * whole bug class the one-builder rule exists for.
 *
 * The two halves that are NOT projects-specific — reading the server's filename
 * back off `Content-Disposition`, and handing the bytes to the browser without
 * decoding them — live in `@/lib/export`, shared with the CRM's export
 * (WS-26i-export). They carry their own warnings, including the one about
 * fetching rather than navigating.
 */

import { type Filters, type SubtaskMode, toQuery } from "./grouping";
import { subtaskReadParams } from "./subtaskView";
import { type TableSort, sortQuery } from "./table";

/** What the export needs to know, all of it already on screen. */
export interface ExportRequest {
  /** The selected project, or `null` for "everything I can see". */
  projectId: string | null;
  filters: Filters;
  /** The view's shown fields — the file's columns, in the table's order. */
  shownFields: readonly string[];
  /** The table's header sort, or `null` for the endpoint's default. */
  sort: TableSort | null;
  /**
   * D-PM-38 — the STORED Subtasks mode. Hidden sends `top_level`, so the
   * file holds the rows the board shows. Absent reads as "no mode".
   */
  subtasks?: SubtaskMode | null;
}

/**
 * The query string for one export.
 *
 * `include_subtree` rides along with a project for the same reason the board's
 * fetch sends it: what you are looking at is the project AND its subprojects,
 * and an export of the parent alone would be missing most of the rows on
 * screen.
 *
 * An empty `shownFields` is sent as an empty parameter rather than omitted, so
 * "this view shows no columns" reaches the server as a choice — the same
 * absent-versus-explicitly-empty distinction `sanitizeShownFields` keeps.
 */
export function exportQuery(request: ExportRequest): Record<string, string> {
  const params: Record<string, string> = {
    ...toQuery(request.filters),
    ...sortQuery(request.sort),
    shown_fields: request.shownFields.join(","),
    // A query string holds text, so the flag is the word "true".
    ...(subtaskReadParams(request.subtasks).top_level ? { top_level: "true" } : {}),
  };
  if (request.projectId) {
    params.project_id = request.projectId;
    params.include_subtree = "true";
  }
  return params;
}

/** `exportQuery` as the path the BFF proxy serves. */
export function exportPath(request: ExportRequest): string {
  const qs = new URLSearchParams(exportQuery(request));
  return `/api/projects/export/tasks.csv?${qs.toString()}`;
}

/**
 * The name a Projects export falls back to when the proxy dropped the header.
 *
 * The server owns the real one; this is only what `filenameFromDisposition`
 * uses when there is nothing to read.
 */
export const EXPORT_FILENAME = "projects-tasks.csv";

/**
 * Whether the Export button does anything.
 *
 * Exporting "everything I can see" is a legitimate request the endpoint
 * answers, so this is NOT gated on a project — only on the board having
 * finished loading enough to describe itself.
 */
export function canExport(request: ExportRequest): boolean {
  return Array.isArray(request.shownFields);
}
