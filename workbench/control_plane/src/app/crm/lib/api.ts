// ── The /crm client ───────────────────────────────────────────────────────
//
// Every request goes through the BFF at /api/crm/* — never at the gateway
// directly, which carries no session (workbench/AGENTS.md, "Identity").
//
// Errors keep their status. The CRM says a lot with its codes — 422 for a
// lost status with no reason or a status filter on an entity with no
// pipeline, 409 for a re-convert or a status still in use — and a client that
// flattened them all to "request failed" would leave the UI unable to explain
// a refusal, which is the difference between a rule and a bug.

import { filenameFromDisposition, saveCsv } from "@/lib/export";
import { moveRequest, type DealMove, type MoveExtras } from "./board";
import { queryString, type ListQuery } from "./filters";
import type { ConnectionInfo, CrmSettings, SyncStatus } from "./connect";
import type { FieldDef } from "./fields";
import type {
  Activity,
  Contact,
  ConvertResult,
  Deal,
  DealContact,
  DealPipeline,
  EntitySlug,
  FunnelReport,
  Lead,
  ListResponse,
  LostReason,
  Organization,
  OwnerLeaderboard,
  Pipeline,
  PipelineReport,
  StageMetadataReport,
  Status,
  StatusKind,
  TimelineEntry,
  WinLossReport,
} from "./types";

export class CrmError extends Error {
  status: number;
  /**
   * The machine code of a refusal: a string `detail` (`sync_running`) or the
   * `code` of an object `detail` (`mirrored_record`, `mirror_mode_create`).
   */
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "CrmError";
    this.status = status;
    this.code = code;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/crm${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: unknown;
      error?: string;
    };
    throw new CrmError(
      detailOf(body) || `Gateway error ${res.status}`,
      res.status,
      codeOf(body)
    );
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/**
 * FastAPI's `detail` is a string for our own HTTPExceptions and an array of
 * objects for a Pydantic validation failure. Rendering "[object Object]" at
 * the user is how a perfectly clear refusal becomes unreadable.
 */
function detailOf(body: { detail?: unknown; error?: string }): string {
  const { detail } = body;
  if (typeof detail === "string") return detail;
  // The write guard (CRM-Z6) answers `{code, source, message}`.
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : String(item)
      )
      .join("; ");
  }
  return body.error ?? "";
}

function codeOf(body: { detail?: unknown }): string | undefined {
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return undefined;
}

// ── Records ───────────────────────────────────────────────────────────────

export function listRecords<T = Record<string, unknown>>(
  entity: EntitySlug,
  query: ListQuery = {}
): Promise<ListResponse<T>> {
  return call<ListResponse<T>>(`/${entity}${queryString(query)}`);
}

export function getRecord<T>(entity: EntitySlug, id: string): Promise<T> {
  return call<T>(`/${entity}/${id}`);
}

export function createRecord<T>(
  entity: EntitySlug,
  body: Record<string, unknown>
): Promise<T> {
  return call<T>(`/${entity}`, { method: "POST", body: JSON.stringify(body) });
}

export function patchRecord<T>(
  entity: EntitySlug,
  id: string,
  body: Record<string, unknown>
): Promise<T> {
  return call<T>(`/${entity}/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteRecord(
  entity: EntitySlug,
  id: string
): Promise<{ deleted: string; entity: string; cascaded: Record<string, number> }> {
  return call(`/${entity}/${id}`, { method: "DELETE" });
}

// ── Field definitions (CRM-Q4) ────────────────────────────────────────────

/** What each custom field of one entity means. Read only. */
export function listFields(entity: EntitySlug): Promise<FieldDef[]> {
  return call<FieldDef[]>(`/fields/${entity}`);
}

// ── The CSV export (WS-26i-export) ────────────────────────────────────────

/** What a download is called when the proxy dropped the server's header. */
export function exportFilename(entity: EntitySlug): string {
  return `crm-${entity}.csv`;
}

/**
 * The current filter as a CSV, saved by the browser.
 *
 * ⚠️ Not `call()`: that parses every body as JSON, and this one is `text/csv`.
 * A refusal from the SAME endpoint is still JSON, though — the gateway answers
 * 422 naming the matched count when a filter is wider than the export cap
 * rather than handing back a partial file — so the error path reuses
 * `detailOf` and throws the same `CrmError` every other call does, and the
 * refusal lands in the store's one error banner.
 *
 * ⚠️ `blob()`, never `text()`: decoding to a string strips the UTF-8 BOM the
 * gateway emits so Excel reads non-ASCII names correctly, and the saved file
 * would then differ from the bytes the endpoint produced.
 */
export async function exportRecords(
  entity: EntitySlug,
  query: ListQuery
): Promise<void> {
  const res = await fetch(`/api/crm/export/${entity}.csv${queryString(query)}`);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: unknown;
      error?: string;
    };
    throw new CrmError(detailOf(body) || `Export failed (${res.status})`, res.status);
  }
  saveCsv(
    await res.blob(),
    filenameFromDisposition(
      res.headers.get("content-disposition"),
      exportFilename(entity)
    )
  );
}

// ── The board ─────────────────────────────────────────────────────────────

export function getPipeline(
  owner?: string | null,
  pipelineId?: string | null,
): Promise<Pipeline> {
  const params = new URLSearchParams();
  if (owner) params.set("owner", owner);
  if (pipelineId) params.set("pipeline_id", pipelineId);
  const qs = params.toString();
  return call<Pipeline>(`/pipeline${qs ? `?${qs}` : ""}`);
}

/**
 * The deal pipelines (CRM-Q2). `null` when the gateway has no such route yet:
 * the CRM then has one implicit pipeline and shows no picker.
 */
export function listPipelines(): Promise<DealPipeline[] | null> {
  return orNullOn404(call<DealPipeline[]>("/pipelines"));
}

/**
 * The one place a deal changes lane. `board.moveRequest` decides the shape and
 * this issues it — the drag and the status pill therefore send the same
 * request by construction rather than by two functions agreeing.
 */
export function moveDeal(
  move: DealMove,
  extras: MoveExtras = {}
): Promise<Deal> {
  const request = moveRequest(move, extras);
  return call<Deal>(request.path, {
    method: request.method,
    body: JSON.stringify(request.body),
  });
}

// ── Pipeline vocabulary ───────────────────────────────────────────────────

export function listStatuses(kind: "lead" | "deal"): Promise<Status[]> {
  return call<Status[]>(`/statuses/${kind}`);
}

export function listLostReasons(): Promise<LostReason[]> {
  return call<LostReason[]>("/lost-reasons");
}

// ── Pipeline settings (WS-26f f2) ─────────────────────────────────────────
//
// The settings grids drive the EXISTING admin API — `POST/PATCH/DELETE
// /crm/statuses/{kind}` and the lost-reason trio — with no addendum. A
// drag-reorder is N per-row PATCHes of `position` (lib/settings.ts::reorder
// decides which rows moved), because a bulk-reorder endpoint would be a
// second way to write the same column.

export function createStatus(
  kind: StatusKind,
  body: Record<string, unknown>
): Promise<Status> {
  return call<Status>(`/statuses/${kind}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchStatus(
  kind: StatusKind,
  statusId: string,
  body: Record<string, unknown>
): Promise<Status> {
  return call<Status>(`/statuses/${kind}/${statusId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteStatus(
  kind: StatusKind,
  statusId: string
): Promise<{ deleted: string; kind: string }> {
  return call(`/statuses/${kind}/${statusId}`, { method: "DELETE" });
}

export function createLostReason(body: {
  label: string;
  position?: number;
}): Promise<LostReason> {
  return call<LostReason>("/lost-reasons", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchLostReason(
  reasonId: string,
  body: Record<string, unknown>
): Promise<LostReason> {
  return call<LostReason>(`/lost-reasons/${reasonId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteLostReason(
  reasonId: string
): Promise<{ deleted: string }> {
  return call(`/lost-reasons/${reasonId}`, { method: "DELETE" });
}

/**
 * The WS-26f f1 pull — dry run unless `apply` is true.
 *
 * ⚠️ `?apply=true` is the registered owner gate (work_plan.md §6): it rewrites
 * the live pipeline. The button that reaches it therefore asks first, and the
 * dry-run report is what it asks WITH.
 */
export function importZohoStages(
  apply = false
): Promise<StageMetadataReport> {
  return call<StageMetadataReport>(
    `/import/zoho/stages${apply ? "?apply=true" : ""}`,
    { method: "POST" }
  );
}

// ── Reports (WS-26g) ──────────────────────────────────────────────────────
//
// Four reads, not one bundled payload. They answer four different questions
// off different tables, and bundling them would make the slowest one decide
// when any of them renders — plus a single failure would blank the whole tab
// instead of the block that could not be computed.
//
// All four are GET, take no parameters, and write nothing. The trailing window
// is the server's constant (`reports.WINDOW_DAYS`) and travels back on the
// payload so the surface can label the number it is showing rather than
// assuming one.

export function getPipelineReport(): Promise<PipelineReport> {
  return call<PipelineReport>("/reports/pipeline");
}

export function getFunnelReport(): Promise<FunnelReport> {
  return call<FunnelReport>("/reports/funnel");
}

export function getWinLossReport(): Promise<WinLossReport> {
  return call<WinLossReport>("/reports/win-loss");
}

export function getOwnerLeaderboard(): Promise<OwnerLeaderboard> {
  return call<OwnerLeaderboard>("/reports/owners");
}

// ── Mode, connection and sync (WS-53 CRM-U5, CRM-Z7) ───────────────────────
//
// A 404 from the three reads below means the Zoho mirror is dark for this
// deployment (`CRM_MIRROR` off). It is an answer, not a failure: each read
// then returns `null` and the mirror UI stays hidden.

async function orNullOn404<T>(work: Promise<T>): Promise<T | null> {
  try {
    return await work;
  } catch (err) {
    if (err instanceof CrmError && err.status === 404) return null;
    throw err;
  }
}

export function getConnection(): Promise<ConnectionInfo | null> {
  return orNullOn404(call<ConnectionInfo>("/connection"));
}

export function getSyncStatus(): Promise<SyncStatus | null> {
  return orNullOn404(call<SyncStatus>("/sync/status"));
}

/** Admin only. A member gets 403, which the caller treats as "unknown". */
export function getCrmSettings(): Promise<CrmSettings> {
  return call<CrmSettings>("/settings");
}

/** Admin only. Choose who may open the CRM (CRM-T5). Each group is `group:<slug>`. */
export function saveAccess(body: {
  access: "admins" | "everyone" | "groups";
  access_groups: string[];
}): Promise<CrmSettings> {
  return call<CrmSettings>("/settings", { method: "PATCH", body: JSON.stringify(body) });
}

/** The organization's groups, for the access control. An empty list when unreadable. */
export async function listGroups(): Promise<Array<{ slug: string; display_name?: string }>> {
  try {
    const res = await fetch("/api/admin/groups", { cache: "no-store" });
    if (!res.ok) return [];
    const rows: unknown = await res.json();
    return Array.isArray(rows) ? (rows as Array<{ slug: string; display_name?: string }>) : [];
  } catch {
    return [];
  }
}

export function chooseNativeMode(): Promise<CrmSettings> {
  return call<CrmSettings>("/settings", {
    method: "PATCH",
    body: JSON.stringify({ mode: "native" }),
  });
}

export function startZohoConnect(): Promise<{ url: string }> {
  return call<{ url: string }>("/connection/zoho/start", { method: "POST" });
}

export function disconnectCrm(): Promise<ConnectionInfo> {
  return call<ConnectionInfo>("/connection/disconnect", { method: "POST" });
}

export function syncZohoNow(): Promise<{ started: boolean }> {
  return call<{ started: boolean }>("/sync/zoho", { method: "POST" });
}

// ── Timeline ──────────────────────────────────────────────────────────────

export function getTimeline(
  entity: EntitySlug,
  id: string,
  limit = 100
): Promise<{ entries: TimelineEntry[] }> {
  return call(`/${entity}/${id}/timeline?limit=${limit}`);
}

export function logActivity(
  entity: EntitySlug,
  id: string,
  body: {
    type: "note" | "call" | "meeting" | "task";
    subject?: string;
    body?: string;
    due_at?: string;
  }
): Promise<Activity> {
  return call<Activity>(`/${entity}/${id}/activities`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function completeTask(
  activityId: string,
  completed: boolean
): Promise<Activity> {
  return call<Activity>(`/activities/${activityId}`, {
    method: "PATCH",
    body: JSON.stringify({
      completed_at: completed ? new Date().toISOString() : null,
    }),
  });
}

// ── Deal contacts (WS-26c's API addendum) ─────────────────────────────────

export function listDealContacts(
  dealId: string
): Promise<{ rows: DealContact[] }> {
  return call(`/deals/${dealId}/contacts`);
}

export function addDealContact(
  dealId: string,
  body: { contact_id: string; role?: string; is_primary?: boolean }
): Promise<DealContact> {
  return call<DealContact>(`/deals/${dealId}/contacts`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function removeDealContact(
  dealId: string,
  contactId: string
): Promise<{ deleted: string; was_primary: boolean }> {
  return call(`/deals/${dealId}/contacts/${contactId}`, { method: "DELETE" });
}

// ── Conversion ────────────────────────────────────────────────────────────

export function convertLead(
  leadId: string,
  body: Record<string, unknown>
): Promise<ConvertResult> {
  return call<ConvertResult>(`/leads/${leadId}/convert`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type { Contact, Deal, Lead, Organization };

/** Admin only. Back to "no mode" (CRM-U5b). A 409 carries a `message` to show. */
export function resetMode(): Promise<CrmSettings> {
  return call<CrmSettings>("/settings", {
    method: "PATCH",
    body: JSON.stringify({ mode: null }),
  });
}
