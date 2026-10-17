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

import { filenameFromDisposition, saveBlob, saveCsv } from "@/lib/export";
import { moveRequest, type DealMove, type MoveExtras } from "./board";
import { queryString, type ListQuery } from "./filters";
import type { ConnectionInfo, CrmSettings, SyncStatus } from "./connect";
import type { FieldDef } from "./fields";
import { asFieldLayout, type FieldLayout } from "./fieldLayout";
import type {
  FieldMap,
  FieldRule,
  MappingPlan,
  PlanChange,
  PlanList,
} from "./fieldMap";
import type { CrmOwner } from "./filterBar";
import type { SavedView, SavedViewBody } from "./savedViews";
import type { SyncHealth, SyncHold } from "./syncHealth";
import type { SourceLabels } from "./views";
import type {
  OverlayPreview,
  SlotWord,
  SuggestionDone,
  SuggestionList,
} from "./suggestions";
import type { RecordCounts, ResetThen } from "./setup";
import type { ResetResult } from "./startOver";
import type {
  Activity,
  Contact,
  ConvertResult,
  CrmDigest,
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
import type { CrmModule, ModuleListResponse, ModuleRecordPage } from "./modules";

export class CrmError extends Error {
  status: number;
  /**
   * The machine code of a refusal: a string `detail` (`sync_running`) or the
   * `code` of an object `detail` (`mirrored_record`, `mirror_mode_create`).
   */
  code?: string;
  /**
   * The custom field that a refusal names (CRM-C4p item 4): the `field` key
   * of an object `detail`. The C4a write path sends it with each refused
   * value, so a form can show the message beside that input.
   */
  field?: string;
  constructor(message: string, status: number, code?: string, field?: string) {
    super(message);
    this.name = "CrmError";
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

/**
 * The one way to call the gateway (CRM-C4p item 5). Each CRM route goes
 * through it, so a refusal keeps its status, its code and its field. A second
 * `fetch` path to `/api/crm` is a defect. The CSV and ZIP downloads below
 * are the exceptions, because their bodies are not JSON.
 */
export async function call<T>(path: string, init?: RequestInit): Promise<T> {
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
      codeOf(body),
      fieldOf(body)
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

/** The `field` of an object `detail`, or undefined (CRM-C4p item 4). */
function fieldOf(body: { detail?: unknown }): string | undefined {
  const { detail } = body;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const field = (detail as { field?: unknown }).field;
    if (typeof field === "string" && field) return field;
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

/**
 * The one layout of an entity (CRM-C4a item 12): the sections, the system
 * fields and the custom fields, in order, with `studio` and `studio_admin`.
 * A body that is not a layout throws, so the caller falls back.
 */
export async function getFieldLayout(entity: EntitySlug): Promise<FieldLayout> {
  return asFieldLayout(await call<unknown>(`/fields/${entity}?layout=1`));
}

// ── The owner filter (CRM-X1) ─────────────────────────────────────────────

/** Each active member, and each record owner who is not a member. */
export async function listOwners(): Promise<CrmOwner[]> {
  return (await call<{ owners: CrmOwner[] }>("/owners")).owners;
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
  extra?: ListQuery,
): Promise<Pipeline> {
  const params = new URLSearchParams();
  if (owner) params.set("owner", owner);
  if (pipelineId) params.set("pipeline_id", pipelineId);
  // CRM-Q8: the board takes the same filters as the list.
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
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

/**
 * The lane order of one kind, in one transaction (WS-53 CRM-C3, §20.6 item 4).
 * `ids` is every id of the kind, in the new order. Mirror mode writes the
 * Metorite order (`ui_position`) and never Zoho. Native mode writes `position`.
 */
export function orderStatuses(kind: StatusKind, ids: string[]): Promise<Status[]> {
  return call<Status[]>(`/statuses/${kind}/order`, {
    method: "PUT",
    body: JSON.stringify({ ids }),
  });
}

/** "Reset order to Zoho" (CRM-C3, item 14): the Metorite order goes. Mirror only. */
export function resetStatusOrder(kind: StatusKind): Promise<Status[]> {
  return call<Status[]>(`/statuses/${kind}/order/reset`, { method: "POST" });
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

/**
 * The newest weekly digests of the org (WS-53 CRM-Q28). Read only. An org
 * with no digest gets an empty list, which the Reports tab draws as nothing.
 */
export async function listDigests(limit = 4): Promise<CrmDigest[]> {
  const res = await call<{ digests: CrmDigest[] }>(`/digests?limit=${limit}`);
  return res?.digests ?? [];
}

// ── The generic Zoho modules (WS-53 CRM-Q6b) ──────────────────────────────

/** The mirrored generic modules, in the profile order. Empty in Native mode. */
export function listModules(): Promise<CrmModule[]> {
  return call<CrmModule[]>("/modules");
}

/**
 * One page of a module's records. The module name goes into the path encoded,
 * and the gateway checks it against the profile before it reads a record.
 */
export function listModuleRecords(
  module: string,
  query: ListQuery = {},
): Promise<ModuleListResponse> {
  return call<ModuleListResponse>(
    `/modules/${encodeURIComponent(module)}/records${queryString(query)}`,
  );
}

/** One generic record, with its field definitions and its lookup links. */
export function getModuleRecord(module: string, id: string): Promise<ModuleRecordPage> {
  return call<ModuleRecordPage>(
    `/modules/${encodeURIComponent(module)}/records/${encodeURIComponent(id)}`,
  );
}

/**
 * The activities of one module record, newest first (CRM-Q23a). Read only:
 * no route writes an activity on a module record. An unknown module or
 * record is a 404.
 */
export function getModuleTimeline(
  module: string,
  id: string,
  limit = 100,
): Promise<{ entries: TimelineEntry[] }> {
  return call<{ entries: TimelineEntry[] }>(
    `/modules/${encodeURIComponent(module)}/records/${encodeURIComponent(id)}/timeline?limit=${limit}`,
  );
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

/** Admin only (CRM-Q9). The sync health card reads it. A 404 means the mirror is off. */
export function getSyncHealth(): Promise<SyncHealth | null> {
  return orNullOn404(call<SyncHealth>("/sync/health"));
}

/** Admin only. A member gets 403, which the caller treats as "unknown". */
export function getCrmSettings(): Promise<CrmSettings> {
  return call<CrmSettings>("/settings");
}

/** The body of `GET /crm/mode`. */
export type CrmModeBody = {
  mode: string | null;
  labels?: SourceLabels | null;
  /** CRM-Q20-2: by core slot, the Zoho module that plays it. */
  overlays?: Partial<Record<SlotWord, string>> | null;
  /**
   * CRM-Q17b: by API slug, whether the org holds a record of each core
   * entity, and whether Zoho turned it off. Read it through `coreStatesOf`.
   */
  entities?: unknown;
};

/**
 * `GET /crm/mode`, for each member the access rule admits. In Mirror mode,
 * for an org on `CRM_FILL_RANK_ORGS`, it also holds the source's names of the
 * core entities (CRM-Q17a-1 item 6). Each other body has no `labels` key.
 * CRM-Q20-2: with an approved role, `overlays` names the module of each slot.
 */
export function getCrmMode(): Promise<CrmModeBody> {
  return call<CrmModeBody>("/mode");
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

/**
 * Start a sync. With `only` (CRM-Q16), the run reads that one count key on
 * its own, also when it is held. Without it, the call sends no body, as before.
 */
export function syncZohoNow(opts: { only?: string } = {}): Promise<{ started: boolean }> {
  const init: RequestInit = { method: "POST" };
  if (opts.only) init.body = JSON.stringify({ only: opts.only });
  return call<{ started: boolean }>("/sync/zoho", init);
}

/**
 * Admin only (CRM-Q16). `sync: false` holds a count key and `sync: true`
 * releases it. The key can be `module:<ApiName>`, so it is encoded.
 */
export function setModuleSync(
  key: string,
  sync: boolean
): Promise<{ holds: Record<string, SyncHold> }> {
  return call<{ holds: Record<string, SyncHold> }>(
    `/sync/modules/${encodeURIComponent(key)}`,
    { method: "PUT", body: JSON.stringify({ sync }) }
  );
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

// ── The Field map (WS-53 CRM-Q7a) ─────────────────────────────────────────

/** Admin only, Mirror mode only. The fields that go to custom, the rules and the targets. */
export function getFieldMap(entity: EntitySlug): Promise<FieldMap> {
  return call<FieldMap>(`/field-rules?entity=${encodeURIComponent(entity)}`);
}

/**
 * Admin only. Pick a target by hand: the rule is approved at once. A rule the
 * server refuses is a 422 with a sentence, and a column that another approved
 * rule fills is a 409 (`rule_taken`).
 */
export function createFieldRule(body: {
  entity: EntitySlug;
  field_api_name: string;
  target_column: string;
}): Promise<FieldRule> {
  return call<FieldRule>("/field-rules", { method: "POST", body: JSON.stringify(body) });
}

/** Admin only. Approve or reject one rule. The next sync applies it. */
export function decideFieldRule(
  id: string,
  status: "approved" | "rejected",
): Promise<FieldRule> {
  return call<FieldRule>(`/field-rules/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// ── The plan of the Field map (WS-53 CRM-Q18-2) ───────────────────────────
//
// Admin only, Mirror mode only. The routes are CRM-Q18-1's. A 409 keeps its
// code: `plan_moved` (the map changed after the draft) and `plan_busy` (a
// clash with the sync, and a retry is safe). `lib/fieldMap.ts` holds the types
// and `runPlanWrite`, which acts on both codes.

/** The versions, newest first, with the count of their rules and no rules. */
export function listMappingPlans(): Promise<PlanList> {
  return call<PlanList>("/mapping/plans");
}

/** One version with its rules. It reads only. */
export function getMappingPlan(version: number): Promise<MappingPlan> {
  return call<MappingPlan>(`/mapping/plans/${encodeURIComponent(String(version))}`);
}

/** A draft: the version in use with the changes. It changes no live rule. */
export function draftMappingPlan(changes: PlanChange[]): Promise<MappingPlan> {
  return call<MappingPlan>("/mapping/plans", {
    method: "POST",
    body: JSON.stringify({ changes }),
  });
}

/** Approve a draft. The next sync applies it. */
export function approveMappingPlan(version: number): Promise<MappingPlan> {
  return call<MappingPlan>(`/mapping/plans/${encodeURIComponent(String(version))}/approve`, {
    method: "POST",
  });
}

/** Approve a new version with the content of an earlier one ("Go back"). */
export function restoreMappingPlan(version: number): Promise<MappingPlan> {
  return call<MappingPlan>(`/mapping/plans/${encodeURIComponent(String(version))}/restore`, {
    method: "POST",
  });
}

// ── The suggestions of the mapping (WS-53 CRM-Q20-2) ──────────────────────
//
// Admin only, Mirror mode only. `lib/suggestions.ts` holds the types and the
// words. Each write is for one slot, and a 409 keeps its code.

/** The open suggestions, and the slots that a module plays now. It reads only. */
export function listSuggestions(): Promise<SuggestionList> {
  return call<SuggestionList>("/mapping/suggestions");
}

/** The records of the module of one suggestion, as the slot reads them. It writes nothing. */
export async function previewSuggestion(version: number, slot: SlotWord): Promise<OverlayPreview> {
  const body = await call<{ overlay: OverlayPreview }>("/mapping/preview", {
    method: "POST",
    body: JSON.stringify({ version, slot }),
  });
  return body.overlay;
}

/** The module of the suggestion plays the slot. */
export function approveSuggestion(slot: SlotWord, version: number): Promise<SuggestionDone> {
  return call<SuggestionDone>(`/mapping/suggestions/${slot}/approve`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

/** The slot shows its own list again. */
export function undoSuggestion(slot: SlotWord): Promise<SuggestionDone> {
  return call<SuggestionDone>(`/mapping/suggestions/${slot}/undo`, { method: "POST" });
}

/** Do not offer the module for the slot again, until Zoho changes. */
export function dismissSuggestion(slot: SlotWord, version: number): Promise<SuggestionDone> {
  return call<SuggestionDone>(`/mapping/suggestions/${slot}/dismiss`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

// ── The setup hub and the start over (WS-53 CRM-X0) ───────────────────────

/** Admin only. The records of the org, by entity. */
export function getRecordCounts(): Promise<RecordCounts> {
  return call<RecordCounts>("/settings/counts");
}

/**
 * Admin only. Remove every record and set the mode from `then`. A wrong
 * `confirm` is a 422 with a sentence. `then = "mirror"` gives the consent URL.
 */
export function resetCrm(body: { confirm: string; then: ResetThen }): Promise<ResetResult> {
  return call<ResetResult>("/reset", { method: "POST", body: JSON.stringify(body) });
}

/** The ZIP of every record, through the proxy. The server names the file. */
export const ALL_RECORDS_ZIP = "/api/crm/export/all.zip";

/**
 * Get the whole ZIP, then save it (review P2). A refusal or a proxy error is
 * a `CrmError` with the server's words, and nothing is saved, so a JSON body
 * never lands on disk as `crm-export.zip`. It resolves only after the save.
 */
export async function downloadAllRecords(
  deps: { fetch: typeof fetch; save: (body: Blob, filename: string) => void } = {
    fetch: (input, init) => fetch(input, init),
    save: saveBlob,
  },
): Promise<void> {
  const res = await deps.fetch(ALL_RECORDS_ZIP, { cache: "no-store" });
  const type = res.headers.get("content-type") ?? "";
  if (!res.ok || !type.includes("application/zip")) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown; error?: string };
    throw new CrmError(detailOf(body) || `The copy did not download (${res.status}).`, res.status);
  }
  const blob = await res.blob();
  deps.save(blob, filenameFromDisposition(res.headers.get("content-disposition"), "crm-export.zip"));
}

// ── Saved views (CRM-Q8) ──────────────────────────────────────────────────

export function listViews(entity: EntitySlug): Promise<SavedView[]> {
  return call<SavedView[]>(`/views?entity=${entity}`);
}

export function createView(body: SavedViewBody): Promise<SavedView> {
  return call<SavedView>("/views", { method: "POST", body: JSON.stringify(body) });
}

export function patchView(id: string, body: Partial<SavedViewBody>): Promise<SavedView> {
  return call<SavedView>(`/views/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteView(id: string): Promise<{ deleted: string }> {
  return call<{ deleted: string }>(`/views/${id}`, { method: "DELETE" });
}
